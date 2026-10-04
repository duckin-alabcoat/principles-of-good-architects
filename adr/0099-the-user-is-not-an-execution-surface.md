# ADR-0099: The user is not an execution surface — commands belong to the Architect, guidance belongs to the user

**Status:** Accepted
**Date:** 2026-08-22
**Deciders:** the operator (the registry-Accept, and the scope correction that is the substance of this ADR). Federation Architect (parenting to P10 rather than a new principle; the R2 re-scope after verifying what had already shipped; deferring R3/R5 to their own items rather than half-shipping them).
**Triaged from:** [`2026-08-19-consultant-command-escalations-to-the operator`](../proposed-edits/federation-arch/applied/2026-08-19-consultant-command-escalations-to-the operator.md) — an `apply: manual` external-consultant brief, 4 days in the inbox.
**Extends:** [ADR-0013](0013-receipt-ritual.md) (the inbox this arrived through), [ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) (canon is inherited, not per-Architect adopted), [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) / [ADR-0098](0098-a-lane-repairs-main-and-clears-its-own-blocked-rebase.md) (the last-mile verbs this doctrine sits on top of).
**Work items:** WI-0158 (R3, advice-string lint) and WI-0159 (R5, escalation metric) opened here. WI-0099 / WI-0105 / WI-0141 grouped as the remaining last-mile package (R2). Closed by the brief's own evidence: WI-0085, WI-0097, WI-0109, WI-0122, WI-0143 — already `done` before this triage.

## Context

the operator, 2026-08-19, named three recurring asks from POGA sessions: (1) run a command in a
terminal, (2) run a session "not in a lane", (3) paste a command into the prompt. His
ruling: the operator should never see any of these when dealing with poga, and POGA never
asks the operator to perform a git or POGA operation.

That was the **third** restatement of the 2026-08-04 ruling (the Architects do all the
git work, not the operator). Per [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
the discipline-level answer was spent: a rule restated three times and still violated is not
a rule anyone is failing to remember, it is a rule with no structure behind it.

The brief that followed is unusual and worth naming: **every instance in it was drawn from
the federation's own journals and work items.** Nothing was inferred. The honesty of the
records is why the brief could be written at all, and that is a property worth protecting —
a substrate that logged these events as successes would have been invisible to its own audit.

### The three generators, which are three different bugs

The brief's contribution is separating what had been read as one failure into three:

- **RC1 — structural.** [ADR-0059](0059-same-tree-concurrency-fails-safe.md)/[ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md)
  steer all work into lanes; the isolation and destructive-git guards then correctly refuse
  the trunk-touching tail; **no verb existed for that tail.** The guards are right. The gap
  is the missing verb, and the documented fallback was a human shell.
- **RC2 — self-contradictory advice.** `reap-lanes` printed `git branch -D <branch>` *as its
  remedy* — the exact command `check-bash` then denies. An advice string its reader cannot
  execute is not advice; it is an escalation generator, and the escalation target is always
  the operator.
- **RC3 — unscoped denials.** WI-0122 is the clean specimen: the guard denied one *syntactic
  form* (`cd <lane> && git …`) while the sanctioned form (`cd <lane> && python3 session.py
  <verb>`) runs fine. The message did not say so, the agent generalized the denial to the
  whole class, and the operator got two commands he did not need to run.

A missing verb, a substrate bug, and a message-layer bug. None of them is the user's problem,
and only the first is expensive.

### What triage found that the brief could not

The brief was written 2026-08-19 and triaged 2026-08-22. **Five of the eight work items in
its R2 package had shipped in between** — WI-0085, WI-0097, WI-0109, WI-0122, WI-0143, built
across sessions 164/165 under ADR-0097 and ADR-0098. Verified by status, not assumed: R2 is
not a six-row promotion, it is three remaining lane-hygiene verbs.

This is [`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)
paying for itself. A brief is a report about a point in time; adopting its work-package
verbatim would have re-promoted five finished items and mis-stated the remaining scope by
more than half.

## Decision

### D1 — Adopt R1 as a habit under P10, keyed on ownership of the work

New universal habit [`never-route-your-own-work-through-the-user`](../habits/master.md#never-route-your-own-work-through-the-user),
Accepted at registry (the operator approved), parented to
[P10 `architect-owns-operational-substrate`](../principles/master.md#p10--architect-owns-operational-substrate).

Habit, not a new principle: P10 already says *"Routine work is the Architect's job, not the
user's."* The principle exists; what was missing is the sharp practice under it. Per
[ADR-0022](0022-architect-ingest-distillation-and-code-channel.md) the registry-Accept is the
gate and the set is then inherited fleet-wide through `CANON.md` — no per-Architect adoption
event, no §4 table edit anywhere.

**The scope correction is the substance of this decision.** R1 as drafted said an Architect
*"never asks the operator to run a terminal command."* That contradicts the already-Accepted
[`batch-user-asks`](../habits/master.md#batch-user-asks), which explicitly sanctions *"a
single user-execute punch list with exact steps"* for work outside the Architect's sandbox —
GUI panes, OS permission dialogs, browser admin consoles. WI-0049 (grant macOS Automation
permission) is precisely that case and cannot be done any other way. Adopting R1 verbatim
would have put two Accepted rules in direct contradiction and produced a canon rule the fleet
must violate on its first legitimate guidance request.

the operator drew the real line at adoption. His ruling: when the user asks an Architect for
guidance, a list of steps and terminal commands is the right answer; what stops is an
Architect handing its own work to the user to carry out.

So the habit keys on **who owns the work**, not on whether a command appears in chat:

- **User-requested guidance** — the user asks how to do something. Steps and exact terminal
  commands are the deliverable. Withholding them would be the failure.
- **Architect-owned work** — the Architect's task is blocked and the block is escalated into
  the user's hands as labour. This is the defect, and it is what stops.

The fallback ladder on hitting a wall is part of the statement, in order: re-read the denial
for the allowed form (RC3's cure); park the work additively so nothing strands; file or
update the item for the missing verb (RC1's cure). Handing over the command is on none of
these lists.

### D2 — R2 is three verbs, and it is grouped rather than re-promoted

WI-0099 (`lanes discard`), WI-0105 (failed-launch rollback) and WI-0141 (`retire-parked`)
are grouped as **`lane-last-mile`**. The five finished items are not reopened.

They stay in `backlog` rather than being promoted to `next` against the brief's instruction,
and the reason is the brief's own acceptance test: *"the next occurrence of any incident in
the evidence list completes with zero commands handed to the operator."* The incidents that generated
this brief are now covered by shipped verbs. What remains is genuine but quieter cleanup — a
lane's litter, a stranded prep marker, a parked branch. Promoting three cleanup verbs to
`next` on the strength of an argument whose sharp evidence has already been addressed would
be [`frame-work-in-outcome-terms`](../habits/master.md#frame-work-in-outcome-terms) inverted:
shipping the artifact the brief named instead of the outcome it wanted.

**The brief's own divergence tripwire is what makes this safe to say and what will falsify it
if it is wrong.** R1-without-R2 is named in the brief as its own failure mode — the operator stops
seeing commands while parked work silently accumulates. That is exactly what D4's paired
metric is for. If the `lane-last-mile` queue grows or ages, the group is wrong and moves to
`next` on that evidence rather than on this argument.

### D3 — R4 lands now: every denial names the sanctioned alternative

RC3 is a message-layer bug and message-layer bugs are cheap. Guard denials that refuse a
*form* now name the allowed form alongside the refused one. This is deliberately the first
thing built after the doctrine, because it is the generator that survives context
compaction — doctrine can be forgotten mid-session; a deny message is read at the moment of
the decision it governs.

### D4 — R3 and R5 become work items, not this session's build

The advice-string lint (R3) and the paired escalations/parked-queue metric (R5) are each real
build work with their own design questions — R3 must extract command-shaped strings and
evaluate them against a guard stack *per audience*, and R5's whole value is in the pairing
(escalations at zero with the queue growing is a failure, not a success, and must read as
loudly). Filed as WI-0158 and WI-0159 with the brief as their `source`.

Half-shipping either would be worse than not shipping it: a lint that misses the contradicted
strings, or a metric that reports zero because nothing feeds it, both **certify** the problem
as solved. That is [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
read the right way round — the detector must be real or it is an active liability, not a
partial credit.

## Consequences

**The rule is now inherited, not remembered.** `CANON.md` regenerates at 21 principles / 46
universal habits and injects into every member session at start, including members not yet
bootstrapped. No Architect adopts it; every Architect has it.

**One habit's scope got sharper by being contradicted.** `batch-user-asks` and this habit now
partition the space cleanly: out-of-sandbox work batches into a punch list with exact steps;
in-sandbox work is never delegated at all. The contradiction was caught only because the
adoption was checked against the existing set rather than appended to it — which is an
argument for the curator registry-read step surviving future trimming.

**RC1's remaining surface is small and named.** Three verbs, grouped, with a metric that will
say if the grouping was wrong.

**What this ADR does not do**, following the brief: no exception to or weakening of the
destructive-git or isolation guards — every incident in the evidence shows the guards being
right. No new human gates. No change to the `--confirm` close approval itself; the ask stays
prose and the *agent* constructs the flag from a plain reply, which is now covered by the
habit rather than by convention.

**The honest residue.** This session generated two more instances of the very pattern while
triaging it: the isolation guard refused a `cd`-prefixed git command twice, and both times
the denial named a remedy without naming the sanctioned `cd <lane> && python3 session.py
<verb>` form. D3 is written against those two denials specifically.
