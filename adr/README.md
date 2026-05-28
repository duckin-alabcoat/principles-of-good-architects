# Architecture Decision Records

This directory holds ADRs for the Federation Architect system — one immutable record per non-trivial decision.

## Conventions

- **Filenames:** `NNNN-kebab-title.md`, 4-digit zero-padded, monotonically increasing.
- **Statuses:** Proposed | Accepted | Deprecated | Superseded by ADR-XXXX.
- **Immutability:** Accepted ADRs are not edited in place except to flip status (and add a historical note when superseded same-day). A revised decision gets a new ADR that supersedes the old one.
- **Template:** [template.md](template.md). Sections: Status, Date, Deciders, Context, Decision, Alternatives Considered, Consequences, References.

See [ADR-0001](0001-use-adrs.md) for the rationale.

## Index

| # | Title | Status | Date |
|---|---|---|---|
| [0001](0001-use-adrs.md) | Record federation decisions as ADRs | Accepted | 2026-05-21 |
| [0002](0002-naming-convention.md) | Architect and system naming convention (4-slot) | Superseded by [ADR-0006](0006-naming-convention-corrected.md) | 2026-05-21 |
| [0003](0003-federation-architect-is-a-participant.md) | The Federation Architect is a federation participant | Accepted | 2026-05-21 |
| [0004](0004-auditor-as-separate-role-class.md) | Auditor as a separate role class from Architect | Accepted | 2026-05-21 |
| [0005](0005-tinkerer-split.md) | Split Tinkerer Architect into Arcade Architect + Auditor pattern | Accepted | 2026-05-21 |
| [0006](0006-naming-convention-corrected.md) | Naming convention — corrected to include orchestrator-agent slot | Accepted | 2026-05-21 |
| [0007](0007-github-as-system-storage-data-excluded.md) | GitHub as system storage; data excluded by policy and gitignore | Accepted | 2026-05-21 |
| [0008](0008-three-bucket-taxonomy.md) | Three-bucket taxonomy — principle, habit, preference | Accepted | 2026-05-22 |
| [0009](0009-data-storage-mechanics.md) | Data storage mechanics — data root, habit registry, user profile | Accepted (partially superseded by [ADR-0010](0010-registries-canon-only-sidecar-history.md)) | 2026-05-23 |
| [0010](0010-registries-canon-only-sidecar-history.md) | Registries are canon-only; sidecar history uniform across all three | Accepted | 2026-05-23 |
| [0011](0011-data-root-config.md) | Data-root configuration form — declared in role-doc top metadata | Accepted | 2026-05-26 |
| [0012](0012-per-architect-repo-conventions.md) | Per-Architect repository conventions | Accepted | 2026-05-26 |
| [0013](0013-receipt-ritual.md) | Receipt ritual — federation edit distribution mechanism | Accepted | 2026-05-26 |
| [0014](0014-existing-architect-retrofit.md) | Existing-Architect retrofit mechanism | Accepted | 2026-05-26 |
| [0015](0015-work-package-briefs.md) | Work-package briefs as the federation parallel-work mechanism | Accepted | 2026-05-26 |
| [0016](0016-memory-is-local.md) | Memory is local; cross-machine continuity is the session-handoff + registries job | Accepted | 2026-05-26 |

## Pending ADRs (likely future decisions)

Open questions tracked in [federation-arch.md §10](../federation-arch.md#10-open-questions) will likely produce ADRs as they get resolved:

- Repo access / file-shuttling mechanic
- Federation cadence
- Conflict-resolution policy (initial answer in PROCESS.md; may formalize)
- Master principles doc versioning policy
- Whether feedback-memory files are in scope for federation
- Standard Auditor briefing template (deferred until first commissioned engagement)
- "Architect role doc corruption" failure mode characterization (cf. ADR-0005)
