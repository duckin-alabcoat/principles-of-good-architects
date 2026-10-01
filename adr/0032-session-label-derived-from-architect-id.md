# ADR-0032: Session-picker label is derived from `architect_id`, not a hand-set field

**Status:** Accepted
**Date:** 2026-06-21
**Deciders:** the operator, Federation Architect

## Context

`session.py` sets the Claude Code session-picker label at session start (e.g. `POGA·S44·Runner·<date>`). The label's prefix came from a free-text `session_name_prefix` field in each system's `session.config.json`, hand-filled per system: federation `POGA`, and a different hand-picked short code for each member. The bootstrap harness auto-derived it as the uppercase initials of the system name — for a hypothetical system `example app`, `EA`.

That free-text field is a footgun. It invites a value that violates the [ADR-0006](0006-naming-convention-corrected.md) naming convention, which defines exactly what an Architect is called (System ID, Architect ID `<system-id>-arch`, canonical Architect name) and warns against conflating those with the **orchestrator agent** or the **system**. In federation session 44 a member's migration brief embedded the member's orchestrator-agent codename as its `session_name_prefix` — the operator's codename standing in for the Architect — and the "fix" of the system's initials was no better: initials are the *system* abbreviation, still not the Architect. The label names the **Architect's** sessions, so it must be the Architect's standard identifier. A hand-typed abbreviation will keep drifting into the operator or system name; the convention already gives the correct value, so the field should not exist.

## Decision

**The session-picker label prefix is derived by `session.py` from `architect_id` — the ADR-0006 standard Architect identifier — and the free-text `session_name_prefix` config field is removed.**

`structural_name()` builds the label `<architect_id>·S<n>·<machine>·<date>` (e.g. `example-app-arch·S5·Laptop·<date>`). `architect_id` always carries the `-arch` suffix, so the label can never be read as the system or the orchestrator agent — the conflation ADR-0006 forbids is structurally impossible.

- `session.config.json` no longer carries `session_name_prefix` (federation copy + `bootstrap-kit/session.config.json` template). The kit's `<<SESSION_NAME_PREFIX>>` token and `bootstrap.py`'s initials-derivation are removed.
- `session.py` reads `architect_id` from config (it was already a required field) and no longer reads `session_name_prefix`.
- **Backward-compatible:** a config that still carries the old field is unaffected — `session.py` simply ignores it. Already-converged Architects (three at the time) need no config edit; they pick up the derived-label behavior when the federation substrate-push ships the new `session.py` ([ADR-0031](0031-delivery-integrity-self-contained-briefs.md)).

This is `code-for-mechanism-not-judgment` ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)) / `add-structural-guard-on-recurrence` applied to naming: the correct name is computed from the standard, not chosen per system, so it cannot be fat-fingered into a non-standard or conflated value. It instantiates the standard substrate's "same pipes everywhere" ([ADR-0023](0023-standard-operating-substrate.md)) — the label format is now identical and derived everywhere, with nothing per-system to get wrong.

## Alternatives Considered

- **Keep the field, fix the bad value (codename → initials).** Rejected — it leaves the footgun loaded; the initials are themselves a conflation (the system, not the Architect), and the next author re-introduces the problem.
- **Auto-derive a short abbreviation from `architect_id`** (e.g. initials → `EA`). Rejected — abbreviation *is* the conflation source; initials of `example-app-arch` reproduce the exact system-initials ambiguity. The full standard identifier is unambiguous.
- **Use the canonical Architect name** ("Example App Architect"). Workable, but ADR-0006 assigns `architect_id` to "references to the Architect role," which a session label is; the ID is also more compact and machine-glanceable.

## Consequences

- Every Architect's picker label changes to `<architect_id>·S<n>·…` (federation's own: `POGA·…` → `federation-arch·…`). Slightly longer, unambiguously the Architect.
- The free-text field, the kit token, and the `bootstrap.py` derivation are gone — one fewer per-system value to fill and to get wrong.
- Generated `STANDARD.md` (substrate description), `federation-arch.md` §7, `PROCESS.md` (token table), and the pre-convergence briefs that embed a `session.config.json` (three of them) are updated to drop the field; their payload `session.py` copies are refreshed to the deriving harness.
- Federation role doc → v2.14.0; bootstrap kit → v0.14.0.

## References

- [ADR-0006](0006-naming-convention-corrected.md) — the five-slot naming convention this enforces.
- [ADR-0023](0023-standard-operating-substrate.md) — standard operating substrate; the harness this changes.
- [ADR-0031](0031-delivery-integrity-self-contained-briefs.md) — substrate-push, the rollout path for the new `session.py`.
- federation session 44 (2026-06-21) — the codename/initials conflation that surfaced the footgun.
