# Virtual edge lab

Start the existing dashboard server and open **http://127.0.0.1:8000/edge-lab.html**.
The warehouse dashboard also links to it under **Deployment → Virtual edge lab**.
The lab reuses the dashboard's 3D renderer and warehouse/robot assets, driven by live
snapshots from the UDP sensor/actuator loop. **3D warehouse** is the default;
**2D map** is available as a diagnostic view and automatic WebGL fallback.

```bash
source .venv/bin/activate
python backend/server.py
```

## One-minute jury walkthrough

1. Click **Start live demo**. Each board card displays the actual PID of a separate
   `edge_node.py` child process. The map moves from live physics snapshots; it is not
   prerecorded playback. Sensor and actuator frame counters come from the UDP bridge.
   Drag to orbit and scroll to zoom. Choose **Top down** for clear aisle visibility,
   or select a robot and choose **Follow robot**. Clicking a 3D robot or its controller
   heading selects the same robot in both panels. **Expand view** enlarges the floor.
2. Point to the authenticated packet trace. A passive subscription observes the
   multicast messages. The WMS announces tasks; it does not select winners.
3. After the run, show completed tasks, measured contacts and per-process
   p99 computation time. **Download evidence** exports this run, not an old report.
4. Click **Run sensor-loss demo**. AMR01 loses sensor input at 3 seconds for 2 seconds.
   Its controller emits a zero-speed safety stop, then recovers when frames resume.
   The end report shows the measured response and whether the test passed.
   The stopped robot's warning halo and wheels follow the same live data. Task and
   cargo fields come from optional read-only telemetry emitted by its edge process;
   the renderer does not infer pickup from proximity to a box.
   **Actual speed** comes from the physics model; the command line is the controller's
   requested velocity. A zero-speed command initiates braking within the configured
   acceleration envelope. The reported sensor-loss response measures time to the stop
   command, not the time until the robot body has completely stopped.
5. Alternatively, click a controller's **Disconnect sensor · 2s** button during a
   normal run. This suppresses actual sensor datagrams. Manual fault requests are
   included in the evidence download; precise stop-response timing is reported only
   for the scheduled sensor-loss experiment.

## What the display proves

The virtual board represents where an onboard computer would sit. BIOS processes,
UDP transport, authentication, sensor timeout and command watchdog logic execute on
the current host. Robot bodies, sensors, the warehouse, and battery draw are simulated.
There is no Raspberry Pi CPU, operating-system or GPIO emulation in this screen.
Physical Raspberry Pi performance requires running on that hardware.

Select 3–10 AMRs and a seed before starting. The **Interfaces** profile retains the
short isolated-lane demonstration. Additional profiles exercise opposing routes,
a one-cell articulation doorway, a dropped pallet, an actual controller process stop,
and three humans crossing with seeded speed/pause/direction changes. Humans do not
broadcast intentions, but retain local collision avoidance; this is not a test of
non-cooperative people deliberately entering an unavoidable braking envelope.

Profiles have fixed maximum windows of 20–240 seconds. The run may finish after all
completion announcements and required events have occurred, with a one-second settling
interval; final node reports still determine PASS. Evidence records actual simulation
time separately from the maximum window. Timeouts and cancelled runs do not pass.

The failure profile stops AMR01's actual process at two seconds. Its chassis remains
in the physics model; the adapter watchdog commands a stop and surviving peers must
complete its active task. This is a controlled process-stop test, not power-loss or
disk-corruption certification. Blocked-aisle events appear only when their physical
footprint is unoccupied. Network partitions/restarts are explicitly unsupported by
this socket runner; no real RF impairment is claimed.

Live completion counts are observed announcements; the end result uses the node
reports. Packet counts measure what the observer received, not guaranteed delivery
to every peer. Computation timing appears after node shutdown. The gate now also checks
the full sensor-read/brain/command-write/journal cycle and scheduler wakeups, not only
the brain calculation. Phase maxima and thread CPU time support diagnosing OS scheduling
versus algorithm work. UI metadata is refreshed at 10 Hz; actuator commands remain 50 Hz.
Late schedule slots are recorded and skipped instead of bursting stale catch-up ticks.
Stopping early marks
the result cancelled and never yields a passing full-completion verdict.

3D movement is interpolated only between received positions, never extrapolated beyond
the latest snapshot. Safety-stop positions take precedence. A `run_id` resets the
scene between runs, while page reloads reattach to the existing processes. Completed
task IDs drive the delivered cargo markers. The existing dashboard playback is unchanged.

For developers, the edge node's `--visual-telemetry` flag adds task state, cargo and a
bounded eight-cell route to actuator packets. It defaults off outside the live lab.
The original `v`, `omega`, `safety_stop` and timestamp command contract is preserved.
Run `node --test tests/test_live_twin.mjs` for the live-frame adapter/interpolation
tests, and `python -m pytest -q` for Python regressions.

## Integration verification (2026-09-06)

The 3D sensor-loss browser run completed 3/3 tasks, with zero contacts, zero measured
control-loop overruns, and a 203.9 ms response to the stop command. Camera switching,
selection, 2D fallback, cargo completion and evidence download were exercised.
Manual sensor interruption also verified braking to rest, stationary rendered wheels
and recovery. One manual run finished 3/3 tasks with zero contacts but recorded four
20 ms computation-budget overruns (maximum 38.73 ms across the nodes), so its result
correctly failed the timing gate. The UI exposes maximum loop time and the overrun
count alongside p99. A desktop OS with simultaneous graphics work is not a hard
real-time platform; no timing gate has been relaxed for presentation.

One run is allowed at a time. Closing/reloading the browser does not stop robot
processes: they finish their bounded run. **Stop run** requests graceful
cleanup. Stopping the server also cleans up the children. Local controls require
same-origin JSON requests. The lab allocates a separate UDP port group and ephemeral
authentication key for every run, and does not overwrite checked-in acceptance JSON.

## Broader acceptance commands

```bash
python edge_stress_acceptance.py --robots 3,6,10 --seeds 3 --jobs 2
python edge_stress_acceptance.py --mode live --robots 3,10 --seeds 1 --jobs 1 --output artifacts/deployment/edge-stress-live.json
```

The first command uses deterministic fixed-step simulation, the second actual UDP
processes with visual telemetry. Run live timing tests separately from batch jobs.
For actual graphics-load evidence, start profiles in the browser and download their
results. Source SHA-256 hashes accompany batch reports, including failures. The older
90-run headline SIH benchmark remains a separately scoped experiment.
