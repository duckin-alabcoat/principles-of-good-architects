# ADR-0105: ROADMAP.md is a compiled, trunk-only view; the outcome paragraph lives in the journal

**Status:** Accepted
**Date:** 2026-08-30
**Deciders:** the operator (chose the trunk-only treatment over splitting the file or merge-driving it, session ~174). Federation Architect (the Outcome section, the `Now` ruling, the ordering).
**Extends:** [ADR-0056](0056-session-branch-gated-trunk.md) (`session-handoff.md` and `STATUS.md` are trunk-only generated views) to the third view. Supersedes nothing.
**Builds on:** [ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) (a journal is per-session and cannot collide), [ADR-0030](0030-roadmap-deliverable-format-federation-owned.md) (the ROADMAP format), [ADR-0073](0073-work-item-store.md) (Next/Backlog already render from the store)
**Reality:** Built federation-side. The fleet rollout rides the queued standard bump, with WI-0055 / WI-0017.
**Work item:** WI-0108

## Context

`ROADMAP.md` was the one generated-ish view left out of
[ADR-0056](0056-session-branch-gated-trunk.md), while being subject to exactly the forces
that ADR was written about. The session-end protocol tells **every** session to refresh
it, so every lane edits the same file at close; it is half rendered and half
hand-authored, so the rendered halves regenerate identically while the prose halves
diverge. **Two lanes closing on the same day therefore conflict by construction, not by
bad luck.**

Observed at session ~129, landing four stranded lanes: three landed, and poga-2 blocked
on a rebase conflict whose *only* conflicting file was `ROADMAP.md` —
`curate/deliver.py` and `tests/test_deliver.py` auto-merged cleanly. The conflict does
not merely delay a lane; the lane stays **unmerged**, which is the state every other
piece of substrate treats as stranded work.

**The cheap fix is a trap, and measuring it is what killed it.** The obvious interim —
auto-resolve a ROADMAP-only conflict by taking the trunk side — was checked against
poga-2's own journal (`20260807-06d0`) before being proposed. That journal's
sections are *What happened / What's next / Open questions / Notes*; there is **no
outcome section**, and the Recently-shipped bullet the session wrote into `ROADMAP.md`
appears **nowhere** in it. The outcome paragraph is authored *only* into `ROADMAP.md`
today, so taking the trunk side would silently delete authored prose on every land, with
no second copy to recover it from. That is precisely the loss session ~129 compensated
for **by hand**, saving poga-2's version off and re-adding the bullet afterwards.

So the ordering is forced: the prose needs a per-session home *before* the shared file
can stop being hand-edited.

## Decision

**`ROADMAP.md` becomes a compiled, trunk-only view. The per-session prose that used to be
typed into it moves to the journal, which cannot collide by construction.**

### D1 — The journal gains an `### Outcome` section

One paragraph per session, framed as **what changed for the system** rather than which
artifacts were committed ([`frame-work-in-outcome-terms`](../habits/master.md#frame-work-in-outcome-terms)).

A journal is a per-session file. That is the entire
[ADR-0051](0051-multi-session-concurrency-retire-the-master-session.md) design, and it
means two sessions writing outcomes cannot collide however concurrent they are. This is
the only place the per-session prose belongs, and it is a **new** section rather than a
re-use of *What happened*: that section is the blow-by-blow, and the outcome is the one
paragraph a reader who was not there needs.

### D2 — `Recently shipped` is compiled from those sections, newest first

`ROADMAP.md`'s `## Recently shipped — outcomes` becomes a generated region, built from the
journals' `### Outcome` sections in the same newest-first order the handoff uses. A session
that writes no outcome contributes no bullet — silence, never a fabricated one.

`Next` / `Backlog` already render from the work-item store ([ADR-0073](0073-work-item-store.md)),
so after this the only hand-authored region left is `Now`.

### D3 — `Now` stays hand-authored, and that is a decision, not an omission

WI-0108 asked for this to be settled deliberately rather than left as a residual hotspot.
It is settled by D4: **`Now` is a standing statement of where the system is, not a
per-session record**, so pushing it into a journal field would ask every session to
re-declare something that usually has not changed. Once lanes stop writing the file at
all, `Now` stops colliding *by construction* — the hotspot closes without moving it.
Editing it is a trunk-side act, like editing the hand-authored half of `STATUS.md`.

**Amended 2026-09-13 (WI-0285), narrowly.** `Now` now *opens* with a generated region of
its own — the finish-line board's one-line verdict, filled by the same trunk compile that
fills the other four. D3 is unchanged in substance and its falsification condition is
untouched: the **prose** under `Now` is still hand-authored, still trunk-side, and still
has no journal field. What moved is smaller and was implicit rather than decided — that a
section is hand-authored or generated *as a whole*. It is not: the markers decide, per
region, and `Now` is the first section to carry both kinds. The alternative was to give
the verdict its own `##` heading above `Now`, which would have satisfied the renderer with
no amendment at all; it was rejected because the deliverable is that the first thing read
in the roadmap is the number, and a second heading competing with `Now` for that position
buys a clean record by making the human-facing file worse.

### D4 — Lanes never write `ROADMAP.md`

The file is marked **GENERATED — do not hand-edit** for its compiled regions, and the
compile that writes it runs on the trunk only, exactly as `run_compile` already does for
`session-handoff.md` and `STATUS.md` ([ADR-0056](0056-session-branch-gated-trunk.md)).

The session-end protocol step changes from *"refresh `ROADMAP.md`"* to *"author your
Outcome paragraph"*. This is the load-bearing half: as long as the protocol tells every
lane to edit the shared file, some lane will.

### D5 — The rollout rides the queued standard bump

Both the ROADMAP format ([ADR-0030](0030-roadmap-deliverable-format-federation-owned.md)) and the journal format
are federation-owned **standard**, so this is a standard change — a version rung, a
detector, and a rollout to every member. It is deliberately sequenced with the bump
WI-0055 and WI-0017 are already queued behind rather than minting its own, because three
standard rungs pushed separately is three fleet disruptions for one coherent change.

## Alternatives considered

**Split the file** — a generated `ROADMAP.md` plus a separate hand-authored file lanes
still edit. Rejected: it renames the hotspot rather than removing it. The hand-authored
file is still one shared file every session is told to touch.

**A git merge driver for the path** — cheapest to build. Rejected: union-merging prose
produces text no one wrote, and a semantic conflict still needs a human. It moves the
conflict from `git` to the reader.

**Auto-resolve ROADMAP-only conflicts to the trunk side** — the tempting interim.
Rejected on measurement, not principle: with no second copy of the outcome paragraph it
silently deletes authored prose on every land. Once D1 exists it is redundant anyway.

**Leave it alone.** Rejected: the cost is not the conflict, it is that a conflicted lane
stays unmerged, which every other surface reads as stranded work.

## Consequences

- Two lanes closing on the same day no longer touch the same file. **This is the
  acceptance test, and it is to be exercised rather than asserted** — the item says so in
  as many words.
- The outcome narrative becomes durable per session instead of existing only in a shared
  file. It also becomes minable, which it was not before.
- A session that writes no `### Outcome` contributes nothing to Recently-shipped. That is
  the honest failure mode — a missing bullet, never an invented one — and it is visible
  in the compiled view rather than silent.
- `Now` remains hand-authored and trunk-side. If it ever does start colliding, that is
  evidence D3 was wrong and it should move to a journal field. (Its prose does. Since
  WI-0285 the section also opens with a generated region — see the amendment under D3.)
- Members keep hand-editing their own `ROADMAP.md` until the rollout lands. The
  federation dogfoods first, as with [ADR-0073](0073-work-item-store.md).

## References

- Work items: WI-0108 (this), WI-0055 / WI-0017 (the bump this rides), WI-0100 (the other view compiled from journals)
- [ADR-0056](0056-session-branch-gated-trunk.md) — the decision this extends
- Habits: [`frame-work-in-outcome-terms`](../habits/master.md#frame-work-in-outcome-terms), [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver), [`no-fabricated-data`](../habits/master.md#no-fabricated-data)
