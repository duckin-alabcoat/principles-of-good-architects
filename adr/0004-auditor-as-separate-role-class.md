# ADR-0004: Auditor as a separate role class from Architect

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect

## Context

During the bootstrap conversation, the user described how the Arcade v1 Architect role doc "got corrupted" — failure mode unknown, but the doc had degraded to the point of needing a teardown. He spun up "Tinkerer" as a cold-context reviewer to come in with no prior knowledge and recommend changes. Then, instead of handing recommendations back to a fresh Document Architect, the user let Tinkerer keep building.

The result — `Tinkerer Architect` — is an awkward fusion of two roles with **opposite design goals**:

- **The cold reviewer** prizes *ignorance*. Its value is the outside view, unburdened by the system's own narrative. Single-engagement, mission-briefed, no persistent state.
- **The Architect** prizes *accumulated context*. Its value is long-term judgment informed by history. Long-lived, system-bound, persistent state.

You cannot optimize for both simultaneously. The role-class confusion explains why Tinkerer Architect doesn't fit the convention and why splitting it is the right move (see ADR-0005).

Beyond the Tinkerer-specific resolution, the cold-reviewer pattern is **generally useful** across the portfolio. Any Architect can hit the limits of its own accumulated context and benefit from a cold pass — "find LLM uses that should be code," "find efficiencies," "review the role doc itself for drift." This pattern needs a first-class name, taxonomy slot, and naming convention.

## Decision

**Auditor is a distinct role class from Architect.** It is not "an Architect with an unusual scope."

### Properties of an Auditor

| Property | Auditor | (Architect for contrast) |
|---|---|---|
| Context model | Cold — no prior knowledge of target system | Warm — full history of target system |
| Lifecycle | Single engagement | Long-lived, persistent |
| Briefing | Per-engagement mission ("find X," "review Y") | Standing mission encoded in role doc |
| State between engagements | None — fresh instance each time | Accumulated across sessions |
| Target | Any system, by engagement | One specific system |
| Output | Recommendations report | Ongoing role-doc edits + system changes |

### Naming and IDs

- **Role class name:** `Auditor`. Used in title case in prose.
- **Engagement identifier:** `<system-id>-aud-<YYYY-MM>[-<slug>]` (date scope = month at minimum; add a slug if multiple engagements in the same month). Examples:
  - `arcade-aud-2026-05`
  - `recipe-box-aud-2026-07-llm-vs-code`
  - `federation-aud-2026-09-role-doc-drift`
- **Auditor short ID per engagement** is the engagement identifier itself. There is no "the Auditor" — only specific engagements.

### Federation treatment

- Auditor learnings (how to brief one well, which missions yield value, failure modes) live in a **separate tier** of the master principles doc — alongside `architect-general` and `domain-general`, not merged in. Suggested tier name: `auditor-general`.
- The Federation Architect is in scope for Auditor engagements just like any other Architect (see [ADR-0003](0003-federation-architect-is-a-participant.md)).
- Auditor engagement reports may seed entries in the target system's `architect-learnings.md` — but this is a deliberate hand-off by the user, not automatic. The Auditor itself doesn't write to producer files.

### What an Auditor is NOT

- **Not a permanent reviewer.** Each engagement is fresh. A new "find efficiencies in recipe-box" engagement six months from now is `recipe-box-aud-2026-11-...`, a separate instance, not a continuation.
- **Not an Architect with extra duties.** Fusing the two roles is exactly the Tinkerer mistake. If the user wants ongoing review, that's an Architect's job (the system's own Architect, or the Federation Architect for cross-system observation). Auditor stays single-engagement.
- **Not a federation function.** The Federation Architect can *commission* Auditor engagements and can incorporate their findings into the master principles doc, but the Auditor is not part of the federation infrastructure. It's a portable role pattern.

## Alternatives Considered

- **Treat cold review as a mode of an Architect ("Architect in audit mode").** Rejected. The two modes want opposite context models. Pretending one role can switch between them leads to exactly the Tinkerer-style drift, where "audit mode" becomes "build mode" without anyone noticing.
- **Don't formalize the pattern; let any Architect spin up cold passes ad-hoc.** Rejected. The pattern is valuable enough to deserve a name, a naming convention, and a dedicated tier in the master principles doc. Ad-hoc usage loses provenance and reusability.
- **Auditor learnings merged into `architect-general` tier.** Rejected. The lens is different enough that mixing dilutes both. An "auditor-general" tier surfaces *how to use Auditors* as its own subject of study.
- **Different role-class name** (Reviewer, Inspector, Outside Architect). Considered. Rejected — "Auditor" carries the right industry connotations: independent, no advocacy, mission-briefed, recommendations-only. "Reviewer" is too soft; "Outside Architect" conflates with Architect; "Inspector" connotes flaw-finding too narrowly.

## Consequences

- The naming convention from [ADR-0006](0006-naming-convention-corrected.md) extends with the `-aud` suffix and engagement-identifier format described above.
- ADR-0005 (Tinkerer split) is the first application: Arcade Architect is re-established, and Tinkerer's cold-reviewer work is preserved as the seed for the Auditor pattern.
- When the master principles doc is created, it needs three tiers minimum: `architect-general`, `domain-general`, `auditor-general`. (System-specific entries are filtered out before clustering, per the founding brief.)
- A future ADR may specify a standard Auditor briefing template — at minimum: target system, mission statement, explicit out-of-scope items, deliverable format. Deferred until first real Auditor engagement is commissioned.
- The Federation Architect itself can be the target of Auditor engagements. This is feature, not bug — closes the loop on the recursive principle.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — Auditor engagements can target the Federation Architect.
- [ADR-0005: Tinkerer Architect split](0005-tinkerer-split.md) — first application of this role class.
- [ADR-0006: Naming convention — corrected](0006-naming-convention-corrected.md) — naming model that this ADR extends.
- Memory: `role_class_auditor.md` — operational summary.
