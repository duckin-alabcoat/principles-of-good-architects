# ADR-0007: GitHub as system storage; data excluded by policy and gitignore

**Status:** Accepted
**Date:** 2026-05-21
**Deciders:** the operator, Federation Architect

## Context

the operator directed that the federation's **system files** — the infrastructure that defines how the federation operates — be persisted to GitHub and maintained by the Federation Architect without per-action approval. **Data** — content drawn from or synthesized from other systems' producer files and role docs — must never be committed.

The directive is explicit and blanket:

> The ruling, in paraphrase: the Federation Architect maintains the system files in GitHub and owns that repository; data never goes to GitHub; the operator does not approve individual commits or review their details — he needs only to know that system progress is stored there and that no data is.

This is a delegation of repository-maintenance authority, paired with a hard constraint on what can be stored. Both need to be operationalized: (a) a clear classification rule so I can sort future files correctly without asking each time, and (b) a mechanical enforcement (`.gitignore`) so accidents don't leak data.

## Decision

### Authority

The Federation Architect maintains the GitHub repository without per-commit or per-push approval. the operator does not review commits, branches, or PRs. He receives a single confirmation when the repo is first stood up, and brief sync confirmations as part of normal session summaries (e.g., "synced to GitHub"). No commit-message screenshots, no diff walkthroughs, no approval gates for routine work.

**Carve-out for destructive or irreversible operations.** Even under blanket authority, the Federation Architect surfaces and confirms before:
- Force-pushing, history rewriting, or branch deletion that could lose work.
- Deleting the repository.
- Making the repository public (if it was private), or vice versa.
- Adding collaborators or changing access settings.
- Pushing files that might be borderline-data (see classification rule below — when in doubt, the file stays local and gets flagged).

Routine commits, pushes, branch creation for feature work, and merges are not destructive and proceed without approval.

### Classification rule

Files are classified as **system** or **data**. Only system files go to GitHub.

**System files** define how the federation operates. They are infrastructure. Examples:
- ADRs (`adr/`)
- The Federation Architect role doc (`federation-arch.md`)
- Top-level docs (`README.md`, `PROCESS.md`)
- Naming convention, template, and process artifacts
- The founding brief (`architect-learnings-federation-brief.md`) — it is the spec, not content

System files may reference other systems by their codename or system ID (e.g., "a member," "an orchestrator agent"). Naming references are not data.

**Data files** are content drawn from or synthesized from other systems. They are operational substrate. Examples:
- Mirrored producer files: `inputs/<system-id>-learnings.md`
- Mirrored Architect role docs from other systems: `inputs/<system-id>-arch.md`
- The master principles doc: `principles/master.md` (synthesized from input data)
- The Federation Architect's own learnings file (when created): `architect-learnings.md` at project root
- Any future Auditor engagement reports stored locally
- Anything containing quoted producer content, even within an otherwise-system file

**Boundary cases**:
- **ADRs that need to explain context drawn from another system's incident** — write the context abstractly. Reference patterns and decisions, not quoted learnings. ADR-0005's a member corruption mention is abstract enough to qualify as system.
- **Memory files** (`~/.claude/projects/.../memory/`) live outside the repo entirely. They are operational continuity for the Federation Architect, not committed anywhere.
- **When in doubt:** the file stays local, and the question is surfaced to the operator before any commit involves it.

### Mechanical enforcement

A `.gitignore` at the repo root excludes data paths by pattern:

```
inputs/
principles/
architect-learnings.md
```

Any new data path added in the future updates `.gitignore` in the same commit that creates the path. The presence of a path-pattern is itself a declaration that the path holds data.

### Repository configuration

- **Account:** a private account (the active GitHub account at setup time).
- **Repo name:** `principles-of-good-architects` (matches working directory).
- **Visibility:** Private. Even though no data is stored, the system files reference internal portfolio details and orchestrator-agent codenames. Private is the default unless the operator directs otherwise.
- **Default branch:** `main`.

## Alternatives Considered

- **Skip GitHub; store everything locally with file-system backups.** Rejected — no versioning history, no off-machine durability, no shareability when an Auditor or new Architect needs to read system files cold.
- **Public repository.** Rejected as default — system files reference internal codenames and operational details. Cost is low to keep private; cost of accidental disclosure is non-trivial.
- **Store data in the same repo with stricter access controls or encryption.** Rejected — violates the explicit constraint ("Data should never go to github"). the operator's rule is categorical, not "data with controls."
- **Require approval for each commit despite blanket authority.** Rejected — directly contradicts the operator's ruling that routine commits need neither his approval nor his review.
- **Always confirm even destructive operations.** Adopted as a carve-out, not the default. Blanket authority does not extend to operations that could lose work or change visibility.

## Consequences

- The Federation Architect can commit and push freely as part of normal work. Each session summary includes a brief sync confirmation.
- `.gitignore` is load-bearing — if it drifts out of sync with reality, data leaks. Every new data path must be added in the same commit that introduces it.
- Future ADRs and role doc edits that quote from producer files would convert those files to data. To avoid this, ADRs write context abstractly and reference patterns rather than verbatim text.
- An Auditor or new Architect can clone the repo to get the full federation system context. They will not get any cross-system data — that has to be requested through the operator.
- A repository-level Auditor engagement (cf. ADR-0004) becomes practical — point an Auditor at the GitHub repo, brief them, get a clean cold-context review.
- The founding brief is committed as system. If the operator disagrees on this classification, he can flag it and we move it to local-only.

## References

- [ADR-0001: Record federation decisions as ADRs](0001-use-adrs.md) — establishes that this delegation of authority needs its own ADR.
- [ADR-0003: Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — the Federation Architect's accountability under this delegated authority.
- [federation-arch.md](../federation-arch.md) — role doc, updated to reflect this policy.
- [PROCESS.md](../PROCESS.md) — onboarding flow; will note that system files live in GitHub.
