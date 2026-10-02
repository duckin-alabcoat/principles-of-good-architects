# ADR-0078: Version sets are planned; releases are harvested — and `next` is derived from set membership

**Status:** Accepted
**Date:** 2026-07-29 (session ~109)
**Builds on:** [ADR-0073](0073-work-item-store.md) (the work-item store and its `section` field this changes), the 2026-07-29 versioning brief (withheld) (whose minors-are-harvested rule this reconciles rather than reverses), [ADR-0076](0076-operational-work-is-its-own-kind.md) (the sibling kind, which carries no version at all)
**Deciders:** the operator (ruled that work is taken as a set of items making up the next major/minor version, and that those items are what `next` means; that occasional discussions to reorder version sets are acceptable; and, for D6, that every ship is followed by a discussion of the next version's features and capabilities, from which the Architect determines the required work items, 2026-07-29); Federation Architect (the plan/harvest split, the release-record consequence in D5, and the reviewable-derivation guard in D6)
**Reality:** Not-built — `releases/` does not exist yet (WI-0044); this ADR is its design input

## Context

`section` is a hand-set field with two values, `next` and `backlog`. It has stopped
discriminating: **24 of 41 open items read `next`**. When two-thirds of a backlog is
"next", the field carries no information, and the chart column that renders it is noise —
which is how it was noticed, as a display complaint (too many items all reading `next`)
rather than as the data problem it is.

The deeper failure is that nothing ever *said* why an item was next. `next` meant "someone
typed next," so the list answered "what did we mark?" instead of "what are we doing?".
the operator's framing names the missing concept directly: work is picked up because it belongs to
the set that gets us to the next version.

**This collides with a rule already accepted.** The versioning brief states that only
**majors** are scope-declared, and that:

> MINORS are harvested, never planned — whatever landed since the last release.

with the reason given as *"declared sets get held hostage by their slowest item."* Taken
literally, planning a minor's contents is the thing that rule exists to forbid.

The collision is real but shallow, and it comes from the brief treating one word — the
"set" — as both a plan and a promise.

## Decision

### D1 — Split the plan from the harvest

They are different objects with different lifecycles, and conflating them is what made
the rule look like a prohibition:

| | **The version set** | **The release** |
|---|---|---|
| What it is | intent — what we mean to finish | fact — what actually shipped |
| When it exists | while work is in flight | at the cut |
| Mutable? | yes, freely (add, drop, reorder) | no, it is a record |
| Gates the cut? | **never** | n/a |

A set is declared and reorderable. A release is harvested from whatever landed. Because
the set never gates the cut, **the hostage failure the brief warned about cannot occur** —
an item that slips does not hold the release, it moves to the next set. The brief's rule
survives intact in the part that matters (the *release* is harvested); what changes is
that planning the *set* is no longer mistaken for pre-declaring the release.

### D2 — The version number stays DERIVED, and is computed once, at the cut

Unchanged from the brief, and restated because the natural reading of "this item is a
minor bump" is wrong in a way that would be expensive.

An item carries `impact: fix | feature | breaking`. **Closing an item changes no version
number.** At the cut, the bump is `max(impact)` over the items that actually landed — any
`breaking` → major, else any `feature` → minor, else patch — via the existing
`_wi_derive_bump`, which refuses to compute at all if any item is unlabelled rather than
guessing ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

So a `feature` item *makes the release a minor* only in the sense that it is the max so
far; a `breaking` item landing later overrides it. **The version never churns while work
is in flight**, which is what makes a set safe to reorder — reordering changes the plan,
not any number anyone has seen.

### D3 — A set is a member list, not a range

Sets grow. the operator's own worked example is the proof: finishing one item spawns three more
that belong to the same set. A range (`WI-0010`–`WI-0019`) cannot express that, and a set
that cannot absorb discovered work would be abandoned the first time work was discovered —
which is always.

So a set is an explicit list of ids. This is also the concrete reason a **minor** set
cannot be a closed manifest: closure is a promise, and there is no one to promise a minor
to.

### D4 — `section` becomes DERIVED from set membership

`next` = a member of the in-flight version set. `backlog` = everything else.

This is a schema change to [ADR-0073](0073-work-item-store.md): `section` stops being
authored and `wi-status --section` is retired in favour of assigning an item to a set.
Two properties fall out that are worth more than the field ever was:

- **The re-triage problem dissolves.** Today, fixing "24 items say next" means 24
  judgment calls. Derived, it is one decision — what is in the set — and every item's
  section follows.
- **Nothing is stranded by a cut.** When a version cuts with items unfinished, no item
  is left holding a stale `next` that someone must remember to clear — the field follows
  membership, so it is correct the moment the new set is declared.

> **Amended in the same session (see D6).** This decision first said rollover was
> *automatic* — unfinished items roll into the next set and stay `next` with nobody
> re-marking them — justified as avoiding a lapsing chore. That was wrong, and the flaw
> is that it makes the set **harvest and re-absorb**: version two begins pre-loaded with
> version one's leftovers, so a ship date creates strong pressure to cut the release (the
> easy decision) and almost none to shrink the set (the hard one), and the 24-item problem
> returns wearing a version number. Unfinished items now drop to `backlog` at the cut and
> are re-derived like anything else. They get no presumptive claim on the next version.

Majors keep the stronger form the brief already gives them: a named theme plus a **closed**
manifest, frozen once declared, amendments allowed but loud. A major is a promise made
outward, so it is the one set whose membership is a commitment rather than a plan.

### D5 — A release record must name what SLIPPED, not only what shipped

The amendment this ADR adds beyond the operator's framing.

If a set declares ten items and the release ships eight, a record listing the eight is
literally true and quietly false: it rewrites the plan to match the outcome, so *"we hit
the plan"* becomes true by omission at every single release, and the one number worth
watching — how much a set routinely overruns — becomes unrecoverable.

So a release record carries the shipped ids **and** the deferred ids with the set they
rolled into. Deferral is normal and expected under D1; what is not acceptable is that it
leave no trace. Same discipline as
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) applied
to a plan: a partial result must never render as a complete one.

### D6 — Every ship triggers a capability discussion, and the set is DERIVED from it

the operator ruled, 2026-07-29, that every time a version ships there is a discussion between him
and the Architect or the consultant about what constitutes the next version(s); that
discussion is about features and capabilities, and the Architect then determines which work
items are required to deliver them.

This makes a version set a **derived** artifact rather than an assembled one, and it is
what the earlier draft of D4 got wrong. A set is not "the leftovers plus some new ids." It
is the answer to *what should the next version do?*, translated into the work that
delivers it. Leftovers are re-derived like everything else — if an unfinished item serves
the next version's capabilities it is ruled in, and if it does not, it goes to backlog
however close to done it was. Sunk cost gets no vote.

**The division of labour is the load-bearing part**, and it matches the non-delegable rule
the versioning brief already carries (*"the theme sentence and the rule-in/rule-out
judgment"* — an agent-written theme produces majors like "various improvements", a version
with no promise in it):

| Who | Decides |
|---|---|
| the operator, with the Architect or a consultant | **What capabilities the next version delivers.** Features and outcomes, never work items. |
| The Architect | **Which work items are required to deliver them.** A derivation, reported back with its reasoning. |

**The derivation must be reviewable, or it becomes the place scope hides.** If the operator names
three capabilities and the Architect returns forty items, the capability discussion was
theatre — the scope decision simply moved to a stage the operator cannot see. So the set records
the mapping **capability → items**, not a flat list of ids, and the Architect reports the
cost per capability. That is what lets the operator see that capability B costs eighteen items and
cut it *as a capability*, which is the level he is deciding at.

**A patch does not trigger it.** A patch carries no new behaviour by definition, so there
is no capability to discuss; the discussion is owed at minor and major.

**"Next version(s)" is plural, but only one set is in flight.** The discussion may plan a
sequence — v2 and v3 — and future sets are real and declarable. Only the in-flight set
derives `next` (D4); declared-but-future sets render as roadmap. This is where "everything
else becomes roadmap/future version" gets its structure: an item can belong to a named
future version without being `next`.

**The trigger needs a machine half, or it lapses.** A ritual that lives only in prose is a
chore that silently stops happening
([`retention-enforced-by-code`](../habits/master.md#retention-enforced-by-code)) — and this
one runs exactly when everyone is most relieved to be done. The release step records that
the next-version discussion is **owed**, and the startup surface reports an undeclared next
version until it happens. Deliberately *surfaced*, not *blocking*: refusing to complete a
release until the next one is planned would hold shipping hostage to planning, which is the
same hostage failure D1 exists to prevent, one level up.

### D7 — A version carries a ship target, and the record keeps the ORIGINAL one

the operator ruled, 2026-07-29, that versions carry ship target dates, so that the date puts live
pruning pressure on the planning discussion.

The store has had no time dimension at all, which is a strange gap in a system whose canon
carries both [P11](../principles/master.md#p11--ship-working-system-on-time) (ship a working
system on time) and [P12](../principles/master.md#p12--manage-scope-for-schedule) (manage
scope to keep the schedule). A set with no date is a wish; the date is what the D6
capability discussion pushes against.

The date belongs on the **version**, not on items. Per-item estimates rot, are never
believed, and multiply the maintenance surface by the size of the set; one date on the set
is a single commitment about a whole.

**The release record keeps the original target beside the actual ship date.** A target that
can be quietly moved as it approaches creates no pressure at all — every version then ships
"on time" by retroactive redefinition, and the one number worth watching (how far a set
routinely overruns) becomes unrecoverable. Slipping a date is fine and expected; slipping it
*silently* is what this forbids. Exactly the D5 discipline applied to schedule rather than
scope: a partial result must never render as a complete one.

## Consequences

**Good.**

- `next` becomes an answer to "what are we doing and why", not "what did someone type".
- The chart's `SECTION` column starts discriminating again, because membership is
  meaningful where a hand-set flag was not.
- Reordering a set is a cheap, explicit conversation — the operator named this as acceptable, and
  D2 is what keeps it cheap (no number moves when a set is re-cut).
- Plan-versus-actual becomes measurable for the first time (D5).

**Bad, and accepted.**

- **A schema change to a store with 58 live items.** `section` must be backfilled from
  set membership, and until the first set exists there is no membership to derive from —
  see the sequencing note below.
- **A ritual with real cost.** D6 puts a planning conversation between the operator and the
  Architect (or a consultant) after every minor and major. That is deliberate — it is the
  only place scope is genuinely decided — but it is not free, and a release now carries an
  owed obligation as well as a record.
- **Set size is pressured, not bounded.** D6's re-derivation removes the drift mechanism
  (leftovers no longer accumulate silently), and a ship target gives the discussion
  something to push against. But nothing *forces* a set to be small: a capability
  discussion can still rule in eighteen items. The capability→items mapping makes that
  visible at the moment of decision, which is the most a mechanism can honestly do here —
  claiming it prevents bloat would be the same over-claim D5 warns about.
- **Retiring `wi-status --section`** breaks a verb that scripts and habits use.
- **`ops` items sit outside all of this.** They carry no impact and no version
  ([ADR-0076](0076-operational-work-is-its-own-kind.md) D1), so they are in no set and
  their `section` is empty rather than derived — a third case the render must handle
  rather than a uniform rule.

**Sequencing — this ADR cannot be built first.** There is nowhere for a set to live until
`releases/` exists (WI-0044), so the order is: stand up `releases/` including the set
manifest format → declare the federation's own current set → derive `section` from it →
retire `wi-status --section`. Attempting D4 before WI-0044 would mean deriving a field
from a store that does not exist.

## Alternatives considered

**Reverse the brief and plan minors outright.** The straightforward reading of the operator's
request. Rejected because it discards a rule that was adopted for a stated reason, when
the reason (hostage-taking) is fully addressed by D1's split — the set can be planned
precisely *because* it never gates the cut. Reversing would also lose the property that
makes reordering safe.

**Keep `section` authored and just re-triage the 24 items.** Cheapest possible fix, no
schema change. Rejected: it treats the symptom. The field would drift back to
all-`next` within weeks because nothing about the mechanism changed, and it leaves `next`
still meaning "someone typed next".

**Make `section` derived from a priority number instead of a version set.** Ordering
without versions — simpler, no dependency on `releases/`. Rejected because it answers
"what is most urgent" and not "why are we doing this now", and the operator's ask is explicitly
about tying work to the next version bump. It also reintroduces a hand-maintained field,
just numeric.

**Let a set gate its release (ship only when the manifest is complete).** The strict
reading of a declared set. Rejected — this IS the hostage failure, and it is the reason
the brief forbade planned minors in the first place. Majors come closest, but even a
major's manifest is a promise about *scope*, not a bar on cutting.
