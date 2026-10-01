# ADR-0091: The harness commits what the harness writes, and dirty-tree warnings name only authored work

**Status:** Accepted
**Date:** 2026-08-13
**Deciders:** the operator (the ruling that prompted the work: routine messages about the main checkout's uncommitted state are the harness's housekeeping, not something the user should keep hearing about from every Architect — and the explicit approval of the precedent change in D2), Federation Architect (the mechanism choices below).
**Extends:** ADR-0090 (withheld) (the substrate owns its litter), [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) (the liveness sidecar), [ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) (every session is a lane).
**Work items:** WI-0097, WI-0113, WI-0117 (and the standing-dirt half of WI-0100).

## Context

The substrate writes files into working trees it then never commits, and the session
rituals make every Architect report the resulting dirt to the operator instead of owning it.
Measured live on 2026-08-13, all of it verified in the repos rather than from reports:

- **Federation main checkout** — session 136's post-land journal addendum plus the
  regenerated `session-handoff.md` sat uncommitted for two days. The lane that wrote
  them could not commit them (the isolation guard correctly refuses `git -C` into the
  shared checkout, and no sanctioned verb existed — WI-0097), and "the next session's
  close sweeps them" never came because every subsequent session was a lane
  ([ADR-0070](0070-worktree-lanes-are-fleet-substrate.md) made that the population).
  Every lane start since re-announced the same two files at the operator.
- **A parked member** — the nightly fleet janitor closed a provably-dead journal (the
  session-136 fix working as designed) and, per the invoke-their-harness-but-never-commit
  precedent, left the close in the working tree of a **parked** repo that no session
  will ever open (WI-0117). Standing dirt with no expiry.
- **A second member** — uncommitted nightly journals accumulated the same way.
- **Two more members (dozens of dirty paths between them)** — `.session-state/` is
  tracked in member repos, so every session dirties the repo just by existing, and
  heartbeat residue shows up as tracked deletions (WI-0113). The standard section has
  always described the sidecar as gitignored; members bootstrapped before that line
  existed never got it.
- **One member with staged product code** from live repair work: the one dirty tree
  that is a *genuine* signal, and it renders identically to all the litter above.

The common cause is one asymmetry: **the machinery has write paths into these trees but
no commit responsibility**, while the conversation layer (the lane-start `main:` warning,
the `session-start-git-ritual` "if dirty, stop and ask") treats every dirty path as
news for the user. P10 (`architect-owns-operational-substrate`) says routine work is the
Architect's job; a warning about the harness's own uncommitted writes is the harness
delegating its housekeeping to the operator.

## Decision

**D1 — `.session-state/` is gitignored everywhere, enforced by the janitor.** The
janitor (which already runs in every member repo nightly via the fleet pass, and in the
federation at every start) gains an idempotent migration step: if the repo's
`.gitignore` lacks a `.session-state/` entry, append it; if tracked files exist under
`.session-state/`, `git rm -r --cached` them. This is a member's *own harness* acting on
its *own repo* — the same standing the `apply-briefs` engine already has — so it does
not breach the federation's never-write-authored-files rule.

**D2 — the janitor commits what the harness wrote (precedent change, the operator-approved).**
After a non-dry sweep in a repo with **no provably-live session**, the janitor commits —
pathspec'd, never `add -A` — exactly the harness-owned paths that are dirty: the journal
dir, the compiled handoff, `STATUS.md`, `ROADMAP.md`, plus the D1 migration
(`.gitignore` + the cached removals). This reverses the session-136 "invoke their
harness but never commit for them" precedent, which the evidence shows is the cause of
permanent dirt on parked repos: a close nobody commits is a close no git history
records. The commit message names the janitor so the history is honest about who acted.

**D3 — a sanctioned lane→main sweep (`session.py main-sync`), auto-run at first
activity.** A lane may commit, in the main checkout, exactly the harness-owned paths —
under the same preconditions the land path already uses for its cross-tree writes
(main resolvable, on the trunk, no live session) — and touches nothing else. Mixed dirt
commits only the harness subset and leaves authored files alone. The isolation guard is
unchanged: the verb carries the safety argument the guard cannot see (a denied hand-run
becomes a substrate verb, never an exception to the guard). It runs automatically at a
lane's first heartbeat (the [ADR-0055](0055-lazy-session-start.md) materialization
point — never in the hook path, so a phantom start still writes nothing), and exists as
a standalone verb for deliberate use.

**D4 — dirty-tree messaging names only authored work.** The lane-start `main:` block
classifies: harness-owned dirt is stated as "pending sweep" (and is then swept by D3);
only authored/unknown paths keep the warning, because those are the genuine signal. The
`session-start-git-ritual` habit's statement is amended the same way — the
harness's own records are the harness's job; "stop and ask" applies to authored files.

## Consequences

- The recurring "main checkout has N uncommitted changes" message class ends: the dirt
  is either swept automatically (harness paths) or worth the operator's attention (authored).
- Parked members go clean and stay clean without anyone opening sessions in them.
- The janitor's commit step is the second writer in a repo only when no session is
  live there — the same evidence rule every other cross-tree write uses (P13 held by
  liveness, as in `_sync_main_checkout`).
- A journal the janitor closes is now durable in git history immediately, so a
  fleet-driven close is no longer weaker than an interactive one (WI-0117's gap).
- The `.gitignore` append is a one-line addition to an authored file, accepted as the
  narrow exception D1 argues for; it is idempotent and converging, never accreting.
- Rollout: ships with the substrate push (`session.py` byte-identical fleet-wide);
  members clean up at their next nightly janitor pass with no member-side action.
