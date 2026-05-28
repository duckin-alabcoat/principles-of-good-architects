# Federation participation — onboarding and ongoing flow

This document is the operator's runbook for bringing an Architect into the federation and keeping it participating thereafter. Audience: the user, future operators, and the Federation Architect itself ([ADR-0003](adr/0003-federation-architect-is-a-participant.md) — Federation Architect is also a participant).

PROCESS.md drives the [bootstrap kit](bootstrap-kit/). The kit is the templated scaffolding; this doc is the runbook that uses it.

## Onboarding paths

Two paths, picked at the start of onboarding based on the candidate Architect's state:

- **Fresh Architect** — repo doesn't exist yet, or exists empty. Uses the [bootstrap kit](bootstrap-kit/) to install federation-spec'd scaffolding from cold. **This doc's runbook covers this path.**
- **Existing Architect (retrofit)** — repo exists with an evolved role doc and ongoing practice (Recipe Box Architect, Hearth/Home Hub, Cairn/Trail Log, Campaign Tracker, Arcade). Uses the retrofit mechanism in [ADR-0014](adr/0014-existing-architect-retrofit.md). The retrofit reconciles existing practice against federation conventions; it does *not* use the bootstrap kit. PROCESS.md does not duplicate the retrofit spec — read ADR-0014 directly.

If unsure which path applies: if the candidate Architect has *any* committed role-doc content beyond template skeleton, go retrofit. If it's a cold start, go fresh.

## Fresh-Architect onboarding steps

The runbook. Follow in order. Each step lands a discrete piece of state.

### 1. Assign IDs

Per [ADR-0006](adr/0006-naming-convention-corrected.md) (naming convention — 4-slot):

- **Function name** (descriptive, e.g., "Recipe Box," "Home Hub")
- **Canonical Architect name** (e.g., "Recipe Box Architect," "Home Hub Architect")
- **System ID** (kebab, e.g., `recipe-box`, `home-hub`)
- **Architect ID** (kebab, e.g., `recipe-box-arch`, `home-hub-arch`)
- **Orchestrator agent** (if any — e.g., "Basil," "Hearth"). Not every system has one.

Add the row to [portfolio.md](portfolio.md) in the **federation repo** (this repo, not the new Architect's repo). The portfolio is a federation-owned singleton — new Architects are registered in it, not bearers of it.

### 2. Choose the data root

Per [ADR-0011](adr/0011-data-root-config.md), every Architect declares a `Data root:` value in its role doc. Absolute path, per-machine.

The data root is where the new Architect's *data* lives (producer file, registries, receipt-ritual inbox). The system repo lives separately under the operator's normal git working area. Data root and system repo can be siblings, parent/child, or wholly separate locations — that's the operator's call.

Multi-machine note: if the Architect will run on more than one machine, each machine declares its own data-root path. Cross-machine continuity rides authority-bearing artifacts (role doc, ADRs, session-handoff), not memory ([ADR-0016](adr/0016-memory-is-local.md)).

### 3. Create the Architect's GitHub repo

Per [ADR-0012](adr/0012-per-architect-repo-conventions.md), each Architect has its own GitHub repo for system files. Standard layout:

```
<architect-repo>/
├── <architect-id>.md         # role doc (per Open-for-the user #1 resolved)
├── adr/                      # this Architect's ADRs
│   ├── README.md             # index
│   └── template.md
├── architect-learnings.md    # producer file (gitignored)
├── session-handoff.md        # per-session state transfer
├── .gitignore
├── .claude/settings.json     # harness allow/deny lists
└── README.md
```

Create the empty repo; clone it locally. The operator's clone path becomes the system-repo root.

### 4. Copy kit files into the new repo

The bootstrap kit lives at [`bootstrap-kit/`](bootstrap-kit/) in this (federation) repo. Per the kit's currently-shipped version (`Kit version: v0.6.0`), copy:

| Kit file | Destination in new repo | Rename |
|---|---|---|
| `claude-md-template.md` | repo root | `CLAUDE.md` |
| `role-doc-template.md` | repo root | `<architect-id>.md` |
| `gitignore-template` | repo root | `.gitignore` |
| `adr-readme-template.md` | `adr/` | `README.md` |
| `adr-template.md` | `adr/` | `template.md` |
| `session-handoff-template.md` | repo root | `session-handoff.md` |
| `architect-learnings-template.md` | repo root | `architect-learnings.md` |
| `claude-settings-template.json` | `.claude/` | `settings.json` |
| `readme-template.md` | repo root | `README.md` |

Skip `bootstrap-kit/README.md` itself — that's the kit's own doc, not a deliverable into the new repo.

Record the kit version used (`v0.6.0` at time of writing) — the first session-handoff entry in the new repo cites it.

**Why `CLAUDE.md` matters:** Claude Code auto-loads `CLAUDE.md` (and not `<architect-id>.md`) as project context at session start. Without `CLAUDE.md`, the role doc never reaches the Architect's first turn and the §11 session-start protocol silently no-ops — the Architect responds as a generic Claude Code instance with no identity, no git refresh, no stamp. This was discovered when Plant Care Architect's first kit-driven session failed in exactly this mode (federation session 13, 2026-05-27). Kit v0.6.0 added `claude-md-template.md` to close the gap.

### 5. Find-and-replace token substitution

Templates use `<<TOKEN_NAME>>` markers. Replace each across all copied files:

| Token | Value | Example |
|---|---|---|
| `<<ARCHITECT_NAME>>` | Canonical Architect name | `Home Hub Architect` |
| `<<ARCHITECT_ID>>` | Kebab Architect ID | `home-hub-arch` |
| `<<SYSTEM_ID>>` | Kebab system ID | `home-hub` |
| `<<SYSTEM_NAME>>` | Function name | `Home Hub` |
| `<<DATA_ROOT>>` | Absolute path chosen in step 2 | `/Users/user/home-hub` |
| `<<USER_ID>>` | Bound user ID | `user` |
| `<<ORCHESTRATOR_AGENT>>` | Orchestrator agent name or `(none)` | `Hearth` |
| `<<ORCHESTRATOR_AGENT_CLAUSE>>` | Full sentence — see role-doc-template.md inline guidance | (template-resolved) |
| `<<TODAY>>` | ISO date of onboarding | `2026-05-27` |
| `<<TODAY_TIME>>` | Start time of bootstrap session in `HH:MM <<TIMEZONE>>` | `08:17 CDT` |
| `<<TODAY_END_TIME>>` | End time of bootstrap session in `HH:MM <<TIMEZONE>>` | `09:42 CDT` |
| `<<TODAY_DURATION>>` | Bootstrap-session duration | `1h 25m` |
| `<<TODAY_MACHINE>>` | Machine label where bootstrap ran | `Laptop` |
| `<<TIMEZONE>>` | Architect's pinned timezone (short label for stamps) | `CDT` |
| `<<TIMEZONE_LONG>>` | Architect's pinned timezone (IANA name for `TZ=…` env) | `America/Chicago` |
| `<<MACHINE_LABELS>>` | Short-label mapping for the user's machines (comma-list) | `Laptop, Workstation` |
| `<<FEDERATION_REPO_REF>>` | Path or URL to federation repo for cross-refs | `../principles-of-good-architects` or full URL |
| `<<MISSION_PROSE>>` | Free-form mission paragraphs (per role-doc inline guidance) | (operator writes) |
| `<<SCOPE_DO_BULLETS>>` | Architect-specific scope bullets | (operator writes) |
| `<<SCOPE_DONT_BULLETS>>` | Architect-specific don't-do bullets | (operator writes) |

Tokens marked "(operator writes)" or "(template-resolved)" require human content, not mechanical substitution — the role-doc template carries inline guidance blocks for each. Delete the guidance blocks after writing.

### 6. Create the receipt-ritual inbox under data root

Per [ADR-0013](adr/0013-receipt-ritual.md), the receipt-ritual inbox lives under data root. Run:

```sh
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/pending
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/applied
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/rejected
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/withdrawn
```

(Substitute the actual data-root path and Architect ID.) These directories are data — under data root, gitignored from the system repo, not committed.

### 7. Initial commit

Per ADR-0012's blanket-maintenance authority, the Architect commits its own system repo. First commit lands the kit'd files. Conventional Commits message style (per the `conventional-commits` universal habit):

```
feat: stand up <architect-id> system repo from bootstrap kit v0.1.0
```

Push to `main`.

### 8. Register with Federation Architect

Open a Federation Architect session (in the federation repo). Tell it the new Architect is onboarded; provide the system-repo URL. Federation Architect:

- Confirms the portfolio entry.
- Mirrors the new role doc into `inputs/<architect-id>-arch.md` (gitignored).
- Notes the onboarding event in `session-handoff.md` (federation repo).
- May, in the same or a follow-up session, draft an initial principle/habit shipment via the receipt ritual if anything in the new Architect's mission warrants explicit codification.

After step 8, the Architect is federation-bound and operational.

## What the kit installs

See [`bootstrap-kit/README.md`](bootstrap-kit/README.md) for the canonical list. High level: role doc, gitignore, ADR scaffolding, session-handoff scaffold, producer file scaffold, Claude Code settings, repo README. Pre-populated with the 10 federation principles and 15 universal habits as of the kit's shipped version.

What the kit does NOT install:

- **Memory-sync infrastructure** — [ADR-0016](adr/0016-memory-is-local.md). Architects must not build memory-sync. Cross-machine continuity is the role-doc + ADR + session-handoff job.
- **A briefs/ directory** — per [ADR-0015](adr/0015-work-package-briefs.md) Resolutions item 3, briefs are Federation-Architect-only.
- **Backfilled producer-file entries** — `producer-side-learnings` habit prohibits manufacturing.
- **A data-root directory tree as committed files** — data root is per-machine, gitignored from the system repo. The `mkdir` in step 6 creates the inbox paths in the operator's local data root.

## The producer file

Each Architect maintains `architect-learnings.md` at its repo root. Producer-side, append-only, gitignored (data).

### Schema

```markdown
# Architect learnings — <System Name>

<Preamble: purpose, scope tags, format, entry discipline.>

## Entries

## YYYY-MM-DD · scope: <tag> · <Short Title>

<1–3 paragraphs: observation, principle, application, provenance.>
```

Headers parseable: `## ` + ISO date + ` · scope: ` + tag + ` · ` + title. Newest entries first.

### Scope tags

- **`<system-id>-specific`** — applies only to this system. Filtered out of cross-system clustering unless multiple systems surface it.
- **`architect-general`** — applies to any Architect role. Primary clustering target.
- **`domain-general`** — applies to any AI-collaborator role (Architects, Auditors, others). Highest leverage.
- **`auditor-general`** — applies to the Auditor role class per [ADR-0004](adr/0004-auditor-as-separate-role-class.md).

### Producer-rule snippet

The kit's `role-doc-template.md` includes a §"Architect-learnings discipline" section that captures this for the Architect's own role doc. No separate action needed during onboarding — it ships with the template.

## Flow: from learning to shipped principle or habit

```
┌─────────────────────────────────────────┐
│ Architect captures an entry in          │
│ architect-learnings.md (producer-side)  │
└──────────┬──────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────┐
│ Federation Architect aggregates entries │
│ on cadence (mirrors into inputs/)       │
└──────────┬──────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────┐
│ Distillation: cluster, classify per     │
│ ADR-0008 (principle / habit /           │
│ preference). Promote candidates to      │
│ status: Proposed in the registries.     │
└──────────┬──────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────┐
│ the user reviews — approves, refines, or    │
│ rejects. Approved candidates flip to    │
│ Accepted in the registries.             │
└──────────┬──────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────┐
│ Federation Architect drafts receipt-    │
│ ritual edits (one per affected          │
│ Architect) and drops them in            │
│ <data-root>/proposed-edits/             │
│ <architect-id>/pending/                 │
│ per ADR-0013.                           │
└──────────┬──────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────┐
│ Each affected Architect's next session  │
│ checks its pending/ inbox, walks the user   │
│ through each edit, applies/rejects/     │
│ withdraws per the receipt ritual.       │
│ Applied edits land in the Architect's   │
│ role doc; role doc CHANGELOG bumps.     │
└─────────────────────────────────────────┘
```

The receipt-ritual mechanism ([ADR-0013](adr/0013-receipt-ritual.md)) is the load-bearing infrastructure for the last two stages. New principles and universal habits propagate through this flow; the bootstrap kit pre-loads the *initial* set at onboarding-time so a fresh Architect doesn't have to receipt-ritual-adopt every principle on day one.

## Ongoing federation participation

After onboarding, each Architect:

1. **Captures learnings** at session end into its `architect-learnings.md` when there's something durable to capture. Zero is fine — don't manufacture.
2. **Checks its pending/ inbox** at session start. Any new federation-proposed edits get walked through with the user via the receipt ritual. Applied edits land in the role doc; the receipt moves to applied/ (or rejected/ or withdrawn/).
3. **Updates session-handoff.md** at session end with the standard entry format.
4. **Commits and pushes** per the `session-end-git-ritual` universal habit.

The Federation Architect runs aggregation on cadence (currently user-triggered — see Cadence below) and proposes new edits via the receipt-ritual mechanism. Architects don't push to federation; federation reads producer files.

## What the federation does NOT do

- **Edit producer files.** Producer files are write-only from their Architect; read-only from federation's POV.
- **Ship role-doc edits without the user's approval.** Always a gate. The receipt ritual is the mechanism but user-approval is the gate.
- **Force consistency.** When systems intentionally diverge, principles are tagged with applicability constraints (or universal habits get per-Architect overrides per [ADR-0009](adr/0009-data-storage-mechanics.md) §"Overrides").
- **Federate without provenance.** Every principle and habit traces back to specific entries in specific Architects' producer files or role docs.
- **Sync memory between machines.** [ADR-0016](adr/0016-memory-is-local.md). Memory is local; if a memory is load-bearing enough to need to survive a cross-machine restart, promote it to role doc / habit / ADR storage.

## Cadence

Currently undetermined — open question in [federation-arch.md §13](federation-arch.md#13-open-questions). Working assumption: **user-triggered.** Federation runs when the user asks for it. Revisit when producer files start accumulating real volume.

## Conflict resolution

When two systems' learnings disagree (Recipe Box: "verbose narration helps"; Home Hub: "terse narration is essential") — the Federation Architect **surfaces the conflict to the user rather than auto-resolving**. Likely outcomes:

- **Genuine divergence** → tag the principle/habit with an applicability constraint. Both systems hold their position.
- **One is wrong** → corrected position becomes the principle. Other system gets a receipt-ritual edit to align.
- **Both are wrong** → the user specifies the right answer. Principle reflects that.

Federation Architect does not unilaterally resolve.

## References

- [federation-arch.md](federation-arch.md) — Federation Architect role doc; the prototype the kit's role-doc template is modeled on.
- [bootstrap-kit/README.md](bootstrap-kit/README.md) — kit contents, versioning, kit-local open items.
- [adr/0003](adr/0003-federation-architect-is-a-participant.md) — recursive participation principle.
- [adr/0006](adr/0006-naming-convention-corrected.md) — naming convention (used in step 1).
- [adr/0007](adr/0007-github-as-system-storage-data-excluded.md) — system/data classification.
- [adr/0008](adr/0008-three-bucket-taxonomy.md) — principle/habit/preference taxonomy.
- [adr/0009](adr/0009-data-storage-mechanics.md) — data storage mechanics; user overrides.
- [adr/0010](adr/0010-registries-canon-only-sidecar-history.md) — registries are canon-only; role docs are the adoption record.
- [adr/0011](adr/0011-data-root-config.md) — `Data root:` declaration.
- [adr/0012](adr/0012-per-architect-repo-conventions.md) — per-Architect repo conventions.
- [adr/0013](adr/0013-receipt-ritual.md) — receipt ritual; pending/applied/rejected/withdrawn inbox.
- [adr/0014](adr/0014-existing-architect-retrofit.md) — existing-Architect retrofit path (alternative to this kit, for already-evolved Architects).
- [adr/0015](adr/0015-work-package-briefs.md) — work-package briefs (Federation-Architect-only parallelism).
- [adr/0016](adr/0016-memory-is-local.md) — memory is local; no memory-sync infrastructure.
