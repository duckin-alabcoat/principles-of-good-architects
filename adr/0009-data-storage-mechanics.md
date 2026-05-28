# ADR-0009: Data storage mechanics — data root, habit registry, user profile

**Status:** Accepted; partially superseded by [ADR-0010](0010-registries-canon-only-sidecar-history.md) (2026-05-23, same-session refinement)
**Date:** 2026-05-23 (drafted and accepted same session)
**Deciders:** the user, Federation Architect

> **Note on partial supersession.** Two clauses below are superseded by ADR-0010:
> - §"Habit registry" — the "Adoption status per Architect" field is removed. Adoption is recorded in each adopting Architect's role doc, not in the registry.
> - §"User profile — change history" — the sidecar-history pattern is **extended** to `principles/master-history.md` and `habits/master-history.md`; the profile sidecar's mechanics are unchanged.
>
> The rest of this ADR — the data-root abstraction, the layout, the override mechanism, the spec trade-off resolutions (R3.3, R4.3, R5.2, R4.2), the promotion/demotion workflow, the failure-mode behavior, the architectural envelope — remains in force.

## Context

[ADR-0008](0008-three-bucket-taxonomy.md) locked the taxonomy (principle / habit / preference) and explicitly deferred storage mechanics to this ADR. [`specs/user-profile-storage.md`](../specs/user-profile-storage.md) captured the contract any user-profile implementation must satisfy and left four open trade-offs for ADR-0009 to resolve. The habit-registry destination was also flagged in ADR-0008 as "TBD by ADR-0009."

The driving constraint comes from outside the spec: the federation's *architectural envelope* is broader than its current test surface. The federation in active use runs on a single machine with a single user; the architecture must also work for federations whose Architects are distributed across machines, possibly cloud-hosted, possibly serving multiple users. ADR-0009's default implementation can be simple, but the abstraction must leave room for sync scripts, network reads, or git-based mechanisms without architectural change.

Two existing decisions constrain the design:

- **[ADR-0007](0007-github-as-system-storage-data-excluded.md)** classifies user profiles, habit registries, and the master principles doc as **data**. Excluded from the federation's public GitHub repo by policy and gitignore. They live on user-controlled storage.
- **[ADR-0008](0008-three-bucket-taxonomy.md)** requires that universal habits propagate by per-Architect role-doc edits (no auto-sync), so the habit registry is a *source* the federation drafts from, not a runtime configuration Architects read directly during their work.

This ADR picks the mechanics.

## Decision

### The data root abstraction

Federation data lives under a configured path called the **data root**. Each machine running a federation-participating Architect has exactly one data root, declared in that Architect's local config. The data root is opaque to the Architect: it knows the path, it does not assume the substrate.

**Why this is the right primitive:** it decouples *where data lives on this machine* from *how data syncs across machines*. The Architect reads and writes through a path; the substrate (local directory, mounted volume, git working tree, network filesystem) is an implementation choice that can change without code-level change in the Architect.

For the federation's current single-machine case, the data root resolves to the existing working directory `/Volumes/user/principles-of-good-architects/`. The directories below are already gitignored as data per ADR-0007; this ADR formalizes their layout and adds two more.

### Layout under the data root

```
<data-root>/
  principles/
    master.md                        # synthesized principles (already exists)
  habits/
    master.md                        # universal habit registry  (new)
  users/
    <user-id>/
      profile.md                     # base user profile        (new)
      profile-history.md             # append-only change log   (new)
```

Architect-local overrides live with the Architect, not under the data root — see [Overrides](#overrides) below.

`.gitignore` is updated in the same commit that introduces `habits/` and `users/`, per the ADR-0007 rule that a path-pattern's presence is itself a data declaration.

### Habit registry

The universal habit registry lives at `<data-root>/habits/master.md`. Sibling to `principles/master.md` rather than nested inside it. The two files have different lifecycles (principles change rarely; habits churn faster as failure modes are discovered) and different propagation mechanisms (principles ride the recursive principle; habits propagate by per-Architect role-doc edits).

Each entry carries:

- A stable slug (kebab-case).
- The parent principle (named, linked by slug to `principles/master.md`). Required per ADR-0008 — universal habit without a named parent principle is a classification error.
- Failure-mode-breadth argument.
- Citation back to originating Architect(s) and producer-file entries.
- Date added, last edited.
- Adoption status per Architect: which Architects have landed this in their role doc, with the role-doc version that adopted it. This is the audit trail ADR-0008 required.

The registry is read by the Federation Architect when drafting role-doc edits. It is **not** read by participating Architects at runtime — they pick up adopted habits via their own role docs, not by querying the registry. This preserves the per-Architect audit trail ADR-0008 requires.

### User profile — base

The base profile lives at `<data-root>/users/<user-id>/profile.md`. `<user-id>` is a slot; the current instance resolves to a specific user but the schema bakes in no user.

Each preference entry has the shape:

```markdown
### <slug>

- **Added:** YYYY-MM-DD
- **Last edited:** YYYY-MM-DD
- **Citation:** "<user> on YYYY-MM-DD: <paraphrase or quote>"

<preference content — one or more paragraphs>
```

The slug is the stable ID overrides reference (R3.1). Wording can change; the slug cannot, except through a deliberate rename event that updates all overrides in the same change.

Preferences are grouped under topical `##` sections (e.g., `## Communication style`, `## Naming`, `## Units`). Grouping is for human readability; the slug is what the system addresses.

### User profile — change history

Change history is captured in a sidecar at `<data-root>/users/<user-id>/profile-history.md` — append-only, newest-first.

**Why a sidecar instead of relying on git:** the data root *may* be a git working tree (and probably will be, for multi-machine cases), but the Architect cannot assume so. A sidecar works whether the data root is a git repo, a shared mount, a synced directory, or a network filesystem. Where git is also present, the sidecar duplicates some information harmlessly — the sidecar is the authoritative history doc.

Each history entry records: date, what changed (slug + add/edit/remove), the prior value if applicable, the citation that justified the change, and the author (Federation Architect or user direct edit). Cold-readable: someone reading the history file alone can reconstruct what happened.

Deletion is explicit (R4.4) — a remove entry in the history is what makes a deletion traceable. An entry vanishing from `profile.md` without a corresponding history entry is a bug.

### Overrides

Per-Architect overrides live **with the Architect**, in a dedicated `## User overrides` section of the Architect's role doc. Each override entry:

```markdown
### <base-slug>  *(override)*

- **For user:** <user-id>
- **Override value:** <scoped value>
- **Reason:** <one-line argument>
- **Added:** YYYY-MM-DD
```

The Architect resolves preferences at session start by: (a) reading `<data-root>/users/<user-id>/profile.md`; (b) reading its own `## User overrides` section; (c) applying overrides on top of base by slug match. Override wins on conflict (R2.4).

**Override hygiene checks** at session start:

- Each override must resolve to a live base slug. Unresolved overrides (the base entry was removed or renamed) are surfaced, not silently dropped (R3.2, R7.3).
- The Architect's local config declares which `<user-id>` it is bound to. Overrides naming a different user-id are an error.

### Resolutions of the spec's open trade-offs

| Spec ref | Question | Decision |
|---|---|---|
| R3.3 | Architect-local preferences without a base entry: allow or forbid? | **Forbid.** New preferences land in base first, then optionally are overridden. Base remains the single discovery surface. Friction is low (base is data, no commit gate); clarity gain is real. |
| R4.3 | Staleness threshold for preferences. | **12 months** since last-edited. Surface as a "may be stale" flag when the Architect touches a preference area whose entries cross the threshold; do not auto-delete. Re-confirmation is a user-prompted federation event, not a calendar one. |
| R5.2 | Read-freshness window. | **Next session start.** The profile is loaded at session start and held for the session. Mid-session edits do not propagate until the next session. Simplest semantics; matches how Architects already cold-start from their role docs. |
| R4.2 | Change-history mechanism. | **Append-only sidecar** at `<data-root>/users/<user-id>/profile-history.md`. Independent of whether the data root is a git working tree. |

### Promotion / demotion workflow

The buckets and direction were defined in [ADR-0008](0008-three-bucket-taxonomy.md). This ADR pins the mechanics.

A promotion or demotion is a **federation event** with four artifacts produced in order:

1. **Argument doc** — Federation Architect drafts the case in the appropriate registry file (habit promotion → entry in `habits/master.md` with status `proposed`; preference promotion → entry in `profile.md` with the date and citation; principle promotion → entry in `principles/master.md`).
2. **User approval** — explicit, recorded by status flip from `proposed` to `accepted` (or the equivalent in the file's schema). For preferences, "user approval" can be the user's original statement itself; for habits and principles, an explicit accept is required.
3. **Downstream role-doc edits drafted** — for habits and principles, Federation Architect drafts per-Architect role-doc edits per ADR-0008's propagation rule.
4. **Per-Architect adoption** — each owning Architect lands the edit in its own role doc, bumping its version. The habit registry's per-Architect adoption status updates as each lands.

Demotion is the symmetric inverse: argument → user approval → drafted reversal edits → per-Architect un-adoption. The originating Architect's role doc retains the entry with its (now-narrower) scope per ADR-0008.

### Failure-mode behavior

- **Absent profile (R7.1).** Architect runs with an empty preference layer. Logged at session start, not a crash.
- **Unreachable profile mid-session (R7.2).** Architect continues with last-known-good snapshot taken at session start. Surfaces the staleness on next reference to a preference, not silently.
- **Dangling override (R7.3).** Surfaced at session start; the override is suspended (not silently dropped, not applied as-is). Federation Architect drafts a resolution at next federation pass.
- **Cross-user contamination (R6.2, R6.3).** The Architect reads only `<data-root>/users/<bound-user-id>/profile.md`. The bound user-id comes from the Architect's local config. There is no mechanism by which an Architect reads a profile it is not bound to; if multiple user directories exist, the unbound ones are invisible to it.

### Architectural envelope — what this design *also* supports

The default implementation is local filesystem; the abstraction also covers:

- **Multi-machine federation.** Data root is a clone of a private data repository (separate from the federation's public system repo). Sync via push/pull. Each machine has its own clone; the read-freshness window (R5.2) is enforced by pulling at session start.
- **Cloud-hosted Architect.** Data root is a mounted volume or a clone reached via deploy key. The Architect's read path is unchanged.
- **Multi-user federation.** Additional `<user-id>` subdirectories under `users/`. An Architect's bound user-id determines which one it reads. Cross-user containment is the existing filesystem boundary plus the Architect's bound-id check.

None of these require changes to the layout or to the Architect's read code. They are substrate changes below the data-root abstraction.

## Alternatives Considered

- **Bake the profile location into the federation working directory permanently.** Rejected — collapses the test surface and the architectural envelope. A cloud-hosted Architect or a second-machine Architect has no federation working directory; the data root is the indirection that makes the architecture portable. (See [[feedback-architecture-target-vs-test-surface]] in memory.)
- **Single shared file with no override layer.** Rejected — contradicts spec R1.4 and the federation's existing observation that some preferences are user-wide and some are per-Architect ("be more exploratory with Federation, more terse with Recipe Box").
- **Overrides centralized in the user directory** (e.g., `users/<user-id>/overrides/<architect-id>.md`). Rejected — contradicts R2.3 (write ownership of an override belongs to the Architect, not the user directory). Also makes the Architect's role doc no longer cold-readable for "what does this Architect actually do for this user."
- **Allow Architect-local preferences without a base entry** (R3.3 inversion). Rejected — fragments the discovery surface. If the preference is truly Architect-only, it's usually a sign it should be a habit, not a preference. Holding the line forces that classification question at the right moment.
- **Use git history as the canonical change record** (no sidecar). Rejected — couples the architecture to a substrate choice. The sidecar works in all substrates; git history becomes a redundant additional record where present, not a missing requirement where absent.
- **Real-time read freshness (R5.2 stricter).** Rejected — requires either polling or a notification channel, neither of which the architecture justifies at current scale. Next-session-start is sufficient and matches existing cold-start semantics.
- **Auto-prune preferences past staleness threshold.** Rejected — silent deletion violates R4.4. Surfacing is the right behavior; deletion is a deliberate event.
- **Habit registry nested inside `principles/master.md`.** Rejected — different lifecycles, different propagation mechanisms. Sibling files keep both readable.
- **Architects read the habit registry at runtime** to pick up new habits automatically. Rejected — ADR-0008 explicitly rejected auto-sync of habits to preserve the per-Architect audit trail. Habits propagate by role-doc edits; runtime registry reads would re-introduce the failure mode.
- **Store user-profile data in a private branch of the federation's public repo.** Rejected — contradicts the spirit of ADR-0007 (system files and data are separated by repository, not by branch within one repository) and complicates ADR-0007's blanket-authority delegation. A separate data repo, if used, is its own thing.

## Consequences

### Immediate

- `.gitignore` gets two new entries (`habits/`, `users/`) added in the same commit that creates either directory.
- The habit registry destination is locked at `<data-root>/habits/master.md` — unblocking the Recipe Box candidate re-classification pass and the universal habit registry stand-up listed in the session-3 handoff.
- The user-profile artifact destination is locked at `<data-root>/users/<user-id>/profile.md`. The current user instance's file path resolves accordingly; the schema is portable.
- Per-Architect role docs gain a standard `## User overrides` section template. Federation-arch.md will need this section when it bumps to v0.4.0; participating Architects' role docs gain it when they next take an override.

### On federation-arch.md and ADR-0003

Federation Architect is a federation participant ([ADR-0003](0003-federation-architect-is-a-participant.md)), so it is also bound to a user-id and reads from `<data-root>/users/<user-id>/profile.md`. Its `## User overrides` section is the local override layer for preferences scoped to working with this Architect specifically. The federation-arch.md v0.4.0 bump (session-3 next-step #6) absorbs both this and the universal-habit adoption surface.

### Risks

- **Data-root configuration drift.** If a participating Architect's local config points to the wrong data root, it reads stale or no preferences. Mitigation: the absent-profile and stale-profile failure modes (R7.1, R7.2) are non-fatal; the Architect logs the condition at session start, surfacing misconfigurations early rather than crashing or silently mis-personalizing.
- **Sidecar history can diverge from `profile.md`** if an editor (or process) modifies the base without writing the corresponding history entry. The base file is the truth; the sidecar is the audit trail. Mitigation: Federation Architect is the single writer in the normal case; direct user edits are explicitly listed as a known write path, and the sidecar's authorship field flags them. A reconciliation routine can be added if direct-edit drift becomes a real problem.
- **Slug collisions / renames.** Two profile entries with the same slug, or a renamed slug whose overrides weren't migrated, both break the resolution rule. Mitigation: slugs are unique per profile (enforced by Federation Architect at edit time); renames are an explicit event that updates all overrides in the same federation pass.
- **Premature path commitments.** Locking `<data-root>/users/<user-id>/profile.md` now means a later sync mechanism that wants a different layout has to either conform or break compatibility. Mitigation accepted: the layout is simple enough to survive most plausible substrate choices, and the data-root abstraction itself is the escape valve if a fundamentally different shape becomes necessary.

### Deferred

- **Choice of substrate for multi-machine federations.** Whether the multi-machine case uses a private data repo, a sync tool, a mounted volume, or something else is not picked here — the abstraction supports any of them, and the choice can wait until the federation actually spans machines.
- **Reconciliation routine for sidecar drift.** Not built; not yet needed at current writer count.
- **Multi-user policy.** Mechanically supported by the layout. Policy questions (how a new user-id is provisioned, how cross-Architect rebinding works) are deferred until a second user appears.
- **Stable-ID generation for principle slugs.** This ADR uses kebab-case slugs by hand; if the principle / habit / preference corpus grows large enough to make hand-curation error-prone, a generation convention can be specified later without affecting any reader.

## References

- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — establishes that the profile and habit registry are data; sets the .gitignore rule this ADR extends.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — defines the buckets whose storage mechanics this ADR pins.
- [`specs/user-profile-storage.md`](../specs/user-profile-storage.md) — the storage contract this ADR satisfies; the four open trade-offs that section explicitly deferred are resolved above.
- [ADR-0003: Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — Federation Architect is itself bound to a user-id under this ADR.
- [federation-arch.md](../federation-arch.md) — will absorb the `## User overrides` section in its v0.4.0 bump.
- [session-handoff.md](../session-handoff.md) — session-3 handoff lists ADR-0009 as the immediate blocker.
