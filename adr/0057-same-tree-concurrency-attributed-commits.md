# ADR-0057: Same-tree concurrency — attributed commits and last-one-out landing

**Status:** Superseded by [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (D2/D4; D1 carries forward unchanged)
**Date:** 2026-07-19
**Deciders:** the operator (set the constraints — scale to N sessions, no per-session workflow, launch from the Claude app today and a terminal later, session 78); Federation Architect (design)

> **Historical note (2026-07-19, same session as acceptance):** D2's *last-one-out
> landing* traded a corruption bug for a **liveness** bug and was superseded within the
> hour. the operator broke it with three questions: a deferring session never runs the gate at
> its own close; with at least one session always live, "last one out" never arrives and
> **nothing ever merges**; and a year of deferred work would land as one enormous
> ungovernable rebase. The frozen trunk would also have frozen the compiled views
> (`session-handoff.md`, `STATUS.md`, `ROADMAP.md`), so work would look finished on disk
> while silently never landing. [ADR-0058](0058-land-per-session-with-an-isolated-gate.md)
> replaces the deferral with land-per-session behind an isolated gate. **D1 (attributed
> commits) was correct and carries forward unchanged.** This ADR is retained in full: the
> failure mode it introduced is more instructive than the bug it fixed, and it was caught
> by a user asking what happens at scale — not by its own 16 tests, all of which passed.

## Context

[ADR-0056](0056-session-branch-gated-trunk.md) (concurrency Phase 2 / C3) gave every
session its own branch and a fail-closed gate on the trunk. It was verified against a
**two-tree** model of concurrency: `test_rebases_onto_concurrent_origin_work_then_lands`
simulates the second session through `origin`, i.e. a separate clone or worktree. Session
77's handoff recorded that test as covering "the real two-session case." It covers the
real two-**tree** case. The distinction was never drawn.

A supported configuration — **two Claude app windows opened on the same
folder** — is a different thing, and Phase 2 mishandles it. Two windows share one working
tree, and a working tree has exactly one checked-out branch. `_cut_session_branch` only
cuts from the trunk; a session starting while the tree is already on `session/<A>` takes
the non-trunk path and returns A's branch without checking anything out. Session B
silently adopts A's branch and A's tree.

The resulting sequence, verified by reading the close path:

1. Both sessions edit files in one tree on one branch.
2. Whichever closes first runs `git add -A` — committing the other session's in-flight
   edits under its own close.
3. It gates, ff-merges to the trunk, then `git branch -d session/<A>` — deleting the
   branch the still-open session is working on, leaving that window's tree on the trunk.
4. The second session closes, finds it is not on a session branch, prints "nothing to
   merge," and lands nothing. Its work already shipped under the first session's name and
   was never gated as its own increment.

No work is lost — everything is committed — but neither Phase 2 guarantee (isolation, a
per-session gate) holds. The `session/`-prefixed arm of that branch is untested;
`test_non_trunk_non_session_branch_not_hijacked` covers only the hand-cut-branch arm.

**Constraints the operator set**, which rule out the obvious fixes:

- It must **scale to N** — a hundred simultaneous sessions is unrealistic, but the design
  must not break there. Fixed lanes (a standing `w2`, `w3`) don't.
- **No per-session workflow.** The opening prompt names a session; it cannot become a shell
  command. Encoding a lane in what the operator types (a different word for the second
  session) makes the operator the allocator — the opposite of the goal.
- **Two launch surfaces.** Sessions launch from the desktop app or from a terminal
  launcher. Both must work; the terminal is allowed to be better.

A worktree-per-session design satisfies isolation but fails the constraints: a session
cannot relocate itself (cwd is fixed at launch), so it requires a launcher, which the app
surface has no hook for. It also fragments Claude Code's per-directory identity — memory
lives at a path derived from the project folder, so a unique folder per session scatters
memory and project history across directories.

**Subagents were investigated and are not part of this problem.** Spawning a subagent
leaves the sidecar count unchanged, and the subagent reports the parent's branch.
Subagent tool calls mint no session state — no journal, no ordinal, no
branch cut. A subagent runs inside its parent's tree on its parent's branch, and its work
is the parent's work, landing with the parent's close as one correctly-attributed
increment. The residual risk (two subagents writing one file concurrently) is a
within-session briefing concern — disjoint file sets, or the Agent tool's
`isolation: "worktree"` — not a harness concern.

## Decision

Isolate at **commit time** rather than by directory, and decide landing at **close**
rather than at start.

### D1 — Attributed commits

Each session records the files it touches as it works (the `PreToolUse` hook already sees
every tool call and its input). At close, a session commits **only its attributed paths**
instead of `git add -A`.

Attribution is exact for `Write` / `Edit` (the tool input carries `file_path`) and
heuristic for `Bash`, which can write files the harness cannot see from the call (a
script regenerating `CANON.md`). The fallback for unattributed changes is: commit what
changed since session start **minus** paths another live session has touched. This is
deliberately good-not-perfect; the claim mechanism (C4) is what keeps sessions on
disjoint work items, which keeps them on disjoint files, which keeps attribution
unambiguous in practice.

### D2 — Last-one-out landing

At close, a session asks one question: **is another session still live in this tree?**

- **No** → land normally (branch, gate, ff-merge, delete). This is the existing Phase 2
  path, unchanged, and it is the path every solo session takes.
- **Yes** → commit attributed files, close the journal, and **do not land**. Surface that
  the land is deferred. The last session out of the tree runs the gate and lands the
  accumulated work.

Deciding at close rather than at start is the load-bearing choice. A session that decides
at start has no good move when it is joined mid-flight: it is already on a branch, and
falling back would move the tree under a live sibling. At close the facts are known and
the decision is total.

This removes the branch-deleted-under-a-live-session path by construction: landing only
happens when there is no one left to strand.

### D3 — Shared coordination state at the git common dir — DEFERRED

Session state should eventually move from `ROOT/.session-state` to the directory reported
by `git rev-parse --git-common-dir`, so that sessions in different worktree lanes can see
each other's liveness and claims (in a plain checkout it resolves to `.git`; in a worktree,
to the *shared* `.git`).

**Not built here.** The original framing — "one line now versus a migration later" — was
wrong: on the order of a hundred existing sidecars must be migrated whenever it happens, so nothing is saved by
doing it early. Doing it now would also mean writing worktree-visibility code with no
worktree to exercise it against, while simultaneously rewriting the close path that
liveness depends on. It lands with the launcher (D4), when it can actually be tested
against real lanes.

Consequence: until then, sessions in *different* trees cannot see each other's liveness,
so D2's "another session live in this tree" question is answered per-tree only. That is
exactly the scope of the bug being fixed, so nothing regresses.

### D4 — The launcher is not a mode

A terminal launcher, when it exists, allocates each session its own worktree lane from a
**reusable pool** — a session takes the lowest free lane and returns it at end, so the
folder count is the high-water mark of *simultaneous* sessions, not of sessions ever run.

It requires no new harness code. A session alone in its lane is always the last one out,
so it always takes D2's immediate-land path. The launcher does not add a mode; it creates
the condition that already earns the strong guarantee. App sessions get safe deferred
landing; terminal sessions get exact isolation and immediate landing.

## Consequences

- **The corruption path is closed** for two windows on one folder, without any
  change to how the operator opens or labels a session.
- **Solo sessions are unaffected.** The common case still branches, gates, and lands
  immediately — no regression to the Phase 2 guarantee.
- **Two-up trades immediacy for safety.** The first session to close does not see its work
  on the trunk until the second closes. The work is committed and safe throughout; only
  the merge waits.
- **Sessions sharing a tree see each other's in-flight edits**, so a broken edit by one can
  fail another's gate. This is shared-checkout reality — coupling, not corruption — and it
  is the accepted cost of not moving to per-session directories.
- **Attribution is heuristic at the `Bash` boundary.** A session that generates files
  through a script relies on the since-start fallback. Claims (C4) reduce this from a
  correctness risk to a tidiness one.
- **Phase 2's tests encoded a two-tree model.** The same-tree arm needs its own coverage;
  the misread ("the real two-session case") is corrected in this ADR's Context so the
  record does not carry it forward.
- **Claims (C4) are unblocked and become load-bearing** rather than advisory: they are now
  the mechanism that keeps concurrent sessions on disjoint files, not merely disjoint
  intentions.
