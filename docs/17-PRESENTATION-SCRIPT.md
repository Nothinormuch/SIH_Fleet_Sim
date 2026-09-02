# 17. THE FIVE-MINUTE PRESENTATION SCRIPT

> The words to say, the moments to stop talking and hand a judge the keyboard, and the one
> comparison the whole pitch hangs on — built so that all 18 requirements and both success
> criteria are either spoken aloud or visibly demonstrated inside five minutes.

**Audience:** whoever is presenting. Read it out loud twice before the slot. Out loud —
reading it silently will not tell you where you run out of breath.
**Reads best after:** [16. Demo Runbook](16-DEMO-RUNBOOK.md), which is the operator's
checklist: *what to click*. This document is *what to say*, and — just as important — where
to stop saying anything and let someone else drive.

---

## The story you are actually telling

Before the beats, the shape. Five minutes is not enough time to explain a system this size,
and trying is the most common way teams lose the room. So do not explain the system. Tell
one story with a beginning, a turn and a number:

> Two robots meet in an aisle built for one. The obvious fix — everybody stops and waits —
> is so bad it never finishes the work at all. We made them talk to each other instead.
> Here is exactly how much better that is, and here is precisely what we have not proven.

Everything in this script serves that. The architecture, the thirteen policies, the
neuroevolution, the space-time planner — all of it is *supporting evidence for one claim*,
and none of it belongs in the five minutes unless a judge asks. When you feel the urge to
add a detail, ask whether it makes that story more believable. If it doesn't, it is
competing with it.

The other thing worth internalising: **you are not trying to sound impressive. You are
trying to be believed.** Those pull in opposite directions more often than people expect. A
judge who catches you overstating one thing will silently re-audit everything else you said,
and you will never know it happened. That is why the honest caveats in Beat 8 are not a
weakness in the pitch — they are the reason the rest of it lands.

---

## How to rehearse this

Three passes, and they are different jobs. Do not skip to the third.

1. **Read it aloud, slowly, with the screen off.** You are learning the words and finding
   the sentences that fight your mouth. Rewrite those — this script is a draft of *your*
   voice, not a fixed text. If a line feels like someone else wrote it, it will sound like
   it too.
2. **Read it with the demo running, no clock.** You are learning what appears when, so you
   can look at the judges instead of the screen. The single biggest upgrade available to
   most presenters is knowing the demo well enough to face away from it.
3. **Full dress, with a timer, twice.** Only now do you care about the clock. Expect the
   first timed run to overshoot by ninety seconds. That is normal and it is what the
   `«optional»` markers are for.

If you have time for a fourth pass, do it with someone who interrupts you rudely. Being
interrupted is the likeliest thing that will happen and the least likely thing anyone
practises.

---

## Before the jury sits down

🎬 Three things, in this order. All are in
[16. Demo Runbook §1](16-DEMO-RUNBOOK.md#1-pre-flight-checklist).

1. **Server up, page loaded, `showcase_chokepoint` played through once.** This warms the
   cold-load path, so the live run in Beat 4 comes back in seconds instead of twenty. Twenty
   seconds of loading screen in a five-minute slot is seven percent of your time and all of
   your momentum.
2. **Two browser tabs**, same scenario, same seed, one run with `stop_and_wait` and one with
   `BIOS_PIBT.6`. Scrub both to the same timestamp. This is the comparison and it is the
   heart of the pitch — set it up before anyone is watching, so that flipping between them
   costs you one keystroke and no explanation.
3. **A terminal in the repo, font size cranked up**, ready for `edge_demo.py`. Judges at the
   back of a room cannot read a 12pt terminal, and asking them to squint is worse than not
   showing them at all.

Then close everything else. Notifications, chat, the other seventeen tabs. Not for
neatness — because a message popping up mid-demo is the kind of thing that costs you ten
seconds of composure you cannot spare.

---

## THE SCRIPT

**Symbols.** 🎬 stage direction · 🗣 speak · 🔁 alternative if the room is cold ·
✋ what to do with your body · ⛔ what not to over-explain · **[HAND OVER]** judge acts ·
⏱ running clock · `REQ n` requirement evidenced.

**Spoken length: about 1,050 words.** At a conversational 140 words per minute that is
around 7:30 of talking, which is deliberately too much for a five-minute slot. Roughly a
third is marked `«optional»` and is meant to be cut live, based on the room. Having more
than you need and choosing what to drop is a far better position than running dry at 3:40
and filling with waffle.

---

### Beat 1 · ⏱ 0:00–0:40 · The one-lane bridge
`REQ 1, 2, 11`

🎬 Do not touch the screen. Do not introduce yourself or the team — the slide behind you
already did. Face the judges and start with the problem.

✋ Hands still, or one hand loosely holding the other. The temptation is to gesture at the
screen you haven't shown yet.

🗣 "Two delivery robots meet in a warehouse aisle that's exactly wide enough for one.
Neither can pass. Neither can see round the other. There's no traffic light and there's
nobody watching."

🗣 "The standard answer is the polite one — whoever got there first goes, everybody else
stops and waits. It's simple, it's safe, and it's the baseline this problem statement asks
us to beat by twenty percent."

«optional» 🗣 "It's also what a lot of real warehouses genuinely do, which is why it's a
fair thing to be measured against."

**[HAND OVER]** 🗣 "Before I show you anything — pick me a number between nought and
twenty-nine. That's going to be our random seed."

> 🔁 *If nobody answers within two seconds:* "I'll take seed one, then." Say it warmly and
> move on immediately. A judge who doesn't want to play is not a judge who is unimpressed;
> some rooms are just quiet at the start. Never wait for an answer twice.

🗣 "Same warehouse. Same seed. Same braking, same sensors, same safety system. The only
thing I'm going to change is how the robots talk to each other."

⛔ Do not explain what a seed is. Anyone who needs that explained is not going to follow the
next four minutes anyway, and anyone who doesn't will feel talked down to.

---

### Beat 2 · ⏱ 0:40–1:30 · The comparison, side by side
`REQ 7, 11, 20`

🎬 Flip to the **stop-and-wait** tab. Let it run four or five seconds — long enough that the
robots reach the corridor mouth and visibly stop. Resist narrating over the first three
seconds; let them see it.

🗣 "That's stop-and-wait. Twelve jobs on the board. It is going to finish **none** of
them — it's still sitting exactly like that when our ten-minute cutoff runs out."

🎬 `Ctrl+Tab`. Same seed, same map. The robots interleave through the corridor.

✋ Step back half a pace here. Physically getting out of the way of the screen tells the
room to look at it.

🗣 "Same warehouse. Same seed. All twelve jobs done, in three hundred and eighty-six
seconds."

🎬 Now the line the whole pitch turns on. Slow down for it.

🗣 "And here's the part I'd be suspicious about, if I were sitting where you are. You'd
assume we won by flooding the network — that we're just talking more. **We're not.**
Stop-and-wait used nine-point-three messages per robot per second. We used
nine-point-nine."

🗣 "Same bandwidth. Opposite outcome. We're not talking more — we're talking about the
right thing."

«optional» 🗣 "If you want one number that captures it: stop-and-wait spent a hundred and
fourteen thousand simulation ticks standing still. We spent three and a half thousand.
Thirty times less waiting, on the same radio budget."

⛔ Do not get drawn into *which* policy this is, or the fact that there are thirteen of
them, unless asked. It is genuinely interesting and it is a five-minute detour.

---

### Beat 3 · ⏱ 1:30–2:15 · What they actually say to each other
`REQ 3, 4, 5`

🎬 Point at the faint lines *between* robots. Then at the lines fading *ahead* of each one.
Two distinct gestures — they are two different things and the room will conflate them if
you wave once.

🗣 "There are two things on this screen that aren't decoration. The lines *between* the
robots are live peer links — that's who can physically hear whom, right now, this second.
The lines *ahead* of each robot are its published intent: the cells it's about to move
through, broadcast so everyone else can plan around them."

🎬 Pause. This next line is the one people remember afterwards.

🗣 "It's the difference between watching the car next to you and that car using its
indicator. Position tells you where something **is**. Intent tells you where it's going to
**be** — and that's the only part you can actually plan against."

🗣 "Every robot broadcasts its own position, its own intent, its own battery. Nobody
collects it. There's no shared map anywhere in this system."

«optional» 🗣 "And that intent expires. If a robot goes quiet, its claim on those cells
ages out on its own — nobody has to notice it's gone and clean up after it."

---

### Beat 4 · ⏱ 2:15–3:05 · Six robots, one junction, nobody in charge
`REQ 8, 9, 10, 11`

🎬 `Tab` → **Deployment** → Seed field → `99` → **Launch** → `Tab` to close. Practise this
until it is muscle memory; it is the most persuasive forty seconds in the demo and the worst
possible place to fumble a keystroke.

🗣 "This is seed ninety-nine. Six robots, and we've arranged it so every single one of their
jobs needs the same junction at the same moment. It's the nastiest case we could construct
on purpose."

🎬 Let it play at 1×. Pause on the moment all six show blocked.

**[HAND OVER]** 🗣 "Count them for me — how many are moving?"

> *The answer is none. All six are blocked, at 0.72 seconds in.*
> 🔁 *If the room is silent:* answer it yourself — "None of them. All six."

🗣 "All six. Now — who unblocks them?"

🎬 **Stop talking.** Two full seconds. This silence is doing real work; do not rescue it.

🗣 "Nobody. There's no dispatcher in this simulation to unblock anything. The first one
frees itself about half a second later, and the whole knot clears in a bit over a hundred
seconds. Zero contacts, the entire time."

«optional, but the best story you have» 🗣 "Getting there was harder than it looks. We had a
version where two robots would defer to each other forever — four of them stood perfectly
still for four hundred seconds, each one politely waiting for a robot that was waiting for
it. Two people in a doorway, both saying *after you*, indefinitely. Nothing crashed, nothing
errored, every robot was individually behaving correctly. The fix was that a robot has to
compare against what its neighbour actually **published**, not against what it currently
believes — because otherwise both sides can look at slightly different information and both
conclude they lost."

⛔ Do not call it a deadlock detector. We have one; it is not what solved this, and claiming
it is invites a question you will answer badly. See
[16 §8](16-DEMO-RUNBOOK.md#8-what-not-to-claim).

---

### Beat 5 · ⏱ 3:05–3:35 · A blocked aisle, and a job that changes hands
`REQ 12, 13, 14`

🎬 Load `blocked_aisle`, or simply narrate over the current run if you're behind the clock.
This beat survives being told rather than shown.

🗣 "An aisle gets blocked. A dropped pallet — something with no radio that never announces
itself. The robot's own sensors see something that isn't moving, it writes that into its own
local map with an expiry on it, and it re-plans around it. Nobody told it to, and nobody
else has to agree."

🗣 "And if a robot genuinely can't finish — flat battery, hardware fault, gone — its claim
on that job simply expires, and the job goes back in the pool for somebody else to bid on.
Like an undelivered parcel going back on the round. Except there's no depot deciding."

«optional» 🗣 "That's a real scenario in our suite, and there's a test that kills the robot
holding the job and asserts the work still completes."

---

### Beat 6 · ⏱ 3:35–4:15 · Prove there's no server
`REQ 6, 15`

🎬 Switch to the terminal. This is the strongest forty seconds you have. Protect the time
for it — if you are running late, cut Beat 5 entirely rather than trimming this.

🗣 "Now, everything I've shown you so far has been one program on one laptop. So here's the
fair challenge, and it's the one I'd ask: how do you know there isn't a coordinator hiding
in the middle of it?"

🎬 Run `python edge_demo.py --robots 3 --duration 8 --port 26123`.

🗣 "Three separate operating-system processes. Three different process IDs — you can see
them there. Three deliberately unsynchronised clocks, ten thousand seconds apart from each
other, because nothing in this protocol is allowed to assume a shared clock. They find each
other over authenticated multicast and they coordinate. No parent process is relaying
anything between them."

**[HAND OVER]** 🗣 "If you'd like to check that rather than take my word for it — run
`tcpdump` on that port yourself while it's going. Every packet is one robot to the group.
There's no unicast traffic to anything, because there's nothing for it to go to. Or pick one
of those three process IDs and I'll kill it in front of you. The other two carry on."

> 🔁 *If packet capture needs root and the laptop is locked down:* "I've got a capture from
> earlier I can show you instead." Have the `.pcap` ready. Do not spend stage time fighting
> `sudo`.

🗣 "And that's the same agent code as the simulation — not a port of it, the same code. It
does no networking of its own; the transport gets handed to it. That's the whole reason it
can drop onto a Raspberry Pi unchanged."

---

### Beat 7 · ⏱ 4:15–4:40 · The dashboard, and battery as a decision
`REQ 16, 17, 18`

🎬 Back to the browser. **[HAND OVER]** 🗣 "Click any robot you like."

🎬 Press `2` for the Fleet sheet.

🗣 "Live position, live battery, what it's carrying, who it can currently hear. But this
dashboard is a **reader** — it cannot command anything. Close it and the fleet behaves
exactly the same."

🗣 "That matters more than it sounds. The problem statement asks for no central server, and
then asks for a dashboard showing the whole fleet — and those two things pull against each
other, unless the dashboard is strictly passive. Ours is. That was a deliberate constraint,
not a convenience."

🗣 "And battery isn't just a readout on a screen. It's a hard gate on bidding. A robot will
not bid for a job it can't finish and still reach a charger afterwards."

---

### Beat 8 · ⏱ 4:40–5:00 · The two numbers, honestly
`REQ 19, 20`

🎬 Press `4` for the Evidence sheet, or just say it. Face the judges, not the screen. This
is a spoken close.

✋ Stop moving. Whatever you were doing with your hands, stop.

🗣 "Two success criteria. Zero inter-robot collisions: we have **zero contacts of every
kind** — robot to robot, robot to person, robot to racking — across two hundred and
sixty-eight robot-hours."

🗣 "And twenty percent faster than stop-and-wait. We measure **sixty-four percent, fifty-one
percent and thirty-three percent** at four, six and eight robots."

🎬 Slow right down. This last part is what separates you from every other team in the room.

🗣 "Two honest caveats, before you ask me for them. Those percentages are **lower bounds**,
not speedups — the baseline never finished at all, so we report the worst case our data can
support. The real numbers are better than that, and we'd rather quote the floor. And zero
observed collisions is not a proven rate of zero. It's zero events across a measured
exposure, and that bound only comes down by running more, not by using stronger adjectives."

🗣 "Everything we haven't proven is written down in one document, including the fact that
none of this has ever run on physical hardware."

🎬 Stop. Do not add a summary. The caveat *is* the close, and it is stronger than any
summary you could put after it.

---

## The comparison card

Print this one. It is the only thing worth having on paper in your hand.

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

The last row is the argument. Near-identical bandwidth, opposite outcomes — and it kills the
sharpest objection available to a judge before they can raise it.

Reproduce either side in about a minute:

```bash
python run.py --scenario sih_acceptance_overlap --policy stop_and_wait \
    --allocation-policy auction --robots 4 --seed 1 --duration 600
python run.py --scenario sih_acceptance_overlap --policy BIOS_PIBT.6 \
    --allocation-policy auction --robots 4 --seed 1 --duration 600
```

---

## The six hand-overs

A demo a judge participated in is worth three they watched. But a hand-over that stalls is
worse than none at all, so every one has an exit.

| ⏱ | Ask | If they decline |
| --- | --- | --- |
| 0:30 | "Pick a seed, nought to twenty-nine." | "I'll take seed one, then." |
| 2:40 | "How many are moving?" | Answer it: "None. All six." |
| 2:50 | "Who unblocks them?" | Two-second pause, then "Nobody." |
| 4:00 | "Run `tcpdump` on that port yourself." | Show the pre-recorded `.pcap`. |
| 4:05 | "Pick a PID and I'll kill it." | Kill the first one yourself. |
| 4:20 | "Click any robot you like." | Press `C` and take the next one. |

**Five seconds is the limit.** Past that, answer your own question cheerfully and keep
moving. Silence you have chosen reads as confidence; silence that happened to you does not.

---

## The analogies, and what each is doing

| Analogy | Explains | Beat |
| --- | --- | --- |
| One-lane bridge | Why a chokepoint is the entire problem | 1 |
| Indicator vs. watching the car | Position sharing vs. **intent** sharing | 3 |
| Two people in a doorway, "after you" | Symmetric deadlock, and why arbitration must use *published* state | 4 |
| Undelivered parcel back on the round | Task re-assignment with no depot | 5 |
| Braking distance, not a fixed bubble | Why the protective field scales with speed | reserve |
| Single-line railway token | Block control — one robot in the corridor at a time | reserve |
| Everyone does the same arithmetic | An auction with no auctioneer | reserve |

The three marked **reserve** are held back deliberately. They are the best answers to "but
how does that actually *work*?" and they are wasted if you spend them unprompted. The
railway token in particular is worth saving: single-track railways solved exactly this
problem in the nineteenth century with a physical brass token that only one driver could
hold, and an engineer in the room will enjoy that far more than a description of a mutex.

---

## Requirement coverage

Every requirement is either **shown** on screen or **said** aloud inside the five minutes.

| # | Requirement | Beat | How |
| ---: | --- | :---: | --- |
| 1 | ≥3 AMRs | 1, 4 | Four on screen, then six |
| 2 | Dynamic warehouse | 1, 5 | Blocked aisle appears mid-run |
| 3 | Decentralized communication | 3, 6 | Peer links; then three real processes |
| 4 | Position sharing | 3 | Spoken, peer links visible |
| 5 | Intent sharing | 3 | Intent horizons — the indicator line |
| 6 | No central server | 6, 7 | Three PIDs, packet capture, passive dashboard |
| 7 | Multi-agent path planning | 2 | The interleaving through the corridor |
| 8 | Collision avoidance | 4, 8 | Zero contacts through the gridlock |
| 9 | Real-time conflict resolution | 4 | Resolved while moving |
| 10 | Deadlock resolution | 4 | All six blocked, self-clearing |
| 11 | Chokepoint handling | 1, 2, 4 | The spine of the whole pitch |
| 12 | Blocked aisle | 5 | Sensed, written to the local map |
| 13 | Re-routing | 5 | Re-plan around the blockage |
| 14 | Task re-assignment | 5 | Claim expires, another robot bids |
| 15 | Edge / local execution | 6 | Three OS processes, no-I/O agent |
| 16 | Fleet dashboard | 7 | On screen throughout |
| 17 | Real-time positions | 7 | Live poses |
| 18 | Battery status | 7 | Inspector, and the bidding gate |
| 19 | **Zero collisions** | 8 | 0 contacts / 268.54 robot-hours |
| 20 | **≥20% reduction** | 2, 8 | 64% / 51% / 33%, spoken as bounds |

---

## Cutting it down

Slots get shortened. Decide what goes *before* you're standing there.

| Slot | Keep | Drop |
| --- | --- | --- |
| **3 min** | Beats 1, 2, 4, 6, 8 | Beats 3, 5, 7 — fold position-and-intent into one sentence in Beat 2 |
| **2 min** | Beats 2, 4, 8 | Everything else. Open straight on the two tabs, no preamble |
| **90 s** | Beat 2 (the flip), then Beat 8 (the numbers) | Everything. Mention the multi-process demo exists and let them ask |

Below three minutes, **the tab flip and the two numbers are the demo.** Nothing else earns
its seconds, and trying to squeeze in one more thing is how you end up rushing the close —
which is the only part they'll definitely remember.

---

## If you're interrupted

Judges interrupt, and it's usually a good sign — it means they're engaged. Answer properly,
then re-enter here. Never restart from the top.

- **Interrupted in Beats 1–2** → answer, then go straight to Beat 4, seed 99. It carries
  requirements 8 through 11 on its own.
- **Interrupted in Beats 3–5** → answer, then jump to Beat 6, the three processes. It's the
  single strongest item you have.
- **Interrupted in Beats 6–7** → answer, then go straight to Beat 8. Whatever else happens,
  do not lose the numbers.
- **Asked something you don't know** → *"We haven't measured that. What we have measured
  is…"* and pivot to the nearest thing you did measure. This is a genuinely strong answer,
  not a fallback. Full answers to the fifteen most likely questions are in
  [16. Demo Runbook §6](16-DEMO-RUNBOOK.md#6-anticipated-judge-questions).

---

## If something breaks

It might. Recovery commands are in
[16. Demo Runbook §7](16-DEMO-RUNBOOK.md#7-failure-recovery-on-stage). The human half:

**Say what happened, once, without apologising twice.** "That's the server gone — one
second." Then fix it. A calm ten-second recovery costs you nothing; a flustered apology
that runs for thirty seconds costs you the room's confidence in everything that follows.

**You have a fallback that needs no software at all**: the comparison card in your hand. If
the laptop dies completely, you can deliver Beats 1, 2 and 8 off that piece of paper and
still make the whole argument. Know that, because knowing it is what stops you panicking.

---

## Two speakers

If the slot is shared, split at 3:35 — the change of medium from browser to terminal is a
natural seam, and it gives the audience a reason for the handover beyond "it's my turn now."

- **Speaker A (0:00–3:35)** — the problem, the comparison, the protocol, the gridlock. Owns
  the browser and never touches the terminal.
- **Speaker B (3:35–5:00)** — the multi-process proof, the dashboard, the numbers and the
  caveats. Owns the terminal and takes the questions.

Speaker B's handover line: *"So that's the behaviour. Let me show you it isn't a trick."*

Whoever takes Beat 8 must be the person most comfortable saying "we haven't proven that",
because that beat lives or dies on sounding unbothered rather than defensive.

---

## Do not say

The short version. Expanded, with the reasoning, in
[16. Demo Runbook §8](16-DEMO-RUNBOOK.md#8-what-not-to-claim).

| Never | Say instead |
| --- | --- |
| "Twenty percent faster." | "At least twenty percent — it's a lower bound, the real figure is higher." |
| "Zero collisions, guaranteed." | "Zero observed contacts across 268 robot-hours; the bound falls with more exposure, not stronger wording." |
| "It runs on a Raspberry Pi." | "It's built to, and nothing blocks it — but we've never put it on one." |
| "Peer-to-peer fixes Wi-Fi dead zones." | "Not on its own — same radio, same hole. It needs a different link layer." |
| "The dashboard proves decentralization." | "The dashboard shows the behaviour. The three processes and the packet capture are the proof." |

That last row is the one to internalise. **A judge who catches a single overclaim will
quietly re-examine everything else you said, and you will never find out it happened.** The
caveats in Beat 8 aren't damage control — they're what makes the sixty-four percent
believable.

---

## One last thing

You built something that works, you measured it honestly, and you wrote down what it can't
do. That's a stronger position than most teams in the room will be in, and it's worth
walking in knowing that.

If you forget a line, the story still works: *the polite answer never finishes the job, we
made them talk instead, here's the number, here's what we haven't proven.* Everything else
in this document is detail on top of those four clauses.

---

**Siblings:** [README](README.md) · [00. Problem Statement](00-PROBLEM-STATEMENT.md) ·
[01. Requirements Traceability](01-REQUIREMENTS-TRACEABILITY.md) ·
[12. Benchmark and Evidence](12-BENCHMARK-AND-EVIDENCE.md) ·
[15. Limitations](15-LIMITATIONS.md) · [16. Demo Runbook](16-DEMO-RUNBOOK.md)
