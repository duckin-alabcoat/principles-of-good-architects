# ADR-0073: A canonical work-item store, drawn WI-NNNN ids, one file per item

**Status:** Accepted
**Date:** 2026-07-27 (session ~101)
**Builds on:** [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) (drawn, never picked — the allocator this mirrors), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (coordination store, claims), [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) C4 (claims)
**Deciders:** the operator (commissioned via the 2026-07-27 consultant brief, ruled the ROADMAP-derivation direction); Federation Architect (design + build)
**Reality:** Built — session ~101, suite 782 green

## Context

A consultant brief (`2026-07-27-consultant-work-item-store-and-startup-ritual.md`)
observed that a session start needs the same three things — run the startup, state
the Architect's identity, list the numbered work — and that two of the
three parts already exist mechanically (identity via the announce banner; "what's
checked out" via the ADR-0062 coordination store) but the third, a canonical
machine-readable work list, does not. `ROADMAP.md` is the standard's work list, but it
is prose: no stable ids, no dependencies, no oracle a session can query.

Investigating turned up a live mechanism doing part of this job already, built session
78: `cmd_claim`/`cmd_claims` (ADR-0051 C4) let a lane claim a ROADMAP `## Next` bullet
carrying a hand-picked `<!-- id: slug -->` anchor, recorded as a `claims/<id>.json`
record in the shared coordination store. It has no draw discipline — unlike ADR
numbers, two lanes can mint the same slug with zero collision protection — which is
exactly the defect class ADR-0069 fixed for ADR numbers and this brief asks fixed here.

## Decision

### D1 — Durable ids, drawn via the same coord-store discipline as ADR numbers

Work items get `WI-NNNN` ids, reserved via `session.py wi-next` (`_wi_reserve_next`),
a direct structural mirror of `_adr_reserve_next` (ADR-0069): a `wi-alloc` coord-store
namespace, defended at the land gate (R5a-equivalent, `_wi_land_gate`), with sibling
lane-tip scanning (R5c) and TTL-tied-to-trunk-visibility reservation lifetime (R5b).
Numbers are drawn, never picked — the same rule, the same mechanism, a new namespace.

### D2 — One file per item (`work-items/WI-NNNN-slug.md`), not one growing file

Discovered empirically while building this: a single shared `work-items.md` file, with
every `wi-new` appending a block, means two lanes each adding an item conflict at the
**git-rebase level** — both editing the tail of the same file — before either reaches
the land gate's collision logic at all. That is exactly the class of conflict the
one-file-per-ADR layout was already built to avoid. `work-items/` therefore mirrors
`adr/NNNN-slug.md` precisely: the number is identity, the slug (regenerated from the
title on every write, stale filename removed) is for human readability in a directory
listing or `git log`. Two lanes adding *different* items never touch the same path, so
they never conflict; two lanes hand-picking the *same* number produce either a real git
ADD/ADD conflict (identical filename) or, when their slugs differ, survive the rebase
and get caught by name at the land gate — verified by test (`WiAllocationGateTest`,
mirroring `AdrAllocationGateTest` case-for-case).

### D3 — Single writer per fact (P13)

- **The item file** owns `id`/`title`/`status`/`section`/`blocked-by`/`group`/`notes`.
  `status ∈ {open, in-progress, done, held}`; `section ∈ {next, backlog}` decides which
  rendered ROADMAP section an open/in-progress item appears under.
- **The coordination store stays the sole owner of claims** — nothing in the item file
  duplicates claim state. `cmd_claim`/`cmd_claims`/`_claims_orientation_line` are
  retargeted onto `_wi_claimable_items()` (open/in-progress items) in place of the old
  ROADMAP-anchor parser; the CLI contract (exit codes, refusal messages, orientation
  line shape) is unchanged, only the backing store is.
- **The rendered list is a join computed at render time** (`session.py wi-list`):
  items + live claims + blocked-by-against-still-open-ids. Nothing writes the join. A
  snapshot (`{ordinal: real_id}`) is pinned to `.session-state/wi-snapshot-<csid>.json`
  on every render, so a later "do 3" resolves against what was actually shown even if
  the store changed underneath (the brief's D2 requirement).

### D4 — No DAG engine

`blocked-by` is a flat list of ids. `wi-list` marks an item blocked when any listed id
is still open; no topological sort, no critical path, no scheduling — that belongs to
whatever dispatches work against the list (a separate, later concern), not the store.

### D5 — In-repo files, not GitHub Issues

Matches the federation's file-based, offline-first, multi-runtime doctrine (an external
issue tracker would break offline, add a second source of truth outside the repo, and
not survive a non-Claude runtime the same way). No real trade-off once stated this way.

### D6 — ROADMAP.md's Next/Backlog render from the store; Recently-shipped stays prose

the operator's ruling. `Next` and `Backlog` are, structurally, *already* nothing but a work-item
list — rendering them from `work-items/` retires the whole premise of parsing ROADMAP
prose as data (a pending brief proposing a ROADMAP parser) and collapses to one list, never
two describing the same work (P16). `Recently-shipped` stays hand-authored: a work-item
title cannot carry the outcome-framing ("X can no longer be fooled by a stale mirror")
the maintenance footer requires, and that needs a human, not a render.

## Alternatives Considered

- **One shared `work-items.md` file.** Rejected — proven live to create exactly the
  git-rebase-conflict class the ADR layout was built to dodge; see D2.
- **GitHub Issues / an external tracker.** Rejected per D5.
- **Render `Recently-shipped` from the store too, retiring hand-authored ROADMAP
  entirely.** Rejected — flattens outcome narrative into task titles; see D6.
- **Derive the store from ROADMAP prose (parse structured blocks back out of it).**
  Rejected — keeps two sources of truth for one fact, the opposite of what this ADR is
  for.

## Consequences

- The session-78 `<!-- id: slug -->` anchor mechanism in `ROADMAP.md` is superseded;
  once `Next`/`Backlog` are regenerated from the store those anchors become vestigial
  and are removed in the same pass (see the migration commit).
- `cmd_claim`/`cmd_claims`/`_claims_orientation_line` keep their exact CLI surface;
  every existing caller (the start-block orientation line) needed no change beyond the
  backing-store swap.
- Land, at all three land paths (candidate / branch / worktree-lane), now runs a second
  gate (`_wi_land_gate`) alongside the existing ADR one — same fail-open-on-git-error
  posture, same collision message shape.
- Fleet redistribution is a deliberate later step, not part of this pass — dogfooded on
  `federation` first per established practice (the `session.py`/`poga` vendoring
  pattern), then shipped via the normal fleet push once proven.
- `poga`'s eventual PREP step (the lifecycle-inversion brief, pending) will call
  `wi-list`'s underlying module directly; until then it ships as a `SessionStart` hook
  invocation of the same code path, per the brief's own sequencing note.

## Verification

- `tests/test_worktree_lane.py::WiAllocationGateTest` — the R5a/R5b/R5c acceptance
  drills, one-for-one with `AdrAllocationGateTest`: hand-picked number against a
  sibling reservation blocks the land; trunk-duplicate number (different filename)
  blocks the land; a drawn number lands; sibling-lane-tip scan pushes the allocator
  past a committed-but-unlanded number; an expired reservation with a living lane still
  holds, reaps once the branch is gone.
- `tests/test_claims.py` — rewritten against the `work-items/` fixture: claimable-items
  parsing (open/in-progress only, done excluded), claimed/available/orphan status, the
  claim/release CLI contract, lane-branch identity, plus new coverage for
  `wi-new`/`wi-status`/`wi-list` (draws distinct ids, updates one file without touching
  siblings, excludes done items, pins a snapshot).
- Full suite: 782 green.
