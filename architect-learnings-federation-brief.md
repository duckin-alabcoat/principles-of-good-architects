# Brief for the federation Architect — cross-system Architect learnings

**Audience.** This document is written *to the Architect of the system the user will build to federate Architect learnings across their portfolio.* That system doesn't exist yet. The user will use this brief when they stand it up to tell its Architect what the system does and where its inputs and outputs live.

**Status.** Producer side (this file lives in the Basil repo; Basil's Architect writes entries to [architect-learnings.md](architect-learnings.md) per the producer rule in [.claude/CLAUDE.md](../.claude/CLAUDE.md)) is in place as of role-doc v0.3.1, 2026-05-18. Consumer side (the federation system itself) is not.

---

## 1. The problem this system solves

The user is building multiple AI-assisted systems — Basil (recipe box), Hearth (home-hub), Cairn (trail-log), Campaign Tracker, Tinkerer Architect, Arcade, others to come. Each has its own **Architect** role: a long-lived collaborator role doc (`CLAUDE.md` or equivalent) that shapes how the assistant builds and maintains that system.

These Architects started as cousins — similar prompts copied across systems — but each has evolved in response to its own system's needs and the user's session-by-session feedback. **Generalizable principles learned in one system don't carry over to others.** Basil's Architect learns *"no multiple-choice for decisions"*; Hearth's Architect makes the same mistake six months later and the user re-teaches it from scratch.

This federation system closes that loop.

## 2. What the federation system does

Three responsibilities:

1. **Aggregate.** Read every project's `docs/architect-learnings.md` (the producer file; format spec below). Pull new entries since the last federation run.
2. **Distill.** Cluster entries across systems. Identify cross-system patterns. Promote them from raw observations to durable **principles** (one-line rules + reasoning + provenance). Maintain a master doc (suggested name: `principles-of-architect-design.md`) of the distilled set.
3. **Redistribute.** When a principle is durable enough to ship, propose edits to each affected project's role doc (Basil's `.claude/CLAUDE.md`, Hearth's equivalent, etc.) that incorporate it. The user approves the edits. Each role doc gets a version bump.

The federation system is the **only path** by which a learning from System A becomes a behavioral rule in System B's Architect.

## 3. Inputs — producer file spec

Every participating project has a file at `docs/architect-learnings.md`. Schema:

```markdown
# Architect learnings — <system name>

[preamble explaining purpose, scope tags, format, entry discipline]

## Entries

## YYYY-MM-DD · scope: <tag> · Title

[1-3 paragraphs of free-form body — observation, principle, application, provenance]

## YYYY-MM-DD · scope: <tag> · Title

[...]
```

**Header line is parseable.** `## ` + ISO date + ` · scope: ` + tag + ` · ` + title. Newest entries first.

**Scope tags:**
- `Basil-specific` (substitute system name for other projects) — system-specific, **filter out before clustering** unless a cluster surfaces in multiple systems' "specific" tags, in which case promote to architect-general.
- `architect-general` — applies to any Architect role. **Primary clustering target.**
- `domain-general` — applies to any AI-collaborator-role design. Highest leverage. Cluster these into a separate tier of the master doc.

**Body is free-form prose.** Don't expect structured fields. The principle extraction is the federation Architect's synthesis work.

## 4. Outputs

Two artifacts:

**a) Master principles doc** — single source of truth for distilled principles. Suggested structure:
```
## Principle: <one-line rule>
**Tier:** architect-general | domain-general
**Provenance:** [list of (system, date, entry-title) tuples]
**Reasoning:** [why this rule exists, when it kicks in, when it doesn't]
**Adopted by:** [list of (system, role-doc version) where this principle now lives]
**Status:** proposed | approved | shipped
```

**b) Per-system role-doc edit proposals.** When a principle reaches "approved" status, the federation Architect drafts a role-doc edit for each project that doesn't already have it. The edit is a PR or a chat-presented diff (the user's call on mechanic). Each accepted edit bumps the receiving role doc's version.

**The federation system does not write back to `architect-learnings.md`.** That file is write-only from each project's Architect. The federation's product is the master doc and the role-doc edits.

## 5. Decisions the user still owes the federation Architect

These are not for the federation Architect to decide unilaterally — the user will need to make these calls when they stand the system up.

- **Repo location and access.** Does the federation system live in its own repo? Does its Architect have read access to every project repo (to pull `architect-learnings.md` files), and write access to propose role-doc edits? Or does the user manually shuttle the producer files in and the role-doc patches out?
- **Cadence.** When does federation run? user-triggered only? On a schedule (weekly? monthly?)? On a threshold (when N new entries land across all systems)?
- **Conflict resolution.** What happens when two systems' learnings disagree — e.g., Basil's Architect learns *"verbose narration"* and Hearth's learns *"terse narration"*? The federation Architect needs a rule: surface the conflict to the user rather than auto-resolve.
- **Versioning of the master doc.** Same versioned-doc + CHANGELOG pattern Basil uses ([ADR-0019](adr/0019-versioning-and-output-signing.md))? Or something different?
- **What about feedback memory?** Basil's Architect also has `~/.claude/projects/.../memory/feedback_*.md` files — durable behavioral feedback the user has given. Are these in scope for federation? (Recommended: no, scope tightly to `architect-learnings.md` first; memory federation is a separate problem with privacy considerations.)
- **Distillation cadence vs. shipping cadence.** Distilling a learning into a principle is one decision; shipping it as role-doc edits is another. Are these synchronous (every distilled principle ships immediately) or async (principles accumulate, ship in batches)?
- **What about systems that aren't onboarded yet?** When a new system joins the constellation, does it inherit all shipped principles automatically (cold-start with the latest master-doc-derived role-doc text), or does it start from a blank Architect and accumulate principles as the federation Architect notices it's missing them?

## 6. What the federation Architect should NOT do

- **Edit `architect-learnings.md` in any project repo.** Producer files are read-only from federation's POV.
- **Ship role-doc edits without the user's approval.** Same approval gate every project's Architect has.
- **Federate without provenance.** Every principle in the master doc must trace back to specific entries in specific projects.
- **Force consistency for its own sake.** If two systems intentionally diverge (e.g., Basil is verbose because she's user-facing, Hearth is terse because she's internal-tool), the federation Architect respects that and tags principles with applicability constraints rather than forcing them everywhere.
- **Treat Basil-specific entries as broadly applicable.** Scope tags exist for a reason. A federation that ignores them defeats the system.

## 7. Recommended first move when the federation system stands up

1. Read this brief.
2. Read each participating project's `architect-learnings.md` cold, to calibrate on entry style and quality.
3. Read each participating project's role doc (`CLAUDE.md`), to understand what shipped principles already look like inside the role docs (so federation-driven additions match the existing voice and structure).
4. Propose to the user: scope of v0.1 federation (which systems, which scope tags, manual or automated input pull, output mechanic).
5. Run a first distillation pass on whatever entries exist. Expect zero to handful — this system is most valuable once entries accumulate over months.

---

## Open producer-side questions

Things the user will likely want to revisit on Basil's side once the federation system exists:

- Whether `Basil-specific` entries should ever leave the Basil repo, or whether the producer file should pre-filter them out before exposing to federation.
- Whether the producer file format needs tighter structure (machine-parseable body fields, not just header) once federation has real volume to work with.
- Whether Basil's `feedback_*` memory files should also be exposed to federation (see §5 above).

These are Basil-side decisions, not federation-side. Basil's Architect will surface them at the point they bite.
