# ADR-0066: A lane land owns the checkout it strands, and the reaper is not gated by surface

**Status:** Accepted
**Date:** 2026-07-23 (session 90)
**Builds on:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (terminal worktree lanes), [ADR-0059](0059-same-tree-concurrency-fails-safe.md) (detect-and-refuse), [ADR-0055](0055-lazy-session-start.md) (lazy start)
**Deciders:** Federation Architect (design + build); external consultant (findings F1/F2, brief `2026-07-22-lane-landing-checkout-and-reaper-gaps`); the operator (directed F1 first)
**Reality:** Built

## Context

ADR-0060 made a lane land cheap and lockless: rebase the lane onto the trunk tip, gate it,
then advance `refs/heads/<trunk>` by compare-and-swap with **no checkout** of either branch.
Not checking anything out is the whole point — it is what lets N lanes land independently
without contending on a working tree.

But the main checkout has that ref as its `HEAD`. Advancing the ref underneath it leaves its
index and working tree at the pre-advance commit while `HEAD` resolves to the new tip. Two
consequences, one cosmetic and one not:

- **Cosmetic but dangerous to a human.** `git status` reports the entire delta as a *staged
  deletion*. After session 89 the main checkout showed ~2,032 lines apparently staged for
  removal — `adr/0062`, `adr/0063`, the C4/C5 test files, 748 lines of `session.py` — which
  reads exactly like a deliberate revert of the night's work. The natural cleanup instincts
  diverge sharply: `git checkout .` is safe, `git stash` and `git commit` are not.
- **Not cosmetic at all.** Anything running *from* that tree operates on the stale content.
  The adopt-runner LaunchAgent sets `WorkingDirectory` to the main checkout, and
  `push-substrate.py` reads `session.py` from the working tree. A push from a stranded tree
  would ship the pre-C4/C5 harness fleet-wide as byte-identical substrate — and report
  success, because the old harness is perfectly green against the old tests.

The second consequence nearly fired. The checkout sat 5 commits behind for hours
with the scheduled sweep armed against it. It did not fire, for a reason worth recording: the
sweep found **zero eligible briefs**, so it never opened a headless session and never reached
`push-substrate`. The safety margin was luck, not design.

A separate finding in the same brief: the lane reaper had stopped reaping. `worktree-poga-3`
sat merged and reapable for a full day while the sweep "ran" at every session start.

## Decision

### D1 — The land path syncs the checkout it strands, or says why it didn't

`_land_worktree_lane` calls `_sync_main_checkout(trunk, parent, tip)` after a successful CAS.
It fast-forwards the main checkout **only** when that checkout provably holds nothing of its
own, and otherwise touches nothing and writes a banner naming the divergence.

The land path owns this because the land path caused it. No other actor knows a strand
happened at the moment it happens.

**The precondition set is the whole decision**, and the obvious formulation is wrong:

> `git status --porcelain` is empty

fails on precisely the case it exists for. In the stranded state `HEAD` already resolves
through the advanced ref while the tree sits at the old commit, so status is *loud* — it
reports the whole delta as staged. A clean-tree gate would never fire. `rev-parse HEAD` is
useless for the same reason: it reports the new tip, not what the tree holds.

The honest witnesses compare the index and the tree against `parent`, the tip we CAS'd away
from:

1. `git diff --cached --quiet <parent>` — index == parent (nothing staged of its own)
2. `git diff --quiet` — tree == index (nothing modified of its own)
3. `git ls-files --others --exclude-standard` empty — nothing new of its own

Together these prove the checkout is byte-for-byte the pre-advance commit, so resetting it to
`tip` can destroy nothing. Additionally the sync defers if a session is **provably live** in
that tree (fresh heartbeat, no exit marker) — a tree someone is working in is not ours to
rewrite even when it is clean.

Any failing condition means real content: touch nothing, write `.session-state/stale-checkout.txt`
naming the reason and the manual fix. Never guess. The banner is cleared on a successful sync
so a stale warning cannot outlive the condition it described.

**On [P13](../principles/master.md#p13--single-writer-per-state).** This is a cross-tree
write, which deserves an explicit answer rather than a shrug. The land path is already the
single writer of the trunk ref, and a checkout *materializes* that ref rather than being
independent state — so syncing it is the same writer finishing its own write. The
fast-forward-only precondition is exactly what keeps that true: the moment the tree holds
anything of its own, it **is** independent state, and we stop.

### D2 — The substrate push refuses bytes the trunk does not carry

`push-substrate.py` gains a source-integrity gate: refuse to ship if `FED_ROOT`'s working copy
of any `BYTE_IDENTICAL` file differs from the trunk tip's blob.

Green tests prove the harness in the working tree *works*; only this proves it is the harness
the trunk *blessed*. One `git diff <trunk> -- <files>` catches both directions of drift — tree
behind the ref (a stranded checkout) and tree ahead of it (uncommitted local edits shipping
before they are committed or reviewed) — because they are the same defect: the pushed bytes
are not the blessed bytes.

This gate **fails closed**, unlike most guards in this substrate, which fail open so they can
never brick the thing they guard. A push is a fleet-wide irreversible write, so an
*undeterminable* comparison must block rather than proceed. A guard that fails open when git
breaks is a guard that is absent on exactly the day it matters.

D2 is deliberately redundant with D1. D1 prevents the stranding; D2 makes the worst
consequence of a stranding structurally impossible regardless of checkout hygiene — including
strandings from causes not yet imagined.

### D3 — The reaper is gated by lane age, not by which surface is sweeping

`_auto_reap_lanes` returned early unless it ran from the main checkout. That gate covered a
real hazard: a just-born sibling lane has not taken its lock yet, and its branch — cut seconds
ago from the trunk — is trivially merged, so it classifies `reap`. A naive sweep would delete
a session that is still starting up.

But [ADR-0059](0059-same-tree-concurrency-fails-safe.md)'s detect-and-refuse steers every
session into a `poga` lane. The gate therefore restricted sweeping to the one surface the
system actively tells people not to use, and the reaper became dead code the day that landed.
The consultant read F2 as a classification miss; it was not — `worktree-poga-3` classified
correctly and always did. Nothing was reaching the classifier.

The birth race is now handled where it belongs. `LANE_REAP_MIN_AGE_MIN` (60 min) keeps the
sweep off any lane active within the hour. Age is a property of the *lane*, not of whoever is
sweeping, so the guard holds from every surface. An active lane keeps touching its own
directory and reads young; an abandoned one stops and ages into reapability. Unknown age
reads as young — refuse to reap what cannot be proven stale.

> **Amended 2026-09-05 (WI-0257).** Two claims in the paragraph above were false as written,
> and the second is what made the first false.
>
> The exemption this decision granted — *"the loud `reap-lanes` command is exempt: a human
> asking for the sweep has already looked"* — is **withdrawn**. Its premise does not hold:
> nothing shows a lane's birth time (there is deliberately no read-only lane-inspect verb),
> so the human has not looked at the one property the exemption turns on; and `poga` spawns
> lanes concurrently, so sweeping while a dispatcher is spawning is not a coincidence, it is
> the race itself.
>
> And *"the guard holds from every surface"* was **aspirational, not implemented**, for the
> four months this ADR has stood. The age term lived in `_auto_reap_lanes`'s own filter;
> `_scan_lanes` merely RECORDED `age_min`. So the loud path — and `discard-phantom-lanes` —
> inherited none of it, while this ADR and the code's own docstrings asserted the opposite.
> A lane in its birth window is the worst case to get wrong: the directory exists, the branch
> is freshly cut so trivially merged, the tree is clean because nothing has run, and the child
> has not locked yet. Every predicate answers "safe" about a lane that is still being born.
>
> As of WI-0257 the age term IS in the classification: `_scan_lanes` returns a distinct
> `young` status, so every consumer inherits it structurally and `_auto_reap_lanes` no longer
> carries an age test of its own. Held-back lanes are named in the report with the reason —
> silence would reproduce WI-0082. `reap-lanes --include-young` is the explicit opt-out for an
> operator who actually watched the session die.
>
> One lane sweep still does not classify through `_scan_lanes`: `poga lanes --clean`
> re-derives merged-ness in bash and calls `git branch -d`. It needs no age term. A lane being
> born has a worktree, and git refuses to delete a branch checked out in one — probed
> 2026-09-05, `git branch -d` on a fresh `git worktree add -b` branch exits 1 with *"cannot
> delete branch ... used by worktree at ..."*. `-d` rather than `-D`, so an unmerged branch is
> refused as well.

Concurrent sweeps need no lease. Each git operation is individually atomic, the sweep is
idempotent, a loser merely reports a failure line, and on the automatic path the lines are
discarded.

### D4 — A deferred sync is surfaced at the next start

When D1 declines to sync, the next session start in that checkout surfaces a `STALE:` line
naming the reason and pointing at the banner. Without it the tree merely looks confusingly
dirty — the session-89 signature that reads as a deliberate revert.

## Consequences

- A lane land leaves the main checkout current, or leaves it untouched with a stated reason.
  The silent third option — stranded and unexplained — is gone.
- The catastrophic path (redistributing a stale harness fleet-wide while reporting success)
  is blocked at two independent points.
- Abandoned lanes are reaped from whichever surface is actually in use, an hour after they go
  quiet, instead of never.
- The automatic sweep is now *slower* to reclaim a lane (an hour, versus immediately from the
  main checkout). This is the intended trade: the guard's whole job is to cost a little
  latency in exchange for making the birth race impossible rather than merely unlikely.
- A `reset --hard` now runs unattended in the land path. This is safe only because of the
  precondition set in D1; it is the load-bearing part of this ADR and the part to re-verify
  before anyone relaxes those checks.

## Verification

- `tests/test_worktree_lane.py::MainCheckoutSyncTest` — 7 tests: fast-forward on a clean
  strand, hands-off with banner on untracked / unstaged / staged content, deferral on a live
  session, non-deferral on a cleanly-exited one, no-op from the main checkout, banner cleared
  on success.
- `tests/test_worktree_lane.py::ReapLanesTest` — the sweep runs from inside a lane, spares a
  just-born sibling, and treats unknown age as young. (Since WI-0257 that sibling classifies
  `young`, not `reap`; five further pins cover the loud path, the report line, the
  `--include-young` opt-out, `discard-phantom-lanes`, and the invariant that nothing reaches
  `reap` while in-window. Each was verified to FAIL against the pre-fix behaviour.)
- `tests/test_push_substrate.py::SourceIntegrityGate` — 5 tests: clean tree passes, stranded
  checkout refused, uncommitted edit refused, unrelated in-flight file does not block,
  unresolvable trunk fails closed.
- Suite 550 → 564 green.
- Live: `reap-lanes` correctly classified 6 live lanes as protected, 2 unmerged orphans as
  kept, and reaped `poga-1` + `poga-3`.

The land-path acceptance checks (land with a clean main checkout → auto-syncs; land with a
real local edit there → untouched, banner present, push refuses) belong in
`drills/concurrency-game-day.md`.
