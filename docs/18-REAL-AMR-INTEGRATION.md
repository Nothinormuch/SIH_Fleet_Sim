# 18. REAL AMR INTEGRATION AND PROOF PLAN

> The exact answer to “we bought AMRs; how does BIOS run on them, and how do you prove
> it before risking hardware?”

BIOS does not replace a commercial AMR's drive controller, localization stack, safety
PLC, scanner, brakes, or emergency stop. One Raspberry Pi or Jetson runs one
`edge_node.py` process beside that controller. A small vendor adapter is the only
brand-specific component.

## 1. Deployment architecture

```text
WMS / ERP
  |  authenticated TASK_NEW announcement only; no winner assignment
  v
warehouse LAN or dedicated peer radio
  |
  +--------------------+--------------------+
  |                    |                    |
  v                    v                    v
Pi / Jetson AMR01  <-> Pi / Jetson AMR02 <-> Pi / Jetson AMR03
edge_node.py            edge_node.py          edge_node.py
  | JSON/UDP adapter      |                     |
  v                       v                     v
vendor controller       vendor controller     vendor controller
  |                       |                     |
motors + encoders + certified scanner/brakes/E-stop
```

The access point may forward packets, but it never chooses a task winner, route, or
right-of-way. Each BIOS process receives peer state and independently runs the same
auction and traffic rules. The dashboard is an observer.

## 2. The stable integration contract

The existing `HardwareIO` boundary is implemented by `UdpJsonHardwareIO` in
`src/edge_runtime.py`. A driver sends a fresh sensor frame at the configured control
rate with:

- map-frame pose `[x, y, theta]` in metres/radians;
- measured `v` and `omega`;
- battery state of charge from 0 to 1;
- grid cell derived from the deployed, versioned map;
- forward static/dynamic and 360-degree dynamic clearances;
- anonymous obstacle detections with position, radius, range and estimated velocity;
- charging-dock contact state.

BIOS returns only `v`, `omega`, `safety_stop`, and its monotonic timestamp. The vendor
adapter clamps commands to the purchased AMR's documented envelope and converts them
to that controller's API. If BIOS frames stop arriving, the adapter and controller must
independently command zero motion. BIOS is not inserted into the certified emergency
stop chain.

Possible adapters are:

- ROS 2 topics/services for a ROS-capable AMR;
- the manufacturer's Ethernet SDK or REST/UDP/TCP interface;
- CAN or CANopen through an isolated interface;
- RS-485/Modbus or vendor serial protocol;
- VDA 5050/MQTT at the order/state boundary, where the AMR supports it.

There is no honest “universal cable.” Compatibility requires documented access to pose,
battery, safety/fault state and a supported waypoint or velocity command. An AMR that
exposes no external control API needs manufacturer cooperation and cannot be made
compatible by software alone.

## 3. Configuring a different warehouse and robot

`config/site.example.json` is the deployment source of truth. It contains a fleet id,
map version/frame, grid, stations, docks, AMR starts, cell pitch, footprint, motion
limits, braking assumptions, battery envelope and payload capacity. `src/site_config.py`
rejects unknown fields, malformed/non-finite values, racks used as operational cells,
duplicate starts, disconnected docks/stations and a lane pitch smaller than the
configured footprint plus safety margins.

`config/tasks.example.json` is the WMS input contract. `task_injector.py` validates
pickup/drop reachability, cargo type, weight, priority, deadline and generation, then
broadcasts authenticated task announcements. It never names a robot and never sends an
award:

```bash
export SIH_FLEET_PSK="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
python task_injector.py \
  --site-config config/site.example.json \
  --tasks config/tasks.example.json
```

## 4. What can be proved without buying AMRs

Run:

```bash
python deployment_acceptance.py --duration 20
```

This is a distributed software-in-the-loop test, not a mock UI. It launches three public
`edge_node.py` executables. The physics referee sends every node sensor JSON over UDP,
the nodes exchange authenticated multicast directly, and their returned actuator JSON
drives the warehouse. The referee never forwards peer messages or chooses auction
winners.

The single JSON result proves, for that measured run:

1. three distinct edge-node processes ran;
2. all crossed the production sensor/actuator socket boundary;
3. authenticated peer messages were received;
4. Auction V2 completed 100% of the short declared deployment workload;
5. no deadline was missed;
6. contacts were measured by category;
7. a forced sensor outage caused a bounded fail-safe stop and recovered after frames
   returned;
8. a wrong fleet key was rejected;
9. a duplicate authenticated datagram was rejected as replay;
10. the site and WMS task inputs passed strict validation.

The output explicitly records the host platform and whether Linux's device tree really
identified it as a Raspberry Pi. ARM emulation or an ARM laptop is not mislabeled as a
Pi.

## 5. Digital Raspberry Pi versus physical Pi

An ARM64 container or QEMU Raspberry Pi OS virtual machine is useful for checking
architecture, packaging, process isolation and startup scripts. It does **not** measure
Pi CPU scheduling, Wi-Fi behavior, GPIO/CAN hardware, thermal throttling, or physical
safety. Call it an “emulated ARM deployment,” not “tested on Raspberry Pi.”

The checked-in ARM64 image makes that limited claim reproducible:

```bash
docker buildx build --platform linux/arm64 --load \
  -f deploy/container/Dockerfile.arm64-proof -t bios-arm64-proof .
docker run --rm bios-arm64-proof
```

On an x86 host, Docker Buildx may execute it through QEMU; on an ARM host it executes
natively. In both cases `raspberry_pi_tested` remains false unless Linux's device tree
identifies a real Raspberry Pi.

The strongest low-cost proof is three actual Pi boards with the digital warehouse still
acting as the plant:

```text
laptop: warehouse physics + dashboard only
   | sensor UDP                 ^ actuator UDP
   v                            |
Pi 1 edge_node <peer radio> Pi 2 edge_node <peer radio> Pi 3 edge_node
```

This is hardware-in-the-loop. The laptop may calculate sensor frames and integrate
motion, but it may not forward peer packets, assign tasks, decide priority, or alter the
returned command. Capture UDP port 26123 to show that every coordination packet is
robot-to-multicast-group and there is no coordination server.

**Current status:** `hil_demo.py` launches all edge-node executables locally. Its packet
contract and destination fields are suitable for a network bridge, but a multi-host
referee launcher is not checked in yet. The three-Pi experiment therefore requires
either that small multi-host feeder or the actual vendor adapters. Do not tell a judge
that the current command already distributes the child processes across boards.

## 6. Commissioning ladder

Do not jump from a laptop simulation to three full-speed robots.

1. Run the one-command deployment gate on the development computer.
2. Run it unchanged on one Pi with three subprocesses; record board model, temperature,
   CPU/RAM and loop p95/p99/max.
3. Add a multi-host sensor/actuator feeder, or use the first vendor adapter, then run one
   edge node on each of three Pis through the digital warehouse; introduce packet loss,
   a wrong key, sensor loss, node termination and AP loss.
4. Implement one vendor adapter and replay recorded controller telemetry against it.
5. Connect one stationary AMR with wheels disabled; validate units, coordinate frame,
   battery, scanner fields, command limits and watchdog stop.
6. Use one AMR at walking speed inside a fenced test area.
7. Add AMR 2, then AMR 3, with physical E-stops and the vendor safety controller active.
8. Re-run chokepoint, blocked-aisle, task reassignment and network-loss acceptance on
   hardware, preserving raw logs and packet captures.
9. Complete the manufacturer/integrator risk assessment and applicable safety
   validation before operational use.

## 7. Acceptance evidence to show the jury

Keep one evidence folder containing:

- the exact Git commit and clean/dirty state;
- `deployment-acceptance.json` from the laptop and each Pi;
- one packet capture filtered to the peer group/port;
- per-node hostnames, IP addresses, PIDs, CPU/RAM and loop timing;
- sensor-cut, controller-watchdog, wrong-key, replay and node-loss results;
- a site-config fingerprint shared by all nodes;
- a short video showing the Pi labels, terminals, digital twin and a cable/network fault;
- the AMR manufacturer's API manual and the adapter field mapping;
- a limitations page stating what is simulated, measured, hardware-tested and certified.

Suggested measured gates are zero process crashes, zero invalid sensor/actuator frames,
zero control deadline misses, loop p99 below the 20 ms period, authenticated peer traffic
on every node, a sensor-staleness stop within the configured 200 ms timeout plus normal
control/transport scheduling and no later than the 300 ms acceptance ceiling, successful
recovery after sensor return, and zero observed contacts in
the declared campaign. Physical stopping distance must use the purchased AMR's measured
braking and controller reaction data rather than the example profile.

## 8. Standards position

- ISO 3691-4:2023 concerns safety requirements and verification for driverless
  industrial trucks. BIOS can be designed around that safety boundary, but this
  repository is not certified: <https://www.iso.org/standard/83545.html>.
- ISO 21423 is under publication in 2026 and addresses communication/interoperability
  among industrial AMR systems while excluding safety requirements:
  <https://www.iso.org/standard/86749.html>.
- VDA 5050 defines a vendor-neutral fleet-control/mobile-robot communication interface
  over MQTT/JSON, but explicitly leaves safety, traffic algorithms and cybersecurity
  outside its scope: <https://github.com/VDA5050/VDA5050/blob/main/VDA5050_EN.md>.
- The MassRobotics AMR Interoperability Standard focuses on sharing location, speed,
  direction, health and availability for multi-vendor coexistence; it is not itself a
  navigation or safety system:
  <https://github.com/MassRobotics-AMR/AMR_Interop_Standard>.

BIOS therefore borrows compatible data concepts but keeps its decentralized auction and
traffic protocol separate. Claim “vendor-adapter architecture aligned with open
interoperability work,” not “all AMRs are plug-and-play” or “VDA 5050 compliant.”

## 9. The exact jury answer

> We install one BIOS edge node on each AMR's Raspberry Pi or Jetson and keep the
> manufacturer's motion and certified safety controller intact. A vendor adapter
> converts the AMR's pose, battery, scanner and fault data into our validated SI-unit
> contract and converts BIOS velocity or hold output back into the supported controller
> API. The nodes exchange authenticated intent and auction messages directly; the WMS
> only announces jobs. Today we prove this boundary using three independent executable
> nodes in a closed UDP loop, including sensor loss, wrong-key and replay tests. The next
> evidence step is to run that identical gate on three Pis, then replace the digital
> plant with the purchased AMR driver under the original safety controller.
