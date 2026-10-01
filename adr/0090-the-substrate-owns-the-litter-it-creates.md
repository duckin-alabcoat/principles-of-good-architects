# ADR-0090: The substrate owns the litter it creates, and a reaper reports outcomes

**Status:** Accepted
**Date:** 2026-08-07
**Deciders:** the operator (the ruling that prompted the work: the time spent in every session, with every Architect, fixing stranded lanes and similar residue must go to zero), Federation Architect (the four design calls below).
**Extends:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (worktree lanes), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the isolated merge gate), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (the coordination store), [ADR-0089](0089-a-lane-checkpoints-its-own-work-and-the-repo-recovers-it.md) (stranded *work*).

## Context

[ADR-0089](0089-a-lane-checkpoints-its-own-work-and-the-repo-recovers-it.md) closed the
stranded-**work** class: a lane's uncommitted edits are checkpointed, a land absorbs the
checkpoint, and the owning repo names stranded lanes at its own next start. the operator's
complaint here is the *other* half, and it survived that fix intact: the machinery keeps
leaving **litter** behind — artifacts with no owner, which someone then clears by hand,
every session, in every repo.

Four instances, all filed separately, all the same shape:

- **Merge-gate worktrees** ([WI-0063](../work-items/)). `_gate_commit` removes its scratch
  worktree in a `finally`, so any gate whose process dies first leaks a *registered*
  worktree permanently. Eleven accumulated in one four-minute burst of concurrent lands on
  2026-07-28 and were removed by hand.
- **Coordination claims** (WI-0078). Claim release was wired to exactly one teardown
  route. The reaper and manual teardown — the *documented* route whenever a live sibling
  lane must be preserved — both left the holds behind. One member tore a lane down properly and
  its claims outlived it by a week.
- **Session sidecars** (WI-0085). The janitor is anchored on `ROOT`, and `.session-state/`
  is per-tree by design. A lane swept the lane's own handful of files; the main checkout's
  pile reached **78** stale markers, oldest 2026-07-05, while the sweep reported itself as
  having run.
- **Lane processes** (WI-0094). Nothing had *ever* looked at the process that was running
  in the lane. Session ~124 found four Claude sessions alive in lanes that no longer
  existed, the oldest up 7d17h and dispatched on an item that shipped in 6.1.0.

The common cause is not four bugs. It is that **each mechanism cleans up only on its own
happy path**, and every abnormal exit — the case the concurrency substrate exists to
survive — produces residue that no code is responsible for.

## Decision

### D1 — A residue sweep is scoped by the tool's own registry, never by a filesystem glob

`session.py` is byte-identical fleet substrate, so *every* member stages its gate
worktrees under the same `fed-gate-` prefix in the same temp directory; one from another member was
observed appearing there mid-cleanup on 2026-07-30. A sweeper that globs the prefix would
delete another repo's in-flight gate run and fail its land.

The sweep is therefore driven from `git worktree list` **of the repo being cleaned**. This
is not a filter applied after the fact — a foreign gate is never *visible* in the first
place, which is a stronger property than being skipped. The test pins the hazard directly:
it asserts that a glob would have found two, and that we found one.

The residual case — a concurrent land *in our own repo* — is handled by an age guard, not
by a liveness probe. Unknown age reads as young, the same rule `_lane_too_young` already
applies to lanes.

**Generalization, and the reusable half of D1:** where shared substrate names a resource in
a namespace it shares with sibling systems, scope every destructive operation by the
*owning tool's* view of that namespace, never by the name itself.

### D2 — Reconcile on the fact, not on the route

Claim release was wired into `cmd_worktree_remove`. The fix is *not* to wire it into
`_reap_lane` as well, and then into the next teardown path someone adds.

A lane's branch is deleted only when that lane lands or is torn down, so
`refs/heads/<identity>` being absent is **proof** the holder is gone. Reconciling on that
fact covers every route that exists and every route not yet written, including the manual
one that no hook can observe. Per
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence),
the third route leaking was the signal that wiring routes one at a time is the wrong shape.

Scoped to `worktree-` identities only. A non-lane identity is a Claude session id, and an
absent session id is not evidence of anything — treating it as proof of death is the
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) collapse.
Those stay with the TTL.

### D3 — Directories, records and files are swept automatically; processes are reported

A process is the one thing in this set whose removal cannot be undone by re-running
anything. It is reported in the start banner of every session in the owning repo and
terminated only by `reap-lanes --processes`
([`confirm-destructive-ops`](../habits/master.md#confirm-destructive-ops) /
[P9](../principles/master.md#p9--destructive-ops-confirmed)).

**This still satisfies the ruling.** Zero time is zero of **the operator's** time, not zero of
the Architect's: the blocker was never that a human judgment was required, it was that the
harness permission classifier refuses a bare `kill` from inside a session, so the one
actor who could not act was the Architect. A substrate verb with the safety argument built
into its predicate is the thing that unblocks it — and it is a different act from
hand-typing a pid past a guard that declined to approve it.

**The predicate is not "does the lane directory exist", and this is the finding worth
carrying.** Lane names are *recycled*, so a nine-day-old process and a live session both
named `poga-1`; a directory-existence test misses the worst offender. Two proofs only:
`lane-gone`, and `predates` — the process started before the lane directory was **born**,
so it belongs to an earlier lane of that recycled name. Ambiguous cases (two live processes
on one lane, an ordinary resumed session) are left alone, because a list meant to be safe
to act on must not mix *proven* with *likely*.

**The grace margin is load-bearing and was found by probing, not by reasoning.** A lane's
own process necessarily starts *before* its directory exists — measured live, the session
authoring this ADR started **one second** before its lane directory. Without the margin the
detector classifies live sessions as stranded, and `--processes` would terminate the
session that asked for the sweep. Confirmed by falsification before the code was kept.

### D4 — A reaper's receipt is a post-check, never the attempt

The process reaper's first cut printed `terminated pid N` on the strength of `os.kill` not
raising — which proves the signal was *delivered*, nothing more. Exercised against a real
four-day-old stranded process, it reported `terminated` while the process kept running.

Every outcome now comes from re-probing the world after acting, and the three outcomes get
three distinct words — `terminated`, `killed (ignored SIGTERM)`, and `SURVIVED … it needs a
human`. This is
[`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence)
applied to cleanup, and it is the decision with the widest reach here: a sweep that
overstates itself is worse than no sweep, because it stops anyone from looking. That it
slipped into a sweep whose entire purpose is to stop lying about residue is the argument
for making it a rule rather than a fix.

## Consequences

**The operator-visible invariant:** *no artifact the lane machinery creates outlives its
owner without something naming it* — and the operator runs no command to make that true.

- Litter is swept by the repo that owns it, at its own session start, on the path every
  session already takes. No external observer, no remembered chore.
- A released coordination record leaves a **tombstone** naming holder and reason
  (`released` / `reclaimed` / `teardown`), so "released cleanly" and "never claimed" stop
  reading identically — the rung an outside reader of the store had to report as
  *not distinguishable* (WI-0074). Tombstones carry their own TTL and are reaped on the same pass as live
  records, so the trace directory cannot become the next litter class
  ([`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code)).
- The `predates` proof needs `st_birthtime` and yields nothing where the filesystem has
  none. It deliberately does **not** fall back to `mtime`, which a lane's own activity keeps
  fresh and which would therefore prove the opposite of what it appears to prove; such
  members get the `lane-gone` proof only, and the redistribution brief must say so.
- This is shipped substrate, so it reaches every member at the next push. No config key,
  no member-side action; absent state degrades to today's behaviour.
- Every mechanism fails open. A sweep that cannot run must never block a session from
  opening or closing.

Cleared on the day of the decision, as the acceptance run: four stranded processes (up to
8d13h), 130 sidecar files, and the main checkout's stale-marker pile from 78 to 16 — with
zero older than the 14-day policy.

## Update note — same session, the operator's direction

The three caveats this ADR shipped with were named to the operator as things it did *not* fix. His
answer was *"fix 1, 2, and 3"*, so they are decided here rather than left as known gaps.
Recorded as an update note rather than a second ADR: two refine D3, one extends D1's
argument to a new surface, and splitting them would fragment one decision made in one day.

**D3a — the `predates` proof falls back to git's own registration record.** `st_birthtime`
is a macOS/BSD field Python does not expose on Linux, so shipping only that would have made
this detector quietly weaker on every Linux member — not broken, not warned about, just
never firing for the harder half of the class. The fallback is
`<common>/worktrees/<name>/gitdir`, written when the worktree is registered and not
rewritten by ordinary work (commits touch `HEAD`). It is also the *more correct* signal
where a lane branch is **reused**: anything derived from the branch reports the original
lane's age and defeats exactly the recycled-name detection this proof exists for. Both
sources agree to the second on the live pool. Where neither resolves, the process is
returned in a **separate `unprovable` list** and the banner prints NOT CHECKED — an
un-asked question cannot reach the list the killer acts on, enforced by the return type
rather than by remembering to filter.

Found while testing this: **`st_birthtime` is not immutable on macOS** — setting an older
mtime drags it down, so a restore or `rsync -t` can move it. Survivable only because of the
*direction*: an earlier birth makes `process_start < birth - grace` harder to satisfy, so
the failure mode is a stranded process we decline to kill, never a live one we do. Pinned,
because a refactor could invert that comparison without noticing what it cost.

**D3b — a lane can see the shared checkout, and still cannot touch it.** The harness
refuses both routes from an isolated worktree (`git -C <main>`, and editing a
shared-checkout path). Both guards are right. But the consequence was that a lane could not
even *look*, so main's state was discovered only by tripping over it — a land that declines
to sync, a refused substrate push, or a session-end from main committing the dirt verbatim,
which is WI-0089's revert-dressed-as-housekeeping. `session.py main-status` and a start
banner line report it, with what it blocks and whether a session is live there.

This is not a bypass of those guards and the distinction matters: what they exist to prevent
is a lane *mutating* a tree it does not own, and the land path already reads and writes main
under a defined contract (`_sync_main_checkout`). A test pins that this path never writes.
The generalization, which is D1's argument on a new surface: **when a guard correctly
forbids a hand-run operation, the answer is a substrate verb with the safety contract built
in — not an exception to the guard.** Two of this session's fixes took that shape
(`reap-lanes --processes`, `main-status`), both after a hand-run was refused.

**D3c — a denial log polluted by its own tests carries no threshold.** The permission
friction the operator named is almost entirely harness-side and not ours to fix; measuring it
exposed one part that is. `guard-firings.jsonl` held **532 records of which 432 were test
fixtures** — the path was bound at import, so every suite run appended real-looking
denials to the real log (`git -C /x reset --hard HEAD~1`, 147 times). The
[`denial-audit-log`](../habits/master.md#denial-audit-log) habit exists so that a spike in
blocked actions is *a signal someone sees*; a log in which a routine test run is
indistinguishable from a real burst cannot carry that alert. The path now resolves at call
time. Verified: a full suite run left the real log at exactly 532.

## Alternatives considered

- **Glob the shared temp prefix** — D1; rejected as able to destroy another member's
  in-flight land, and pinned as a hazard by test rather than left as a comment.
- **Wire claim release into each teardown route** — D2; rejected as the shape that already
  failed three times.
- **Auto-terminate stranded processes at session start** — D3; rejected under P9. The
  predicate is subtle enough that a near-miss was found during its own construction.
- **Leave a `coord-events.jsonl` instead of tombstones** — WI-0074 offered either and
  the requesting reader had no preference. Tombstones chosen because they expire with the existing
  reaper, where an append-only log would need a retention mechanism built for it.
- **Report a process that ignores SIGTERM and stop** — D4; rejected as handing back exactly
  the chore this removes, given that the predicate has already proved the process stranded.
