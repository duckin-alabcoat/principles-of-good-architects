# Principles of Good Architects

This project is the **federation system** that consolidates learnings across a portfolio of per-system Architects (Recipe Box, Home Hub, Trail Log, Campaign Tracker, Arcade, ...) and proposes role-doc edits back so principles learned in one system propagate to the others.

The Architect of this system is the **Federation Architect** — see [federation-arch.md](federation-arch.md).

> ### About this public version
>
> This is a **sanitized, generic version** of a personal system, shared as a reference for anyone interested in the pattern. It describes a way of running several long-lived AI "Architect" collaborators (one per project) and federating the lessons each one learns so they don't have to be re-taught across projects.
>
> - **The user is referred to generically** ("the user"). Personal identifiers, account names, machine names, and paths have been replaced with placeholders.
> - **The example systems are fabricated.** Recipe Box, Home Hub, Trail Log, Campaign Tracker, Arcade, and Plant Care (and their orchestrator agents Basil, Hearth, Cairn) are illustrative hobby projects invented to demonstrate the format — they are not anyone's real systems. Swap in your own.
> - **The running session log has been replaced with a short stub** ([session-handoff.md](session-handoff.md)); the operational `briefs/` directory is omitted. Everything else — the role doc, all ADRs, the bootstrap kit, the process — is the real framework.
> - It's designed to be OS-agnostic (macOS or Linux).

## Where to start

- **Founding intent:** [architect-learnings-federation-brief.md](architect-learnings-federation-brief.md) — the user's original brief for the system. Predates several naming corrections; treat as historical context, not current spec.
- **Current role doc:** [federation-arch.md](federation-arch.md) — what the Federation Architect does, scope, principles, voice.
- **Decisions to date:** [adr/README.md](adr/README.md) — every structural choice with context, alternatives, and consequences.
- **Process for participating systems:** [PROCESS.md](PROCESS.md) — how a system's Architect joins the federation.
- **Portfolio:** [portfolio.md](portfolio.md) — live registry of systems, orchestrator agents, and Architects.
- **Current state:** [session-handoff.md](session-handoff.md) — most recent entry tells what last session did and what's next.

## Directory layout

```
.
├── README.md                                # this file
├── architect-learnings-federation-brief.md  # the user's founding brief
├── federation-arch.md                       # Federation Architect role doc (versioned)
├── PROCESS.md                               # onboarding for participating Architects
├── adr/                                     # Architecture Decision Records
│   ├── README.md                            # ADR index
│   ├── template.md                          # ADR template
│   └── NNNN-*.md                            # one ADR per decision
├── inputs/                                  # mirrored producer files & role docs (not yet populated)
│   ├── <system-id>-arch.md                  # mirror of each system's Architect role doc
│   └── <system-id>-learnings.md             # mirror of each system's architect-learnings.md
└── principles/                              # distilled cross-system principles (not yet populated)
    └── master.md                            # the master principles doc
```

## Glossary

- **System** — one of the user's products (recipe box, home-hub, trail-log, campaign-tracker, arcade). Named by function.
- **Orchestrator agent** — codename for the central agent inside a system, where one exists (Basil = recipe box; Hearth = home-hub, TBC; Cairn = trail-log). Not the same as the system.
- **Architect** — long-lived collaborator role that builds and maintains a system. One per system. Named `<Function> Architect`.
- **Auditor** — short-lived, cold-context, mission-briefed reviewer. Separate role class from Architect. See [ADR-0004](adr/0004-auditor-as-separate-role-class.md).
- **Producer file** — a system's `docs/architect-learnings.md`. Append-only journal of that Architect's learnings. Read-only from the federation's POV.
- **Master principles doc** — `principles/master.md`. Distilled cross-system principles. Tiered: `architect-general`, `domain-general`, `auditor-general`.
- **System ID** — kebab-case function name (`recipe-box`, `home-hub`).
- **Architect ID** — `<system-id>-arch` (`recipe-box-arch`).
- **Auditor engagement ID** — `<system-id>-aud-<YYYY-MM>[-<slug>]`.

## Status

Bootstrapping (2026-05-21). No producer files exist yet portfolio-wide. First substantive work: ingesting the Recipe Box Architect's role doc and doing a backfill pass on de-facto principles.

## Principles followed

- Every non-trivial decision lands as an ADR. See [ADR-0001](adr/0001-use-adrs.md).
- The Federation Architect is a participant in the federation, not an outside observer. See [ADR-0003](adr/0003-federation-architect-is-a-participant.md).
- Provenance is mandatory — no principle ships without traceable references to source entries.
- The user has approval authority on all role-doc edits and principle promotions.
- System files are stored in GitHub and maintained by the Federation Architect without per-commit approval. Data is excluded by policy and `.gitignore`. See [ADR-0007](adr/0007-github-as-system-storage-data-excluded.md).
