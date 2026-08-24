# Version 1 edge wire protocol

Every edge node uses compact JSON datagrams over administratively scoped IPv4 multicast.
The transport is advisory and never replaces the local protective-stop layer.

## Envelope and authentication

```json
{
  "v": 1,
  "type": "HB",
  "src": "AMR01",
  "seq": 42,
  "t": 1234.5,
  "sid": "8f4a92b60b453a21",
  "body": {},
  "mac": "64 lowercase hexadecimal characters"
}
```

- `v` is mandatory and unsupported versions are dropped.
- `src` is a bounded identifier; `seq` is a non-negative integer.
- `sid` is a fresh random boot/session identifier. Replay tracking is keyed by
  `(src, sid)`, allowing a legitimate restart to begin a new sequence safely.
- `mac` is HMAC-SHA256 over the canonical JSON envelope without the `mac` field.
- Production nodes reject unsigned, incorrectly signed, oversized, non-object,
  non-finite, malformed and replayed packets without raising into the control loop.
- The receive window accepts modest UDP reordering while rejecting duplicates and old
  sequence numbers.

The deployment PSK must contain at least 16 bytes; the example runbook generates 32
random bytes. A PSK is appropriate for this prototype, not a substitute for per-device
identity, rotation and secure provisioning in a production fleet.

## Time semantics

`t` is useful for single-sender ordering and logs only. Different devices do not share a
monotonic-clock epoch. Any value that another node must compare to its own time is sent
as a bounded relative duration:

- intent entry/exit windows: offsets from transmission;
- auction bid deadline: `ttl`;
- task award lease: `ttl`;
- block/cell claim lease: `ttl`;
- central plan schedule: offsets from transmission.

The receiver converts each duration to its own local deadline on receipt and clamps it
to a policy-specific maximum. Legacy absolute `dl`/`u` fields are accepted only for old
trace compatibility and are converted using the sender's own `t`; new constructors do
not emit them.

## Message types

- `HB`: pose, cell, state, goal, battery and frozen priority summary.
- `IN`: complete near-term route intent plus relative time windows.
- `CL` / `RL`: expiring claim or early release for a cell/block.
- `YD`: observable yield decision.
- `TN` / `BD` / `AW` / `TD`: task announcement, bid, leased award and completion.
- `MB` / `PQ` / `PS`: optional manager beacon, plan request and relative-time response.

Every type has a bounded, type-specific schema before it reaches `AMRBrain`. Identifiers,
lists, numeric ranges, cell coordinates, epochs and datagram size are checked. UDP loss,
duplication and ordering are normal inputs: messages carry complete current state or an
expiry, never a delta that requires every earlier datagram.

## Verification

`tests/test_core.py` covers invalid JSON shapes, invalid bodies, HMAC tampering, missing
authentication, non-finite signed input, replay/reordering and unrelated clock epochs.
`tests/test_edge_runtime.py` starts three real processes and sockets and verifies peer
traffic, authentication, independent clocks, contact-free motion and control deadlines.
