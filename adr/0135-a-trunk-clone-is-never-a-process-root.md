# ADR-0135 — A trunk clone is never a process root, and a sealed machine writes on a channel

**Status:** Accepted
**Date:** 2026-09-13
**Session:** ~328 (lane, dispatch D-dc0e06)
**Work item:** WI-0360
**Supersedes:** the "the Runner keeps a full federation checkout" reading of
[ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md)
**Amends:** [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) (transport only — the leg is unchanged),
[ADR-0109](0109-preflight-repairs-what-it-can-and-escalates-only-what-it-tried.md) (the no-git-to-the operator rule; the brief said ADR-0120 and ADR-0120 is about renumbering an unlanded ADR — corrected here, not raised)
**Deciders:** the operator (R1–R4), Federation Architect (the mechanism)

---

## Context

Every other system on the Runner runs from a sealed release tree under `~/deploy/<system>`,
checked out at a tag and written by nothing. On 2026-09-13 every one of them reported *"already
running vX — nothing to do"* on every sweep. None had stranded, drifted or diverged once.

The federation was the single exception, and in **two ways at once**:

1. **It ran from a live trunk clone**, `~/principles-of-good-architects` — the same trunk
   devbox advanced all day, so it fell behind by hundreds of commits within a day.
2. **Its own unattended jobs wrote into that clone and then tried to publish.** The deploy
   runner queued receipts (`outbox.publish`), the mail-poller committed queued mail, the
   runner wrote escalation notes into `comms/`. Each write that landed after the trunk had
   moved left the clone one commit ahead and N behind: it could neither fast-forward nor
   push. Measured three times that day (`721862ee`, queued mail; `7507a2e3`, a
   receipt; and once more). **Each was cleared by hand.**

The second is not a consequence of the first and is not fixed by fixing it. Worse: sealing
the process root — which WI-0224 and WI-0359 correctly did — *breaks*
things that worked, because a deploy tree is a **detached checkout at a tag with no
upstream**:

| Path | Behaviour in a trunk clone | Behaviour in a deploy tree |
|---|---|---|
| `curate/outbox.py` `publish()` | commit, push, rebase-on-reject | commits **on a detached HEAD**, logs *"committed, not pushed"*, returns True. Receipts stop travelling. |
| `curate/mail-poller.py` `refresh()` | `fetch` + `merge --ff-only @{u}` | no `@{u}`; delivers the **tag's frozen queue** forever. Inbound mail to the Runner-resident members stops. |
| `deploy/runner.py` `escalate()` | writes `comms/…md`, a session commits it | writes into a tree the next `checkout --force` overwrites. (Five such notes already sat unread in the process clone, one per night since 09-11.) |
| `deploy/runner.py` `seed_state()` | seeds from a declared map | **no machine had ever written the map.** After cutover the adopt-runner can locate zero member repos. |

Every one of those fails **quietly**, as a reasonable-sounding line in a log. That is the
property that let the arrangement survive as long as it did — and the nightly
fast-forward pull that kept rescuing the clone was itself part of the camouflage: *a
refresh that succeeds is what hides a design that cannot.*

## Decision

**R1 — Sealed copy, same as every other system.** The federation's three Runner units run from
`~/deploy/federation` at a tag, updated only by a tag. **No trunk clone is a process root.**

**R2 — Production never writes into a trunk clone.** Receipts, escalations and queued mail
from a resident machine travel on a **channel** that only that machine writes.

**R3 — The live trunk clone on the Runner is retired as a process root.** Nothing runs from
it, nothing refreshes it, nothing depends on it.

**R4 — Guards, so it cannot come back.** Unit definitions may not name a WorkingDirectory
outside a deploy tree; the runner refuses to run as a scheduled job from a working clone
once the machine has adopted the sealed model; the channel refuses a second writer; and a
publish into a sealed tree refuses rather than committing where nothing can push.

### The mechanism (D1–D8)

D5–D7 arrive from **rev 2 of the brief** (2026-09-13), in which the brief's own author
re-checked the design against the code and corrected three things. The brief lives in
gitignored `proposed-edits/`, so a lane cannot read it; rev 2 reached this lane as prose and
was acted on only after each claim was verified here independently. Rev 2's third correction
— keep the process clone as a hard-reset read-only mirror rather than leaving it inert — is
**accepted in principle and not built**: a hard reset cannot refuse or strand the way the
ff-pull did, so it does not reverse §6's DO NOT, but the channel clone already is the
Runner's current-trunk read surface and D4's vault already holds `repo-paths.local`, so the
mirror would be a second always-current clone for no capability. The directory's disposition
is a W4 decision either way.

**D1 — One channel clone, `~/deploy/federation-channel`.** Neither a process root (nothing
execs from it) nor a trunk clone (nobody develops in it). Created **by the runner and the
poller**, never by a person: the ruling forbids a manual step on the Runner, so the first
sweep or poll after cutover makes its own write surface from the deploy tree's own
`origin`.

**D2 — One branch we write, `runner/mail`, with exactly one writer.** A branch only one
machine ever commits to is always ahead of its own remote tip, so **its push always
fast-forwards**. The strand becomes impossible *by construction* rather than by retry.
`outbox.publish`'s rebase-on-reject recovery remains correct for a shared branch and is
simply never reached here.

**D3 — No local `main` in the channel, and the trunk is merged IN, not rebased onto.**
There is no local trunk to fall behind, so there is nothing to diverge. The working tree
still needs a *current* `outbox/` and `mailboxes.json` for the inbound leg, and it gets one
by merging `origin/main` into `runner/mail` each cycle. That keeps the push a fast-forward
from its own remote tip whatever it absorbed, and makes devbox's landing merge trivial
because the branch already contains main.

**D4 — The seed map is an OUTPUT, not a precondition.** `seed_state` derives its source
from the checkout the system is being moved off — read from the running units' live
`WorkingDirectory`, falling back to the runner's own checkout for the `self` row — and then
**writes** `deploy/seed-paths.local.json` naming the deploy tree's own copies. The three
federation state entries become `required: true`: a miss now refuses at deploy time, with
the previous version still running, instead of failing silently at 03:15.

**D5 — The devbox side is a READ, not a land.** The Runner's receipts are published on
`origin/runner/mail`, and a branch on `origin` is exactly as durable as `main` for
provenance — so folding them onto the trunk is about *history*, not delivery, and is a later
lane's option. What is not optional is that a waiting brief be **visible**: mail that arrives
where nobody looks is mail that did not arrive. `channel.waiting()` reads the branch and
`curate/deliver.py`'s audit line reports it.

**The first cut of this got it wrong in the way the ADR is about.** It landed `runner/mail`
onto the trunk from `mail-poller.refresh()` — which is unreachable: on the Runner that
function has already returned from the sealed branch, and **devbox has no mail-poller at
all** (`~/Library/LaunchAgents` carries no federation agent; checked). An automatic path hung
on a hook that never fires is an errand with its receipt filed in advance. The read lives in
the delivery audit instead, which is a SessionStart hook and therefore an actor that
demonstrably runs.

**D6 — Every push names its destination.** `curate/outbox.py` pushed bare. A bare `git push`
takes its destination from the clone's upstream plus the operator's `push.default`, neither
of which is visible at the call site — and after D1 that code also runs inside the channel
clone. Measured against the pre-fix shape across all four settings: under
`push.default=upstream` **the unattended Runner job would have pushed the trunk**; under
`matching` it reported *"Everything up-to-date"* and published nothing; only `simple`
refused. The rebase retry was worse again — it rebases onto `@{u}` first, so a wrong upstream
rewrites the branch onto main *and then* pushes it there. Both sites now push an explicit
`HEAD:<branch>` and refuse on a detached HEAD rather than guessing.

The safety property has to live at the push site. It previously rested on an invariant
established in `channel.ensure`, which only the deploy sweep and the poller's refresh call —
so the on-demand `deploy` verb reached the push without it having run.

**D7 — The cutover repoints `~/.local/bin/poga`.** It is a symlink into the process clone,
and it is not a unit, so nothing in the cutover was looking at it.
Retiring the process root under R3 with the link still pointing into it takes down every
Runner-resident Architect's tooling. Repointing it also makes the CLI on the Runner
self-updating through the tag, which it was not. It repoints only a symlink that currently
resolves outside the deploy tree, refuses a real file, and swaps atomically so the command is
never briefly absent.

**D8 — What the fleet reads did not move.** `outbox/to-<id>/` is still the queue,
`mailboxes.json` is still the roster, a comms note is still the escalation.
[ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)'s return leg is unchanged; only its transport moved.

### The rule this ADR exists to be cited for

> **Runners live in the deploy tree of the system they belong to. A trunk clone is never a
> process root, and a sealed machine writes on a channel, never on `main`.**

## Alternatives Considered

- **Keep the trunk clone and make the refresh more robust** (the nightly fast-forward pull
  of WI-0351). Rejected, and the reason is the ADR: the refresh is what *concealed* the
  defect for months. Under R3 it is removed, not improved.
- **Make `outbox.publish`'s rebase recovery smarter.** It was made correct on 2026-09-13
  and is correct — for a shared branch. It cannot help a detached HEAD, which has no
  upstream to rebase onto. Fixing the recovery treats the symptom the writer's *location*
  causes.
- **An orphan `runner/mail` carrying only the mail payload.** Push-able for the same
  reason, but it cannot carry a current `outbox/`/`mailboxes.json`, so the inbound leg would
  need a second mechanism. Two mechanisms where one does.
- **Write by git plumbing** (`hash-object` / `mktree` / `commit-tree` / `update-ref`)
  against a clone checked out on a `main` mirror. Works, needs no merge — and is unreadable
  at 03:15 by the person holding the failure. This code's readers are the people it wakes.
- **A local `main` hard-reset to `origin/main` each cycle.** Fine until the day a reset
  drops mail committed and not yet landed on the trunk: silent data loss in the one program
  whose job is not losing mail.
- **A network write path from devbox to the Runner.** Ruled out by the operator, 2026-09-13, as a
  hard boundary. [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) D1 already says the
  git remote is the only bridge, and this ADR keeps that true.
- **R4(b) as written — refuse whenever REPO_ROOT has a symbolic branch head.** Narrowed to
  *a job running under launchd*. Taken literally the broad form also refuses a person
  typing `python3 deploy/runner.py example-app` in a trunk clone on any machine that ever
  completed one deploy. That is not the hazard, and a guard that fires on correct use is a
  guard somebody deletes. `running_under_unit()` separates the unattended writer from the
  human exactly.

## Consequences

**Good.** The stranded-commit class is closed by construction, not by vigilance. Receipts
and escalations keep travelling *after* cutover, which they would not have. Inbound mail to
the Runner-resident members survives sealing. The adopt-runner can find member repos
on a machine nobody hand-configured. And the federation stops being the one system on the
Runner that is special.

**Costs and new obligations.**

- **A third clone on the Runner.** Disk, and one more thing that can be wrong — mitigated
  by it being created and repaired automatically, and by it holding no state anyone else
  reads.
- **`runner/mail` accumulates on `origin` and is not folded onto `main`.** Deliberate (D5):
  the branch is as durable as the trunk for provenance, and it is *visible* — the delivery
  audit reports what is waiting. Folding it onto main for history is a later lane's option,
  and `channel.land()` is the verb for it.
- **`required: true` can refuse a deploy.** Intended. The refusal leaves the previous
  version running and says what is missing; the silent alternative is an adopt-runner that
  reports a clean night having looked at nothing.
- **Merge commits on `runner/mail`.** Accepted: the branch is transport, not history
  anyone reads.

**Not done in the lane that wrote this, and not claimable from it.** W3 (cut over and prove
the *second* tag needs nobody) and W4 (retire the process root) are acts on the Runner, from
a tag. A lane on devbox can cut no tag and may open no path to the Runner. They are carried
forward on WI-0360 with the acceptance clauses unchanged.

**Follow-on measurement, not lane acceptance.** *"WI-0159 records zero Runner touches for
the seven days after W3"* is what proves the class stayed **closed** rather than merely
fixed. A lane cannot observe a seven-day window without holding its claim and its dispatch
slot for a week — the starvation WI-0354 was built to end — so it belongs to OPS-0007's
weekly review.

## References

- [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) — residency and the deploy model;
  D1 ("the git remote is the only bridge") is preserved, the full-checkout reading is not.
- [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) — the return leg, transport amended here.
- [ADR-0109](0109-preflight-repairs-what-it-can-and-escalates-only-what-it-tried.md) — amended with the
  standing no-git-to-the operator rule: no git sentence reaches the operator unless the design is being changed
  or something was lost. It is the same ruling ADR-0109 already carries (preflight, git and lane problems are fixed
  and carried on from, not reported), extended from preflight to the deploy substrate.
- [ADR-0046](0046-per-system-comms-surface.md) — the comms note as the channel to the operator,
  which is why an unpublished escalation is not an escalation.
- [ADR-0091](0091-the-harness-commits-what-the-harness-writes.md) — the harness commits what the
  harness writes, pathspec'd; the channel keeps both halves of that.
- `proposed-edits/federation-arch/pending/2026-09-13-consultant-federation-runs-on-the-runner-like-every-other-system.md`
  — the brief, with the measured stranding times and the operator's four rulings.
- WI-0360 (this wave), WI-0224 / WI-0359 (the sealing), WI-0225 (retire the dev clones),
  WI-0351 (the nightly ff-pull, reversed here), WI-0159 (the hand-touch ledger).
