# ADR-0097: A lane integrates the trunk with its remote, instead of asking a human to push

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** Federation Architect (the replay-in-a-scratch-worktree mechanism, the generated-view skip, the park-on-refusal, the scope boundary). Under the operator's standing ruling that POGA never asks the operator to perform a git or POGA operation (2026-08-19, the third restatement of the 2026-08-04 ruling).
**Extends:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (C3 — the loser rebases), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (D4 — gate in a throwaway worktree), [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (D2 — the CAS land, and the rule that generated views never ride a rebase).
**Work items:** WI-0143 (this job). Same family as WI-0097 (no verb to resync the main checkout's working tree) and WI-0109 (a lane blocked on a rebase conflict) — *a lane can do everything except the last mile that touches main.* This is the remote half.

## Context

Hit live at session ~157/158, at the land, with the work already gate-green.

`session.py end` landed the lane: gate ok on all four checks, main checkout fast-forwarded,
views recompiled — and then reported `(local — push owed)`. The push that followed was
**rejected**: while the lane was landing, a peer session pushed its own journal. Local
`main` and `origin/main` had genuinely diverged — origin had their one commit, local had
sixteen, both descending from the same parent.

**From a lane there was no path at all.** A fast-forward push requires `origin/<trunk>` to
be an *ancestor* of local `<trunk>`, which requires replaying our commits on top of theirs —
i.e. operating on the trunk. The trunk is checked out in the **main checkout**, and a
worktree-isolated session's guard correctly refuses `git -C` into it. Cherry-picking their
commit onto our side does not help: a copy has a new sha, so origin is still not an
ancestor and the push is still non-ff. The only two ff-clean resolutions were
rebase-mine-onto-theirs (needs the trunk checked out) and force push (forbidden,
[P9](../principles/master.md#p9--destructive-ops-confirmed)).

The existing verbs were checked before concluding anything was missing: `merge` lands a lane
onto the *local* trunk, `main-sync` sweeps harness-written records into the main checkout,
`recover-lanes` lands stranded lanes. **None of the three reconciles with the remote.**

So the land's own advice — "run `git push`" — was an instruction to a human that could not
succeed from where the work was done. That is precisely the escalation the 2026-08-19
consultant brief says must become structurally impossible, and by
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
the discipline-level answer for it is spent.

Note what was **not** wrong: the land itself. Nothing was lost or corrupted. The gap was
purely that the final publish could not be completed from the surface all work now happens
on.

## Decision

**D1 — a new verb, `session.py integrate` (`poga integrate`), and the fix is a predicate,
not an exception to the guard.** The isolation guard denied a genuinely unsafe operation and
was right to. The remedy for a correctly-denied operation is a verb whose *preconditions*
carry the safety argument, never a hole punched in the denial. `integrate` never checks out
the trunk and never reaches into the main checkout to run git there.

**D2 — the replay happens in a throwaway detached worktree.** Exactly the primitive
[ADR-0058](0058-land-per-session-with-an-isolated-gate.md) D4 already uses to gate a candidate:
nothing is checked out that another worktree holds, and neither `refs/heads/<trunk>` nor the
lane branch is touched. The function only *produces* a candidate commit; whether it becomes
the trunk is a separate, gated decision. It reuses `GATE_WORKTREE_PREFIX`, so the existing
residue reaper covers it for free rather than needing a second sweeper
([P16](../principles/master.md#p16--avoid-duplication)).

**D3 — the commit list is `--cherry-pick --right-only`, not a plain range.** This lists
commits reachable from local and not from origin *minus any whose patch is already
upstream*, so a hand-made merge or an earlier integrate never causes the same work to be
replayed twice. Merges are flattened, which is what a CAS trunk expects — that trunk is
linear by construction.

**D4 — a conflict on the GENERATED VIEWS drops the commit; any other conflict refuses.**
This is [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) D2's rule —
*generated views never ride a rebase* — applied one level up, and it is load-bearing rather
than tidy. Every lane land ends with `_recompile_main_checkout` committing `session-handoff.md`
and `STATUS.md` **straight onto the trunk**. Two machines that both landed therefore hold two
commits rewriting the same generated files, so a naive replay conflicts on essentially every
real integrate. Dropping ours is an argued no-op, not a guess at whose content wins: the
integrate regenerates both files from the journals seconds later. The set of "generated" names
is read from the same config keys `_recompile_main_checkout` commits, so the two cannot drift.

**D5 — every refusal is inert, and says so.** A conflict on an authored file, a failed gate,
a number collision, an offline fetch: the local trunk ref is left **exactly** where it was
and origin is untouched. The failure mode is "not yet published", never "lost" and never
"half-integrated".

**D6 — a refusal parks the trunk tip on origin, additively.** `refs/heads/parked/<trunk>-<sha>`
— the move session ~157 made by hand so the work was not stranded on one disk, now automatic.
Safe unasked because it is purely additive and named after the commit it carries: a repeat is
a no-op rather than an overwrite, nothing existing is touched, and it never forces.
**Retired by `session.py retire-parked`** (WI-0141, 2026-09-12), which deletes the ref local
and on origin only where every commit on it is a trunk ancestor or already content-equivalent
there. Recorded here because a decision that creates a permanent artifact and names no end for
it is how the ref became the stale signal it was parked to avoid: the two live instances were
both authorised for deletion by hand and both correctly denied by the P9 guard, because the
safety argument lived in a human's assertion instead of in a predicate.

**D7 — the counter land gate runs again, against ORIGIN's tree.** A `WI-`/ADR number that
collides only once the two trunks are combined is invisible to either land in isolation, and
this is the first and only moment it can be caught before publication. It cannot false-block:
a number whose file is already on the local trunk is accounted for by the file scan, so no
sibling can newly reserve it.

**D8 — the land does this for itself.** When `_land_worktree_lane`'s push is rejected it runs
the integrate rather than printing "run `git push`". Fail-open and never silent: the land
already succeeded, and the publish step must not be able to turn it into a failure. The
standalone verb remains for the retry cases — a land that was offline at the time, an
interrupted close, a `--no-merge` deferral landed later.

**D9 — five cases, kept distinct.** No remote / no remote trunk; already equal; behind
(fast-forward the ref, bring the checkout along, push nothing); ahead (the push is already a
ff); diverged (the case this verb exists for). Collapsing any two of them is how "we did not
publish" gets reported as "we published". The exit code carries the same split: 0 integrated
or nothing to do, 1 refused with the trunk untouched, 2 the trunk kept moving under us.

## Alternatives Considered

**Force push.** The one operation that makes the divergence go away in a single command,
and it discards whatever the peer pushed. Forbidden by
[P9](../principles/master.md#p9--destructive-ops-confirmed), and the failure is silent from
the pusher's side — you find out when the other machine's work is missing, not when you run
it. Rejected outright; `integrate` never passes `--force` to anything.

**Cherry-pick origin's commit onto our side.** The intuitive move, and it does not work:
the copy has a new sha, so `origin/<trunk>` is *still* not an ancestor of local and the push
is still non-ff. It also duplicates the peer's commit in the history. Rejected on mechanism,
not taste.

**Punch a hole in the isolation guard so a lane can `git -C` into the main checkout and
rebase the trunk there.** This is the shape that looks like the smallest change, and it is
the worst one: the guard denied a genuinely unsafe operation, and an exception makes every
future denial negotiable. The rule this ADR follows instead — the fix for a correctly-denied
operation is a verb whose predicate carries the safety argument — is the same one
[ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) applied to the land
itself. Rejected.

**Leave it manual and just print a better message.** The status quo, improved. Rejected
because there *is* no message that works: from a lane, no sequence of git commands the
operator could run resolves it either, so a better message is a better-worded dead end. It
is also exactly the escalation the 2026-08-19 brief names.

**A `git pull --rebase` in the main checkout, run by the lane.** Needs the trunk's working
tree, which may hold another session's in-flight edits — the same reason
[ADR-0058](0058-land-per-session-with-an-isolated-gate.md) D4 gates in a scratch worktree
rather than in the shared tree. Rejected for the identical argument.

## Consequences

**A lane can now finish the last mile.** Work done in a lane reaches origin even when a peer
pushed during the land, with no human at a terminal and no force push. The one remaining
"run `git push`" on the lane path is gone.

**The likeliest divergence is now the cheap one.** Because the recompile commit is what
diverges, D4 turns what would have been a refusal on nearly every real integrate into a
silent, correct drop-and-regenerate.

**A refusal is still a refusal, and that is the point.** Two machines editing the same
authored file is a genuine conflict with no mechanical answer; inventing one would overwrite
real work. What changed is that the refusal is inert, names the commit and the paths, and
leaves the work parked off-machine — a state someone can act on, rather than a lane holding
the only copy.

**Scope, stated rather than implied.** This is the **lane** half, matching what WI-0143
reports. From the main checkout the trunk is checked out, and `_land_branch` already rebases
onto `origin/<trunk>` before merging — so that path has an integration step and does not need
a second, differently-argued one. A main-checkout tree that diverges *without* a land in
flight is not addressed here; it would need its own tree-materialization argument (our own
live session in that tree defeats `_sync_main_checkout`'s live-session precondition), and
inventing one to look complete would be the wrong kind of thorough.

**Not yet redistributed.** `integrate` is in `session.py` and `poga` — byte-identical fleet
substrate — so it *ships* with the next push. It is deliberately **not** written into
`standard-source.md` this session: the standard section is a fleet-wide rollout with its own
gate, and no lane operator needs a new instruction, because D8 means the land runs the verb
itself. Adding it to the standard belongs with WI-0055's redistribution pass.
