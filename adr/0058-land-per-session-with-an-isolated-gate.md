# ADR-0058: Land per session with an isolated gate

**Status:** Accepted
**Date:** 2026-07-19
**Supersedes:** [ADR-0057](0057-same-tree-concurrency-attributed-commits.md) (D2 and D4; D1 is retained unchanged)
**Deciders:** the operator (found the defect by interrogating the design, session 78); Federation Architect

## Context

[ADR-0057](0057-same-tree-concurrency-attributed-commits.md), decided and built earlier
this same session, fixed a real corruption bug: two sessions in one working tree shared a
branch, and the first to close swept the other's in-flight files into its own commit and
then deleted the shared branch out from under the still-live session.

Its fix was **last-one-out landing** — a session lands only when no sibling is still live
in the tree; otherwise it commits its attributed work and defers the merge to whoever is
last out.

the operator broke that design with three questions:

1. *"If someone else is still going, does the test suite still run?"* — **No.** The gate
   runs only inside the land path, and a deferring session returns before reaching it. No
   session's work is verified at its own close; it is gated later, in aggregate, by
   whichever session happens to be last. That session inherits responsibility for
   everyone else's breakage with no signal about whose it is.
2. *"If I have concurrent sessions forever, will anything ever get merged?"* — **No.** If
   at least one session is always live, "last one out" never arrives and nothing ever
   reaches the trunk. Unbounded starvation.
3. *"If I have concurrent sessions for a year, will the final one actually be able to
   merge everything?"* — **No.** One enormous rebase and a single gate run over a year of
   accumulated work; on failure, everything stays blocked with no sensible owner.

And a fourth, about what he would actually see: with the trunk frozen, the **compiled
views** (`session-handoff.md`, `STATUS.md`, `ROADMAP.md`) regenerate on the trunk only
(ADR-0056 D2), so they silently freeze for the entire duration of any concurrency. Work
appears finished on disk, is not landed, and nothing says so.

**The diagnosis.** ADR-0057 conflated two different things. The hazard was narrow —
deleting a *shared* branch, and sweeping another session's files. The fix suppressed
merging entirely, converting a **corruption** bug into a **liveness** bug. Liveness bugs
are quieter and, at the scale the operator asked about, worse.

## Decision

**Sessions do not share a branch, and no session ever waits on another to land.**

### D1 — Attributed commits (retained from ADR-0057, unchanged)

A session commits only the files it is recorded as having written. Exact for
`Write`/`Edit`/`NotebookEdit` via the `PreToolUse` hook; `Bash`-written files fall back to
a close-time sweep of changed paths no live sibling claims. Verified live in session 78.

### D2 — Land immediately, always

At close a session commits its attributed work, gates it, and lands. It never defers and
never waits. Nothing can strand anyone, because sessions no longer share a branch for one
of them to delete.

This makes the gate run at **every** close, keeps the trunk moving at any concurrency
level, and keeps the compiled views current — the three properties last-one-out lost.

### D3 — Build the candidate without touching shared state

Two sessions in one tree share the index and `HEAD`, so neither may be mutated to build a
commit. The session builds its candidate through a **private index file**
(`GIT_INDEX_FILE` pointed at a temp file): read the trunk tip into it, add the attributed
paths, `write-tree`, then `commit-tree` with the trunk tip as parent. The shared index and
the shared `HEAD` are never touched, so concurrent sessions cannot corrupt each other's
staging.

### D4 — Gate the candidate in a scratch worktree

The working tree contains other sessions' in-flight edits, so running the suite *in it*
tests their half-finished work. Instead the candidate commit is checked out into a
throwaway worktree (`git worktree add --detach`), the gate runs **there**, and the
worktree is removed. The gate therefore sees exactly what the trunk would become — no
in-flight noise from anyone.

This uses a worktree for the few seconds of a gate run, not for a session, which is why it
does not carry any of the workflow costs that ruled worktree-per-session out (the operator,
session 78: no per-session workflow, `poga` stays a label, must scale to N).

### D5 — Advance the trunk with a compare-and-swap

On a green gate the trunk ref advances via `git update-ref <ref> <new> <old>` — an atomic
compare-and-swap against the tip the candidate was built on. If another session landed
first the swap fails; the loser rebuilds on the new tip and re-gates rather than
clobbering. This is the ADR-0051 C3 "loser rebases" rule, enforced by the ref update
instead of by a checkout.

## Consequences

- **Every session is gated at its own close**, and a failure names the session that caused
  it instead of pooling into one aggregate run.
- **No starvation at any concurrency level.** Answers the operator's forever/year questions
  directly: each session lands on finishing, so the trunk advances continuously and the
  hundredth simultaneous session is no different from the first.
- **The compiled views stay current**, because the trunk keeps moving — the human-facing
  docs no longer freeze for the duration of concurrent work.
- **A session can still be blocked by a genuine incompatibility**: A lands, B's work turns
  out to conflict with what A landed, B's gate catches it. This is not a broken build (A
  could not land breakage — A was gated too); it is two changes that genuinely conflict,
  which no system prevents. Recovery is the normal rebase-and-retry the land flow already
  performs. This is the merge-queue model, i.e. the *fixed* version of the shared-trunk
  era rather than the problem it solved.
- **Session branches become optional.** A solo session may still branch (ADR-0056) with no
  behavioral difference — it is always alone, so it always lands immediately. The branch
  is no longer load-bearing for isolation; attribution and the isolated gate are.
- **The scratch worktree costs a checkout per close.** Bounded and short-lived; the
  alternative (gating the shared tree) tests other sessions' unfinished work, which is
  worse and non-deterministic.
- **`LAND_DEFER_GRACE_MIN` and `_live_sibling_csids`' land-blocking role retire.** Sibling
  liveness is still read, but only to decide *attribution* (whose files to leave alone),
  never to decide whether to land.
- **ADR-0057 is superseded, not amended.** Its reasoning and the starvation mistake stay
  visible: the failure mode it introduced is more instructive than the bug it fixed, and
  it was caught by a user asking what would happen at scale rather than by the tests.
