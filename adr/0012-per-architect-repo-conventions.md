# ADR-0012: Per-Architect repository conventions

**Status:** Accepted
**Date:** 2026-05-26
**Deciders:** the user, Federation Architect

## Context

[ADR-0007](0007-github-as-system-storage-data-excluded.md) pinned the rules for **the Federation Architect's own repo**: GitHub as system storage, data excluded by policy and gitignore, blanket maintenance authority for the Federation Architect, destructive-op carve-outs requiring the user confirmation, mechanical enforcement via `.gitignore`. Those rules govern one repo — the federation's — and they have been load-bearing since session 2.

Three forces have surfaced since:

1. **Every federation-participating Architect needs the same discipline.** Each participating Architect (Recipe Box, Home Hub, Trail Log, Campaign Tracker, Arcade, Federation, and future additions per [portfolio.md](../portfolio.md)) has its own role doc, its own ADRs, its own templates — its own system surface. And each will, in time, accumulate its own data: an `architect-learnings.md` producer file, mirrored cross-system artifacts, any registries or sidecars per [ADR-0009](0009-data-storage-mechanics.md) and [ADR-0010](0010-registries-canon-only-sidecar-history.md) that live under its data root. The same system-vs-data classification line ADR-0007 drew for the federation must hold for each of them, or data leaks into the wrong repo.

2. **Federation Architect is a participant, not an exception.** [ADR-0003](0003-federation-architect-is-a-participant.md) — extended to universal habits by [ADR-0008](0008-three-bucket-taxonomy.md) — makes the federation a participant on equal footing with every other Architect. A rule that ships to all Architects via the federation applies to the Federation Architect too. ADR-0007 already governs the federation-side repo by name; what's missing is the *generalized pattern* whose application to the federation is one instance among many.

3. **The pattern is needed before any new repo is stood up.** The forthcoming bootstrap kit (session-8 handoff next-step #3) needs a conventions ADR to point at when it tells a new Architect's first session how to lay out its repo. Existing-Architect retrofit ([ADR-0014 brief](../briefs/adr-0014-existing-architect-retrofit.md)) needs the same anchor when it reconciles an existing Architect's already-evolved repo against federation conventions. Neither workstream can render its template without the conventions pinned.

This ADR generalizes ADR-0007's federation-side rules into per-Architect conventions. It does not amend ADR-0007 (the federation's repo continues to be governed by ADR-0007 directly) — it lifts the pattern one level so every other participating Architect lands on the same shape.

Two prior decisions constrain the design:

- **[ADR-0007](0007-github-as-system-storage-data-excluded.md)** is the template. The structure of this ADR — authority, classification rule, mechanical enforcement, repository configuration, destructive-op carve-outs — mirrors ADR-0007's structure on purpose. The intent is *isomorphism*: someone who has internalized ADR-0007 can read this ADR and recognize the pattern immediately.
- **[ADR-0009](0009-data-storage-mechanics.md) + [ADR-0010](0010-registries-canon-only-sidecar-history.md)** define what "data" means under the data-root abstraction: registries (`principles/master.md`, `habits/master.md`), the user profile (`users/<user-id>/profile.md`), and their append-only sidecars. The classification rule below must be consistent with that definition.

This is a *conventions* ADR, not a bootstrap ADR. It establishes the rules every participating Architect's repo operates under; it does not stand up any specific repo. Standing up a new Architect's repo is the bootstrap kit's job; bringing an existing Architect's repo into compliance is ADR-0014's job.

## Decision

Every federation-participating Architect maintains its own repository for its own system files, governed by the conventions below. The conventions mirror ADR-0007 in structure and intent; the differences are scope (per-Architect rather than federation-only) and authority delegation (each Architect over its own repo rather than Federation Architect over the federation repo).

The Federation Architect's repo is already governed by [ADR-0007](0007-github-as-system-storage-data-excluded.md) directly. This ADR does **not** supersede ADR-0007; it generalizes the pattern ADR-0007 instantiated. Federation Architect complies via ADR-0007; every other participating Architect complies via this ADR. The shapes are intentionally identical.

### Authority

**Each Architect maintains its own repository without per-commit or per-push approval.** the user does not review commits, branches, or PRs in any participating Architect's repo. Each Architect receives a single confirmation when its repo is first stood up (or first brought into compliance, per ADR-0014), and brief sync confirmations as part of normal session summaries (e.g., "synced to GitHub"). No commit-message screenshots, no diff walkthroughs, no approval gates for routine work.

This is the same blanket-maintenance authority ADR-0007 grants the Federation Architect over the federation repo, instantiated once per participating Architect.

**Carve-out for destructive or irreversible operations.** Even under blanket authority, each Architect surfaces and confirms with the user before:

- Force-pushing, history rewriting, or branch deletion that could lose work.
- Deleting the repository.
- Making the repository public (if it was private), or vice versa.
- Adding collaborators or changing access settings.
- Pushing files that might be borderline-data (see classification rule below — when in doubt, the file stays local and gets flagged).

Routine commits, pushes, branch creation for feature work, and merges are not destructive and proceed without approval. This carve-out is the federation-wide instantiation of the `confirm-destructive-ops` universal habit (parent: P9, *destructive-ops-confirmed*) and is consistent with the `routine-ops-autonomy` universal habit (parent: P10, *architect-owns-operational-substrate*).

### Classification rule

Files in a participating Architect's repo are classified as **system** or **data**. Only system files are committed.

The classification is **shared semantics, not shared file lists.** Each Architect's specific file list will differ — the system files of a Home Hub Architect will not be the system files of a Arcade Architect. But the rule for sorting any given file into system or data is the same across Architects.

**System files** define how *this Architect* operates. They are infrastructure for the Architect's own system. Examples (the list is per-Architect, not literal):

- This Architect's role doc (e.g., `home-hub-arch.md`, `recipe-box-arch.md`).
- This Architect's ADRs (`adr/`).
- This Architect's templates, naming conventions, process docs.
- Top-level orientation docs for this Architect's repo (`README.md`, `PROCESS.md` or equivalent).
- Any founding-brief equivalents specific to this Architect.
- The `Data root:` declaration in the role-doc top metadata block per [ADR-0011](0011-data-root-config.md) — the declaration is configuration on the role doc (system); the path it points at is data (not committed here).

System files may reference other Architects, systems, orchestrator agents, or users by name or ID (e.g., "Basil," "recipe-box," "the user"). Naming references are not data.

**Data files** are content drawn from or synthesized from outside this Architect's own system definition. They are operational substrate. Examples:

- This Architect's own `architect-learnings.md` producer file (recommendation: data; see §"Open for the user" item 2 — this is the trickiest case and the user resolves it).
- Mirrored cross-system artifacts: another Architect's role doc, another Architect's producer file, federation outputs this Architect ingested. Typically under `inputs/` or equivalent.
- The bound user's profile read at session start per ADR-0009 (`<data-root>/users/<user-id>/profile.md` — lives under the data root, not under the repo, but the rule is the same: data does not go to GitHub).
- Any synthesized registries this Architect maintains under its own data root.
- Any locally-stored Auditor engagement reports.
- Anything containing quoted cross-system content, even within an otherwise-system file.

**Boundary cases** (mirror ADR-0007):

- **ADRs that need to explain context drawn from another system's incident** — write the context abstractly. Reference patterns and decisions, not quoted learnings. The federation's ADR-0005 (Arcade corruption) is the prototype: enough context to be cold-readable, no quoted producer content.
- **Memory files** (e.g., `~/.claude/projects/.../memory/`) live outside any repo. They are operational continuity for the Architect's sessions, not committed anywhere.
- **When in doubt:** the file stays local, and the question is surfaced to the user before any commit involves it. Same hold-and-flag rule ADR-0007 established.

The classification rule is the per-Architect instantiation of the `gitignore-enforces-data-system-boundary` universal habit (parent: P3, *data-system-separation*).

### Mechanical enforcement

Each Architect's repo carries a `.gitignore` at the repo root that excludes data paths by pattern. The specific patterns are per-Architect (different Architects have different data path conventions), but the rule is uniform: **the presence of a path-pattern in `.gitignore` is itself a declaration that the path holds data.**

Minimum patterns every participating Architect's `.gitignore` should carry, derived from the universal data-root layout in ADR-0009:

```
inputs/
principles/
habits/
users/
architect-learnings.md
```

Additional patterns are added as the Architect introduces new data paths. **Any new data path is added to `.gitignore` in the same commit that creates the path.** This is the load-bearing discipline ADR-0007 established; it ports unchanged.

### Repository configuration

Each Architect's repo carries the following configuration. Defaults below are recommendations; per-Architect divergence is permitted with the reason recorded in that Architect's ADR (or its repo's `README.md` if pre-ADR).

- **Account.** Recommendation: same GitHub account as the federation repo (currently `<your-github-account>`). See §"Open for the user" item 3 — the user resolves whether this is mandatory or per-Architect.
- **Repo name.** Per-Architect. Suggested form: matches the Architect's working-directory name or system ID (e.g., `home-hub-architect`, `recipe-box-architect`).
- **Visibility.** Recommendation: **Private** by default. System files reference internal portfolio details, orchestrator-agent codenames, and operational context. Per-Architect override permitted if the user directs (e.g., a public-facing Architect might justify a public repo).
- **Default branch.** `main`.

### Scope of applicability

These conventions apply to **every federation-participating Architect**, defined as every Architect listed in [portfolio.md](../portfolio.md) with a Status of `Active`, `WIP`, or `Re-establishing`. New Architects join the scope at portfolio registration time.

By [ADR-0003](0003-federation-architect-is-a-participant.md) (recursive participation), this includes Federation Architect itself. Federation Architect already complies via ADR-0007, which is the prototype these conventions generalize from; this ADR does not modify ADR-0007 or the federation repo's rules. The two ADRs say the same thing about the federation repo; ADR-0007 is the direct governing document and remains so.

Architects with no orchestrator agent (currently Campaign Tracker, Arcade per [portfolio.md](../portfolio.md)) are in scope on the same terms — see §"Open for the user" item 5 for the recommendation and reasoning.

### Cross-repo references

When an Architect's role doc or ADR needs to reference another Architect's role doc or ADR, the reference is by **system ID + artifact name** (e.g., "Recipe Box Architect's role doc," "ADR-0007 in the federation repo"). The resolution mechanism — federation's `inputs/` mirror, direct cross-repo link, or both — is recommended in §"Open for the user" item 6; the user resolves.

## Resolutions at Accept

Six architectural choices the brief surfaced that the executing session was instructed not to decide unilaterally. Each was drafted with a recommendation and reasoning. **All six resolved per recommendations on 2026-05-26 (session 9).** The Decision section above is the canonical text; this section retains the reasoning for provenance.

### 1. Is having a GitHub repo mandatory under federation, or optional?

**Question.** Some Architects might be local-only (no remote). Does federation participation *require* a remote, or is local-only acceptable as long as the system-vs-data discipline holds?

**Recommendation.** **Mandatory.** A GitHub repo (or equivalent remote) is required for every federation-participating Architect.

**Reasoning.**

- **Off-machine durability.** A local-only Architect loses everything on disk failure. ADR-0007's rationale for the federation repo applies identically to every other Architect: no remote = no recoverability.
- **Auditor and onboarding cold-context.** ADR-0004's Auditor pattern and the bootstrap-kit retrofit (ADR-0014 brief) both assume a remote a fresh context can clone. A local-only Architect can't be audited cold without copying files over a side channel — fragile and slow.
- **Cross-Architect references.** Item 6 below depends on each Architect having a fetchable remote (whether via federation mirroring or direct cross-repo links).
- **Symmetry with the federation.** ADR-0007 made GitHub mandatory for the federation repo for the same reasons; the asymmetry of allowing some Architects to be remote-less is hard to justify.

**Flagged alternative.** **Optional with strong recommendation.** Permits local-only Architects (e.g., experimental or sensitive ones) as long as discipline holds. Reject as the default — the costs of local-only (above) are real and the benefit (one fewer setup step) is small. Accept if the user wants a single carve-out class for sensitive-data Architects whose remotes would themselves be a risk.

### 2. Where does `architect-learnings.md` sit for a producer Architect?

**Question.** For the Federation Architect (ADR-0007), `architect-learnings.md` is classified as *data* and gitignored. For a producer Architect (one that writes its own learnings file *and* whose learnings will be federated up), is its own `architect-learnings.md` also data (gitignored locally; user-controlled), or is it **system-local** (committed to that Architect's repo because the Architect owns its own production)?

This is the trickiest classification question in this ADR. Two competing intuitions:

- *"Producer files are content; content is data."* The federation's classification rule (ADR-0007 §"Classification rule") explicitly names producer files (`inputs/<system-id>-learnings.md`) as data. By the same rule, an Architect's own `architect-learnings.md` is content — entries it has written about its work — and therefore data.
- *"Each Architect owns its own production."* Unlike the federation's `inputs/`, which is *mirrored* content from other Architects, an Architect's own `architect-learnings.md` is *its own writing about itself*. It is not drawn from outside — it is produced by the Architect inside its own system. Under "system files define how this Architect operates," its own self-observation arguably belongs to its system.

**Recommendation.** **Data. Gitignored. user-controlled.** Match ADR-0007's classification.

**Reasoning.**

- **Federation-side symmetry is load-bearing.** The federation reads other Architects' `architect-learnings.md` as data (it mirrors them into `inputs/`). The Federation Architect's own `architect-learnings.md` is data on its own side. Producer files are data *as a class*, not data *only when mirrored*. Reclassifying as system for producer Architects creates a producer-vs-consumer asymmetry where the same file type is one bucket on the producing side and another on the consuming side. Hard to defend cold.
- **Volume and churn profile.** Learnings files churn faster than role docs. Treating them as system means every learning entry is a commit on the system repo, intermixing high-signal structural changes with high-volume captures. Treating them as data keeps the system repo's commit log focused on system evolution.
- **Sensitivity.** Learnings entries can include candid working observations the user may not want pushed to a remote without review. Gitignored-by-default + explicit-flag-to-share matches the cautious posture.
- **Producer-side capture is non-delegable, but storage is separable.** Universal habit `producer-side-learnings` (parent: P6, *observations-captured-at-source*) says the Architect captures its own observations; it doesn't say where the captured file lives. Local-only storage satisfies the capture requirement.
- **Backup is a separate concern.** Data files need backup (machine failure loses them); that's true regardless of whether they're in the system repo. The data-root abstraction (ADR-0009) already contemplates substrates that include backup (e.g., a separate private data repo, a synced volume). The "data files need durability" argument is real but does not justify putting them in the system repo specifically.

**Flagged alternative.** **System-local.** Commit `architect-learnings.md` to that Architect's own repo (but not to the federation's `inputs/` mirror — that mirror remains data). Arguments above for system-local are real; this is genuinely close. Accept if the user wants every Architect's full system context (including its self-observation) reconstructable from one clone, and is comfortable with the volume/sensitivity tradeoffs. The federation's `inputs/<system-id>-learnings.md` mirror remains data either way — that classification is fixed by ADR-0007.

### 3. Repository ownership / visibility defaults

**Question.** ADR-0007 settled on `<your-github-account>` account, private, for the federation repo. Do Architect repos default to the same account, the same visibility? Or does each Architect choose at bootstrap?

**Recommendation.** **Same account (`<your-github-account>`) and same visibility (private) by default; per-Architect override permitted with the reason recorded.**

**Reasoning.**

- **Account.** Concentrating all of the user's Architects under one GitHub account means one billing surface, one access-control surface, one auditing surface. Splitting across accounts adds operational overhead without clear benefit. Override case: if an Architect's domain requires legal or organizational separation (e.g., a fiduciary-system Architect needs to be on a corporate account), that's a deliberate the user decision and the reason gets recorded.
- **Visibility.** Same argument ADR-0007 made: even when no data is stored, the system files reference internal portfolio details, orchestrator-agent codenames, operational context, and Architect-internal reasoning that's not for public consumption. The cost of staying private is zero; the cost of accidental disclosure is non-trivial.
- **Override mechanism.** Per-Architect divergence from the default is permitted but must be recorded — in that Architect's ADRs, or in its repo's `README.md` pre-ADR. The default holds where no record exists.

**Flagged alternative.** **Each Architect chooses at bootstrap.** Maximally flexible. Reject as the default — the bootstrap kit can render a sensible default and let the user override at first run, rather than asking for a choice every time. Accept if the user has portfolio-wide reasons for split-account ownership (e.g., per-system compliance boundaries).

### 4. Is this ADR a *convention* or a *universal habit*?

**Question.** ADR-0007 is system-specific (Federation). ADR-0012 generalizes — but the propagation mechanism ADR-0008 prescribes for universal-applicability rules is *universal-habit registration*, not ADR-level. Should ADR-0012's rules also be registered as universal habits (e.g., `repo-system-data-separation`, `architect-maintains-own-repo`)? Or does this ADR stand alone?

**Recommendation.** **ADR-level, with downstream registration of universal habits for the specific practices that already exist in the registry.** The ADR is the structural decision; specific load-bearing practices it crystallizes are habit-grade and already (mostly) registered.

**Reasoning.**

- **ADR-0007's pattern.** ADR-0007 is itself the structural-decision precedent; its specific load-bearing practices were registered as universal habits in the `0.4.0` federation-arch.md bump:
  - `gitignore-enforces-data-system-boundary` (parent: P3) — already accepted, federation-side instantiation already encoded.
  - `confirm-destructive-ops` (parent: P9) — already accepted.
  - `routine-ops-autonomy` (parent: P10) — already accepted.

  These habits are *already* universal. ADR-0012 doesn't introduce new universal habits — it lifts the *application surface* from "the federation's repo" to "every participating Architect's repo." The habits ride this ADR's adoption.

- **What's an ADR vs. what's a habit (per ADR-0008).** ADR-0008 says principles are property claims, habits are practices. ADR-0012 is a *structural* decision: how participating Architects' repos are organized, who has authority over them, what gets committed. That's an ADR-shaped decision (one-time, structural, requires Alternatives + Consequences). Habits are the operational practices that enact the structure: gitignore discipline, destructive-op confirmation, routine-ops autonomy. ADR + already-registered habits is the right division of labor.

- **One potential new habit candidate.** The "Architect maintains its own repo with blanket authority over its own system files" pattern could plausibly be promoted to a universal habit slug (e.g., `architect-maintains-own-system-repo`, parent: P10 *architect-owns-operational-substrate*). It's not currently in `habits/master.md`. Recommendation: **defer.** This ADR is the registration event for the structural rule; the habit-level promotion can follow if a parallel ADR-0008-style federation event makes it useful. Pre-registering it as a habit alongside this ADR is harmless but not load-bearing.

**Flagged alternative.** **Register ADR-0012's rules as new universal habits in addition to the ADR.** Possible new slugs: `repo-system-data-separation` (parent: P3), `architect-maintains-own-system-repo` (parent: P10). Reject as the default — the existing habit set already covers the operational practices; new slugs would duplicate or fragment. Accept if the user wants every load-bearing rule to have a habit slug in addition to an ADR for cross-Architect propagation tracking.

### 5. Carve-outs for "no orchestrator agent" Architects

**Question.** Per [portfolio.md](../portfolio.md), Campaign Tracker and Arcade have no orchestrator agent. Do the same repo conventions apply, or does the lack of a runtime change anything?

**Recommendation.** **Same conventions apply; no carve-out.**

**Reasoning.**

- **The conventions are about the Architect, not the runtime.** Everything in the Decision section (authority over the repo, classification rule, mechanical enforcement, repo configuration) operates at the Architect level. The orchestrator agent (Basil, Hearth, Cairn) is a separate entity — a user-facing runtime — and is not the thing committing to the repo or reading the data root. The Architect is.
- **A repo's value is independent of having a runtime.** A Campaign Tracker or Arcade Architect with no orchestrator agent still has a role doc, ADRs, system files, and (eventually) producer-side learnings. All of those need durability, audit trail, and a clean system-vs-data line. The same arguments that justified ADR-0007 for the federation (which itself has no orchestrator agent — see [portfolio.md](../portfolio.md)) justify this ADR for Campaign Tracker and Arcade.
- **Federation Architect is itself a no-orchestrator-agent case** and is governed by ADR-0007 with no special treatment. Campaign Tracker and Arcade inherit the same shape.

**Flagged alternative.** **Lighter conventions for no-orchestrator-agent Architects** (e.g., visibility default to local-only, no mandatory remote). Reject — collapses to item 1's "local-only" alternative, which was rejected. The presence or absence of a runtime doesn't change the durability or audit requirements.

### 6. Cross-repo references

**Question.** When Architect A's role doc needs to reference Architect B's role doc (e.g., for ADRs cross-citing), do those references resolve via federation only (federation's `inputs/` mirror) or directly cross-repo?

**Recommendation.** **Reference by system ID + artifact name in prose; resolution is via federation's `inputs/` mirror as the primary path, with direct cross-repo links permitted as a secondary path when appropriate.**

**Reasoning.**

- **Prose stability.** Linking by system ID + artifact name (e.g., "Recipe Box Architect's role doc," "ADR-0007 in the federation repo") makes the reference cold-readable and survives repo renames, moves, or visibility changes. A hardcoded `https://github.com/<your-github-account>/recipe-box-architect/blob/main/...` link is brittle.
- **Federation mirror is the canonical read path.** The federation already mirrors other Architects' role docs and learnings under `inputs/<architect-id>-arch.md` and `inputs/<architect-id>-learnings.md` (federation-arch.md §8 Inputs table). When Architect A needs to cite Architect B, the federation's mirror is the natural resolution point — read-only, versioned via federation pulls, available cold to Auditors who clone the federation repo.
- **Direct cross-repo as secondary path.** When the federation mirror is stale or the reference needs to point at a specific commit hash, a direct cross-repo link is permitted. The recommendation is to render both: prose name first (stable), direct link in parens if useful (current).
- **Access asymmetry is fine.** Architect A's repo doesn't need read access to Architect B's repo. Federation does (per ADR-0007 and its mirror discipline); individual Architects don't. This keeps each Architect's repo's access surface minimal.

**Flagged alternative.** **Direct cross-repo only** (no federation mirror dependency). Each Architect that needs to cite another gets read access to the other's repo. Reject as the default — proliferates access grants, makes cold-context onboarding harder (an Auditor cloning Architect A's repo can't resolve references without also being granted Architect B's repo), and tightly couples the Architects against the federation's mirror-and-broker pattern. Accept if the user wants Architects to have direct visibility into each other's repos by default.

## Alternatives Considered

- **Amend ADR-0007 to apply to all Architects rather than draft a new ADR.** Rejected — ADR-0007 is system-specific (it names `<your-github-account>/principles-of-good-architects`, the federation's classification examples, the federation's `.gitignore`). Amending it to be generic would dilute the federation-specific instance and break the immutability convention ADR-0001 establishes for Accepted ADRs. The right move is a new ADR (this one) that generalizes the pattern; ADR-0007 remains the federation-specific instance of the now-generalized convention.

- **One single repo for all Architects (monorepo).** Rejected — collapses the per-Architect authority boundary. ADR-0007's blanket-maintenance delegation is *per-repo*; a monorepo would need a per-directory authority model that doesn't naturally exist in git. Also collapses the per-Architect data-root abstraction (each Architect has its own data root per ADR-0009), and would force cross-Architect referencing to be intra-repo path-references rather than the more portable system-ID-and-artifact-name pattern. Per-Architect repos preserve the boundaries that make the federation model coherent.

- **No repository at all; everything is local or in some other substrate.** Rejected as the default — see §"Open for the user" item 1.

- **Public repositories by default.** Rejected for the same reasons ADR-0007 rejected public for the federation repo: system files reference internal portfolio details and operational context. Cost of staying private is low; cost of accidental disclosure is non-trivial.

- **Per-commit approval gates** (each commit reviewed by the user). Rejected — duplicates the per-Architect overhead the user explicitly didn't want for the federation repo, multiplied by Architect count. Blanket authority with destructive-op carve-outs is the load-bearing pattern; replicate it.

- **Skip mechanical enforcement (`.gitignore`); rely on Architect discipline.** Rejected — ADR-0007's argument applies identically: `.gitignore` is load-bearing precisely because Architect discipline can lapse, and the cost of an accidental data leak is unrecoverable in a public-readable system. Mechanical enforcement is cheap and asymmetric in value.

- **Different conventions per Architect** (e.g., bespoke per-system rules). Rejected — the *point* of this ADR is uniformity. Per-Architect divergence is permitted on specific knobs (account, visibility, repo name) with the reason recorded; the *rules* (authority, classification, mechanical enforcement) are uniform. Diverging on rules would fragment the federation's discipline.

- **Bake `architect-learnings.md` as system-local for producer Architects.** See §"Open for the user" item 2 — flagged for the user, not decided.

- **Register the conventions as universal habits instead of (or in addition to) an ADR.** See §"Open for the user" item 4 — flagged for the user, not decided.

## Consequences

### Immediate

- **The bootstrap kit unblocks on this ADR.** The role-doc template, `.gitignore` template, and `README.md` template the bootstrap kit produces can render the per-Architect conventions per the form pinned above. A new Architect's first session stands up its repo to spec from day one.

- **ADR-0014 (existing-Architect retrofit) gains its conventions anchor.** The retrofit workflow can reconcile an existing Architect's already-evolved repo against this ADR's rules: classification audit, `.gitignore` audit, repo-visibility check, account check.

- **No participating Architect's repo exists yet to retrofit (except Federation Architect's, governed by ADR-0007).** Home Hub, Trail Log, Recipe Box, Campaign Tracker, and Arcade are listed in [portfolio.md](../portfolio.md) but none has a federation-spec'd repo today. First retrofit candidate will be the first Architect ADR-0014 targets.

- **Federation Architect's repo unchanged.** Continues to be governed by ADR-0007 directly. This ADR does not modify ADR-0007. The two ADRs say the same things; ADR-0007 is the canonical governing document for the federation repo and remains so.

### On future Architect onboarding

- **Bootstrap consistency.** Every Architect bootstrapped via the kit lands on the same repo shape: private repo on `<your-github-account>`, `main` default branch, `.gitignore` with the universal data-path patterns, role doc with `Data root:` declaration (per ADR-0011), classification line drawn before any commit.

- **Retrofit consistency.** Every existing-Architect retrofit (per ADR-0014's brief) reconciles against the same shape, with the divergences logged in that Architect's repo's ADR sequence.

### On the federation model

- **Per-Architect authority boundaries are now first-class.** Each Architect has blanket maintenance authority over its own repo. The federation does not commit into other Architects' repos directly; federation-driven changes are proposals, accepted by each owning Architect, landed by that Architect into its own repo (the role-doc-edit flow already established in [federation-arch.md §9](../federation-arch.md#9-approval-gates)).

- **Cross-Architect references stabilize.** The system-ID-and-artifact-name pattern (per §"Open for the user" item 6) becomes the canonical citation form. Federation's `inputs/` mirror is the resolution point.

### Risks

- **Per-Architect `.gitignore` drift.** Each Architect's `.gitignore` is maintained by that Architect. If one Architect's gitignore drifts out of sync with the data-path conventions (e.g., misses a new data path), data leaks into that Architect's repo. Mitigation: the minimum-pattern list in §"Mechanical enforcement" gives every Architect a known-good starting set; the bootstrap kit renders it directly; ADR-0014's retrofit audit catches existing-Architect drift. Ongoing drift remains a per-Architect responsibility, same as ADR-0007 leaves it for the federation.

- **Classification-call inconsistency across Architects.** Without a shared "stays local and gets flagged" referee, two Architects might classify the same kind of file differently (especially for boundary cases like §"Open for the user" item 2 — own learnings). Mitigation: this ADR pins the shared semantics; the user is the consistency point when an Architect surfaces a borderline file. The federation can additionally surface inconsistencies as part of normal aggregation passes once ingestion is live.

- **Account-level access concentration.** Defaulting all repos to `<your-github-account>` (per item 3) concentrates risk at one GitHub account. Mitigation: ADR-0007's existing risk for the federation repo, generalized — same magnitude per repo, same mitigations (private visibility, no production data committed). The override mechanism in item 3 covers cases where concentration is unacceptable.

- **Cross-repo-reference brittleness.** Direct cross-repo links (per item 6) can break on rename, move, or visibility change. Mitigation: the recommended pattern is system-ID-and-artifact-name in prose (stable) with direct link as secondary; the prose form survives all of those changes.

### Deferred

- **Standing up any specific Architect's repo.** Bootstrap kit (separate workstream, lands after this ADR + ADR-0013 + ADR-0014). Per the brief: this is conventions, not bootstrap.

- **Existing-Architect retrofit.** ADR-0014 brief, separate workstream.

- **Multi-account, multi-org, or compliance-driven account models.** Out of scope; reopen if a participating Architect surfaces a hard requirement.

- **Per-Architect access control beyond visibility** (collaborator policy, branch protection, required reviews, etc.). Defer until a real need surfaces; current default is "only the user has access" via the single-owner `<your-github-account>` account.

- **Federation-side automation that audits per-Architect `.gitignore` and visibility settings.** Not built. Could be added once federation aggregation is live; not load-bearing now.

## References

- [ADR-0003: The Federation Architect is a federation participant](0003-federation-architect-is-a-participant.md) — recursive participation; the reason these conventions apply to Federation Architect as well as every other participating Architect.
- [ADR-0007: GitHub as system storage; data excluded by policy and gitignore](0007-github-as-system-storage-data-excluded.md) — the prototype this ADR generalizes from. Federation Architect's repo continues to be governed by ADR-0007 directly; this ADR lifts the pattern to every other participating Architect.
- [ADR-0008: Three-bucket taxonomy](0008-three-bucket-taxonomy.md) — classifies what counts as principle, habit, preference; relevant to §"Open for the user" item 4 (ADR-vs-universal-habit choice for the conventions in this document).
- [ADR-0009: Data storage mechanics — data root, habit registry, user profile](0009-data-storage-mechanics.md) — defines what counts as data under the data-root abstraction. The classification rule above is consistent with this ADR's data inventory.
- [ADR-0010: Registries are canon-only; sidecar history uniform](0010-registries-canon-only-sidecar-history.md) — role-doc-as-canon locus; relevant to where each Architect's adoption record lives (its own role doc).
- [ADR-0011: Data-root configuration form — declared in role-doc top metadata](0011-data-root-config.md) — the `Data root:` declaration that each participating Architect's role doc carries; the path it points at is data (gitignored per this ADR's classification rule); the declaration itself is system.
- [federation-arch.md](../federation-arch.md) — Federation Architect's role doc; the §4 adoption record for the universal habits (`gitignore-enforces-data-system-boundary`, `confirm-destructive-ops`, `routine-ops-autonomy`) that operationalize this ADR's rules on the federation side.
- [portfolio.md](../portfolio.md) — the registered Architects this ADR governs. Federation participation = listed in this registry with status `Active`, `WIP`, or `Re-establishing`.
- [briefs/adr-0012-per-architect-repo-conventions.md](../briefs/adr-0012-per-architect-repo-conventions.md) — the brief this ADR was produced from.
- [briefs/adr-0014-existing-architect-retrofit.md](../briefs/adr-0014-existing-architect-retrofit.md) — the retrofit workstream that reconciles existing Architects' repos against these conventions.
