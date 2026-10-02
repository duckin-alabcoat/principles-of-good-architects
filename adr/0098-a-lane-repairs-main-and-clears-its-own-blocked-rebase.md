# ADR-0098: A lane repairs the main checkout under proof, and clears its own blocked rebase under a named policy

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** Federation Architect (the ancestor-blob predicate in place of the specified subset test, the bounded conflict class, the banner rewrite, the ROADMAP exclusion). Under the operator's standing rulings that no session runs in the main checkout (2026-08-15) and that POGA never asks the operator to perform a git or POGA operation (2026-08-19).
**Extends:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (the CAS land and the generated-views rule), [ADR-0091](0091-the-harness-commits-what-the-harness-writes.md) (D3/D4 — harness records are the harness's job, warnings name only authored work), [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) (the same predicate-not-exception rule, applied to the remote).
**Work items:** WI-0097 (found ~125, reopened ~145) and WI-0109 (found ~129) — closed together because they are one problem wearing two costumes. Related: WI-0089 (the ROADMAP two-writer defect that produced the first instance), WI-0099 and WI-0108 (still open, same family).

## Context

Three items were opened over four sessions describing the same shape: **a lane can do
everything except the last mile that touches main.** [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md)
closed the remote third. These are the other two.

**WI-0097 — the authorised repair that went back to the operator's shell.** main's working
`ROADMAP.md` was STALE, missing prose a concurrent lane had landed, so committing it
verbatim would have *reverted* that prose. The `wi-render` guard caught it and refused:
working as designed. the operator then explicitly authorised the repair — `git -C <main> restore
ROADMAP.md`, a discard of strictly-stale content since HEAD is newer — and the lane could
not run it. The isolation guard refuses `git -C` at the shared checkout; `Edit` refuses
shared-checkout paths. **Both guards are right.** A python subprocess or a raw file write
would have accomplished it and would have been the same bypass wearing a different hat, so
the authorised repair went to a human terminal — the one outcome that session existed to
eliminate.

Reopened at ~145 because it had been marked done and the capability did not exist:
`main-sync` (ADR-0091 D3) shipped the *harness-written* half and prints `left alone
(authored)` for exactly the dirt this item is about. The authored half — the whole item —
was never built.

**And the banner made it worse.** With no live session in main it printed *"open one in the
main checkout to resolve them"* — an instruction addressed to a session that **by standing
policy never exists**. the operator, ~145, restated the policy: no session runs in the main checkout.
That is the WI-0122 root cause again (a recovery step addressed to someone who is not
there), and a nag whose only remedy is a place no session goes trains the reader to skim the
whole block.

**WI-0109 — the blocked rebase with no next verb.** `merge` rebases, conflicts, aborts
cleanly and reports BLOCKED. Correct: it never leaves a half-state. But the output and the
banner both said *"resolve it in this lane"*, which means opening a session in that lane —
the manual step the lane substrate exists to avoid, and what left poga-2 unmerged after
poga-1 and poga-3 landed.

## Decision

**D1 — `session.py main-restore` (`poga main-restore`), the lane-runnable repair.** Reads and
writes the main checkout through the same substrate contract the land path already uses
(`_main_checkout_state`, `_sync_main_checkout`). This is not a bypass of the guards: what
they exist to prevent is a *hand-run* git command against a tree the session does not own,
and the rule this substrate has now applied three times is that a correctly-denied hand-run
becomes a verb whose **predicate** carries the safety argument.

**D2 — the predicate is ancestor-blob identity, NOT the strict-subset test WI-0097
specified, and the change is load-bearing.** The item proposed restoring where the working
copy is a strict subset of HEAD — *"no content present in the working copy that HEAD
lacks"*. That is computable, and it has a false positive with teeth: **a human deliberately
deleting a paragraph also produces a diff with no added lines.** Under the specified
predicate, discarding it is "provably safe" and destroys real work.

What is asked instead is whether the working copy is **byte-identical to some ancestor
commit's version of that path** — provably content the trunk has already superseded, still
reachable in history, so the discard cannot lose anything even in principle. It answers YES
for the stale-materialization case the item is about and NO for a human's edit, which
matches no commit. Strictly stronger, and it is pinned by a test that fails against the
specified predicate: `test_a_pure_deletion_of_committed_lines_is_still_kept` restores and
destroys the edit when the weak predicate is substituted.
[P9](../principles/master.md#p9--destructive-ops-confirmed) is discharged structurally
rather than by an operator's judgement in the moment.

**D3 — untracked is never restorable, and a live session in main refuses everything.** There
is nothing in history to restore an untracked file *to*, so the only "repair" would be
deletion. And no per-path proof can distinguish "stale" from "being edited right now", so
the whole verb declines while a session is live there.

**D4 — everything is reported, and `--show` prints what was kept.** A path is either
restored *with the commit that proves it* or kept *with what it holds*. A repair that
silently skipped what it could not prove would be the same wrong-answer-shaped-like-a-right-one
the proof exists to prevent. `--show` prints the kept file's diff, bounded, with the
elision named — so even the judgement call is made **from the lane**, which is the half that
keeps this from merely relocating the trip to the main checkout.

**D5 — the banner names the verb, never a place.** `main:` now points at `session.py
main-restore --dry-run`. This is the specific ask WI-0097 added on reopening, and it
generalizes: an instruction addressed to a session that does not exist is not advice, it is
noise that costs the reader's attention for every real signal in the same block.

**D6 — `--resolve <policy>` on `merge` / `end`, defaulting to `generated`.** A stopped
rebase whose conflict is confined to this member's **generated views** is resolved by taking
the **trunk** side. The safety argument is not that those files are unimportant — it is that
they are *derived*: `_recompile_main_checkout` regenerates both from the journals at the end
of the very land this unblocks, so the side taken here is overwritten before anyone reads
it. Identical to [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) D4, one
level down. `--resolve none` declines the policy; an unknown policy is refused, never
silently treated as off.

**D7 — `ROADMAP.md` is deliberately NOT in the class**, though WI-0109 gestures at
"generated-or-regenerable". It is authored prose with one writer per lane, and taking the
trunk side would silently drop a lane's roadmap edit. *"The lane's prose is durable in its
journal"* is true of the narrative and not of the roadmap entry itself. A policy that
quietly discards authored text is precisely what a bounded policy exists to prevent.
WI-0108 is the structural fix for that collision.

**D8 — outside the class, the refusal names the conflicting paths and a verb.** `poga resume
<lane>` re-enters the lane with a session — the honest answer, because a genuine conflict
between two authored edits needs a mind and no policy should pretend otherwise. What
changed is that the reader is given something to run instead of a description of a place to
be.

**D9 — the resolution loop is bounded** (`REBASE_RESOLVE_MAX_STEPS = 50`) and refuses if a
rebase is still in progress when the policy has finished
([P19](../principles/master.md#p19--cap-what-can-run-away)). A lane with more conflicted
commits than that has a problem no policy should be papering over.

## Alternatives Considered

**Implement WI-0097's strict-subset predicate as specified.** Rejected on the evidence: it
green-lights discarding a human's deliberate deletion. Following a written spec into a data
loss is not fidelity. The item's own framing — *"a discard of strictly-stale content, since
HEAD is newer"* — is satisfied more precisely by ancestor identity, which is what "stale"
actually means.

**Have `main-restore` also commit genuinely-authored dirt from main onto the trunk.** It
would empty the banner completely. Rejected: a lane committing another author's in-progress
work under its own name is exactly what ADR-0091 D3 excluded authored files for, and the
lane cannot know whether that work is finished. `--show` gives the reader what they need to
decide without that guess.

**Fold `main-restore` into `main-sync`.** One verb, fewer names. Rejected: `main-sync`
*commits* harness records and `main-restore` *discards* stale authored content — opposite
directions on different files. Two operations under one verb is how a caller ends up
invoking the one they did not mean.

**Auto-resolve any conflict by preferring the trunk.** The version that makes every land
succeed. Rejected outright — it is a silent data-loss machine, and the entire value of D6
is that the class is *named and provable*.

**Leave the rebase dead end and improve the message.** Rejected for the same reason
ADR-0097 rejected it: from a lane whose session is over there is no sequence of commands
the message could name that anyone is there to run.

## Consequences

**Two of the three "last mile" items are now closed** (this pair) plus the remote third
(ADR-0097). The lane can repair main's tree under proof, clear its own bounded conflict
class, and publish past a peer's push — none of it reaching a human terminal.

**The banner is now actionable for the first time.** Every branch of the `main:` block names
either a verb to run or a reason nothing is owed.

**What still reaches a person, deliberately.** A genuine conflict between two authored edits,
and authored dirt in main that holds real content. Both are decisions, not chores. The
verbs make the decision cheap — `--show` from the lane, `poga resume` to re-enter — but they
do not pretend to make it mechanical.

**WI-0099 remains open** (a lane's own litter cannot be cleared from inside the lane) and is
the last member of this family; **WI-0108** (ROADMAP as a per-lane edit) is the structural
fix D7 defers to.

**Not yet redistributed.** Both verbs live in `session.py` + `poga`, byte-identical fleet
substrate, so they ship with the next harness push. As with ADR-0097, `standard-source.md`
is untouched: the standard section is a fleet rollout with its own gate, and it belongs with
WI-0055's pass.
