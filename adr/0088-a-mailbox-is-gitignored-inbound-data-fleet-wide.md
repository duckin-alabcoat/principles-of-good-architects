# ADR-0088: A mailbox is gitignored inbound data, fleet-wide

**Status:** Accepted
**Date:** 2026-08-04
**Deciders:** the operator (asked in plain terms whether a member's inbox should be part of their repo; answered *"yes"* to gitignored fleet-wide), another member's Architect (the survey and the discriminating question), Federation Architect (the ruling and the migration).
**Extends:** [ADR-0013](0013-receipt-ritual.md) (receipt ritual), [ADR-0027](0027-federation-delivers-edits-into-target-inboxes.md) (the federation writes into inboxes, never authored files).
**Unblocks:** WI-0072 (the `deliver` verb), which cannot be written until this is settled.

> *Public edition.* Individual members are not named here; the fleet survey is given
> without fleet totals, and each member that needed action is described only by the defect it had.

## Context

`proposed-edits/<architect-id>/pending/` has been the fleet's mailbox since
[ADR-0013](0013-receipt-ritual.md). What was never decided is whether that directory is
**part of the receiving system's tracked history** or **gitignored inbound data**. It was
left to each system, and the fleet answered it in several different ways.

One member's Architect surveyed every locatable member with one question — *if a brief
is written there, does git ignore it?* — and found:

| State | Members | Writing a brief there… |
|---|---|---|
| Ignored (data) | most, including the federation | is safe; nothing in git moves |
| **Tracked** | **2** | lands in their tree and is swept into their history |
| **Structurally unreachable** | **1** | fails — the path is a symlink loop |

**Re-probing the non-conforming members changed the survey, and the corrections are
the argument for D5.** The survey asked each member one binary question — *is it
ignored?* — which is the right question and could not express what was actually wrong:

- **One older member is not missing a mailbox; it has one at a path the survey could not look at.**
  Its declared inbox is `architect/proposed-edits/pending` (`session.config.json`), not
  `proposed-edits/<architect-id>/pending`. The directory exists, and
  `check-ignore` returns exit 1 — **tracked**, like the other tracked member. So that member moves from "no mailbox"
  to the harmful column, and a survey that assumes the convention cannot find a member
  that does not follow it.
- **The unreachable member's mailbox is a self-referential symlink.**
  `proposed-edits` is a link to an absolute path that resolves back to the link — it
  points at itself. `check-ignore` fails with *"beyond a symbolic link"* (exit 128) and `ls` returns
  `ELOOP: Too many levels of symbolic links (os error 62)`. It is not an empty mailbox and
  not an absent one; it is a directory that **appears to exist to any audit that only tests
  presence** and fails for anything that tries to use it.
- **A correlation worth carrying:** the same kind of window can also leave
  `.claude/settings.json` files corrupted in other repos. Recorded as a correlation to
  investigate, not an established cause.

The conforming members are carried from the survey and were **not**
re-probed here; only the non-conforming ones were, since those are the ones this
ADR acts on. Stated so the next reader knows which rows are first-hand
([`capture-the-probe`](../habits/master.md#capture-the-probe)).

**The tracked case is not untidiness, it is harm.** One tracked member's mailbox is tracked *and* its
session-end runs a sweep commit. A brief dropped there is picked up by **their** next
session and committed into **their** history under **their** authorship, in a commit they
did not intend and did not review. That is a change to another system's audit trail made
by a system with no authority over it — the precise thing
[P2](../principles/master.md#p2--system-artifacts-evolve-auditably) exists to protect, and
a violation of [P13](../principles/master.md#p13--single-writer-per-state) by the back door:
the sender never wrote to that member's history, but caused a write to it.

The rule the fleet had been carrying — *"never write inside an observed repo"* — is both
too strict and too loose. Too strict, because writing into a gitignored inbox perturbs
nothing and is how every successful delivery has worked. Too loose, because it says
nothing about the case that actually causes harm. **The discriminating question is not
"is this repo observed?" but "is this mailbox tracked?"** — and until now nothing anywhere
recorded the answer per member.

## Decision

**D1 — A mailbox is gitignored, fleet-wide.** `proposed-edits/` and everything beneath it
is inbound **data**, not system history. the operator's ruling, in plain terms: a mailbox holds
letters other people sent you; it is not a record of what you built. Tracking it means
anyone who writes to you edits your history.

This also puts the mailbox on the right side of the line
[ADR-0007](0007-github-as-system-storage-data-excluded.md) and
[P3](../principles/master.md#p3--data-system-separation) already draw, and makes
[`gitignore-enforces-data-system-boundary`](../habits/master.md#gitignore-enforces-data-system-boundary)
the enforcing mechanism rather than per-system discretion.

**D2 — Every member has a *reachable* mailbox, and reachability is tested by using it,
never by testing presence.** The self-symlink is the case that sets the rule:
it satisfies "does the directory exist?" and fails every actual write. A member that
cannot receive is not "quiet", it is **unreachable**, and unreachable reads identically to
nothing-to-say — the failure this ADR's whole neighbourhood keeps re-finding. The repair
is to replace the loop with a real directory; the durable part is that the conformance
check must attempt a write-and-remove, not an `is_dir()`
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes),
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)).

**D2b — The path is part of the convention, not just the ignore status.** The older member's mailbox
works but sits at `architect/proposed-edits/pending`. It is not broken and nothing about it
harms anyone, so it is **not** being moved by this ADR — churning a working path to satisfy
tidiness is exactly the over-reach [ADR-0023](0023-standard-operating-substrate.md) warns
against on the domain side. What D5 requires is that the registry record that member's *actual*
path, so the next survey finds it instead of concluding it has none.

**D3 — Already-tracked briefs are untracked, not deleted.** A member can carry briefs tracked
across `applied/` and `pending/`, because git keeps honouring
files that were already tracked when an ignore rule arrived. These are removed from
tracking (`git rm --cached`) and left on disk. Deleting them would destroy delivered mail
to fix a bookkeeping error, and the history that already records them stays — this is a
change to what git tracks *going forward*, never a rewrite of what happened.

**D4 — The convention is federation-owned, the migration is each member's to apply.**
The federation writes generated substrate into member repos
([ADR-0034](0034-settings-are-federation-pushed-generated-substrate.md)) but never their
**authored** files, and `.gitignore` is authored. So this ships as a receipt-ritual brief
per affected member, not as a push. Only the non-conforming members are affected (a tracked
mailbox or a symlink loop); the rest already conform and need nothing.

**D5 — Where each mailbox is, and that it is ignored, becomes recorded state.** The
survey above is a fact about the fleet that was expensive to establish and will rot
immediately. It belongs in the registry the `deliver` verb reads, so delivery is a lookup
rather than a guess and a non-conforming member is **detected** rather than discovered by
a sender. This is the prerequisite WI-0072 is blocked on.

## Consequences

**`deliver` becomes writable.** Its refusal conditions are now expressible: an unknown
member, a member with no mailbox, or a mailbox that is not ignored are three distinct
named failures instead of a silent successful write to the wrong place.

**The send-side guard gets a rule it can enforce.** *Refuse a write into a tracked
mailbox* is a mechanical check; *don't write inside an observed repo* never was.

**One member's history stays slightly wrong, on purpose.** Its existing commits that
swept briefs into their tree are not rewritten. The record shows what happened, including
this defect — which is what [P2](../principles/master.md#p2--system-artifacts-evolve-auditably)
asks for. D3 stops the bleeding; it does not pretend the wound never existed.

**This does not fix cross-member routing**, and should not be read as doing so. The
survey's Finding 3 — a finding about member B, discovered by member C, has no working
destination and currently terminates at the operator personally — is a separate problem that this
ADR only clears the ground for. Making the mailbox uniform means a relay is now *possible*;
it does not make one exist.

## Update note — D3's guarantee holds in exactly one checkout (2026-08-16, session ~155)

**D3 as written is a promise the mechanism cannot keep past one clone.** It says the briefs
stay on disk because they were untracked with `git rm --cached`, "the `--cached` is
load-bearing." That is true — and true **only in the checkout where the command runs**.
`--cached` is not a property that travels in the commit; the commit says *these paths are
gone from the index*, which is the entire point of it. Every **other** checkout still has
those files tracked, so when it pulls the untracking commit git does what it always does
with a tracked file deleted upstream: it deletes the working-tree file. D3's guarantee
inverts on propagation — the member who runs the command keeps its mail, and everyone who
syncs afterwards loses theirs.

**This is not an exotic second-machine case; it is the normal adoption path.** A member that adopted did so
in a worktree lane — correct, verified in the lane (`git ls-files proposed-edits` → 0, all
12 files present) — and the loss landed when the lane merged and the main checkout
fast-forwarded: `applied/` gone in its entirety (9 briefs), the 3 tracked briefs in
`pending/` gone, and the 2 that had arrived *after* the untracking still there. That last
detail is the tell: only the tracked ones died. Since every member adopts through a lane,
every member reaches this the same way.

**Reproduced first-hand before writing this note** ([`capture-the-probe`](../habits/master.md#capture-the-probe)) —
throwaway origin + two clones, mailbox seeded tracked, untracked in clone B, pulled into
clone A: the untracking clone kept 3 of 3 files with 0 tracked; the pulling clone finished
with **0 files on disk and the `applied/` directory absent**. The member's account is confirmed,
not relayed.

**Recovery.** This restores the working tree without re-staging, leaving the files in
exactly the state D1 wants — present and ignored:

```sh
git restore --source=<untracking-commit>^ -- <mailbox-path>/
```

Verified in the reproduction: 3 of 3 files back, `git ls-files` still 0, index still 0,
working tree clean (they are ignored, so they do not even surface as untracked noise).

**`git checkout <untracking-commit>^ -- <mailbox-path>/` is the wrong move**, and is named
here because it is the more familiar command. It restores the same bytes and **re-stages
them** — measured at 3 of 3 re-tracked — which re-tracks the mail and silently undoes this
ADR. A member reaching for the command it already knows gets a result that looks identical
and is the opposite.

**D6 — the restore is a step in the adoption instruction, not a recovery to be discovered.**
Recovery-on-demand was the cheaper option and is rejected: the failure is **silent** —
nothing errors, nothing warns, the mail is simply absent the next time someone looks — and
for `applied/` mail nobody may ever look, which is not safety but delay, since `applied/`
briefs are cited by work-item `source:` fields and that is exactly how the member found it (an item
citing `applied/2026-07-29-…` that was not there to read). A silent failure with a known
one-line fix belongs in the instruction that causes it, not in a footnote the affected
member has to already suspect. Cost is one line per brief; it closes the class fleet-wide
rather than per-discovery.

**Fleet state at the time of writing**, probed directly rather than carried from the
original survey ([`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)):

| Member | Tracked under its mailbox | State |
|---|---|---|
| an adopting member | 0 | Adopted; lost 12 briefs and recovered all 12. Any other checkout of it is still exposed and is its own to handle. |
| a second member | **5** (all under `applied/`) | **Has not adopted — still ahead of the trap.** Its brief must carry the D6 step. |
| the symlink-loop member | 1 — and it is `proposed-edits` **itself**, the self-referential symlink | **D2 still unrepaired.** Not a brief; the tracked entry is the loop. |
| the older member (D2b) | 0 | Mailbox at `architect/proposed-edits/pending` (D2b) and already ignored. Nothing owed. |
| the rest, including the federation | 0 | Conforming; nothing owed. |

**No decision is revised.** D1 (a mailbox is gitignored), D2, D2b, D4 and D5 stand
unchanged; this corrects a factual claim inside D3 and adds D6. Gitignoring the mailbox is
right and the reporting member did not ask to revisit it.

**Reported by** an adopting member's Architect, filed in its own work-item store on landing D3.
