# ADR-0126 — The federation couriers a member's mail, and never files their copy

- **Status:** Accepted
- **Reality:** Built — `curate/mail-poller.py` sweeps every outbox this machine can reach; `curate/outbox.py:retire` refuses a foreign queue; `deliver.MalformedBrief` types the refusal no re-run can clear; 18 pinning tests in `tests/test_outbox.py`, each proven to fail when its guard is removed (three mutation passes, **19/19 caught**, 2026-09-11). Measured the same day, the transport gap is CLOSED and **nothing is deliverable from this machine**: 26 briefs queued fleet-wide → **13 elsewhere** (12 addressed to members resident on another machine, 1 to a member the registry records as having no reachable mailbox), **2 unroutable**, **11 malformed**, **0 failed**. Of those 26, **21 are the member briefs the sweep newly reaches** — 9 elsewhere, 1 unroutable, 11 malformed — and no code path had touched any of them before. Every population is now named with an owner and a remedy; none of them moves until that owner acts.
- **Date:** 2026-09-11
- **Extends:** [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) — its D3 poller drained one outbox; this widens the source set and draws the boundary that widening needs.
- **Related:** [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1, [ADR-0110](0110-a-delivery-verdict-declares-the-machine-its-evidence-covers.md), [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md), `curate/push-substrate.py`, WI-0079, WI-0235, WI-0336

---

## Context

**A surface the fleet reported on, and nobody emptied.**

`deliver.py:_outbox_dirs` has read `outbox/to-<architect-id>/` in a *member's* tree since
WI-0079. It is reached from exactly one place: `audit()`, which **classifies**. The poller
that actually delivers reads `outbox.pending()`, and `outbox.OUTBOX` is
`ROOT / "outbox"` — the federation's own repo, and nothing else. So a member writing to
the convention the federation itself documents was audited on every startup and delivered
for by no one.

When it was found, **a backlog of briefs across several members** was waiting, the oldest some seven weeks old. Independently observed
twice: by OPS-0007's first run, and by one of those members' own 2026-09-04 brief, which
says members have no send path.

**The reason this hid for so long is that the mail was visible.** The startup banner
prints the audit's classification every session, so the channel read as instrumented
rather than broken. A defect that leaves its evidence in plain sight and no actor to
consume it is the slow kind: every reader assumed the line they were looking at belonged
to someone else's queue.

The cited ancestor on WI-0336 was wrong and is corrected here: WI-0032 is dispatch
fan-out, unrelated. The real ancestor is **WI-0079**, which built the audit sweep over
this surface and left delivery out.

## Decision

**D1 — the poller drains every outbox on this machine, not just ours.** `outbox.pending_in(repo)`
reads a member's queue by the same `_queued` helper `pending()` uses, so the two can never
learn different conventions ([P16](../principles/master.md#p16--avoid-duplication)). The
poller keeps [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
D3's rule unchanged — it asks *can I reach this recipient?*, never *am I the Runner?* — so
widening the **source** set does not touch the residency logic, and the Runner's share is
still the Runner's.

**D2 — we deliver their mail; we do not file their copy.** A delivery of our own ends in a
`retire`, which **moves a tracked file**. Doing that in a member's tree would put a commit
in their history under our authorship. `curate/push-substrate.py` already draws this exact
line and keeps it — *the federation may write+commit its OWN generated substrate anywhere
it reaches, but still never a target's authored files* — and a brief a member wrote is
theirs. Delivery into the recipient's **gitignored** mailbox is untouched by this and stays
sanctioned ([ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md)); the
asymmetry is not about which repo we may touch but about whose **authored, tracked** state
we may rewrite ([P13](../principles/master.md#p13--single-writer-per-state)).

**D3 — the boundary is a refusal in `retire`, not a rule the caller remembers.** A guard
enforced only by the poller's own care is one refactor from being gone, and the failure is
silent: a foreign brief would be moved into *our* `outbox/delivered/`, vanishing from the
sender's repo. `outbox.retire` therefore refuses any path in a `to-*` queue that is not
under our `OUTBOX`. Scoped to a foreign **queue** rather than to "anything outside
`OUTBOX`", because retiring a brief handed in from elsewhere is an existing deliberate use
and a blanket containment test would have broken it while looking like a tightening.

**D4 — `sender_files` is a census, and WI-0235 is satisfied by naming, not by clearing.**
On the next run the collision guard recognises a couriered brief as one the recipient
holds; since D2 forbids moving it, the count is the same tomorrow. WI-0235's lesson was
narrower than *no count may persist*: what poisoned that report was a permanent `1` in the
**failed** channel — the one number someone watches to notice the mail breaking. This count
is named for what it is, never touches the exit code, and clears when its owner files it.
It is the same shape the delivery audit's own *"N delivered leftover(s) to file"* line has
carried on the startup banner all along.

**D5 — `elsewhere` must not swallow `nowhere`.** `elsewhere` promises *another machine will
carry this*, which is why [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
D3 can call it a healthy outcome. A recipient with no row in `mailboxes.json` is
**UNSURVEYED**, not remote: no poller on any disk can resolve them, so filing them under
`elsewhere` hands the brief to a courier who does not exist — WI-0336's own failure, one
bucket over. Told apart by **asking the registry whether a row exists**, never by matching
`resolve()`'s prose, for the reason `AlreadyHeld` is a type: a caller that separates
outcomes by reading error text is one wording change from folding them back together.
`unroutable` does not set the exit code — its remedy is a registry row a person adds, and a
scheduled job that fails for days until someone does paperwork is the alarm nobody reads —
but it prints unconditionally, `--quiet` included, and names the fix. Live today: **2
briefs to one member Architect** that has no registry row.

**D6 — our own tree is skipped by self-report, not by path.** The federation joined the
locator map on 2026-08-13 so briefs could be delivered *to* the hub, which enrols us in
this sweep as a member too. Identity is the repo's **own declared `architect_id`**, the
authority `audit()` already uses for the same question, so a lane worktree and the main
checkout both answer `federation-arch` and both are skipped. A resolved-path comparison
would call the main checkout foreign whenever the poller runs from a lane — and would then
deliver from one path while declining to retire from the other, stranding mail we are
entitled to file.

## Consequences

- **The courier exists; nothing is delivered yet, and the distinction is the point.** All
  The waiting member briefs are now reached, and all of them are refused further down by gates that were
  right to refuse: **9** are addressed to members resident on another machine and always were,
  **1** names a member with no registry row (D5), and **11** carry `apply:` headers that
  route them to nobody (D7). What changed is not that mail moved — it is that three
  populations which were invisible or filed under the wrong promise now each carry a named
  owner and a remedy. A fix that closes a transport gap and delivers nothing is worth
  recording as exactly that.
- Briefs that do become deliverable arrive as ordinary inbox mail, gated by the receiving
  Architect's own startup sweep. The courier does not decide what they mean.
- **No scrub re-gate on the courier path, deliberately.** `outbox.stage` gates the sender's
  write because the outbox is tracked and permanent; that call was made in the sender's
  repo, by the sender, and a courier that re-adjudicates it becomes a censor of another
  Architect's mail. The destination is a gitignored mailbox, so nothing couriered enters
  tracked history by this route.
- `run_once` now reads the locator before the queue, so a run costs a disk walk even when
  nothing is queued. That is what the audit on the same schedule already costs.
- The Runner's share (briefs to a member resident there) is untouched here and drains when
  that machine polls — the reachability rule working, not a gap.

**D7 — a brief whose own header routes it to nobody is `malformed`, not `failed`.**
`_check_apply_mode` enforces the ADR-0049/ADR-0050 partition and refuses the same brief on
every run until its **author** edits the header — which for a couriered brief means an
author in another repo, so no re-run here can change the outcome. Widening the sweep
surfaced 11 at once; left in `failed` they would have pinned this program's exit code
non-zero forever on its own ten-minute schedule, which is WI-0235's harm re-created by the
fix for WI-0336. `deliver.MalformedBrief` is a **type**, for the reason `AlreadyHeld` is
one. A *missing* checker stays a bare `ValueError` in the alarm channel: nothing is known
about the brief, the fault is in our own tooling, and a re-run clears it.

**D8 — `--dry-run` applies the write path's refusals.** It previously returned before
`deliver.deliver()` and therefore before both of its gates, reporting *is this queued and
is the recipient reachable?* as a delivery: it promised **11 couriered** where the live run
delivered **none**, and that number reached a session journal before anyone ran the real
path. It now calls the apply-mode gate and the collision guard — both read-only — and
classifies their outcomes identically, skipping exactly one thing: the write. A rehearsal
that cannot refuse is a false assurance of the kind this file's every other decision exists
to prevent.

## Alternatives rejected

- **Retire the member's copy and commit in their repo.** The obvious fix, and the one the
  fleet's own recorded boundary forbids. It also writes into a tree a human may be mid-
  session in, and the commit would carry our authorship on their authored file.
- **Write a receipt brief into the sender's mailbox so their Architect files the copy.**
  Closes the loop with the right writer — and needs a federation-side ledger to avoid
  re-sending the receipt every ten minutes, which is more durable state than the problem
  justifies. Reconsider if `sender_files` grows past nuisance size.
- **Ship the poller to every member so each drains its own outbox.** Does not fix it:
  A member on devbox cannot reach a member resident on another machine any more than we can, so the machine
  boundary stays exactly where it was, and every member would need the mailbox registry.
- **Fold `unroutable` or `malformed` into `failed`.** True, actionable, and permanently
  non-zero — the stuck-alarm shape WI-0235 exists to prevent.
- **Fix the 11 non-conforming `apply:` headers ourselves.** One edit each, and it would
  have turned the measured result from *0 delivered* into something that reads far better.
  They are other Architects' authored files — D2's boundary, applied when it was
  inconvenient rather than when it was comfortable. Their authors need telling, through the
  outbox this ADR just taught the fleet to drain.
- **Add the missing registry row here and make D5 moot.** A registry row is a survey
  result, not a guess; that member has no `STATUS.md`, which is why it is unsurveyed, and a
  brief about that is already out to them. Fabricating the row to clear a count is how a
  registry stops describing the filesystem.
