# ADR-0056: Session branch → gated trunk (concurrency Phase 2 / C3)

**Status:** Accepted
**Date:** 2026-07-19
**Deciders:** the operator (delegated the design — *"do what you think is best"*, session 77); Federation Architect (design within ADR-0051 C3)

## Context

[ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) retired the
master session and established the multi-session model in phases. Phase 1 (identity +
journals, C1/C2) shipped and is hardened (ADR-0054/0055): every session owns a durable
journal, and `session-handoff.md` / `STATUS.md` are compiled views. **Phase 0** (every
member off a file-sync folder onto a plain git repo) completed fleet-wide session 76.

What Phase 1 did **not** change: every session still commits **straight to `main`** and
pushes (`session.py cmd_end`). Two simultaneous sessions on the same repo are safe only
by luck — the liveness reaper and the fact that they rarely touch the same file. There is
no gate on what lands on `main`: a session that breaks the test suite pushes the breakage
to the trunk the whole fleet pulls from.

C3 is the fix: *"Every session works on a branch cut from a recorded base commit; the
merge to `main` passes the verification gate. Short-lived branches; loser of a conflict
rebases. Doctrine: no held commits, ever."* This ADR is the follow-on ADR-0051 permits
where a sub-part "grows a distinct decision surface" — C3's merge-gate design,
compile-timing, and merge mechanics are that surface.

## Decision

### The key simplifier — a branch inherits the landed narrative

A session branch is cut **from the trunk**, so at birth it already contains **every
landed journal** as committed files. Ordinals, `compile`, and history therefore work on
the branch from the working tree alone — **no cross-branch journal reading is required.**
The only thing a branch's working tree cannot see is a *second, concurrently-running,
not-yet-landed* branch's journal — which is exactly the true-simultaneity case Phase 3
(claims + leases) exists to make safe. Phase 2 targets the common case (branch/gate
discipline, git-native conflict handling for genuine same-file edits) and is correct for
it; the concurrent-unlanded-sibling edge is named and deferred, not silently handled.

### D1 — Branch lifecycle, lazy-aware

Every session works on `session/<session-id>` cut from the trunk. The cut happens at the
**same moment the journal materializes** — the session's first heartbeat
(`_complete_lazy_start`, [ADR-0055](0055-lazy-session-start.md)), never at `SessionStart`
— so a phantom preload (which never heartbeats) never cuts a branch, exactly as it never
writes a journal. The eager (manual) start path cuts immediately.

The cut is best-effort and **fail-open**: if the working tree is on the trunk and clean
enough to branch, cut; on any git error, stay on the trunk (degraded to Phase-1 behavior,
never bricked). A session already on a non-trunk branch (a worktree, a hand-cut branch)
is left where it is — its branch *is* its session branch.

### D2 — Compile on trunk only

The compiled views (`session-handoff.md`, `STATUS.md`) are regenerated-and-committed
**only on the trunk**, never on a session branch. This is what keeps merges
conflict-free: if two branches each committed a regenerated handoff, they would collide
on the *generated* file (the journals themselves never collide — each session owns its
file). `run_compile` is guarded to no-op on a session branch. A branch's views simply lag
until it lands (ADR-0051 already accepts compiled-view lag); the merge regenerates them on
the trunk with the newly-landed journal included.

### D3 — `end` closes on the branch; `merge` lands through the gate

`session.py end` closes **this session's** journal (frontmatter surgery: `ended` +
duration + title) and commits it plus any pending work **to the session branch**
(`git add -A`). It then invokes the **land** flow unless `--no-merge` is given (the escape
hatch for a branch deliberately kept open for review):

1. `git fetch` the trunk (best-effort; offline-tolerant — land locally, push owed).
2. **Rebase** the session branch onto the (fetched) trunk — *loser of a conflict rebases*
   (ADR-0051 C3). A rebase **conflict** aborts (`git rebase --abort`), surfaces
   *"manual rebase needed"*, and leaves the branch with its journal closed and work
   intact — nothing lands. This is the one genuinely-human case.
3. **Gate** (D4). A gate failure surfaces and leaves the branch un-landed — the journal is
   closed and work is committed on the branch; nothing reaches the trunk.
4. `git checkout <trunk>`, fast-forward it to the fetched remote, then
   `git merge --ff-only <session-branch>` — **linear history, no merge commits.** Because
   the branch was rebased onto the trunk tip, the ff-only merge always succeeds here.
5. `run_compile` on the trunk (regenerate the views with the landed journal), commit the
   regenerated views, `git push`.
6. Delete the local session branch (`git branch -d` — the safe, already-merged delete).

If `end` runs while **on the trunk** (no session branch — a member with branching off, or
a session that started under the pre-Phase-2 harness, like the one that ships this), it
degrades to the Phase-1 path: compile, commit, push to the trunk directly.

A standalone **`session.py merge`** subcommand runs steps 1–6 without re-closing the
journal, so a session blocked at the rebase or the gate can fix the problem and **re-land**
without reopening its close.

### D4 — The gate is a config-driven command list, fail-closed

The gate is the load-bearing home for verification-before-trunk. It is a list of shell
commands from `session.config.json` `"gate"`; each must exit `0` or the merge is blocked.
The default, when no `"gate"` is configured, is the test suite **if a `tests/` directory
exists** (`python3 -m unittest discover -s tests`), else empty — so a member with no tests
is not blocked by a gate it can't satisfy, and the federation lists its own checks
(`distill.py --check`, `standardize.py --check`, `gen_settings.py --check`, the full
suite) explicitly. The gate is **fail-closed**: a nonzero exit *or* a command that cannot
launch blocks the merge (a gate that fails open is not a gate). Blocking never bricks the
session — the journal is closed, the work is safe on the branch, and `session.py merge`
retries.

**Deferred to a Phase-2.1 follow-on:** folding the [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md)
R3 conformance miners into the gate as a *warn on newly-introduced exceptions* (not a hard
block). Phase 2 ships the hard command-list gate; the new-vs-pre-existing-exception diff is
its own increment.

### D5 — Config

Three `session.config.json` keys, all with safe defaults so an un-updated member is
unaffected until its config is refreshed:

- `"trunk"` — the trunk branch name (default `"main"`).
- `"branch_sessions"` — whether to cut session branches (default `true`). A documented
  **kill switch** for rollout safety and for a member that genuinely cannot branch, not a
  floor subtraction; defaults on, so the floor is branch-per-session.
- `"gate"` — the merge-gate command list (default: unittest-if-`tests/`).

## Alternatives Considered

- **Compile on the branch, resolve the handoff conflict at merge.** Rejected: makes the
  *generated* view a merge hotspot and forces conflict resolution on a file no human should
  ever hand-edit. Compile-on-trunk-only removes the conflict by construction.
- **Merge commits instead of rebase + ff-only.** Rejected: merge commits litter the trunk
  history with noise and don't force the loser-rebases discipline ADR-0051 chose. Linear
  history is easier to reason about and to mine (ADR-0048).
- **`end` auto-merges with no standalone `merge`.** Rejected: a gate/rebase block would
  then have no clean retry path short of reopening the close. A separate `merge` subcommand
  gives the retry and backs `--no-merge`.
- **Cross-branch journal reading now (full true-simultaneity).** Deferred to Phase 3:
  reading journals from every ref is real machinery whose only payoff is the concurrent
  *unlanded* sibling, which claims + leases (C4/C5) address directly. Building it now would
  inflate Phase 2 for an edge Phase 3 owns.
- **Gate fails open.** Rejected outright — a gate that lets breakage through on its own
  error is not a gate. The *session* fails safe (work preserved on the branch); the *merge*
  fails closed.

## Consequences

- **Nothing lands on the trunk unverified.** A session that breaks the suite cannot push
  the breakage fleet-wide; it stays on its branch until fixed and re-`merge`d.
- **Git-native conflict handling for real same-file edits.** Two sessions editing the same
  source now rebase-and-resolve like any git workflow, instead of last-writer-wins on
  `main`.
- **Linear trunk history**, which the metrics layer (ADR-0048) mines more easily
  (flown-vs-landed = journals vs. trunk merges).
- **Every session now does a rebase + gate at close** — added latency (the gate runs the
  suite). Acceptable: correctness at the trunk boundary is the point, and the solo case
  degenerates to a cheap fast-forward with a fast/empty gate.
- **New failure surface handled:** rebase conflict (surface + human), gate failure
  (surface + retry via `merge`), offline (land locally, push owed). Each leaves the journal
  closed and the work safe on the branch — no data loss path.
- **The session that ships this (session 77) runs on the trunk** under the pre-Phase-2
  harness, so it lands via the D3 degenerate path; the branch flow first exercises live at
  the *next* session's start. Dogfood-live is therefore next session; this session proves
  it by the test suite.
- **Fleet rollout** is the standard-substrate path (new standard-version); deferred until
  proven live, per the Standardize discipline (don't push unproven substrate to the fleet).
- **Interactions:** Phase 3 (C4/C5 claims + leases) builds on this — the concurrent
  unlanded-sibling edge and the Rule-3 ADR-number allocation live there; hardening R4's
  collision game day exercises this merge flow.

## References

- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — the model;
  C3 is this ADR's mandate, C4/C5 are Phase 3.
- [ADR-0055](0055-lazy-session-start.md) — the first-heartbeat materialization point the
  branch cut hooks into.
- [ADR-0048](0048-federation-metrics-mining-layer.md) — flown-vs-landed mining over the
  linear trunk.
- [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) — the R3 conformance miners
  the gate will host as a warn-layer (Phase 2.1).
- [ADR-0047](0047-versioned-standard-substrate-and-rollout-reconciliation.md) — the
  standard-version + fleet-rollout mechanism this ships through once proven.
