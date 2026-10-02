# <<ARCHITECT_NAME>>

This repo holds the system files for the **<<SYSTEM_ID>>** system's Architect. The Architect of this system is the **<<ARCHITECT_NAME>>** — see [`<<ARCHITECT_ID>>.md`](<<ARCHITECT_ID>>.md).

<<ORCHESTRATOR_AGENT_CLAUSE>>

> Token guidance: replace `<<ORCHESTRATOR_AGENT_CLAUSE>>` with one of:
> - If orchestrator agent exists: `The user-facing runtime is <<ORCHESTRATOR_AGENT>>; this repo holds the Architect's system surface, not the runtime.`
> - If no orchestrator agent: `This system has no orchestrator agent; the Architect is the only entity in scope.`
> Delete this guidance block after replacing.

## Where to start

- **Role doc:** [`<<ARCHITECT_ID>>.md`](<<ARCHITECT_ID>>.md) — what the <<ARCHITECT_NAME>> does, scope, principles, voice, rituals.
- **Decisions:** [`adr/README.md`](adr/README.md) — index of Architect-side decisions.
- **Current state:** [`session-handoff.md`](session-handoff.md) — most recent entry tells what last session did and what's next.
- **Federation context:** [federation-arch.md in the federation repo](<<FEDERATION_REPO_REF>>/federation-arch.md), [PROCESS.md](<<FEDERATION_REPO_REF>>/PROCESS.md), [federation ADRs](<<FEDERATION_REPO_REF>>/adr/) — federation-wide context this Architect operates under.

## Directory layout

```
.
├── README.md                          # this file
├── <<ARCHITECT_ID>>.md                # role doc (versioned)
├── session-handoff.md                 # per-session state transfer (newest first)
├── adr/                               # Architect-side Architecture Decision Records
│   ├── README.md                      # ADR index
│   ├── template.md                    # ADR template
│   └── NNNN-*.md                      # one ADR per decision
├── architect-learnings.md             # producer file (gitignored — data per ADR-0012)
└── .claude/
    └── settings.json                  # permission baseline (substrate enforcement)
```

Data (gitignored, lives under the data root at `<<DATA_ROOT>>`):

```
<<DATA_ROOT>>/
├── users/<<USER_ID>>/profile.md       # read at session start (maintained by federation)
├── principles/master.md               # read at session start (maintained by federation)
├── habits/master.md                   # read at session start (maintained by federation)
└── proposed-edits/<<ARCHITECT_ID>>/   # receipt-ritual inbox (per ADR-0013)
    ├── pending/
    ├── applied/
    ├── rejected/
    └── withdrawn/
```

## Repo governance

This repo is governed by the federation's [ADR-0012: Per-Architect repository conventions](<<FEDERATION_REPO_REF>>/adr/0012-per-architect-repo-conventions.md):

- **Authority.** The <<ARCHITECT_NAME>> maintains this repo without per-commit approval. Routine commits proceed without gating; destructive operations confirm with <<USER_NAME>>.
- **Classification.** System files (role doc, ADRs, templates, this README) are committed. Data files (mirrored cross-system artifacts, the producer-side learnings file, receipts, user-profile reads) are not — they live under the data root.
- **Mechanical enforcement.** `.gitignore` carries the data-path patterns. Any new data path is added in the same commit that creates the path.
- **Visibility.** Private. System files reference internal portfolio details and operational context.

## Federation participation

This Architect is registered in the federation at [`portfolio.md`](<<FEDERATION_REPO_REF>>/portfolio.md). Federation-wide principles and universal habits land here via the [receipt ritual](<<FEDERATION_REPO_REF>>/adr/0013-receipt-ritual.md) — proposed edits arrive in `<<DATA_ROOT>>/proposed-edits/<<ARCHITECT_ID>>/pending/` and are surfaced at session start per [`<<ARCHITECT_ID>>.md` §11](<<ARCHITECT_ID>>.md).

The Architect produces durable observations into `architect-learnings.md` per the `producer-side-learnings` universal habit. The Federation Architect reads that file (via mirroring) and may distill new principles or habits to ship back through the federation.
