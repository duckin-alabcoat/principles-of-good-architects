# ADR-0029: Receiving Architects auto-adopt federation edits at next startup

**Status:** Accepted
**Date:** 2026-06-21
**Deciders:** the operator, Federation Architect

## Context

Under [ADR-0013](0013-receipt-ritual.md) the receiving Architect's session-start receipt-ritual sweep **surfaces** each pending federation edit to the user and **gates per-edit** — the user confirms each one (or opts into "apply all pending") before it lands. The standard session-start step 7 encodes this: *"do not auto-apply — the user gates per-edit."*

In practice this produces exactly the friction the operator flagged (session 43): Architects were asking him whether to implement what was being rolled out to them, and he ruled that they should always adopt it at the next startup. Every Architect, every session with a pending edit, re-asks him whether to apply something he has **already approved**.

The key observation: **the user's approval already happened upstream.** A federation edit only exists because its principle / habit / standard-substrate change was Accepted into canon — and registry-Accept *is* the operator's explicit gate ([ADR-0009](0009-data-storage-mechanics.md) §promotion, [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md): the universal set is inherited, not re-negotiated per Architect). Re-gating the same change at each receiving Architect asks him to approve the same decision N more times. That is a redundant gate, and removing redundant gates on already-approved routine work is exactly [`routine-ops-autonomy`](../habits/master.md#routine-ops-autonomy) ([P10](../principles/master.md#p10--architect-owns-operational-substrate)).

## Decision

**A receiving Architect auto-applies its pending federation edits at session start, then reports what it applied — it does not ask the user to gate them.** The user's gate is upstream (registry-Accept); the receiving side is execution, and execution is the Architect's job.

The standard session-start receipt-ritual sweep changes from *surface-and-gate* to *apply-and-report*:

1. **Apply.** For each pending edit (FIFO by edit-id) whose `expected-base` matches the current role-doc version: apply the brief's `## After` content, bump the role-doc version per the brief, add the CHANGELOG entry per the brief's adoption note, and move the brief `pending/` → `applied/`. The application rides the session's normal commit.
2. **Report.** Name what was applied in the session-start summary (*"Applied N federation edit(s) this startup: …"*) — transparency at the conversation layer ([P7](../principles/master.md#p7--transparency-at-conversation-layer)), so the user sees it even though they didn't gate it. An empty inbox stays silent.

**Three carve-outs surface to the user instead of auto-applying** (the gate isn't gone where judgment is genuinely required — it's gone where it was redundant):

- **Version-drift mismatch.** If a brief's `expected-base` ≠ the current role-doc version, the `## After` anchor may no longer be valid; surface it (apply-anyway / redraft-needed / pause) rather than force a stale patch. ([ADR-0013](0013-receipt-ritual.md) conflict handling, unchanged.)
- **Manual-apply briefs.** A brief that is not a self-contained role-doc edit — substrate installs, runtime migrations, guard wiring (e.g. a brief that migrates a member onto the reference harness) — declares **`Apply: manual`** in its header and surfaces for a working session. Routine canon/habit adoption (a clean `## After` block + version bump + CHANGELOG) is the auto-apply default; multi-step substrate work is opt-out.
- **Application failure.** If the `## After` block can't be applied cleanly (anchor not found, file moved), surface the failure; never half-apply.

This **amends [ADR-0013](0013-receipt-ritual.md)**: the receipt ritual's transport, format, directory lifecycle (`pending`/`applied`/`rejected`/`withdrawn`), and provenance are unchanged; only the *gate* moves from per-edit-at-the-target to once-at-registry-Accept. It binds every Architect via the standard session-start section ([ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md)); the Federation Architect, as a participant ([ADR-0003](0003-federation-architect-is-a-participant.md)), auto-adopts into its own role doc the same way.

## Alternatives Considered

- **Keep per-edit gating (status quo).** Rejected — it is the friction the operator named; it re-asks for approval already given at registry-Accept.
- **Auto-apply *everything*, including migrations.** Rejected — a runtime migration or multi-file substrate install isn't a deterministic text patch; auto-running it at startup risks a broken Architect. Hence the `Apply: manual` carve-out.
- **Auto-apply but still pause for a yes/no "I applied these, ok?"** Rejected — that's the same interruption in a thinner coat. Report-after (P7) gives the user full visibility and the git trail to revert; it doesn't stop the session to collect a rubber-stamp ([`no-question-without-a-tradeoff`](../habits/master.md#no-question-without-a-tradeoff)).
- **A code harness that applies briefs deterministically.** Attractive (P15) and compatible with this ADR, but brief application still needs a small read (locate the `## After` anchor, confirm base version). Left as a future option; this ADR sets the behavior, not the implementation.

## Consequences

- **The redundant gate is gone.** Shipped canon lands at each Architect's next startup with no user prompt; the user approves once (at Accept) and sees an applied-report after.
- **Faster, more reliable propagation.** The lag between Accept and per-Architect adoption collapses to "their next session." This also makes standardizing *new* artifacts (e.g. the roadmap deliverable) low-friction — the briefs just land.
- **Briefs must self-classify.** The brief format gains an optional `Apply: manual` header (default: auto). Migration briefs must set it; the Federation Architect sets it when drafting substrate/migration briefs. *(A brief that migrates a member's harness is multi-step substrate work → `Apply: manual`.)*
- **A new structural-guard candidate** ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)): if auto-apply is ever code-harnessed, the harness enforces the three carve-outs (base-version match, manual flag, clean anchor) so a malformed brief can't silently mis-apply.
- **Standard-section change ships fleet-wide.** `standard-source.md` step 7 is rewritten; `STANDARD.md` + the kit copy regenerate; every Architect inherits it by injection, and existing Architects pick it up at their next startup (auto-adopting the very change that enables auto-adopt).

## References

- [ADR-0013](0013-receipt-ritual.md) — the receipt ritual this ADR amends (gate only; transport/format/lifecycle unchanged).
- [ADR-0009](0009-data-storage-mechanics.md) / [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) — registry-Accept is the upstream user gate; the universal set is inherited, not re-negotiated per Architect.
- [ADR-0024](0024-standard-role-doc-section-is-generated-and-injected.md) — the standard session-start section this ships in.
- [`routine-ops-autonomy`](../habits/master.md#routine-ops-autonomy) / [`no-question-without-a-tradeoff`](../habits/master.md#no-question-without-a-tradeoff) — the habits this instantiates (don't re-ask for already-approved routine work).
- Federation session 43 (2026-06-21) — the operator ruled that receiving Architects always adopt rolled-out edits at the next startup.
