# ADR-0055: Lazy session start — mutating start work waits for the first heartbeat

**Status:** Accepted
**Date:** 2026-07-18
**Deciders:** the operator (approved the lazy-start decision, session 74); Federation Architect (design + build, handed to the harness-alone reaper-fix session)

## Context

POGA supports launching sessions from the Claude desktop app. The phantom-SessionStart launcher is that app: the **Claude desktop app itself** pre-spawns short-lived headless CLI sessions per project around new-session activity and discards them — every `claude` process a child of `Claude.app` via `CLAUDE_CODE_ENTRYPOINT=claude-desktop`; no LaunchAgents, cron, or scheduled tasks involved. the operator's framing correction stands as the doctrine: the app pre-spawning is *normal, universal behavior* — **the harm is ours**, because our `SessionStart` hook does heavy mutating work on every start, assuming every start is a real session. Each phantom start allocated a journal, compiled the handoff, ran the reaper ([ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md)) — and could falsely close a live session's journal (the [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) reaper hardening removes the false-close; this ADR removes the phantom's ability to run any of it).

The discriminator is clean: **a real session produces a heartbeat** (a tool call, or the Stop hook after its first turn); **a phantom fires SessionStart + SessionEnd and nothing in between**.

## Decision

`session.py start` on the hook path (a `session_id` on stdin) becomes **lazy**:

- **At SessionStart (read-only + ephemera):** git fetch/diagnose/ff-pull ([ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) — kept so context is fresh; ff-only on a clean tree, idempotent, so a phantom running it is harmless), context injection, banner staging, the `.live` liveness marker, and a new **`.session-state/<csid>.pending-start` marker** carrying the start facts (durable id, start time, ordinal preview, machine, role-doc version, base commit).
- **At the first heartbeat (tool call or Stop — `_complete_lazy_start`):** freeze-migrate, **reap**, write this session's **journal backdated to the recorded start**, recompile the handoff/STATUS views, and perform the **deferred auto-push** (`_push_if_ahead` — the one outward act, now impossible for a phantom). Idempotent under concurrent duplicate fire (atomic writes; the journal write is skipped if the file exists, which also protects a narrative the Architect already wrote at the path the start block names); the marker is consumed **last**, so a failed attempt retries on the next beat. `end` also completes a still-pending start before closing, covering a real session that ends inside its first turn.
- **Phantom debris** (a `.pending-start` whose session has a `.ended` and never beat) is swept at the next real session's first beat; unexplained markers are kept until the [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) crash grace passes (a young marker may be a concurrent session still in its first turn).
- **Manual runs stay eager.** No hook stdin → no csid to key a marker → the journal is written immediately, exactly as before. The legacy non-Claude `stamp` binding is untouched.

Because the [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) all-tools `PreToolUse` heartbeat ships in the same release, materialization normally lands at the session's **first tool call — seconds into turn 1** — not at the end of the first turn; the "journal from turn 1" durability window of [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) C2 narrows only to "journal from first activity" (accepted by the operator).

The `lazy-start` capability joins standard-substrate release **v1.3.0** alongside `heartbeat-on-tooluse` (folded into the same release: v1.3.0 had not rolled out anywhere yet).

## Alternatives Considered

- **Report the pre-spawn upstream as an Anthropic bug and wait.** Rejected by the operator's analysis: the app behavior is universal; a harness that mutates on every start is the defect. Fix ours.
- **Heuristic phantom detection at start** (process ancestry, env sniffing, timing). Fragile, launcher-specific, and still mutates before deciding. The heartbeat discriminator needs no heuristic: phantoms are no-ops *by construction*.
- **Defer the ff-pull too** (fully read-only start). Would let the model orient on stale trunk — the ADR-0035 injection would inject yesterday's handoff. A phantom's ff-only pull on a clean tree is the same state any fetch produces; kept at start.
- **Materialize only at first Stop** (the decision's literal wording). Strictly dominated by "first heartbeat from either hook": the PreToolUse beat arrives seconds into turn 1, restoring nearly all of the turn-1 durability window at no cost — phantoms produce neither signal.

## Consequences

- Phantom SessionStarts leave only gitignored ephemera (`.live`, `.pending-start`, `.ended`, a banner file) — no journal, no ordinal, no reaper run, no compile, no push. The class dies at the source; ADR-0054's grace remains as defense-in-depth for real concurrent sessions.
- The first tool call of each session bears the materialization cost (~1 s of local writes; the deferred push when prior commits are owed). One-time per session.
- A real session that crashes before its first heartbeat leaves no journal — correct: it had no narrative to lose; its marker sweeps after the grace.
- The reaper now runs at first-activity rather than at start; its `reaped:` line no longer appears in the orientation block (dispositions happen silently at a beat) — the `prior:` flown-not-landed count remains the start-block signal.
- Fleet rollout rides the same v1.3.0 `push-substrate` as ADR-0054 (session.py + standard_check.py + settings).

## References

- [ADR-0054](0054-heartbeat-on-tool-use-and-crash-reap-grace.md) — companion: heartbeat on tool use + crash-reap grace.
- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) / [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) / [ADR-0035](0035-startup-turn-budget-inject-and-git-diagnosis.md) — the amended start/liveness/reap machinery.
- `comms/2026-07-18-phantom-sessions-desktop-app.md` — the comms note recording the phantom-launcher finding.
