# ADR-0102: `integrate` merges a diverged trunk; it never replays it

**Status:** Accepted
**Date:** 2026-08-23
**Deciders:** Federation Architect (merge-not-replay, parent order, the per-path view policy, the ancestor assertion, the separate standard capability). Under the operator's standing ruling (2026-08-19) that POGA never routes git or POGA operations to the operator.
**Supersedes in part:** [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md). Its D1, D2, D5–D9 stand unchanged. **D3 (the `--cherry-pick --right-only` replay) is superseded outright**, and **D4 (the generated-view class) is retained but re-expressed per PATH rather than per COMMIT**.
**Work items:** WI-0153 (this job), found by the acceptance test WI-0143 asked for and never got.

## Context

ADR-0097 shipped `integrate` and it was exercised against a genuinely stuck Runner at
session ~161. **The easy half held.** Behind fast-forwarded in one step. The refusal path
was well built: on conflict it refused, named the file, left both trunks untouched — read
from the refs, not from the banner — and parked the tip off-machine at
`refs/heads/parked/main-5640258ee5d9`, confirmed present with `ls-remote`.

**The diverged case did not resolve, and could not.** ADR-0097 D3 reconciles by *replaying*
our unpushed trunk commits onto origin's tip — rebase semantics — and that is the wrong
operation for two trunks that both advanced, for two independent reasons:

**It rewrites our side.** Every local commit gets a new sha. The peer's clone, a parked
ref, a lane branched from the trunk — all of them still point at the originals, so they are
now diverged from a trunk they used to be part of. Reconciliation that changes one side's
history is not convergent: the two machines take turns rewriting each other. WI-0143's own
opening note had already identified this trap in a neighbouring form — *cherry-picking their
commit does not help, it produces a copy with a new sha* — and the verb built to close
WI-0143 inherited it in the mirror direction.

**It conflicts far more than a merge does.** A cherry-pick's base is one commit's parent,
not the true merge base. Two sides that each edited a different region of the same file
merge cleanly and replay badly; a commit that touched a file and a later one that reverted
it conflicts on the way through even though the net trees agree.

`session.py merge` cannot cross it either — the land rebases the lane onto the trunk, so it
hits the mirror-image conflict. **Neither verb could produce the one thing that resolves a
genuine divergence: a merge commit on the trunk.**

The incident was resolved by hand, and the shape of that resolution is the fix: build the
merge inside a lane, where a session is allowed to work; resolve the file there; push the
lane tip, which *is* a fast-forward for origin because the lane contains origin as an
ancestor. Every step was sanctioned. It simply was not a verb, so an operator had to invent
it — which is the [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user)
failure in its structural form (RC1: a missing verb), one day after that habit was adopted.

Note what was **not** wrong, again: nothing was lost, and every refusal was inert. The gap
was that the refusal was permanent.

## Decision

**D1 — the diverged case builds a MERGE COMMIT, never a replay.** `_merge_onto(ours,
theirs)` merges `origin/<trunk>` into the local trunk tip. The result has both as parents,
so `origin/<trunk>` is an **ancestor** of it — which is exactly the predicate a fast-forward
push needs — while every commit on either side keeps its sha. This is the operation that
makes reconciliation *convergent* rather than a rewrite war, and it is why ADR-0097 D3 is
superseded rather than tuned.

**D2 — it still runs in a throwaway detached worktree.** ADR-0097 D2 unchanged and
load-bearing: nothing is checked out that another worktree holds, neither
`refs/heads/<trunk>` nor the lane branch is touched, and the function only *produces* a
candidate. Whether it becomes the trunk stays a separate, gated decision. It reuses
`GATE_WORKTREE_PREFIX` so the existing residue reaper covers it
([P16](../principles/master.md#p16--avoid-duplication)).

**D3 — parent order is load-bearing, and is asserted rather than assumed.** Ours is the
first parent. `_checkout_merge_base` and `_main_restore_verdict` both walk `--first-parent`
to find where the main checkout was last materialized, and `_sync_main_checkout` requires
`local → merge` to be a strict fast-forward. Merging the other way round leaves this
machine's history on a side branch and defeats all three **silently** — no error, no amber,
just a checkout-base search that stops finding its answer. Pinned by a test on `main^1` /
`main^2`.

**D4 — the generated-view class survives from ADR-0097 D4, re-expressed per PATH.** The
replay could only drop a whole COMMIT, which discards anything else that commit carried. A
merge resolves the conflicting PATH and keeps the rest. The safety argument is unchanged and
is not "these files are unimportant": they are **derived**, and the integrate regenerates
both from the journals seconds later, so the side taken is overwritten before anyone reads
it. `--ours` in a merge is the side being merged into — our trunk — the same side the land's
rebase policy takes, so the two cannot drift into disagreeing.

**D5 — one policy vocabulary for both reconciliations.** `RESOLVE_POLICIES` is now shared by
`_merge_onto` (the integrate's merge) and `_resolve_rebase_conflict` (the land's rebase), and
`integrate` grows `--resolve` to match `merge`/`end`. `generated` is the default; `none`
refuses on any conflict; an unknown policy is refused, never silently treated as off. The
land passes its own policy through, so one lane has one answer to *which conflicts may be
resolved without a human* rather than two surfaces that can be configured to disagree.

**D6 — the merge asserts the property the verb rests on.** Before returning a candidate,
`_merge_onto` checks `merge-base --is-ancestor theirs tip` and refuses if it does not hold.
Git will not normally violate this, and that is the point: a tip that cannot fast-forward
would otherwise surface as a mystery push rejection minutes later, after the CAS
([`capture-the-probe`](../habits/master.md#capture-the-probe) applied to a postcondition).

**D7 — a refusal is still inert, and its remedy is now true.** The old advice — *land the
conflicting change through a lane, then integrate again* — did not work under a replay: the
same commit was replayed and conflicted identically. Under a merge it works, with a
condition that must be stated or the operator repeats the loop: the reconciled content must
**incorporate origin's side**, because a resolution that merely differs from theirs conflicts
against the same unchanged merge base. The refusal says so.

**D8 — a separate standard capability, `trunk-merge-integrate` (v1.13.0), not a re-cut of
1.11.0.** A replay-only member passes the `trunk-integrate` detector — the verb exists, the
land calls it — and is nonetheless unable to resolve a real divergence. Folding this fix
into that row would report every member GREEN on a capability it does not have, which is
worse than reading BEHIND. [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability):
a version bump that changes what a member *does* needs a marker that changes what the
surface *sees*.

**D9 — `trunk-integrate`'s detector now counts call sites instead of matching a literal.**
It required the exact string `_integrate_trunk_with_remote()` and went ABSENT the moment the
land grew a `resolve=` keyword. The capability had not moved; the marker was pinned to an
argument list. Caught by running the self-check rather than by review, and fixed in the
direction that matters: a detector must test the wiring, not the spelling (WI-0158's family).

## Alternatives Considered

**Keep the replay and add merge only as a fallback when it conflicts.** The smallest
diff, and it preserves a linear trunk in the common case. Rejected: it leaves the rewrite
in place for every *successful* integrate, which is the half that makes two machines
diverge again next time — so the instability WI-0153 reports would survive its own fix.
Two reconciliation paths that can disagree is also exactly the shape D5 exists to prevent.

**Merge, but with origin as the first parent.** Reads more naturally as "our work on top of
theirs". Rejected on mechanism: it puts this machine's history on the second-parent side and
silently defeats the checkout-base walk and the sync precondition. See D3.

**Resolve authored conflicts automatically under some wider policy** — newest wins, longest
wins, union-merge the file. Rejected. Two machines editing the same authored file is a
genuine disagreement with no mechanical answer, and the notes-field incident that triggered
this is the proof: both sides were *correct* appends. A policy that picks writes one of them
away. The bounded named class stays bounded.

**Force push.** Still forbidden ([P9](../principles/master.md#p9--destructive-ops-confirmed)),
still silent from the pusher's side, still never passed to anything.

**Teach the lane land to merge instead of rebase.** Symmetrical-looking and wrong: a lane
branch is owned by one session, so rebasing it is safe and keeps the trunk's own history
readable. The divergence this ADR addresses is between two TRUNKS, which nobody owns
exclusively. Different problem, different operation.

## Consequences

**The trunk is no longer linear by construction, and that claim is retired.** ADR-0097 D3
asserted it as a justification for `--no-merges`; it was true only because the replay
enforced it. A trunk reconciled across two machines carries one merge commit per
reconciliation. The two `--first-parent` consumers are unaffected by D3's parent order, and
the CAS model never depended on linearity — it depends on the ref moving forward, which a
merge does.

**A refusal now converges.** It refuses on strictly fewer inputs (a 3-way merge resolves
what a sequential cherry-pick could not), and the remedy it names actually terminates.

**The acceptance WI-0143 never had is now a test.** Two checkouts with real work on both
sides reconcile with `integrate` alone — nothing rewritten, both sides' content preserved,
and a second run reports "already in sync". Verified against two deliberately broken builds:
the old replay fails the *nothing rewritten* and first-parent assertions, and a
`merge -s ours` that keeps the parents while dropping their content fails the content
assertions. A test that cannot fail is not evidence.

**One class of conflict should not exist at all, and is not fixed here.** The collision that
triggered WI-0153 was not a disagreement: two machines APPENDED to the same work-item notes
field with an empty common base, so git could not order two appends. An append-only field
should not be a merge hotspot — writing notes as separate records would make this class
*impossible* rather than resolvable. Filed as its own item rather than folded in, because it
is a store-shape change, not an integrate change.

**Rollout gap widens by one.** Most of the fleet was already behind at v1.12.0; this makes
it v1.13.0. Shipping the fix without its marker would have closed the gap on paper by
certifying the defect as absent, which is the trade this ADR declines to make.
