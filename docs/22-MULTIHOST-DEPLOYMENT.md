# Two-computer BIOS deployment proof

This demo runs separate `edge_node.py` controllers on a Mac and Windows computer.
With three AMRs, the Mac owns AMR01/AMR02 and Windows owns AMR03. With ten AMRs,
each computer owns five. Robot bodies, localization, people, battery and contacts
remain simulated on the Mac. No Raspberry Pi CPU or physical AMR is being emulated.

The Mac referee only supplies sensors, applies commands through per-robot watchdogs,
announces work and observes evidence. Each independent edge process performs its own
auction and traffic decisions. Authenticated robot multicast travels directly over
the selected Ethernet interfaces; the referee does not relay peer packets or select
auction winners. Closing the dashboard does not stop the controller processes.

## Prepare both computers

Use the **exact same candidate source** on both machines. A Windows installation of
the older personal `main` is intentionally rejected. The session pins hashes of all
`src/*.py`, `edge_node.py`, `multihost_demo.py`, the generated scenario and settings.
Create the session only after the candidate is frozen; regenerate it after edits.
Copy source files byte-for-byte (for example, a prepared source archive). A Windows
Git checkout that converts LF to CRLF will intentionally have a different source hash;
do not disable the compatibility check to hide that mismatch.

Use a connected Ethernet interface on each computer. The initial addresses supplied
for this setup are Mac `169.254.90.241` and Windows `169.254.250.160`; verify the
actual addresses before preparing. The application does not change network settings.
Permit the selected bridge TCP port into the Mac and the multicast UDP peer port on
both Ethernet interfaces, scoped to this trusted link. Do not disable the firewall
globally. Application-only link-local testing does not prove warehouse Wi-Fi coverage.

From the Mac repository with its virtual environment active:

```bash
python multihost_demo.py prepare \
  --mac-ip 169.254.90.241 --windows-ip 169.254.250.160 \
  --robots 3 --duration 25 \
  --scenario deployment_socket_acceptance \
  --windows-repo 'C:\BIOS7' \
  --output artifacts/deployment/ethernet-3.json
```

The command creates a session JSON and a private `.key` file, and prints the exact
Mac referee, Mac agent and Windows PowerShell commands. Change `--windows-repo` to
the actual Windows repository location. Copy the session JSON and `.key` into that
Windows repository root alongside the matching candidate. Keep the key private;
never upload it to GitHub or put it in screenshots. It is not part of the evidence.

Start the printed **Mac referee** command first. Then run the Mac agent in a second
terminal and the Windows agent in PowerShell. Work is not announced until both hosts
are authenticated and every controller has returned an actuator frame linked to a
fresh sensor sample. The default readiness timeout is 120 seconds.

The initial `deployment_socket_acceptance` scenario uses independent lanes to prove
the deployment boundary and actual cross-host peer communication. It is **not** a
traffic-performance comparison. It deliberately interrupts the first Windows AMR's
sensor input at three seconds for two seconds and checks stopping and recovery.

The default policy is BIOS 6. For the experimental BIOS 7 candidate, explicitly
add `--policy BIOS_PIBT.7` when preparing the session. Policy is pinned in the
configuration and cannot be changed silently between the two computers.

### Passive live warehouse view

Run `python backend/server.py 8001` on the Mac and open
`http://127.0.0.1:8001/edge-lab.html?source=multihost`. The referee publishes a
latest-only local snapshot; the browser shows real controller PIDs, host/IP
ownership, simulated movement and observed packets. Launch/stop/fault controls
are disabled in this view. Closing it does not stop the terminal-owned run.
The separate local Edge Lab page remains available without `?source=multihost`.

Use `referee --no-view` to measure without snapshot publishing. Reports must state
whether the viewer was active; neither view mode is a physical Pi measurement.

After it works, use a fresh output name and `--robots 10`. For shared-floor operation,
choose `edge_overlap`, `edge_chokepoint`, `blocked_aisle`, `robot_failure_reassignment`
or `edge_human_crossing` and a suitable evidence window, e.g. `--duration 240`.
Use `--no-sensor-cut` when isolating the other faults. The declared faults must occur
and all declared tasks must finish for a passing result. A small window is allowed
to fail; it is not silently stretched or treated as completed.

## Interpreting the result

- A JSON report records actual host platform/hostname, robot-process PID mappings,
  node reports, source/workload identity, completion, contacts and timing violations.
- Completion requires a direct owner's authenticated certificate, matched to the
  announced descriptor and received inside the evidence window, plus final node-report
  confirmation. Work finished during shutdown cannot turn a timeout into a pass.
- `cross_host_peer_sources_by_robot` must show that every node accepted authenticated
  traffic from an AMR assigned to the other host. Receiving only WMS announcements
  is not sufficient.
- `sensor_to_motion_actuation_age_ms` measures a causally linked sensor sample's
  send-to-command-return age on the referee's clock. It includes both transport
  directions, driver scheduling and control computation. It is not a one-way network
  latency claim. Motion older than the command-watchdog bound is rejected. A received
  command retains its originating sensor sample's expiry; receipt does not renew it.
- The sensor-loss response is time until a stop command, not physical stopping time.
  The simulated body still brakes according to the configured motion model.
- Controller and full-cycle deadline misses, skipped scheduler slots, referee timing
  overruns, unexpected stale motion, missing reports, incomplete tasks and contacts fail the gate.
  Real OS/network timing is nondeterministic even though the scenario seed is fixed.
- `real_multihost` only reports different hostnames observed through authenticated
  host agents. It is not hardware attestation or evidence of three physical boards.
  A loopback smoke explicitly records `false`.
  `software_boundary_pass` separates the software checks from this host requirement;
  overall `success` cannot pass a loopback run as a two-computer deployment.

During an injected sensor outage, the referee's 100 ms sensor-origin command watchdog
can reject old motion before the edge node's 200 ms sensor-timeout emits its stop.
Those deliberately induced rejections are reported separately, and never applied to
the simulated robot. Unexpected stale motion outside that injected window fails.
The node stop-response number is therefore not the time of the earlier watchdog stop
or the time the physical body finishes braking.

To stop, press Ctrl+C in the referee terminal. Agents receive a session-authenticated
stop, gracefully stop their child controllers and return final reports. On link loss,
agent timeouts stop local processes and the referee watchdog rejects stale commands.
If graceful shutdown fails, the launcher kills only the child PIDs it created and
reports missing evidence; a missing report cannot pass. Windows uses a separate
console process group with Ctrl+Break handling for graceful reports.

## Security and supported fault scope

The LAN bridge uses HMAC-SHA256, fresh server/client nonces, strict directional frame
sequences, a fixed host-IP/robot assignment and source/configuration matching. It
provides authenticity and integrity, **not encryption**. Possession of the fleet key
is trusted; this is not Byzantine consensus or compromised-host isolation. Local
sensor/actuator UDP remains loopback-only and assumes trusted local processes.

Frame sizes, handshake deadlines, receive work and retained sensor histories are
bounded. Every motion command must echo a sensor causality token. The referee does
not infer that a stale or disconnected robot has physically disappeared.

Supported live events are sensor suppression, physical obstacle appearance/removal
and controlled controller-process stops with task recovery. A TCP bridge interruption
tests the simulated hardware link, not a peer-radio partition. Network partition and
restart scenarios are rejected instead of being silently simulated incorrectly.

## Automated local verification

```bash
python -m pytest tests/test_multihost_demo.py -q
```

The tests cover authenticated-frame tampering, replay/session separation, final-report
framing, source/map mismatch rejection and two host-agent processes on one loopback
network. This smoke proves software boundaries only. Passing on a Mac cannot be
reported as a measured Windows or Raspberry Pi result.
