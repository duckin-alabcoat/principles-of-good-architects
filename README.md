# Principles of Good Architects

**POGA** is a **federation system** for a portfolio of per-system *Architects* — long-lived collaborator roles that build and maintain a set of personal AI systems (one per project). It does three things:

1. **Bootstraps new Architects.** Stands up a competent, federation-bound Architect from cold using a templated [bootstrap kit](bootstrap-kit/) and the [PROCESS.md](PROCESS.md) runbook — so a new project gets a working Architect on day one, pre-loaded with everything learned so far.
2. **Curates feedback from existing Architects.** Reads each system's `architect-learnings.md`, clusters observations across systems, and distills the durable ones into **principles** (claims about Architect quality) and **universal habits** (concrete practices).
3. **Provides principles and habits back.** Proposes role-doc edits to each affected Architect, so something learned with one Architect benefits all of them. The user approves every edit.

The Architect of this system is the **Federation Architect** — see [federation-arch.md](federation-arch.md). Per [ADR-0003](adr/0003-federation-architect-is-a-participant.md) it is itself a participant: the principles and habits it ships apply to it too.

> ### About this public version
>
> This is a **sanitized, generic version** of a personal system, shared as a reference for anyone interested in the pattern. It describes a way of running several long-lived AI "Architect" collaborators (one per project) and federating the lessons each one learns so they don't have to be re-taught across projects.
>
> - **The user is referred to generically** ("the user"). Personal identifiers, account names, machine names, and paths have been replaced with placeholders.
> - **The example systems are fabricated.** Recipe Box, Home Hub, Trail Log, Campaign Tracker, Arcade, and Plant Care (and their orchestrator agents Basil, Hearth, Cairn) are illustrative hobby projects invented to demonstrate the format — they are not anyone's real systems. Swap in your own.
> - **The running session log has been replaced with a short stub** ([session-handoff.md](session-handoff.md)); the operational `briefs/` directory is omitted. Everything else — the role doc, all ADRs, the bootstrap kit, the process — is the real framework.
> - It's designed to be OS-agnostic (macOS or Linux).

## Where to start

- **Current role doc:** [federation-arch.md](federation-arch.md) — the Federation Architect's mission, scope, principles, voice. The canonical entry point.
- **Bootstrapping a new Architect:** [PROCESS.md](PROCESS.md) — the fresh-Architect onboarding runbook — and [bootstrap-kit/](bootstrap-kit/) — the templates it installs.
- **Decisions to date:** [adr/README.md](adr/README.md) — every structural choice with context, alternatives, and consequences.
- **Portfolio:** [portfolio.md](portfolio.md) — registry of systems, orchestrator agents, and Architects.
- **Current state:** [session-handoff.md](session-handoff.md) — in the real system, the most recent entry says what the last session did and what's next (stubbed in this public version).
- **Founding intent:** [architect-learnings-federation-brief.md](architect-learnings-federation-brief.md) — the original brief. Predates several naming corrections and the mission reframe ([ADR-0017](adr/0017-bootstrapping-is-a-federation-responsibility.md)); treat as historical context, not current spec.

## Directory layout

```
.
├── README.md                                # this file
├── CLAUDE.md                                # auto-load trigger → points the agent at the role doc
├── federation-arch.md                       # Federation Architect role doc (versioned)
├── PROCESS.md                               # fresh-Architect onboarding runbook
├── bootstrap-kit/                           # templates the runbook installs into a new Architect's repo
├── portfolio.md                             # registry: systems, orchestrator agents, Architects
├── session-handoff.md                       # per-session state transfer (stubbed in this public version)
├── architect-learnings-federation-brief.md  # founding brief
├── adr/                                     # Architecture Decision Records
│   ├── README.md                            # ADR index
│   ├── template.md                          # ADR template
│   └── NNNN-*.md                            # one ADR per decision
└── specs/                                   # design specs

# Data (gitignored in the real system; omitted from this public version —
# synthesized from other systems' producer files and role docs):
#   inputs/        mirrored producer files & role docs
#   principles/    distilled cross-system principles registry + history
#   habits/        universal-habits registry + history
#   users/         per-user preference profiles + history
#   architect-learnings.md   the Federation Architect's own producer file
```

## Glossary

- **System** — one of the user's products (recipe box, home-hub, trail-log, campaign-tracker, arcade). Named by function.
- **Architect** — long-lived collaborator role that builds and maintains a system. One per system. Named `<Function> Architect`.
- **Orchestrator agent** — codename for the central agent inside a system, where one exists (e.g. Basil = recipe box; Hearth = home-hub). Not the same as the system.
- **Auditor** — short-lived, cold-context, mission-briefed reviewer. Separate role class from Architect. See [ADR-0004](adr/0004-auditor-as-separate-role-class.md).
- **Producer file** — a system's `architect-learnings.md` (at the Architect repo root). Append-only journal of that Architect's learnings. Read-only from the federation's POV.
- **Principle** — a property claim about Architect quality; universal by definition. Registry: `principles/master.md`.
- **Universal habit** — a concrete practice implementing a named parent principle. Registry: `habits/master.md`.
- **Bootstrap kit** — the templated scaffolding ([bootstrap-kit/](bootstrap-kit/)) that stands up a new Architect's repo from cold.
- **System ID** — kebab-case function name (`recipe-box`, `home-hub`).
- **Architect ID** — `<system-id>-arch` (`recipe-box-arch`).

## Operating principles

- The mission is three co-equal responsibilities — bootstrap, curate, redistribute. See [federation-arch.md §2](federation-arch.md) and [ADR-0017](adr/0017-bootstrapping-is-a-federation-responsibility.md).
- Every non-trivial decision lands as an ADR. See [ADR-0001](adr/0001-use-adrs.md).
- The Federation Architect is a participant in the federation, not an outside observer. See [ADR-0003](adr/0003-federation-architect-is-a-participant.md).
- Provenance is mandatory — no principle or habit ships without traceable references to source entries.
- The user has approval authority on all role-doc edits and principle promotions.
- System files are stored in GitHub and maintained by the Federation Architect without per-commit approval. Data is excluded by policy and `.gitignore`. See [ADR-0007](adr/0007-github-as-system-storage-data-excluded.md).
</content>
