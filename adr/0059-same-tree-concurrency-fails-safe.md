# ADR-0059: Same-tree concurrency fails safe — the app surface catches accidents, it doesn't isolate work

**Status:** Accepted — **amended session 88 (2026-07-22): the Q3 `branch_sessions`-on ruling is reversed for detect-and-refuse** (see the amendment section below); the fail-safe intent (D1–D5) stands
**Date:** 2026-07-21
**Supersedes (in direction):** the attribution-substrate thrust of [ADR-0057](0057-same-tree-concurrency-attributed-commits.md) / [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) — same-tree attribution is no longer the way concurrent work is isolated.
**Pairs with:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (intentional concurrency lives in the terminal, isolated natively by `--worktree`).
**Deciders:** the operator (exposed the defect by running the first real concurrent pair, and reframed the fix from "make shared-tree concurrency work" to "catch it as an accident" — session 82, 2026-07-21); Federation Architect (design)
**Slug note:** renamed from `0059-attribution-outlives-liveness` on accept (session 83); the original slug reflected a superseded draft direction.

## Context

ADR-0058 made **attribution** the load-bearing isolation property for two Claude **app** windows
sharing one working tree, and defaulted `branch_sessions` off (session 79), so immediate-land on
the shared trunk became the default. The **first real concurrent pair** exercised it on
2026-07-21: sessions 80 (curate) and 81 (ADR sweep), closing within ~0.5 s. Result:

- **One** commit, `a2531a1`, containing **both** sessions' files — session 81 swept session 80's
  `habits/master.md`, `habits/master-history.md`, journal `f9cb`, and `curate/seen.json`.
- Session 80's `end` **bailed** (couldn't identify its own journal via the unreliable
  `CLAUDE_CODE_SESSION_ID`) and fell back to a hand commit onto an already-clean tree.
- Session 81 **reported the opposite of the truth**: *"committed only this session's work … left
  the sibling's in-flight files untouched."* The commit proves that false.

No work was lost, which is why it passed as clean. But isolation, per-session gating, and honest
reporting all broke silently. (Full forensic detail — the `.session-state/a762dc92….touched`
ledger naming the swept files, the undocumented-env-var root cause — is retained in this ADR's
git history at the superseded draft revision.)

**The reframe (the operator, session 82).** An earlier draft of this ADR tried to make shared-tree
attribution *robust* — outlive liveness, cover Bash writes, fix self-identification, guard the
sweep. But [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) establishes that
**intentional** concurrency belongs in the **terminal**, where Claude Code's native `--worktree`
gives each session its own dir + index + branch — real isolation, for free, with our existing
`end` branch-land doing the merge. Once that is true, two windows on one shared tree is no longer
a thing to *make work*. It is an **accident**: you opened a second window on a folder you were
already working in. The app surface does not need clean per-session attribution — it needs to
**not corrupt, not lose, and not lie.** That is a guard, not a substrate.

## Decision (DRAFT)

**On the shared-tree (app) surface, concurrency is detected and made to fail safe — never
silently blended.** The elaborate attribution machinery (the superseded D1–D5) is dropped.

### D1 — Detect a live sibling at close (existing read, no new state)

At `end`, consult the already-maintained liveness view (`_live_sibling_csids`, keyed on open
journals) to answer one question: **is another session live in this same tree?** No new
bookkeeping — this read already exists.

### D2 — If solo: land immediately, unchanged

The common case. One session in the tree lands its work to the trunk exactly as today. Nothing in
this ADR touches the solo path.

### D3 — If a sibling is live: preserve, don't blend, don't merge

Do **not** run the blend-prone shared-trunk land. Instead commit **only this session's own
ledger'd files** to a session branch (the ADR-0056 branch, reused here for its original purpose:
isolation), leave the trunk untouched, and defer the merge. The sibling's files are never swept —
the ledger names them as not-ours, and we simply don't touch what isn't in our own ledger. The
merge lands when the tree is next solo (last-one-out), or when the user resolves it. Starvation —
the failure that killed last-one-out in ADR-0058 — **does not apply here**, because it only
bites *sustained* concurrency, and sustained concurrency now lives in the terminal with real
worktrees. App concurrency is transient by definition (you will close one of the two windows).

### D4 — Tell the truth (the fix for the silent misreport)

Whenever a sibling was detected, `end` states it plainly to the user: *"Another session is live
in this folder. This is shared-tree mode — your work is committed to branch `<x>` and will land
when the folder is solo. For parallel work, start sessions from the terminal (`poga`), where each
gets its own worktree."* Never a "committed only this session's work" claim that isn't verified.
A session may not report clean isolation it did not achieve
([`no-fabricated-data`](../habits/master.md#no-fabricated-data) / P18).

### D5 — Point accidental concurrency at the real surface

The message in D4 is also the nudge: intentional parallelism has a home (the terminal + `poga`
worktree lanes, [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)). The app
guard exists to make the *accident* safe, not to invite parallel work onto the shared tree.

## Consequences

- **The blend cannot recur.** A live sibling routes to the branch-preserve path; a session never
  sweeps a path outside its own ledger. `a2531a1` becomes impossible.
- **The silent misreport cannot recur.** Detection is surfaced; unverified isolation claims are
  forbidden.
- **Far less machinery than the superseded draft.** No claim-on-change, no attribution-outlives-
  liveness subtlety, no self-identification fix, no fail-closed sweep guard. The whole thing is:
  read liveness (exists) → branch-preserve + honest message when not solo.
- **The self-identification bug (old Defect B) is contained, not fixed here.** In shared-tree
  mode a bailing `end` is annoying but safe (it commits nothing wrong). The *fix* for it lives in
  ADR-0060: in the terminal each session is alone in its worktree, so there is only one journal to
  close and nothing to identify. The app path just needs `end` to not do damage when it can't
  self-identify — which D3 ensures.
- **Deferred merges can queue** if someone keeps two app windows open a long time. Acceptable:
  the work is committed and safe on its branch; this is the transient-accident case, and the
  honest message tells the user how to clear it (close a window) or avoid it (use the terminal).

## Resolved at acceptance (session 83, 2026-07-22)

Accepted as the **app-only fallback**, specified but **not eagerly built** — the terminal
(`poga` + native worktrees, [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md))
is the primary surface, and the concurrent pair that exposed the defect was a drill, not routine
use. The rulings on the three open questions:

1. **Q3 chosen as the structural guard (subsumes Q1/Q2).** Turn ADR-0056 `branch_sessions` back
   **on for the app surface**, so each window is always on its own `session/*` branch. The blend
   (`a2531a1`) becomes impossible by construction — two sessions were never on the same branch to
   blend onto — using machinery that already exists (`_land_branch`), with no new branch-preserve
   code. This is the one-line flip that realizes D3 without building D3's bespoke path.
2. **Q1/Q2 — no elaborate preserve path built.** With branches-on, D3 is automatic; the honest
   message (D4) is the only added behaviour. We do *not* build the "commit-my-ledger'd-files-and-
   defer" machinery eagerly, and we do *not* implement a flat refuse-to-close. If the app surface
   is ever used concurrently in anger and this proves insufficient, revisit — but per ADR-0060's
   rule, the answer to a recurring shared-tree leak is to move the workflow to the terminal, not
   to grow the app path.
3. The branch-lifecycle cost ADR-0058 simplified away is re-accepted **for the app surface only**;
   the terminal surface stays worktree-native and does not cut `session/*` branches.

## Amendment — detect-and-refuse replaces `branch_sessions`-on (session 88, 2026-07-22)

The session-83 Q3 ruling — *turn `branch_sessions` back on for the app surface so each window is
on its own `session/*` branch* — was a **defect**, surfaced by the session-83 two-lane drill and
confirmed while clearing its follow-ups.

**Why it can't work.** Two `claude` windows on one folder share **one working tree, and a git
working tree has exactly one HEAD.** They cannot sit on two different `session/*` branches at
once. `branch_sessions`-on therefore only ever protects a **solo** app session (which never
needed protecting); the moment a *second* window opens — the exact case the guard exists for —
cutting it onto its own branch either fails or moves the **shared** HEAD out from under the first
window. The one-line flip realized nothing it claimed to.

**The replacement — detect-and-refuse (realizes D1/D4/D5 directly).**

1. **`branch_sessions` → off** for the app surface (`session.config.json`). App closes route back
   through the ADR-0058 attribution path, which already commits **only this session's own
   attributed files on the fresh trunk tip** — so the `a2531a1` blend is prevented by
   construction, without branches. (The flag stays in the loader, default off; poga lanes ignore
   it and land via `_land_worktree_lane` regardless — ADR-0060.)
2. **Detect at start (`_live_same_tree_siblings`).** A new read scoped to the **per-worktree**
   sidecar (`ROOT/.session-state/*.live`) — *not* the shared journal dir — counts other sessions
   whose heartbeat is fresher than `HEARTBEAT_STALE_MIN` and that left no clean-exit marker. A
   poga lane, isolated in its own worktree with its own sidecar, is **never** counted; crashed
   `.live` debris is excluded by the freshness bar, so a solo restart never false-refuses.
3. **Refuse loudly (D4/D5).** When a live same-tree sibling is present, `start` emits a `REFUSE:`
   line steering the user to close the window and run concurrent work via `poga` (each lane its
   own worktree). A SessionStart hook cannot hard-abort the session, so "refuse" is the strongest
   honest signal a hook can post — prominent, at the top of the orientation block — plus the
   safe-by-construction close. The end-side note likewise names shared-tree mode and points at
   `poga` ([`no-fabricated-data`](../habits/master.md#no-fabricated-data) / P18).

**Net.** Same fail-safe *intent* as the original D1–D5, realized by a read + two honest messages
+ a config flip, with **less** machinery than the (broken) branch path — and no false claim that
two windows are isolated when a shared HEAD means they cannot be. This also **closes the
post-land staleness wart** (the session-83 item-6 follow-up): with the app surface strictly solo,
no plain-`claude` session sits in the main checkout while poga lanes CAS-advance `main` under it,
so the stale-working-tree hazard (a manual `git add -A` staging a lane's files as deletions)
cannot arise from concurrency. A belt-and-braces guard against committing from a stale checkout
is deferred until it recurs ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
— the trigger is a repeat, and the harness close paths are already safe).
