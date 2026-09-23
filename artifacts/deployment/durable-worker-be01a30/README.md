# Durable-worker candidate: tests passed, release checks pending

Code commit `be01a30399aec332f73f6e776ab086cb791d0e9d`, source fingerprint
`2908d09776e4474ec18b7d126b05cfeb55607eb2c7cc1d789595f94be7927c3a`.

The existing checksum, certificate validation, atomic rename and fsync remain
unchanged. One prestarted worker accepts at most one detached journal snapshot.
During persistence, the controller commands a protective stop, pauses brain
steps and withholds the entire outgoing batch, preserving sender sequence order.
It never replays a saved motion command after acknowledgement: the next decision
uses a new sensor frame. Physical deceleration still follows the simulated brake
model; this is not an assertion of instantaneous physical stopping.

The acknowledgement budget is one second. An error/timeout is sticky and fails
closed; shutdown first commands a stop, then performs a bounded drain. A failed
writer cannot satisfy the live gate. A successful shutdown does not publish old
held intentions. Record/outbox storage is bounded. The real disk duration is
reported in `journal_worker.max_write_ms`; the legacy `journal_flush` control
phase now measures submission, not fsync. The full control-cycle limit is still
20 ms. Separating disk I/O is an architectural change, not deleting its timing.

- All **1,102 Python tests passed in 167.48 host seconds** (`full-suite.xml`).
- Targeted worker/durability/runtime/transport tests passed; Ruff/diff checks passed.
- The separate 20-second local live-process acceptance passed **3/3 tasks**,
  zero contacts, zero reported control deadline misses, sensor-loss stopping at
  **219.3 ms** and recovery, plus wrong-key/replay rejection.
- `local-acceptance.json` is single-host software deployment evidence, not LAN
  proof, Raspberry Pi performance or physical safety certification.

The seven-round Mac/Lenovo package is newly pinned to these bytes. Its results
are pending and must be retained even if it fails. Heavy headless studies must
not run concurrently with live timing. Latest-source registered headless and
two-host gates remain required before main promotion or collaborator publication.
