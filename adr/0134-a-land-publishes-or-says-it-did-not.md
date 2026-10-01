# ADR-0134 — A land publishes, or says out loud that it did not

**Status:** Accepted
**Date:** 2026-09-13
**Session:** ~323 (lane, dispatch D-2659c6)
**Work item:** WI-0355
**Amends:** [ADR-0050](0050-headless-background-adoption-runner.md) §4 (mechanism only)
**Implements:** [ADR-0056](0056-session-branch-gated-trunk.md) D3 step 5, which was never true

---

## Context

`session.py merge` pushed only with `--push`, a `store_true` flag that defaulted off. Every
dispatch brief lands a lane with `merge --continue` or `merge --commit "<message>"` and no
flag, and `recover-lanes` drives its lands the same way (`_drive_lane_merge`). So **every
dispatched lane landed locally and left the trunk owed** to whichever interactive session
next happened to notice.

Nothing surfaced it. Measured twice in session ~302: **6 unpushed commits on local `main`
at one point and 9 at another**, all accumulated from lanes that had landed cleanly hours
earlier, found only by comparing local `main` to origin by hand. Between those two pushes
origin was stale while `poga work show` and every lane read a local `main` that was ahead
of it.

It also contradicted the repo's own writing in three places:

- [ADR-0056](0056-session-branch-gated-trunk.md) D3 makes `git push` **step 5 of the land
  flow**, not a flag, and frames "land locally, push owed" as the *offline exception*;
- `_publication_is_stalled`'s comment: *"in a healthy repo it cannot persist: every land
  pushes, so a trunk that origin does not carry means some land did not finish"*;
- `poga`'s own header, which describes the rejected-push self-heal as something the land
  does for itself.

Exactly three strings in the repo passed `--push` — one `standard-source.md` block and its
two generated copies. Every executable path and every lane-facing brief omitted it.

## The constraint that shaped the answer

Making the push the default puts a **flaky network on the critical path of every land**.
A route to the remote can be intermittently broken — re-measured this session, one
failure in four `ls-remote` probes: exit 128 after 19.6s with an ssh operation timeout
and a broken pipe, with successes at 0.85s either side of it.

The land receipts price the fault exactly. Of 91 receipts, 16 carry a push stage: min
1.26s, median 1.44s, p90 2.56s — and one outlier, `2026-09-12`, with **`push
75.013s` followed by `integrate 75.024s`, a 151.4s lock hold** on a land whose work was
already on the trunk. Neither half bought anything: 75s is ssh's own TCP timeout, and the
integrate that followed re-paid it on a fetch down the same dead route.

## Decision

**D1 — The push is a step of the land. `--no-push` is the opt-out.** `merge` and `end`
both default to `push=True`. `--push` is retained as an accepted no-op so the
`STANDARD.md` that ships to every member — and every brief already written — keeps
working; members do not all re-render on the same day, and turning a stale doc into a hard
argparse error on the one verb a member cannot work without is not an upgrade.

**D2 — A failed push never fails the land and never strands the lane.** The commits are on
the local trunk and are not lost. What is owed is one retry, not a rollback. `LandOutcome`
is unchanged and the exit code stays 0.

**D3 — But it is never silent.** `landed and pushed` and `landed, push owed` are different
states of the world, and the second being invisible *is* the defect this ADR repairs. A
land that could not publish prints, on the banner:

```
landed:  lane 'worktree-poga-6' → main
         LANDED, PUSH OWED — the work is safe on the local main; origin does NOT carry it yet.
         reason: origin unreachable (no answer in 30s)
         nothing is lost and nothing is rolled back — retry the push alone with `python3 session.py integrate`.
```

An auto-push that swallowed its own failure would reproduce WI-0355's bug with extra steps.
Success stays silent: the push is a step of the land now, so its happening is not news.

**D4 — The push is bounded.** `sh` passes no `timeout` to `subprocess.run`, so a push into
a black-holed TCP session hangs for as long as the kernel allows — and a hang *inside the
serialized section* is the worst failure available here, because it is invisible: the lane
goes on reporting live while holding the land gate against every queued peer.
`LAND_PUSH_TIMEOUT_SECONDS` is derived from `LAND_LOCK_HOLD_BUDGET_SECONDS` (30s) rather
than written as a second literal — it is the same constraint said once. A push still
running when the time for the whole serialized section is spent is not going to finish. It
is ~12x the measured p90.

**D5 — A push that never reached origin does not buy a futile integrate.** `REJECTED`
(origin answered and refused us — a peer moved the trunk) still escalates to
[ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md)'s integrate, which fixes it
without a human. `UNREACHABLE` returns immediately, because integrate's first act is a
fetch down the same route that just failed; the 2026-09-12 receipt is what that costs.

Classification is by **positive identification only**: a timeout, or stderr matching
`PUSH_UNREACHABLE_MARKERS`. Anything unrecognised keeps the old behaviour and escalates.
The classifier is allowed to be incomplete; it is not allowed to be wrong in the direction
that removes a working repair.

**D6 — One pusher, not three.** `_push_trunk` replaces three near-identical push blocks in
`_land_worktree_lane`, `_land_candidate` and `_land_branch` ([P16](../principles/master.md#p16--avoid-duplication)).
"Did this land reach origin?" is one question, and three copies of the answer are three
chances to drift.

**D7 — No remote configured is not an owed push.** A member with no origin is local by
design — the same answer `_integrate_trunk_with_remote` gives — and a fixture repo is the
common case in the suite. Reporting either as a stalled delivery would make `push owed`
mean nothing inside a week, which is how a real one gets ignored.

## Alternatives rejected

**Leave the default and fix the briefs** — add `--push` to every briefed land command.
Rejected: it is the [`a-verb-is-still-an-errand`](../habits/master.md) failure written into
the substrate. A fix that works only when the next brief-author remembers a flag has not
shipped, and there are ~20 briefed land strings across three modules to remember it in.

**Flip `merge` only, leave `end` opt-in.** Rejected: `end` reaches the same three landers,
so the defect would have survived behind a second door. The cost is that ADR-0050's
headless runner inherited its "the one outward act stays off" property from a default that
moved; it now **declares** `--no-push`, which is the only form of that rule that survives
the next default change.

**Push outside the serialized section, after the lock is released.** Genuinely attractive —
it takes the network off the hold budget entirely and would have saved the whole 151.4s of
the 2026-09-12 receipt. Rejected *for now* because the lock is what makes "the trunk ref
and origin agree" a single transaction: a push outside it can interleave with the next
lane's CAS, and the rejected-push integrate (which re-enters the gate) has no lock to
re-enter. It is the right next step and is recorded as one rather than taken here.

**Retry the push inside the land.** Rejected: a retry on a flaky link is spending the land
gate on a coin flip, and `integrate` already exists as the cheap out-of-band retry — it is
one verb, it runs without the gate, and the banner now names it.

## Consequences

- A dispatched lane publishes with no human action; origin equals the local trunk one push
  after the land. The "push owed" backlog this item was minted for stops accumulating.
- ADR-0056 D3 step 5 becomes true for the first time.
- Members receive the change through the ordinary `STANDARD.md` redistribution; `--push`
  keeps working throughout, so there is no flag day.
- The headless adoption runner's behaviour is unchanged, now by declaration.
- **Still owed:** the push remains inside the held section (see the rejected alternative),
  so a 30s unreachable push can still spend the hold budget once. That is bounded now,
  where it was not, and FL7 reports it.
