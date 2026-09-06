# Four-minute chokepoint timing diagnosis: failed

Code `ac2ee3a2dc17c1e7256e509a3f2592f2ee4eee26`, source fingerprint
`5a0ca5656d1d71a40b880a358458c1c9064d3d12e56595dc5fa976f185c6fa22`.
The exact source remained unchanged. Ten real controllers on the Mac/Lenovo link
completed 10/10 declared tasks with zero measured contacts, but timing failed.
This declared one-round diagnostic does not replace the seven-round campaign.

The new phase evidence isolated a Windows AMR09 journal write at local elapsed
23.580 s: **23.332 ms journal flush within a 23.966 ms full cycle**. This confirms
that synchronous persistence can exceed the 20 ms loop budget, without proving
that all earlier Windows overruns had the same cause. No other Windows node
overran its full cycle in this run; their maximum journal times were 11.779 to
15.747 ms. Durable writes were not disabled.

All five Mac controllers recorded a full-cycle overrun near their respective
136.8-second local elapsed times, with maxima 49.139–75.221 ms. Their clocks have
independent start offsets, so this suggests a shared host disturbance rather
than providing a precisely synchronized trace. Two peer-receive phases reached
67.173 and 72.024 ms although each node's maximum measured thread CPU per tick
was below 5 ms. AMR04 later had one late wake. The referee had one timing miss
(25.393 ms maximum lag). One unexpected stale motion frame was rejected; none
was accepted. The native macOS scheduling overrides applied and were released,
but they did **not** eliminate timing failures. No causal performance benefit
is established for the hint.

After the run, read-only host checks showed roughly 9.6–9.7 GB swap usage on a
16 GiB Mac; a later memory-pressure query reported 39 percent system-wide free.
Those are post-run observations, not proof that paging caused a particular
overrun. The operator was asked to save and close unrelated applications before
further timing measurements, leaving Codex, the 3D dashboard and PowerShell open.
That change in background load must be disclosed when interpreting later runs.

Next repair: isolate durable I/O in a bounded worker, command a protective stop
and withhold publication until acknowledgement, retain fsync and explicit failure
handling, and measure the separate persistence time. Keep the 20 ms control gate.
The Mac pause remains a separate issue until retested. No release or push follows
from this failed diagnostic; all raw reports remain unmodified.
