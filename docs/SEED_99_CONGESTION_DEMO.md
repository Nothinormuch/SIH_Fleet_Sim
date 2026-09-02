# Seed 99: six-AMR launch-gridlock demonstration

## What it proves

Seed 99 is a fixed, jury-facing congestion workload. Six AMRs start on distinct,
collision-free cells packed around one rack junction. Each announced task has its pickup
under one chassis and a drop on the opposite side of the cluster. The decentralized
auction therefore awards local work without a WMS-selected winner; after the awards, all
six agents request occupied cells and the BIOS traffic layer must establish an order,
yield, and drain the launch gridlock.

There is no timed release, teleported robot, forced auction winner, or front-end motion
script. The dashboard replays the ordinary `AMRBrain`, network, world, and 50 Hz safety
loop. Seed 99 changes only the pinned map starts and task catalog used by this explicit
demo.

The dashboard measures an AMR as blocked when its recorded traffic state is `blocked` or
`retreat`, or when it names a non-empty wait-for owner. `First release` is the first 10 Hz
telemetry frame after the observed 6/6 standstill in which fewer than six agents remain
blocked. It does not claim that every later route interaction has ended.

## Checked result

Configuration: six AMRs, `BIOS_PIBT.6`, `auction_bundle`, seed 99, 180 s maximum window.

- full opening gridlock: 6/6 AMRs at 0.72 s
- first release: 1.22 s, or 0.50 s after the measured standstill
- task completion: 6/6 at 106.22 s
- robot-robot, robot-human, and robot-rack contacts: 0 / 0 / 0
- closest observed robot separation: 1.188 m
- BIOS traffic yield decisions: 228
- identical basic stop-and-wait result: 0/6 at the 180 s cutoff
- identical competition stop-and-wait result: 0/6 at the 180 s cutoff

These are deterministic simulation observations for this fixed workload, not physical
safety certification and not a universal performance claim.

The result's `deadlocks_detected` counter remains zero by design. That counter records the
later stale wait-for-cycle breaker. In this workload, BIOS priority arbitration and cell
gates release the six-way standstill before it ages into that fallback. For the jury, say
"BIOS prevented the opening gridlock from becoming a persistent deadlock," not "the
deadlock detector fired once."

## Run it

Dashboard:

1. Start `python backend/server.py` inside the virtual environment.
2. Open `http://127.0.0.1:8000/`.
3. Select BIOS 6 and Auction V2 (`auction_bundle`).
4. Enter `99` in the Seed field. The UI pins six AMRs and a 180 s window.
5. Launch, pause near 0.72 s to show all six wait-for states, then play to completion.

Headless BIOS 6 evidence:

```bash
python -m src.main --scenario seed_99_congestion --policy BIOS_PIBT.6 \
  --allocation-policy auction_bundle --robots 6 --seed 99 --duration 180
```

Identical stop-and-wait comparison:

```bash
python -m src.main --scenario seed_99_congestion --policy stop_and_wait \
  --allocation-policy auction_bundle --robots 6 --seed 99 --duration 180
```

Regression gate:

```bash
python -m pytest -q tests/test_seed_99.py
```
