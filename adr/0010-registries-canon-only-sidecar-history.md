# ADR-0010: Registries are canon-only; sidecar history pattern is uniform across all three

**Status:** Accepted
**Date:** 2026-05-23
**Deciders:** the user, Federation Architect

## Context

[ADR-0009](0009-data-storage-mechanics.md) stood up three data-root registries — `principles/master.md`, `habits/master.md`, `users/<user-id>/profile.md` — and made two choices that, on review, deserve revision:

1. **Adoption tracking lives inside the habit registry.** ADR-0009 §"Habit registry" specifies an "adoption status per Architect" field on each habit entry. Principles registry has no such field (principles are universal by ADR-0008). Profile has no such field (preferences belong to a user, not adopted by Architects).
2. **Change history is a sidecar for the user profile only.** ADR-0009 §"User profile — change history" creates `profile-history.md` as an append-only sidecar. Neither the principles registry nor the habit registry gets equivalent treatment — change history for those depends on git log alone.

Both choices were defensible at the time but produce two problems once the registries are reviewed together:

**Adoption tracking conflates canon with bookkeeping.** The habit registry answers "what is the habit?" Adoption answers "which Architects have taken it on?" These are two different queries with different update cadences (habit definition rarely changes; adoption changes every time an Architect lands an edit). Per [ADR-0008](0008-three-bucket-taxonomy.md)'s propagation rule, the authoritative record of adoption is the adopting Architect's role doc (the habit's edit lands in their role doc, with their version bump). The adoption-status column in `habits/master.md` is a duplicate of that record — and a hand-maintained one, which means it can drift from the role-doc truth.

**Asymmetric change-history substrate-couples the registries.** The reasoning ADR-0009 §"Why a sidecar instead of relying on git" gave for the profile applies identically to the other two registries: the data root may be a git working tree, but the Architect cannot assume so. A sidecar works whether the substrate is git, a synced directory, a mounted volume, or a network filesystem. Leaving principles and habits to depend on git log alone violates the substrate-opacity stance ADR-0009 established for the profile.

Both gaps surfaced in session 4's review of the registries' shape (2026-05-23, same session ADR-0009 was accepted). User direction (the user, same review): registries should be pure; adoption is a separate function and independent of canon; all three docs should be tracked the same way.

## Decision

### Adoption-locus rule

**Adoption is not tracked in any data-root registry.** It lives in the adopting Architect's role doc, where the actual adoption event (the role-doc edit and version bump) is recorded.

Mechanically:

- `principles/master.md` entries have no adoption field. Principles are universal by [ADR-0008](0008-three-bucket-taxonomy.md); every Architect bound to the federation is bound to every accepted principle by the recursive principle ([ADR-0003](0003-federation-architect-is-a-participant.md)). "Adoption" is conceptually incoherent at the registry level.
- `habits/master.md` entries have no adoption field. Each universal habit is adopted by being landed in an Architect's role doc; that role doc is the authoritative record of which Architects have taken on which habits, and at which versions.
- `users/<user-id>/profile.md` continues to have no adoption field. Preferences are scoped to a user, not adopted by Architects. Architect-specific deviation is captured by overrides in the Architect's role doc (per ADR-0009 §"Overrides"), which is again role-doc-resident.

If a cross-Architect view of adoption is wanted (e.g., "which Architects have adopted `producer-side-learnings`?"), it is a **derived view** generated from role docs by a federation pass — not a maintained file. Federation Architect can produce such a view on demand or on a schedule; the source of truth is always the role docs.

### Change-history sidecar — uniform across all three registries

Each of the three data-root registries gets an append-only sidecar history file alongside it:

```
<data-root>/
  principles/
    master.md
    master-history.md          # new
  habits/
    master.md
    master-history.md          # new
  users/
    <user-id>/
      profile.md
      profile-history.md       # already exists per ADR-0009
```

Each history entry records: date, change type (add / edit / status-flip / remove / rename), affected slug(s), prior value for edits or removes, citation that justified the change, author. Cold-readable.

Why uniform: the substrate-opacity argument ADR-0009 made for the profile applies equally to the other two. Where git is also the substrate, the sidecar harmlessly duplicates some information — the sidecar is the authoritative history doc regardless of substrate.

### Habit-entry schema — revised

The schema in ADR-0009 §"Habit registry" is amended by dropping the adoption field. Remaining fields: stable slug, status, parent principle (linked), statement, failure-mode-breadth argument, citation, date added, date last edited.

Per-Architect adoption that was previously expressed in the registry is, going forward, expressed in each Architect's role doc — and was always going to be, since landing the habit means landing the role-doc edit.

## Alternatives Considered

- **Leave ADR-0009's adoption-in-registry as is.** Rejected. The duplication is real (role doc is the actual record of the adoption event), and registries reading more cleanly when they hold only canon is a structural win, not cosmetic.
- **Strip adoption from registries but keep change-history as git-only for principles and habits.** Rejected on consistency grounds. The substrate-opacity argument doesn't change based on registry; if it earns its keep for one registry it earns its keep for all. Asymmetric treatment also makes readers wonder why — invites confusion and pattern-drift.
- **Maintain a separate central adoption file (e.g., `adoption-status.md`) hand-edited at every adoption event.** Rejected. Same duplication problem as keeping adoption in the registry — the adopting Architect's role doc is the authoritative record. A derived view is the right shape when a cross-Architect query is wanted.
- **Inline semver + CHANGELOG inside each registry (the role-doc pattern).** Rejected. Profile already uses sidecar (just landed in ADR-0009); migrating profile to inline would mean amending a freshly-accepted decision asymmetrically. Inline CHANGELOGs also lean on git for the actual diff text. Sidecar is more substrate-portable and keeps the main file clean for reading current state. Registries are reference data, not behavioral contracts — no consumer pins to "principles/master.md v1.2" the way consumers pin to a role-doc version.

## Consequences

### Immediate

- `habits/master.md` is edited to drop the "Adoption" subsection from each entry and the "Adoption-status legend" at the bottom. Per-entry adoption state previously recorded there is **not migrated** — it is captured (where it should always have been) in each Architect's role doc, with the federation-arch.md v0.4.0 bump being the next opportunity to formalize federation-side adoptions for several pending entries.
- `principles/master-history.md` is created with a single initialization entry citing the session 4 derivation pass.
- `habits/master-history.md` is created with a single initialization entry citing the session 4 derivation pass.
- ADR-0009 is marked **Partially superseded by ADR-0010** with a brief note identifying which clauses are superseded; the bulk of ADR-0009 (data-root abstraction, layout, overrides, failure-mode behavior, etc.) remains in force.

### Forward

- Federation-arch.md v0.4.0 — the next planned change — formally adopts the 13 universal habits in the role doc itself, which under this ADR is the only canonical record of federation-side adoption. The same v0.4.0 bump will produce the first cross-Architect "what has Federation Architect adopted" datapoint that a future derived adoption view would aggregate.
- When new Architects are onboarded, each `master-history.md` will gain entries as principles and habits are added, status-flipped, or refined. The sidecar pattern scales the same way the profile sidecar does.
- A future ADR may specify a derived-adoption-view mechanism (script, periodic federation pass, or query convention). It is not specified here — the deferral is intentional. The decision this ADR locks is that adoption is not registry-resident; the mechanism for *reading across role docs* can be designed when the need is concrete.

### Risks

- **Loss of "all adoption visible in one place."** The habit registry's adoption column gave one-stop visibility. A reader now has to consult role docs (or wait for a derived view) to see who's adopted what. Mitigation: at current scale (one Architect formally bound; two more on the near-term roadmap) the answer is short enough that grep across role docs suffices. The derived-view ADR can come when scale demands it.
- **Sidecar drift for principles and habits.** Same drift risk ADR-0009 §"Risks" identified for the profile sidecar applies here: an editor can modify the registry without writing the corresponding history entry. Mitigation is identical: Federation Architect is the single writer in the normal case; reconciliation routine can be added if direct-edit drift becomes a real problem.
- **One-day-old ADR amendment.** ADR-0009 is being partially superseded the same day it was accepted. This is unusual but not problematic — the changes are refinements aligned with ADR-0009's substrate-opacity spirit, not reversals. Recording it as a proper supersession (rather than editing ADR-0009 in place) preserves the audit trail per [adr/README.md](README.md) convention.

## References

- [ADR-0009: Data storage mechanics](0009-data-storage-mechanics.md) — partially superseded by this ADR (adoption-tracking clause in §"Habit registry"; change-history-sidecar scope in §"User profile — change history").
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — principles are universal by definition; habit propagation is by per-Architect role-doc edits. Both ground the adoption-locus rule.
- [ADR-0003: Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — recursive principle binding Federation Architect to all federation principles.
- [ADR-0007: GitHub as system storage; data excluded](0007-github-as-system-storage-data-excluded.md) — establishes that the registries and sidecars are data, not system; gitignored.
- [session-handoff.md](../session-handoff.md) — session 5 entry will record this ADR's drafting and the registry refactor.
