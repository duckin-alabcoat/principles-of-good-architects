# ADR-0139: A closed work item stays closed, and a residue is minted fresh with a resolvable citation

**Status:** Accepted
**Date:** 2026-09-17
**Session:** ~336 (lane poga-10, dispatch D-2c0d07)
**Work item:** WI-0343
**Builds on:** [ADR-0073](0073-work-item-store.md) (the store — `status`, one file per item, single writer per fact), [ADR-0074](0074-inbox-is-a-mailbox-triage-mints-work-items.md) (the `source:` field and the point-don't-restate rule)
**Deciders:** Federation Architect — the rule was decided in session ~302 (2026-09-13) under the operator's standing decide-and-record delegation, after he ruled the question plumbing and left the call in the lane; this ADR records it where it can be found, and adds D2's resolvability requirement and D4
**Reality:** — (doctrine; the decision is the artifact)

---

## Context

[ADR-0073](0073-work-item-store.md) D3 gives an item a `status` and makes the item file its
single writer. What no ADR has ever said is whether `done` is **final**.

The gap surfaced as a disposition problem, not as theory. OPS-0007's first run (2026-09-11)
found three *residues* — work that outlived the item that owned it:

- **WI-0234** (a member has no deploy contract, no tag, no registry row) was closed in
  session ~241 because all three premises in its title had become false. It closed on
  **expired premises, not on completed work**, and the brief behind it still asked for four
  things.
- **WI-0296** (`cmd_wi_render` writes ROADMAP.md with no under-test guard) closed because a
  *different* item's fix composed over it. Its own closing note records what survived:
  *"a trunk checkout that is NOT a lane still has no am-I-writing-the-tree-I-was-asked-about
  check."*
- A third, attributed to "WI-0210", needed no filing at all: that id does not exist — it was
  renumbered to WI-0304 at the session-241 land, WI-0304 is open, and its body already **is**
  the precondition the residue described.

Both surviving residues sit behind a closed item, so the obvious move — extend the item that
owns the work — is unavailable without **reopening** it. Nothing in the store, the verbs, or
the ADRs took a position, and the substrate's silence read as permission:
`poga work status <id> --status open` accepts a `done` item today with no refusal and no
comment (`cmd_wi_status` validates only membership in `WI_STATUSES`; `wi-check` checks
unknown statuses, gaps, self-blocking, dangling `blocked-by`, unset `impact` and `version`-on-
non-terminal — never a done→open transition).

**This is not a rare shape.** A backlog that is actively triaged closes items on expired
premises and on composed fixes as routine, and every such close is a candidate. It has also
already been answered *both ways* in this repo's own history — see D4.

## Decision

### D1 — Done stays done

**A closed work item is never reopened.** `done` is terminal, and so are `superseded` and
`declined`. A residue discovered after an item closes is **minted as a fresh item**.

The reason is that `done` is not a note to ourselves; it is **read by machines and by
history**. The release machinery, the ROADMAP render and every "what shipped" view resolve
against it, and some of those answers are already frozen into cut tags. If a closed row can
move back to open, `done` silently means *"done unless something turns up"* — and it means
that **retroactively**, for every item ever closed, including ones stamped into releases
nobody will re-cut. A ledger whose closed rows can move is one nobody can read backwards.
The alternative costs one extra row.

### D2 — The fresh item carries a citation back to the closed one, and it must RESOLVE

The citation is not decoration; it is the whole mechanism that keeps the two readable as one
thread. Minting is what D1 buys; what it **spends** is lineage — with reopening the history
is visible in the item's own status, and with minting it is visible only in the citation.
**An unminted residue with no citation is worse than either option**, which is why this is
mandatory rather than recommended.

Two forms. Which applies is decided by whether `source:` is already spent:

| Case | Carry the citation as |
|---|---|
| The fresh item has no other provenance | `source: work-items/WI-NNNN-<slug>.md` — the closed item's **repo-relative path** |
| `source:` already names a brief, journal or ADR the item was minted from | body prose that **names the closed id** and says what survived it |

The second row is forced, not a loophole: `source:` is single-valued, and
[ADR-0074](0074-inbox-is-a-mailbox-triage-mints-work-items.md) D2 gives it a specific job —
pointing at the brief an item was triaged from. An item minted from a brief that *also*
cleans up after a closed item has two provenances and one field. The brief wins the field,
because that is the join `inbox-check` computes; the closed id goes in the body. (WI-0050
independently flagged the same single-valued limit from the other direction.)

**A path, never a bare id, when it goes in the field.** `_triage_debt()` resolves `source:`
as a path relative to the store root and reports anything that does not resolve as a **dead
source** — a live line in the startup banner. A `source: WI-0296` would be permanently dead
there, so the naive reading of "cite the closed item" would have quietly manufactured
startup debt on every residue-minted item. The path form resolves, so the citation is checked
by machinery that already exists instead of by anyone remembering to look.

This extends `source:` past ADR-0074 D2's literal wording ("the brief"), along a line the
store had already taken by itself: values in the store today include `sessions/journal/…` and
`adr/0098-…` as well as briefs. The honest reading of the field is *the repo-relative artifact
this item came from*. The `inbox-check` join is unaffected — its reverse direction matches
`accepted/` briefs by basename, and a `WI-NNNN-*.md` filename cannot collide with a brief's.

### D3 — Whether a residue deserves an item at all is unchanged

This settles **where** a residue attaches, not **whether** it is worth attaching. That stays
ordinary judgment, and in particular it stays subject to the standing rule that a working
session records findings while items are minted in review. D1 does not license a session to
mint; it says what minting looks like **once the decision to mint has been made**.

The "WI-0210" case above is the standing reminder that the question is asked at all: for one
of the three residues the right answer was that nothing needed filing, because an open item
already carried it. WI-0234's was the same — WI-0316 was already carrying it.

### D4 — The rule is prospective; the reopens already in the record stand as history

Reopening has really happened here, it is documented, and this ADR does not rewrite it.
`adr/README.md` and WI-0097 record *"REOPENED session ~145 (2026-08-15) — this was marked
done and the capability does not exist… the **false-done class**"*;
[ADR-0098](0098-a-lane-repairs-main-and-clears-its-own-blocked-rebase.md) repeats it; ROADMAP
records WI-0006 reopened under ADR-0094.

Those stay exactly as they are. Restating history to match a rule adopted afterwards is the
same move D1 exists to forbid — a ledger edited to agree with today's policy is one nobody
can read backwards, which is the argument, not an exception to it. **The false-done class
those reopens belong to is real and D1 does not dissolve it**; it routes it differently. A
`done` that was never true is corrected by minting an item that says so and cites the false
close, which leaves *both* facts in the record — that it was claimed done, and that it was
not — where reopening leaves only the second.

## Alternatives Considered

**Reopen the closed item.** The intuitive move, with a genuine advantage this gives up:
lineage stays visible in the item's own status, with no second row and no citation to
maintain. It also has real precedent here (D4). Rejected because it makes `done`
retroactively conditional *everywhere*, including inside already-cut releases — a cost paid
by every past close to save one row on this one.

**Mint fresh with no citation.** Cheapest, and what happens by default when nobody writes a
rule down. Rejected outright: it produces orphan items whose motivation must be
reconstructed by git archaeology, which is the failure the store exists to prevent. Worse
than *either* real option, which is why D2 is mandatory.

**A `reopened` status or a `residue-of` field.** Models the relationship precisely. Rejected
as more machinery than the problem earns — a new field needs a writer, a validator, a render
and a migration, and [ADR-0073](0073-work-item-store.md) D4 already set the precedent of
refusing schema growth for relationships flat prose carries adequately. `source:` plus a body
line is the existing mechanism, already validated and already surfaced at startup.

**Enforce D1 with a refusal in `wi-status`.** The strongest form: make `--status open` on a
terminal item refuse and print the mint-fresh remedy, so the rule is met at the moment of
temptation rather than by reading. Rejected **here for scope, not on merit** — `sessionlib/`
ships byte-identical to every converged member (`push-substrate.py`'s `BYTE_IDENTICAL`), so
that refusal is a fleet rollout, and a lane does not open one. Recorded as owed, not declined;
see Consequences.

## Consequences

- The disposition of a post-close residue is answerable without re-deriving it, which is what
  WI-0343 existed to obtain.
- **The rule is doctrine, not enforcement.** `poga work status <id> --status open` still
  accepts a `done` item silently. Until a refusal ships, D1 holds by discipline and by this
  record. Per
  [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
  the **first violation of D1 after this ADR is the trigger** that converts it into code —
  and that guard is a fleet change when it comes, so its scheduling is the operator's.
- **The fleet-facing substrate is still silent on item finality.** `standard-source.md`'s
  work-item-store section — which generates `STANDARD.md`, `STANDARD-REFERENCE.md` and both
  bootstrap-kit copies, and so teaches every member how the store works — says nothing
  about whether `done` is terminal. Correcting it redistributes, so it is a finding for the operator
  rather than something a lane closes. Until then this ADR binds the federation only.
- `source:` now means, in practice and in this record, *the repo-relative artifact this item
  came from* — broader than ADR-0074 D2's "the brief", and in agreement with what the store
  already contains.
- A reader auditing closed items will find residue successors only by following citations,
  never by a status query. That is the cost named in D2 and accepted.

## Verification

- The dead-source hazard in D2 was **measured, not assumed**: `_triage_debt()` in
  `sessionlib/store.py` resolves `source:` with a plain filesystem existence check against the
  store root and appends anything that fails to the `broken` list the startup banner reports
  as *"N work item(s) with a dead source"*. A repo-relative work-item path satisfies it; a
  bare id does not.
- **D2's path rule has a live offender in the store proving it is not hypothetical.**
  `inbox-check` names `WI-0264 -> WI-0250 ambient-state sweep, session ~191` among its dead
  sources — a `source:` field holding a bare id and some prose instead of a path, dead from
  the day it was written and reported at every startup since. That is precisely the artifact
  the naive reading of *"`source:` citing the closed item"* would have produced on every
  residue, and it was found in the store rather than imagined.
- **The rule was then exercised end to end on the real case, not just reasoned about.**
  WI-0367 was minted under D1 with `source: work-items/WI-0296-…md`, and `inbox-check`
  afterwards still reports **3** dead sources, the same three as before, with WI-0367 absent
  from the list. The citation resolves.
- The reverse (`untracked`) direction was checked for collateral damage: it collects
  `Path(source).name` and compares only against `accepted/*.md`, so a work-item filename in
  the field cannot mark a brief as spuriously carried.
- The fleet scope in Alternatives was checked against `curate/push-substrate.py` rather than
  assumed from the directory layout: `sessionlib/*.py` is enumerated into `BYTE_IDENTICAL` at
  runtime by `federation_sessionlib_files()`, and `adr/` and `work-items/` appear in no
  manifest.
- D2's second row is not hypothetical — WI-0316 is the worked example, minted for WI-0234's
  remainder with `source:` spent on the consultant brief and WI-0234 named in its body.

## References

- [ADR-0073](0073-work-item-store.md) — the store, `status`, one file per item.
- [ADR-0074](0074-inbox-is-a-mailbox-triage-mints-work-items.md) — `source:` provenance and
  the point-don't-restate rule.
- [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) — drawn
  numbers, and why a spent one is never reused.
- WI-0343 — the item that raised the question and carries the original ruling note
  (session ~302, 2026-09-13).
- WI-0316 — the worked example of D2's second row, and the item that already carried
  WI-0234's residue.
- WI-0296 — the closed item whose residue is minted fresh under D1 with `source:` pointing
  back at it: D2's first row.
