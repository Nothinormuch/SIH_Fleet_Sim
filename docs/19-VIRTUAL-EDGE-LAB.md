# Virtual edge lab

Start the existing dashboard server and open **http://127.0.0.1:8000/edge-lab.html**.
The warehouse dashboard also links to it under **Deployment → Virtual edge lab**.

```bash
source .venv/bin/activate
python backend/server.py
```

## One-minute jury walkthrough

1. Click **Start live demo**. Each board card displays the actual PID of a separate
   `edge_node.py` child process. The map moves from live physics snapshots; it is not
   prerecorded playback. Sensor and actuator frame counters come from the UDP bridge.
2. Point to the authenticated packet trace. A passive subscription observes the
   multicast messages. The WMS announces tasks; it does not select winners.
3. After the 20-second run, show completed tasks, measured contacts and per-process
   p99 computation time. **Download evidence** exports this run, not an old report.
4. Click **Run sensor-loss demo**. AMR01 loses sensor input at 3 seconds for 2 seconds.
   Its controller emits a zero-speed safety stop, then recovers when frames resume.
   The end report shows the measured response and whether the test passed.
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

The three separate test lanes exercise the deployment boundary and make each robot's
motion legible. They are not a congestion or human-avoidance benchmark. Contacts of
absent body types are naturally zero. Use the main warehouse scenarios for those tests.

Live completion counts are observed announcements; the end result uses the node
reports. Packet counts measure what the observer received, not guaranteed delivery
to every peer. Computation timing appears after node shutdown. Stopping early marks
the result cancelled and never yields a passing full-completion verdict.

One run is allowed at a time. Closing/reloading the browser does not stop robot
processes: they finish their bounded 20-second run. **Stop run** requests graceful
cleanup. Stopping the server also cleans up the children. Local controls require
same-origin JSON requests. The lab allocates a separate UDP port group and ephemeral
authentication key for every run, and does not overwrite checked-in acceptance JSON.
