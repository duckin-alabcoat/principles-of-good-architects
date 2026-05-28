# ADR-0002: Architect and system naming convention

**Status:** Superseded by [ADR-0006](0006-naming-convention-corrected.md)
**Date:** 2026-05-21
**Deciders:** the user, Federation Architect

> **Historical note (2026-05-21, same day as acceptance):** The "Product name" column in this ADR conflated two distinct things — the system (e.g., recipe box) and the orchestrator agent inside it (e.g., Basil). The user corrected this shortly after acceptance. ADR-0006 introduces a fifth slot to disambiguate. The core conventions in this ADR (suffix `-arch`, function-based Architect names, kebab-case IDs) carry forward unchanged in 0006.

## Context

The user's portfolio includes multiple AI-assisted systems — Basil (recipe box), Hearth (home-hub), Cairn (trail-log), Campaign Tracker, Arcade, and likely more. Each system has its own Architect — a long-lived collaborator role doc that builds and maintains it.

The Federation Architect needs consistent identifiers for both systems and Architects across artifacts (filenames, scope tags, provenance lines in the master principles doc, prose references). Two ambiguities surfaced early in the bootstrap conversation:

1. **Architect vs. product naming.** the user corrected an early conflation: "Basil" is the *product*, not the Architect. "Basil's Architect" is awkward and couples role identity to a product name that may change.
2. **System vs. Architect short ID.** A bare token like `home-hub` is ambiguous — does it mean the Home Hub system or the Home Hub Architect? In provenance lines and prose references with no artifact suffix, the disambiguation must live in the ID itself.

## Decision

**Four-slot convention:**

| Slot | What | Example |
|---|---|---|
| Product name | Friendly codename, free-form, used conversationally | Basil |
| Function | What the product does, plain English | recipe box |
| Canonical Architect name | `<Function> Architect`, title case, used in prose | Recipe Box Architect |
| System ID | kebab-case function, used in scope tags and references to the system | `recipe-box` |
| Architect ID | `<system-id>-arch`, used in filenames, provenance, references to the role | `recipe-box-arch` |

**Architect names derive from function, not product.** A product rename does not churn the Architect's identity.

**Suffix `-arch` disambiguates Architect IDs from system IDs.** Prefix forms (`a.home-hub`) were considered and rejected.

**Portfolio mapping (as of 2026-05-21):**

| Product | Canonical Architect | System ID | Architect ID |
|---|---|---|---|
| Basil | Recipe Box Architect | `recipe-box` | `recipe-box-arch` |
| Hearth | Home Hub Architect | `home-hub` | `home-hub-arch` |
| Cairn | Trail Log Architect | `trail-log` | `trail-log-arch` |
| Campaign Tracker | Campaign Tracker Architect | `campaign-tracker` | `campaign-tracker-arch` |
| Arcade | Arcade Architect | `arcade` | `arcade-arch` |
| (this system) | Federation Architect | `federation` | `federation-arch` |

Cairn expands to "Chief Trail Agent" — *Agent* is in the product name, no collision with *Architect*.

**Scope tags in producer files use the system ID:** `scope: recipe-box-specific`, `scope: architect-general`, `scope: domain-general`. This replaces the founding brief's example of `Basil-specific`.

**File layout in this project:**
```
inputs/
  recipe-box-arch.md         # the Architect's role doc
  recipe-box-learnings.md    # producer file mirror
principles/
  master.md                          # distilled cross-system principles
adr/
  NNNN-*.md
```

## Alternatives Considered

- **Product-name-based Architect names** ("Basil Architect," `Basil-arch`). Rejected — couples role identity to product name (rename churn), and the master principles doc becomes opaque to anyone who doesn't know what Basil *is*.
- **Prefix `a.` for Architect IDs** (`a.home-hub`). Rejected. Dots play badly in filenames (shell globs, extension parsing, tab completion). The prefix also requires the convention in your head to parse — `home-hub-arch` is self-explanatory cold.
- **Bare short ID for both system and Architect, disambiguated by context.** Rejected — too fragile in provenance lines and cross-references where context isn't carried.
- **Suffix `-architect` (full word) instead of `-arch`.** Considered. Rejected for verbosity in filenames; `-arch` is short, clear, and consistent with future role-class suffixes (`-aud` for Auditor — see ADR-0004 when written).

## Consequences

- All federation artifacts use these IDs consistently from 2026-05-21 forward.
- Adding a new product to the portfolio requires: assign a function name, derive the canonical Architect name, derive both IDs, update the mapping table (in this ADR or a successor).
- Future role classes (Auditor, etc.) follow the same suffix pattern. ADR-0004 (when written) will lock the Auditor's suffix.
- The founding brief's example scope tag (`Basil-specific`) is now stale — should be `recipe-box-specific`. Not editing the brief; this ADR governs.
- If the portfolio grows large (10+ products), the mapping table may warrant moving out of this ADR into a separate live registry. Defer until that pressure exists.

## References

- [architect-learnings-federation-brief.md](../architect-learnings-federation-brief.md) — founding brief.
- Memory: `convention_naming.md` — operational summary for the Federation Architect.
