# Spec — User-profile storage

**Status:** Draft
**Date:** 2026-05-22
**Companion to:** [ADR-0008: Three-bucket taxonomy](../adr/0008-three-bucket-taxonomy.md)
**To be satisfied by:** ADR-0009 (storage mechanics — forthcoming)

## Purpose

This spec defines the requirements that any implementation of the user-preference storage layer must satisfy. It is the *contract*; ADR-0009 will pick the implementation that meets it.

Preferences are bucket #3 in the federation taxonomy (see [ADR-0008](../adr/0008-three-bucket-taxonomy.md)): user-scoped expressions of how a specific user prefers to be collaborated with. They propagate by user identity rather than by federation mechanism — every Architect bound to a given user reads that user's profile.

## Scope and framing

The federation in current use is single-user (the user), but the system is designed to be portable. This spec is written in **user-agnostic terms** — `<user-id>` is a slot, not a value. Populating it with `user` is configuration; adding a second user means a second profile file with the same shape. Nothing structural changes.

In scope: requirements that constrain *any* implementation.
Out of scope: which implementation to pick (deferred to ADR-0009); specific policy numbers like read-freshness windows or staleness thresholds (set by ADR-0009 within these constraints); user-identity management itself (assumed as a primitive).

## Definitions

- **User-id:** stable opaque identifier for an individual user, unique within a federation deployment.
- **Base profile:** the single source of truth for one user's default preferences, addressed by `<user-id>`.
- **Override:** per-Architect modification to a specific base entry, living with the Architect that scopes it.
- **Bound Architect:** an Architect whose local config declares it operates on behalf of a specific user-id.

## Requirements

### 1. Identity & scoping

- **R1.1** Each user has exactly one base profile, addressed by a stable `user-id`. The federation never has to guess which preferences apply.
- **R1.2** The base profile is the single source of truth for default preferences. No copies that can drift silently.
- **R1.3** Every Architect bound to a given user reads that user's profile. The Architect→user binding is established by the Architect's local config; the storage layer does not disambiguate.
- **R1.4** Per-Architect overrides are a distinct layer: they live with the Architect (role doc or sidecar), reference base entries by stable ID, and take precedence over the base on read.

### 2. Read/write semantics

- **R2.1** Read access: every bound Architect must be able to read the current base profile during a session. "Current" can be eventual within a defined window (see R5.2).
- **R2.2** Write access to the base is single-writer: only the Federation Architect (or the user directly, in the limit) updates the base. No other Architect mutates the base.
- **R2.3** Write access to overrides is owned by the Architect they live in. Federation Architect can *propose* override edits via the standard role-doc-edit workflow; the owning Architect's adoption is what makes the change effective.
- **R2.4** Reads must compose cleanly: base + applicable overrides = the preference set the Architect acts on. Override wins on conflict; no ambiguity.

### 3. Override semantics

- **R3.1** Each base entry has a stable identifier (slug, anchor, or similar) that overrides reference. Renames don't break links; the ID is durable across edits to wording.
- **R3.2** An override references the base entry it modifies and states the scoped value. If the base entry is removed, the override becomes dangling and must be flagged, not silently dropped.
- **R3.3** Overrides may only narrow or replace; they may not introduce preferences absent from the base. New preferences land in the base first, then are optionally overridden. Keeps the base as the discovery surface.
  - *Open trade-off for ADR-0009:* whether truly Architect-local preferences are ever allowed without a base entry. Default position here is "no, promote to base first."

### 4. Provenance & lifecycle

- **R4.1** Every preference (base or override) carries: date added, last-edited date, and the citation that justified it (paraphrase or quote of the user statement). Cold-readable — anyone reading the file can see *why* this preference exists.
- **R4.2** Edits to the base must be reviewable. Some form of change history is required; full append-only journal is not. Minimum bar: prior value recoverable for some retention window, change author identifiable.
- **R4.3** Stale-preference handling: preferences carry enough metadata to identify staleness (last-touched beyond a defined interval). Re-confirmation cadence is policy (ADR-0009); storage must surface the data the policy needs.
- **R4.4** Deletion is explicit and traceable, never implicit. A preference that vanishes silently is a bug.

### 5. Portability & access

- **R5.1** The base profile must be reachable from every machine where a bound Architect runs. Mechanism (sync, mount, network read, copy-on-session-start) is implementation; reachability is the requirement.
- **R5.2** Read freshness has a defined upper bound: when the base changes, all bound Architects must observe the change within a stated window (e.g., next session start, or ≤ N minutes). The window is set by ADR-0009; storage must support whatever policy is chosen.
- **R5.3** Format is human-readable text (markdown). Both the user and the Architects parse it. No machine-only encoding.
- **R5.4** No specific user is baked into the schema. The schema slot is `user-id`; adding a second user means a second profile file with the same shape.

### 6. Privacy & containment

- **R6.1** The base profile is **data** under [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md). Excluded from the federation's public repository; lives on user-controlled storage.
- **R6.2** Cross-user containment: if multiple users ever exist, one user's Architects cannot read another user's base. Default-deny on cross-profile reads.
- **R6.3** Cross-system containment: an Architect not bound to a user does not auto-receive that user's profile by virtue of existing in the same federation. Binding is explicit.

### 7. Failure modes

- **R7.1** Absent profile: an Architect operating without a bound profile (no-user / unbound case) must function without crashing or assuming preferences exist. The preference layer is empty, not undefined.
- **R7.2** Stale profile: if the profile is unreachable mid-session, the Architect uses the last-known-good snapshot and surfaces the staleness rather than failing silently.
- **R7.3** Conflicting overrides (base updated such that an override no longer makes sense) are surfaced, not silently resolved.

## Open trade-offs for ADR-0009 to resolve

The requirements above leave a small number of decisions for the implementation ADR:

- **R3.3** — Architect-local preferences without a base entry: allow or forbid?
- **R4.3** — Staleness threshold for preferences (e.g., 6 months, 12 months, never).
- **R5.2** — Read-freshness window (next session start, ≤ N minutes, real-time).
- **R4.2** — Change-history mechanism (git history, sidecar log, append-only journal).

These are policy choices within the constraints, not constraint relaxations.

## References

- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](../adr/0007-github-as-system-storage-data-excluded.md) — establishes that the profile is data and therefore gitignored.
- [ADR-0008: Three-bucket taxonomy](../adr/0008-three-bucket-taxonomy.md) — defines the preference bucket this spec serves.
- ADR-0009 (forthcoming) — will satisfy this spec by picking concrete mechanics.
