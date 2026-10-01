# ADR-0060: The concurrency substrate is a git lifecycle wrapper; shared-tree is the app-only fallback

**Status:** Accepted
**Date:** 2026-07-21 (accepted session 83, 2026-07-22)
**Builds on:** [ADR-0056](0056-session-branch-gated-trunk.md), [ADR-0057](0057-same-tree-concurrency-attributed-commits.md), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md), [ADR-0059](0059-same-tree-concurrency-fails-safe.md) — realizes ADR-0057's deferred D4 ("the launcher is not a mode")
**Deciders:** the operator (identified that the shared-tree constraints are artifacts of the Claude *app*, not the LLM, and proposed the wrapper — session 82, 2026-07-21); Federation Architect (design)

## Context

ADR-0056 → 0059 are one long fight with a single root cause. Two Claude **app** windows open
on one folder share exactly one working tree — one checkout, one index, one `HEAD` — and git's
entire isolation model (branch / index / worktree) assumes *one worker per working tree*. So we
have spent four ADRs hand-rebuilding, on a shared tree, what git already provides for concurrent
workers: per-session attribution (`.touched`), a private `GIT_INDEX_FILE` to avoid the shared
index, a scratch worktree *just* to gate, a compare-and-swap trunk advance with rebuild-on-
conflict (a hand-rolled merge queue), and — in 0059 — a fragile scheme for a session to identify
its own journal at close.

**the operator's decomposition (session 82):** none of that is forced by Claude or the LLM. It is forced
by the **app's launch model**. ADR-0057 rejected worktree-per-session for three reasons, and each
is an app artifact, not an LLM one:

| ADR-0057's objection | Why it's an *app* artifact |
|---|---|
| A session's cwd is fixed at launch; it can't relocate itself. | In the **terminal**, a wrapper program sets cwd *before* exec'ing `claude`, dropping the session into a pre-made worktree lane. The session never relocates — the wrapper places it. |
| The app surface has no launcher hook. | The terminal **is** the launcher surface; the wrapper is the thing you run. |
| Memory/project identity is folder-keyed, so lanes fragment it. | Verified false for the part that matters — see below. |

The shape the operator named: **a git wrapper around an LLM.** A deterministic outer shell that owns the
lifecycle — **setup** (allocate a worktree lane, place the session) → **exec `claude`** (the
non-deterministic LLM core) → **teardown** (gate, land, remove the lane) — with the LLM in the
middle doing only the judgment work. Clean separation, and portable: any LLM CLI could sit in
that shell.

### Verified harness facts (claude-code-guide, session 82)

- **Auto-memory identity derives from the git *repo root*, not the worktree cwd.** Per the
  Claude Code docs, *all worktrees and subdirectories of one repo share a single auto-memory
  directory.* The fragmentation objection is **void** — memory follows the repo across every lane.
- **Session transcripts key per-cwd** (they fragment across worktree paths). For the federation
  this is **cosmetic**: the durable session narrative is the git-tracked journals in
  `sessions/journal/`, shared through the repo regardless of cwd. The CCD transcript store is not
  system state.
- **Claude Code has native `--worktree` support** and manages `.claude/worktrees/`. Confirmed
  lifecycle (claude-code-guide, session 82): `claude --worktree <name>` (CLI flag `-w`, also an
  `EnterWorktree` tool) creates a **new** worktree at `.claude/worktrees/<name>/` on branch
  `worktree-<name>`. Teardown is **removal-only, no merge** — empty worktrees auto-removed, ones
  with work prompt keep/remove, `-p` leaves them. **Merge and gating are explicitly out of
  scope** — ours to own. A prior session in this very repo already ran in a worktree. So the
  isolation substrate is **native**, not something to build.
- **A `WorktreeRemove` hook** fires when Claude removes a worktree, receives the worktree path,
  and is **fail-open** (a non-zero exit is logged, never blocks session exit — the same shape as
  every other federation guard). This is the deterministic wiring point for our gate + land +
  cleanup: `--worktree` for isolation, `WorktreeRemove` → our teardown.
- **Symlinking `.claude` is explicitly refused** by Claude Code — but is unnecessary, since
  memory is already shared by repo-root identity.
- (**From 0059**) `CLAUDE_CODE_SESSION_ID` is undocumented and unreliable in a Bash-tool
  environment; `session_id` is delivered *reliably* only to **hooks**, via stdin.

The three objections that ruled out worktrees are therefore either terminal-solvable (cwd,
launcher) or void (memory). What remains true is only that the **app** cannot host a wrapper — so
the shared-tree machinery stays as the app's fallback, and stops being the universal answer.

## Decision (DRAFT)

### D1 — The concurrency substrate is a terminal git-lifecycle wrapper

Concurrency is owned by a deterministic wrapper, not by in-session harness code fighting a shared
tree. The wrapper: allocates a worktree **lane** from a reusable pool (lowest free lane, returned
at end — so folder count is the high-water mark of *simultaneous* sessions, not of sessions ever
run, per ADR-0057 D4), launches `claude` with cwd in that lane, and on exit runs the
deterministic teardown (gate → land → remove lane). The LLM core is unchanged; only its shell is
new.

### D2 — One session per tree makes isolation git-native, dissolving 0057–0059's machinery

A session alone in its own worktree owns every changed file in it. Therefore, **for
wrapper-launched sessions**:

- **Attribution (`.touched`) — unnecessary.** Nothing to attribute; no sibling in the tree.
- **The private-index commit build and the scratch-worktree-for-gating — unnecessary.** The lane
  *is* an isolated index and an isolated checkout. Land is the ordinary branch → gate → merge.
- **0059's Defect B (self-identification / env drift) — cannot occur.** The wrapper created the
  lane and holds its journal id **out-of-band**; it invokes `end --session-id <frozen-id>`
  itself. Identity lives *outside* the LLM process — exactly where it is stable. There is one
  open journal in the lane, so there is nothing to disambiguate.

This is not a tweak to 0059; it is the condition under which most of 0057–0059 is simply not
needed.

### D3 — Memory stays unified; transcripts fragment harmlessly

No action required — the verified repo-root memory identity gives every lane one shared
auto-memory directory, and the load-bearing narrative (journals, handoff, STATUS) is repo-tracked
and therefore shared. Transcript fragmentation is accepted as cosmetic.

### D4 — Two surfaces, two models, honestly labeled

- **App surface** → shared tree, no wrapper possible → **ADR-0059** is the model. It is not
  deleted; it is frozen as the app-only fallback and **stops growing**.
- **Terminal surface** → wrapper + worktree lanes → git does isolation, the gate, and the merge;
  0057–0059's hand-rolled substitutes are dormant.

The rule going forward: **do not grow shared-tree concurrency machinery.** If a future defect
would require a "0061" to patch another shared-tree leak, that is the signal to move the affected
workflow to the terminal wrapper instead of patching the app path again.

### D5 — The wrapper is `poga`

The wrapper is a `poga` terminal command — the same CLI the held
`2026-07-21-poga-cli-bootstrap-restore-brief` is independently pushing toward, with `session` /
`run` joining `bootstrap` / `restore` as verbs on one spec-driven tool. This does **not** conflict
with ADR-0057's "poga stays a label": that objection was to making *the operator* the lane allocator by
encoding a lane in what the operator types. A wrapper that **auto-allocates** does the opposite:
the operator runs one command; the wrapper picks the lane. `poga`-the-label (typed inside a session to name it)
and `poga`-the-command (the wrapper you launch) coexist, or the command subsumes the label.

## Consequences

- **Most of 0057–0059 becomes dormant in the terminal** — the strategic win: we stop maintaining
  a hand-built worktree substitute and let git do it.
- **The app keeps 0059.** We do not get to delete the shared-tree path; we get to stop extending
  it. Two models is the honest cost of supporting both surfaces.
- **Portability improves.** The wrapper is LLM-agnostic — the same shell fits a non-Claude
  runtime (the ADR-0041 direction), so concurrency stops being Claude-Code-shaped.
- **The reusable-lane pool bounds folder count** to simultaneous sessions, answering ADR-0057's
  scale-to-N constraint without a standing lane per possible session.

### Facts settled during the build (session 83)

- **`git update-ref refs/heads/<trunk>` DOES advance the trunk while it is checked out in the
  main worktree.** This is the load-bearing assumption of the plumbing land, and it is pinned by
  `tests/test_worktree_lane.py::test_cas_advances_trunk_while_checked_out`. If a future git ever
  changes this, the lane land breaks — the test is the tripwire.
- **The main checkout goes momentarily stale after a lane lands.** Advancing `refs/heads/<trunk>`
  by ref (no checkout) leaves the main worktree's HEAD on the new tip but its working files on the
  old one, so `git status` there shows the just-landed files until a refresh. Accepted: the main
  checkout is the trunk holder, not an active concurrent worker; the next `session.py start` there
  reconciles it. Do not "fix" this by resetting another worktree from the lane (violates
  touch-only-your-own-tree).
- **A lane leaves the trunk views FRESH — recompiled inside the land, folded into the close
  commit.** An earlier build no-op'd compile on lanes (extending ADR-0056 D2) and accepted a
  one-session handoff/STATUS lag. That was over-cautious: the D2 hotspot needs a genuine *merge*
  of two divergent full-file rewrites, but the lane land is **linear** (rebase onto the trunk tip
  → ff CAS, never a merge). So `_land_worktree_lane` recompiles the views against the trunk tip it
  is advancing to (`run_compile(force=True)`) and amends them into the close commit — recomputed on
  every CAS-race retry, so the result is always a superset of every landed journal. No lag, no
  hotspot. Decisive reason it is write-side, not read-side: the busiest STATUS readers are the
  federation's *cross-system* miners (`metrics.py`, `reconcile.py`) reading OTHER systems' STATUS
  off mirrors — those must read as-reported and can never refresh (no runtime; P4 / verify-against-
  self-report). Freshness is the writer's job at its own land boundary, not every reader's. Pinned
  by `test_worktree_lane.py::test_land_refreshes_trunk_views`.

## Resolved at acceptance (session 83, 2026-07-22)

1. **RESOLVED — there is no bespoke wrapper.** Native `--worktree` does isolation; a
   `WorktreeRemove` hook running our existing `session.py end` branch-land does teardown. D1's
   "lifecycle wrapper" collapses to **configuration + one hook**, not an outer program. The ADR's
   framing stands, but the implementation is thin: wire `WorktreeRemove` → gate + ff-merge
   `worktree-<name>` to trunk + cleanup, reusing `_land_branch` (ADR-0056).
2. **RESOLVED — land-from-worktree via plumbing, no checkout.** The worktree is checked out on
   `worktree-<name>`; `end` (run inside the lane) ff-advances trunk to the branch tip via
   `update-ref` after the gate passes — never checking out the trunk (the main tree holds it) and
   never checking out the branch (the lane holds it). The lane is left clean; Claude removes it;
   the `WorktreeRemove` hook deletes the now-merged branch. Pinned with a test.
3. **RESOLVED — the operator types `poga`.** A supported configuration is a shell function that
   **auto-allocates** the lowest free lane from a reusable pool and execs
   `claude --worktree poga-<n>`. The operator types one word; the wrapper picks the lane. The
   lane is never encoded in what the operator types (ADR-0057's objection preserved).
4. **RESOLVED — 0059 is specified, not eagerly built.** The terminal is the primary surface; two
   app windows on one folder is supported but secondary. [ADR-0059](0059-same-tree-concurrency-fails-safe.md)
   accepts as the app fallback with `branch_sessions` turned back **on for the app** (each window
   on its own branch → blend structurally impossible), and the honest-message guard — no eager
   branch-preserve build.
5. **OPEN — lane crash-safety (fast-follow, not a blocker).** A wrapper that dies mid-session
   leaves an orphaned worktree + branch. Ship the happy path + a `poga lanes --clean` manual
   sweep first; a full auto-reaper (mirroring the journal reaper) follows. Tracked so we don't
   trade the journal-orphan class for a silent worktree-orphan one.
