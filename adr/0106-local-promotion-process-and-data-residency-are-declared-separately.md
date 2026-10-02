# ADR-0106: Local systems promote devbox → Runner, and process residency is declared separately from data residency

**Status:** Accepted
**Date:** 2026-08-30
**Deciders:** the operator (the 2026-08-22 machine-role ruling; and this session's two design calls — the Runner keeps a checkout, and residency is two axes rather than one), Federation Architect (the mechanism, the refusal policy, and the detector).
**Accepted:** 2026-08-30 (session ~174) — the operator's explicit acceptance, with the instruction to build, after the two design calls above were put to him and answered in order.
**Extends:** [ADR-0042](0042-deployment-classes-and-promotion-discipline.md) (deployment classes + promotion discipline, whose §4 this binds to the local classes), [ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) (the declared data root, which this ADR gives a second instance).
**Bears on:** [ADR-0050](0050-headless-background-adoption-runner.md) (the runner is itself a production process under this ADR), [ADR-0096](0096-poga-refuses-a-launch-a-machine-cannot-host.md) (the refusal + fail-open + named-bypass shape reused here).
**Work items:** WI-0004 (the two-root requirement that blocks its design), WI-0148 (the devbox `machine_map` row, promoted from backlog nicety to hard prerequisite), WI-0025.

> **RENUMBERED 0103 → 0106 on 2026-09-02, and D3/D4 superseded.** This ADR was accepted on
> devbox as ADR-0103 while the Runner had, five days earlier, accepted a *different* ADR-0103
> on the same subject — [Promotion is a tag; the Runner runs a per-repo deploy
> contract](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md). Neither trunk
> could see the other: the consultant brief carrying the accepted design was delivered into a
> **gitignored** mailbox on the Runner ([ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md)),
> so it never travelled, and devbox designed a promotion path from the operator's 2026-08-22 machine-role
> ruling alone. That is [WI-0004](../work-items/) — shared Architect data living at a gitignored
> path — with a forked design record as its measured cost, and it is recorded there.
>
> the operator asked the consultant to review all three designs and choose. **ADR-0103 keeps the number
> and owns the promotion mechanism; this ADR keeps everything ADR-0103 has no counterpart for.**
> 0103 keeps the number because it is already baked into immutable history *outside* the
> federation — the release tags already cut in member repos under that contract and their tracked
> `deploy/deploy.json` contracts — while every citation of this ADR lives inside this repo and
> renumbers in one commit. **D1, D2, D5, D6, D7 and D8 below stand unchanged. D3 and D4 are
> superseded** (see the replacement text at D3/D4). WI-0195.

## Context

**The ruling exists; the mechanism does not.** On 2026-08-22 the operator ruled on machine roles:
both machines keep running for a while, and long term all development moves to devbox
while the Runner takes the production role. Correcting a first reading of it that had split
the fleet by *system identity*, he ruled that every system is developed on devbox, and any
system with a production mode that is more than a dev tool or dev environment comes to the
Runner to run.

**The axis is runtime, not identity.** Every system is developed on devbox; only a system
with a genuine production mode also runs on the Runner. That ruling is recorded in two work
items (WI-0004, WI-0148) and **nowhere else**. It has no ADR, no work item owning the
promotion path, no declaration a machine can read, and no verb. Eight days on, the only
machine-to-machine flow that exists runs in the *opposite* direction: devbox's gitignored
shared data (`users/`, `proposed-edits/`, `architect-learnings.md`) is a one-way copy taken
from the Runner with no sync back, so the Runner is the writer **by convention** — the
[P13](../principles/master.md#p13--single-writer-per-state) held by discipline that WI-0004
already exists to end.

### What the fleet cannot currently say

**Nothing declares a deployment class.** [ADR-0042](0042-deployment-classes-and-promotion-discipline.md)
defined three (local-tool / local-resident / cloud-deployed) and put the field in the
**retrofit report** — a report, not a declaration. Searched this session: `deployment-class`
appears in no `session.config.json` and no member config anywhere in the repo; where it
appears at all, it is in a retrofit report. The `binds-to` scope that ADR-0042
introduced *did* get built, in the habit registry with a `distill.py` exclusion — so the
class-scoping machinery exists while the class itself is undeclared.

That table is also stale. Its "Members today" column was written 2026-07-12 and predates
several of the systems now in [portfolio.md](../portfolio.md).

**The promotion discipline is already canon, and bound to the wrong class.** ADR-0042 §4 says
it plainly: iterate where failure is free, verify before exposure, promote deliberately,
rollback in one step. It binds those to **cloud-deployed** only, because when it was written
the local fleet had one machine and there was nothing to promote *to*. the operator's ruling creates
the identical problem one class over. This ADR does not invent a discipline; it gives the
existing one a local binding.

### The constraint that settles the direction

**devbox opens no connections to the other machines, by rule.** Recorded 2026-08-23: the
repo is the only channel between them, that isolation is deliberate, and it is not to be
worked around.

So **promotion is pull-side or it does not happen**. Nothing on devbox can push to the
Runner, and git is the only channel between them. This is not a design preference to be
weighed; it is the shape of the problem.

### The two-root requirement, already ruled

the operator's own correction to WI-0004 states the consequence that makes a single residency field
impossible:

> a member's data root is not one location per system, it is potentially TWO per system — the
> dev root on devbox and, for the subset that has a production mode, a production root on the
> Runner — and the resolver must not assume a single host per member.

[ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) is Accepted
and **Not-built**; its resolver, declaration reader and three-state detector landed in session
~157 (commit `f88b2a5`) built for exactly one root per member. That is the design change
blocking WI-0004's migration pass, and it is why this ADR must precede it rather than follow
it: building the one-root version first means building it twice.

### The federation is its own counter-example

The federation is a dev system by the ruling — and it runs a **production process on the
Runner**: the [ADR-0050](0050-headless-background-adoption-runner.md) adoption runner, a
nightly LaunchAgent that must live in the Runner's logged-in Aqua session because `claude -p`
on the subscription auth path needs the subscription login's token, which a LaunchDaemon and a
non-interactive `ssh` cannot reach. A system can therefore be dev-only in its domain and still
own a production process; and a member can be the mirror image — non-derivable
records that must stay on one machine, and no always-on process at all. **Neither is expressible as one fact per
member**, which is the whole argument for D1.

## Decision

**D1 — Residency is two declared axes, and absence is a third state, not a default.**
`session.config.json` gains a required `residency` block naming *machine labels*, never paths:

```json
"residency": {
  "process": "Runner",
  "data": "Runner"
}
```

- **`process`** — the machine that runs this system's production process, or `null` for *no
  production process*. `null` is a positive declaration, not a blank.
- **`data`** — the machine holding this system's **production** data root, or `null` for *no
  production data*, meaning the dev root on devbox is the only root.
- **Dev residency is not a field.** Every system is developed on devbox by the ruling; a
  per-member field restating a fleet invariant would be the
  [P16](../principles/master.md#p16--avoid-duplication) duplication ADR-0095 D3 just removed.

**A missing `residency` block means UNDECLARED, never "no production."** This is
[ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) D2's
empty-value rule and [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
applied here: folding "we never asked" into "there is nothing" is the collapse that produced
session 90's `inbox: (empty)` over two live briefs, and a promotion path is a worse place to
repeat it.

Worked examples of the shapes the design supports: the federation is `process: Runner` (the adoption
runner) with `data: null` (a dev system's data root is its dev root); a member with an
always-on service and data of its own would be `process: Runner, data: Runner`; a member that keeps
records on one machine but runs nothing would be `process: null, data: Runner`; a pure dev tool is
`null, null`. These are illustrative — **each member declares its own**, per D5.

**D2 — Values are machine-map labels, resolved per machine; never absolute paths.** The label
is looked up in the existing `machine_map`, so the same tracked declaration is correct on
every machine and no host's path prefix enters git. This is ADR-0095 D2's `$HOME` argument on
a second surface, and the fact that one disk can appear at a different path on each machine
that reaches it is why a literal cannot be used. **Consequence: WI-0148 stops being a backlog nicety and becomes a hard
prerequisite** — a member whose `machine_map` has no devbox row cannot resolve a residency
declaration that names one, and today only the federation's own config carries that row.

**D3 and D4 — SUPERSEDED by [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md).**
This ADR originally decided its own promotion verb: `poga promote`, running on the machine named
by `residency.process`, fast-forwarding the member's **existing checkout** to an immutable release
marker and printing a manual `git reset --hard` as its rollback. That decision is withdrawn. The
mechanism is ADR-0103's, which was accepted five days earlier on the other trunk and is already
built and tested: **promotion is `poga deploy`; the marker is the semver tag; the production tree
is the separate checkout `~/deploy/<system>`; and restart, smoke-before-restart, verify-after,
the ledger and automatic rollback are the deploy contract's.**

The substance of the withdrawal, so it is not re-argued: `poga promote` had no smoke gate, no
post-restart verification, no ledger and no automatic rollback, and its marker was a file's
introducing commit rather than a tag. Finishing it would have rebuilt — weaker — something that
already existed. The one genuine design difference between the two was **same tree or separate
tree**; when it was put to the operator, he delegated it
to the Federation Architect, and separate was chosen. Separate is also what turns **D6 below
from a `poga` guard into a filesystem property**, which is a strictly stronger form of this ADR's
own decision.

**What this ADR still owns, and ADR-0103 does not:** nothing on the deploy side declares *which*
machine is a system's production host or where its production data root lives — `deploy/registry.json`
hard-codes its systems by hand. D1/D2 (the declaration), D6 (the production host refuses dev work),
D7 (the two-root resolver) and D8 (the four-state detector) have no counterpart in ADR-0103 and are
unaffected by this supersession. **Consequence, filed rather than built here:** `deploy/registry.json`
should be *derived* from `residency.process == this machine` once members declare, instead of being
hand-written; the hand-written registry stands until then.

**The `restart` key is withdrawn from the residency block.** It was this ADR's way of saying how a
promoted process gets picked up; the deploy contract's own `restart` field owns that, and two
declarations of one fact is the [P16](../principles/master.md#p16--avoid-duplication) duplication
this ADR's own D1 argument rejects.

**D5 — The declaration is the member's; the convention and the verb are the federation's.**
Each member writes its own `residency` block, exactly as
[ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) D5 and
[ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D4 place the migration with
the member. The federation may not classify another system's production mode — that is the
system's fact and the [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) boundary.
It ships the convention, the verb, and the detector, and delivers a brief asking each member to
declare.

**D6 — A machine that hosts a system's production process refuses to *develop* it there.**
This is the operator's Option-A choice made structural rather than remembered. On a machine whose label
equals a member's `residency.process`, `poga` refuses lane creation, `resume` and `dispatch` for
that member.

**Operating is not developing, and the line is drawn deliberately.** A plain `session.py start`
— an ordinary Architect session — is **allowed** there, because a system with production data on
the Runner but no process there can only be operated where its data is. What is refused is
the concurrency substrate used to *build*: lanes, dispatch, resume. *You may operate here; you may
not develop here.*

**It fails open, and the bypass announces itself.** Both halves are ADR-0096's precedents. The
refusal fires only on a **declared** production host; UNDECLARED warns by name and passes, because
`session.py` ships byte-identical fleet-wide and blocking on "could not determine" would refuse
every launch on every member that has not yet declared. `POGA_ALLOW_DEV_ON_PROD=1` is named in the
refusal text and prints a line saying it was used, so a bypassed session is never mistaken for a
compliant one — ADR-0096 D7.

**D7 — ADR-0095's resolver gains a second root, keyed on the machine it is running on.**
Data-root resolution becomes: `POGA_DATA_ROOT` if set (unchanged, still the seam tests drive);
otherwise, if this machine's label equals `residency.data`, the **production** root; otherwise
the **dev** root. Both sit under `${XDG_DATA_HOME:-$HOME/.local/share}/poga/<system-id>/`, which
already differs per machine by construction — so the two roots need no second naming scheme and
no machine stores the other's prefix. A member with `data: null` has one root everywhere, which
is today's behaviour and needs no migration.

**D8 — Ship the detector with the capability.** `standard_check.py` gains a residency detector
reporting four distinct outcomes: **declared and resolvable** · **declared but unresolvable**
(names a machine absent from `machine_map` — the WI-0148 failure) · **undeclared** · **declared
and contradicted** (this machine is the production host, but the production data root does not
exist here). Four states, not a boolean, per
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
and ADR-0095 D6 — an exemption that defaults to "pass" does not merely fail to detect a gap, it
certifies it.

## Alternatives Considered

- **One residency axis — a system runs on the Runner, and its data follows the process.**
  Rejected by the operator this session. It cannot express the ruling he already made (two roots per
  member), and it resolves the records of any data-only member onto the dev box
  because those systems have no daemon to justify a Runner presence. The classification would be
  decided by a rule about processes and get the data wrong.

- **No checkout on the Runner — production is an installed artifact plus its data root.**
  Rejected by the operator this session. It buys a genuinely hard boundary, but at the cost of a
  packaging and install step for nearly every system, which have none, and it removes the source from the machine
  where a production failure has to be diagnosed. Since devbox cannot push, the artifact would
  travel through the git repo regardless — a checkout with extra steps for most of the fleet.

- **Push from devbox when a release is cut.** Rejected on the constraint, not on taste. devbox
  opens no connections to the other machines by rule; designing against that isolation is
  explicitly out of bounds.

- **Derive production mode from the ADR-0042 deployment class instead of declaring it.**
  Rejected. The class is declared nowhere machine-readable today, its member table is a fleet
  generation out of date, and the derivation gets the federation itself wrong — a dev system that
  owns a production process on the Runner. A rule whose first counter-example is the system
  writing it is not a rule.

- **Promote off `main` rather than a release tag.** Rejected — it collapses ADR-0042 §4's
  deliberate promotion into continuous deployment, and it makes rollback a reconstruction
  problem, since "the previous main" is not a name anything recorded.

- **Refuse *all* sessions on a production host, not just lanes.** Rejected — it would make
  a data-only member's production data unreachable by its own Architect, which
  is the opposite of what D1's data axis exists to protect.

- **Wait for WI-0004's migration and fold this into it.** Rejected — the causality runs the other
  way. ADR-0095's resolver assumes one root per member; that assumption is what this ADR
  overturns, so folding it in means building the one-root version and then rebuilding it.

## Consequences

- **WI-0004 is unblocked, and grows.** Its remaining migration pass now has a settled two-root
  design to build against instead of a moving one. The D7 resolver change is additive to the pure
  functions that landed in `f88b2a5`, not a rewrite of them.

- **WI-0148 is promoted to a prerequisite.** The devbox `machine_map` row must reach every
  member before any residency declaration naming devbox can resolve. It is a one-line addition
  per member and one redistribute brief, and it has been unblocked since 2026-08-22.

- **A new fleet-wide declaration lands, and it is asked for, not assumed.** Every system
  gains a `residency` block. Until a member declares, its detector reads **undeclared** and its
  launches warn rather than refuse — so the rollout degrades to today's behaviour rather than to a
  bricked fleet.

- **The one-way copy inverts, and must not be re-established.** devbox's shared data is currently a
  one-way copy from the Runner. For a dev-only system — the federation included — devbox becomes
  the writer under this ADR. That transition belongs to WI-0004's migrate verb, which moves the
  data once and leaves nothing behind; re-pointing the copy the other way would reproduce the
  same convention-not-mechanism defect facing the other direction.

- **The federation must declare honestly about itself.** `process: Runner` for the adoption
  runner means the federation's own repo on the Runner refuses lane work under D6 — correct, and
  the first place this ADR will be felt.

- **Risk: the transition period is explicitly two-machine.** the operator's ruling keeps both machines
  running "for a while," so a system may legitimately be developed on the Runner today. D6's
  refusal fires only on a declared production host and carries a named bypass; a member that has
  not yet moved simply declares `process: null` until it has.

- **Risk: `$HOME` under the LaunchAgent, again.** ADR-0095 already flags that the ADR-0050 runner
  and the `deploy/` LaunchAgent may carry a different environment than an interactive shell, and
  it was never empirically confirmed — a probe LaunchAgent was blocked by the harness classifier
  and the gap was recorded as a gap. D7 makes that unresolved question load-bearing on a second
  path, since the runner now runs on a machine where two roots exist and picking the wrong one is
  silent. **This must be measured, not reasoned about, in the build.**

- **Risk: a promotion during an open lane.** Mirrors ADR-0095's migrate hazard. `poga promote`
  must refuse while a lane on the production host holds a live session, using the liveness
  evidence `_tree_has_live_session` already reads, rather than discovering it afterwards.

- **ADR-0042 §4 stops being cloud-only.** Its promotion discipline now binds the local-resident
  class and any system with a declared production process. Its §3 cloud obligation set is
  untouched, and its §5 restraint clause (hold at three classes) is unaffected — this ADR adds no
  class.

## References

- [ADR-0042](0042-deployment-classes-and-promotion-discipline.md) — deployment classes and the §4
  promotion discipline this ADR binds to the local classes.
- [ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) — the declared
  data root; D7 gives its resolver a second instance.
- [ADR-0050](0050-headless-background-adoption-runner.md) — the Runner LaunchAgent that makes the
  federation its own counter-example.
- [ADR-0096](0096-poga-refuses-a-launch-a-machine-cannot-host.md) — the refuse / fail-open /
  named-bypass shape D6 reuses.
- [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) — federation-owned
  convention, member-applied migration; the D5 precedent.
- [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) — why the federation asks a
  member to classify itself instead of classifying it.
- WI-0004, WI-0148, WI-0025 — the items carrying the ruling, the prerequisite, and the
  third-machine substrate consequences.
