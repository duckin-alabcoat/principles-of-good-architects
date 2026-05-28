# Architecture Decision Records

This directory holds ADRs for the <<ARCHITECT_NAME>> system — one immutable record per non-trivial decision.

## Conventions

- **Filenames:** `NNNN-kebab-title.md`, 4-digit zero-padded, monotonically increasing.
- **Statuses:** Proposed | Accepted | Deprecated | Superseded by ADR-XXXX.
- **Immutability:** Accepted ADRs are not edited in place except to flip status (and add a historical note when superseded same-day). A revised decision gets a new ADR that supersedes the old one.
- **Template:** [template.md](template.md). Sections: Status, Date, Deciders, Context, Decision, Alternatives Considered, Consequences, References.

This convention mirrors the federation's ADR convention. See [ADR-0001 in the federation repo](<<FEDERATION_REPO_REF>>/adr/0001-use-adrs.md) for the rationale.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| (none yet — ADRs land here as decisions accumulate) | | | |

## Cross-references to federation ADRs

This Architect operates under federation-wide ADRs. The most-cited from this repo are:

- [ADR-0003: Federation Architect is a federation participant](<<FEDERATION_REPO_REF>>/adr/0003-federation-architect-is-a-participant.md) — recursive participation.
- [ADR-0007: GitHub as system storage; data excluded](<<FEDERATION_REPO_REF>>/adr/0007-github-as-system-storage-data-excluded.md) — federation prototype for this Architect's repo conventions.
- [ADR-0008: Three-bucket taxonomy](<<FEDERATION_REPO_REF>>/adr/0008-three-bucket-taxonomy.md) — principle / habit / preference distinctions.
- [ADR-0009: Data storage mechanics](<<FEDERATION_REPO_REF>>/adr/0009-data-storage-mechanics.md) — data-root abstraction.
- [ADR-0010: Registries are canon-only; sidecar history pattern](<<FEDERATION_REPO_REF>>/adr/0010-registries-canon-only-sidecar-history.md) — role-doc-as-canon convention.
- [ADR-0011: Data-root configuration form](<<FEDERATION_REPO_REF>>/adr/0011-data-root-config.md) — `Data root:` declaration.
- [ADR-0012: Per-Architect repo conventions](<<FEDERATION_REPO_REF>>/adr/0012-per-architect-repo-conventions.md) — this Architect's repo governance.
- [ADR-0013: Receipt ritual](<<FEDERATION_REPO_REF>>/adr/0013-receipt-ritual.md) — inbound federation edit mechanism.
- [ADR-0016: Memory is local](<<FEDERATION_REPO_REF>>/adr/0016-memory-is-local.md) — memory locality; no memory-sync infra.

Cross-reference by system ID + artifact name in prose per [ADR-0012](<<FEDERATION_REPO_REF>>/adr/0012-per-architect-repo-conventions.md) §"Cross-repo references."
