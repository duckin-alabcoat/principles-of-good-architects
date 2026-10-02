# ADR-0081: A major is declared, not derived — `breaking` splits into size and migration

**Status:** Accepted
**Date:** 2026-07-30
**Deciders:** the operator (ruled that `breaking` is the wrong trigger — a major fundamentally alters the system, which may break it or may only add capabilities — then approved building it, 2026-07-30), Federation Architect (the two-axis split and the failure cases).
**Amends:** [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) D2 (the derived bump) and [ADR-0073](0073-work-item-store.md) (the item schema). **Resolves** [ADR-0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) D7, which was Proposed.

## Context

[ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) D2 made the release
number **derived**: each work item carries `impact: fix | feature | breaking`, and at the
cut the bump is `max(impact)` — any `breaking` → major, else any `feature` → minor, else
patch. The point was to replace one expensive argument per release with one cheap
question per item.

[ADR-0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) then declared
the project version at `6.0.0` and, on the operator's rule that breakage-and-repair is a *patch*,
made a major a **named milestone**. That left two accepted decisions disagreeing about
what produces a major, recorded as ADR-0080 D7 (Proposed) rather than resolved in code.

The disagreement was framed there as an edge case — a lone breaking item shipping outside
a chapter. the operator's own description of the process made it worse and then his critique of
the label dissolved the whole framing:

> A milestone is named, its work items identified and worked towards, and a date is
> picked; as the date approaches, features or work items may be cut from the milestone;
> once the remaining items are done, it ships and a version is derived.

That process is [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md)
restated almost exactly (D6 capability discussion, D7 ship target, D1 mutable set, D5
record what slipped, D2 derive at the cut) — but it produces a **concrete** failure
against D2, not an edge case. Cutting items as the date approaches is what the date is
*for*; if the cuts happen to remove the last `breaking` item, the derivation quietly
returns **minor** for a release already promised outward as a major.

Then the deeper objection, which is the one this ADR is really about:

> A major change fundamentally alters the system. That may break it, or it may only add
> new capabilities on top.

**A major is about significance, and `breaking` measures consequence.** They are
different things, and the label was measuring the wrong one. Our own history is the
proof: [ADR-0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) recorded
6.0.0 (lanes become fleet substrate) as *"thinner than the rows above it"* because only
two of its ADRs broke anything — when what actually happened is that every member gained
a whole capability it had never had. That was a fundamental alteration which broke
almost nothing, and the breakage axis could not see it. The label did not just risk a
wrong number in future; it had already produced a wrong judgment about our own past.

## Decision

**D1 — A major is DECLARED by naming a milestone. It is never derived.** Significance is
not a property any per-item label carries, and `max()` over labels cannot compute it.
This is [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) read in the
direction people forget: mechanism belongs in code, and **judgment does not**. Naming a
major is judgment, so code gets no vote. `_wi_derive_bump` can return `"minor"` or
`"patch"` and nothing else — enforced by test, not by convention.

**D2 — `impact` becomes a two-value judgment about SIZE: `fix | feature`.** Did this add
a capability, or repair something? The derived small-number bump is unchanged in shape:
any `feature` → minor, else patch, and an unlabelled item still **refuses** the whole
computation rather than being rounded in either direction
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

**D3 — `migration: yes | no` is a new, orthogonal field carrying the useful half of what
`breaking` conflated.** *Does shipping this force other members to edit their own files?*
It feeds **no version number**. It exists so the work is visible before the ship rather
than discovered after it, and so a release record can say how many repos need editing.

The two axes come apart in both directions, which is why one field could not carry them:

| | | |
|---|---|---|
| Moving a file to fix a small bug | `fix` | `migration: yes` |
| Adding lanes to the whole fleet (our 6.0.0) | `feature` | `migration: no` |

**D4 — Absent `migration` means "no migration required" — two states, not three.** The
usual objection (never fold *unknown* into *no*) is aimed at **detectors** reading
someone else's subject; this is authored data, set by the same hand that sets `impact`,
and a mandatory third judgment on every item buys a nag nobody asked for. Revisit if a
migration is ever missed because nobody was prompted. The field is written to the item
file only when set, so the ~50 items with nothing to say gain no line.

**D5 — The seven items labelled `breaking` are re-labelled, not grandfathered**, and
`wi-check` **rejects** the retired value by name rather than printing a bare "not one of":
an operator hitting it is holding a label that used to be correct, so the error says where
both halves went. Six became `feature` + `migration: yes`; **WI-0010** (canon
consolidation) became `feature` + `migration: no` — canon is generated and injected, so no
member edits anything. That single row is the split earning its keep: under the old schema
it was indistinguishable from the other six.

## Alternatives Considered

**Keep `breaking` and just stop mapping it to major.** Rejected: the word would still say
"this is the big one" while meaning "members must edit files", and the next reader would
re-derive the old mapping from the name. the operator's objection was to the *choice of concept*,
not only to its effect.

**Rename `breaking` to `migration` as a single three-value field
(`fix | feature | migration`).** Tempting and wrong — it keeps the two axes fused in one
slot, so a migration-forcing *repair* has no expressible label. The WI-0010 row above
would still be unrepresentable.

**Let a major be derived from something else — set size, item count, a `major: yes`
flag.** Rejected: all three are proxies for significance, and a proxy that can be gamed by
adding items is worse than an honest human declaration. A `major: yes` flag is just
declaring it with extra steps, in the one place (per item) where the decision does not
belong.

**Leave ADR-0080 D7 open and decide at `releases/` build time.** Rejected: the derivation
already exists in code (`_wi_derive_bump`, with tests) and would have been wired to a
caller during that build. Deciding after the wiring is how the wrong rule ships.

## Consequences

- **The seven-item 7.0.0 set is unaffected in substance** — it is still the same seven
  items and still a major, because the major was named (*"Members stop inheriting the
  federation's shape"*), not computed. What changed is that its majorness no longer
  depends on which items survive the cut.
- **`releases/` (WI-0044) can now be built once.** It wires `_wi_derive_bump` for the small
  numbers, takes the major from the declared milestone, and reports `migration: yes` items
  as a deployment section of the release record.
- **ADR-0080's "6.0.0 is thin" note is superseded in substance.** It is left in place —
  ADRs are immutable — but it was an artifact of the retired axis, and this ADR is the
  correction of record.
- **A schema change landed from a lane, and the front door could not carry it.**
  `poga work` deliberately drives the store with the *trunk's* harness, so the new
  `--migration` flag did not exist there; the relabelling had to run the lane's own
  `session.py` against a store synced from the trunk. That is WI-0056's gap with a sharper
  edge — a lane changing the store's *schema* has no sanctioned path — and is recorded
  there rather than worked around silently again.
- **Members are unaffected.** The work-item store is still federation-only (WI-0055 carries
  fleet redistribution), so no member has labels to migrate. Doing this *before* the store
  ships to the other systems is the cheap moment; after would have been one migration per member.

## References

- [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) — D2, the derived
  bump this amends; D1/D5/D6/D7 are untouched and the operator's described process matches them.
- [ADR-0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) — D7, Proposed
  there, resolved here.
- [ADR-0073](0073-work-item-store.md) — the item schema this adds a field to.
- [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) — judgment does not
  belong in code; the direction of the principle this decision turns on.
- WI-0044 (`releases/`), WI-0045 (project version), WI-0056 (a lane's store verbs).
