# Collaboration integration — September 23, 2026

Personal main e5bedb4 was already an ancestor of collaboration main bbe5987.
The intervening commits removed Edge Lab and deployment tooling while adding
manual task counts, improved shadows/environment lighting and Dokploy deployment.
Consequently a plain Git merge would not restore the missing files.

This integration restores removed tracked personal files, including the lab UI,
backend endpoints, deployment/LAN tools, acceptance harnesses, tests, configuration
examples and historical evidence. Runtime command deadlines, bounded UDP receive
handling and asynchronous durable saving are restored with the lab. Shared
dashboard files retain the collaboration changes. Private keys and transient
multi-host telemetry remain ignored.

Collaboration's policy defaults are retained: BIOS7 for Chokepoint, BIOS6 elsewhere,
Auction V2 allocation. The local Edge Lab defaults to BIOS6 with BIOS7 selectable.
Earlier documents and evidence describe their original sources and configurations;
they are historical and do not establish performance of this integrated commit.
In particular the BIOS7 seed-2017 failure and three failed strict LAN timing rounds
remain disclosed. Restoring this software is not a new hardware qualification.

## Running

Start `python backend/server.py 8002` from the repository with its virtual
environment active. Open `http://127.0.0.1:8002/` and select **Virtual edge lab**,
or open `http://127.0.0.1:8002/edge-lab.html` directly. The LAN viewer at
`edge-lab.html?source=multihost` requires a separately started multi-host session.
Remote Docker dashboard visitors cannot launch local lab processes through the
loopback-only control API. Run the lab locally for the interactive demonstration.

## Integration verification

A local HTTP smoke run fetched the dashboard, Edge Lab page, scenario metadata
and multi-host status, then launched the lab through its API. Three independent
controllers completed 3/3 jobs with zero observed contacts and returned success.
The same check confirmed the manual task-count metadata and Chokepoint/Grand
Challenge defaults. The five live-view JavaScript tests passed. Docker itself was
unavailable on this host, so no container-build claim is made; its recipe now
includes the restored lab's required edge_node.py child-process entrypoint.
The final Python suite passed 1,104 tests in 164.88 seconds. Targeted lint,
JavaScript syntax and Git whitespace checks also passed.
