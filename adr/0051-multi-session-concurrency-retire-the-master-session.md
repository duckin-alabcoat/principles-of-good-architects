# ADR-0051: Multi-session concurrency — retire the master session

**Status:** Accepted
**Date:** 2026-07-17
**Deciders:** the operator (endorsed the B+C recommendation 2026-07-17); Federation Architect (design within B+C)

> **Update (2026-07-18, [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) + [ADR-0055](0055-lazy-session-start.md)):** the reaper's crash-class semantics and C2's start timing are amended. Heartbeats fired only from the Stop hook (between turns), so one long working turn made a live session look crashed — session 74's journal was falsely reaped mid-build, twice. ADR-0054: heartbeats now also fire on every tool call (all-tools PreToolUse), and a stale heartbeat alone closes a journal only after a 48 h grace; clean-exit and phantom reaping are unchanged. ADR-0055: the hook-path start is LAZY — the journal (and reap/compile/push) materialize at the session's first heartbeat rather than at SessionStart, so a phantom preload (the desktop-app pre-spawn that ran the false reaps) is a no-op by construction; "journal from turn 1" becomes "journal from first activity."

## Context

The federation's session model imposes a **total order** on work that is naturally
partial-ordered. `session.py` allocates a **monotonic counter** (`next_session_number`
reads the highest row of the SESSION LOG table and adds one), and every session's
correctness depends on completing an exclusive, ordered write to two **shared** documents
— `session-handoff.md` (the SESSION LOG index + newest-first prose) and `STATUS.md`. That
counter is an implicit **master session**: a single allocator that two simultaneous
sessions cannot both satisfy, and a shared ledger two sessions cannot both append to
without clobbering.

the operator's requirement (2026-07-17): POGA must work with several simultaneous sessions on
the same project, with no single "master session". Named
scenarios: one operator on two machines; multiple sessions on one machine; eventually multiple
people across timezones. Zero-touch background adoption ([ADR-0050](0050-headless-background-adoption-runner.md))
already makes a `claude -p` session run *while the operator works interactively* — that is
concurrency today, made safe only by luck (the liveness guard) rather than by design.

The failure mode is not hypothetical. Two abandoned sessions sat idle
while other sessions advanced the same repos:

- **One member's architect** committed and pushed real work (an ADR and a new
  module), then auto-closed as an orphan **without a writeup** —
  so the narrative was lost and later sessions planned as if the module was unbuilt. The code
  survived; the *narrative* was the loss.
- **Another member's architect** was *holding a git commit* for days. A later session swept
  it and synthesized an end stamp.
  Recovered — but a held commit in an abandoned session is exactly how orphaning becomes
  **data loss**.

Both trace to one root: correctness depends on a session completing an exclusive, ordered
write to a shared ledger. Remove that dependency and an abandoned session is a non-event.

Every mature concurrent system retired its equivalent the same way — coordination attaches
to durable artifacts, never to sessions. IDs are `timestamp+node+random` (UUID/Snowflake)
*precisely because a sequential counter requires a single allocator, and a single
allocator is a master*. Writers append only to their own stream; shared state is a derived
view. Exclusivity is a named lease with a TTL, and its expiry *is* the orphan recovery.

## Decision

Adopt **B + C as layers**. B (git-native optimistic concurrency) answers *how artifacts
stay consistent*; C (event-sourced coordination) answers *how work and narrative stay
coordinated*. This one ADR establishes the model; a sub-part gets its own follow-on ADR
only if it grows a distinct decision surface. Design choices *within* B+C are the
federation's and are recorded here — the A–D option choice is closed (see Alternatives).

### C1 — Session identity: retire the monotonic counter

A session's durable identity is `<utc-compact-timestamp>-<machine>-<short-random>`, e.g.
`20260717-a3f9`. Nothing allocates order. Causality is recorded as **facts**
on the session's journal: the base commit it cut from (`main@<sha>`), the work item(s) it
claimed, the journals it supersedes. The format is **extensible** to
`<timestamp>-<machine>-<person>-<random>` (the multi-person layer, C-future) without a
schema change. The ephemeral Claude `session_id` (the liveness-sidecar key, ADR-0036) is
recorded in the journal frontmatter as a cross-reference — it is not the durable identity.

The human-facing **ordinal ("Session 70")** survives, but as a *derived label assigned by
the historian*, never claimed at birth (see C2).

### C2 — Narrative: own-journal-only writes; handoff/STATUS become compiled views

Each session appends **exclusively to its own journal**, `sessions/journal/<session-id>.md`,
created **from turn 1** (not only at a successful close). No session ever edits a shared
narrative document mid-flight. The journal is tracked in git (it is durable system
narrative, not data) and carries frontmatter: `session-id`, `machine`, `runtime`,
`role-doc-version`, `base-commit`, `started`, `ended` (empty until close), and
`claude-session-id`.

`session-handoff.md` and `STATUS.md` become **compiled views** — generated deterministically
from the journals + git history by a **compactor** (`session.py compile`), which any
session's startup ritual runs (and which can also run scheduled). They carry a
GENERATED-do-not-edit banner. Conflict-free by construction; the "orphan with no writeup"
class **dies here**, because the journal exists from the first turn.

**Migration-forward** (not history-rewrite, per the Standardize discipline): the existing
69-session `session-handoff.md` is frozen **once** into `sessions/pre-journal-archive.md`
(its SESSION LOG rows + retained prose, verbatim). The compactor composes the live
`session-handoff.md` as: generated banner → SESSION LOG (frozen historical rows + rows
derived from journals) → recent prose entries compiled from journals → a pointer to the
archive for older prose. No pre-journal session is re-narrated.

**Retroactive ordinals** (the operator-endorsed addendum): the compactor sorts journals by
`started` (ties by session-id), assigns ordinals **continuing from the frozen historical
max**, so compiled entries still read "Session N — title" — sessions learn their number
from the historian instead of claiming it at birth. The compactor prints a **census** line
in the compiled STATUS (sessions to date, from journals). The metrics layer
([ADR-0048](0048-federation-metrics-mining-layer.md)) additionally reports **flown vs.
landed** (journal count vs. merges into main); the gap is the orphan rate.

### C3 — Artifacts: session branch → gated trunk

Every session works on a **branch cut from a recorded base commit**; the merge to `main`
passes the verification gate (the load-bearing home for the mined-verification checks,
hardening R3). Short-lived branches; loser of a conflict rebases. **Doctrine: no held
commits, ever** — commit early and often to the session branch; any one-commit-per-session
constraint applies to *merges into main*, never to saving work. (Phase 2.)

### C4 — Tasks: claims with a TTL

Inbox / work items gain `claimed-by: <session-id>` + `claim-expires:`. A stale claim is
reclaimable. The orphan sweeper **becomes the lease reaper** — same mechanism, systemic
role. This is what stops two simultaneous sessions from silently building the same thing
twice. (Phase 3.)

### C5 — Exclusive resources: named leases, and almost nothing qualifies

A lease file (holder, TTL, heartbeat) exists **only** for genuinely single-writer
resources: deploying/mutating a *live* service, and physically single-writer stores. The
lease-worthy set is enumerated per system; leasing broadly is Option A returning. For the
**federation itself** the set is small and named: the `gen_settings` / `push-substrate`
file-set (the one file-set two otherwise-disjoint work lanes both plausibly touch) and any
live-service deploy (the federation has none — no orchestrator). (Phase 3.)

### C6 — Prerequisite: no repo of record in a file-sync folder; `repo_root` is config-resolved

A repo inside a file-sync folder is a hazard the design avoids. A file-sync service is a
sync substrate, not a concurrency substrate: it can replay one machine's half-written tree
onto another. So a repo of record lives in a plain local checkout, and a synced folder, if
wanted, is a deployed tree fed by a publish step. The one federation-side question is settled
here: the harness resolves its **repo root from config** (`repo_root`, defaulting to the git
toplevel of the script location), so a system whose deployed tree ≠ repo-of-record points
the harness at the repo of record — the same shared-harness change C3 needs. (Phase 0.)

### Multi-person layer — deferred

CODEOWNERS-style authority, ADR review quorum, per-person identity. Design nothing beyond
keeping C1's ID extensible. Do not build governance for people who don't exist.

### Phasing

0. **No repo of record inside a synced folder.** A repo placed in one must move to a plain
   local checkout before Phase 2 applies to it; other systems proceed independently.
1. **Identity + journals (C1, C2).** Kills counter contention and narrative loss; works
   even while sessions stay de-facto serial. Federation dogfoods first (ADR-0003).
2. **Branch discipline + gate (C3).** Needs Phase 0 on any synced-folder repo.
3. **Claims + leases (C4, C5).** Only now is true simultaneity safe end-to-end.
4. **Multi-person** — dormant until a second human exists.

## Alternatives Considered

- **A — Formalized single-writer (per-project lock + heartbeat).** Smallest change, linear
  narrative, zero merge conflicts — but *fails the requirement*: it serializes rather than
  parallelizes, needs cross-machine lock babysitting, and is still a master with a
  transferable crown. Baseline only.
- **B alone — git-native optimistic concurrency.** The most battle-tested pattern in
  existence, and git already implements all of it — but it covers *artifacts* only. It says
  nothing about who is working on what, and shared narrative docs (handoff, STATUS) become
  the new conflict hotspot unless restructured anyway. Hence B **+** C.
- **C alone — event-sourced coordination.** Fixes narrative + coordination but leaves
  artifact consistency to chance without B's branch/gate. Hence B **+** C.
- **D — real-time convergence (CRDT/OT, the Figma/Docs model).** For concurrent edits to
  the same artifact in the same second. POGA sessions collaborate at minutes-to-days
  granularity. Overkill — dismissed.
- **One ADR vs. a family.** Chose one ADR: C1–C6 are one coherent model and cross-reference
  heavily; splitting now would fragment the rationale. A sub-part earns its own ADR only if
  it later grows a distinct decision surface.

## Consequences

- **An abandoned session becomes a non-event.** Its journal exists from turn 1 (narrative
  never lost) and simply has no `ended`; nothing was allocated, so nothing must be
  reclaimed to "not burn the counter." The `NC` counter-burn mechanic is retired with the
  counter. Unclosed journals surface as *flown-not-landed* statistics, not corruption.
- **Zero-touch adoption (ADR-0050) becomes safe by design**, not by luck — concurrent
  background sessions are the ordinary case, not a raced exception.
- **Held commits are doctrine-forbidden**, removing the demonstrated data-loss mode.
- **New surfaces to build and get right:** the journal schema; the compactor's determinism
  (it must produce byte-identical output from the same journals + git, and handle a
  half-written journal without crashing); retroactive-ordinal stability; the branch/gate
  workflow (C3) that changes git flow for every session; the claim/lease metadata (C4/C5).
- **Compiled views lag** between compactions — acceptable, and the startup-ritual trigger
  keeps the lag ≤ one session boundary.
- **Interactions:** metrics ([ADR-0048](0048-federation-metrics-mining-layer.md)) mines
  journal/claim/lease stamps and gets concurrency + orphan-rate lines for free; hardening
  R3 mined-verification runs its checks against per-session journal files instead of a
  contended shared ledger (strictly easier); hardening R4 failure-injection gains cases
  (two sessions claim one item; a lease-holder dies mid-deploy; the compactor meets a
  half-written journal). The parallel-work-partition plan (`2026-07-17-parallel-work-partition`)
  is the sequencing layer that consumes C4's claim machinery once Phases 1–3 land.
- **Fleet rollout:** the harness change ships to all Claude members via the standard
  substrate path (ADR-0023/0034/0047); each converges as its own migration. Non-Claude
  members satisfy the obligations natively or with a named compensating control
  (ADR-0041), out of scope for the `session.py` binding.

## References

- Consultant brief: `2026-07-17-multi-session-concurrency` (canonical copy in the
  consultant home; delivery copy in the federation inbox).
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — session rituals are a code
  harness (this ADR re-architects its identity + narrative model).
- [ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) — startup git diagnosis
  + inject; the compiled-view compactor runs alongside it.
- [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) — liveness sidecar;
  the orphan sweeper becomes the C4 lease reaper.
- [ADR-0050](0050-headless-background-adoption-runner.md) — the first standing concurrent
  session; the case this ADR makes safe by design.
- [ADR-0016](0016-memory-is-local.md) — cross-machine continuity is the handoff's job;
  compiled from journals here.
- Partition/sequencing plan: `2026-07-17-parallel-work-partition` (federation inbox).
