# Architecture Decision Records

This directory holds ADRs for the <<ARCHITECT_NAME>> system — one immutable record per non-trivial decision.

## Conventions

- **Filenames:** `NNNN-kebab-title.md`, 4-digit zero-padded, monotonically increasing.
- **Statuses:** Proposed | Accepted | Deprecated | Superseded by ADR-XXXX.
- **Immutability:** Accepted ADRs are not edited in place except to flip status (and add a historical note when superseded same-day). A revised decision gets a new ADR that supersedes the old one.
- **Template:** template.md (withheld). Sections: Status, Date, Deciders, Context, Decision, Alternatives Considered, Consequences, References.

This convention mirrors the federation's ADR convention. See ADR-0001 in the federation repo (withheld) for the rationale.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| (none yet — ADRs land here as decisions accumulate) | | | |

## Cross-references to federation ADRs

This Architect operates under federation-wide ADRs. The most-cited from this repo are:

- ADR-0003: Federation Architect is a federation participant (withheld) — recursive participation.
- ADR-0007: GitHub as system storage; data excluded (withheld) — federation prototype for this Architect's repo conventions.
- ADR-0008: Three-bucket taxonomy (withheld) — principle / habit / preference distinctions.
- ADR-0009: Data storage mechanics (withheld) — data-root abstraction.
- ADR-0010: Registries are canon-only; sidecar history pattern (withheld) — role-doc-as-canon convention.
- ADR-0011: Data-root configuration form (withheld) — `Data root:` declaration.
- ADR-0012: Per-Architect repo conventions (withheld) — this Architect's repo governance.
- ADR-0013: Receipt ritual (withheld) — inbound federation edit mechanism.
- ADR-0016: Memory is local (withheld) — memory locality; no memory-sync infra.

Cross-reference by system ID + artifact name in prose per ADR-0012 (withheld) §"Cross-repo references."
