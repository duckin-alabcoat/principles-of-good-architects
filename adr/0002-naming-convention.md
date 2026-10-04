# ADR-0002: Architect and system naming convention

**Status:** Superseded by [ADR-0006](0006-naming-convention-corrected.md)
**Date:** 2026-05-21
**Deciders:** the operator, Federation Architect

> **Historical note (2026-05-21, same day as acceptance):** The "Product name" column in this ADR conflated two distinct things — the system (e.g., a hypothetical example assistant) and the orchestrator agent inside it (e.g., a hypothetical codename `Nimbus`). the operator corrected this shortly after acceptance. ADR-0006 introduces a fifth slot to disambiguate. The core conventions in this ADR (suffix `-arch`, function-based Architect names, kebab-case IDs) carry forward unchanged in 0006.

## Context

the operator's portfolio includes multiple AI-assisted systems — several at the time, some fronted by a named orchestrator agent, and likely more. Each system has its own Architect — a long-lived collaborator role doc that builds and maintains it.

The Federation Architect needs consistent identifiers for both systems and Architects across artifacts (filenames, scope tags, provenance lines in the master principles doc, prose references). Two ambiguities surfaced early in the bootstrap conversation:

1. **Architect vs. product naming.** the operator corrected an early conflation: an orchestrator codename (say, a hypothetical `Nimbus`) is the *product*, not the Architect. "Nimbus's Architect" is awkward and couples role identity to a product name that may change.
2. **System vs. Architect short ID.** A bare token like `example-assistant` is ambiguous — does it mean the system or its Architect? In provenance lines and prose references with no artifact suffix, the disambiguation must live in the ID itself.

## Decision

**Four-slot convention:**

| Slot | What | Example |
|---|---|---|
| Product name | Friendly codename, free-form, used conversationally | Nimbus (hypothetical) |
| Function | What the product does, plain English | example assistant |
| Canonical Architect name | `<Function> Architect`, title case, used in prose | Example Assistant Architect |
| System ID | kebab-case function, used in scope tags and references to the system | `example-assistant` |
| Architect ID | `<system-id>-arch`, used in filenames, provenance, references to the role | `example-assistant-arch` |

**Architect names derive from function, not product.** A product rename does not churn the Architect's identity.

**Suffix `-arch` disambiguates Architect IDs from system IDs.** Prefix forms (`a.federation`) were considered and rejected.

**Portfolio mapping.** Every registered system gets one row in this shape (the rows themselves live in the portfolio registry, not in this ADR; the examples are hypothetical):

| Product | Canonical Architect | System ID | Architect ID |
|---|---|---|---|
| Nimbus | Example Assistant Architect | `example-assistant` | `example-assistant-arch` |
| (this system) | Federation Architect | `federation` | `federation-arch` |

Orchestrator codenames are free-form, so they never collide with the *Architect* suffix.

**Scope tags in producer files use the system ID:** `scope: example-assistant-specific`, `scope: architect-general`, `scope: domain-general`. This replaces the founding brief's example, which used a product codename in the tag.

**File layout in this project:**
```
inputs/
  example-assistant-arch.md          # the Architect's role doc
  example-assistant-learnings.md     # producer file mirror
principles/
  master.md                          # distilled cross-system principles
adr/
  NNNN-*.md
```

## Alternatives Considered

- **Product-name-based Architect names** ("Nimbus Architect," `nimbus-arch`). Rejected — couples role identity to product name (rename churn), and the master principles doc becomes opaque to anyone who doesn't know what the codename *is*.
- **Prefix `a.` for Architect IDs** (`a.federation`). Rejected. Dots play badly in filenames (shell globs, extension parsing, tab completion). The prefix also requires the convention in your head to parse — `example-assistant-arch` is self-explanatory cold.
- **Bare short ID for both system and Architect, disambiguated by context.** Rejected — too fragile in provenance lines and cross-references where context isn't carried.
- **Suffix `-architect` (full word) instead of `-arch`.** Considered. Rejected for verbosity in filenames; `-arch` is short, clear, and consistent with future role-class suffixes (`-aud` for Auditor — see ADR-0004 when written).

## Consequences

- All federation artifacts use these IDs consistently from 2026-05-21 forward.
- Adding a new product to the portfolio requires: assign a function name, derive the canonical Architect name, derive both IDs, update the mapping table (in this ADR or a successor).
- Future role classes (Auditor, etc.) follow the same suffix pattern. ADR-0004 (when written) will lock the Auditor's suffix.
- The founding brief's example scope tag (a codename-based tag) is now stale — it should use the system ID. Not editing the brief; this ADR governs.
- If the portfolio grows large (10+ products), the mapping table may warrant moving out of this ADR into a separate live registry. Defer until that pressure exists.

## References

- architect-learnings-federation-brief.md (withheld) — founding brief.
- Memory: `convention_naming.md` — operational summary for the Federation Architect.
