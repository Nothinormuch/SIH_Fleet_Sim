# Edge deployment and team-member runbook

This runbook covers a clean clone, the authenticated multi-process proof, and one
independent node per AMR. The batch simulator remains dependency-free; development and
report tooling live in the virtual environment.

## Clean clone and virtual environment

```bash
git clone https://github.com/saksham001k/SIH_Fleet_Priority.git
cd SIH_Fleet_Priority
git switch codex/edge-integration-hardening   # until the branch is reviewed

python3 -m venv .venv
source .venv/bin/activate                     # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

python -m pytest -q
python backend/server.py                      # http://127.0.0.1:8000
```

Use `python -m ...` or the `.venv/bin/python` path after cloning. Do not run commands
from a different repository copy: `git rev-parse --show-toplevel` should print the
directory you just cloned.

## Prove that the brains are separate processes

```bash
source .venv/bin/activate
export SIH_FLEET_PSK="$(python -c 'import secrets; print(secrets.token_hex(32))')"
python edge_demo.py --robots 3 --duration 5 --port 26231 \
  --allocation-policy auction --output artifacts/edge-demo-local.json
```

The JSON must report different PIDs, different clock offsets, peer messages on every
node, authenticated transport, no missed 20 ms control deadlines, and no contacts. The
parent process supplies physics and lidar only; it neither forwards peer messages nor
chooses routes, priorities, bids, or actuator commands.

To observe the actual datagrams on Linux:

```bash
sudo tcpdump -ni any udp port 26231
```

## Connect a real robot driver

Run one `edge_node.py` process per robot. It receives sensor JSON on its `--sensor-port`,
sends actuator JSON to `--actuator-port`, and exchanges signed multicast peer messages
on `--peer-port`. The sensor packet uses SI units:

```json
{
  "pose": [1.4, 2.8, 0.0],
  "v": 0.0,
  "omega": 0.0,
  "battery_frac": 0.82,
  "cell": [1, 2],
  "clearance_m": 4.0,
  "clearance_static_m": 4.0,
  "clearance_dynamic_m": 4.0,
  "clearance_omni_m": 4.0,
  "detections": [],
  "on_dock": false
}
```

Each actuator packet is `{"v":...,"omega":...,"safety_stop":...,"t":...}`. Missing or
stale sensor frames cause an explicit zero-speed safety stop; invalid/non-finite fields
are discarded.

Example development command:

```bash
export SIH_FLEET_PSK="a-real-shared-secret-from-your-secret-store"
python edge_node.py --robot-id AMR01 --robot-index 0 --robots 3 \
  --scenario dense_aisles --interface 192.168.10.21 \
  --sensor-port 27101 --actuator-port 28101 --report AMR01-report.json
```

Use the address of the AMR network interface, not `127.0.0.1`, for multi-host multicast.
The network must allow the administratively scoped group `239.26.1.23` and UDP port
`26123`. Each machine uses its own monotonic clock; wire time windows are relative TTLs,
so NTP synchronisation is helpful for logs but not required for correctness.

## Raspberry Pi service

1. Create a dedicated `sih-fleet` user and install the repository in `/opt/sih-fleet`.
2. Create `/opt/sih-fleet/.venv` and install the project.
3. Copy `config/edge-node.example.env` to `/etc/sih-fleet/AMR01.env`, set the real
   values, and run `sudo chmod 600 /etc/sih-fleet/AMR01.env`.
4. Create `/var/lib/sih-fleet`, owned by the service user.
5. Copy `deploy/systemd/sih-edge-node@.service` into `/etc/systemd/system/`.
6. Run `sudo systemctl daemon-reload && sudo systemctl enable --now
   sih-edge-node@AMR01`.

Inspect `journalctl -u sih-edge-node@AMR01` and the JSON report. Record the exact Pi
model, OS image, Python version, node count, run duration, CPU time, peak RSS, loop
mean/p95/p99/max, deadline misses, sensor timeouts, and transport counters.

## What is and is not proven

The checked-in local test proves process isolation, real authenticated UDP multicast,
independent clock epochs, fail-safe sensor handling, and timing on the machine that ran
it. It is not a Raspberry Pi measurement. A release claim about Pi CPU/RAM or sustained
50 Hz operation requires running the command on the named Pi/Jetson and checking in its
unaltered JSON report.
