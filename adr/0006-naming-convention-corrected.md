# ADR-0006: Naming convention — corrected to include orchestrator-agent slot

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect
**Supersedes:** [ADR-0002](0002-naming-convention.md)

## Context

ADR-0002 established a four-slot naming convention with a "Product name" column populated by codenames like Basil, Hearth, and Cairn. Shortly after acceptance, the user issued a correction: those codenames are *not* the products. They are **orchestrator agents** that live inside the products. The product itself is the functional system — the recipe box, the home-hub system, the trail-log system — and the orchestrator agent (Basil, Hearth, Cairn) is one component within it.

This isn't a cosmetic distinction. The collapsed framing in ADR-0002 caused real confusion in conversation ("Basil the system" vs. "Basil the agent") and would have propagated forward into every artifact the Federation Architect produced — file headers, prose, the master principles doc — corrupting the model.

The core of ADR-0002 (suffix `-arch`, function-based Architect names, kebab-case IDs) remains sound. What's wrong is the slot taxonomy: there are five things to name, not four. The Product Name column in 0002 was actually conflating two distinct things (system and orchestrator agent).

## Decision

**Five-slot model.** Replaces the four-slot model from ADR-0002.

| Slot | What | Example |
|---|---|---|
| System (product) | The functional thing being built. Named by what it does. | recipe box |
| Orchestrator agent (optional) | Codename for the central agent inside the system. Not every system has one. | Basil |
| Canonical Architect name | `<Function> Architect`, title case. Used in prose. | Recipe Box Architect |
| System ID | kebab-case function. Used in scope tags and references to the system. | `recipe-box` |
| Architect ID | `<system-id>-arch`. Used in filenames, provenance lines, references to the Architect role. | `recipe-box-arch` |

**Architect names derive from function, not from any codename.** Renaming Basil to Stella tomorrow doesn't change the Recipe Box Architect's identity.

**Suffix `-arch` retained** from ADR-0002 for the same reasons (no filename interference, self-explanatory cold, extensible to other role suffixes).

**Portfolio mapping (as of 2026-05-21):**

| System | Orchestrator agent | Canonical Architect | System ID | Architect ID |
|---|---|---|---|---|
| recipe box | Basil | Recipe Box Architect | `recipe-box` | `recipe-box-arch` |
| home-hub | Hearth (TBC) | Home Hub Architect | `home-hub` | `home-hub-arch` |
| trail-log | Cairn (Chief Trail Agent) | Trail Log Architect | `trail-log` | `trail-log-arch` |
| campaign-tracker | TBC | Campaign Tracker Architect | `campaign-tracker` | `campaign-tracker-arch` |
| arcade | TBC | Arcade Architect | `arcade` | `arcade-arch` |
| federation | (none — internal-only system) | Federation Architect | `federation` | `federation-arch` |

**Three slots marked TBC** require the user's confirmation: whether Hearth is the home-hub orchestrator (likely), and whether Campaign Tracker and Arcade have named orchestrator agents at all.

**Scope tags in producer files** use the system ID, unchanged from ADR-0002: `scope: recipe-box-specific`, `scope: architect-general`, `scope: domain-general`.

## Alternatives Considered

- **Amend ADR-0002 in place.** Rejected — violates ADR-0001's immutability rule. Even for a same-day correction, supersession preserves the historical record of what we initially thought.
- **Treat orchestrator-agent name as belonging in the "product name" slot of ADR-0002 with a note.** Rejected — the original column was *named* "Product name," which is exactly what the codename is *not*. Renaming the column without changing the model would leave the underlying confusion intact.
- **Add the slot but make it required.** Rejected — not every system has a named orchestrator agent. The Federation Architect itself doesn't (no user-facing orchestrator). Marking the slot optional preserves correctness across the portfolio.

## Consequences

- ADR-0002 is superseded but not deleted. It remains as historical record with its status flipped.
- All artifacts produced after this ADR use the five-slot model.
- The mapping table has three TBC entries; the user should confirm them before any artifact references those orchestrator names with certainty.
- The founding brief uses "Basil" as shorthand for the recipe box system throughout. The brief is preserved as-is (it's the user's founding document, not subject to retroactive editing), but the Federation Architect uses the corrected model going forward. Any artifact derived from the brief should normalize the names.
- ADR-0002's "Consequences" section noted that future role classes (Auditor — ADR-0004 pending) follow the same suffix pattern. That carries forward unchanged.

## References

- [ADR-0001: Record federation decisions as ADRs](0001-use-adrs.md) — immutability rule that drives supersession instead of in-place edit.
- [ADR-0002: Architect and system naming convention](0002-naming-convention.md) — superseded by this ADR.
- [architect-learnings-federation-brief.md](../architect-learnings-federation-brief.md) — founding brief; uses pre-correction naming.
- Memory: `convention_naming.md` (operational summary, kept in sync with this ADR), `naming_architect_vs_product.md` (behavioral rule about not conflating).
