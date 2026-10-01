# ADR-0084: A major ships when its promise is complete

**Status:** Accepted
**Date:** 2026-07-31
**Deciders:** the operator (ruled that each promised item ships at its own size and the major rolls when the last one ships; that fixes bump the patch number and features the minor; then confirmed that a major means the promise is complete, 2026-07-31), Federation Architect (the early-ship failure case and the accounting gap).
**Amends:** [ADR-0081](0081-a-major-is-declared-not-derived.md) D1 (never derived) and [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) D5 (what slipped).

## Context

A ruled-in item finished early and there was no honest thing to do with it.

WI-0027 is one of the nine items promised for 7.0.0. It closed on 2026-07-31, eight
items ahead of the rest. `release-cut --dry-run` offered to ship it as `6.1.1` — which is
correct by the letter of [ADR-0081](0081-a-major-is-declared-not-derived.md) (the harvest
takes whatever is closed and unstamped) and wrong in effect: stamping it `6.1.1` removes
it from the harvest that eventually ships 7.0.0, so **7.0.0 would record eight items
having promised nine, with nothing anywhere saying where the ninth went.**

The first proposal was to *hold the item back* — forbid a smaller release from taking a
ruled-in item. the operator asked what industry practice was, which is what killed it: milestones
everywhere are re-scoped freely, a finished fix ships in the next release that goes out,
and holding completed work to keep a plan tidy is inventory on a shelf. The proposal
protected a document at the cost of the work.

the operator's model inverts which half is fixed:

> A version promising four items, starting from 4.0.0, goes to 4.1.0 when one of them
> ships, and so on; when the last one ships, it rolls to 5.0.0.

Nothing waits. Each item ships when it is done, at its own size. The major is not a
container the items sit in until it is opened — it is the moment the **last** one lands.

## Decision

**D1 — A major ships when every one of its ruled-in items has shipped.** The manifest is
still declared up front by a human naming a milestone, its theme, and its set — all of
[ADR-0081](0081-a-major-is-declared-not-derived.md) D1's reasoning about significance
being un-computable is untouched. What is derived is only the **moment**: code cannot
judge whether a chapter matters, but it can certainly count to nine. Declaring the
promise stays judgment; noticing it was kept is mechanism
([P15](../principles/master.md#p15--code-for-mechanism-not-judgment) read in both
directions, as ADR-0081 asked).

**D2 — A ruled-in item never waits for its milestone.** It ships in the next ordinary
release at its own size. Being promised to 7.0.0 confers no hold, no reservation, and no
special handling at the moment it lands.

**D3 — The small numbers are unchanged and are about size, not membership.**
`impact: feature` → minor; `impact: fix` → patch. An unlabelled item still refuses the
whole computation. A batch takes the largest size in it.

**D4 — The major's record accounts for EVERY item it promised, naming where each one
shipped.** A `## Previously shipped in this promise` section lists each ruled-in item that
went out under an earlier number, with that number. This is the load-bearing half: without
it D2 silently shrinks every milestone, since the major's own harvest can only contain
whatever happened to be unstamped on the day it completed. The promise was nine items and
the record says nine items — some of them dated earlier.

**D5 — `--ship-major` is removed. Dropping an item is an amendment, not a flag.** A major
that could be shipped on demand could be declared complete while incomplete, which is the
one thing D1 exists to prevent. To ship without an item, **amend the manifest** — loudly,
dated, with a reason, original kept verbatim — which removes it from the set and thereby
completes the promise. The escape hatch still exists; it just leaves a record.

**D6 — `## Slipped` becomes structurally empty for a major, and that is the point.** Under
D1 a major cannot ship with an item outstanding, so nothing can slip out of one silently.
[ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) D5's honesty
requirement is not dropped — it **moves to the amendment log**, which is strictly
stronger: a slipped line required no explanation, an amendment requires a written reason
and preserves what it changed.

## Alternatives Considered

**Hold ruled-in items back until the major ships.** The first proposal, rejected on
the operator's industry question. It delays finished, tested work to preserve the tidiness of a
plan; it also makes the milestone's arrival *less* visible, because the number stops
moving during exactly the period the work is landing.

**Let the major stay a manual flag and just fix the accounting.** Rejected: it leaves the
number's arrival dependent on someone remembering to pass `--ship-major` on the right
harvest. The promise-complete condition is checkable, so checking it is free; a ritual
that depends on memory is one that silently stops happening
([`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code) applied to
a release ritual).

**Roll the major only when the last item is itself significant.** Rejected as
unimplementable and against the model. Six of 7.0.0's nine items are `fix`, so the last
one is more likely than not to be small — and under this rule a promise could be fully
kept and still never produce its version.

**Drop the third number and run 6.1, 6.2, 6.3.** Raised and rejected in conversation:
every tool that parses a version expects three fields, and the ability to say *"that
release needed repairing"* is worth a digit.

## Consequences

- **Today's two finished items split by size, not by membership.** WI-0027 (`fix`,
  ruled in) and WI-0066 (`fix`, off-manifest) both ship as **6.1.1**. WI-0027's
  ruled-in status changes nothing now, and is remembered by D4 later.
- **7.0.0 arrives on its own.** Eight items remain; as each lands the number moves, and
  whichever release contains the last of the nine *is* 7.0.0 — even if that item is a
  one-line fix.
- **Progress becomes visible in the number.** `6.1.1 → 6.2.0 → 6.2.1 …` draining toward
  7.0.0 replaces a static number plus a file someone has to open.
- **The `impact` label keeps its job** but loses a second one it was quietly doing —
  it no longer has any bearing on whether the major fires.
- **§10 of the role doc is rewritten** to state this, which also clears three stale claims
  it carried (the version's home "once WI-0044 lands", which landed; the next major named
  as *"members stop inheriting the federation's shape"*, which is WI-0062, the version
  *after* 7.0.0; and a `6.0.0` header after 6.1.0 shipped). That is the remainder of
  WI-0045.
- **The 2026-08-10 target now means something checkable.** "Is the promise complete?" has
  a machine answer on any given day, where "should we declare victory?" did not.

## References

- [ADR-0081](0081-a-major-is-declared-not-derived.md) — D1, amended here: the *number and
  theme* stay declared; the *moment* is derived from completion.
- [ADR-0078](0078-version-sets-are-planned-releases-are-harvested.md) — D1 (harvest), D5
  (slippage, relocated to amendments), D7 (the original target kept beside the ship date).
- [ADR-0080](0080-the-federation-declares-its-project-version-at-6-0-0.md) — the project
  version this numbers.
- [P15](../principles/master.md#p15--code-for-mechanism-not-judgment) — judgment declares
  the promise; mechanism notices it was kept.
- WI-0027 (the item that forced this), WI-0045 (§10), WI-0066.
