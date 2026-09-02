# 17. THE FIVE-MINUTE PRESENTATION SCRIPT

> The words to say, the moments to hand the judge control, and the one comparison the
> whole pitch hangs on — built so that all 18 requirements and both success criteria are
> either spoken or visibly demonstrated inside five minutes.

**Audience:** whoever is presenting. Read it out loud twice before the slot.
**Reads best after:** [16. Demo Runbook](16-DEMO-RUNBOOK.md), which is the operator's
checklist — *what to click*. This document is *what to say*, and where to stop talking and
let a judge drive.

---

## How this script is built

**One spine: the comparison.** Every beat answers "what does the naive method do, what do
we do, and what is the number?" A judge who remembers nothing else should leave holding one
sentence: *same map, same seed, same safety layer — stop-and-wait finished nothing, we
finished everything, and we did it on the same amount of radio traffic.*

**Interactive by default.** Six points are marked **[HAND OVER]**. At each one you stop
talking and let the judge choose, type, or verify something. A demo the judge participated
in is worth three they watched. If they decline, the fallback line is given — never stall.

**Symbols.** 🎬 stage direction · 🗣 speak · **[HAND OVER]** judge acts · ⏱ running clock ·
`REQ n` the requirement that beat evidences.

**Spoken length: about 740 words.** At a normal 140 wpm that is 5:15 of talking, which is
too much — so roughly a fifth of it is designed to be cut live. Cut markers are `«optional»`.

---

## Before the jury sits down

🎬 Three things, in this order. All are in
[16. Demo Runbook §1](16-DEMO-RUNBOOK.md#1-pre-flight-checklist).

1. Server up, page loaded, `showcase_chokepoint` **played through once** — this warms the
   cold-load path so the live run in Beat 3 returns in seconds, not twenty.
2. **Two browser tabs**, both on the same scenario and seed, one run with `stop_and_wait`
   and one with `BIOS_PIBT.6`. This is the comparison. Have them scrubbed to the same
   timestamp and ready to flip with `Ctrl+Tab`.
3. A terminal open in the repo, large font, ready for `edge_demo.py`.

---

## THE SCRIPT

### Beat 1 · ⏱ 0:00–0:35 · The one-lane bridge
`REQ 1, 2, 11`

🎬 Do not touch the screen yet. Face the judges.

🗣 "Two delivery robots meet in an aisle exactly wide enough for one. Neither can pass.
Neither can see round the other. There is no traffic light."

🗣 "The standard answer is: whoever gets there first goes, everyone else stops and waits.
That is the baseline the problem statement asks us to beat by twenty percent."

**[HAND OVER]** 🗣 "Pick a number between zero and twenty-nine for me — that's our random
seed."

> *If they decline:* "I'll take seed one." Never wait more than two seconds.

🗣 "Same warehouse, same seed, same safety system. The only thing I'm changing is how the
robots talk to each other."

---

### Beat 2 · ⏱ 0:35–1:20 · The comparison, side by side
`REQ 7, 11, 20`

🎬 Flip to the **stop-and-wait** tab. Let it play four or five seconds. The robots reach
the corridor mouth and stop.

🗣 "That's stop-and-wait. Twelve jobs to do. It is going to finish **none** of them —
it's still sitting there when our ten-minute cutoff expires."

🎬 `Ctrl+Tab` to the **BIOS** tab. Same seed. Robots interleave through the corridor.

🗣 "Same map. Same seed. Same braking. All twelve jobs done in three hundred and
eighty-six seconds."

🎬 Now the line that lands. Point at the message-rate readout, or just say it.

🗣 "And here's the part I'd want to hear if I were sitting where you are — **the radio
traffic is the same.** Nine-point-three messages per robot per second for stop-and-wait,
nine-point-nine for ours. We are not winning by talking *more*. We are winning by talking
about the right thing."

«optional» 🗣 "Stop-and-wait spent a hundred and fourteen thousand simulation ticks
waiting. We spent three thousand seven hundred. Thirty times less standing still."

---

### Beat 3 · ⏱ 1:20–2:05 · What they actually say to each other
`REQ 3, 4, 5`

🎬 Point at the faint lines between robots, then the lines fading ahead of each one.

🗣 "Two things on screen that aren't decoration. The lines *between* robots are live peer
links — who can hear whom, right now. The lines *ahead* of each robot are its published
intent: the cells it is about to occupy, broadcast so the others can avoid them."

🗣 "It's the difference between watching the car next to you and that car using its
indicator. Position tells you where something **is**. Intent tells you where it will
**be** — and that's the part you can plan against."

🗣 "Every robot broadcasts its own position, its own intent, and its own battery. Nobody
collects it. There is no central map."

---

### Beat 4 · ⏱ 2:05–2:50 · Six robots, one junction, nobody in charge
`REQ 8, 9, 10, 11`

🎬 `Tab` → **Deployment** → Seed field → type `99` → **Launch** → `Tab` to close.
Let it play at 1×.

🗣 "Seed ninety-nine. Six robots, and every one of their jobs needs the same junction at
the same moment. This is the worst case we could construct."

🎬 Pause on the moment all six show blocked. **[HAND OVER]**

🗣 "Count them for me — how many are moving?"

> *Answer: none. All six blocked, at 0.72 seconds.*

🗣 "All six. Now — who unblocks them?"

🎬 Two-second pause. Let it sit.

🗣 "Nobody. There's no dispatcher in this simulation. The first one frees itself half a
second later, and the whole knot clears in about a hundred seconds. Zero contacts."

🗣 "Two of them politely deferring to each other forever is a real failure mode — we hit
it. Four robots stood still for four hundred seconds, each waiting for a robot that was
waiting for them. Two people in a doorway, both saying *after you*. The fix was that a
robot must compare against what its neighbour **published**, not what it currently thinks
— because otherwise both sides can conclude they lost."

---

### Beat 5 · ⏱ 2:50–3:25 · A blocked aisle, and a job that changes hands
`REQ 12, 13, 14`

🎬 Load `blocked_aisle`, or narrate over the current run if time is short.

🗣 "An aisle gets blocked — a dropped pallet, no radio, no warning. The robot's sensors
see something that isn't moving, it writes that into its own local map with an expiry, and
it re-plans around it. Nobody told it to."

🗣 "And if a robot can't finish at all — flat battery, hardware fault — its claim on the
job expires and the job goes back into the pool. Another robot bids and takes it. Like an
undelivered parcel going back on the round, except no depot decides."

«optional» 🗣 "That's a real scenario in our suite, and there's a test asserting the job
completes after the winner is killed."

---

### Beat 6 · ⏱ 3:25–4:00 · Prove there's no server
`REQ 6, 15`

🎬 Switch to the terminal. This is the strongest thirty-five seconds available — protect it.

🗣 "Everything so far has been one program. Fair challenge: how do you know there isn't a
coordinator hiding in it?"

🎬 Run `python edge_demo.py --robots 3 --duration 8 --port 26123`.

🗣 "Three separate operating-system processes. Three different process IDs. Three
deliberately unsynchronised clocks — ten thousand, twenty thousand, thirty thousand
seconds apart — because nothing in this protocol may assume a shared clock. They find each
other over authenticated UDP multicast and coordinate. No parent process relays anything."

**[HAND OVER]** 🗣 "If you'd like, run `tcpdump` on that port yourself while it's going.
Every packet is one robot to the group. There's no unicast flow to anything, because there
is nothing to flow to. Or pick one of those three PIDs and I'll kill it — the other two
carry on."

> *Fallback if capture needs root:* show the pre-recorded `.pcap`.

🗣 "That's the same agent code as the simulation. It does no input or output of its own —
the network gets handed to it. That's what makes it droppable onto a Pi."

---

### Beat 7 · ⏱ 4:00–4:30 · The dashboard, and battery as a decision
`REQ 16, 17, 18`

🎬 Back to the browser. **[HAND OVER]** 🗣 "Click any robot you like."

🎬 Press `2` for the Fleet sheet.

🗣 "Live position, live battery, current job, who it can hear. But the dashboard is a
**reader** — it can't command anything. Close it and the fleet behaves identically. That
matters, because the problem statement asks for no central server and then asks for a
fleet-wide dashboard, and those pull in opposite directions unless the dashboard is
strictly passive."

🗣 "And battery isn't just a readout. It's a hard gate on bidding — a robot will not bid
for a job it cannot finish and still reach a charger."

---

### Beat 8 · ⏱ 4:30–5:00 · The two numbers, honestly
`REQ 19, 20`

🎬 Press `4` for the Evidence sheet, or just speak it.

🗣 "Two success criteria. Zero inter-robot collisions: **zero contacts of every kind** —
robot to robot, robot to person, robot to racking — across two hundred and sixty-eight
robot-hours."

🗣 "Twenty percent faster than stop-and-wait: we measure **sixty-four, fifty-one and
thirty-three percent** at four, six and eight robots."

🎬 Slow down. This next part is the one that separates you from every other team.

🗣 "Two honest caveats, before you ask. Those percentages are **lower bounds**, not
speedups — the baseline never finished, so we report the worst case the data supports, and
the true numbers are higher. And zero observed collisions is not a proven rate of zero;
it's zero events over a measured exposure, and that bound only comes down with more
running, not with stronger adjectives."

🗣 "Everything we haven't proven is written down in one document, including that this has
never run on physical hardware."

---

## The comparison card

Print this. It is the only thing worth having on paper.

`sih_acceptance_overlap` · 4 robots · seed 1 · 600 s cutoff · `auction` allocation ·
identical safety layer. **The only variable is the coordination policy.**

| | `stop_and_wait` | `BIOS_PIBT.6` |
| --- | ---: | ---: |
| Tasks completed | **0 / 12** | **12 / 12** |
| Makespan | never finished | **386.16 s** |
| Net progress toward goals | 51 cells | **308 cells** |
| Ticks spent standing still | **114,372** | 3,731 |
| Ticks in protective stop | 56,794 | 786 |
| Re-plans | 182 | 42 |
| Closest approach | 0.944 m | 1.026 m |
| **Messages per robot per second** | **9.26** | **9.88** |

The last row is the argument. Near-identical bandwidth, opposite outcomes.

Reproduce either side in about a minute:

```bash
python run.py --scenario sih_acceptance_overlap --policy stop_and_wait \
    --allocation-policy auction --robots 4 --seed 1 --duration 600
python run.py --scenario sih_acceptance_overlap --policy BIOS_PIBT.6 \
    --allocation-policy auction --robots 4 --seed 1 --duration 600
```

---

## The six hand-overs

| ⏱ | Ask | If they decline |
| --- | --- | --- |
| 0:30 | "Pick a seed, nought to twenty-nine." | "I'll take seed one." |
| 2:20 | "How many are moving?" | Answer it yourself: "None. All six." |
| 2:30 | "Who unblocks them?" | Two-second pause, then "Nobody." |
| 3:15 | "Run `tcpdump` on that port yourself." | Show the pre-recorded `.pcap`. |
| 3:20 | "Pick a PID and I'll kill it." | Kill the first one yourself. |
| 4:05 | "Click any robot you like." | Press `C` and take the next one. |

Never let a hand-over cost more than five seconds of silence.

---

## The analogies, and what each one is doing

| Analogy | Explains | Beat |
| --- | --- | --- |
| One-lane bridge | Why a chokepoint is the whole problem | 1 |
| Indicator vs. watching the car | Position sharing vs. **intent** sharing | 3 |
| Two people in a doorway, "after you" | Symmetric deadlock, and why arbitration must use *published* state | 4 |
| Undelivered parcel back on the round | Task re-assignment with no depot | 5 |
| Braking distance, not a fixed bubble | Why the protective field scales with speed «if asked» | — |
| Single-line railway token | Block control: one robot in the corridor at a time «if asked» | — |
| Everyone does the same arithmetic | An auction with no auctioneer «if asked» | — |

The last three are held in reserve for questions — they are the best answers to "how does
that actually work?" and are wasted if spent unprompted.

---

## Requirement coverage

Every requirement is either **shown** on screen or **said** aloud within the five minutes.

| # | Requirement | Beat | How |
| ---: | --- | :---: | --- |
| 1 | ≥3 AMRs | 1, 4 | Four on screen, then six |
| 2 | Dynamic warehouse | 1, 5 | Blocked aisle appears mid-run |
| 3 | Decentralized communication | 3, 6 | Peer links; then three real processes |
| 4 | Position sharing | 3 | Spoken + peer links visible |
| 5 | Intent sharing | 3 | Intent horizons visible — the indicator line |
| 6 | No central server | 6, 7 | Three PIDs, packet capture, dashboard-is-a-reader |
| 7 | Multi-agent path planning | 2 | The interleaving in the corridor |
| 8 | Collision avoidance | 4, 8 | Zero contacts through the knot |
| 9 | Real-time conflict resolution | 4 | Resolved while moving |
| 10 | Deadlock resolution | 4 | All six blocked → self-clearing |
| 11 | Chokepoint handling | 1, 2, 4 | The entire spine |
| 12 | Blocked aisle | 5 | Sensed, written to local map |
| 13 | Re-routing | 5 | Re-plan around the blockage |
| 14 | Task re-assignment | 5 | Claim expires, another robot bids |
| 15 | Edge / local execution | 6 | Three OS processes, no-I/O agent |
| 16 | Fleet dashboard | 7 | On screen throughout |
| 17 | Real-time positions | 7 | Live poses |
| 18 | Battery status | 7 | Inspector + bidding gate |
| 19 | **Zero collisions** | 8 | 0 contacts / 268.54 robot-hours |
| 20 | **≥20% reduction** | 2, 8 | 64% / 51% / 33%, stated as bounds |

---

## Cutting it down

| Slot | Keep | Drop |
| --- | --- | --- |
| **3 min** | Beats 1, 2, 4, 6, 8 | Beats 3, 5, 7 — fold "position and intent" into one sentence in Beat 2 |
| **2 min** | Beats 2, 4, 8 | Everything else; open straight on the two tabs |
| **90 s** | Beat 2 (the flip), then Beat 8 (the numbers) | Say the multi-process demo is available and let them ask |

Below three minutes, **the tab flip and the two numbers are the demo.** Nothing else earns
its seconds.

---

## If you are interrupted

Judges interrupt. Re-entry points, so you never restart:

- **Interrupted in Beats 1–2** → answer, then go straight to Beat 4 (Seed 99). It carries
  reqs 8–11 on its own.
- **Interrupted in Beats 3–5** → answer, then go to Beat 6 (three processes). It is the
  strongest single item.
- **Interrupted in Beats 6–7** → answer, then go straight to Beat 8. Never lose the numbers.
- **Asked something you don't know** → "We haven't measured that. What we have measured
  is…" Full answers for the fifteen most likely questions:
  [16. Demo Runbook §6](16-DEMO-RUNBOOK.md#6-anticipated-judge-questions).

---

## Two speakers

If the slot is shared, split on the natural seam at 2:50 — the change of medium from
browser to terminal.

- **Speaker A (0:00–2:50)** — the problem, the comparison, the protocol, the gridlock.
  Owns the browser. Never touches the terminal.
- **Speaker B (2:50–5:00)** — the multi-process proof, the dashboard, the numbers and the
  caveats. Owns the terminal, takes the questions.

Speaker B's handover line: *"So that's the behaviour. Let me show you it isn't a trick."*

---

## Do not say

Short list, expanded in [16. Demo Runbook §8](16-DEMO-RUNBOOK.md#8-what-not-to-claim).

| Never | Say instead |
| --- | --- |
| "Twenty percent faster." | "At least twenty percent — it's a lower bound; the real figure is higher." |
| "Zero collisions, guaranteed." | "Zero observed contacts across 268 robot-hours; the bound falls with more exposure." |
| "It runs on a Raspberry Pi." | "It's built to, and nothing blocks it — but we have never put it on one." |
| "Peer-to-peer fixes dead zones." | "Not on its own — same radio, same hole. It needs a different link layer." |
| "The dashboard proves decentralization." | "The dashboard shows behaviour. The three processes and the packet capture are the proof." |

The last row matters most: **a judge who catches one overclaim will re-examine everything
else you said.** The caveats in Beat 8 are not a weakness in the pitch — they are the
reason the rest of it is believable.

---

**Siblings:** [README](README.md) · [00. Problem Statement](00-PROBLEM-STATEMENT.md) ·
[01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) ·
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) ·
[15. Limitations](15-LIMITATIONS.md) · [16. Demo Runbook](16-DEMO-RUNBOOK.md)
