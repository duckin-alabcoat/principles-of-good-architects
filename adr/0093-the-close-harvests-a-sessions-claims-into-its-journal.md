# ADR-0093: The close harvests a session's claims into its journal

**Status:** Accepted
**Date:** 2026-08-14
**Deciders:** the operator (observed that a lot of work was getting done without reference to work items — the items had gone too quiet, session ~109; the ADR-first direction for this mechanism, session ~140), Federation Architect (the mechanism below)
**Extends:** [ADR-0073](0073-work-item-store.md) (the store), [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (the journal is the durable per-session record), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (claims live in the coordination store), ADR-0092 (withheld; it closed this item's display half)
**Work items:** WI-0058 (its remaining half); depends on WI-0123 (the claim-identity defect below)

## Context

An earlier decision closed the first of
WI-0058's three symptoms — the list rendered once at startup and was then invisible for
the rest of the session. POGA now publishes the list as a live feed, and any optional
consumer can show it continuously. Two remain, and the item
is explicit that they are not the same kind of problem: *"still NOT YET DESIGNED — do not
build a nagger."*

**Symptom 3 is the one with a mechanism behind it, and it is not a visibility problem.**
The binding between a session and the work it claimed already exists, in the right shape,
recorded automatically: a claim record carries the owning `journal`, the branch, the
machine, the runtime and the claim time, and every way a hold ends — `released`,
`reclaimed`, `teardown` — leaves a tombstone carrying the same fields plus why it ended
(WI-0074). Nothing needs to be invented to know what a session worked on.

The defect is that **all of it is ephemeral**. Claims and tombstones both carry
`CLAIM_TTL_SECONDS` (8 hours) and are pruned by `_coord_reap` on the same pass. The
durable artifact — the journal, committed to git, compiled into the handoff, mined by
`curate/metrics.py` — never learns any of it. So *"what did this session do?"* is
answerable for eight hours and then never again.

That is precisely why the original complaint landed the way it did. WI-0058's sharpest
line is that session ~109's single largest deliverable had no item at all, and the store's
answer to *"what did ~109 do"* was *"filed three, closed one."* The reconstruction was
attempted after the evidence had already expired. The store did not fail to record the
work; the record was garbage-collected before anyone asked.

Four things already address the neighbouring symptoms, and a design that ignores them
would rebuild what exists: claiming re-titles the terminal tab with lane + item topic
(WI-0031 Part A), the feed carries live claims with expiry, the tombstones give
a feed reader its *"released or never claimed"* rung, and the standard's session-start
step 8 now asks every Architect to recommend two or three items rather than relay the list.

**Symptom 2 — work arrives from conversation rather than from the list — has no
mechanism proposed here, deliberately.** Every candidate for forcing it (refusing work
that names no item, prompting when untracked work starts, re-surfacing a claim at turn
boundaries) is the nagger the item forbids, and
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
does not fire on an impression. There is currently no number for how often a session
closes having claimed nothing, because there is no durable record to count — which is
the same gap D1 closes.

## Decision

**D1 — the journal gains a `work-items:` frontmatter key, written at close.** At
`session.py end`, after the journal is located and before it is stamped, the harness
collects the work-item ids this session held and writes them as a frontmatter field.
`_rerender_journal` already preserves keys a journal grew, so the schema extension needs
no separate migration and old journals stay valid.

The close is the correct and only moment: it is the one point where both facts are
simultaneously true — the coordination store still holds this session's live claims and
its unexpired tombstones, and the journal is open for its stamp. Harvesting earlier would
miss later claims; harvesting later is impossible, because the evidence expires.

**D2 — the harvest keys on the claim's `journal` field, never on the identity key.**
A claim record carries both. The identity key is computed from *the checkout that executed
the write* — `_coord_identity()` returns the lane branch inside a lane and the Claude
session id otherwise — and WI-0123 reproduces live that this is already wrong for any
claim taken through `poga work claim`, which correctly anchors on the main checkout and so
records the session id while the lane's own `release` computes the branch and refuses to
match. The `journal` field does not have that defect: it named the right journal in the
same live case. **D2 is therefore load-bearing rather than stylistic — the obvious key is
the broken one**, and this ADR should not ship before WI-0123 fixes the match key it
shares.

**D3 — "claimed nothing" and "evidence already gone" are different values.** An empty
harvest is written as an empty field, meaning *this session claimed nothing* — an honest
and common state. A harvest that cannot read the coordination store at all writes no key,
which is a third state distinct from both. Folding "could not tell" into "nothing" is the
failure [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
names, and this surface exists to be counted, so a miscount would propagate into the D5
measurement as fact.

**D4 — nothing is prompted, refused, or interrupted.** The harvest is a passive read at a
moment that already exists. No session is asked to claim an item, warned that it has not,
or blocked from closing without one. This is what keeps D1 on the correct side of *"do not
build a nagger"*: it changes what is *recorded*, never what the session is *made to do*.

**D5 — symptom 2 gets no mechanism until D1 has produced a number.** Once the harvest
exists, *"what fraction of sessions closed holding zero items"* becomes a mined value from
committed journals rather than an impression, and `curate/metrics.py` is where it belongs.
That number, not this ADR, decides whether symptom 2 needs anything built. Designing the
fix before the measurement exists is the move the operator has ruled against directly, and the one
this project has paid for before.

## Alternatives Considered

**Prompt for an item when untracked work starts.** The most direct attack on symptom 2,
and the explicit thing WI-0058 forbids. It also cannot be built correctly: deciding that
work has "started" is judgment, and a code-side heuristic would fire on conversation and
stay silent on the real cases, which is worse than not firing at all.

**Refuse to close a session holding no claims.** Structural, enforceable, and wrong — it
converts a recording gap into a workflow blocker, punishes the legitimate sessions
(conversational turns, investigations that close nothing), and would be routed around by
claiming an item pro forma, which corrupts the very record D1 is trying to make truthful.

**Extend the claim TTL so the evidence survives longer.** Treats a symptom. The TTL exists
to stop a dead lane's holds blocking siblings forever, so lengthening it makes the
coordination store worse to make the record better. Durability belongs in the durable
artifact, not in a lock's expiry.

**Have `poga work` write the item→session link into the store at claim time.** Puts the
record in the same place that already expires, and gives the item file a second writer
(claims are the coordination store's, [P13](../principles/master.md#p13--single-writer-per-state)).
The journal is the artifact that is already durable, already committed, already compiled
and already mined.

**Keep reconstructing after the fact, better.** The status quo. It has been tried once, on
session ~109, and produced the wrong answer — not through carelessness but because the
evidence was already gone. A discipline that requires remembering to look within eight
hours is not a mechanism.

## Consequences

- *"What did this session do?"* becomes answerable from committed history, permanently,
  for every session after this ships — including sessions nobody thought to ask about at
  the time.
- The compiled `session-handoff.md` can render each entry's items, and
  `curate/metrics.py` gains a countable claim→session series it did not have.
- **The journal frontmatter is fleet substrate**, so this is a standard-version bump with
  a detector shipped in the same pass
  ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability))
  — three consecutive releases have now had to retrofit a marker for code that shipped
  without one, and this ADR is not adding a fourth.
- **No migration.** Journals written before this carry no `work-items:` key, which is
  honest absence rather than an empty claim — consistent with D3.
- Blocked on WI-0123: the match-key defect must be fixed first, or the harvest inherits it.
  The two share a direction (key on the journal, not on the executing checkout) and should
  land together.
- Nothing about how work *starts* changes. If symptom 2 turns out to be real at a rate
  worth acting on, D5 says the number will say so; if it does not, this ADR has still
  fixed the record.

## References

- [ADR-0073](0073-work-item-store.md) — the work-item store.
- ADR-0092 (withheld) — closed WI-0058's display half; its cutover is what made the remainder legible.
- [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) — claims/leases in the git common dir.
- [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) — the journal as the durable per-session record.
- WI-0058 (the item and its own notes), WI-0074 (release tombstones), WI-0123 (the claim-identity defect), WI-0031 Part A (terminal re-title on claim).
- [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes), [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence), [P13](../principles/master.md#p13--single-writer-per-state), [P15](../principles/master.md#p15--code-for-mechanism-not-judgment).
