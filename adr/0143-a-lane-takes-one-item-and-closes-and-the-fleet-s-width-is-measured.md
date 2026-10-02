# ADR-0143 — A lane takes one item and closes itself, and the fleet's width is measured rather than fixed

**Status:** Accepted
**Date:** 2026-09-17
**Session:** ~362 (lane, dispatch D-2c72d4)
**Deciders:** the operator — ruled R2 and R3 YES on 2026-09-04 (session ~208) after reading them in plain
language, and clarified the cap the following day (2026-09-05: fan-out of work items needs a viable
route). Federation Architect — the record, the supersession chain in D6, and the decline in D5.
**Work item:** WI-0345
**Records:** WI-0288 R2 and R3 — the ruling, its shipped code, and the one piece of it that was
withdrawn. WI-0288 asked for this ADR in its own body, closed without it, and said so in its close
note; WI-0345 is the item that refused to let that record decay into a `done` row.
**Builds on:** [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md)
(the `--confirm` gate and the `close-confirm` receipt, both kept),
[ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md) (the exemption follows authorization,
not location), [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md)
(D1 dispatch is a launcher not a boss, D2 the wave trigger is the landing session, D3 one wave by
default, D4 the `--cap`), [ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md) (lanes are
leaderless peers, not orchestrated subagents),
[ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) (a lane may block on a question,
never invisibly), [ADR-0125](0125-an-unattended-run-closes-on-its-own-receipt.md) (an unattended run
closes on its own receipt)
**Corrects:** [ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md)'s Context, which licenses
its supersession of ADR-0104 D5 on two sentences — *"`session.py merge` commits, gates and
CAS-advances the trunk and **leaves the journal open**"* and *"A lane refused a close today lands with
`merge` and carries on."* Both were true when written on 2026-09-04. Both became false in session
~230, when R2 (1) inverted `merge`'s default. **ADR-0113's decision is unaffected** — see D6; only its
stated ground needs re-reading, through `merge --continue`.
**Reality:** Built — D1, D2, D3, D4 and D6 all ship in the tree and are cited below by symbol. D5 is a
**decline** and correctly has no artifact.

---

## Context

**A fleet-wide doctrine inversion shipped, is enforced in code against every member, and was never
written down.** On 2026-09-04 the operator ruled two structural changes YES: **R2**, one item / one lane /
one session, and **R3**, dispatched lanes never block. The doctrine half landed in `769178b`; the
code half landed in session ~230; the decisions view (R3 (5)) landed in session ~267 as WI-0330.
WI-0288 was then closed on instruction.

None of it got an ADR, and WI-0288's own body had asked for one — *"both are structural changes with
fleet blast radius … R2 in particular reverses a paragraph that ADR-0113 and ADR-0104 D5 both reason
about, so the supersession chain needs stating."* [`federation-arch.md`](../federation-arch.md) §5
requires an ADR for every non-trivial structural choice.

**The gap has a specific, visible shape, and it is the reason this record is not ceremony.** `cmd_end`
prints a refusal today — reached by a bare `merge` through `_merge_close_args` — that asserts in
shipped, operator-facing text:

> (A bare `merge` is a close too since WI-0288 R2, and is refused here on the same terms.)

A decision cited by number, enforced against the whole fleet, recorded in no ADR at all. A grep of
`adr/` for any phrasing of a bare merge being a close returns **zero** files; **zero** ADRs in the
tree cite WI-0288. A substrate that refuses an operator on the authority of a decision it never wrote
down is asking them to take its word for it. That is what this record closes.

(Guard against a false match, so nobody re-derives it: the only `adr/` hits for *"turn budget"* are
[ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) and
ADR-0036 (withheld), whose budget is the number of turns spent at
**startup** — a different quantity from a dispatched lane's `--max-turns`, which is what R2 (2) ruled
on. Same word, different mechanism; neither is prior art for D2.)

### The supersession chain, which is the part that actually needed stating

Three records reason about the same paragraph, and each moved the line:

1. **[ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md) D5** exempted
   a worktree lane's `end` from the `--confirm` gate, because in a lane `end` *is* the land. D5 named
   the hole it was leaving open in its own text — the exemption was drawn on **where** `end` runs
   rather than on **what it is doing** — and kept it anyway, on one stated reason: *"a lane that
   cannot land strands its work — a considerably worse outcome than the one being prevented."*

2. **[ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md)** superseded D5 on 2026-09-04 and
   redrew the line where D5 itself said it belonged — on what **authorized** the close. It could do
   that because D5's reason had expired: `merge` had become a land verb that *left the journal open*,
   so a lane refused a close could land with `merge` and carry on. The refusal cost one question, not
   a strand.

3. **R2 (1) then inverted `merge`.** Since session ~230, `merge` **closes** by default. ADR-0113's
   licensing sentence — the one asserting that `merge` leaves the journal open — has been false in
   the tree ever since.

**The conclusion survives the premise, and that is what decides the remedy.** ADR-0113 needed *a route
by which a refused lane can still reach the trunk without closing*. That route still exists; it is now
spelled `merge --continue` rather than bare `merge`. The escape hatch was **renamed, not removed** —
and R2 (1) shipped the flag in the same change that inverted the default, precisely so it would not
be. So ADR-0113 is **corrected, not superseded** (D6). Retiring a sound decision because one
supporting sentence aged would throw away a correct conclusion to punish its footnote.

### The wave cap: a starting value that was read as doctrine

WI-0288's R3 carried a third code item — *"Waves capped at two."* — still listed as unshipped when
WI-0345 was filed. The citation checks out, and was re-verified in this lane rather than inherited:
`DISPATCH_CAP_DEFAULT = 5` in `sessionlib/config.py`, and **no identifier containing "wave" exists
anywhere under `sessionlib/`** — every occurrence of the word is a comment, docstring or help string.
Nothing counts waves, so nothing can cap them.

But the number had already been withdrawn — by the operator, on 2026-09-05, one day after the R3 ruling and
six weeks after the cap was first decided. His clarification is quoted in full in D5. Two independent
records agree with it, both verified here:

- **The cap has never been 2.** `DISPATCH_CAP_DEFAULT` was born at **5** on 2026-07-29 in the commit
  that introduced dispatch, whose message records the basis — *"Cap defaults to 5 concurrent (the operator's
  call, on the 07-23 five-way evidence)"* — and has held that value continuously since, moving only
  once, untouched, in the `sessionlib/` split. There is no commit in the repo that ever set it to 2.
  So cap 5 is not a raise anybody has to justify against the operator's *"start at two today"*; it is the
  original July decision that the same clarification says *"stands"*, recorded as
  [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D4.
- **Cap 5 is live practice.** The consultant's own later wave plans, 2026-09-10 and 2026-09-12, both
  plan at cap 5 as ordinary operation.

So R3 (3) is not a piece that fell through. It is a piece that was **rescinded before any of R3's code
shipped**, and WI-0288's body — written from the 2026-09-04 ruling — was simply never updated for the
2026-09-05 clarification. An item that quotes a withdrawn value keeps asking for it forever, and two
separate sessions have now correctly found no wave-cap symbol and drawn the wrong conclusion from it.

## Decision

**D1 — `merge` closes the session by default; `--continue` is the opt-out.**

The shape of a session is: open a lane, land one work item, close. `merge` gates and CAS-advances the
trunk by the same plumbing the land uses, then closes the session exactly as `end` does.
`merge --continue` lands *without* closing, keeping the session's id, ordinal, context and claims —
the **exception you say out loud**, not the shape of a session.

There is **one close path, not two**: `cmd_merge` delegates to `cmd_end` rather than reimplementing a
close ([P16](../principles/master.md#p16--avoid-duplication)), and it stamps the verb the operator
actually typed so a refusal names `merge` rather than `end`. That delegation is also what keeps D1
from being a bypass: an ungated closing `merge` would be a clean way around ADR-0113's guard, so the
closing half runs through the guarded verb.

The inversion must not regress the exit-code honesty landed in `0bf8906` across the five
`LandOutcome` call sites; `cmd_end` and `cmd_merge` share the 0/1/2 exit convention from
`cmd_integrate`, and changing merge's default close behaviour touches those same paths.

**D2 — A dispatched headless lane launches with a per-lane TURN budget; it is a third bound, with its
own name, and it notifies rather than kills.**

Nothing bounded a headless lane's *length*. A turn budget is what makes D1 true for lanes nobody is
watching. `dispatch --max-turns`, default **60** — deliberately generous and not derived from
anything: the budget exists to bound a lane that has **stopped converging**, not to cut short one that
is working.

*The naming is load-bearing and is part of the decision.* Three quantities bound a dispatch, and two
of them were already called "budget" somewhere:

| Quantity | Name | Flag | Default |
|---|---|---|---|
| How many dispatched lanes run **at once** | `cap` | `--cap` | 5 ([ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D4) |
| How many lanes are **ever opened** by one dispatch | `budget` | `--run` | one wave, i.e. `cap` ([ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D3) |
| How long **one lane** runs | `turn_budget` | `--max-turns` | 60 |

`turn_budget` is never folded into `budget`: that key is the spawn count, `CapAndBudgetTest` asserts
on it, and conflating the two would silently re-scope both. WI-0288 warned about this collision before
it could happen, and the warning is kept here because the names are the only thing preventing it.

*How it is enforced, stated plainly because the mechanism is not the obvious one.* `--max-turns` is
**not a `claude` flag** — verified rather than assumed: the installed CLI has none, and these lanes
launch as interactive TUI sessions. So the budget is ours to enforce, at the only event that fires
once per turn: the `Stop` hook, counting only when `POGA_DISPATCH` is set, so a hand-opened session is
never counted. It is delivered as a **notice, not a kill**. Killing the runtime at turn N would
strand unlanded work in a worktree — exactly what the budget exists to protect — so what the lane
receives is an instruction to land and close, or to park. A budget that cannot terminate a lane is a
weaker instrument than one that can, and that trade is made knowingly: a stranded worktree costs more
than an overrunning lane.

**D3 — A dispatched lane never blocks. It writes the question on its item, parks it, exits, and
releases the slot.**

The operator answers from the decisions review surface (`poga decisions`), and re-dispatches. A lane opened by a dispatch was
opened by a process, not a person, so a question asked into it holds a slot nobody is coming to
release. Hand-opened sessions still ask live — this changes nothing about a session a human is sitting
in front of.

**D4 — Dispatch refuses an item with no acceptance criteria or with an open decision, and the smaller
queue is accepted rather than escaped.**

This is the half that makes D3 honest. If a lane parks because its item was never answerable, dispatch
should not have spawned it; without the refusal, D3 converts blocked lanes into parked items and the
queue depth merely moves.

Measured against the live store when the predicate shipped, it took dispatch from 112 open items to
8 — 96 refused for no acceptance criteria, 8 for an open decision. the operator ruled on that number in
session ~241: **accept the smaller queue.** No `--force` escape for items carrying no acceptance
criteria, because that reopens exactly the hole the gate exists to close. The backfill happens in
triage sittings, which is a demonstrated route — one session backfilled 33 items in a single pass.

**D5 — The fleet's width is bounded by three conditions and configured by `--cap`. A fixed cap of two
is DECLINED, under either reading of the phrase.**

the operator's clarification of 2026-09-05 is the operative ruling, quoted in full so nobody has to chase the
brief:

> *"waves capped at two" was the consultant's starting value while lanes still blocked on the operator and
> starved each other at the gate — NOT doctrine, and it must not be encoded as a fixed number. the operator
> ruled in July that the lane cap is configurable; that stands. The rule is: a lane takes ONE item
> (R2) — the fleet runs as MANY lanes as three conditions allow: each item READY (acceptance criteria
> + decisions made), the in-flight set INDEPENDENT (no shared files/subsystem — dispatch checks
> declared scope), and the count BOUNDED BY GATE THROUGHPUT (raise the cap until the land queue starts
> growing; the board's context-per-turn and parked-question rows say when it is too high). Start at
> two today; raise it as R3 and the gate redesign land.*

So the width is a **measurement**, not a constant, and all three conditions it rests on are built:

- **READY** — the readiness predicate, D4 above.
- **INDEPENDENT** — dispatch reads each item's declared `FILES:` and plans the in-flight set as
  disjoint. It also names the items it **could not** assess rather than folding *"couldn't tell"* into
  *"checked and it's fine"*, which matters here: planning-as-disjoint on no evidence is the failure
  mode that would make this condition decorative.
- **BOUNDED BY GATE THROUGHPUT** — `--cap`, raised or lowered against what the land queue is actually
  doing, with the board's rows as the read-out.

*The phrase admits two readings, and the decline covers both.* "Waves capped at two" can mean the
**width** of a wave (two concurrent lanes) or the **number of rounds** (two waves, then stop). The
decider's own gloss resolves it to the width — he answers it with *"the lane cap is configurable"*,
*"as MANY lanes as three conditions allow"* and *"start at two today"*, and the harm he names is lanes
*"starving each other at the gate"*, which is contention between concurrent lanes rather than between
rounds. That reading is declined above. The rounds reading is declined too, and by older law: under
[ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D2 the wave
trigger lives in each landing session and there is no supervisor to count rounds — an orchestrating
dispatcher that watches lanes and feeds the next wave is a **rejected alternative** in that ADR. The
chain is bounded instead by `budget`, the total-spawn count, which is the bound D2's table names.
Counting rounds would require re-introducing the boss ADR-0075 D1 exists to refuse.

**"Start at two today" was a dated instruction, not a default.** It was never implemented, and the
value it would have lowered has been 5 since the day dispatch was built, on the operator's own July evidence.
Encoding `2` now would contradict the July decision his clarification explicitly preserves, and would
convert a tuning knob into doctrine — the precise thing he said not to do.

This decline is the written refusal WI-0345's acceptance criteria calls for. **R3 (3) is closed as
declined — not as shipped, and not as owed.**

**D6 — ADR-0113 is corrected, not superseded; ADR-0104 D5 stays superseded by it.**

Stated as a chain, so a reader landing cold gets it in one pass:

| Record | What it held | Standing now |
|---|---|---|
| ADR-0104 D5 | A lane's `end` is the land and is exempt from `--confirm`; the hole is drawn on **where** `end` runs | **Superseded** by ADR-0113, and stays superseded |
| ADR-0113 | The exemption follows **what authorized the close**; licensed by *"`merge` … leaves the journal open"* | **Stands.** Its decision is untouched; its licensing sentence is corrected here |
| D1 above (R2 (1)) | `merge` closes by default; `--continue` lands without closing | Current |

ADR-0113's argument needed a route by which a refused lane reaches the trunk without closing. That
route is now `merge --continue`. Read ADR-0113's Context with that substitution and every step of its
reasoning holds; read it literally and one sentence is false. Per [`adr/README.md`](README.md),
accepted ADRs are not edited in place, so the correction lives in this record's `Corrects:` header
rather than in ADR-0113's text — which also preserves the evidence that the sentence was true when
written.

## Alternatives Considered

- **Ship the wave cap of two, as WI-0288's text literally asks.** Rejected — it was withdrawn by the
  decider on 2026-09-05, before any of R3's code shipped, and building it would mean implementing a
  number against the explicit instruction that it *"must not be encoded as a fixed number"*.
- **Set `DISPATCH_CAP_DEFAULT = 2` while keeping `--cap` configurable** — the soft reading of *"start
  at two today"*. Rejected — the instruction is dated, was never implemented, and the value it would
  lower is not a drift but the original July decision the same clarification preserves. Narrowing live
  fan-out to satisfy a quotation rather than a measurement inverts D5's own rule.
- **Cap the number of waves instead of the width**, on the literal reading of the phrase. Rejected —
  see D5: nothing counts waves, and counting them requires the supervisor
  [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) D1 and D2
  exist to refuse.
- **Supersede ADR-0113 rather than correct it.** Rejected — its conclusion survives the change to its
  premise intact, because the escape hatch it depends on was renamed (`merge --continue`) and not
  removed. Superseding would retire a sound decision and force a re-derivation of reasoning that is
  already right.
- **Edit ADR-0113's Context in place to say `merge --continue`.** Rejected — accepted ADRs are
  immutable except for status flips; `Corrects:` on a later record is the sanctioned route.
- **Two ADRs, one for R2 and one for R3.** Rejected — they were ruled together, in one sitting, and
  they are load-bearing on each other: R2's one-item-one-lane shape is what makes R3's
  park-and-release-the-slot coherent, and D2's turn budget is what makes D1 true for a lane nobody is
  attached to.
- **Make the turn budget terminate the lane.** Rejected — a kill at turn N strands unlanded work in a
  worktree, which is the outcome the budget exists to prevent. See D2.
- **Leave it unwritten**, as WI-0288's closing note allowed — a mint for the weekly review, not a
  reason to hold the item. Rejected — that is the outcome WI-0345 exists to correct, and the
  enforced-but-unrecorded refusal quoted in Context is what it costs.

## Consequences

- **The refusal text can now cite a decision that exists.** `cmd_end`'s refusal asserts that a bare
  `merge` is a close *"since WI-0288 R2"*; that decision is D1 of this record. A reader tracing the
  claim lands on an ADR rather than on a closed work item.
- **R3 (3) stops being owed.** It has been carried as unshipped across two items for roughly two
  weeks and re-verified as missing at least twice — each verification correctly finding no wave-cap
  symbol and drawing the wrong conclusion from it. The code was never the problem and no code answers
  it.
- **The cap remains a knob someone has to turn.** D5 makes width a measurement, which means nothing
  automatically raises or lowers `--cap` against gate throughput. The obligation lands on whoever
  plans a wave, informed by the board. That is deliberate: a load-based auto-guard is
  [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md)'s explicitly
  deferred design, and it belongs *beside* the dispatch cap rather than folded into it.
- **The `INDEPENDENT` condition is only as good as the `FILES:` declarations.** Dispatch assesses
  disjointness from authored declarations and reports which items it could not assess; items without a
  `FILES:` line are planned as disjoint on no evidence. The check says so out loud, and D5 depends on
  someone reading that line.
- **The turn budget can be exceeded.** D2 notifies; it does not enforce. A lane that ignores the
  notice runs on, and the only remedy is a person or a later sweep. Worth knowing before anyone treats
  the budget as a bound rather than a signal.
- **An ADR premise can expire without anyone editing anything.** ADR-0113's sentence was true the day
  it was written and false eleven days later, because a *different* record changed the code it
  described. This is the second recorded instance of that class in `adr/` after
  [ADR-0140](0140-the-integrate-validates-outside-the-land-gate.md). A present-tense claim about
  mechanism is a hypothesis with a shelf life; `Corrects:` is the cheap fix, and finding these belongs
  to the ADR-reality sweep rather than to luck.
- **Closing an item on instruction does not close what the item required.** WI-0288 wrote its own
  incompleteness into its close note rather than letting a `done` row absorb it, and that paragraph is
  the only reason this ADR exists. The habit is worth keeping: write the gap into the close, and let
  the weekly review mint it.

## References

- WI-0288 — the ruling, the split halves, and the close note that recorded this ADR as owed
- WI-0345 — the item requiring this ADR and the written decline in D5
- WI-0330 — R3 (5), the decisions review surface (`poga decisions`)
- `proposed-edits/federation-arch/pending/2026-09-04-consultant-finish-line-tests-and-scoreboard.md`
  §1a — the operator's R2 and R3 rulings, and the 2026-09-05 cap clarification quoted in D5. The same section
  carries his framing that this makes a dispatched lane a batch worker with its own lane, journal and
  landed commit rather than a subagent — the axis
  [ADR-0063](0063-poga-lanes-vs-orchestrated-subagents.md) draws
- [ADR-0104](0104-a-session-closes-on-the-user-s-word-never-the-agent-s-judgment.md),
  [ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md) — the supersession chain stated in D6
- [ADR-0075](0075-dispatch-is-a-launcher-and-the-wave-trigger-is-the-landing-session.md) — dispatch as
  a launcher; D2 the wave trigger, D3 the one-wave default, D4 the `--cap`
- [ADR-0101](0101-a-lane-may-block-on-a-question-never-invisibly.md) — the blocking contract D3
  replaces for dispatched lanes
- [`STANDARD.md`](../STANDARD.md) §"All work happens in a lane" — the doctrine text D1 is enforced
  against
