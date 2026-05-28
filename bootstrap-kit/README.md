# Bootstrap kit

**Kit version:** v0.6.2
**Last updated:** 2026-05-28

The bootstrap kit is the templated scaffolding that stands up a **fresh** federation-participating Architect's repo from cold. The onboarding operator (the user, or the Federation Architect on the user's behalf) copies these files into the new Architect's repo, fills in the substitution tokens per [PROCESS.md §5](../PROCESS.md), and runs the receipt-ritual inbox `mkdir` commands. After that, the new Architect has a federation-spec'd repo and can take its first session.

This kit is for the **fresh-Architect** path only. Existing Architects (Recipe Box, Hearth/Home Hub, Cairn/Trail Log, Campaign Tracker, Arcade) onboard via the retrofit path specified in [ADR-0014](../adr/0014-existing-architect-retrofit.md). The retrofit path reconciles an already-evolved Architect's repo against current federation conventions; it does not use this kit.

The kit is driven by [PROCESS.md](../PROCESS.md) §"Fresh-Architect onboarding steps." Reading this README in isolation tells you *what* is in the kit; PROCESS.md tells you *how* to use it.

## Contents

| File | Purpose |
|---|---|
| `README.md` | This file — kit overview, contents, open items. |
| `claude-md-template.md` | Single-purpose Claude Code auto-load trigger. Renamed to `CLAUDE.md` at the new repo's root; tells Claude on session start to read the role doc and execute §11 session-start protocol before responding. Added in kit v0.6.0 — without it, the session-start ritual silently fails to fire on a fresh Architect (Claude Code auto-loads `CLAUDE.md`, not `<architect-id>.md`; fresh projects have no memory to compensate). |
| `role-doc-template.md` | Full role-doc skeleton modeled on [federation-arch.md](../federation-arch.md). Top-metadata block (incl. `Data root:` per [ADR-0011](../adr/0011-data-root-config.md)), §1 Identity through §13 Open questions, References, CHANGELOG. Pre-populates the §4.1 principles table with P1–P13 (see Open-for-the user item 2). |
| `gitignore-template` | `.gitignore` patterns enforcing the [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) / [ADR-0012](../adr/0012-per-architect-repo-conventions.md) data-vs-system boundary. Covers data-layer directories, OS/editor cruft, local-only working files, Claude Code transient files. |
| `adr-readme-template.md` | ADR index template. Modeled on [adr/README.md](../adr/README.md). |
| `adr-template.md` | Verbatim copy of the federation's [adr/template.md](../adr/template.md). Used unchanged for new Architects. |
| `session-handoff-template.md` | Empty session-handoff with header and entry format. Newest-first convention noted. |
| `architect-learnings-template.md` | Producer file template — preamble, scope-tag list, entry format. Empty (no backfilled entries — would violate the `producer-side-learnings` habit's "don't manufacture" rule). |
| `claude-settings-template.json` | Generalized version of the federation's [`.claude/settings.json`](../.claude/settings.json). Routine-git allow-list, destructive-op deny-list. See Open-for-the user item 5. |
| `readme-template.md` | Basic repo-root README for the new Architect's repo. Points at the role doc, the ADR index, PROCESS.md (in the federation repo), session-handoff. |

## What the kit does NOT include

- **Memory-sync infrastructure.** Forbidden by [ADR-0016](../adr/0016-memory-is-local.md). Memory is local to each machine; cross-machine continuity rides ADRs, role doc, session-handoff, and data-root registries — not memory. A new Architect must not build memory-sync; if a memory feels load-bearing, promote it to role doc / habit / ADR.
- **A pre-populated `architect-learnings.md` with backfilled entries.** Forbidden by the `producer-side-learnings` universal habit — entries must be captured at source, not manufactured. The template is intentionally empty.
- **A `briefs/` directory.** Per [ADR-0015](../adr/0015-work-package-briefs.md) §"Resolutions at Accept" item 3, briefs are Federation-Architect-only. Participating Architects do not have their own `briefs/` directory.
- **A `<data-root>/` directory tree as committed files.** The data root is per-machine and lives outside the system repo (gitignored). PROCESS.md instructs the operator to create the receipt-ritual inbox paths (`<data-root>/proposed-edits/<architect-id>/{pending,applied,rejected,withdrawn}/`) with `mkdir` after choosing the data root.

## How PROCESS.md drives the kit

PROCESS.md owns the runbook. The operator follows PROCESS.md §"Fresh-Architect onboarding steps" in order:

1. Assign IDs (canonical name → system-id → architect-id), update [portfolio.md](../portfolio.md).
2. Choose the new Architect's data-root path (per [ADR-0011](../adr/0011-data-root-config.md) — absolute path, per-machine).
3. Create or clone the new Architect's GitHub repo (per [ADR-0012](../adr/0012-per-architect-repo-conventions.md)).
4. Copy these kit files into the new repo, renaming as appropriate (e.g., `role-doc-template.md` becomes `<architect-id>.md`).
5. Find-and-replace `<<TOKEN_NAME>>` substitution markers per PROCESS.md's token table.
6. Run the `mkdir` commands to create the receipt-ritual inbox under data root.
7. Initial commit of the new Architect's repo (per ADR-0012 blanket-maintenance authority).
8. Register the new Architect with the Federation Architect (Federation Architect mirrors the role doc into `inputs/` and confirms federation binding).

The substitution is normally done *by the executing agent* — Claude, running as the Federation Architect — which gathers the token values from the operator (asking for anything it can't safely infer) and applies them. A human can do the find-and-replace by hand as a fallback, but the kit is designed to be agent-driven; the kit files themselves do not auto-substitute.

## Token convention

Templates use `<<TOKEN_NAME>>` (double angle brackets, uppercase, underscore-separated) for substitution markers. PROCESS.md §5 is the canonical list of every token and what it resolves to. Operator runs find-and-replace once per token at install time.

Example tokens (non-exhaustive):
- `<<ARCHITECT_NAME>>` — canonical name (e.g., "Home Hub Architect").
- `<<ARCHITECT_ID>>` — kebab ID (e.g., `home-hub-arch`).
- `<<SYSTEM_ID>>` — system kebab ID (e.g., `home-hub`).
- `<<DATA_ROOT>>` — absolute path chosen at step 2.
- `<<USER_ID>>` — bound user (e.g., `user`).
- `<<ORCHESTRATOR_AGENT>>` — orchestrator agent name if any (e.g., "Hearth"), or `(none)` for no-orchestrator Architects.
- `<<TODAY>>` — ISO date of onboarding (e.g., `2026-05-26`).

## Versioning

This kit is **versioned** with semver, declared in the top metadata block (`Kit version: vX.Y.Z`). Initial version `v0.1.0` shipped 2026-05-26.

Bump rules:

- **MAJOR** — incompatible template changes (existing onboardings would need re-render to remain conformant; e.g., a token rename, a removed required file).
- **MINOR** — additive (new template file, new token, new section in an existing template).
- **PATCH** — wording, clarification, doc-only corrections.

Bumps happen in the same commit as the kit-contents change. PROCESS.md's onboarding runbook captures the kit version at onboarding-time in the new Architect's session-handoff so the kit version is findable from the Architect's own logs without spelunking the federation repo.

**Version history:**

- **v0.6.2** (2026-05-28) — Onboarding reframed agent-driven (federation session 15). The runbook is now explicitly executed *by Claude* (running as the Federation Architect), which gathers token values from the operator via a short intake and applies them — rather than the operator hand-doing find-and-replace. [PROCESS.md](../PROCESS.md) §"Fresh-Architect onboarding steps" gains a "How to run this — agent-driven by default" note; step 5 retitled "Fill in the template tokens" and rewritten to instruct the agent to *ask* only about genuine choices (project naming, data-root path, how to address the user) while auto-detecting what the system already knows (timezone via `date +%Z`, machine via `hostname`) rather than asking for it. This README's "How PROCESS.md drives the kit" line corrected (was "the operator does the find-and-replace; the kit does not auto-substitute" — which under-sold the agent path and made manual substitution sound mandatory). Manual remains a documented fallback. Patch — doc-only; no template or token changes.
- **v0.6.1** (2026-05-28) — Documentation completeness fix (federation session 15). Surfaced by a "could this bootstrap an Architect on Linux?" review: the kit is OS-portable (machine-detect is `hostname`-primary, `scutil` only an optional macOS nicety; timestamps via `TZ=… date`; settings.json is git/gh/mkdir/ls/cat — no macOS-locked command), but the [PROCESS.md §5](../PROCESS.md) token table was missing ~12 tokens the templates actually use — most consequentially `<<USER_NAME>>` (used throughout `role-doc-template.md` and `claude-md-template.md` with no inline guidance), plus `<<REPO_OWNER>>`, `<<REPO_NAME>>`, `<<HABITS_TABLE>>`, `<<SYSTEM_SPECIFIC_PRINCIPLES>>`, `<<VOICE_STYLE_BULLETS>>`, the `<<EXTRA_*>>` and `<<ARCHITECT_SPECIFIC_*>>` rows. A strict find-and-replace from the table would have left unsubstituted placeholders. PROCESS.md §5 table completed. Stale counts refreshed: PROCESS.md "10 principles / 15 habits" → "13 / 21"; this README's contents-table "P1–P10" → "P1–P13" and Open-items 2–3 counts; `role-doc-template.md` CHANGELOG `0.1.0` line corrected from "10 principles (P1–P10) … v0.7.0 habits" to the current 13/21 set. Not a Linux-specific issue — would have tripped any from-the-docs bootstrap on any OS. Patch — doc-only corrections; no template behavioral change, no new tokens, no incompatible changes.
- **v0.6.0** (2026-05-27) — Adds `claude-md-template.md` — a small Claude Code auto-load trigger that points at the role doc and instructs §11 session-start execution before first response. Fixes a structural gap discovered when Plant Care Architect's first kit-driven session (session 13 in the federation, 2026-05-27) failed to fire the session-start ritual: Claude Code auto-loads `CLAUDE.md` but not `<architect-id>.md`, and a fresh project has no memory entries to compensate, so the ritual silently no-ops on first run. PROCESS.md step 4 table gains a row for the new file. No new tokens (uses existing `<<ARCHITECT_NAME>>`, `<<ARCHITECT_ID>>`, `<<USER_NAME>>`). Minor — additive template; no incompatible template changes.
- **v0.5.0** (2026-05-27) — `role-doc-template.md` `<<HABITS_TABLE>>` guidance updated to 21 habits (adds `no-fabricated-data` under P7). §4.1 header version stamp bumped to federation-arch.md v0.11.0. Minor — additive surface, no incompatible template changes.
- **v0.4.0** (2026-05-27) — `role-doc-template.md` `<<HABITS_TABLE>>` guidance updated to 20 habits (adds `no-compound-bash` under P7). §4.1 header version stamp bumped to federation-arch.md v0.10.0. Minor — additive surface, no incompatible template changes.
- **v0.3.0** (2026-05-27) — `role-doc-template.md` §4.1 expanded to v0.9.0 set: 10 → 13 principles (adds P11 `ship-working-system-on-time`, P12 `manage-scope-for-schedule`, P13 `single-writer-per-state`). `<<HABITS_TABLE>>` guidance updated to 19 habits (adds `mid-session-checkpointing` and `emergency-write-and-checkpoint-commands` under P5). §4.1 header version stamp bumped to federation-arch.md v0.9.0. Stale "Note: P11 and P12 are not yet adopted" footnote removed (resolved by the v0.9.0 §4.1 cleanup). Minor — additive surface, no incompatible template changes.
- **v0.2.0** (2026-05-27) — `role-doc-template.md` §11 rewritten to mirror federation-arch.md v0.8.0 (lifts home-hub-arch session protocol): silent git refresh, machine auto-detect, timezone-stamped session stamps, monotonic session counter via SESSION LOG table, orphan detection with `NC` tagging. `session-handoff-template.md` adds SESSION LOG table and `Start:` / `End:` stamp lines on the bootstrap entry. `<<HABITS_TABLE>>` guidance list updated to v0.8.0 set (15 → 17 habits — adds `session-stamp-and-counter` and `session-orphan-detection`). New tokens introduced: `<<TIMEZONE>>`, `<<TIMEZONE_LONG>>`, `<<MACHINE_LABELS>>`, `<<TODAY_TIME>>`, `<<TODAY_MACHINE>>`, `<<TODAY_END_TIME>>`, `<<TODAY_DURATION>>`. Minor — additive surface, new tokens, no incompatible template changes.
- **v0.1.0** (2026-05-26) — Initial kit shipped.

## References

- [PROCESS.md](../PROCESS.md) — onboarding runbook that drives this kit.
- [federation-arch.md](../federation-arch.md) — the prototype role doc the `role-doc-template.md` is modeled on.
- [ADR-0007](../adr/0007-github-as-system-storage-data-excluded.md) — system-vs-data classification rule the kit's `.gitignore` enforces.
- [ADR-0011](../adr/0011-data-root-config.md) — `Data root:` declaration in role-doc top metadata.
- [ADR-0012](../adr/0012-per-architect-repo-conventions.md) — per-Architect repo conventions the kit instantiates.
- [ADR-0013](../adr/0013-receipt-ritual.md) — receipt-ritual inbox layout under data root.
- [ADR-0014](../adr/0014-existing-architect-retrofit.md) — existing-Architect retrofit path (alternative to this kit, for already-evolved Architects).
- [ADR-0016](../adr/0016-memory-is-local.md) — memory is local; no memory-sync infrastructure in the kit.
- [adr/README.md](../adr/README.md) — index template's prototype.
- [adr/template.md](../adr/template.md) — ADR template carried unchanged.

## Open for the user

The kit's executing session did not decide the following architectural choices; each is drafted as a recommendation with reasoning and a flagged alternative. Foreground walks the user through these at fold-back.

### 1. Role-doc filename convention

**Question.** Federation Architect uses `federation-arch.md`. Recipe Box Architect uses `CLAUDE.md`. The kit needs a default filename for new Architects' role docs.

**Recommendation.** **`<architect-id>.md` at repo root** — e.g., `home-hub-arch.md`, `Cairn-arch.md`, `arcade-arch.md`. Matches the existing federation-side convention; the filename carries the Architect ID per [ADR-0006](../adr/0006-naming-convention-corrected.md); cold-readers can identify the Architect from the filename alone without opening it.

**Reasoning.** `CLAUDE.md` is a Claude Code convention that conflates "this is the role doc for whoever's reading" with "this is *that specific Architect's* role doc." The federation has multiple Architects; per-Architect identity in the filename is more honest. `federation-arch.md` already follows this pattern in the federation repo; generalizing the same pattern is the consistent move.

**Flagged alternative.** **`CLAUDE.md` at repo root.** Claude Code reads it automatically as project context, which is a real ergonomic win in the orchestrator-agent runtime case. Accept if the user values the auto-context-load over the per-Architect identifier in the filename. (A reconciling option: use `<architect-id>.md` as the canonical filename and symlink `CLAUDE.md` to it. Available as a per-Architect choice; kit default does not need it.)

### 2. Pre-populated principles in the role-doc template's §4.1

**Question.** Principles are universal by definition ([ADR-0008](../adr/0008-three-bucket-taxonomy.md)). A fresh Architect inherits all 13 (P1–P13) on day one. Two options:
- Pre-populate §4.1 with the 13 current principles in the template; onboarding Architect starts already-adopted; kit version-stamps the principle set ("as of federation-arch.md v0.7.0").
- Leave §4.1 empty; first session post-onboarding runs an initial retrofit per [ADR-0014](../adr/0014-existing-architect-retrofit.md) to register adoptions.

**Recommendation.** **Pre-populate §4.1 with the 13 current principles** in the template, version-stamped "as of federation-arch.md v0.7.0 / principles/master.md state at <<TODAY>>." Fresh Architects are *categorically* in scope for every principle ([ADR-0008](../adr/0008-three-bucket-taxonomy.md) makes them universal). Running an initial retrofit on a fresh Architect would produce 13 "outcome B — gap-to-close" rows for principles plus N rows for universal habits, all of them trivially "adopt" — a synthetic exercise that just renders what the template could have already declared.

**Reasoning.**
- **Speed.** Onboarding session 1 starts with a working role doc rather than a backlog of 13+ adoption events.
- **Mechanism honesty.** [ADR-0014](../adr/0014-existing-architect-retrofit.md) was scoped to *existing* Architects with evolved role docs. Running it on a cold-start template is the wrong-tool-for-the-job — there is no evolved practice to reconcile against. The outcome-A vs. outcome-B distinction is meaningless when there's no role doc to walk.
- **ADR-0008's universality argument.** Principles are *property claims* applicable to every Architect; binding them at template-render is consistent with what makes them principles.
- **Audit trail preserved.** The template's CHANGELOG `0.1.0` entry cites the principles set's source version. If the federation accepts a new principle between template-render and onboarding, that new principle lands via a normal ongoing-retrofit receipt-ritual edit ([ADR-0014](../adr/0014-existing-architect-retrofit.md) §"Trigger model — two modes," ongoing case).

**Flagged alternative.** **Leave §4.1 empty; run initial retrofit at session 1.** Mechanism uniformity — every Architect goes through the same path. Accept if the user strongly values "no Architect skips the retrofit ceremony," even when the ceremony is synthetic. Reject if the speed and honesty arguments above carry more weight.

### 3. Pre-populated universal habits in §4.2

**Question.** Universal habits are case-by-case per [ADR-0008](../adr/0008-three-bucket-taxonomy.md)'s failure-mode-breadth test. Every currently-Accepted habit has already passed that test. Same two options as item 2.

**Recommendation.** **Pre-populate §4.2 with all 21 currently-Accepted universal habits**, same version-stamp pattern as item 2. **Consistent with item 2.** The case-by-case rule in [ADR-0008](../adr/0008-three-bucket-taxonomy.md) gates *promotion to universal* (does the habit address a failure mode present in every Architect's context?). Once promoted, the universal scope applies. Distinguishing "promoted but not auto-adopted by new Architects" from "promoted and auto-adopted" would carve out a third bucket the taxonomy doesn't have.

**Reasoning.** Same as item 2 — speed, mechanism honesty, taxonomy alignment. If items 2 and 3 split (one pre-populated, one not), the role-doc template becomes incoherent — §4.1 says "you've adopted these 13 principles" and §4.2 says "you've adopted nothing yet; run retrofit." That asymmetry is artificial.

**Flagged alternative.** **Leave §4.2 empty.** Same argument as item 2's alternative. Accept only paired with item 2's alternative.

### 4. Kit versioning  *(Resolved: flagged alternative — kit gets semver, starting v0.1.0)*

**Resolution.** The kit is versioned. `Kit version: v0.1.0` declared in the top metadata block. Bump rules in §"Versioning." Onboarding handoff captures the kit version. The user's reasoning at resolution: *the federation git history isn't going to show up in the new Architect's logs or artifacts* — meaning the federation main SHA is invisible from the Architect's side, so the kit needs its own version stamp findable without federation-repo spelunking.

**Original recommendation (rejected):** implicit-version, no kit semver, federation `main` SHA as traceability anchor. Rejected on the grounds above.

### 5. `.claude/settings.json` portability

**Question.** The federation's `.claude/settings.json` was tuned for Federation-Architect-specific surface (git ops focused on the federation repo). For a participating Architect, the allow/deny lists may need adjustment. Two options:
- Ship the federation's `settings.json` verbatim as the kit default. Operator tunes after-the-fact if needed.
- Ship a minimal `settings.json` template that allow-lists only the truly universal patterns (read-only git, basic write git, destructive-op deny). Operator adds Architect-specific patterns.

**Recommendation.** **Ship the federation's `settings.json` verbatim as the kit default.** The federation's allow/deny lists are already substantially generic — read-only git, routine write git, destructive-op deny. The only federation-specific element is the `"//"` comment at the top citing ADR-0007. The cost of starting from a working set and trimming back is lower than starting from minimal and adding what's needed; in practice, an Architect's settings needs will be a superset of the federation's.

**Reasoning.**
- **The destructive-op deny-list is portable.** Force-push, hard-reset, branch -D, filter-branch, etc. are universally destructive regardless of which Architect runs them. Stripping any of these to make the kit "minimal" would weaken the substrate enforcement of P9 (`destructive-ops-confirmed`) for new Architects.
- **The routine-allow-list is portable.** Read-only git, `add`, `restore`, `commit`, `push origin`, `checkout`, `merge`, `stash`, `fetch`, `pull`, `mkdir`, `ls`, `cat`, etc. apply uniformly. The federation file does not contain federation-specific paths or repo names in its rules.
- **Operator tunes if needed.** A new Architect with non-git tooling (e.g., a custom CLI) adds patterns to their own `settings.json` post-onboarding. That edit lives in the Architect's own repo, not in the kit.
- **The `"//"` comment line is the one Federation-specific bit.** The template carries a generalized version of the comment (cites ADR-0012 and references P9/P10 generically); the substantive rules are unchanged.

**Flagged alternative.** **Minimal `settings.json` template.** More honest about portability — only ship what's universally true; let each Architect declare its own routine allow-list. Accept if the user wants the kit to make no assumptions about what routine ops a new Architect needs. Reject if the recommendation's "trim-from-working-set" argument wins.

### 6. Whether `portfolio.md` gets a template too

**Question.** `portfolio.md` is federation-owned. New Architects don't have their own portfolio file — they're an *entry* in the federation's portfolio. Does PROCESS.md's step "add portfolio entry" need clarification? Does the kit ship a portfolio template?

**Recommendation.** **No portfolio template in the kit. PROCESS.md explicitly clarifies that the portfolio entry is an edit to *the federation repo*, not a file in the new Architect's repo.** The PROCESS.md "add portfolio entry" step instructs the operator to add a row to [portfolio.md](../portfolio.md) in the federation repo as part of onboarding — that edit lands in the federation repo's commit log, not the new Architect's.

**Reasoning.** This is doc-clarity, not a design choice. The portfolio is a singleton registry living in the federation repo; new Architects are *registered* in it, not *bearers* of it. Shipping a portfolio template would suggest each Architect has its own portfolio, which is wrong. The kit's silence on portfolio is correct; PROCESS.md's runbook needs an explicit "this is a federation-repo edit" note to prevent confusion at the moment the operator is editing files.

**Flagged alternative.** **Ship a `portfolio-entry-template.md` snippet** with the single row's columns laid out (`| System | Orchestrator | Architect | system-id | architect-id | Status |`). Adds a small ergonomic affordance for the operator. Reject as kit-content (the entire snippet is six pipes; over-templating); accept as PROCESS.md prose if the user prefers an inline example in the runbook.
