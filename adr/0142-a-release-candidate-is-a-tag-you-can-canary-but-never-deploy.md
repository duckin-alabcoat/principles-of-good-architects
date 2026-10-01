# ADR-0142 — A release candidate is a tag you can canary but never deploy

**Status:** Accepted
**Date:** 2026-09-17
**Session:** ~358 (lane, dispatch D-2a81c7)
**Deciders:** Federation Architect — all of it, under the operator's standing ruling of 2026-09-13 (*"Plumbing, mechanics, schema details, ordering, retries, which of two equivalent fixes: make the decision yourself, record it under Decisions I made without you"*). Nothing here changes what the system is for, what it costs, or who can reach it.
**Work item:** WI-0228, which carries the combined amendment WI-0233 was closed into.
**Source brief:** the 2026-09-03 consultant brief on machine-bound development, §Exit B (B1; B2 is deliberately out of scope here).
**Amends:** [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) **D2** (the tag vocabulary gains a candidate form that is matchable but never selectable), **D8** (the sweep's cadence: *"a nightly reconcile sweep"* is withdrawn — the substrate polls every 600s and has since the 2026-09-02 addendum), and **D9** (the gate stands and gains one named, unmeasured precondition).
**Reality:** **Built** — `--canary`, the widened pattern, the production refusal, 21 tests, 10/10 mutations caught. **Recorded, not built** — the D8 cadence correction describes substrate that already shipped. **Not built, and named as such** — the sweep/adopt-runner contention measurement under D5 below, which needs the Runner.

---

## Context

### Three facts, each verified in this lane rather than inherited

**The runner had no read-only path.** `deploy/runner.py` listed eight flags and none of them
was `--canary`; searching the repo for the word returned a test comment and two journal
lines. There was exactly one way to find out whether a release worked on the Runner, and it
was to deploy it. That is the shape the 2026-09-03 brief filed as Exit B: a session on
devbox that wants to know whether a candidate runs has no move that is not a promotion.

**The tag pattern could not spell a candidate.** `SEMVER_TAG_RE` was
`^v(\d+)\.(\d+)\.(\d+)$`. An `-rc.N` tag was not refused by it — it was *invisible* to it:
not selected, not rejected, not mentionable in an error message. There was no way to say
"try this one" because there was no such thing as a tag that is not a release.

**D8's cadence is stale prose.** D8 says *"a nightly reconcile sweep."* The substrate it
describes is `deploy/com.federation.deploy-sweep.plist.template`, whose `StartInterval` is
**600** (line 71), changed from nightly-at-04:30 by the 2026-09-02 consultant addendum with
its reasoning recorded in the template's own comment. The ADR was never updated, so the
canonical record and the shipped artifact have disagreed about the trigger for two weeks.
This is the `an-adr-premise-about-the-code-can-be-false-from-birth` class arriving by decay
rather than at birth: the sentence was true when written.

### The risk WI-0233 was the only record of

The 600s sweep may fire inside the 03:15 window in which the adoption runner
(`StartCalendarInterval` Hour 3 / Minute 15, verified in
`deploy/com.federation.adopt-runner.plist.template:45-50`) is working the fleet. Both write
git state; neither knows about the other; **that contention has never been measured.** It
is inert today only because D9 gates the sweep closed, which means it goes live on exactly
the day somebody decides the gate has been satisfied — the worst possible moment to
discover it. WI-0233 was closed into WI-0228, and this record is now its only home.

## Decision

### D1 — A canary is evidence, and evidence may not act as the system

`poga deploy <system> --canary <tag>` checks the tag out into a separate tree, runs that
tag's own `smoke` command there, records a verdict, and stops. It restarts no unit, cuts
nothing over, repoints no CLI link, and writes no byte of the production ledger.

**The separation is arithmetic, not care.** The canary root is derived from the deploy root
by appending an unconditional suffix (`~/deploy` → `~/deploy-canary`), so there is no
configuration in which a canary resolves to a production tree — including every
configuration a future fixture invents. The canary path never calls `checkout`,
`seed_state`, `restart_units`, `perform_cutover`, `repoint_cli_link`, `write_ledger` or
`post_result`; there is no `canary=True` boolean threaded through the deploy path, because a
boolean with a default is one wrong argument away from a probe that writes production. This
is [`evidence-is-separated-from-state-by-construction`](../habits/master.md#evidence-is-separated-from-state-by-construction)
read literally: *"must not be able to address live state at all — not merely be trusted not
to."*

**Three outcomes, two exit codes.** `passed` (0), `failed` (1), and **`inconclusive` (1)** —
a tag whose contract declares no `smoke` command has measured nothing, and a caller that
reads only the exit code must not see that as a pass. The deploy path is right to treat an
undeclared gate as non-failing, because a deploy does other things; for a canary the smoke
*is* the canary.

**It does not seed state**, and says so in its own verdict. `seed_state` copies from the
machine-local vault and writes a seed map — production-shaped writes performed on behalf of
a probe. A contract whose smoke needs seeded state fails here, and that is a limit of the
canary rather than a defect in the release. Naming the limit is
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes); folding
it into "failed" would be the collapse that habit exists to prevent.

### D2 — `vX.Y.Z-rc.N` is matchable, and for that exact reason must be unselectable

The pattern becomes `^v(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$`. **The admission and the
exclusion are one change, not two.** `newest_tag` is what the unattended sweep asks *"what
should be running?"*, so a pattern that merely *matched* a candidate would have made an
unfinished release the thing that deploys itself within ten minutes with nobody in the room
— D2's accidental promotion arriving through a regex instead of through a push. So
`newest_tag(tree)` excludes pre-releases by default and `prereleases=True` exists for the one
caller that is explicitly asking about candidates and restarts nothing.

`-rc.N` is the **whole** pre-release vocabulary this runner speaks. A candidate ranks below
its own release; `rc.10` beats `rc.2`.

### D3 — Production refuses a candidate by name, not only by resolution

`--version v2.0.0-rc.1` walks straight past `newest_tag`, and that is the route a *person*
takes. A rule enforced on one of two routes is a rule about which route was taken rather
than about the hazard, so `resolve_target` refuses a candidate outright and the refusal
names `--canary` as the path that does work. Cutting the release tag remains the promotion
decision (D2, unchanged); a candidate is by definition the tag that says that decision has
not been made yet, and deploying one would make it by typing.

### D4 — D8's trigger is a 600s poll, not a nightly sweep

D8's sentence *"a nightly reconcile sweep"* is **withdrawn**. Everything else in D8 stands
unchanged: two triggers, one path, the sweep is not a second implementation, and
`--rollback` is the same verb pointed at the ledger's `previous`. The substrate polls every
600s, and the reason it is safe to poll that often is D2's own: **the sweep is not the
promotion event.** The tag is. Polling more often shortens the delay between a deliberate
act and its effect; it does not create additional promotions. `RunAtLoad` stays false for
the mirror-image reason — a sweep that fires on login turns *"I rebooted"* into a
promotion.

### D5 — D9's gate stands, and the contention is now part of it

D9 is unchanged: the sweep is installed only after the on-demand path has succeeded twice
and a rollback has been drilled. Two things are added to it.

**The unmeasured contention is a named precondition.** Before the sweep is installed on the
Runner, measure what happens when a 600s fire lands inside the 03:15 adoption-runner window.
It is not enough to observe that nothing has gone wrong: nothing has *run*. This is
[`a-timing-claim-is-measured-never-read`](../habits/master.md#a-timing-claim-is-measured-never-read)
— "they probably do not overlap" is a claim with a truth value and no evidence behind it.

**A canary is not a rollback drill, and does not discharge D9.** It is worth saying because
the temptation is obvious: `--canary` is the first way to exercise a release on the Runner
without deploying it, and it will make D9's "succeeded twice" cheaper to reach. It proves
the *forward* path on a candidate. D9's requirement is about the *failure* path, which is
the half that has never run. Nothing below the rollback drill counts.

## Alternatives Considered

**Thread `canary=True` through the deploy path.** Smallest diff by far — `deploy_tree()`
gains a flag and six helpers pass it down. Rejected, and this is the load-bearing rejection:
it makes "a canary cannot touch production" true of the code as currently written rather
than true of the code. Every future edit to `_deploy` would have to remember the flag, and
the failure mode of forgetting is a probe that restarts production. A derived root is a
property of arithmetic that no future edit can forget.

**Put the canary tree at `~/deploy/<system>-canary`,** which is what the brief's B1 says.
Rejected on a collision that is concrete rather than stylistic: that scheme puts canary
trees and production trees in one namespace, so a system whose registry id ends in
`-canary` resolves to the same directory as another system's canary. It also puts the probe's
tree *inside* the production root, where anything that walks `deploy_root()` sees it. A
sibling root costs one directory and removes both.

**Let `--canary` take any tag and leave `SEMVER_TAG_RE` strict** — the brief's stated
alternative. Rejected for the brief's own reason, which is better than the one I would have
given: a candidate that ships unchanged should become the release by **re-tagging the same
commit**, and that only works if the candidate is a version rather than an arbitrary label.
A canary of `nightly-2026-09-17` produces a verdict that can be filed against nothing.

**Admit the full semver pre-release grammar** (`-alpha.2+build.7`). Rejected: every consumer
of the pattern would then have to decide what `-alpha` ranks against `-beta`, and this
machine has no use for that ordering. One shape ordered by one integer is a vocabulary the
comparison cannot get wrong. `v2.0.0-alpha.1` reads as *not a semver tag*, which is the
honest answer rather than a silent partial match.

**A git worktree of the production clone instead of a second clone.** Shares the object
store, so one fetch serves both. Rejected for the same reason ADR-0103 rejected it for
production, pointed the other way: the probe would share a `.git` with the tree production
runs out of, and a `checkout --force` in the canary is then one bad path away from the
thing it exists not to touch.

**Amend ADR-0103 in place** (the `## Amendment —` shape ADR-0053 and ADR-0059 use) rather
than drawing a new number. Rejected on a known failure: an in-file amendment leaves
`adr/README.md` with one row for two dates, and that is exactly the defect
`curate/check_substrate_docs.py` was built after — on 2026-09-11 ADR-0059's index row
carried its amendment's date instead of its own. The `**Amends:**` header is also the
dominant recent convention (0119, 0124, 0130, 0134, 0136, 0137).

**Mail the canary verdict home.** That is B2 of the source brief, it is somebody else's
item, and WI-0228's own body says to keep it out of scope. `--status` prints the verdict
instead, which is the minimum that stops the record from being written where nobody reads
it.

## Consequences

- **A release can be exercised on the Runner without being promoted there.** That is the
  capability Exit B asked for, and it is the first one on this machine that produces
  evidence about a version without changing what runs.
- **Tagging a candidate is now a safe act.** Before this, `v2.0.0-rc.1` on origin was a tag
  the runner could not see; after this it is a tag the runner can see and will not deploy.
  Both are safe; only the second is useful.
- **`poga deploy <system> --version <an rc>` now fails where it previously would have
  deployed.** That is a deliberate behaviour change to a path nobody could have used before
  this commit, since no rc tag existed for any registered system.
- **The canary verdict is machine-local, like the ledger and for the same reason** (D7). A
  devbox session cannot read the Runner's canary result until B2 ships. Until then the
  answer reaches a person by `poga deploy <system> --status` on the machine that ran it —
  which is a real limit, stated rather than glossed.
- **D9 is now harder to satisfy on paper and the same to satisfy in fact.** The contention
  measurement was always owed; it was recorded in a work item that no longer exists as its
  own row. Writing it into the gate is the difference between an obligation and folklore.
- **Downstream:** B2 (WI-0230's neighbourhood) makes the verdict travel. A per-system
  contract with no `smoke` command now has a second surface reporting that gap, which is
  `ship-the-detector-with-the-capability` working as intended rather than a new complaint.

## References

- [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) — the parent. D2, D8 and D9 are amended above; D1, D3–D7 and D10–D11 stand.
- [ADR-0141](0141-diagnosis-is-a-read-only-collector-that-publishes-on-a-state-change.md) — the other read-only Runner surface, landed hours before this one. Same posture: observe, never act.
- [ADR-0135](0135-a-trunk-clone-is-never-a-process-root.md) — why an unattended writer's working directory is load-bearing, which is the neighbourhood of the D5 contention.
- Consultant brief, 2026-09-03 — machine-bound development, Exit B.
- 2026-09-02 consultant addendum — the nightly→600s cadence change, whose reasoning lives in `deploy/com.federation.deploy-sweep.plist.template`.
