# ADR-0005: Split Tinkerer Architect into Arcade Architect + Auditor pattern

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect

## Context

The Arcade system had a v1 Architect role doc that, per the user, "got corrupted." The exact failure mode is not documented (a separate observation worth tracking — see Consequences), but the result was that the role doc had degraded enough to need a teardown rather than incremental repair.

The user's response was to spin up a new role called **Tinkerer** with a specific brief: review the system with no prior context and recommend changes. Cold-context, mission-briefed, no accumulated state. This is exactly the Auditor pattern formalized in [ADR-0004](0004-auditor-as-separate-role-class.md), though it predates the formalization.

Rather than hand Tinkerer's recommendations to a fresh Document Architect, the user let Tinkerer keep building. Tinkerer thus drifted from cold reviewer into de-facto Architect. The current `Tinkerer Architect` entity is a fusion of:

1. **A cold reviewer** (the original Tinkerer engagement) — value-source: ignorance.
2. **A de-facto Arcade Architect** (everything Tinkerer did after the review phase) — value-source: accumulated context.

These two roles want opposite things (see ADR-0004 for the full taxonomy). Leaving them fused means neither does its job cleanly: the Architect side has weird origin assumptions, and the reusable Auditor pattern is locked inside a one-off entity.

## Decision

**Split Tinkerer Architect into two distinct entities, with both pieces preserved.**

### Part A: Re-establish the Arcade Architect

- Create a fresh **Arcade Architect** role doc, following the naming convention from [ADR-0006](0006-naming-convention-corrected.md): canonical name "Arcade Architect," system ID `arcade`, Architect ID `arcade-arch`.
- **Merge Tinkerer's accumulated Arcade work** into the Arcade Architect's domain. Specifically: any system changes, design decisions, or operational knowledge Tinkerer produced after its initial review phase belongs to Arcade Architect now. The Arcade Architect inherits the system, not Tinkerer.
- The new role doc should be written from the current state of the Arcade system, not from the corrupted v1. Past learnings worth preserving get re-encoded explicitly.

### Part B: Extract the cold-reviewer pattern as the Auditor seed

- The **review-phase behavior** of Tinkerer — the part that came in cold and recommended changes — is the proto-instance of the Auditor pattern. It is not bound to Arcade; it is portable across the portfolio.
- Formalized in [ADR-0004](0004-auditor-as-separate-role-class.md). Future cold-context reviews use the Auditor naming convention (`<system-id>-aud-<date>[-<slug>]`) and engagement framework.
- The original Tinkerer engagement, retroactively, becomes **`arcade-aud-2026-XX`** (date TBC based on when the original review happened). Any artifacts from that engagement (review notes, recommendations) should be re-tagged accordingly.

### Disposition of the `Tinkerer Architect` entity

The fused entity is **retired**. Its work is split between Parts A and B above. No future references to "Tinkerer Architect" — the name is associated only with the historical fusion mistake, and the brief's mention of it is now a known anachronism.

## Alternatives Considered

- **Keep Tinkerer Architect as a special-case Architect.** Rejected. Special cases multiply. Today it's Tinkerer; tomorrow another corruption + cold review produces "Tinkerer2-Architect." Better to name the pattern (Auditor) once.
- **Discard the Auditor pattern; treat Tinkerer as a one-off historical anomaly.** Rejected. Cold review is too valuable a pattern to lose. The user explicitly named it as something they want to preserve: "the idea of an outside architect to review is one I want to keep."
- **Re-establish Arcade Architect but don't merge Tinkerer's build-phase work.** Rejected. That work represents real system state. Discarding it forces re-derivation. Merging preserves continuity for the Arcade system itself.
- **Have the Arcade Architect inherit Tinkerer's identity / history wholesale.** Rejected. Inheriting the cold-reviewer framing pollutes the Architect role. The new Arcade Architect should be written from the system's current state, with Tinkerer's outputs as inputs, not as identity.

## Consequences

- **Arcade Architect needs to be stood up.** This is downstream work, not done in this ADR. It's the first opportunity to apply the federation's principles end-to-end: write a role doc following convention, instantiate ADRs for its own decisions, set up its `architect-learnings.md` producer file.
- **The Auditor pattern has its first artifact:** the historical Tinkerer engagement, retroactively named `arcade-aud-2026-XX`. When the Federation Architect later compiles `auditor-general` principles, this engagement is the first data point — what worked, what failed, what the cold-review-to-build drift teaches us.
- **The "Architect role doc corruption" failure mode is a flagged open question.** the user has not characterized what "corruption" meant for Arcade v1. The Federation Architect should track this — it may be a generalizable failure mode (drift, internal contradictions, accumulated cruft) that the federation is well-positioned to detect early in other Architects' role docs. Deferred to a future ADR or principle.
- **The founding brief's portfolio list includes "Tinkerer Architect."** That mention is now historically accurate but operationally stale. The brief is preserved as-is; the canonical portfolio table lives in ADR-0006.
- **Decoupling unlocks reuse.** Any system can now host an Auditor engagement (`<system-id>-aud-<date>`) without inventing a new role each time.

## References

- [ADR-0003: Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — Auditor can target the Federation Architect.
- [ADR-0004: Auditor as a separate role class](0004-auditor-as-separate-role-class.md) — defines the role class this ADR draws on and seeds.
- [ADR-0006: Naming convention — corrected](0006-naming-convention-corrected.md) — supplies the Arcade Architect's IDs and the Auditor engagement-ID format.
- [architect-learnings-federation-brief.md](../architect-learnings-federation-brief.md) — founding brief; references "Tinkerer Architect" in the portfolio list as a known anachronism.
- Memory: `project_tinkerer_history.md`, `role_class_auditor.md`.
