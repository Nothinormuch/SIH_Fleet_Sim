# 08. EDGE DEPLOYMENT

> This document establishes how the same coordination code that produces the benchmark evidence runs as one independent operating-system process per robot on edge hardware, and states precisely which parts of that claim are measured, which are implemented but unmeasured, and which have never touched a physical Raspberry Pi.

**Audience:** SIH judges and BEL evaluators assessing requirement 15, and teammates who must launch the multi-process demonstration live and answer "but has it actually run on a Pi?"
**Reads best after:** [02. Architecture](02-ARCHITECTURE.md)

## Requirements evidenced

| # | Requirement | Where | Evidence |
|---|---|---|---|
| 15 | Edge / local execution | [§1](#1-one-brain-two-transports), [§2](#2-the-edge-runtime) | `src/edge_runtime.py:266`, `src/edge_runtime.py:281` |
| 15 | Deployable without a build step (stdlib-only agent path) | [§1](#1-one-brain-two-transports) | `pyproject.toml:11`, AST audit in [§1](#1-one-brain-two-transports) |
| 15 | One OS service per robot | [§4](#4-systemd-deployment) | `deploy/systemd/sih-edge-node@.service:12` |
| 6 | No central coordination server | [§5](#5-the-multi-process-udp-demonstration) | `src/distributed_demo.py:276`, `tests/test_edge_runtime.py:80` |
| 3 | Decentralized communication | [§5](#5-the-multi-process-udp-demonstration) | `src/transport.py:194`, `src/transport.py:255` |
| 1 | At least 3 AMRs | [§5](#5-the-multi-process-udp-demonstration) | `src/distributed_demo.py:164` |
| 18 | Battery status | [§9](#9-sensor-and-localization-contract) | `src/edge_runtime.py:255` |
| 19 | Zero inter-robot collisions (in the multi-process run) | [§5](#5-the-multi-process-udp-demonstration) | `src/distributed_demo.py:287`, measured in [§6](#6-hardware-targets-and-resource-budget) |

Requirements 7-14 and 20 are argued in [04. Path Planning](04-PATH-PLANNING.md), [05. Coordination Policies](05-COORDINATION-POLICIES.md), [06. Task Allocation](06-TASK-ALLOCATION.md) and [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md). This document only claims that the code producing those results is the code that runs on a node.

---

## 1. One brain, two transports

The problem statement demands execution on constrained edge hardware and accepts a simulation as the deliverable. The project resolves that by making the two the same object. `AMRBrain` performs no I/O: it is a pure function of `(time, sensor frame, inbox) -> (actuation, outbox)`. It never opens a socket, never reads a file, and never asks who else exists. Everything around it is substitutable.

The batch simulator calls it at `src/main.py:338`:

```python
act, outbox = brains[rid].step(t, sensors, net.poll(t, rid))
```

The edge runtime calls it at `src/edge_runtime.py:109`:

```python
actuation, outbox = self.brain.step(local_t, local_sensors, inbox)
```

Same class, same method, same signature. The only difference is what supplies `sensors` and what carries `outbox`: `SimNetwork` (`src/transport.py:75`, a seeded faster-than-realtime radio model) in the benchmark, `UdpMulticastTransport` (`src/transport.py:194`, a real non-blocking multicast socket) on a node. Both satisfy the same three-method protocol declared at `src/edge_runtime.py:37`. This is why a benchmark result is evidence about the deployed system rather than about a separate research prototype.

### The dependency position, verified

The claim in `requirements.txt:1` — that the agent core is stdlib-only so "the agent node must drop onto a bare Raspberry Pi image without a build step" — was checked by parsing every module with `ast` and testing each top-level import name against `sys.stdlib_module_names`, not by reading the comment.

| Path | Non-stdlib imports | Status |
|---|---|---|
| `src/` (all 26 modules, including `amr.py`, `edge_runtime.py`, `transport.py`, `planner.py`, `benchmark.py`) | none | Verified stdlib-only |
| Repo-root entry points (`edge_node.py`, `edge_demo.py`, `run.py`, `benchmark.py`, `baseline_comparison.py`, `fault_campaign.py`, `auction_v2_campaign.py`) | none | Verified stdlib-only |
| `backend/server.py` (dashboard HTTP/SSE bridge) | none — uses `http.server`, `threading` (`backend/server.py:37`) | Verified stdlib-only |
| `tools/bake_brand_assets.py`, `tools/bake_twin_textures.py` | `numpy`, `PIL` (`tools/bake_brand_assets.py:41`) | Offline asset baking for the 3-D frontend. Not imported by any node, benchmark, or dashboard path. |

`pyproject.toml:11` declares `dependencies = []`, which is correct. The agent path therefore needs only a CPython interpreter (`pyproject.toml:10` requires >= 3.10) and the repository, which is what "no build step" means: no compiler, no wheels, no `pip install` on the robot.

**Contradiction found — `requirements.txt` is wrong in both directions.** It pins `websockets>=12,<16` and `matplotlib>=3.8,<4` (`requirements.txt:4-5`) and describes them as serving "the dashboard bridge and the report plots". Neither package is imported anywhere in the repository: a search across `src/`, `backend/`, `tools/`, `tests/` and the root scripts returns matches only in `requirements.txt:4-5` and the unused `reports` extra at `pyproject.toml:14-17`. The dashboard bridge uses `http.server`, not `websockets`. Meanwhile `numpy` and `PIL`, which *are* imported (by `tools/`), are listed nowhere. The file overstates what must be installed and understates what the asset tooling needs. This does not weaken the edge claim — it strengthens it, because the true runtime dependency count for a node is zero — but the file should be corrected before submission so a judge who runs `pip install -r requirements.txt` and greps for `matplotlib` does not conclude the comment was written aspirationally.

---

## 2. The edge runtime

`src/edge_runtime.py` is the deployment boundary. One OS process owns one brain, one monotonic clock, one UDP peer transport, one sensor/actuator adapter, and one completion journal.

### Process model

`EdgeRuntime` (`src/edge_runtime.py:91`) is the thin composition object: brain + transport + config + optional journal. It is single-threaded. There is no thread pool, no async loop, and no lock, because there is nothing to contend for — the whole node is one sequential loop. `run_edge_node` (`src/edge_runtime.py:266`) is the loop that drives it.

### Loop timing

The period comes from configuration, not a literal: `period = 1.0 / cfg.rates.safety_hz` (`src/edge_runtime.py:281`), with `safety_hz = 50.0` (`src/settings.py:108`). That is a 20 ms budget per tick.

Scheduling uses absolute deadlines rather than `sleep(period)` (`src/edge_runtime.py:303`):

```python
next_tick += period
time.sleep(max(0.0, next_tick - time.monotonic()))
```

Because `next_tick` advances by a fixed period regardless of how long the tick took, per-tick jitter does not accumulate into clock drift. The consequences when a tick overruns are analysed in [§7](#7-real-time-feasibility).

The time handed to the brain is the node's own elapsed monotonic time plus a configured epoch offset (`src/edge_runtime.py:291`), and `EdgeRuntime.tick` overwrites the incoming sensor timestamp with it (`src/edge_runtime.py:107`, `replace(sensors, t=local_t)`). Every timestamp the node puts on the wire is therefore from its own clock. No node compares another node's absolute timestamp against its own; wire time windows are relative TTLs. This is what makes NTP synchronisation useful for log correlation but unnecessary for correctness — a claim the multi-process demo tests directly by starting each child at a deliberately unrelated epoch (`src/distributed_demo.py:190-193`).

### How the real transport is wired to the brain

Order within one tick (`src/edge_runtime.py:105-119`):

1. `inbox = self.transport.poll()` — drain up to 256 datagrams, non-blocking (`src/transport.py:263`).
2. `actuation, outbox = self.brain.step(local_t, local_sensors, inbox)`.
3. Every message in `outbox` is sent (`src/edge_runtime.py:110-111`).
4. Loop duration is recorded against the period (`src/edge_runtime.py:115-118`).

The socket is explicitly non-blocking (`src/transport.py:249`). A robot never stalls its safety loop waiting on the radio: if nothing has arrived, `poll()` returns `[]` and the agent proceeds on stale peer data. A send failure is counted, not raised (`src/transport.py:258-261`) — a dropped packet is a normal event in a protocol designed for loss, so the correct response is to continue.

### Health and fail-safe behaviour

There are two distinct mechanisms, and neither is a liveness watchdog.

**Sensor staleness (implemented and tested at the schema level).** Before every tick, `run_edge_node` reads the newest sensor frame and its local receive time (`src/edge_runtime.py:290`). If there is no frame, or the newest frame is older than `sensor_timeout_s` (default 0.25 s, `src/edge_runtime.py:268`), the node increments `sensor_timeouts` and emits an explicit stop instead of ticking the brain (`src/edge_runtime.py:292-295`):

```python
runtime.metrics.sensor_timeouts += 1
actuation = Actuation(v=0.0, omega=0.0, safety_stop=True)
```

The planner is never asked to continue on old world data. One consequence is worth stating because a judge may ask: during a sensor outage the brain does not run, so the node also stops emitting heartbeats and intents. Peers see it go silent and treat its reservations as expiring — which is the desired behaviour, but it is a side effect of the fail-safe rather than a separately coded one.

**Per-tick deadline accounting (implemented and measured).** `EdgeMetrics.record_loop` (`src/edge_runtime.py:63-69`) counts a `deadline_miss` whenever a tick's own duration exceeds the period, and retains up to 100,000 samples for percentiles computed at `src/edge_runtime.py:71-88`. This is observation, not enforcement: nothing kills or degrades the node on a miss.

**Not implemented: a liveness watchdog.** The systemd unit sets `Restart=on-failure` (`deploy/systemd/sih-edge-node@.service:18`) but no `WatchdogSec=`, and the runtime makes no `sd_notify` call. A node that hangs while still alive — a blocked syscall, a pathological loop — is never restarted. A crashed node is restarted after 2 s. This gap is closable with one unit directive plus a keepalive ping, but it is not closed today.

### Shutdown

`SIGINT` and `SIGTERM` are captured and turned into a flag, not an exception (`src/edge_runtime.py:275-280`), so a stop request never interrupts a tick mid-decision. The `finally` block (`src/edge_runtime.py:305-311`) then, in order: writes a final `safety_stop` actuation to the hardware, flushes the completion journal and closes the transport, closes the hardware sockets, and restores the previous signal handlers. `systemctl stop` therefore leaves the wheels commanded to zero, not merely un-commanded.

Disk persistence is deliberately sequenced *after* the actuation write (`src/edge_runtime.py:298-302`, with the reasoning in the source comment): a slow filesystem may delay the next coordination tick but can never delay the protective command already chosen for the current sensor frame.

---

## 3. Node configuration

Every variable in `config/edge-node.example.env`. All nine are consumed — seven by the systemd `ExecStart` substitution, one by the runtime's environment lookup, and one by the scenario bootstrap.

| Variable | Line | Meaning | Example / default | Units | Consumed at |
|---|---|---|---|---|---|
| `SIH_FLEET_PSK` | `config/edge-node.example.env:3` | Pre-shared key authenticating every peer datagram. Read from the environment by name, not passed on the command line, so it never appears in `ps`. | none — must be set | bytes (>= 16 enforced at `src/transport.py:221`; >= 32 recommended by the file comment) | `src/edge_runtime.py:374` |
| `ROBOT_INDEX` | `:4` | This robot's index into the scenario's start poses and pre-assigned task queues. Validated against `ROBOTS` at startup. | `0` | integer, 0-based | `src/edge_runtime.py:348`, `:351` |
| `ROBOTS` | `:5` | Fleet size the scenario is generated for. Every node must agree. | `3` | count | `src/edge_runtime.py:350` |
| `SCENARIO` | `:6` | Named scenario supplying the warehouse map, start cells and task set. See [§9](#9-sensor-and-localization-contract) for why this is the real-hardware integration seam. | `dense_aisles` | key of `SCENARIOS` (`src/scenarios.py`) | `src/edge_runtime.py:350` |
| `NETWORK_INTERFACE` | `:7` | Local IPv4 address for the multicast join and outbound interface selection. `0.0.0.0` lets the kernel choose — correct on one host, wrong for multi-host. | `0.0.0.0` (use the AMR NIC address on real hardware) | IPv4 address | `src/edge_runtime.py:328`, `src/transport.py:240-244` |
| `PEER_PORT` | `:8` | UDP port for the peer multicast group. | `26123` | TCP/UDP port number | `src/edge_runtime.py:327`, `src/transport.py:45` |
| `SENSOR_PORT` | `:9` | Local UDP port this node binds to receive sensor frames from the robot driver. Must be unique per node on a shared host. | `27101` | port number | `src/edge_runtime.py:330`, `src/edge_runtime.py:166` |
| `ACTUATOR_PORT` | `:10` | UDP port this node sends wheel commands to. | `28101` | port number | `src/edge_runtime.py:332`, `src/edge_runtime.py:169` |

Two settings a judge may look for are **absent from the example env file** and therefore fall back to defaults, since the unit does not pass them either:

| Setting | Default used | Where |
|---|---|---|
| Multicast group | `239.26.1.23` (administratively scoped, mnemonic for SIH26123) | `src/transport.py:44` |
| Sensor / actuator host | `127.0.0.1` — the driver is assumed to be on the same board | `src/edge_runtime.py:329`, `:331` |
| Coordination policy | `BIOS_PIBT.6` | `src/edge_runtime.py:324` |
| Allocation policy | `auction` | `src/edge_runtime.py:325` |
| Completion journal path | `$XDG_STATE_HOME/sih-fleet-priority/<id>-terminal.json`, else `~/.local/state/...` | `src/edge_runtime.py:352-359` |

That last default is the source of a deployment defect — see [§4](#4-systemd-deployment).

### The key is mandatory unless you opt out loudly

Startup refuses to run unauthenticated by accident (`src/edge_runtime.py:374-378`):

```python
secret = os.environ.get(args.psk_env)
if not secret and not args.allow_unauthenticated:
    raise SystemExit(f"{args.psk_env} is unset; configure a fleet PSK or pass "
                     "--allow-unauthenticated for an isolated development network")
```

Because the unit file never passes `--allow-unauthenticated`, a Pi with a missing or empty `SIH_FLEET_PSK` fails to start rather than joining the fleet in the clear.

---

## 4. systemd deployment

`deploy/systemd/` contains exactly one file: `sih-edge-node@.service`, a systemd *template* unit. Instantiating it as `sih-edge-node@AMR01` substitutes `%i` = `AMR01` throughout, so N robots need N `systemctl enable` calls and N env files, not N unit files.

| Directive | Line | Effect |
|---|---|---|
| `After=` / `Wants=network-online.target` | `:3-4` | Delays start until the interface is up, so the multicast join at `src/transport.py:241` does not fail on a cold boot. |
| `Type=simple` | `:7` | No forking, no readiness protocol. systemd considers the node started the moment `exec` succeeds. |
| `User` / `Group=sih-fleet` | `:8-9` | Runs unprivileged. Nothing in the node needs root — multicast join and UDP bind above 1024 require no capability. |
| `EnvironmentFile=/etc/sih-fleet/%i.env` | `:11` | Per-robot secrets and ports, outside the repository. |
| `ExecStart=` | `:12-17` | One `edge_node.py` process, robot id from the instance name, everything else from the env file, final JSON report to `/var/lib/sih-fleet/%i-report.json`. |
| `Restart=on-failure`, `RestartSec=2` | `:18-19` | Non-zero exit restarts after 2 s. Clean exit (`systemctl stop`) does not restart. No burst limit is set, so a persistently failing node restarts indefinitely at 2 s intervals. |
| `NoNewPrivileges=true` | `:20` | No setuid escalation from the node. |
| `PrivateTmp=true` | `:21` | Private `/tmp`. |
| `ProtectSystem=strict` | `:22` | Entire filesystem read-only except `/dev`, `/proc`, `/sys` and `ReadWritePaths`. |
| `ProtectHome=true` | `:23` | `/home`, `/root` and `/run/user` are made inaccessible. |
| `ReadWritePaths=/var/lib/sih-fleet` | `:24` | The single writable directory. |
| `CapabilityBoundingSet=` (empty) | `:25` | All capabilities dropped. |
| `LockPersonality=true`, `MemoryDenyWriteExecute=true` | `:26-27` | Standard hardening; compatible with CPython, which does not JIT. |
| `WantedBy=multi-user.target` | `:30` | Starts at boot once enabled. |

### Install, on each Pi

```bash
# 1. Service account. The home directory matters - see the defect note below.
sudo useradd --system --home-dir /var/lib/sih-fleet --create-home \
             --shell /usr/sbin/nologin sih-fleet

# 2. Code and interpreter. No wheels are needed for the node itself.
sudo install -d -o sih-fleet -g sih-fleet /opt/sih-fleet /var/lib/sih-fleet
sudo git clone <repo-url> /opt/sih-fleet
sudo -u sih-fleet python3 -m venv /opt/sih-fleet/.venv

# 3. Per-robot configuration, root-only.
sudo install -d -m 0755 /etc/sih-fleet
sudo cp /opt/sih-fleet/config/edge-node.example.env /etc/sih-fleet/AMR01.env
sudo nano /etc/sih-fleet/AMR01.env          # set PSK, ROBOT_INDEX, NETWORK_INTERFACE
sudo chmod 600 /etc/sih-fleet/AMR01.env

# 4. Unit.
sudo cp /opt/sih-fleet/deploy/systemd/sih-edge-node@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sih-edge-node@AMR01

# 5. Observe.
systemctl status sih-edge-node@AMR01
journalctl -u sih-edge-node@AMR01 -f
```

The same `SIH_FLEET_PSK` value must appear in every robot's env file; a mismatched key produces `auth_failed` counts in the transport stats (`src/transport.py:276-277`) and total coordination silence rather than a visible error.

### Defect: the completion journal is unwritable under the shipped sandbox

The `ExecStart` at `deploy/systemd/sih-edge-node@.service:12-17` passes `--report` but **not** `--terminal-journal`, and the env file sets no `XDG_STATE_HOME`. The journal path therefore falls back to `Path.home()/".local"/"state"/"sih-fleet-priority"` (`src/edge_runtime.py:355-359`). Combined with `ProtectHome=true` (`:23`) and `ProtectSystem=strict` (`:22`), that directory is unwritable for any service account whose home is under `/home`.

The failure is delayed and therefore easy to miss in a short test:

- `TerminalJournal.load()` returns `[]` when the file does not exist (`src/terminal_journal.py:90-92`), so **startup succeeds**.
- The first time a terminal record changes — that is, the first completed task — `flush_terminal_records` calls `sync()` (`src/edge_runtime.py:126`), whose `mkdir`/write path raises `TerminalJournalError` on any `OSError` (`src/terminal_journal.py:145-147`).
- `EdgeRuntime.flush_terminal_records` (`src/edge_runtime.py:121-128`) does not catch it, so the exception propagates out of the loop, the node exits non-zero, and `Restart=on-failure` restarts it — into the same crash on its next completed task.

The install command in step 1 above pins `--home-dir /var/lib/sih-fleet`, which is inside `ReadWritePaths` and avoids the defect. That is a workaround, not a fix. The one-line fix is to make the path explicit in the unit:

```ini
ExecStart=... --terminal-journal /var/lib/sih-fleet/%i-terminal.json
```

`archive/EDGE_DEPLOYMENT.md` (the runbook this document supersedes) says only "Create a dedicated `sih-fleet` user" without specifying the home directory, so following it literally with a default `useradd` reproduces the crash loop. Status: **defect present in the committed unit at the time of writing; not fixed by this document.**

A secondary robustness note from the same code path: if `runtime.close()` raises at `src/edge_runtime.py:308`, then `hardware.close()` (`:309`) and the signal-handler restore (`:310-311`) are skipped. The final `safety_stop` was already written at `:306`, so the robot is left safe, but the sockets leak on the way out.

---

## 5. The multi-process UDP demonstration

**This is the strongest available evidence for requirement 6 (no central coordination server), because it can be packet-captured rather than argued.**

### Implementation status: the launcher exists and is tested

Earlier project notes recorded `UdpMulticastTransport` as unit-tested but the multi-process launcher as unwritten. **That note is stale.** The launcher is `src/distributed_demo.py` (366 lines), exposed as `edge_demo.py` at the repo root and as the `sih-edge-demo` console script (`pyproject.toml:27`). It is covered by `tests/test_edge_runtime.py:80`, `test_three_real_processes_exchange_authenticated_multicast`, which asserts distinct PIDs, observed peer messages, authenticated transport, met control deadlines, three distinct clock epochs, and zero robot-robot contacts. That test passes: `pytest tests/test_edge_runtime.py -q` reports 7 passed. Classification: **implemented and tested.**

### What the parent process is, and is not

The parent is a physics and lidar referee only. It integrates `World`, computes each robot's sensor frame, ships it down an OS pipe, and applies the returned wheel command (`src/distributed_demo.py:236-259`). It never forwards a peer message, never plans a route, never assigns a priority, and never picks an auction winner. Each child (`src/distributed_demo.py:40`) constructs its own `AMRBrain`, its own `UdpMulticastTransport` with `require_auth=True`, and its own `EdgeRuntime`, then coordinates with its siblings over the real multicast group. Children are started with the `spawn` context (`src/distributed_demo.py:176`), so they share no interpreter state with the parent.

Peer traffic never enters the pipe. The pipe carries exactly three message shapes — `tick` with a sensor frame, `actuation` with a wheel command, and `stop`/`report` — which is verifiable by reading `src/distributed_demo.py:78-92`.

The run is scored against five conditions (`src/distributed_demo.py:276-289`): distinct PIDs, every node received peer messages, zero auth/malformed/replay failures, zero deadline misses and zero sensor timeouts, and zero contacts of any kind.

### Launch: N processes on one machine

```bash
cd /path/to/SIH_Fleet_Sim
export SIH_FLEET_PSK="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"

python3 edge_demo.py --robots 3 --duration 30 --port 26123 \
        --allocation-policy auction \
        --output artifacts/edge-demo-live.json
```

Fewer than three robots is rejected outright (`src/distributed_demo.py:164-165`), which is requirement 1 enforced in code rather than by convention. `--no-realtime` runs as fast as IPC permits and is the right flag for CI, the wrong flag for a demo — see the provenance warning in [§6](#6-hardware-targets-and-resource-budget).

### Launch: one process per Pi, N Pis

`edge_demo.py` is single-host. Across physical robots you run `edge_node.py` once per board — the same `EdgeRuntime`, with the referee pipe replaced by the JSON/UDP hardware adapter:

```bash
# On AMR01 (repeat on each board with its own index, ports and interface address)
export SIH_FLEET_PSK="the-same-32-byte-secret-on-every-robot"
python3 edge_node.py --robot-id AMR01 --robot-index 0 --robots 3 \
        --scenario dense_aisles \
        --interface 192.168.10.21 \
        --peer-port 26123 --sensor-port 27101 --actuator-port 28101 \
        --terminal-journal /var/lib/sih-fleet/AMR01-terminal.json \
        --report /var/lib/sih-fleet/AMR01-report.json
```

Use the board's real NIC address, not `127.0.0.1` — with `0.0.0.0` the kernel picks an interface and multi-host multicast may silently select the wrong one. The switch or AP must pass the administratively scoped group `239.26.1.23` on UDP `26123`; consumer APs commonly have IGMP snooping or multicast-to-unicast conversion enabled, which is the single most likely cause of a fleet that starts cleanly and then never hears anyone.

**Honest gap:** `edge_node.py` will not do anything visible on its own. It binds `--sensor-port` and waits, and with no frames arriving it emits `safety_stop` every tick and increments `sensor_timeouts` (`src/edge_runtime.py:292-295`). There is **no sensor feeder in the repository** — `UdpJsonHardwareIO` (`src/edge_runtime.py:151`) is constructed only inside `main()` (`src/edge_runtime.py:384`), is referenced by no test, and no checked-in script sends to a sensor port. Running across N Pis therefore requires the operator to supply a driver, a ROS2 bridge, or a small feeder script emitting the schema in [§9](#9-sensor-and-localization-contract). Classification of the JSON/UDP hardware bridge: **implemented; the frame parser `sensors_from_dict` is unit-tested (`tests/test_edge_runtime.py:65`), the socket path is not exercised by any test.**

### What a judge sees

The JSON written by `--output` (and printed to stdout) contains, per node: a distinct `pid`, a distinct `clock_offset_s`, a distinct `session_id`, non-zero `brain.msgs_recv`, `transport.auth_failed == 0`, `runtime.deadline_misses == 0`, and the node's own `platform` and `python` strings. The top level carries `separate_processes`, `peer_messages_observed`, `authenticated_transport`, `control_deadlines_met`, and `contacts` broken out by kind. Exit code is 0 only if all of those hold (`src/distributed_demo.py:362`).

Alongside it, `ps` shows N interpreters:

```bash
ps -o pid,ppid,etime,pcpu,rss,comm -C python3        # Linux
```

### Packet-capture proof that there is no server

Run these in a second terminal while the demo is running. The first is the persuasive one.

```bash
# 1. Watch the multicast group. Every datagram is robot -> group. There is no
#    unicast flow to any coordinator, because there is no coordinator.
sudo tcpdump -ni any -vv 'udp port 26123 and host 239.26.1.23'

# 2. Same, saved for later inspection or for showing offline.
sudo tcpdump -ni any -w /tmp/fleet.pcap 'udp port 26123'

# 3. Count datagrams per source. Expect roughly equal counts from N sources and
#    NO source that receives without sending - the signature of a hub.
sudo tshark -i any -f 'udp port 26123' -T fields -e ip.src \
  | sort | uniq -c | sort -rn

# 4. Confirm every destination is the group address and nothing else.
sudo tshark -r /tmp/fleet.pcap -T fields -e ip.dst | sort -u
#    -> 239.26.1.23     (one line, and only one line)

# 5. Show the payload is authenticated, not plaintext commands from a master.
sudo tshark -r /tmp/fleet.pcap -x -c 2
```

Capture 4 is the argument in one line: if a central coordinator existed, some datagrams would be addressed to it. Every destination is the group.

The kill test makes the same point behaviourally: terminate any one node (`kill <pid>` from the `ps` output) and the remaining nodes keep coordinating, because there was never a node whose loss stops the others. Traffic in the capture drops by one source and continues.

Two caveats a well-prepared teammate should volunteer before a judge finds them. First, on a single host `IP_MULTICAST_LOOP` is enabled (`src/transport.py:248`), so each node also receives its own datagrams — the `recv` counts include self-traffic. Second, `tcpdump` needs root or `CAP_NET_RAW`; on a locked-down demo laptop, run captures 2-5 from a `.pcap` recorded earlier rather than discovering this on stage.

---

## 6. Hardware targets and resource budget

### Measured, on non-target hardware

Two runs exist. Both are real measurements of the real code; neither is a Pi.

| | Checked-in artifact | Reproduced for this document |
|---|---|---|
| File | `artifacts/benchmarks/bios6-distributed-demo.json` | fresh run, not committed |
| Host | macOS 26.6.2, arm64 | Windows 11, x86-64 (Intel, Family 6 Model 186) |
| Python | 3.14.7 | 3.13.14 |
| Repo state | as committed | `7740efb` |
| Scenario / fleet | `open_floor_control`, 3 robots, `auction` | same |
| Robot-time duration | 120 s (6000 ticks/node) | 6 s (300 ticks/node) |
| Wall time | **1.52 s — run with `--no-realtime`** | 6.26 s — genuine realtime |
| Loop mean (range across the 3 nodes) | 0.077 - 0.086 ms | 0.590 - 0.639 ms |
| Loop p95 | 0.249 - 0.283 ms | 1.750 - 2.061 ms |
| Loop p99 | 0.368 - 0.400 ms | 2.915 - 3.227 ms |
| Loop max | 2.24 - 3.03 ms | 10.47 - 11.09 ms |
| Deadline misses (> 20 ms) | 0 of 6000 | 0 of 300 |
| Sensor timeouts | 0 | 0 |
| Peak RSS per node | 29.5 MB | 28.1 MB |
| Robot-robot contacts | 0 | 0 |
| Auth / malformed / replay failures | 0 | 0 |

Loop timings come from `EdgeMetrics` (`src/edge_runtime.py:63-88`) and cover `transport.poll()` + `brain.step()` + all `transport.send()` calls — the genuine on-node cost including socket syscalls. Peak RSS comes from `_max_rss_mb` (`src/distributed_demo.py:106`), which measures on both POSIX and Windows rather than returning a convenient zero.

**Provenance warning about the checked-in artifact.** Its `wall_time_s` is 1.52 s for a `duration_s` of 120 s, which means it was produced with `--no-realtime`. Its per-tick costs and its zero-collision result are valid; its "120 seconds" is 120 seconds of *simulated* robot time compressed into 1.5 s of wall time. It is **not** evidence of two minutes of sustained wall-clock 50 Hz operation. The Windows run above is realtime (6.26 s wall for 6.0 s requested) and is the one to cite for sustained-rate claims — for six seconds.

The mean-cost gap between the two hosts (0.083 ms vs 0.61 ms) is socket syscall overhead, not algorithmic. Isolating the brain confirms it: with the transport replaced by a null object, on the same Windows machine, over 12,000 samples (4 robots x 3000 ticks, `dense_aisles`, `BIOS_PIBT.6`, with peer messages actually fanned out between the four brains):

| Pure `AMRBrain.step`, no sockets | ms |
|---|---|
| mean | 0.080 |
| p50 | 0.060 |
| p95 | 0.190 |
| p99 | 0.305 |
| max (tick 0, cold start) | 2.20 |
| steady-state max (excluding tick 0) | 2.16 |
| samples above 5 ms | 0 of 12,000 |

So ~0.08 ms is computation and ~0.53 ms was Windows socket overhead. Linux socket syscalls are much closer to the macOS figure, which is why the macOS *with-sockets* mean (0.083 ms) sits at the pure-compute cost.

### Network budget, measured

From the 120 s artifact, per node, per second of robot time:

| Metric | Value | Derivation |
|---|---|---|
| Messages sent | 11.5 /s | 1379 / 120 s |
| Bytes sent | 3.1 kB/s (~25 kbit/s) | 374,419 / 120 s |
| Bytes received | 9.5 kB/s (~76 kbit/s) | 1,145,200 / 120 s (3 senders incl. own loopback) |
| Mean datagram | 272 bytes | 374,419 / 1379 |
| Representative signed heartbeat | 267 bytes | measured via `msg.encode` with a 32-byte key |
| Representative signed intent (6-cell horizon) | 284 bytes | as above |
| Hard datagram ceiling | 2048 bytes | `src/messages.py:56` |
| Modelled MTU | 1400 bytes | `src/settings.py:127` |

Because the protocol is broadcast, per-node *receive* load scales linearly with fleet size at ~3.1 kB/s per additional robot: ~31 kB/s (250 kbit/s) at 10 robots, ~62 kB/s (500 kbit/s) at 20. Negligible for any 802.11n/ac link in raw bandwidth. The practical constraint is not bandwidth but that 802.11 transmits multicast at a low basic rate without acknowledgement, so airtime and loss — not throughput — are what degrade first. That trade-off is analysed in [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md), and the dead-zone consequence is argued honestly in the module docstring at `src/transport.py:14-25`.

### Raspberry Pi and Jetson Nano — ESTIMATE, not measurement

**No number in the following table was measured on the named device.** Each is the measured pure-compute figure above multiplied by a single-thread CPython slowdown factor for that SoC. Treat the factors as engineering judgement.

| Target | SoC / clock | Assumed slowdown vs the measured x86 core | Est. mean/tick | Est. p99/tick | Est. worst (cold start) | Est. CPU, one node at 50 Hz | Est. RSS |
|---|---|---|---|---|---|---|---|
| Raspberry Pi 5 | Cortex-A76, 2.4 GHz | 3.5x | 0.28 ms | 1.07 ms | 7.7 ms | ~1.4% of one core | ~30 MB |
| Raspberry Pi 4B | Cortex-A72, 1.5 GHz | 8x | 0.64 ms | 2.44 ms | 17.6 ms | ~3.2% of one core | ~30 MB |
| Jetson Nano | Cortex-A57, 1.43 GHz | 10x | 0.80 ms | 3.05 ms | **22.0 ms** | ~4.0% of one core | ~30 MB |

RSS is estimated flat because the resident set is dominated by the CPython interpreter and the loaded modules, not by fleet state; both measured hosts landed within 1.5 MB of each other on different interpreters and different operating systems. Even four nodes co-resident on one 2 GB Pi would sit near 120 MB.

**Replace the estimate with a measurement in one command.** A portable pure-CPython calibration loop ran in **0.3532 s** on the measured x86 machine. On the target:

```bash
python3 -c "
import time
t=time.perf_counter(); s=0.0
for i in range(2_000_000): s += (i*3+1)%7*0.5
print('calibration_loop_s =', round(time.perf_counter()-t, 4))"
```

Divide the Pi's result by 0.3532 to obtain the real slowdown factor, then rescale the pure-compute column. Better still, skip the arithmetic and run the harness itself on the board — see [§8](#8-what-has-run-on-real-hardware).

---

## 7. Real-time feasibility

The safety loop targets 50 Hz (`src/settings.py:108`), so each tick has 20 ms. The coordination and route layers run slower — reactive at 10 Hz, route at 1 Hz, heartbeat at 5 Hz (`src/settings.py:109-111`) — and are subsumed inside the same tick, so 20 ms is the only budget that has to be met.

| Figure | Value | Fraction of the 20 ms budget | Headroom |
|---|---|---|---|
| Measured p99, pure compute, x86 | 0.305 ms | 1.5% | 66x |
| Measured p99 with real sockets, macOS arm64 (worst node) | 0.400 ms | 2.0% | 50x |
| Measured p99 with real sockets, Windows x86 (worst node) | 3.227 ms | 16% | 6.2x |
| Measured worst single tick, realtime Windows run | 11.09 ms | 55% | 1.8x |
| *Estimated* p99, Pi 4B | 2.44 ms | 12% | 8.2x |
| *Estimated* p99, Jetson Nano | 3.05 ms | 15% | 6.6x |
| *Estimated* cold-start tick, Jetson Nano | 22.0 ms | **110%** | **does not fit** |

Steady-state cost fits with large margin on every named target. Measured deadline misses are zero across 18,900 ticks of realtime and non-realtime multi-process running.

The single figure that does not fit is the estimated cold-start tick on the slowest target. The outlier is well characterised: the four slowest of 12,000 pure-compute samples were the first tick of each robot — 2.20, 1.83, 1.83 and 1.58 ms, against a steady-state 0.06 ms median — because the initial route plan is computed there. On a Jetson Nano at a 10x factor that becomes roughly 22 ms, one tick over budget, occurring once at startup while the robot is stationary and before it has been commanded to move. It is a startup transient, not a control-loop risk. If it needed removing, the fix is to plan the initial route before entering the loop rather than inside the first tick.

### What happens when a tick overruns

Nothing degrades the decision; the *rate* degrades, visibly and honestly. The mechanism is at `src/edge_runtime.py:303-304`:

```python
next_tick += period
time.sleep(max(0.0, next_tick - time.monotonic()))
```

- **Single overrun.** `next_tick - now` is negative, the sleep is zero, and the next tick begins immediately. `next_tick` is *not* re-phased to the present, so the loop runs back-to-back ticks until it catches up. A 100 ms stall produces a burst of five zero-sleep ticks. `deadline_misses` increments once for the overrunning tick (`src/edge_runtime.py:68-69`).
- **Sustained overrun** (mean cost above 20 ms). The loop can never catch up and free-runs at whatever rate the hardware allows. There is no throttle, no rate-halving fallback, and no alarm.
- **What does not break.** The brain's notion of time is `clock_offset_s + (now - started)` from the real monotonic clock (`src/edge_runtime.py:291`), never a tick counter. A slow node therefore samples the world less often but never believes the wrong time; its heartbeats and intents stay truthful and its peers' TTL-based reasoning stays correct. It simply reacts later — and if it falls far enough behind that its own sensor frames age past 0.25 s, the staleness check at `src/edge_runtime.py:292` converts the degradation into an explicit safety stop rather than into stale-data driving.
- **What is not implemented.** No graceful degradation ladder — the node does not, for example, drop to a 25 Hz safety-only mode under load. The evidence that it is not needed is the 6.2x-66x measured headroom; the honest statement is that the ladder does not exist.

Detection after the fact is straightforward: `deadline_misses`, `loop_p95_ms`, `loop_p99_ms` and `loop_max_ms` are in every node's final JSON report (`src/edge_runtime.py:80-88`), and the multi-process demo fails the run outright if any node reports a miss (`src/distributed_demo.py:283-286`).

---

## 8. What has run on real hardware

**Nothing in this project has been executed on a physical Raspberry Pi or Jetson Nano.** No Pi, no Nano, no ARM SBC of any kind. Every timing, CPU and memory number in this document was produced on a macOS arm64 laptop or a Windows x86-64 laptop. Any statement in the submission implying otherwise is wrong.

What is genuinely established:

| Claim | Status |
|---|---|
| The agent path imports nothing outside the standard library | Verified by AST audit ([§1](#1-one-brain-two-transports)) |
| The same `AMRBrain` runs in the batch benchmark and in the deployed runtime | Verified — identical call site, `src/main.py:338` and `src/edge_runtime.py:109` |
| N robots run as N independent OS processes with real authenticated UDP multicast and unrelated clock epochs | Implemented and tested — `tests/test_edge_runtime.py:80`, passing |
| Zero robot-robot contacts in the multi-process run | Measured, on a laptop — `src/distributed_demo.py:287` |
| Per-tick cost fits inside 20 ms with 6.2x-66x headroom | Measured, on a laptop |
| Stale or missing sensor frames produce an explicit stop | Implemented (`src/edge_runtime.py:292-295`); the parser is tested, the socket path is not |
| A hardened per-robot systemd service exists | Implemented, never installed on a real board, and carries the journal-path defect in [§4](#4-systemd-deployment) |
| Per-node CPU / RAM / sustained 50 Hz **on a Pi or Nano** | **Not measured. Estimated only.** |
| The JSON/UDP bridge driving real motors or a real lidar | **Never run.** No sensor source exists in the repository. |

### Closing the gap

The work is small and fully specified; only the hardware is missing.

1. Flash Raspberry Pi OS (64-bit) on a Pi 4B or Pi 5. Its stock Python 3.11 satisfies `requires-python >= 3.10`.
2. `git clone` the repository. Install nothing — the node path has no dependencies. `pytest` is needed only if you want the test suite.
3. Run the calibration one-liner from [§6](#6-hardware-targets-and-resource-budget) and record the number. That alone converts every estimate in this document into an arithmetic result.
4. Run the multi-process demo on the single board, at realtime, for two minutes:
   ```bash
   export SIH_FLEET_PSK="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
   python3 edge_demo.py --robots 3 --duration 120 --allocation-policy auction \
           --output artifacts/edge-demo-pi5.json
   ```
   Three nodes on one Pi is a *harder* test than one node per Pi, because they contend for the same cores. Do not pass `--no-realtime`.
5. Commit `artifacts/edge-demo-pi5.json` unaltered. It already contains `platform`, `python`, `pid`, `cpu_time_s`, `max_rss_mb`, loop mean/p95/p99/max, `deadline_misses`, `sensor_timeouts` and the full transport counters (`src/distributed_demo.py:68-75`, `src/edge_runtime.py:130-142`) — every field a reviewer would ask for, without editing.
6. For the multi-board claim, repeat across three Pis on one AP using `edge_node.py`, and capture the multicast traffic per [§5](#5-the-multi-process-udp-demonstration). This step additionally requires writing a sensor feeder, which does not exist today.

Steps 1-5 take under an hour with a board in hand and would replace the entire estimate section with measurement.

---

## 9. Sensor and localization contract

For a real robot to run this code unchanged, its driver must emit one JSON datagram per control cycle to the node's `--sensor-port` matching the schema parsed by `sensors_from_dict` (`src/edge_runtime.py:222-263`). All units are SI. Validation is strict: any non-finite or out-of-range number raises and the frame is discarded as `invalid_sensor_frames` (`src/edge_runtime.py:186-191`) rather than being clamped, because a silently clamped pose is worse than a dropped frame.

| Field | Required | Type / range | Meaning | Line |
|---|---|---|---|---|
| `pose` | yes | `[x, y, theta]`, each finite, abs <= 1e7 | Metric pose in the map frame; metres, radians CCW | `:252` |
| `cell` | yes | `[int, int]` (booleans rejected) | Grid cell the robot occupies. See the localization note below. | `:229-231` |
| `v` | yes | float, -20..20 | Forward speed, m/s | `:254` |
| `omega` | yes | float, -100..100 | Yaw rate, rad/s CCW | `:254` |
| `battery_frac` | yes | float, 0..1 | State of charge. **This is the source for requirement 18.** | `:255` |
| `clearance_m` | yes | float, >= 0 | Nearest obstacle of any kind in the forward cone, m | `:257` |
| `clearance_static_m` | no (default 99.0) | float, >= 0 | Nearest *mapped* obstacle — shelving, walls | `:258` |
| `clearance_dynamic_m` | no (default 99.0) | float, >= 0 | Nearest *unexpected* object in the forward cone | `:259` |
| `clearance_omni_m` | no (default 99.0) | float, >= 0 | Nearest unexpected object in **any** direction, 360 deg | `:260` |
| `detections` | no (default `[]`) | list, max 1024 objects | Unlabelled obstacles: `x`, `y`, `r`, `range_m`, optional `vx`, `vy` | `:232-246` |
| `on_dock` | no (default `false`) | strict boolean | Robot is physically on a charging dock | `:247-249` |

The node replies to `--actuator-port` with `{"v":..., "omega":..., "safety_stop":..., "t":...}` (`src/edge_runtime.py:197-203`), where `t` is the node's own monotonic time. `safety_stop` is set by the certified local layer and the driver should treat it as a command to stop, not as advice.

Five obligations fall on the integrator, and they are where a real deployment would actually cost effort:

1. **Grid localization, not just metric pose.** `cell` must be supplied, and it must be consistent with `pose` under `to_cell(p, cell_m) = (floor(x/1.4), floor(y/1.4))` (`src/geometry.py:38`, `src/settings.py:305`, `cell_m = 1.4` m). The robot must therefore be localized in a map whose origin and cell size match the deployed warehouse definition. Nothing in the node cross-checks the two, so a driver that reports a correct pose and a stale cell will produce coherent-looking but wrong coordination.
2. **Segmented clearances from the safety scanner.** The three optional clearance fields default to 99.0 m — "nothing anywhere near me". A driver that omits them does not get conservative behaviour, it gets blind behaviour. In particular `clearance_omni_m` is the only field that sees a peer merging from 90 degrees at a junction, which is precisely the chokepoint case in requirement 11. Supplying only `clearance_m` is a silent safety downgrade.
3. **Identity-free detections.** `Detection` (`src/world.py:60`) deliberately carries no identity: the reactive layer must work on blobs, not on the peer table, because people, forklifts and dropped pallets do not broadcast. The driver should pass raw lidar clusters and must not filter out objects it believes are peers.
4. **A frame at least every 0.25 s.** Anything slower trips the staleness stop (`src/edge_runtime.py:268`, `:293`). At the 50 Hz loop rate, one frame per tick is the intended cadence.
5. **The map and task source, which is the real integration seam.** A node bootstraps its warehouse grid, home cell and initial task queue from a named `SCENARIO` (`src/edge_runtime.py:350-351`), not from the live plant. `scenario.env` is a `Warehouse` (`src/environment.py:37`) — width, height, an occupancy grid, station cells and dock cells. Deploying into a real warehouse means constructing that object from the site map and feeding real work in, either through the auction path (`TASK_NEW` messages on the same multicast group, as the demo's `WMS` sender illustrates at `src/distributed_demo.py:210-229`) or a WMS bridge. **This is not implemented for a real facility**, and it is the largest remaining piece of real-hardware integration work after the sensor driver.

---

## Status summary

| Component | Classification |
|---|---|
| `AMRBrain` shared between benchmark and node | Implemented and tested |
| Stdlib-only agent path, no build step | Verified |
| `EdgeRuntime` 50 Hz loop, absolute-deadline scheduling | Implemented and measured |
| Sensor-staleness safety stop | Implemented; parser tested, socket path untested |
| Signal-driven clean shutdown with final stop command | Implemented, not covered by a test |
| `UdpMulticastTransport` (real authenticated multicast, replay window) | Implemented and tested |
| Multi-process launcher `src/distributed_demo.py` | **Implemented and tested** (contradicts stale earlier notes) |
| `UdpJsonHardwareIO` JSON/UDP driver bridge | Implemented, never exercised end-to-end |
| Sensor feeder / real robot driver | Not implemented |
| Real warehouse map and WMS ingestion | Not implemented |
| systemd template unit | Implemented, never installed on a real board, one known defect |
| Liveness watchdog / degradation ladder | Not implemented |
| Pi / Jetson CPU, RAM, sustained-rate figures | **Estimated only — never measured on target hardware** |

## Related documents

- [00. Problem Statement](00-PROBLEM-STATEMENT.md) — the edge-versus-simulation tension this document answers
- [01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) — the full 20-requirement matrix
- [02. Architecture](02-ARCHITECTURE.md) — the layered design the runtime instantiates
- [03. Decentralized Protocol](03-DECENTRALIZED-PROTOCOL.md) — message types, signing, and the dead-zone finding
- [07. Safety](07-SAFETY.md) — what the certified local layer does inside each tick
- [12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) — the batch results this runtime shares code with
- [13. Testing](13-TESTING.md) — `tests/test_edge_runtime.py` in the context of the whole suite
- [15. Limitations](15-LIMITATIONS.md) — the honest ledger this document contributes to
- [16. Demo Runbook](16-DEMO-RUNBOOK.md) — the live sequence, including the packet capture
