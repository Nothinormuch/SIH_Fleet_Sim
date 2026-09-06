# Preserved observer snapshot for the historical three-controller LAN run

`observer-final.json` is a byte-identical preservation of the terminal-owned
`artifacts/deployment/multihost-live.json` snapshot before a later session can
replace that transient file. Its SHA-256 is
`ca325d2f7da8f9f3ebee937aeb7f1eea2547dccbe2ba66a0a950337e06ded8e1`.
The snapshot and referee report identify the same session:
`73effbfccc007ec54f984f1f9be1a5e3`.

At simulated world time 24.92 seconds, the bounded packet trace contains 24
authenticated observer events from 23.82 through 24.92 seconds. These include
Mac controllers AMR01/AMR02 and Windows controller AMR03, after workload start.
This supports cross-host packet emission observed during the declared window.
It is not a timestamped record of receipt at every individual robot.

The controller lifetime peer-receipt gate includes readiness. Keep that scope
distinct from the observer trace and from the task-completion events. This is
historical source-pinned software-in-the-loop evidence, not a new BIOS 7 release
campaign or physical Raspberry Pi measurement. No session keys are included.
