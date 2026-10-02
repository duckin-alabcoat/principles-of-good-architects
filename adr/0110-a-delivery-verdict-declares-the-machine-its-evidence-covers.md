# ADR-0110: A delivery verdict declares the machine its evidence covers, and residency is declared rather than inferred

**Status:** Accepted
**Date:** 2026-09-04
**Deciders:** Federation Architect (the mechanism — the evidence-scope preamble, the travels/machine-local asymmetry that keeps UNDELIVERED sharp, the `resides` field, and the fourth verdict).
**Extends:** [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1 (a mailbox is gitignored inbound data, fleet-wide) — this ADR prices the consequence D1 did not.
**Builds on:** [ADR-0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md) D2 (residency values are machine-map labels, never paths), [ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) D2 (an empty value is UNDECLARED, never a default), [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) D1 (the outbox is tracked and mail transits `origin`) and D3 ("not locatable from this machine" is a normal outcome, counted separately from failure).
**Reality:** Built — the `OFF_MACHINE` state and its `Buckets` field, `this_machine`, `declared_elsewhere`, `_tracked_names`, `_travels`, `absence_is_not_evidence`, the `travels` argument to `classify`, the `by_design` split in `reachability`/`audit_status_line`/`_print_audit`, the probeable-rows split in `_registry_staleness`, and the `//resides` note plus the `resides: "Runner"` rows in `mailboxes.json`. Verified by running `--audit` on devbox while writing this record.
**Work items:** WI-0205 (the measured defect), WI-0004 (the upstream this does not fix), WI-0253 (reconcile's identical conflation, deliberately out of scope).
**Landed onto:** WI-0223's five-state taxonomy, which reached the trunk while this was being built — see D6 for how the two compose rather than collide.

## Context

**[ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1 gitignores every
member's mailbox, fleet-wide, deliberately** — a mailbox holds letters other people sent
you, and tracking it lets anyone who writes to you edit your history. That decision is
right and is not revisited here. What it did not price is the consequence: **a mailbox's
contents then exist on exactly one machine and never travel between machines.** Nothing in
the fleet recorded that, and the delivery audit was written as if a mailbox were a fleet
fact.

**The defect was measured in session ~174 and was caused by a fix
made the same session** — worth recording in that order, because the fix was correct.

Several members showed as UNREACHABLE from the dev machine because their
`proposed-edits/<architect-id>/pending/` directories did not exist here — the fresh-clone
gap: `proposed-edits` is gitignored in every one of those repos, so a machine that clones
A member gets a member with no mailbox. Creating the directories fixed **reachability**:
`--audit` reported fewer unreachable members, and `--probe` reported `reachable yes` for
each one created.

**With the mailbox now readable, the audit immediately produced a confident wrong answer.**
It reclassified a brief from one member to another as *"UNDELIVERED — absent
from the recipient's mailbox, which was read."* It is not undelivered. Its own frontmatter
says it was delivered on the Runner. It was delivered **on the Runner**,
into a gitignored directory, and devbox cannot see that and never will. So the fix
converted an honest UNREACHABLE (*we cannot tell*) into a confident and **wrong**
UNDELIVERED — and the obvious next action, deliver it, would have duplicated a brief the
recipient already holds. It was not delivered, deliberately.

That is the sharper failure. UNREACHABLE was useless but honest; UNDELIVERED was useful
and false, and its usefulness is what makes it act.

**Second symptom, same root, and it prescribes.** The audit reported members that live only on the Runner — a class the layout supports —
as *"UNREACHABLE — nothing can be delivered to these at all"* with
the remedy *"map it in repo-paths.local"*. That advice is wrong on devbox.
A machine-local `reconcile-roots.local` can record that the dev box is the development machine and carries only
the devbox-column repos, and that the Runner-side roots are **deliberately absent** — *"a
smaller fleet view here is the mechanism working, not a gap."* Their repos are genuinely
not on this disk, so following the advice would require **fabricating a path to a repo
that does not exist here**. Wrong advice is worse than no advice, because it is
actionable.

**And the fact that would have prevented it lived only in a prose comment.** The
deliberate-absence statement above is a sentence in a gitignored config file, addressed to
a human. No check can read it. So the sweep folded *deliberately elsewhere* into the same
bucket as *broken* — not because the information was missing from the fleet, but because
it was written in a form no mechanism could consult.

**Where this sits against [ADR-0108](0108-a-read-names-the-tree-and-commit-it-read.md).**
That ADR's D6 names WI-0205 as one of four surfaces converging on its D1 provenance floor,
and this is that convergence — but on a different axis. ADR-0108 D1 asks *which tree, at
what commit*; the delivery audit's blindness is *which machine's disk*, and no commit
answers it, because the evidence in question is by construction not in git. So `_print_audit`
does not render through `provenance_clause()`; it states its own scope in the same shape,
for the same reason. Stated plainly so the next reader does not record this as the D2
helper having a fourth caller.

## Decision

### D1 — A delivery verdict states the machine its evidence covers

`--audit` prints an **evidence-scope preamble** before any verdict, naming this machine and
the property that bounds what it can know:

```
Evidence scope: DevBox. Member mailboxes are gitignored (ADR-0088 D1), so their contents
are MACHINE-LOCAL and a delivery made elsewhere leaves no trace this machine can read.
DELIVERED and UNDELIVERED below are therefore this-machine verdicts; a brief whose own copy
travels between machines is reported OFF-MACHINE rather than undelivered.
```

It is a preamble rather than a footnote because the reader acts on the first list they
read. It names the *fleet's* property, not this sweep's limitation: what a mailbox held on
another machine is unknowable from here **by construction**, and saying so is
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) applied
to the one assumption this tool had never written down.

`this_machine()` reuses the harness's `detect_machine()` rather than reimplementing the
`scutil --get ComputerName` → `machine_map` lookup ([P16](../principles/master.md#p16--avoid-duplication)),
so the audit names itself with the same label every stamp, banner and residency check in
the fleet is written against. **It returns `""` when it cannot tell, and that is
load-bearing** — a machine that cannot name itself must not be able to *excuse* an absent
member as elsewhere-by-design. Not knowing where you are standing is a reason to withhold
that verdict, never to grant it.

### D2 — Absence is evidence only when our own copy is machine-local

The discriminator is **asymmetry**, and it is machine-checkable.

| Our copy of the brief | Their mailbox | What an absence means |
|---|---|---|
| Tracked (travels) | Gitignored (machine-local) | **OFF_MACHINE** — a delivery made anywhere leaves both sides looking exactly like this |
| Machine-local (gitignored) | Gitignored (machine-local) | **UNDELIVERED** — this machine is the only possible sender |
| Any | Tracked | **UNDELIVERED** — a delivery from anywhere is committed and visible from here |
| Unknown (git could not answer) | Gitignored | **OFF_MACHINE** — `None` in, `None` out |

The sending member keeps its outbound mail in `outbox/`, which is **tracked** ([ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
D1 makes that deliberate — a gitignored directory cannot cross `origin`). So devbox can
read the sender's copy and can never read the recipient's mailbox as it stood on the Runner. The
audit read the copy, read an empty mailbox, and asserted a global fact from one-sided
evidence.

**This deliberately does not gut UNDELIVERED.** The converse row is what keeps it sharp: a
brief sitting in our own gitignored `proposed-edits/*/pending/` is machine-local too, so
this machine is the only machine that could have sent it, and `_retire_source` would have
moved it out of `pending/` if it had. There, absence really is evidence and the verdict
stands, unhedged. **The softening fires only where the evidence is one-sided — the
member-to-member case that was wrong.**

**The probe is lazy, because `audit_status_line()` runs on the SessionStart path.** The
travels question is only ever asked on the one branch about to say UNDELIVERED, and the
answer is memoised per directory per run, so N briefs sharing a mailbox cost at most one
`git ls-files`. The first cut probed every directory
eagerly and cost **a subprocess per mailbox directory every session** to change a single verdict —
several times the cost of the rest of the audit. Lazy, it is at most one subprocess.
`_registry_staleness()` in the same file already records why a check that shells out per
row has no business at startup; adding one there while quoting that rule would have been
the same mistake with a fresh motive.

A directory in no git repository at all answers with an empty set, not `None`: nothing
outside git travels by git, so that is an established fact rather than an unknown. `None`
is reserved for git genuinely failing to answer and must never collapse into either real
answer. The set holds **direct children only** — `ls-files` reports paths relative to the
directory, so a tracked `applied/foo.md` would otherwise contribute the bare name `foo.md`
and mark an untracked `foo.md` beside it as travelling, which is the filename-matching this
audit exists to refuse, one directory down.

**One seam is named rather than closed.** [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
D6 gives the federation's own outbox a receipt that *does* travel: `outbox/to-<X>/` is
queued, `outbox/delivered/` is delivered, and the move is the record. For mail under that
convention, queue position is tracked evidence and an absence really would mean
undelivered — so D2's rule is more conservative than it needs to be there. It is left that
way deliberately: this sweep does not currently reach the federation's own ADR-0107 outbox,
that member's `outbox/` runs the older convention (it stamps frontmatter and leaves the file in
place, so its queue position is not a receipt), and being conservative can only ever
understate — *cannot tell* where *undelivered* was warranted, never a false accusation.
Refine it when a case arises rather than on anticipation
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).

**A `delivered:` frontmatter stamp is corroboration, never a receipt.** Where one exists it
is quoted into the UNDETERMINED reason — *"which is the sender's self-report, not a
receipt, and is quoted as corroboration rather than relied on"* — and the verdict is
computed without it. This is
[`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence):
the only valid receipt that a ritual ran is the artifact its own closing code produces. The
recipient's mailbox is that artifact. A sender's line in its own file is the claimed
sentence, and promoting it to proof would rebuild the trust decision this audit exists to
replace with a file test.

### D3 — Residency is declared in `mailboxes.json`, never inferred

A member's row gains an optional `resides` field carrying a **machine_map label** — `"Runner"`,
never a path. A row carries it only where the member is declared to live on one other machine.

**A label and not a path is what makes it safe in a tracked file.** One disk can appear at a
different path on each machine that reaches it, so a literal path is wrong on some machine by construction, while
the label resolves correctly on every one of them
([ADR-0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md) D2,
[P3](../principles/master.md#p3--data-system-separation)).

**Absent means UNDECLARED, never "lives everywhere."** A row with no `resides` that cannot
be found stays an ordinary unreachable member — the gap it is. That is
[ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) D2's
empty-value rule, and the reason it is not negotiable here is
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability):
an exemption that defaults to *pass* does not merely fail to detect a gap, it actively
certifies one. Absence of evidence is exactly the symptom the two states share, so the tool
must never infer the excuse from it.

**Both conditions are required** before the new verdict is granted: the row must **declare**
another machine **and** the repo must **actually be absent here**. A member declaring
`resides: Runner` that is nonetheless sitting on this disk falls through to the normal
resolve path. `declared_elsewhere()` also returns `""` when the declared machine *is* this
one — a member that claims to live here and cannot be found here is a real gap, not a
design.

**Why the tracked registry and not a machine-local file.** Where a member's repo lives is a
**fleet** fact: it is true whichever machine asks, so it wants one tracked source. A
per-machine copy is the hand-maintained duplicate [P16](../principles/master.md#p16--avoid-duplication)
forbids, and devbox's copy and the Runner's copy could disagree with each other silently
— which is the failure this ADR is repairing, reintroduced one layer down. The prose
comment in `reconcile-roots.local` was already the machine-local version of this fact, and
its defect was not that it was wrong but that only a human could read it.

### D4 — "Elsewhere by design" is its own verdict, not a kind of broken

`reachability()` returns `Unreachable(..., by_design=True)` for a declared-elsewhere member,
and the report splits on it: an **ELSEWHERE BY DESIGN** section that says *"lives on Runner,
by declaration (DevBox does not carry this repo). Nothing is missing here and there is
nothing to map — deliver to it from Runner,"* and an **UNREACHABLE** section for genuine
defects. `audit_status_line()` filters `by_design` out of the SessionStart count entirely,
while `--audit` keeps the rows visible under their own heading.

Both states are *"cannot be written to from here."* The split is **DEFECT vs EXPECTED** —
the same distinction [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md)
D3 already draws for the poller, where *"not locatable from this machine"* is a normal
outcome counted separately from failure.

**The payoff.** The UNREACHABLE list keeps only genuine defects — a committed
self-referential symlink, say — which were previously listed last among the non-problems. A report that lists non-problems trains its reader to
skim it, and the one row that matters is the one the skimming loses.

### D5 — A warning whose remedy this machine cannot perform is not that machine's warning

`_registry_staleness()` measured the oldest `verified` date across **every** row and told
the reader to run `curate/deliver.py --probe`. On the dev machine the oldest rows were
exactly the members declared to live on the Runner
— and `write_probe_results()` only advances `verified` where a probe actually determined
something. So the dev machine printed *"mailbox registry 19d stale, run `--probe`"* at every
session start, and running `--probe` would have changed nothing, for ever.

That is D1's principle applied to the status line rather than the verdict list: an alarm
that cannot be cleared by the command it names teaches its reader to skip the line, and the
line is the only delivery signal most sessions ever see. Staleness is now measured over the
rows this machine **can** probe, and rows it cannot get their own clause naming the remedy
truthfully — *"can only be re-probed from the machine they live on"*, which is a machine,
not a command. Excluding them silently was not an option: that would be
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) failing
in the other direction, dropping a fact rather than overstating it.

Found while checking whether `--probe` shared the defect this ADR fixes in `--audit`. It
did, one function over.

## Alternatives Considered

- **Weaken UNDELIVERED to UNDETERMINED whenever the recipient's mailbox is gitignored.**
  That is every mailbox in the fleet by ADR-0088 D1, so the verdict would never fire again.
  The audit's entire value is the sharp verdict; D2's asymmetry test is what preserves it
  while removing exactly the case that was wrong.
- **Trust the `delivered:` frontmatter stamp as a receipt.** It would have given the right
  answer on the one brief that motivated this, which is precisely why it is tempting. It is
  the sender's self-report about someone else's disk, and building a verdict on it is the
  trust decision `a-close-is-the-banner-not-the-sentence` exists to refuse. It is quoted as
  corroboration instead.
- **Infer residency from absence — "if the repo is not here and the roster says it exists,
  call it elsewhere."** This is the `ship-the-detector-with-the-capability` failure written
  out: absence is the symptom deliberately-elsewhere and genuinely-missing share, so the
  inference cannot distinguish them and would certify every real gap as by-design.
- **Put `resides` in `repo-paths.local` or `reconcile-roots.local`.** Rejected in D3: a
  fleet fact in a per-machine file is a duplicate that can disagree with itself, and both
  files are gitignored, so the Runner would have to be told separately what devbox already
  knows.
- **Read `reconcile-roots.local`'s prose comment.** Parsing an English sentence written for
  a human, in a file whose format is a list of walk roots, to recover a fact nobody declared.
  The comment stays; it is now a description of a declaration rather than the only copy of one.
- **Teach the audit to read the other machine's mailboxes over SSH.** Forbidden by
  [ADR-0103](0103-promotion-is-a-tag-and-the-runner-runs-a-deploy-contract.md) D1 — the git
  remote is the only bridge, and neither machine holds credentials for the other — and it
  would answer the question by removing the constraint rather than by reporting honestly
  inside it. The real removal is WI-0004, below.
- **Do nothing and let the operator remember which machine they are on.** This is what was
  in place. It produced a delivery instruction that would have double-sent a brief, and the
  only thing that stopped it was a human noticing a date in a frontmatter block.

### D6 — The verdict is a sixth state, on WI-0223's own axis

This ADR was designed against a three-state audit and landed onto a five-state one:
[WI-0223](../work-items/) split `UNKNOWN` into **UNKNOWN** (*could not tell this run — a
named blocker, fixable, re-runnable*), **UNRESOLVABLE** (*no content key exists at all, and
no re-run will ever produce one*) and **CORROBORATED**, hours before this landed. Its axis
is exactly the right one to ask here: **will a re-run ever answer this?**

That axis has a third position it had not needed a name for. A brief blocked by the machine
boundary **has** a content key, no re-run on *this* machine will ever settle it, and another
machine could settle it today. So:

- Reporting it **UNKNOWN** sends the reader back to a check that cannot answer here — the
  precise failure WI-0223 had just removed, re-introduced from the other side.
- Reporting it **UNRESOLVABLE** is worse: it *is* resolvable, and the remedy is a machine,
  not an amended brief.

Three distinct next actions — *fix the blocker and re-run*, *ask the sender to amend*, *run
the audit where the disk is* — so three states. `OFF_MACHINE` prints under its own heading
and carries its own clause in the startup line.

**It does not fail the exit code**, and that asymmetry is deliberate on WI-0223's own
act-or-not test: an unresolvable brief has a remedy the exit code exists to press for, while
nothing an operator on this machine can do changes an off-machine verdict. Failing every
session on a fact about a different disk is the crying-wolf failure in its purest form. The
same reasoning excludes a by-design unreachable from the alarm count.

`Buckets` absorbed the sixth field without incident, which is that type's own docstring
paying off — it was made a NamedTuple precisely so *"the next state added must break loudly
at a missing attribute, not quietly at an index."* `_split` now builds it **by keyword**, so
the next insertion cannot silently renumber the rest either.

## Consequences

**The SessionStart line stops crying wolf.** Before:

```
Delivery audit: N recipient(s) unreachable; 1 undelivered; 2 undetermined; 4 delivered leftover(s)
```

After, on the WI-0223 taxonomy:

```
Delivery audit: N row(s) last verified 19d ago can only be re-probed from the machine they live on; 1 recipient(s) unreachable; 1 answerable only from another machine; 2 corroborated by the sender's own note; 4 delivered leftover(s) to file — run `python3 curate/deliver.py --audit`.
```

Unreachable rows that are the layout working correctly no longer count as defects, and a
one-sided undelivered is no longer asserted. What remains is genuine defects, a few honest *cannot tell*s,
and — per D5 — a staleness clause that now names a machine instead of a command that
would not have helped.

**This machine now knows it cannot answer a question it used to answer wrongly, and that is
the cost.** The member-to-member brief has moved from a verdict to a question, and no
amount of work on devbox will resolve it — the answer is on the Runner's disk, in a
directory that by design never travels. An operator who wants certainty must ask from
there. Trading a false answer for an acknowledged gap is the right trade and it is still a
loss of coverage, not a repair.

**This is a truthful report of a limitation, not a fix for it.** The real end of this is
**WI-0004** — mailbox state is shared Architect data living inside each repo at a gitignored
path, which is exactly the two-copies problem the [ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md)
data-root move exists to end. When mailbox state lives under a declared data root, the
asymmetry D2 tests for disappears and D1's preamble becomes a historical note. Until then,
every verdict this tool prints is a this-machine verdict and now says so.
[ADR-0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md)'s own
renumbering was caused by the same gap: a consultant brief carrying an accepted design was
delivered into a gitignored mailbox on the Runner, never travelled, and devbox designed a
second promotion path in ignorance of it. This ADR makes that class of blindness *legible*;
WI-0004 removes it.

**`reconcile.py` has the same conflation and is deliberately not fixed here.** Its
UNLOCATED state ([ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md))
folds *deliberately on another machine* into *not reachable from this machine* in exactly
the way `reachability()` used to. It is a separate work item (**WI-0253**) and not a copy of this patch,
because reconcile keys off the **portfolio.md roster** (`roster - set(found)`), not the
mailbox registry — so `resides` is not in the data it reads, and deciding where the
declaration belongs for a roster-driven sweep is its own question. Named here so the
next reader knows the omission is a decision rather than an oversight.

**A fourth verdict is a fourth thing to keep true.** `mailboxes.json` now carries a claim
about the fleet's physical layout, which can go stale like any other row in it — a repo
that moves to devbox leaves a `resides: "Runner"` behind that will silently excuse a real
absence. The `verified` discipline (`--probe`, and the staleness clause in the status line)
covers mailbox reachability, not residency. That is an accepted, named gap.

**The registry's `//resides` note carries the rule for readers who reach the data
before the record.** It carries the same rule as D3 — label not path, absent means undeclared —
because a schema whose constraints live only in an ADR gets edited by someone who never
read it.

## References

- **WI-0205** — the measured defect: the 2026-08-30 reachability fix, the wrong UNDELIVERED it produced, the wrong `repo-paths.local` advice, and the renumbering from WI-0177.
- **WI-0004** — shared Architect data at a gitignored path; the upstream this ADR reports rather than repairs.
- [ADR-0088](0088-a-mailbox-is-gitignored-inbound-data-fleet-wide.md) D1, D2, D5 — the gitignored mailbox, reachability-by-use, and the registry this decision extends.
- [ADR-0106](0106-local-promotion-process-and-data-residency-are-declared-separately.md) D1/D2 — residency is declared, values are machine-map labels, and a missing block means UNDECLARED.
- [ADR-0095](0095-shared-data-lives-outside-the-repo-under-a-declared-data-root.md) D2 — the empty-value rule and the declared data root that ends the two-copies problem.
- [ADR-0107](0107-mail-transits-origin-and-every-machine-drains-its-own-share.md) D1/D3 — the tracked outbox (the "our copy travels" half of D2's asymmetry) and "not locatable here" as a normal outcome.
- [ADR-0108](0108-a-read-names-the-tree-and-commit-it-read.md) D1/D6 — the provenance floor this is the machine-axis sibling of.
- [ADR-0037](0037-repo-path-locator-map-unlocated-is-not-unonboarded.md) — reconcile's UNLOCATED, which carries the same conflation and is out of scope.
- [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes), [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability), [`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence).
- `curate/deliver.py` — `this_machine`, `declared_elsewhere`, `_tracked_names`, `_travels`, `absence_is_not_evidence`, `classify`, `reachability`, `audit_status_line`, `_print_audit`.
- `mailboxes.json` — the `//resides` note and the `resides: "Runner"` rows.
