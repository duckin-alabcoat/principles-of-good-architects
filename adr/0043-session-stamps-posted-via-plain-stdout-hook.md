# ADR-0043: Session stamps are posted to the user via a plain-stdout hook, not systemMessage

**Status:** Accepted
**Date:** 2026-07-12
**Deciders:** the operator, Federation Architect
**Accepted:** 2026-07-12 (session 57) — the operator's explicit direction: the user-visible stamps at session start and end were inconsistent and must become reliable.

## Context

Contract obligation **C2 — State continuity** ([ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md)) requires a monotonic session stamp at each session boundary. The *durable* half works: `session.py start` writes the start stamp into `session-handoff.md`; `session.py end` writes the end stamp. But the **user-visible** half — the operator actually *seeing* a start and an end stamp each session — was unreliable, which is what he reported.

Root cause, start side: `start` runs as a `SessionStart` hook that must emit **pure JSON** (`hookSpecificOutput` carries `sessionTitle` + the `additionalContext` canon/standard/handoff injection). Its intended announce channel, `hookSpecificOutput.systemMessage`, is **broken upstream** (anthropics/claude-code [#9090](https://github.com/anthropics/claude-code/issues/9090), [#50542](https://github.com/anthropics/claude-code/issues/50542), [#15344](https://github.com/anthropics/claude-code/issues/15344); unresolved at the time of writing). `additionalContext` is **model-visible only**. So the live path had fallen back to *the Architect relaying the announce in prose as session-start step 3* — agent goodwill, which agents (this one included) skip by going straight to the task. Sessions 53/54/55 also show the end-side failure: sessions that orphaned never ran `end`, so no end stamp was ever posted.

Per [ADR-0041 §4](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) (prefer structural enforcement over goodwill), the posting must not depend on the model.

## Decision

Post both stamps deterministically, over the one SessionStart channel that renders to the user — **plain stdout** — and stop relying on `systemMessage` or the agent.

1. **Start stamp → a second `SessionStart` `startup` hook, `session.py announce`.** `start` stages the user-facing banner into the gitignored `.session-state/announce.txt`; `announce` (which runs immediately after `start` in the same hook array) prints it to plain stdout and consumes the file. Splitting it into a second hook keeps `start`'s own stdout pure JSON — a stray print there would corrupt the parse. Plain stdout from a SessionStart hook renders to the user (verified: the federation's own `apply`/`gather`/`reconcile` status hooks surface this way every session).

2. **End stamp → `session.py end` prints the end banner itself.** `end` runs as the Architect's Bash command, so its stdout is already user-visible; it now emits the same one-line banner shape as start.

3. **Consistent banner shape at both boundaries:** `──── Session N start · <Architect> vX.Y.Z · <Machine> · <stamp> ────` and the `end` sibling with `· <duration>`.

4. **Orphan end stamps are surfaced retroactively.** When `start` auto-closes an orphaned prior session ([ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md)), the staged banner leads with that session's *end* stamp — so a session that never ran its own close ritual still gets a visible end stamp at the next start.

5. **`systemMessage` stays wired** in `_emit_start` (parked-correct-code) so the redundant channel self-heals if the upstream bug is fixed; it is no longer load-bearing.

6. **Non-Claude binding:** `session.py stamp` (the [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) runtime-agnostic start) and `end` both print the same banner to stdout, so a non-Claude runtime that calls them in its session ritual gets the same visible bookends. Wiring them into that ritual is the binding's job.

## Alternatives Considered

- **Keep relying on `systemMessage`.** Rejected — broken upstream, out of our control, and the failure the operator reported.
- **Keep the prose relay as the primary path (strengthen the instruction).** Rejected — it is agent goodwill; the recurrence is exactly what [ADR-0041 §4](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) says to replace with a structural enforcement point.
- **Have `start` print the banner alongside its JSON.** Rejected — mixed stdout corrupts the hook's JSON parse; hence the sibling-hook split.
- **Post the end stamp from a `SessionEnd` hook.** Rejected — `SessionEnd` stdout is not reliably user-visible and its `systemMessage` is the very thing #9090 broke; the Architect-run `end` command already puts the banner on screen.

## Consequences

- Federation `session.py` gains `cmd_announce` + banner staging; `cmd_end` prints the banner; `standard-settings.json` (the floor) gains the second `startup` hook, so every generated `.claude/settings.json` carries it. Kit settings template regenerated.
- `standard-source.md` → `STANDARD.md`: session-start step 3 becomes "the harness posts it, don't relay"; session-end step 7 becomes "`end` prints it." Injected to every Architect at its next session.
- Covered by `tests/test_session.py` (banner helpers, `announce` roundtrip + consume, orphan-end surfacing, `end` banner).
- Ships to the Claude fleet via `push-substrate.py` (`session.py` + regenerated `settings.json`); fresh Architects via the kit.
- No principle/habit adoption event — federation-side substrate mechanizing C2's visible half. Self-promotion guard N/A.

## References

- [ADR-0041](0041-runtime-agnostic-substrate-floor-contract-vs-binding.md) — C2 State continuity; §4 prefer structural enforcement over goodwill.
- [ADR-0036](0036-session-liveness-sidecar-and-orphan-auto-triage.md) — orphan auto-triage; the retroactive end stamp rides its auto-close.
- [ADR-0020](0020-session-rituals-are-a-code-harness.md) — session rituals as a code harness.
- [ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md) — settings floor + generation the new hook rides.
