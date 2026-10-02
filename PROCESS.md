# Federation participation — onboarding and ongoing flow

This document is the runbook for bringing an Architect into the federation and keeping it participating thereafter. Audience: the operator, future operators, and the Federation Architect itself ([ADR-0003](adr/0003-federation-architect-is-a-participant.md) — Federation Architect is also a participant).

**Bootstrapping new Architects is one of the federation's three co-equal mission responsibilities** ([federation-arch.md §2](federation-arch.md), [ADR-0017](adr/0017-bootstrapping-is-a-federation-responsibility.md)) — standing up a competent Architect to run a project is a delivered outcome in its own right, not merely a precondition for feeding the curation loop. The Federation Architect is the *actor* that executes this runbook; the kit is the tooling it owns.

PROCESS.md drives the [bootstrap kit](bootstrap-kit/). The kit is the templated scaffolding; this doc is the runbook that uses it.

**Core first, advanced later.** One project on one machine needs only the core: `poga init`, local-only, no remote. Sections marked **Advanced** cover the optional capabilities: remote provisioning, parallel dispatch, the deploy runner, scheduled jobs and fleet-wide distribution. Their docs live in [`deploy/`](deploy/README.md), and the [README](README.md#optional-capabilities) says what turns each one on. A fictional example of the advanced case is in [`examples/multi-machine/`](examples/multi-machine/README.md). The fleet's finish-line board (`python3 curate/finish_line.py`) is the advanced fleet's measure, not a bar for a single project.

## Onboarding paths

**Three** paths, picked at the start of onboarding. The choice turns on **two independent
questions**, not one ([ADR-0067](adr/0067-adopt-in-place-is-the-third-onboarding-path.md)):

| | Repo has real content? | Already has an Architect? | Path |
|---|---|---|---|
| **Fresh** | no | no | `poga bootstrap --spec <spec>` |
| **Adopt-in-place** | **yes** | **no** | `poga bootstrap --spec <spec> --adopt <path>` |
| **Retrofit** | yes | yes | [ADR-0014](adr/0014-existing-architect-retrofit.md) |

- **Fresh Architect** — repo doesn't exist yet, or exists empty. Uses the [bootstrap kit](bootstrap-kit/) to install federation-spec'd scaffolding from cold. **This doc's runbook covers this path.**
- **Adopt-in-place** — the system was **built and works**, but nobody has ever been its Architect (for example, a running service with its own ADRs and no role doc). Same harness, `--adopt <path>`: it skips repo-create/clone, installs the **substrate only**, never overwrites a file the system already has, and **merges** `.gitignore` rather than replacing it (the target's rules are usually load-bearing — they can be the only thing keeping irreplaceable data out of git). A target with no git history yet is `git init`'d and its content committed as the root commit automatically — no manual `git init` step. Creating a git remote is opt-in via `--create-remote`. Both `<path>` and `--spec` are optional: run `poga bootstrap --adopt` with neither from inside the project itself and it adopts the current directory, reading its judgment from a `bootstrap-spec.json` sitting in that project (missing either notifies with a named, actionable message rather than guessing). Read [ADR-0067](adr/0067-adopt-in-place-is-the-third-onboarding-path.md) for the reasoning and the refusal cases.
- **Existing Architect (retrofit)** — repo exists with an evolved role doc and ongoing practice (the older members that had grown an Architect before the federation existed). Uses the retrofit mechanism in [ADR-0014](adr/0014-existing-architect-retrofit.md). The retrofit reconciles existing practice against federation conventions; it does *not* use the bootstrap kit. PROCESS.md does not duplicate the retrofit spec — read ADR-0014 directly.

The old single tie-breaker — *"any committed role-doc content ⇒ retrofit, else fresh"* — is **retired**: it routed a built-but-unarchitected system to `fresh`, the one path that structurally cannot run on a repo that already exists. Ask both questions instead.

## The one-command path — `poga` in the project folder

Everything below this section assumes a **spec already exists**. For a long time nothing produced one: `bootstrap.py` consumed a spec, and the Federation Architect authored it by hand from an intake conversation held in a *federation* session. The judgment half of bootstrap had never moved to the point of use, which meant standing up a new system started with "open the federation repo" rather than with the project.

It doesn't any more. **From inside the new project's folder:**

```
cd ~/Projects/whatever-this-is
poga init
```

**Local-only is the default, and it is a complete setup.** `poga init` joins the project to a small local federation at `~/.config/poga/federation`, made on first use (`$POGA_FEDERATION_HOME` or `--federation DIR` moves it). Every project on the machine finds it without being told. `poga share`, which also runs at the end of every `init`, sends lessons scoped `architect-general`, `domain-general` or `auditor-general` to the other projects' inboxes. The profile is optional and has defaults. `--yes` takes every default. Nothing leaves the machine.

**A remote is opt-in.** Pass `--create-remote` to create a private GitHub repo and push to it. Without that flag, no step of first use creates a remote, and you need no GitHub account. You can add a remote later.

`poga` in a folder that is **not a git repository** runs the same intake (`poga new` is the same thing, named, for when you want to be explicit). It runs five steps, in order:

1. **Says the spec is missing**, and says what a spec *is* — the system's identity, from which every generated file is rendered, which is why it can't be defaulted.
2. **Explains what is about to happen.** Everything stays on this machine unless you ask for a remote. The intake asks, and "local only" is a full answer.
3. **Asks which runtime** should run the intake, unless `-r` already answered it. The roster is the [ADR-0082](adr/0082-lifecycle-inversion-and-per-session-runtime.md) one — `c` claude-code, `cx` codex, `g` gemini.
4. **Gates on y/N.** Nothing has been written at this point; declining leaves the folder untouched.
5. **Launches that runtime in the folder** with the intake prompt, and the agent does the rest: interview → write `bootstrap-spec.json` → run `poga bootstrap --adopt` itself, adding `--create-remote` only if you asked for a remote. You answer questions and get a finished project without touching the keyboard again.

**The intake prompt is [`bootstrap-kit/intake-prompt.md`](bootstrap-kit/intake-prompt.md), and it is deliberately self-contained** — it carries the spec schema, the interview, and the ask-in-prose rules in its own body. The target folder is empty, so there is no role doc, no `CANON.md` and no `CLAUDE.md` for the agent to read; and the runtime may be Codex or Gemini, which inherit no federation canon at all. Pointing at a source the agent cannot resolve is not delivery ([`single-source-and-deliver`](habits/master.md#single-source-and-deliver) — inject where there is a runtime to inject at). `poga` renders the target path, the federation doc-link base, and the bound user into it before launch, and **refuses to launch if any placeholder is unresolved** rather than letting a literal `{{TARGET_DIR}}` reach the agent to be invented or interviewed about.

**Why an agent session rather than a `read`-loop questionnaire.** The spec's load-bearing fields — `mission_prose`, `scope_do`/`scope_dont`, `voice`, `system_principles` — are judgment, not data entry ([P15](principles/master.md#p15--code-for-mechanism-not-judgment)). A prompt loop would collect the five *required* keys and produce a thin Architect, which is the outcome this path exists to avoid. Standing up a competent Architect is the Federation Architect's own job ([ADR-0017](adr/0017-bootstrapping-is-a-federation-responsibility.md)), so the intake belongs in a session.

**Nothing is installed before the agent runs, and that is load-bearing.** Only three steps are spec-free (`git init`, `.gitignore`, the byte-identical shared substrate) and they take under a second — but `ADOPTED_MARKERS` is exactly `session.py` + `CANON.md` + `STANDARD.md`, so front-loading them would convert an abandoned intake from *nothing changed* into a folder the re-run refuses as a retrofit. Today the run either completes or changes nothing. If the agent has already written a `bootstrap-spec.json` when a session dies, re-running `poga` there detects it and offers the install instead of re-interviewing.

**Federation-only.** Like `bootstrap` and `restore`, the intake resolves against the checkout `poga` physically lives in, because the install half needs `poga_cli.py`, which is not shipped to members. A `poga` symlinked from a member checkout says so **before** the interview rather than after it. Covered by [`tests/test_poga_intake.py`](tests/test_poga_intake.py).

## Fresh-Architect onboarding steps

The runbook. Follow in order. Each step lands a discrete piece of state.

**How to run this — code-driven ([ADR-0025](adr/0025-bootstrap-is-a-code-harness.md)).** The mechanical half of this runbook is executed by [`bootstrap.py`](bootstrap.py) at the federation repo root, *not* by hand. Open this federation repo in Claude Code and ask it to onboard a new Architect for your project: the Federation Architect (whose mission includes bootstrapping per [ADR-0017](adr/0017-bootstrapping-is-a-federation-responsibility.md)) runs the **intake conversation** (step 5 — the judgment), writes the gathered values into a **bootstrap spec** ([`bootstrap-spec.example.json`](bootstrap-spec.example.json) is the template), then runs `python3 bootstrap.py --spec <spec>` and reviews the result. The script does steps 3–8 deterministically — the new repo (a GitHub remote only with `--create-remote`), clone into `Projects/<FriendlyName>`, kit copy + token substitution + guidance-block stripping, the receipt-ritual inbox, the profile seed, the new repo's initial commit (and push, when there is a remote), and **staging** (not committing) the portfolio registration. The step-by-step below documents *what the script does* and is the manual fallback; prefer the script, and run it with `--dry-run` first to preview. The former hand-driven "do it by hand" path is retired per [ADR-0025](adr/0025-bootstrap-is-a-code-harness.md) — hand-doing the mechanics produced repeated errors (session 38: wrong clone path, absolute inbox path, a path valid on one machine and not another).

### 1. Assign IDs

Per [ADR-0006](adr/0006-naming-convention-corrected.md) (naming convention — 4-slot):

- **Function name** (descriptive, e.g., "Example Service," "Batch Runner")
- **Canonical Architect name** (e.g., "Example Service Architect," "Batch Runner Architect")
- **System ID** (kebab, e.g., `example-service`, `batch-runner`)
- **Architect ID** (kebab, e.g., `example-service-arch`, `batch-runner-arch`)
- **Orchestrator agent** (if any — a named runtime persona that fronts the system). Not every system has one.

Add the row to [portfolio.md](portfolio.md) in the **federation repo** (this repo, not the new Architect's repo). The portfolio is a federation-owned singleton — new Architects are registered in it, not bearers of it.

### 2. Choose the data root

Per [ADR-0011](adr/0011-data-root-config.md), every Architect declares a `Data root:` value in its role doc. Absolute path, per-machine.

The data root is where the new Architect's *data* lives (producer file, registries, receipt-ritual inbox). The system repo lives separately under the operator's normal git working area. Data root and system repo can be siblings, parent/child, or wholly separate locations — that's the operator's call.

Multi-machine note: if the Architect will run on more than one machine, each machine declares its own data-root path. Cross-machine continuity rides authority-bearing artifacts (role doc, ADRs, session-handoff), not memory ([ADR-0016](adr/0016-memory-is-local.md)).

### 3. Create the Architect's repo (the remote is optional)

Per [ADR-0012](adr/0012-per-architect-repo-conventions.md), each Architect has its own git repo for system files. A GitHub copy of it is optional. Standard layout:

```
<architect-repo>/
├── <architect-id>.md         # role doc (per Open-for-the operator #1 resolved)
├── adr/                      # this Architect's ADRs
│   ├── README.md             # index
│   └── template.md
├── architect-learnings.md    # producer file (gitignored)
├── session-handoff.md        # per-session state transfer
├── .gitignore
├── .claude/settings.json     # harness allow/deny lists
└── README.md
```

Create the empty repo. Local-only is the default: a plain `git init` is a complete answer. The GitHub remote is opt-in, with `--create-remote`; when you ask for one, the repo is created and cloned locally. Either way, the local path becomes the system-repo root.

### 4. Copy kit files into the new repo

The bootstrap kit lives at [`bootstrap-kit/`](bootstrap-kit/) in this (federation) repo. `bootstrap.py` copies these files into the new repo (the current kit version is declared in the [kit README](bootstrap-kit/README.md), which the script reads — not restated here) with the renames below — a human running the fallback copies them by hand:

| Kit file | Destination in new repo | Rename |
|---|---|---|
| `claude-md-template.md` | repo root | `CLAUDE.md` |
| `role-doc-template.md` | repo root | `<architect-id>.md` |
| `status-template.md` | repo root | `STATUS.md` |
| `roadmap-template.md` | repo root | `ROADMAP.md` |
| `gitignore-template` | repo root | `.gitignore` |
| `adr-readme-template.md` | `adr/` | `README.md` |
| `adr-template.md` | `adr/` | `template.md` |
| `session-handoff-template.md` | repo root | `session-handoff.md` |
| `architect-learnings-template.md` | repo root | `architect-learnings.md` |
| `session.config.json` | repo root | `session.config.json` |
| `CANON.md` | repo root | `CANON.md` |
| `STANDARD.md` | repo root | `STANDARD.md` |
| `claude-settings-template.json` | `.claude/` | `settings.json` |
| `readme-template.md` | repo root | `README.md` |

Skip `bootstrap-kit/README.md` itself — that's the kit's own doc, not a deliverable into the new repo.

**`CANON.md` and `STANDARD.md` are *generated* copies — copy them verbatim; never author or regenerate them in the new repo.** Both are produced in the federation (`curate/distill.py` → `CANON.md`, `curate/standardize.py` → `STANDARD.md`) and injected into the Architect's context each session by `session.py start`. The Architect **receives** them; it does not write the canon or the standard section. The generators (`curate/distill.py`, `curate/standardize.py`) and their sources (the registries, `standard-source.md`) are **federation-only** — they are **not** copied to a fresh Architect's repo. A fresh Architect's session-start injection of `STANDARD.md` follows exactly the `CANON.md` pattern ([ADR-0024](adr/0024-standard-role-doc-section-is-generated-and-injected.md)).

**Install the session-ritual harness (the ADR-0022 code channel).** `session.py` is **not** a templated kit file — it is the single shared harness, identical across the federation and every Architect (only `session.config.json` differs per system, per [ADR-0020](adr/0020-session-rituals-are-a-code-harness.md) / [ADR-0022](adr/0022-architect-ingest-distillation-and-code-channel.md)). Copy it verbatim from the federation repo root so it stays current rather than forking a snapshot — the same by-reference pattern step 6 uses for the user profile:

```sh
cp <<FEDERATION_REPO_REF>>/session.py session.py
```

`session.py` (repo root), the filled-in `session.config.json` (repo root, step 5), the `CANON.md` digest (repo root), and the `STANDARD.md` standard section (repo root) are wired by the hooks in `.claude/settings.json`: a `startup`-scoped `SessionStart` hook runs `session.py start` (mechanical session-start ritual, sets the session title, injects `CANON.md` + `STANDARD.md` as context) and a `PreToolUse(Bash)` hook runs `session.py check-bash` (the `no-compound-bash` structural guard). The kit's `claude-settings-template.json` ships both hooks pre-wired — no extra step.

Record the kit version used (`v0.17.0` at time of writing — `bootstrap.py` reads the current version from the [kit README](bootstrap-kit/README.md) at runtime) — the first session-handoff entry in the new repo cites it.

**Why `CLAUDE.md` matters:** Claude Code auto-loads `CLAUDE.md` (and not `<architect-id>.md`) as project context at session start. Without `CLAUDE.md`, the role doc never reaches the Architect's first turn and the §11 session-start protocol silently no-ops — the Architect responds as a generic Claude Code instance with no identity, no git refresh, no stamp. This was discovered when a member Architect's first kit-driven session failed in exactly this mode (federation session 13, 2026-05-27). Kit v0.6.0 added `claude-md-template.md` to close the gap.

### 5. Fill in the template tokens

Templates use `<<TOKEN_NAME>>` markers. **The Architect gathers these values into the bootstrap spec; `bootstrap.py` then applies them across all copied files and strips the template guidance blocks.** This intake — gathering the values — is the judgment half the Architect owns; the substitution is the script's. Before asking the operator anything, sort every value into one of three buckets — and only the third bucket is a question:

1. **Detect — never ask.** Values the machine already knows. Timezone (`date +%Z` for the short label; the system zone for the IANA name), machine label (`hostname`), today's date and time (`date`), the GitHub account (`gh auth status`). Read them; do not ask the operator to type what the system can report. (Composes with the `no-fabricated-data` habit — detected, not estimated.)
2. **Derive — never ask, at most confirm.** Values fixed by convention once the system's function is known. Per [ADR-0006](adr/0006-naming-convention-corrected.md): given the function/system name, the **System ID** is its kebab form, the **Architect ID** is `<system-id>-arch`, and the **canonical Architect name** is `<Function> Architect`. Repo layout, the principle/habit set, and the file structure are likewise convention-fixed (ADR-0012, ADR-0008). The agent derives these and states them; it does **not** open them as questions. The Architect's own name is **not** the operator's to pick.
3. **Ask — genuine choices only.** What the system is *for* (mission/scope prose — the `<<MISSION_PROSE>>` / scope-bullet tokens), the function/system name if not already obvious from the project, the data-root path (where data should live), and how to address the user. (Repo *owner* is **detected** via `gh auth status`; repo *name* **derives** from the system ID — recommend `<<SYSTEM_ID>>` and confirm only if non-obvious. Neither is an open question.)

**Git is the Architect's job, not the operator's** — [P10](principles/master.md#p10--architect-owns-operational-substrate) / [`routine-ops-autonomy`](habits/master.md#routine-ops-autonomy). **Never ask how to set up git** — remotes, branching, push workflow, commit conventions, repo creation mechanics. The Architect owns all of it and just does it. The **one** git-adjacent thing that may surface to the operator is a **data / PII-boundary decision**: repo visibility (private by default), what is gitignored as *data* vs committed as *system* ([P3](principles/master.md#p3--data-system-separation), [ADR-0007](adr/0007-github-as-system-storage-data-excluded.md)), and whether a sanitized public mirror is wanted. That is a safety call with real exposure blast radius — surface it as a brief safety recommendation to confirm, not as a git-configuration question.

**The three-bucket sort is your internal reasoning, not user-facing narration.** Do not label values as "(detected, not asking)" / "(derived per convention)" or otherwise announce that you're following this discipline — narrating your own compliance is just a new kind of noise. Detected and derived values are stated plainly as settled facts ("Timezone: UTC. Architect: Example Service Architect / `example-service-arch`."); only the genuine questions surface, as prose. The operator should see clean conclusions and real questions, never a play-by-play of how you sorted them.

**Sequence the asks — mission first, alone.** The bucket-3 questions are *not* independent, so do not dump them in one round. **The mission — "what is this system for?" — gates everything else.** Ask it first, on its own. Scope specifics, the data-root path, and repo naming are all downstream: they are shaped or reframed by the mission, and several (repo owner, repo name) become detect/derive once the mission and system name exist. Asking about git/repo/data-root before the mission is known is premature — the operator can't evaluate a recommendation for a system you can't yet describe. Only after the mission is established do you ask the (now-framed) remaining choices, bundled into a second round. This is the deliberate exception to the otherwise-applicable `exhaust-questions-in-planning` habit ("ask everything in one round"): bundle only genuinely *independent* asks; **sequence when later asks depend on an earlier answer.**

**How to ask (bucket 3):** plain-prose **recommend-then-confirm**, as a general practice (a bound user's profile may refine the style; read it at intake). State a recommendation with reasoning and ask for confirmation. **Never use `AskUserQuestion`, chip UI, multiple-choice boxes, or option lists.** Within a round (after mission), bundle the genuinely-independent asks with all recommendations surfaced. **Never assume an unanswered value** — if the operator declines or hasn't answered, block and re-ask cleanly; do not proceed on an assumed value (the P1 / `no-fabricated-data` floor applied to intake). A human can do the substitution by hand as a fallback, but the agent-driven intake is the default. The table below is the checklist of what to gather, annotated by bucket:

| Token | Value | Example |
|---|---|---|
| `<<ARCHITECT_NAME>>` | Canonical Architect name | `Example Service Architect` |
| `<<ARCHITECT_ID>>` | Kebab Architect ID | `example-service-arch` |
| `<<SYSTEM_ID>>` | Kebab system ID | `example-service` |
| `<<SYSTEM_NAME>>` | Function name | `Example Service` |
| `<<DATA_ROOT>>` | Absolute path chosen in step 2 | `/Users/operator/example-service` |
| `<<USER_ID>>` | Bound user ID | `the operator` |
| `<<ORCHESTRATOR_AGENT>>` | Orchestrator agent name or `(none)` | `(none)` |
| `<<ORCHESTRATOR_AGENT_CLAUSE>>` | Full sentence — see role-doc-template.md inline guidance | (template-resolved) |
| `<<TODAY>>` | ISO date of onboarding | `2026-05-27` |
| `<<TODAY_TIME>>` | Start time of bootstrap session in `HH:MM <<TIMEZONE>>` | `08:17 UTC` |
| `<<TODAY_END_TIME>>` | End time of bootstrap session in `HH:MM <<TIMEZONE>>` | `09:42 UTC` |
| `<<TODAY_DURATION>>` | Bootstrap-session duration | `1h 25m` |
| `<<TODAY_MACHINE>>` | Machine label where bootstrap ran | `Laptop` |
| `<<TIMEZONE>>` | Architect's pinned timezone (short label for stamps) | `UTC` |
| `<<TIMEZONE_LONG>>` | Architect's pinned timezone (IANA name for `TZ=…` env) | `Etc/UTC` |
| `<<MACHINE_LABELS>>` | Short-label mapping for the user's machines (comma-list) | `Laptop, Runner` |
| `<<MACHINE_MAP>>` | JSON object for `session.config.json` mapping each `scutil --get ComputerName` value → short machine label. **Detect** the user's machine names (`scutil`), don't ask. Must be valid JSON (an object). | `{"Operator Laptop": "Laptop", "Runner Host": "Runner"}` |
| `<<FEDERATION_REPO_REF>>` | Path or URL to federation repo for cross-refs | `../principles-of-good-architects` or full URL |
| `<<MISSION_PROSE>>` | Free-form mission paragraphs (per role-doc inline guidance) | (operator writes) |
| `<<SCOPE_DO_BULLETS>>` | Architect-specific scope bullets | (operator writes) |
| `<<SCOPE_DONT_BULLETS>>` | Architect-specific don't-do bullets | (operator writes) |
| `<<USER_NAME>>` | Name used to address the bound user in the role doc and `CLAUDE.md` | `the operator` |
| `<<REPO_OWNER>>` | GitHub account/org that owns the new Architect's repo | `example-org` |
| `<<REPO_NAME>>` | The new Architect's repo name | `example-service` |
| `<<REPO_LOCATION>>` | Where the system's git repo lives: `owner/name` for a private remote, or "local only — no remote yet". Rendered by bootstrap. | `local only — no remote yet` |
| `<<SYSTEM_SPECIFIC_PRINCIPLES>>` | §5 system-specific operating principles | (operator writes — inline guidance) |
| `<<VOICE_STYLE_BULLETS>>` | §6 voice & style bullets | (operator writes — inline guidance) |
| `<<SYSTEM_SPECIFIC_SESSION_STEPS>>` | §11 system-specific session steps — additions running at the standard extension point (start step 9 / end step 7). Most fresh Architects have none; leave the placeholder. | (operator writes — inline guidance) |
| `<<EXTRA_ARTIFACT_ROWS>>`, `<<EXTRA_INPUT_ROWS>>`, `<<EXTRA_GATE_BULLETS>>` | Optional extra rows in §7 / §8 / §9 | (operator writes, or delete the row — inline guidance) |
| `<<ARCHITECT_SPECIFIC_NEXT_BULLETS>>`, `<<ARCHITECT_SPECIFIC_OPEN_QUESTIONS>>`, `<<ARCHITECT_SPECIFIC_PENDING>>` | Optional rows in the session-handoff bootstrap entry | (operator writes, or delete — inline guidance) |

Tokens marked "(operator writes)" or "(template-resolved)" require human content, not mechanical substitution — the role-doc template carries inline guidance blocks for each. Delete the guidance blocks after writing. The mechanical tokens above (`<<USER_NAME>>`, `<<REPO_OWNER>>`, `<<REPO_NAME>>`) are simple find-and-replace.

**Resident-runtime systems — declare at onboarding, not later.** If this system's repo root is (or will be) owned by a live runtime persona with its own root `CLAUDE.md` (a resident agent), the Architect's `session.config.json` must declare `settings_path` (where the federation pushes its generated settings, off the persona's root file) and `pad_dir` (the Architect's own launch home, a sibling outside the persona's `CLAUDE.md` load path). Skipping this is not a cosmetic gap: without the declaration nothing tells the harness which persona owns the root, so a status check can read such a system as current when it is not, and the headless adoption runner (`curate/adopt-runner.py`) can launch an unattended, permission-bypassed session as the wrong persona. The bootstrap kit has no resident-runtime variant yet; until it does, declare these two keys by hand, following the commented block in `bootstrap-kit/session.config.json`.

### 6. Create the receipt-ritual inbox under data root

Per [ADR-0013](adr/0013-receipt-ritual.md), the receipt-ritual inbox lives under data root. Run:

```sh
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/pending
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/applied
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/rejected
mkdir -p <<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/withdrawn
```

(Substitute the actual data-root path and Architect ID.) These directories are data — under data root, gitignored from the system repo, not committed.

**Seed the user profile.** The new Architect reads `<<DATA_ROOT>>/users/<<USER_ID>>/profile.md` at session start (§11) to resolve the bound user's preferences. Copy the federation's current canonical profile into the new Architect's data root so it ships with the up-to-date preference set (e.g., `recommend-then-confirm`):

```sh
mkdir -p <<DATA_ROOT>>/users/<<USER_ID>>
cp <<FEDERATION_REPO_REF>>/users/<<USER_ID>>/profile.md <<DATA_ROOT>>/users/<<USER_ID>>/profile.md
```

This is a snapshot copy — the same pre-load model the kit uses for principles/habits. Ongoing preference changes propagate later via the receipt ritual once the profile-propagation path is built (federation [§13](federation-arch.md#13-open-questions) open item); until then, re-seed by re-copying when the federation profile changes. The profile is data — gitignored from the system repo, not committed.

### 7. Initial commit

Per ADR-0012's blanket-maintenance authority, the Architect commits its own system repo. First commit lands the kit'd files. Conventional Commits message style (per the `conventional-commits` universal habit):

```
feat: stand up <architect-id> system repo from bootstrap kit v0.17.0
```

Push to `main` if the project has a remote. A local-only project stops at the commit.

### 8. Register with Federation Architect

Open a Federation Architect session (in the federation repo). Tell it the new Architect is onboarded; provide the system-repo URL. Federation Architect:

- Confirms the portfolio entry.
- Mirrors the new role doc into `inputs/<system-id>-arch.md` (gitignored).
- Notes the onboarding event in `session-handoff.md` (federation repo).
- May, in the same or a follow-up session, draft an initial principle/habit shipment via the receipt ritual if anything in the new Architect's mission warrants explicit codification.

After step 8, the Architect is federation-bound and operational.

## What the kit installs

See [`bootstrap-kit/README.md`](bootstrap-kit/README.md) for the canonical list. High level: role doc, gitignore, ADR scaffolding, session-handoff scaffold, producer file scaffold, Claude Code settings (with the session-start + Bash-guard hooks pre-wired), repo README, and the code channel — the shared `session.py` harness (copied from the federation repo root), its per-system `session.config.json`, the generated `CANON.md` digest, and the generated `STANDARD.md` standard section. The universal set of principles + habits reaches the Architect through `CANON.md`, and the standard session rituals reach it through `STANDARD.md` — both **injected** into context each session by `session.py start`, neither a hand-maintained role-doc table or hand-authored §11 — per [ADR-0022](adr/0022-architect-ingest-distillation-and-code-channel.md) / [ADR-0024](adr/0024-standard-role-doc-section-is-generated-and-injected.md). The Architect receives `CANON.md` and `STANDARD.md` as generated artifacts; it never authors or regenerates them, and the federation-only generators and their sources (`curate/distill.py`, `curate/standardize.py`, the registries, `standard-source.md`) are **not** part of a fresh Architect's repo.

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
│ the operator reviews — approves, refines, or    │
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
│ checks its pending/ inbox, walks the operator   │
│ through each edit, applies/rejects/     │
│ withdraws per the receipt ritual.       │
│ Applied edits land in the Architect's   │
│ role doc; role doc CHANGELOG bumps.     │
└─────────────────────────────────────────┘
```

The receipt-ritual mechanism ([ADR-0013](adr/0013-receipt-ritual.md)) is the load-bearing infrastructure for the last two stages. New principles and universal habits propagate through this flow; the bootstrap kit pre-loads the *initial* set at onboarding-time so a fresh Architect doesn't have to receipt-ritual-adopt every principle on day one.

## The appliable-brief schema (`Apply: auto`)

Per [ADR-0029](adr/0029-receiving-architects-auto-adopt-at-startup.md) a receiving Architect **auto-adopts** pending edits at startup (the user's approval already happened upstream at registry-Accept). [ADR-0039](adr/0039-appliable-brief-schema-and-auto-adopt-mechanism.md) mechanizes that: [`session.py apply-briefs`](session.py) (Fork A, lifted from the retired `curate/apply.py`) reads the `pending/` inbox and **applies conforming briefs deterministically before the model wakes**, surfacing everything else. A brief qualifies for automatic application **only if it is authored to the strict schema below** — otherwise it is `Apply: manual` and a human handles it. Anything not conforming is surfaced, never half-applied or guessed.

The `apply-briefs` block in `session.py` (its header comment + engine) is the **executable specification**; this section is the author-facing summary. The template lives at [`bootstrap-kit/appliable-brief-template.md`](bootstrap-kit/appliable-brief-template.md).

**Header — YAML frontmatter at the very top of the brief:**

```
---
edit-id: <id>
target-file: <repo-relative path the ops mutate>
expected-base-version: vX.Y.Z     # MUST equal target-file's current **Version:** line
proposed-new-version: vX.Y.Z      # explicit — never "next MINOR"
apply: auto
---
```

**Body — an ordered list of operations,** each a `## op: <type>` heading. Operation payloads go in **tilde-fenced blocks** (`~~~before` … `~~~`), *not* backtick fences — role docs contain ```` ``` ```` fences themselves, so a backtick payload can't safely wrap them. A tilde fence is ≥3 tildes; the close is a tilde run at least as long as the open (lengthen it if a payload itself contains `~~~`). The four operations:

| Op | Payload | Rule |
|---|---|---|
| `replace` | `~~~before` + `~~~after` | The `before` block must occur **exactly once** in `target-file`. Zero or multiple → surfaced (never guess). No renumbering is ever implied — if numbers change, span the full renumbered range so the replacement is literal. |
| `version-bump` | *(none)* | A specialised `replace` on the `**Version:**` line, derived from `expected-base-version` → `proposed-new-version`. A brief whose proposed ≠ expected **must** include this op (final verify asserts the version moved). |
| `changelog-prepend` | `After:` anchor line + `~~~content`, plus an optional `File:` | Inserts the **literal** content block immediately after an anchor line that occurs exactly once. Not a template to adapt. `File:` names a different file to write into — for a system that keeps its role-doc changelog in a tracked sibling ([ADR-0123](adr/0123-the-role-doc-changelog-moves-to-a-tracked-sibling.md)), which has no `**Version:**` line and so cannot be a second `target-file`. Omitted = `target-file`, exactly as before. The file must already exist, and a path escaping the repo is refused. |
| `create-file` | `Path:` + `~~~content` | Creates the file if it does **not** exist; refuses (surfaces) if it does. Never clobbers. |

**Uniqueness is the anchor.** Explicitly **out of `auto`** (→ author as `Apply: manual`): "renumber the following," "also update X elsewhere," or any instruction whose result depends on reading surrounding context. Those are genuine judgment and route to a human.

**Rollout status (ADR-0039):** Fork A is **shipped** (session 66) — the verified auto-adopt engine has been lifted from `curate/apply.py` into the byte-identical shipped `session.py` as the **`apply-briefs`** subcommand, so every Architect carrying the current harness has the capability (`curate/apply.py` is retired). The federation runs it as its own `SessionStart` hook; the remaining step is promoting that hook into the standard settings **floor** to wire auto-adopt on fleet-wide. Existing prose briefs are **not** retrofitted (Fork B); the schema earns from the next redistribution authored `Apply: auto` forward. Until the floor promotion, an Architect without the `apply-briefs` startup hook still applies briefs via the model at session-start step 7.

### Authoring default: `auto`, not `manual` (ADR-0049)

Per [ADR-0049](adr/0049-apply-auto-is-the-authoring-default.md), redistribution briefs are authored to the strict `Apply: auto` schema **by default**. The rule is *everything automatic, everything code* — a redistribution never reaches the operator's interactive time. `manual` is a routing label between two automatic paths (the `apply-briefs` code path; the R3 headless-agent path on the Runner), never a resting state.

1. **Default is `auto`.** Read the target's *live* role doc over the shared volume ([ADR-0027](adr/0027-federation-delivers-edits-into-target-inboxes.md) forbids writes outside a target's inbox, not reads) and write literal per-target anchored ops. If the mutations fit the four strict ops against the target's actual text, author `auto`. **A parse gap is an engine bug, not a `manual`:** if the engine can't read a member's role-doc format (e.g. a version-line dialect), fix the engine — don't fall back to a human (one member's `` **Version**: `v0.1.7` `` dialect drove the session-67 `VERSION_LINE` broadening).
2. **`manual` requires a `manual-reason:` — and routes to R3, not the operator.** A brief authored `apply: manual` MUST carry a non-empty `manual-reason:` naming the genuine judgment/complexity the four ops can't express (`requires target-side judgment`, `multi-file migration` / `substrate install`, `attended`). It is *not* valid to write `manual` because the engine didn't parse a format, or because prose was easier. Per [ADR-0050](adr/0050-headless-background-adoption-runner.md) the manual partition is **total**: a manual brief must ALSO carry either a **`verify:` command** (→ the R3 headless runner adopts it) or **`manual-reason: attended`** (→ surfaces to the operator). A non-attended manual brief with no `verify:` falls into an unowned gap and `check-apply.py` refuses it.
3. **Capability adoptions fold judgment into the adopted text.** The deterministic part (artifact registration + CHANGELOG + version bump) is `auto` ops; the judgment part ("author a note when warranted") lives inside the adopted section itself — no separate pending item lingers.
4. **Lint before delivery.** `curate/check-apply.py` (sibling of `curate/check-brief.py`) refuses a `manual` brief with no reason, or a brief declaring no recognized apply mode. Run both on every brief before delivery.
5. **Verify each `auto` brief against the target** before delivering — copy it into the target inbox and run the target's own `python3 session.py apply-briefs --dry-run`; confirm it reports "would apply" (not "surface"). Delivery-time verification is the target's engine against the target's live repo ([P18](principles/master.md#p18--verify-everything)), so a broken anchor is caught before the member's next startup, not after.
6. **An `auto` brief may not target a file that decides what code runs — the engine refuses it** (WI-0348). `Apply: auto` is applied unattended at session start, and the brief arrived through a *gitignored* mailbox that peer systems and an external consultant write into. So the blast radius is bounded to ordinary content: anything under `.claude/`, `.git/`, `.github/`, any `.py`/`.sh`/other interpreted suffix, any file marked executable (`poga`, the shipped `git-hooks/`), and named runners like `Makefile` all **surface** instead, whichever of the three path fields names them (`target-file`, `changelog-prepend`'s `File:`, `create-file`'s `Path:`). This costs legitimate authoring nothing — every `auto` brief on disk targets a markdown role doc. If you need a member to change code, that is a `manual` brief with a `verify:` command on the R3 path, not an `auto` one. **Open and the operator's to settle:** whether `auto` should go further and be default-deny with an allowlist, since `session.config.json` and `deploy/registry.json` change behaviour without being code.

### Advanced: the R3 agent path — headless background adoption ([ADR-0050](adr/0050-headless-background-adoption-runner.md))

> **Advanced (optional).** This path needs a scheduled job and a host that runs it. A single project on one machine does not need it: its briefs are applied at session start, or by hand.

The residue that genuinely can't be strict-schema'd — a substrate install, a multi-file migration, an edit needing target-side judgment — is `apply: manual` with a `verify:` command, and it is adopted **not** at the operator's interactive time but by [`curate/adopt-runner.py`](curate/adopt-runner.py), the R3 agent path.

- **What it does.** Sweeps every Claude converged member; for each idle repo (no fresh liveness heartbeat, [ADR-0036](adr/0036-session-liveness-sidecar-and-orphan-auto-triage.md)) holding a runner-eligible brief, it opens a headless `claude -p` session *in that repo as that Architect*, works the brief, runs the brief's `verify:` command, and — **commit-only** ([ADR-0035](adr/0035-startup-turn-budget-inject-and-git-diagnosis.md) auto-pushes at the target's next interactive session) — keeps the adoption or, on a failed verify / errored session / no-commit, **resets to the pre-adoption HEAD** and authors a `comms/` `blocked` note. Non-Claude members are skipped — the runner is the Claude binding's path.
- **Eligibility.** `apply: manual` + non-empty `verify:` + `manual-reason` ≠ `attended`. `attended` / no-`verify` briefs surface to the operator unchanged.
- **Deployment.** A nightly LaunchAgent on the always-on runner host (the `Runner` role), installed via [`deploy/install-adopt-runner.sh`](deploy/install-adopt-runner.sh) (see [`deploy/README.md`](deploy/README.md)). It runs in the GUI session so `claude -p` can reach the operator's stored login token. The token expires and refreshes only interactively; a stale token makes the sweep **abort at its auth preflight** with a `blocked` note — never a half-applied brief. `ANTHROPIC_API_KEY` in the plist environment is the fully-unattended alternative.
- **Author-side takeaway.** When you author a `manual` brief, give it a real `verify:` command (a test, a grep, a `session.py` check) unless it must be `attended`. That `verify:` is the contract that lets the agent path adopt it unattended and safely.
- **And it must be able to ANSWER** ([ADR-0050](adr/0050-headless-background-adoption-runner.md) §5a, WI-0304). A failing `verify:` owes its reader **one line naming what was checked and what was absent — never a stack trace.** Give every `assert` a message (`assert cond, "the member's server entry is missing from its config"`); guard the lookups that can raise. `curate/check-apply.py` refuses a bare `assert` in a `python -c` one-liner at delivery, and the runner records a verify that crashes as a **DEFECTIVE BRIEF** — a fact about your brief, not about the member's adoption — quarantining it on the first sweep instead of retrying a broken check. Re-delivering the corrected brief clears that automatically. The rule exists because one brief's chained message-less asserts failed for fourteen nights and logged nothing but a truncated `KeyError`.

## Ongoing federation participation

After onboarding, each Architect:

1. **Captures learnings** at session end into its `architect-learnings.md` when there's something durable to capture. Zero is fine — don't manufacture.
2. **Checks its pending/ inbox** at session start. Any new federation-proposed edits get walked through with the operator via the receipt ritual. Applied edits land in the role doc; the receipt moves to applied/ (or rejected/ or withdrawn/).
3. **Updates session-handoff.md** at session end with the standard entry format.
4. **Commits, and pushes when there is a remote,** per the `session-end-git-ritual` universal habit.

The Federation Architect runs aggregation on cadence (currently the operator-triggered — see Cadence below) and proposes new edits via the receipt-ritual mechanism. Architects don't push to federation; federation reads producer files.

## What the federation does NOT do

- **Edit producer files.** Producer files are write-only from their Architect; read-only from federation's POV.
- **Ship role-doc edits without the operator's approval.** Always a gate. The receipt ritual is the mechanism but the operator-approval is the gate.
- **Force consistency.** When systems intentionally diverge, principles are tagged with applicability constraints (or universal habits get per-Architect overrides per [ADR-0009](adr/0009-data-storage-mechanics.md) §"Overrides").
- **Federate without provenance.** Every principle and habit traces back to specific entries in specific Architects' producer files or role docs.
- **Sync memory between machines.** [ADR-0016](adr/0016-memory-is-local.md). Memory is local; if a memory is load-bearing enough to need to survive a cross-machine restart, promote it to role doc / habit / ADR storage.

## Cadence

Currently undetermined — open question in [federation-arch.md §13](federation-arch.md#13-open-questions). Working assumption: **the operator-triggered.** Federation runs when the operator asks for it. Revisit when producer files start accumulating real volume.

## Conflict resolution

When two systems' learnings disagree (one says "verbose narration helps"; another says "terse narration is essential") — the Federation Architect **surfaces the conflict to the operator rather than auto-resolving**. Likely outcomes:

- **Genuine divergence** → tag the principle/habit with an applicability constraint. Both systems hold their position.
- **One is wrong** → corrected position becomes the principle. Other system gets a receipt-ritual edit to align.
- **Both are wrong** → the operator specifies the right answer. Principle reflects that.

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
