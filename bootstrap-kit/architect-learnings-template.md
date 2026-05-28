# Architect learnings — <<ARCHITECT_NAME>>

This file is the producer-side capture surface for this Architect's durable observations. It is written exclusively by `<<ARCHITECT_ID>>`. Append-only — never edit or delete past entries.

The Federation Architect reads this file (mirrored at `inputs/<<ARCHITECT_ID>>-learnings.md` in the federation repo) and may propose role-doc edits to other Architects based on cross-system patterns; those edits go through the user via the [receipt ritual](<<FEDERATION_REPO_REF>>/adr/0013-receipt-ritual.md).

Per [ADR-0012](<<FEDERATION_REPO_REF>>/adr/0012-per-architect-repo-conventions.md), this file is **data** — gitignored in this repo's `.gitignore`. It lives on disk but is not committed to the system repo.

Per the `producer-side-learnings` universal habit (parent: P6, *observations-captured-at-source*), entries are captured **at the moment of observation, by this Architect, in this session**. Do not manufacture entries by backfilling or by synthesizing from session memory — that violates the habit.

## Entry format

```markdown
## YYYY-MM-DD · scope: <tag> · <Short Title>

<1–3 paragraphs of free-form prose: observation, principle, application, provenance.>
```

Headers are parseable: `## ` + ISO date + ` · scope: ` + tag + ` · ` + title. **Newest entries first.**

## Scope tags

- **`<<SYSTEM_ID>>-specific`** — applies only to this system. Filtered out of cross-system clustering unless it surfaces in multiple systems' specific tags, in which case the Federation Architect may propose promotion.
- **`architect-general`** — applies to any Architect role. Primary clustering target. Most entries should aim for this scope when applicable.
- **`domain-general`** — applies to any AI-collaborator-role design (Architects, Auditors, others). Highest leverage.
- **`auditor-general`** — applies to the Auditor role class (cold-context reviewers). See [ADR-0004 in the federation repo](<<FEDERATION_REPO_REF>>/adr/0004-auditor-as-separate-role-class.md).

If the observation clearly applies to *this Architect only*, use `<<SYSTEM_ID>>-specific`. If you find yourself wanting a fifth tag, stop and surface to <<USER_NAME>>.

## Entries

*No entries yet. The first entry lands when this Architect captures its first durable observation from actual work. Do not seed with synthetic content.*
