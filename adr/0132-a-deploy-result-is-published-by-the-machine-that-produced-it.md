# ADR-0132: A deploy result is published by the machine that produced it

**Status:** Accepted
**Date:** 2026-09-13
**Deciders:** Federation Architect (WI-0230, dispatched lane — decided in-lane per ADR-0113; the alternatives and the reasoning are recorded here for the operator's batch review rather than asked as a question).

## Context

[ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) put the deploy
on the Runner and answered *"what is running where"* with a ledger — D7, deliberately
**machine-local** and outside the repo, because it records what runs on **this** machine
and a tracked file two machines both write is WI-0132 exactly.

That decision is correct and it has a cost nobody priced. The code is written on devbox.
The result is produced on the Runner. D7 keeps the answer where it was produced, so the
machine that needs it never receives it. Measured for WI-0230: `deploy/runner.py` never
touched `curate/outbox.py` — searching `deploy/` for `outbox` returned nothing — and the
runner's only outbound path was `escalate()`, which writes a `comms/` note for a **failed**
deploy and nothing at all for a successful one. A result reached devbox when a human
carried it.

There is already a channel for exactly this. [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md)
D1 makes `origin` the only bridge between the two machines, and `outbox/to-<architect-id>/`
is the fleet's outbound convention (WI-0079) — **tracked**, precisely so a brief travels by
`origin` rather than by a mount. The runner simply never wrote to it.

And one thing that looked like the channel was not. `curate/outbox.py:stage` was written
for a **session**: it takes a brief a person authored, and the commit that makes it travel
arrives later, from that session's close. An unattended writer has neither — it holds
generated text rather than a file, and it has no close. A brief it merely *wrote* would sit
uncommitted in the Runner's checkout, where `mail-poller.refresh` declines to fast-forward
a dirty tree: **one uncommitted receipt and the checkout stops advancing at all.**

## Decision

**D1 — The ledger stays local and a copy of it travels.** [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md)
D7 is not reopened. The fix is not to make the ledger shared; it is to send a **receipt**
rendered *from* the ledger record. One writer still owns what runs on this machine
([P13](../principles/master.md#p13--single-writer-per-state)), and the development machine
is still told.

**D2 — The write and the commit are one act.** A brief nobody commits has not been sent; it
has been typed. `outbox.post` (generate, scrub-gate, write, read back) and `outbox.publish`
(stage the outbox pathspec, commit, push) exist as a pair, and every unattended writer calls
both. Splitting them **is** the defect — it is what left the receipt on one disk and, worse,
what wedges the checkout it was written into.

**D3 — The poller commits the outbox whether or not it delivered.** `persist` used to return
unless something had *moved*. Mail queued for a recipient this host cannot reach moves
nothing, so it was never committed — the exact case a deploy receipt is. The obligation is a
fact about the **directory** (it is tracked, so it must not be left dirty), not about whether
we happened to deliver today.

**D4 — A receipt posts on a state change, never per invocation.** The sweep re-runs every ten
minutes; a receipt per run would be a tracked file per system per ten minutes forever, and
the record would bury the thing it records. The rule is a change in
`(status, current, attempted)` across the run — which cannot drift the way a list of call
sites would, so a terminal outcome added later posts a brief without anyone remembering to
add one ([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).
`RollbackFailed` is the one exception: it is raised **before** any ledger write, so the single
outcome where production is in an unknown state would otherwise be the silent one. **A dry run
posts nothing** — a rehearsal that writes a tracked file is not a rehearsal.

**D5 — The receipt routes `attended`, never to the headless runner.** A receipt has nothing to
apply, so the strict `apply: auto` schema would be a costume; and the *other* manual route
(ADR-0050) hands the brief to the adopt-runner, which would open a session per deploy to adopt
a record that asks for no change. `apply: manual` / `manual-reason: attended` is the route that
ends in somebody reading it, which is the entire request. This is a correctness requirement, not
a style one: a header `curate/check-apply.py` refuses is bucketed `malformed` by the poller
forever, and **that bucket does not move the exit code** — so the wrong header builds a return
path whose failure is silent.

**D6 — A derived recipient is said out loud.** The architect-id is resolved from
`mailboxes.json` when a row exists and otherwise derived as `<system>-arch`, and the receipt
states which of the two it is ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
A derived id does **not** block the write: a system can be registered for deploy while absent
from the roster, and queuing its receipt at the conventional address still puts the record in a
tracked directory that reaches devbox by `origin`. The poller reports it `unroutable` until
somebody adds the row, which is the visible, correct state.

**D7 — Nothing in the return path can fail a deploy.** The deploy has already happened. A mail
problem is not a deploy problem, and reporting one as the other is how a green release starts
reading as red.

## Alternatives Considered

- **Make the ledger itself tracked and let git carry it.** One file, no new channel — and it
  reintroduces WI-0132 precisely: a per-machine fact stored at one shared path, written by two
  machines. Rejected on [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md)
  D7's original argument, which has not weakened.
- **Write the receipt and let the next commit sweep it up.** What `stage` does today, and it is
  the [`a-verb-is-still-an-errand`] shape: the fix ends in *"and then someone commits"*, so the
  remaining defect is the someone. On an unattended machine there is no someone at all, and the
  uncommitted file actively stops the poller's refresh. Rejected outright.
- **Let the poller alone do the committing (D3 without D2).** Architecturally tidier — one writer
  of outbox commits — and it makes the deploy's return path depend on another program being
  installed and firing. If it is not, the receipt is uncommitted *and* the tree is dirty forever,
  which then wedges the poller if it ever does start. Rejected: the runner publishes its own work,
  and D3 is kept as the net that catches a lost `index.lock` race rather than as the mechanism.
- **Reuse `outbox.stage` by writing a temp file first.** The temp file would exist only to be read
  back by the gate about to refuse it. `post` gates the **text** through the same patterns
  (`scrub.findings`), which is the same check without the detour.
- **Post a receipt on every run, including no-ops.** Simplest rule, and it makes the channel
  useless inside a week. Rejected for D4.
- **`manual-reason:` with a `verify:` command** (the R3 agent route). Rejected in D5 — it spends an
  unattended session per deploy adopting a record with nothing to adopt.
- **Refuse to post for a system with no roster row.** Rejected in D6: it would silence exactly the
  systems that are newest to deploy, and the tracked directory delivers the record regardless of
  whether a mailbox resolves.

## Consequences

- A deploy, rollback or failure on the Runner now reaches devbox as a tracked brief, addressed to
  the system's own Architect, carrying the tag, the previous tag, both gates' output and the
  timestamps — read at that Architect's next session-start inbox sweep.
- **A latent defect closed on the way past:** any brief queued for an unreachable recipient — not
  only a deploy receipt — was never committed by the poller and left the tree dirty. D3 fixes the
  class, not the instance.
- **A test-safety guard that was prose is now a tool.** `deploy/runner.py`'s header has always said
  every path it writes is overridable *as a test-safety property*. Overridable is not overridden:
  adding `outbox_dir()` left the four existing redirections complete and correct, and the suite
  queued fixture receipts into the federation's own outbox and committed eleven of them before
  anything asserted anything. `TestEveryProductionPathIsRedirected` derives the `POGA_DEPLOY_*`
  names from the source and fails when the fixture does not redirect one
  ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).
- **`publish` asks whether an upstream exists before reaching for the network.** A branch with no
  tracking ref — a lane, a fixture, a fresh clone — has nowhere to push and no `@{u}` to merge;
  `git fetch origin` there blocks on DNS or a credential prompt, which is how this first *hung* a
  test run rather than failing it. Absent upstream is now its own answer.
- **Not covered, and named rather than implied:** a `DeployError` refusal (bad contract,
  unregistered system, git failure) leaves production untouched and the ledger unchanged, so it
  posts nothing — the non-zero exit and stderr carry it, as before. A refusal that repeats on every
  sweep is still visible only to whoever reads the sweep's output.
- **A deploy-registered system with no `mailboxes.json` row** reports `unroutable` on every poll
  until one is added. Recorded on WI-0230; it is a roster edit, not this ADR's to make.
- Downstream: WI-0228 (no canary path) is untouched here — a canary, when it exists, is a state
  change like any other and posts through D4 without new code.

## References

- [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) — D1 (`origin` is the
  only bridge), D6 (loud failures), D7 (the ledger is machine-local).
- [ADR-0046](0046-per-system-comms-surface.md) — the comms note `escalate()` writes, which this
  complements rather than replaces.
- [ADR-0049](0049-apply-auto-is-the-authoring-default.md) / [ADR-0050](0050-headless-background-adoption-runner.md) — the
  apply-mode partition D5 routes through.
- [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) — why an outbox is tracked and
  a mailbox is not; `mailboxes.json` as the delivery roster (D5).
- [ADR-0091](0091-the-harness-commits-what-the-harness-writes.md) — the pathspec'd commit.
- WI-0230 (this work), WI-0336 (the outbox the fleet audited and nobody drained), WI-0079 (the
  `outbox/to-<id>/` convention), WI-0132 (a per-machine fact at one shared path).
