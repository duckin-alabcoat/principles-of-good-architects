# ADR-0094: A project declares its data; the backup layer owns retrieval

**Status:** Accepted
**Date:** 2026-08-16
**Deciders:** the operator, Federation Architect

> This is the public version of this ADR. It keeps the decision and the reasoning, and
> describes the backup layer generically.

## Context

[ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D3 introduced
`state-manifest.<system>.json`: a per-system declaration of non-derivable state — what
`poga restore` must fetch, from which backup source, to where. The federation authored its
own as the reference instance; [WI-0006](../work-items/) held the job of authoring one for
every other member.

Two problems surfaced together in session ~153, and they pull in opposite directions.

**First, the manifest conflates three different jobs.** Asked whether every project should
be restorable from a written record, the operator separated cases the design had treated as one:

1. **System crash, lose everything → restore from backup.** Whole-machine. You do not
   consult a per-project record; you restore the machine and the projects come back inside
   it. Nothing in this scenario reads a manifest.
2. **New machine, migrate a project across → `poga restore`.** You do not restore the old
   machine onto the new one. You bring one project over, and its data is not in a backup at
   all — it is live on the old machine.
3. **One project damaged, machine healthy → `poga restore`.** You do not restore a whole
   machine to repair one project.

the operator ruled that these are distinct cases, and that a project does have a reason to know
how to get its data back in order to rebuild.

The manifest as built serves only scenario 1 — the one case that never reads it. Its
`data_sources` array groups by **backup medium**, listing the same paths once per medium
(a `failure_domain` value per medium) so restore can pick by disaster. Scenarios 2 and 3, the common ones, have no
expressible source at all, because "the live copy on the machine you are migrating from"
is not a backup.

**Second, the manifest makes every project know the backup scheme.** the operator's ruling: no
project should know anything about the backup scheme. The federation's own manifest named
the backup media, the machine, the paths inside them, and the steps and pointers needed to
retrieve from each. Multiplied across the fleet that is one copy per system of facts that belong to
the machine, not to any project — the [P16](../principles/master.md#p16--avoid-duplication)
silent-drift shape.

That is not hypothetical here. The federation manifest carries a recorded correction: the
`source_path` was written without the snapshot's source-volume prefix, and on the driver's
first real run **every path failed** `No such file or directory`. One project, one wrong
infrastructure fact, caught by a drill. Every project holding that fact is one more chance
to be wrong and no single place to fix it.

Separately, the operator placed ownership: backup and restore belong to the backup layer, not to
the federation — the same reassignment [WI-0001](../work-items/) already took.

## Decision

**A project declares *what* data it has and *how to know it came back correct*. The backup
layer owns *where* that data is kept and *how* to retrieve it. A project manifest names no
backup medium.**

### 1. What the project declares — and only the project can

- **Which of its files are non-derivable** — not in git, not regenerable. This is a
  judgment about its own contents that no external actor can make. The federation's own
  manifest excludes `inputs/` because those are mirrors re-fetchable from source; that call
  required understanding the project.
- **Where those files sit** inside the install root, parameterized (never a hardcoded home
  directory — the same data can sit at different absolute paths on different machines).
- **How to verify they came back correct**, not merely present. *"`users/` holds the expected
  profile; `architect-learnings.md` is non-zero."* Presence is not integrity, and only the
  project knows the difference.

### 2. What the project must not carry

Backup medium, volume name, path syntax inside a backup, provider, account, retrieval
steps, access pointers, `failure_domain`, `automation` ceiling. All of it is infrastructure. All of it is owned by the backup layer.
None of it is a fact about the project.

### 3. The handoff

The declaration lives **in the project's own git repo at a known path** — the property that
makes this work at all: you clone the repo and hold the declaration before any data exists.
This is the [ADR-0021](0021-cross-system-status-surface.md) `STATUS.md` pattern (every
system emits one; fleet tooling reads each system's own copy), not a federation-held
registry.

The sequence is three actors and two handoffs:

1. The code is rebuilt from git.
2. **A backup-side process places the data** into the declared locations. This is not the
   project's job and the project does not do it.
3. **The project reads its own declaration and verifies**, reporting honestly what is
   missing. This is the reason the project needs the file, and it is the step nothing else
   can perform.

Step 3 is already the built behaviour: `poga restore` refuses to report plain success and
exits `3 — incomplete` when something is owed ([ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D2).
This ADR does not change that contract; it narrows what restore is *responsible for* to
verification and reporting.

the operator stated the same model as a ruling: in a restore the project rebuilds its files, then
reads its data declaration; it is not responsible for placing the data. A backup-side
process does that, when told to restore data according to the declaration.

### 4. Credentials are out of scope and stay that way

Secrets are not restored into a location; they are re-issued. The sibling
`credentials-manifest.<system>.json` already declares transport and re-provision procedure,
never the value ([P14](../principles/master.md#p14--no-confidential-data-in-chats)). A
completed data restore therefore still ends with a short re-provision list. That is correct
behaviour, not a gap.

## Alternatives Considered

- **Leave the manifest as-is and author the rest.** Rejected: it would produce one copy
  per member of the wrong shape — a schema serving only the scenario that never reads it,
  each carrying duplicated infrastructure facts. Authoring first and re-scoping later
  multiplies the correction by the fleet.
- **A retrieval service: the backup layer exposes "get me these paths as of
  ~T", deciding medium itself; projects hold zero backup knowledge and no retrieval file
  exists anywhere.** This is the cleaner end state and this ADR is deliberately compatible
  with it — under this decision no project would change when it arrives. Not chosen *now*
  because it is cross-system work that cannot start until the backup layer builds its half,
  and the duplication harm is live today. Recorded as the intended direction, not a
  rejection.
- **Keep `failure_domain` on the project side as a hint.** Rejected: it is precisely the
  disaster-recovery axis this ADR removes, and a "hint" the project cannot act on is
  infrastructure knowledge with extra steps.
- **A federation-held registry of all systems' data sets.** Rejected: it inverts the
  ownership this ADR establishes and reintroduces a second copy of each project's own
  facts ([P13](../principles/master.md#p13--single-writer-per-state) / [P16](../principles/master.md#p16--avoid-duplication)).

## Consequences

- **`state-manifest.schema.json` changes shape.** `data_sources` (grouped by medium) gives
  way to a declaration of the data set with its verification, stated once. `kind`,
  `failure_domain`, `automation`, `coordinates` and `retrieval` leave the project schema.
  This is a **migration-bearing** change: it alters a declared file format members carry.
- **The retrieval knowledge is not deleted, it moves.** The federation's current manifest
  holds hard-won, probe-verified coordinates (the volume-prefix correction above). Those go
  to the backup layer intact; losing them to a refactor would be the worst outcome here.
- **[ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) D3 is refined, not
  superseded.** Its argument — that restore must share bootstrap's path or rot between
  drills — is untouched, as is the manifest's existence and its consumption by `restore`.
  What changes is what the manifest contains and who owns the other half. D1, D2, D4 and D5
  stand.
- **WI-0006 is reframed rather than closed.** It was briefly closed in session ~153 on the
  premise that whole-machine restore made per-project records pointless; that premise was
  wrong and the closure is recorded as withdrawn on the item. It is now gated on this
  schema change, so the remaining manifests are authored once, in the right shape.
- **Two owners now have to agree.** The federation owns the project-side declaration
  and its schema; the backup layer owns retrieval and the process that places data. Where
  the backup layer's owner is an Architect, this ADR reaches it through the receipt ritual
  ([ADR-0013](0013-receipt-ritual.md)) — a federation decision does not bind another owner's
  domain, and the boundary is a proposal to it, not an instruction.
- **Scenario 1 stops being the design centre.** Whole-machine restore needs no project
  manifest and gets none. The manifest exists for migration and partial repair, which is
  what it will be tested against.

**Reality status (per [`mark-adr-reality-status`](../habits/master.md#mark-adr-reality-status)):**
**Partial** (WI-0145, session ~408). Built: `state-manifest.schema.json` 2.0.0 declares the
data set once under `data`, with per-path verification (present and non-empty, plus
optional `contains`). None of `kind`, `failure_domain`, `automation`, `coordinates` or
`retrieval` remain. A 1.x manifest is refused by name before anything is built.
`state-manifest.federation.json` is the worked example. Hydrate in `poga_cli.py` verifies
what the backup layer placed and retrieves nothing, so no driver remains. Owed: the part of
the old retrieval text the backup layer's own runbook does not yet carry is held verbatim
in dated `//quarantine-*` blocks in the federation manifest, and is deleted once that
runbook is read back and found to carry it. The member brief is written and staged, and the
fleet push waits on the soak. `--scenario` is still accepted and printed, but it no longer
selects anything.

## References

- [ADR-0061](0061-poga-unified-lifecycle-cli-and-state-manifest.md) — the manifest this refines; D2's honesty contract, D3's schema.
- [ADR-0013](0013-receipt-ritual.md) — how this reaches the backup layer's owner.
- [ADR-0021](0021-cross-system-status-surface.md) — the each-system-emits-one, tooling-reads-each pattern the declaration follows.
- [P16](../principles/master.md#p16--avoid-duplication) — the duplicated-infrastructure-fact harm.
- [P13](../principles/master.md#p13--single-writer-per-state) — one writer per fact; the project owns its data set, the backup layer owns retrieval.
- [P14](../principles/master.md#p14--no-confidential-data-in-chats) — why credentials stay in the sibling manifest and are re-provisioned, not restored.
