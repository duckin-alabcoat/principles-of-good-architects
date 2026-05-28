# ADR-0003: The Federation Architect is a federation participant

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect

## Context

The founding brief frames the Federation Architect as the entity that *operates on* the other Architects — reading their learnings, distilling principles, proposing role-doc edits back. That framing implicitly positions the Federation Architect as outside the system it federates over.

The user clarified the intent during bootstrap: "whatever we make for the architects, you'll follow it as well." The Federation Architect isn't a privileged observer. It's another Architect — one whose system happens to be the federation infrastructure itself. Same standards, same artifacts, same lifecycle obligations.

This matters for several concrete reasons:

1. **Self-consistency.** A federation that ships principles to other Architects but doesn't follow them is incoherent. If the federation distills "every Architect should record decisions as ADRs," the Federation Architect must already be doing so. Otherwise the principle is unenforceable rhetoric.

2. **Learning channel.** The Federation Architect generates its own learnings — about federation itself, about cold-context review, about cross-system synthesis. Those learnings are useful inputs to the master principles doc on equal footing with any other system's. Without a producer file of its own, that signal is lost.

3. **Failure-mode parity.** The Federation Architect can suffer the same degradation modes as any other Architect role doc — drift, accumulated contradictions, "corruption" (cf. ADR-0005's Arcade v1 story). It needs the same hygiene practices: a versioned role doc, an ADR trail, an explicit set of constraints it operates under.

4. **No special exceptions.** If a principle ships and the Federation Architect were exempt, the exemption would itself need justification every time. Default participation removes that overhead.

## Decision

**The Federation Architect is a first-class participant in the federation it operates, not an outside observer.**

Concretely:

- **It has its own role doc** at the root of this project — [federation-arch.md](../federation-arch.md). Versioned, edited like any other Architect's role doc, subject to the same approval gates from the user.
- **It has its own learnings file** — when entries start being written portfolio-wide, the Federation Architect maintains its own `architect-learnings.md` for federation-specific observations (how to brief a synthesis pass, conflict-resolution patterns, where the federation itself drifts).
- **It records decisions as ADRs** — already established in [ADR-0001](0001-use-adrs.md), reinforced here.
- **Cross-system principles apply to it** unless the principle has an explicit applicability constraint that excludes it. The default is inclusion; exclusion must be argued in the principle's metadata.
- **It is subject to Auditor review** (when the Auditor pattern is in place — see ADR-0004 pending). A cold-context reviewer can be briefed to audit the Federation Architect itself, on equal terms with any other Architect.

The Federation Architect's `system` slot in the naming convention is `federation`; its `Architect ID` is `federation-arch`; the role doc lives at `federation-arch.md` in this project's root.

## Alternatives Considered

- **Federation Architect operates outside the federation it manages.** Rejected. Creates two-tier system, hides federation-specific learnings, allows the federation to ship principles it doesn't itself hold.
- **Federation Architect participates partially — has a role doc but no learnings file.** Rejected. The learnings file is exactly the channel by which federation-specific observations enter the master principles doc. Without it, the recursion is incomplete.
- **Defer the recursive principle until federation infrastructure is more mature.** Rejected. Establishing the principle is cheaper now than retrofitting it later. Every artifact written without it baked in is a future cleanup task.

## Consequences

- The Federation Architect's role doc ([federation-arch.md](../federation-arch.md)) is a required artifact, not optional. Written immediately after this ADR is accepted.
- When the master principles doc starts shipping principles, the Federation Architect receives the same role-doc edits any other Architect would. The user approves them the same way.
- Auditor engagements (ADR-0004 pending) can target the Federation Architect.
- Any future structural decision must be checked against this principle: "does this rule apply to the Federation Architect too? If not, why is it exempt?"
- The naming convention's `federation` row in the portfolio table is operational, not symbolic. Federation is a system, the Federation Architect is its Architect.

## References

- [ADR-0001: Record federation decisions as ADRs](0001-use-adrs.md)
- [ADR-0006: Naming convention — corrected](0006-naming-convention-corrected.md)
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — extends this ADR's recursive-participation scope to cover **universal habits** in addition to principles. Per ADR-0008 §"Scope of ADR-0003": "Universal habits should also apply to Federation Architect by the same logic — the federation deploys them to every participating Architect, and Federation Architect is a participant." Preferences propagate by user identity and need no extension.
- [architect-learnings-federation-brief.md](../architect-learnings-federation-brief.md) — founding brief; implicitly framed the Federation Architect as external. This ADR re-grounds it.
- [federation-arch.md](../federation-arch.md) — the Federation Architect's role doc, instantiating this principle.
