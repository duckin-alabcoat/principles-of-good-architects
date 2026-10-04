# ADR-0140 — The integrate validates outside the land gate, and the escalation leaves the section

**Status:** Accepted
**Date:** 2026-09-17
**Session:** ~349 (lane, dispatch D-068e90)
**Deciders:** Federation Architect (all of it — the relocation, the overturned refusal, the structural guard's scope, the bounded fetch, and the decision to bound by placement rather than by a timer). Under the operator's standing ruling of 2026-09-13: *"Plumbing, mechanics, schema details, ordering, retries, which of two equivalent fixes: make the decision yourself, record it under Decisions I made without you."* Nothing here changes what the system is for.
**Work item:** WI-0378 (bin A item A14 of the OPS-0007 pack for 2026-09-18)
**Completes:** [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) §"What this does not do" — both findings named there, plus a third that grew afterwards
**Overturns:** [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md)'s refusal to move the push-rejected escalation out of the held section (the premise is corrected below, not the judgment second-guessed)
**Corrects:** [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) line 124, which states that integrate writes receipts under an `integrate` verb. It did not. [ADR-0134](0134-a-land-publishes-or-says-it-did-not.md)'s rejected alternative *"`integrate` … runs without the gate"*, which was quoted from a docstring the code four lines below it contradicted.
**Builds on:** [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) (the verb, and D5's rule that every refusal is inert), [ADR-0102](0102-integrate-merges-a-diverged-trunk-never-replays-it.md) (the merge), [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) D6/D7 (re-entrancy; a rebase is always re-gated), [ADR-0134](0134-a-land-publishes-or-says-it-did-not.md) D2/D3/D4 (LANDED, PUSH OWED as a named state; the bounded push)
**Reality:** Built — three call paths relocated, one shared escalation helper, a bounded fetch, a receipt under the `integrate` verb, and a call-graph guard with five self-tests.

---

## Context

### The measurement, re-taken rather than inherited

WI-0378 was minted from a 14-day figure: *"3 events in 14 days took 51.4% of all hold time and 3 of 4 breaches."* Re-taken on 2026-09-17 against `land-receipts.jsonl` in this machine's git common dir, 184 receipts spanning **2026-09-10 → 2026-09-17**:

| | |
|---|---|
| total land-gate hold, all receipts | **837.926 s** |
| the `integrate` stage | **356.201 s — 42.5 %, from 4 events** |
| holds over the 30 s budget | 4, of which **3 carry an integrate** |

**Three corrections to the figure the item carries, and none of them makes the case smaller.**

1. **It is 42.5 %, not 51.4 %** — and the share fell by *dilution*, not by improvement. The integrate numerator is unchanged at 356.201 s; there has been no integrate event since 2026-09-13. Fifty more receipts landed on 2026-09-17 with no integrate among them and grew the denominator. Re-running the same script at `--asof 2026-09-15` gives 47.2 % on the identical numerator. A falling share here means the rest of the day got busier ([`a-measured-gap-can-invert-not-just-drift`](../habits/master.md)).

2. **The window is 6.85 days, not 14.** The receipts file does not reach further back; retention is 45 days, so a trim is not the cause — there simply are no older lands. Any "per 14 days" rate computed from it is an extrapolation. Volume is also lumpy: all four integrate events fall on two adjacent days (09-12 and 09-13, the heavy concurrent-land days), and 09-17 ran 50 lands with zero. The verb is **bursty and correlated with dispatch waves**, which is exactly what its trigger predicts — it needs a peer to have pushed while you were landing.

3. **`integrate` is not a verb in the receipts at all.** It is a *stage key* inside `land-lane` records. The only verbs present are `land-lane` (183) and `resume-lane` (1). That matters far more than the arithmetic, and it is its own finding — see below.

### What is actually expensive, measured

The stage is recorded as one opaque number, so the breakdown comes from comparing it against each receipt's own `validate_seconds` — one full suite, measured outside the lock, in the same run on the same machine:

| receipt | integrate | that land's own full suite | ratio |
|---|---|---|---|
| 2026-09-12 poga-11 | 75.024 s | 99.157 s | 0.76 |
| 2026-09-13 poga-5 | 154.648 s | 131.890 s | **1.17** |
| 2026-09-13 poga-1 | 124.462 s | 118.285 s | **1.05** |

Median `validate_seconds` across all 183 receipts carrying one: **131.9 s**.

**So the expensive thing is the re-gate and nothing else.** In those same two 2026-09-13 receipts the merge cost 0.143 s and 0.084 s, and the push 0.784 s and 0.776 s. The integrate is a second full suite run, and until this ADR it ran with the land gate held.

Two things the same reading settles, both of which narrow the claim rather than widen it:

- **The 2026-09-12 event is a different fault and is already closed.** Its 75.024 s was not a suite; it was ssh's own TCP timeout on a broken route to the remote, re-paid on a fetch after the push had just spent 75.013 s failing the same way. [ADR-0134](0134-a-land-publishes-or-says-it-did-not.md) D5 stopped the land buying that fetch after a positively-identified unreachable push. Pooling it with the other two would overstate the re-gate case, so it is excluded from the argument and named here instead.
- **The fourth breach has no integrate in it.** 2026-09-13, a 30.576 s hold that was **98.5 % recompile** (30.109 s). Bounding the integrate leaves that one standing. It is [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D1's deferred item with its own named reopening threshold, and it is not touched here.

### Three paths, where the record named two

ADR-0124 closed with two findings: the push-rejected escalation, and the verb. A check written for this item — a call-graph walk from every held section — found **three**:

```
sessionlib/land.py:4695   with land_gate_lock() in cmd_integrate
      _integrate_trunk_with_remote -> _gate_commit
sessionlib/land.py:1506   publish=publish in _resume_owed_publication
      _integrate_trunk_with_remote -> _gate_commit
sessionlib/lanes.py:1016  publish=publish in _land_worktree_lane
      _integrate_trunk_with_remote -> _gate_commit
```

The third is WI-0326's resume path, built *after* ADR-0124 and on top of it, inheriting the shape from the block it was modelled on. Nobody wrote it down because nobody was looking for a class — the two findings were filed where they were observed. This is [`retire-the-class-not-the-instance`](../habits/master.md) arriving on schedule: the moment you are most confident the class is closed is the moment you have the least evidence for it.

### ADR-0124's claim about receipts was false, and it hid the verb

ADR-0124 line 124: *"Its receipts are written under the `integrate` verb so the board can tell the two apart, but the budget is not enforced differently for it."*

The second clause was true. The first was not: `cmd_integrate` wrote no receipt, and neither did `_integrate_trunk_with_remote`. So an operator-invoked integrate could hold the gate for minutes and **FL7 would never see it** — the probe is red if any hold in its window exceeds the budget, and a verb that writes no receipt has no holds in any window. The 42.5 % above is the *nested* stage only; the standalone verb's cost is not in that number and never was.

A capability with no marker reads as absent to the conformance surface it should make visible ([`ship-the-detector-with-the-capability`](../habits/master.md)) — and here it did worse than fail to detect: the ADR's own sentence certified a measurement that did not exist.

## Decision

### D1 — The integrate validates outside the gate and holds it for the advance.

Per attempt, `_integrate_trunk_with_remote` splits at the **write boundary**:

- **Outside, with no lock at all:** the fetch, the classification, `_merge_onto` (which builds in a throwaway worktree — ADR-0097 D2), the counter land gates, and `_gate_commit`. None of it writes anything another lane can observe; ADR-0097 D5 already guarantees every refusal on this half is inert.
- **Inside, through `_serialized_advance`:** re-read the trunk, compare-and-swap, publish (sync, recompile, push).

This is [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D1's shape, applied to the fourth caller it was not applied to, and it brings that helper's three properties with it for free: the parent-equals-trunk identity, the stage-timed receipt, and the `moved` release.

**`moved` is the yield, and it is the whole bound.** A sibling that lands while we are gating is discovered under the lock, before anything is written; the lock is released immediately and the next attempt re-reads and re-gates *outside the queue*. ADR-0114 D7 is untouched — the re-validation is always a full one. What changes is only where it is paid.

**The bound is placement, not a timer,** and this was a deliberate choice against the acceptance's literal wording ("when it exceeds the bound it reports what it was doing and yields"). A timer inside the section would have to fire at some moment and choose between a half-published trunk and holding anyway; placement removes the moment. Validation *cannot* overrun the hold budget, because it does not hold the gate — that is a stronger guarantee than any threshold, and it is checked structurally rather than watched at runtime (D4).

The cheap branches keep their own narrow holds: `behind` wraps `_ff_trunk_to_origin` (which was covered only by the blanket hold and would otherwise have moved the trunk ref with no land gate at all — a regression this change would have introduced silently), and `ahead` / `no-remote-trunk` publish through a new `_serialized_push_only`, which verifies the trunk is still what we classified and then pushes. Neither carries a gate run; `sync` medians 0.087 s over 122 receipts.

### D2 — The push-rejected escalation runs after the section is released, not inside it.

One helper, `_escalate_to_integrate`, replaces the two near-identical blocks that lived inside `_land_worktree_lane`'s and `_resume_owed_publication`'s publish closures. Each caller now records that the push bounced, lets `_serialized_advance` / `_serialized_publish` return, and escalates with the gate free.

**This overturns ADR-0124, and the disagreement is about a premise, not a judgment.** That ADR left the escalation inside the hold because *"the alternative — releasing the lock mid-publication, with the local trunk already advanced and origin not — is a worse shape than a slow hold."*

The intermediate state is real. It is simply not created by the release. **It is created by the rejected push, one line earlier**, and the escalation *inherits* it rather than producing it. Since ADR-0134 D2/D3 that state has a name (`LANDED, PUSH OWED`), a banner that reports it, a rule that it never fails the land, and a verb whose entire purpose is to clear it. Declining to release leaves the repo in exactly the state the release would leave it in, for as long as a second full suite takes, with every queued lane waiting.

ADR-0124's sentence was written before ADR-0134 shipped that state as a first-class outcome. It was right about the shape at the time and the ground moved under it.

**ADR-0114 D6's re-entrancy stays**, and stays exactly as it was described: a second line of defence rather than the only one. Nothing on the new paths re-enters, and the guard is what makes that safe to be wrong about.

### D3 — Every network call on the integrate path is bounded.

`_fetch_origin` joins `_push_trunk`: `LAND_FETCH_TIMEOUT_SECONDS`, derived from `LAND_LOCK_HOLD_BUDGET_SECONDS` rather than written as a second literal, with the same `unreachable` / ordinary-failure split and the same deliberate incompleteness in the classifier. Integrate's own push goes through `_push_trunk` instead of a bare `sh(["git", "push", …])`, which it had kept only because the structural pusher guard permits that function by name.

ADR-0134 D4 bounded the push and its docstring names the hazard exactly — *"a hang inside the serialized section is the worst failure this code can produce, because it is invisible"*. The fetch beside it went through the same unbounded `sh`, and the receipt that sized the push's bound is the one that proves the fetch needed one too.

### D4 — A call-graph guard, because the class is not the instance.

`tests/test_integrate_holds_no_suite.py` enumerates every piece of code that runs with the land gate held — the body of any `with land_gate_lock():`, plus the closure handed to `_serialized_advance` / `_serialized_publish` — and walks calls transitively to assert none reaches `_gate_commit`, `_run_gate` or `_gate_unless_neutral`.

Three properties it was given on purpose:

- **It follows calls, not lexical containment.** Every one of the three defects was a gate run two or three frames below the `with`. A grep would have found none of them.
- **It resolves the publish closure at the CALL SITE, by finding the callable — never by argument index.** `_serialized_advance` takes `publish` fourth and `_serialized_publish` takes it third. The first draft of this check hard-coded one index, reported two findings, and was wrong: it read the resume path as having no closure at all. That is recorded as a test of its own, because the failure mode is a check that passes and says nothing.
- **It proves itself on the real defect.** Five self-tests: the shape it replaced, the closure shape, the index trap, and two negative controls over correct code — a clean publish closure must not fire, or the guard gets deleted by whoever trips it ([`a-guard-that-fires-on-correct-code-gets-deleted`](../habits/master.md)).

### D5 — The integrate writes its own receipt, under its own verb.

`verb: "integrate"`, with the suite's cost filed under `validate_seconds` (outside the lock) and the CAS under `stages.merge` (inside it). FL7 needs no change: it reads every receipt carrying a numeric `lock_seconds`, so the verb drops into the existing population and the existing red-if-any rule, and its FAIL evidence line already prints the worst hold's stage breakdown.

The land's `stages["escalated"]` is a **string**, not a duration, and that is load-bearing. `stages` is the decomposition of what happened *inside* the hold, and every reader — FL7's evidence line, the hold-line printer, the analysis this item was minted from — treats its numeric values as parts of `lock_seconds`. The escalation is no longer one of those parts, so recording its duration there would put time in the hold the hold did not contain. The integrate's own receipt carries its own hold; this key exists only so a land receipt still says the escalation happened.

## Alternatives considered

**A deadline on the held section, with a cooperative yield.** The acceptance's literal wording, and rejected on mechanism. After D1 the section contains a ref re-read, a 60-millisecond CAS, and then publication; there is no slow stage *before* the first write for a deadline to guard, and after the CAS a yield is precisely the half-published trunk ADR-0124 refused. A timer there would either never fire or fire where it must not act — decoration in the first case, a hazard in the second. Placement bounds the same thing without a threshold to tune.

**Leave the standalone verb alone and fix only the nested path.** The nested path is what the 42.5 % measures, so this would have satisfied the number. Rejected: the verb is *the designated recovery route* since WI-0356 — a land that finds the trunk diverged now refuses before taking the gate and tells the operator to run `integrate` — so it is on the critical path of every divergence recovery, and it is the one whose cost nothing measures at all.

**Route the `behind` branch through `_serialized_advance` as well**, retiring `_ff_trunk_to_origin`'s own CAS. Tidier, and one advance helper instead of two. Rejected as out of proportion: WI-0356 extracted that function so the pre-land check and the integrate would share one implementation of "origin is ahead and we hold nothing of our own", and inlining a second CAS beside it re-opens exactly the duplication ADR-0117 D4 is about. The branch carries no gate run, so it is not this item's cost.

**Change `standard_check`'s `d_trunk_integrate` predicate.** It counts non-definition call sites of `_integrate_trunk_with_remote(` and requires at least two; consolidating the two escalation blocks into one helper leaves exactly two, so it passes — at the threshold. Rejected here: it is fleet substrate, every member is measured against it, and tightening it is a change with its own blast radius that no measurement in this item justifies. Recorded as a finding instead.

**Report the 51.4 % as reproducing.** It does not, and the honest number is lower. Rejected for the obvious reason, and recorded in full because a share that falls for the wrong reason is worse than one that rises: it reads as progress.

## Consequences

- **A land whose push is rejected stops costing every queued lane a second full suite.** The escalation still runs, still publishes, still never fails the land — with the gate free.
- **The operator-invoked `integrate` becomes visible to FL7 for the first time.** It was never in the measurement; it is now, under its own verb, with its stages broken out.
- **The intermediate `LANDED, PUSH OWED` window widens slightly** — the gate is released before the escalation rather than after it. Nothing new can happen in that window that could not happen before: a sibling land that takes the gate there is the ordinary case the integrate's five-case classification and its CAS budget already handle, and it is the same window the standalone verb has always run in.
- **A fourth caller can no longer be added with the old shape by accident.** The guard is over the class, not the three instances.
- **The recompile is now the largest single stage inside the hold** — 199.955 s, 23.9 % of total hold, and the one remaining breach. That is ADR-0124 D1's deferred main-checkout-mutex item, whose stated reopening condition is *"when recompile plus sync exceed integrate's share of hold in a 14-day window"*. With the integrate's share heading to zero, **that condition is now met by construction**, which is a finding this ADR hands forward rather than acts on: the threshold was written to compare two live costs, and removing one of them satisfies it without anything about the recompile having changed. **Finding for review**, with the number that should actually reopen it restated in WI-0378's notes.
- **`d_trunk_integrate` now sits exactly at its threshold of two call sites.** It passes, and it would silently go BEHIND — a green suite, a degraded conformance row — if a future change inlined either one. **Finding for review.**

## References

- [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) — the verb; D5's inert-refusal rule is what makes the outside-the-gate half safe.
- [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) — D6 re-entrancy, D7 a rebase is always re-gated.
- [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) — the section this completes; its two findings and the refusal overturned in D2.
- [ADR-0134](0134-a-land-publishes-or-says-it-did-not.md) — D2/D3 made `LANDED, PUSH OWED` a named state, which is the premise D2 turns on; D4 is the bound D3 copies.
- WI-0378 (this), WI-0329, WI-0326, WI-0355, WI-0356, WI-0143, WI-0267, WI-0349.
