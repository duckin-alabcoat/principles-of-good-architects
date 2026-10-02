# ADR-0075: Dispatch is a launcher, and the wave trigger is the landing session

**Status:** Accepted
**Date:** 2026-07-29 (session ~105)
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (the retired master session — the constraint this design is written against), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (`poga` lanes), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the shared coordination store), [ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md) (lanes vs orchestrated subagents — the classification axis), [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) (drawn, never picked), [ADR-0073](0073-work-item-store.md) (the work-item store — the list a range resolves against)
**Deciders:** the operator (asked for one instruction to POGA — take the list, "go do items 1–10" — that spins up a session per item, 2026-07-27; rulings on self-claim, budget, and cap, session ~105); Federation Architect (design + build)
**Reality:** Built — session ~105, 47 tests, suite green; every path built and covered. When this ADR landed, the terminal spawn had **never opened a tab**. The update note below diagnoses that as ungranted macOS Automation permission; **[ADR-0077](0077-spawn-surface-is-detected-and-tmux-is-first-class.md) (session ~107) corrects it** — a session reached over a remote shell is not a GUI login (launchd manager `Background`), so no grant could apply, and Apple Events cannot leave a non-`Aqua` session at all. Dispatch can now spawn (via a detected `tmux` surface). This line formerly read `Partial`, standing *"until a full `poga dispatch --go` runs end-to-end and until the GUI branch is confirmed once."* **Both of those conditions are now met, so Reality is `Built`** (corrected by WI-0190, session ~284): the probe captured on WI-0049 (withheld) ran a full `session.py dispatch WI-0049 --go` end-to-end inside a GUI login session (`launchctl managername` = `Aqua`): one lane spawned in Terminal, the call returned at once with no block, and the lane claimed WI-0049 and wrote its own attention record. The GUI branch was confirmed against the running terminal, not the launcher's own report. No Automation prompt appeared, so the grant this ADR's update note assumed was owed was never owed — consistent with ADR-0077's finding that the blocker was always the `Background` login. Still unexercised and deliberately not claimed: the **iTerm2** branch, which has never been compile-checked against the app's dictionary
**Work item:** WI-0032 (withheld)

## Context

The portfolio can run concurrent lanes, but opening them is manual: read the work-item
list, decide what is independent, open N tabs, tell each one what it is doing. The ask
was to collapse that into one instruction — *"go do items 1–10"* — with the system
deciding what can be fanned out and spinning it up.

The obvious implementation is an orchestrator: one session holds the plan, assigns work,
watches for completions, and dispatches the next wave. That is precisely the **master
session** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)
retired, and re-introducing it would undo the property that makes lanes work — that every
session is a peer which claims, works, and lands independently. The design problem is
therefore not "how do we orchestrate lanes" but **how do we get fan-out without a
coordinator**, which is the same question [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md)
answered for claims.

the operator named the constraint directly: the sessions count themselves, and there is no master.

## Decision

**Dispatch is a launcher, not a boss. It resolves, orders, gates, and spawns — then stops
being special. The wave trigger lives in each LANDING session, not in a supervisor.**

### D1 — Assignment happens at spawn, once

`poga dispatch <range>` resolves an ordinal range against the session's pinned work-item
snapshot, topologically orders it on `blocked-by`, prints one plan, and — on `--go` —
opens a terminal tab per item. After that the dispatching session holds nothing on
anyone's behalf, arbitrates nothing, and collects nothing. A dispatched lane is
indistinguishable from a hand-opened one: it self-claims, works, and lands through the
same CAS gate.

Any design where lanes report back to the dispatcher has reintroduced the master session.

### D2 — The wave trigger is the landing session

A dispatch larger than the concurrency cap does **not** leave a supervisor running to feed
it. The queue is a record in the shared coordination store; each lane, as it lands, pulls
the next queued item and spawns it. The queue drains itself.

The link is an environment variable (`POGA_DISPATCH`) set by the spawning tab, so a
**hand-opened lane never advances someone else's queue** — the continuation is scoped to
sessions that were actually dispatched, not to every session that happens to land.

The right to spawn a given item is **drawn**, by exclusive-create on a spawn slot in the
coordination store ([ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md)):
two lanes landing in the same second cannot both take the same item. The slot doubles as
the item's provenance record and is written **before** the tab is asked for, so nothing
dispatch-spawned can be an anonymous phantom.

### D3 — Two run shapes: one wave by default, a budget on request

The **default is one wave then stop** — spawn up to the cap, then leave the rest queued
and let re-running `poga dispatch` continue it. This keeps the launcher stateless, which
is what keeps D1 honest.

`--run X` gives the chain a **budget** of X lanes to work through on its own before it
stops; `--run all` takes the whole range. The budget is a hard stop, not a suggestion —
"run X and then stop" is a bound the operator sets, and a chain that quietly kept going
would be exactly the runaway [P19](../principles/master.md#p19--cap-what-can-run-away)
names.

### D4 — Cap 5, on evidence, bounding concurrency rather than throughput

`--cap` (default **5**) bounds *concurrent* lanes. The bottleneck it protects is the operator's
attention, not spawn capacity. A crashed-but-unlanded lane counts against it, because it
occupies the same attention a live one does. The 07-23 five-way stress run is the empirical
basis: five concurrent interactive lanes, three landed, one stranded silently.

**AMENDED 2026-08-30 (session ~176, WI-0156): the cap counts DISPATCHED lanes only.**
As written above, D4 counted every live lane. That put it in disagreement with the wave
trigger of D2, which passes the baton only between lanes carrying `POGA_DISPATCH` — so a
dispatch issued while the cap was already full of **hand-launched** lanes spawned zero,
and with zero spawned there was no lane to pass the baton to and never would be. It then
reported itself `[draining]`, which reads as progress. Observed live: `D-4d41da`,
2026-08-22, cap 2 against 2 hand-opened lanes — spawned 0, sat `[draining]`
for six hours.

the operator ruled the fix, and it is not the obvious one: sessions not launched by the command
do not count, and dispatch ignores them and runs its own. A lane he
opened by hand is **his own work, not dispatch load**. Three candidate fixes (refuse
rather than queue; let any landing lane drain any dispatch; merely stop calling it
draining) are **superseded and must not be implemented** — they reconcile the two counts,
while this removes the disagreement. Because a dispatch now always spawns at least one
lane, a lane always exists to carry the baton and the deadlock is **unreachable** rather
than detected: no detector, no refusal path, no special case
([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).

The consequence is deliberate and is flagged rather than argued: **total concurrency on
the machine is now hand-launched lanes PLUS the dispatch cap** — `--cap 5` beside two hand
sessions means seven live sessions. The number that bounds *spend* is therefore the
dispatch cap alone, never the machine total. Any future load-based guard belongs **beside**
this count, never inside it; folding one in would silently restore the all-lanes reading
and with it the deadlock. The D4 reasoning above is otherwise unchanged: the bottleneck is
still the operator's attention, and a crashed-but-unlanded **dispatched** lane still counts.

Mechanism: the dispatch handle is recorded on the lane's own coordination reservation at
the moment the lane is **drawn**, because that is the only point where it is knowable —
`POGA_DISPATCH` rides in the environment the spawning tab set, readable by the child
drawing its lane and by nothing the dispatcher can reach afterwards (the dispatch record
never learns which lane its item landed in). Absent for a hand-launched lane, which is
exactly the distinction the cap needs. Pinned by `TheCapCountsDispatchedLanesOnlyTest`
in `tests/test_lane_alloc.py`, verified against a mutation of the count.

### D5 — Classification stays judgment; the code defaults to lanes

[ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md) gives the axis — sub-tasks of one
goal that one context should synthesize are subagents; independent streams each worth a
steerable session are lanes — but applying it requires *reading the work*, which is
judgment, not mechanism ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).
So there is no classifier in the code. **Every item defaults to a lane** — the safe
direction, since a wrong lane costs a terminal tab while a wrong subagent runs
judgment-needing work headless — and `--subagent WI-…` is how the dispatching Architect
keeps an item in its own session instead.

The alternative (a heuristic scoring items by group, size, or title) would be a judgment
call wearing a mechanism's clothes: deterministic-looking output that nobody could defend
per-item, which is the failure
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
describes one level up.

### D6 — Unresolved is named, held is named, and the surface is refused when unknown

Three honesty properties, each the same shape:

- An ordinal the snapshot does not carry, or an id not in the store, is reported
  **UNRESOLVED** and blocks `--go` unless `--force`. *"Do 1–10"* must never quietly
  become *"do the eight I could find."*
- An item whose live blocker this dispatch will not itself spawn is **held** with its
  blockers named — including a blocker that was skipped because another lane already
  claimed it, which a naive in-range check releases early.
- The spawn surface is validated against a named set (`Terminal`, `iTerm2`) and
  **refused** if unlisted, rather than attempted. The phantom-session saga was a
  launcher-surface problem; "try it and see" is how a session ends up unowned.

### D7 — The land-path hook fails open

The continuation runs after the land is reported and irreversible, and swallows its own
errors. A dispatch problem must never turn a landed lane into a failed one.

## Consequences

- **Fan-out without a coordinator.** The federation gets "go do items 1–10" while keeping
  every property [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)
  bought: no master, no leases held on another session's behalf, every lane landing
  independently.
- **A dispatch survives its dispatcher.** Because the queue lives in the shared store and
  the trigger is the landing session, closing the dispatching session does not strand the
  remaining items — the next lane to land picks them up.
- **A restarted lane breaks its own link.** `POGA_DISPATCH` lives in the spawned shell's
  environment, so a session restarted in that lane without it will not advance the queue.
  The dispatch is not lost — re-running `poga dispatch` continues it — but the chain does
  not self-heal. Accepted for v1 because the default run shape is re-run anyway; revisit
  if it bites.
- **Seeing which lane waits becomes load-bearing.** At cap 5 with waves, knowing which
  lane is *waiting on the operator* is the difference between waves working and repeating the
  silent strand. POGA publishes that state in its status feed; any optional consumer may
  read it and show it. Surfacing it is WI-0031, still open — dispatch ships without it,
  and this is the known gap, not an oversight.
- **v1 is Claude-only.** A non-Claude lane has no journal, no heartbeat, and no reaper
  visibility, so dispatching one would deliberately manufacture the silent-strand class.
  Non-Claude dispatch unlocks with the runtime-tiers work (WI-0033).
- **Fleet substrate.** `poga` and `session.py` are pushed byte-identical, so every member
  gets dispatch at the next substrate push. It no-ops usefully for a member with no
  work-item store: the range simply resolves to nothing.

## Alternatives considered

- **An orchestrating dispatcher that watches lanes and feeds the next wave.** Rejected —
  it is the master session by another name, and it re-creates the contention the peer
  model removed. It also fails on its own terms: the orchestrator has to stay open for the
  duration, so closing the window strands the queue.
- **Fan out with the Workflow tool instead of lanes.** Rejected per
  [ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md): Workflow subagents are
  ephemeral and headless, with no Architect identity, no journal, and no independent
  trunk-landing. the operator's ask was explicitly for sessions that interact with the operator
  and ask questions exactly as a hand-started session would — which is a lane, not a subagent.
  Workflow remains the in-lane tool.
- **A heuristic classifier for subagent-vs-lane.** Rejected per D5.
- **Pre-claim every item at dispatch time.** Rejected in favour of self-claim at lane
  start (the operator's call): pre-claiming leaves claims outliving a failed spawn, and a lost race
  reads louder when the lane itself reports *"WI-0047 is already claimed — I stopped"* than
  when a claim silently exists for a tab that never opened.
- **Let any landing session advance any dispatch.** Rejected: simpler, and genuinely
  leaderless, but a surprise — landing an unrelated lane would open tabs the operator did
  not ask for. Scoping the trigger to dispatched lanes costs one environment variable.

## Update note — 2026-07-29 (same session): the spawn is unexercised, and it hung

The decision above stands unchanged; this records what the live probe found after the
build, because the ADR would otherwise read as though dispatch had been seen to work.

**It has not.** Every test above mocks the terminal. Running the real
`_dispatch_open_tab` on the runner host, `osascript` did not open a tab and did not error —
it **blocked on the Apple Event for ~120 seconds** before returning
`AppleEvent timed out (-1712)`. Identical with the tool sandbox disabled, so the cause
is not the harness: macOS **Automation permission** has not been granted to the calling
app. Until it is, dispatch cannot spawn anything.

Two consequences, both taken:

1. **The unbounded call was a real defect, and the mocked suite was green through it.**
   A five-item dispatch would have sat silently for ten minutes — a hung launcher is the
   silent-strand failure this whole design exists to prevent, arriving through the
   launcher itself. `_dispatch_open_tab` is now bounded at 20 s and reports the probable
   cause, where System Settings → Privacy & Security → Automation is, and the by-hand
   fallback. A wave stops on the first failed spawn rather than grinding through the
   queue. Verified live: 20.0 s, named message. Five tests pin it, including one that
   asserts the `timeout` kwarg is actually passed — a test checking only the
   `TimeoutExpired` branch would have passed against the broken version.
2. **`Reality` is `Partial`, not `Built`** ([`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status)).
   Resolution, ordering, the plan gate, the drawn spawn slot, budget/cap, and the
   landing-session continuation are all exercised. The one step that opens a tab is not,
   and calling that `Built` would be the doc-reality gap the habit exists to catch.

**The iTerm2 branch is likewise unverified** — it was not exercised when this ADR landed, so its
AppleScript could not even be compile-checked (`osacompile` cannot resolve the terms
without the app's dictionary). The Terminal branch compiles clean. iTerm2 stays in
`SPAWN_SURFACES` because a runtime failure there is loud and bounded by the same
timeout, but it is a named-and-untested surface, not a verified one.

## References

- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — the peer-session
  model this must not violate.
- [ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md) — the lanes-vs-subagents axis
  D5 defers to.
- [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) — the
  drawn-not-picked discipline the spawn slot implements.
- `proposed-edits/federation-arch/accepted/2026-07-27-consultant-dispatch-from-the-work-list.md`
  — the commissioning brief and the operator's original rulings.
