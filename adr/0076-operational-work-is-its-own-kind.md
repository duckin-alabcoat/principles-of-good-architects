# ADR-0076: Operational work is its own kind — own numbers, a due date, and a result

**Status:** Accepted
**Date:** 2026-07-29 (session ~105; accepted session ~107 — the operator: *"1 accept"*, both amendments as written)
**Builds on:** [ADR-0073](0073-work-item-store.md) (the work-item store this extends), [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) (drawn ids + the land gate the new namespace mirrors), [ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md) (the spot-audit cadence — the prior art this generalizes)
**Deciders:** the operator (ruled that the three items which do not move the version number are test-and-operations work rather than development; that ops items get their own ops numbers and their own section instead of work-item numbers; and that not all of them recur — some are one-time tests, 2026-07-29); Federation Architect (design + the three amendments in D3/D4/D6)
**Reality:** Built — session ~109, suite 1002 green (D7's supersede lands with it; see the note under Consequences)

## Context

The 2026-07-29 versioning brief (withheld)
gave every work item an impact label — `fix` / `feature` / `breaking` — from which a
release's version bump is derived. Backfilling all 47 live items surfaced three that
fit none of the labels: **WI-0002** (a backup health check),
**WI-0008** (a restore drill), **WI-0009** (failure-injection game days).

They were parked as `fix` so they could not accidentally force a minor, and the gap was
reported rather than papered over. the operator's read: they are test and operations work rather than
development, they need not touch the version number, and the open question was whether
they should be treated differently in other ways too.

They should, and the version number is the least of it. **The property that matters is
that operational work recurs**, and three things follow from it:

- **`done` is the wrong end state.** Marking a recurring drill done is false — it will
  be owed again next cycle. Leaving it open is also false — it reads as stalled
  work. Whichever you pick, the item is permanently misfiled.
- **Staleness alarms point the wrong way.** A development item open for three months
  means nobody picked it up. An operational item open for three months may be exactly on
  schedule. Generic staleness nagging is wrong in *both* directions here: it pesters
  about a drill that is not due, and stays silent about an audit that lapsed.
- **The useful fact is a date, not a state.** "Drill #2 done" says nothing without
  *when*.

This is not a new idea in the federation, only an unconnected one. The monthly spot audit
([ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md)) is already tracked exactly
this way — `curate/metrics.py` mines the newest audit date and nags *"never run"* or
*"overdue"* off a cadence declared in `fleet-cadence.json`, and no work item is involved.
Canon argues the general case in
[`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code): a policy
that lives only in prose is a chore that silently lapses, so enforce it in code on a
schedule. A standing obligation parked in a list someone has to remember to read is that
same chore, one level up.

## Decision

### D1 — `kind: dev | ops` is a first-class field, not a fourth impact label

The obvious cheap move — add `ops` as a fourth `impact` value — is refused. `impact`
means *release impact*; operational work has none. Overloading the field so one of its
values means "this field does not apply" is the shape
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
names: a check whose "not applicable" branch reads as a pass does not merely fail to
detect a gap, it certifies one. `kind` and `impact` are orthogonal axes and stay so.

An `ops` item carries **no** `impact` and **no** `version`, and is exempt from the
[WI-0043](../work-items/) unlabelled-impact debt report — it is not unclassified, it is
out of scope for classification.

### D2 — Operational items get their own counter, `OPS-NNNN` — from **one** allocator

Ops ids live in `ops-items/OPS-NNNN-slug.md` and are drawn against their own `ops-alloc`
reservation namespace, separate from `wi-alloc`. Two arguments carried a separate counter
over reusing the WI sequence with only a `kind` field to tell them apart:

1. **Conversational ambiguity.** With one sequence, *"do item 8"* is unanswerable the
   moment both kinds share it. A prefixed id resolves it in the id itself.
2. **Allocation safety.** ADR-0069's finding is that a *shared* number namespace across
   concurrent writers is where collisions happen. A separate counter reduces contention
   rather than adding it.

**But a separate counter is not a separate allocator.** This is the part that changed
during the design conversation, and it is the more important half of D2.

Today there are **two** allocators — one for ADR numbers
([ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md), built
session 93) and one for WI numbers ([ADR-0073](0073-work-item-store.md), built session
~101, which states it was built "mirroring the ADR allocator exactly, including its
land-gate defense"). Mirroring was a defensible call at the time: copy proven safety code
rather than refactor it mid-build. The result is two independent implementations of the
same three-layer mechanism, measured at **51%** textual similarity in the land gate and
**40%** in the reserve step — same shape, written out twice.

A third copy is where that stops being untidy and becomes the risk ADR-0069 exists to
prevent. The land gate *is* the safety mechanism; three separately-maintained copies of it
will drift, and a fix applied to one and not the others is silent.

So the ops counter is added by **extracting a single allocator with a registry of
counters**, and the counters become data:

| counter | directory | filename | reservation namespace |
|---|---|---|---|
| `adr` | `adr/` | `NNNN-slug.md` | `adr` |
| `wi` | `work-items/` | `WI-NNNN-slug.md` | `wi-alloc` |
| `ops` | `ops-items/` | `OPS-NNNN-slug.md` | `ops-alloc` |

Everything that differs between the existing two is in that table. The mechanism — propose
a candidate from the landed files plus sibling lane tips plus held reservations, claim it
atomically against the reservation ledger, refuse at the land gate if it was not drawn or
is contested — is identical and becomes one implementation.

Two consequences beyond removing the duplication:

- **A new counter is gated by construction.** Today the land gate must be *told* about
  each namespace, so adding a fourth counter means remembering to wire it in. Registry-driven,
  the gate iterates the table and an ungated namespace cannot be shipped by forgetting a
  step, because there is no step to forget
  ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).
- **The canon claim becomes literally true.** [`numbers-are-drawn-never-picked`](../habits/master.md#numbers-are-drawn-never-picked)
  requires a number to come from "a single allocator." That is currently true in spirit and
  false in code. After the extraction it is true in code.

The extraction is a **prerequisite** of this ADR, not a follow-up — the point is to avoid
writing the third copy, which cannot be undone once other code depends on it. Filed as
**WI-0052**, and it lands first.

### D3 — The date model: cadence optional, `last-completed` stamped, `due` derived where it can be

Three fields, and the interaction between them is the whole point:

| Field | Who writes it | Meaning |
|---|---|---|
| `cadence` | Author, optional | `quarterly` / `monthly` / `annual` / … — absent means one-time |
| `last-completed` | Stamped when the item is run | The date the obligation was last satisfied |
| `due` | **Derived** when `cadence` is set; hand-set when it is not | When it is next owed |

**A recurring item's `due` is never typed by a human.** If it were, every cycle would
depend on someone remembering to push the date forward — which is precisely the
silently-lapsing chore this ADR exists to catch, reintroduced as its own bookkeeping.
`due = last-completed + cadence`, computed.

the operator's point that not every one of these recurs — some are one-time tests — is
handled by the same three fields rather than a second shape: **no cadence** means `due`
is authored directly and the item is a one-off. One model, two behaviours, and the
recurring case costs nothing to maintain.

### D4 — An operational item carries a **result**, not only a completion

`last-result: pass | fail | findings`.

This is the axis development work does not have. A feature either shipped or did not; a
*check* has a completion **and** an answer, and the answer is the entire reason it ran.
Without it, a drill that passed and a drill that discovered the system unrecoverable both
render as the same bare completion date.

That is not hypothetical. The first restore drill ran clean **as an exercise** and
found that the software rebuilt quickly while the data did not come back with it —
the finding that produced WI-0005, WI-0006 and WI-0007. A store that cannot tell that
drill apart from an uneventful one is recording the wrong fact about the most valuable
thing it did. `findings` is distinct from `fail` deliberately: that drill did not fail, it
succeeded at telling us something bad.

### D5 — Recurring items never close; one-time items do

An item **with** a cadence has no terminal state — running it stamps `last-completed` and
`last-result`, recomputes `due`, and the item stays open, because a standing obligation is
never finished. An item **without** a cadence closes normally when it is done.

`status: done` on a recurring obligation is therefore not merely discouraged but invalid,
and the validator rejects it.

### D6 — Identity splits; visibility does not — one chart, two namespaces

`kind` becomes a column in the chart, and both namespaces render into the **single**
view. Ops work does not get its own surface.

This is the load-bearing constraint on D2. A standing obligation that lives somewhere the
operator has to remember to visit is exactly how a recurring drill quietly slips a
cycle — the failure mode this ADR is built to prevent, reintroduced by its own
solution. It also keeps faith with the session-~103 ruling (one list, and nothing is ever split
off it into a second list): that ruling governs
**display partitioning**, and this ADR splits **identity**. Ops items are a different kind
of object with different fields and a different lifecycle, which is not the same claim as
showing the operator three filtered views of one thing.

`ops-items/` additionally renders as its own section in `ROADMAP.md` — an overdue
obligation belongs in the human-facing outcome view, where it is visible rather than
merely stored.

### D7 — The three existing items are superseded, never renumbered

WI-0002, WI-0008 and WI-0009 are already WI-numbered. [ADR-0073](0073-work-item-store.md)
makes an id a permanent handle — *once drawn it always resolves* — a rule that exists
because WI-0022 was once deleted and left both a hole in the sequence and a dangling
reference pointing at nothing.

So each becomes `status: superseded` with notes naming its new `OPS-NNNN`, and the WI
number remains forever as a signpost. **Three permanent tombstones is the price**, and it
is paid now rather than later precisely because it grows with every operational item filed
under a WI number in the meantime.

## Consequences

**Good.**

- A lapsed obligation becomes *detectable* — `due` in the past is a machine-checkable
  fact, so the nagging comes from code rather than from the operator's memory
  ([`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code),
  [P15](../principles/master.md#p15--code-for-mechanism-not-judgment)).
- Staleness heuristics stop producing false readings in both directions, because the two
  kinds are no longer measured by one rule.
- The most valuable output of an operational check — what it *found* — is recorded where
  it can be read, rather than surviving only in a session journal.
- The spot-audit cadence ([ADR-0053](0053-mined-ritual-conformance-and-spot-audit.md)),
  currently special-cased inside `metrics.py`, becomes one instance of a general model and
  can be folded in rather than maintained separately
  ([P16](../principles/master.md#p16--avoid-duplication)).

**Bad, and accepted.**

- **A refactor of working safety code, to add three items.** D2's extraction touches the
  ADR and WI allocators — code that currently works and guards against a failure that has
  actually happened (the session-93 five-way ADR collision). Refactoring a live land gate
  to serve a population of three is a real risk, taken deliberately because the
  alternative is a third hand-maintained copy of it. The 886-test suite and the fact that
  both existing counters keep their behaviour are the mitigations; neither is a guarantee.
- **The extraction blocks the feature.** Because it is a prerequisite rather than a
  follow-up (D2), the ops kind cannot ship until the allocator work lands. That is the
  correct order and it is still a delay.
- **Three permanent tombstones** in the WI sequence (D7).
- **A second `poga work`-shaped surface** to learn and to keep in step. Mitigated by D6
  (one chart) but not eliminated — `show`, `new` and `status` all need ops equivalents.
- **`cadence` needs a vocabulary**, and a wrong one calcifies. Deliberately starting
  small (`quarterly` / `monthly` / `annual`) rather than inventing a scheduling grammar
  no one has asked for.

## Build notes (session ~109)

Three things the build settled that the decision left open, recorded because each was a
judgment rather than a transcription.

- **`kind` is real but DERIVED, not stored.** D1 names it a first-class field; D2 then
  gives ops its own counter and directory, which makes a literal `kind:` line redundant
  with the id that already carries it — a second copy of one fact, free to drift
  ([P16](../principles/master.md#p16--avoid-duplication)). So `kind` is computed from the
  namespace and rendered as a column everywhere items are shown. D1's argument survives
  intact: its point is that kind is orthogonal to `impact`, not that it must be stored twice.
- **The ROADMAP's third marker pair is OPTIONAL.** Requiring it would break every member
  whose `ROADMAP.md` predates this ADR, on their next render, for a section they have no
  items to fill. `wi-render` emits the ops section when a third pair is present and keeps
  its existing two-pair behaviour otherwise; a member opts in by adding the markers.
- **D7's supersede is sequenced AFTER the code lands, not before.** Executing it first
  put the trunk's store into a state the trunk's own validator rejected: the
  superseded-needs-a-successor check matched `WI-\d{4}` only, so a WI item naming an
  `OPS-NNNN` successor read as naming none. The pattern now accepts either — but the
  general rule the failure teaches is broader and worth stating: **a store change that
  depends on new validator behaviour must land the code first**, because `poga work`
  deliberately drives the trunk's store with the *trunk's* harness, not the lane's.

## Alternatives considered

**A fourth `impact` value, `ops`.** Cheapest possible change — one field, no namespace, no
migration. Rejected under D1: it makes a release-impact field carry a non-release concept,
and the "not applicable" value would sit in exactly the branch that reads as a pass.

**One counter plus a `kind` field.** Everything in this ADR except the separate sequence,
at a fraction of the build cost — no extraction, no new counter, no tombstones. Rejected
on D2's argument 1: the id stops telling you what kind of thing it is, which is the
ambiguity the split exists to remove.

**One counter, with the prefix rendered from `kind`.** A middle option raised during the
design conversation: a single sequence, where `WI-0002` becoming an ops item simply
renders as `OPS-0002` — same number, same sequence, so no tombstones and no second
counter, while still reading unambiguously. Genuinely attractive, and it dissolves D7
entirely. Rejected by the operator, who ruled for two counters. The cost of that choice is D7's three tombstones and one extra registry
entry; the benefit is that the two kinds cannot contend for numbers at all, and an id's
prefix is then a fact about which sequence issued it rather than a rendering of a mutable
field. Recorded because it was a close call, not an obvious one.

**Move operational work out of the store entirely, into `fleet-cadence.json` beside the
spot audits.** Conceptually cleanest — the cadence machinery already exists and already
works. Rejected because it splits the one list the operator actually reads: an obligation
tracked in a config file consumed by a metrics script is not visible when asking *"what do
I owe?"*, which is the question the store exists to answer.

**Do nothing and leave them labelled `fix`.** The status quo after the versioning
backfill. Rejected because it is a silent mislabel: it makes operational work look like
repair work, and it leaves recurrence unmodelled, which is the actual problem.
