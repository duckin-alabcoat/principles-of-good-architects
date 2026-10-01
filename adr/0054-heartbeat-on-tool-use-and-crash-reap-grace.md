# ADR-0054: Heartbeat on tool use + a crash-reap grace — a stale heartbeat is not death evidence

**Status:** Accepted
**Date:** 2026-07-18
**Deciders:** Federation Architect (substrate bugfix under standing maintenance authority, ADR-0007/ADR-0012; harness-alone session per the parallel-work-partition brief)

## Context

The liveness sidecar ([ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md)) heartbeats `.session-state/<csid>.live` from the **Stop hook**, which fires only **between** assistant turns. The journal reaper ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md), pulled forward to Phase 1b) treated a heartbeat older than `HEARTBEAT_STALE_MIN` (15 min) as a crashed session and closed its journal at the last beat.

Those two facts compose into a false-reap: **one long working turn — routine for build sessions — silences the heartbeat while the session is alive and working.** Proven live in session 74 (2026-07-18): a concurrent phantom `claude` startup ran the reaper and closed session 74's journal mid-build; it had to be reopened by hand. The same window also mis-describes an idle-open session (a terminal left open overnight beats nothing either) — staleness at the 15-minute horizon simply cannot distinguish *dead* from *quiet*.

## Decision

Two complementary changes; both keep everything automatic (no manual triage path is added).

1. **Heartbeat on tool use.** The settings floor gains an **unmatchered (all-tools) `PreToolUse` entry** running the existing `session.py heartbeat` — tool calls fire constantly during a working turn, so a live session now beats *through* long turns, not only between them. `cmd_heartbeat` throttles writes to one per `HEARTBEAT_MIN_INTERVAL_SEC` (60 s): the hot path reads the sidecar and usually exits without writing anything. It remains fail-open and is not a guard (it never denies).

2. **The reaper demands definitive evidence for the crash class.** `_reap_dead_journals` semantics become:
   - **Clean exit** — `.ended` as the latest signal: close at the marker (phantom-delete when stub + <120 s), **unchanged and immediate**. This is the common path, including the background-`claude -p` phantom class, whose clean exits write `.ended`.
   - **Fresh beat** as the latest signal: live, left open — unchanged.
   - **Stale beat, no later `.ended`** (the crash class): closed at the last beat **only once the silence exceeds `REAP_CRASH_GRACE_MIN` (48 h)**. Within the grace the journal is left open — **flown-not-landed**, the state ADR-0051 already renders as in-progress with no action owed.
   - **No sidecar**: left open — unchanged.

   `HEARTBEAT_STALE_MIN` (15 min) keeps its one remaining job: proving a session **live** (the concurrent-elsewhere detector). The thresholds are deliberately asymmetric — 15 minutes of freshness is enough to call something alive; only 48 hours of silence is enough to call it dead.

The **standard substrate reaches v1.3.0** (`heartbeat-on-tooluse`, a `claude-hook`-scope capability whose detector *parses* the settings JSON for a PreToolUse heartbeat entry — the Stop hook runs the identical command, so a grep cannot tell them apart). The legacy `stamp`-binding path (`orphan_triage`, non-Claude members) is untouched: its heartbeat branch only ever fires for hook-bound members pre-journal-cutover — an empty set — and non-Claude bindings have no heartbeats at all (they resolve by `.ended`, git evidence, or `resolve-orphan`).

## Alternatives Considered

- **Only raise `HEARTBEAT_STALE_MIN`** (stopgap c). Moves the cliff without removing it — any threshold short enough to be useful for liveness is long enough to be hit by a long turn or an idle terminal. Rejected: same bug, later.
- **Reap the crash class only on `.ended` (never on staleness).** Structurally immune to false reaps, but a genuine crash then leaves its journal open *forever*, and the ADR-0053 unclosed-session miner would route it to the operator — manual toil, against the everything-automatic rule. The 48 h grace keeps crash cleanup on the code path.
- **Self-healing reopen** (heartbeat detects its own journal was closed underneath it and un-ends it). Elegant, but with the grace in place a false close requires a forged `.ended` or a 48-hour-silent-yet-alive session that resumes — rare enough that the machinery isn't paid for. Revisit on recurrence (`add-structural-guard-on-recurrence`).
- **Heartbeat via `PostToolUse` instead of `PreToolUse`.** Equivalent signal; `PreToolUse` was chosen because the floor already carries PreToolUse infrastructure and the beat lands before a long-running tool call starts, which is the earlier of the two moments.

## Consequences

- A session in a long working turn now proves liveness every tool call (≤1 write/min); the session-74 false-reap class is structurally closed.
- A genuinely crashed session's journal stays flown-not-landed for up to 48 h before auto-closing — cosmetic only (rendered in-progress; nothing contends for it). Crashed *phantoms* likewise self-delete after the grace instead of immediately; clean-exit phantoms (the common kind) still delete at the next start.
- Every tool call spawns the hook's `python3` process (~0.1 s, parallel with `check-bash` on Bash calls). Accepted: same cost class the guards already established.
- The concurrent-elsewhere detector gets *more* accurate for free: a mid-turn session now looks live, so a competing same-repo start warns instead of proceeding.
- Fleet rollout owed: `push-substrate` ships `session.py`, `standard_check.py`, and regenerated settings; members read v1.3.0 `behind` until pushed. The kit template regen also caught it up on two floor hooks it had missed since sessions 64/66 (`standard_check.py --status`, `apply-briefs`).

## References

- [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) — the liveness sidecar this amends.
- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — the journal reaper whose crash-class semantics this amends.
- [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) — session 74's build, where the false reap fired live.
- Session 74 journal (`sessions/journal/20260718-2273.md`) — the incident record.
