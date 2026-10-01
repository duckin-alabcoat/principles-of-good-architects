# ADR-0021: Cross-system status surface (per-system `STATUS.md`; federation publishes, does not aggregate)

**Status:** Accepted
**Date:** 2026-06-07
**Deciders:** the operator, Federation Architect

## Context

A **status consumer** — any tool or Architect that wants a portfolio-wide view — MAY report every project's current status. Such a view would **include the federation-managed systems** (the roster in [portfolio.md](../portfolio.md)). Projects that no Architect runs may exist too; wiring those into a consumer is the consumer's own job, and they are out of federation scope.

The boundary was fixed in the session-28 post-close routing conversation: **federation writes the status surface, the consumer reads it** ([P13](../principles/master.md#p13--single-writer-per-state) single-writer). Whoever builds a consumer builds the read side; federation owns the data contract.

the operator's explicit steer: **start as light as possible.** Not a federation aggregation engine (heed the session-21 over-engineering lesson, [ADR-0019](0019-curate-gather-and-staging-boundary.md)).

The crux is **freshness.** Stale status is worse than no status. Live per-system status is authored in each system's *own* repo at its own session-end; any status federation *mirrors* goes stale the moment another system has a session federation never saw. A design that makes federation the keeper-of-current-state fights the grain of how the systems actually run.

## Decision

**Status is authored at-source; federation publishes the roster and the schema; a consumer MAY read and compose. Federation does not aggregate.**

1. **At-source authoring.** Each system publishes a `STATUS.md` at its repo root, written/updated at that system's session-end by its own Architect. Single-writer per system ([P13](../principles/master.md#p13--single-writer-per-state)); captured at source ([P6](../principles/master.md#p6--observations-captured-at-source)). `STATUS.md` is **system** (git-tracked), in that system's own repo.

2. **The schema (the contract).** YAML frontmatter, six fields:

   ```yaml
   ---
   id: federation          # kebab system id, per ADR-0006
   phase: active           # bootstrapping | active | maintenance | paused | retired
   version: 1.5.0          # current role-doc semver
   last_active: 2026-06-07 # date of last session — the freshness signal
   focus: One line — current focus or what's owed.
   blocked: false          # false, or a short string naming the block
   ---
   ```

   `blocked` carries the y/n *and* the why in one field: `false` when clear, otherwise a short reason string. A human-readable prose line below the frontmatter is optional.

3. **Federation publishes roster + schema only.** [portfolio.md](../portfolio.md) remains the roster authority ([ADR-0006](0006-naming-convention-corrected.md)) and documents the `STATUS.md` convention; this ADR owns the schema. Federation does **not** read, mirror, or aggregate per-system status.

4. **A consumer reads and composes.** A consumer MAY pull the roster from federation, read each system's `STATUS.md`, and merge in any off-roster projects (its own domain). Aggregation happens at **read-time, on the consumer side** — the consumer's build, not federation's.

5. **Freshness via visible `last_active`, not real-time sync.** A consumer renders staleness (*"last touched 6 days ago"*) so stale status announces itself rather than masquerading as current. Stale-but-labeled beats stale-and-silent. There is no push/sync machinery.

6. **Federation dogfoods first.** Federation ships its own `STATUS.md` as the reference implementation ([ADR-0003](0003-federation-architect-is-a-participant.md) recursive participation) before the emit-status step is redistributed to other Architects.

## Alternatives Considered

- **Federation maintains an aggregated mirror of all systems' status.** Rejected — federation only runs in federation sessions, so the mirror goes stale the instant any other system has a session. Reintroduces the freshness problem and makes federation exactly the aggregation engine the operator ruled out.
- **Real-time / push sync** (each system pushes status to a shared store on every change). Rejected — over-engineered for the current scale (a handful of systems, low session frequency); the session-21 lesson. Visible `last_active` makes staleness tolerable without any sync machinery.
- **A single shared status file all Architects write to.** Rejected — violates single-writer ([P13](../principles/master.md#p13--single-writer-per-state)); write contention and muddy provenance. Per-repo `STATUS.md` keeps one writer per file.
- **Reusing portfolio.md's `Status` column as the whole surface.** Rejected — that column is coarse roster-grain lifecycle (Active/WIP/…); it carries no `last_active`, `focus`, or `blocked`, and federation isn't present to keep it live. It maps to the `phase` field only.

## Consequences

- **New constraint on every Architect:** update `STATUS.md` at session-end. Ships via a **redistribute pass** (one proposed-edit per Architect in `proposed-edits/<arch>/pending/`), gated per-edit by the operator. This cross-Architect constraint is why this is an ADR.
- **portfolio.md** gains a "Status surface" section documenting the convention and pointing here.
- **Federation's session-end ritual** gains a status-emit step (a [federation-arch.md](../federation-arch.md) substrate edit). Candidate fast-follow: automate it in `session.py end` so `last_active`/`version` are stamped without effort — federation's implementation detail, not part of the contract other Architects must meet by hand.
- **A consumer's read/compose side is its own builder's work.** The one thing a consumer must solve is *locating each system's repo* to read its `STATUS.md` (a local-checkout map or git remotes). Deliberately out of scope here — repo filesystem paths are machine-specific **data** ([P3](../principles/master.md#p3--data-system-separation)) and do not belong in the system roster.
- **`phase` overlaps portfolio.md's `Status` column** — they should agree. Portfolio is roster-grain; `STATUS.md` is the live per-system surface.
- **Framing:** an extension of federation's existing portfolio-maintenance and **Redistribute** rails, not a new (4th) mission responsibility.

## References

- [portfolio.md](../portfolio.md) — roster authority extended here.
- [ADR-0006](0006-naming-convention-corrected.md) — naming convention; system ids used as `STATUS.md` `id`.
- [ADR-0009](0009-data-storage-mechanics.md) — data/system storage mechanics (`STATUS.md` is system; repo paths are data).
- [ADR-0003](0003-federation-architect-is-a-participant.md) — recursive participation; federation dogfoods the schema first.
- [ADR-0019](0019-curate-gather-and-staging-boundary.md) — the start-light / don't-over-engineer precedent.
- [P13](../principles/master.md#p13--single-writer-per-state), [P6](../principles/master.md#p6--observations-captured-at-source), [P3](../principles/master.md#p3--data-system-separation).
- session-28 handoff (2026-06-06) — the routing decision that scoped this work.
