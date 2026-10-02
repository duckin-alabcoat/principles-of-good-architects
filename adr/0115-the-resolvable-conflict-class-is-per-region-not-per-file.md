# ADR-0115: the resolvable conflict class is per REGION, not per file; `ROADMAP.md` rejoins it conditionally

**Status:** Accepted
**Date:** 2026-09-04
**Deciders:** Federation Architect (the region-scoped predicate, the stage-1 comparison, keeping the roadmap out of `_generated_view_names()`). the operator (authorized the fleet change under grant `G-5cebd3`, origin interactive, scope `close, land, canon, standard, fleet`; the design call was delegated to the lane per *decide-and-record*).
**Supersedes in part:** [ADR-0098](0098-a-lane-repairs-main-and-clears-its-own-blocked-rebase.md). D6, D8 and D9 stand unchanged. **D7 — the blanket exclusion of `ROADMAP.md` from the resolvable class — is superseded**, on the grounds that its stated premise was itself reversed by [ADR-0105](0105-roadmap-is-a-compiled-trunk-only-view.md) D4.
**Builds on:** [ADR-0102](0102-integrate-merges-a-diverged-trunk-never-replays-it.md) (which re-expressed [ADR-0097](0097-a-lane-integrates-the-trunk-with-its-remote.md) D4's class per PATH rather than per COMMIT — this is the next refinement of the same rule), [ADR-0105](0105-roadmap-is-a-compiled-trunk-only-view.md) (the roadmap is a trunk-only compiled view; `## Now` stays hand-authored)
**Related:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) (WI-0267), which landed on this trunk while this lane was working, is the *other* half of the same pain and does not overlap: it serializes the land **gate** so concurrent lands stop starving each other's CAS, while this decides what the rebase does once a land gets its turn. A lane landing behind another lane was paying both costs, and neither fix subsumes the other. WI-0260 (a lane's re-render can itself gut the compiled region) stays open and is argued from here rather than closed by it.
**Reality:** Built. Ships in `session.py`, which is byte-identical fleet-wide, so it reaches every member at the next substrate push.
**Work item:** WI-0252

## Context

### A decision outlived its reason

[ADR-0098](0098-a-lane-repairs-main-and-clears-its-own-blocked-rebase.md) D6 gave a
blocked lane a bounded way out: a stopped rebase whose conflict is confined to this
member's **generated views** is resolved by taking the trunk side, because those files are
*derived* — the recompile at the end of the very land this unblocks rewrites them, so the
side taken here is overwritten before anyone reads it.

D7 then excluded `ROADMAP.md` from that class, in as many words:

> **`ROADMAP.md` is deliberately NOT in the class** … It is authored prose with one writer
> per lane, and taking the trunk side would silently drop a lane's roadmap edit.

That was correct on 2026-08-22. Eight days later, [ADR-0105](0105-roadmap-is-a-compiled-trunk-only-view.md)
D4 decided the opposite fact: `ROADMAP.md` became a trunk-only compiled view, and
`STANDARD.md` session-end step 5 now tells every Architect **"do not hand-edit
`ROADMAP.md`"** in those words. *One writer per lane* stopped being true. Nothing went
back to re-read D7, so the exclusion survived its own justification.

### The cost is close to the default, not an edge case

`_generated_view_names()`'s own docstring makes the argument that decides this, about the
files it *does* include:

> the view commit is the LIKELIEST shape of the divergence, not an edge case: every lane
> land ends with `_recompile_main_checkout` committing these two paths straight onto the
> trunk, so two machines that both landed have two commits rewriting the same generated
> files, and a reconciliation that refused on them would refuse on essentially every real
> integrate.

That argument is now true of `ROADMAP.md` verbatim. Of the last 50 commits touching the
file, **44 carry the identical harness subject** `docs(roadmap): re-render generated
sections from the work-item store`, and the last hand-edit-shaped commit predates
ADR-0105. So a lane's roadmap divergence is almost always the harness's *own* re-render —
precisely the shape D7 refuses on. Measured at session ~190 (lane poga-20): a lane whose
only authored work was in `curate/` and `tests/` could not land, because a sibling had
re-rendered the roadmap seconds earlier. The printed remedy — *"this one needs a mind, not
a policy"* — routed a mechanical, argued no-op to a human, every time.

### And the tempting fix is still a trap

ADR-0105 rejected whole-file auto-resolution outright, and that rejection still stands.
`ROADMAP.md` is generated **in regions**, not in whole. Alongside its four rendered
regions it carries four hand-authored surfaces: the as-of block, `## Now` (ADR-0105 D3),
the open-policy-questions block, and `### Earlier outcomes — frozen` — 241 lines that
ADR-0105 deliberately kept verbatim rather than migrating, *because prose with only one
copy gets silently lost*. Putting the file in the class would put all four under an
automatic trunk-side take. [WI-0260](../work-items/) sharpens the same warning from the
other direction: a lane's re-render can itself be **destructive** (commit `1da47e4`
replaced five compiled outcome entries with the empty placeholder and committed itself),
so the safety argument must rest on *the trunk side is taken*, never on *the two sides are
equivalent regenerations*.

Both facts are true at once. The class was too narrow for the mechanical case and too
coarse for the authored one. The answer is to make it finer, not wider.

## Decision

**The resolvable class is expressed per REGION where a path is generated in regions.
`ROADMAP.md` rejoins it conditionally.**

### D1 — one predicate answers "may this conflict be resolved?", for both reconciliations

`_conflict_is_resolvable(rel, policy, cwd)` replaces the name-membership test used
independently by `_resolve_rebase_conflict` (the land's rebase) and `_merge_onto` (the
integrate's merge). One vocabulary for one rule, as ADR-0098 D6 already required; this
keeps it as the two memberships diverge. A policy other than `generated` admits nothing,
answered **in the predicate** rather than by handing it an empty set — an empty set would
have switched the views off while leaving the roadmap branch live, and only through
`_merge_onto`, which has no early return on `none`.

### D2 — a wholly generated view qualifies unconditionally; the roadmap qualifies on evidence

The compiled handoff and `STATUS.md` are re-derived byte-for-byte, so no inspection is
needed. `ROADMAP.md` is admitted only when the side being discarded changed nothing
**outside** the generated markers, using the existing `_strip_generated_regions`. A lane
that really did edit `## Now` still refuses, by name, with the same `poga resume` verb
ADR-0098 D8 specifies. This is the honest predicate: the refusal now fires exactly when a
lane broke the standard's own rule, and not otherwise.

### D3 — the comparison is the DISCARDED side against the BASE, not side against side

Stage 3 is the discarded side in both reconciliations — `--ours` is stage 2, the trunk in a
rebase and our own trunk in a merge — so one comparison serves both. It is made against
stage 1, the common base, because that asks the question the safety argument actually
rests on: *did the side we are giving up author anything?*

Comparing the two sides instead would refuse whenever the **trunk** had independently
edited `## Now` — which is exactly where `## Now` is supposed to be edited (ADR-0105 D3),
and a divergence that loses nothing. That variant would have reintroduced the failure this
ADR removes, on the trunk lanes most need to land onto.

Every unreadable case is **fail-closed**: a missing stage (added on both sides, so there is
no base), a git error, or any exception returns False and the caller refuses. The cost of a
wrong `True` is deleting prose that exists in no other file.

### D4 — the roadmap is NOT added to `_generated_view_names()`, and that is load-bearing

The obvious implementation — a third entry in the set — is rejected. That set has a second
consumer with a much sharper edge: `_lane_adds_nothing_the_trunk_lacks` treats a member of
it as contributing nothing and on that basis **deletes a lane branch**. Membership there is
safe for a file every byte of which is re-derived and unsafe for one generated only in
regions: a lane holding a real `## Now` edit would read as empty and be discarded. The
widening would have become a way to lose the exact prose the conditional membership exists
to protect. A test asserts the roadmap's absence from that set, so the shortcut cannot be
taken back later by someone reading only this ADR's headline.

### D5 — the roadmap path becomes a declared layout key

`ROADMAP.md` was a hardcoded literal in six call sites while its two sibling views resolved
through `layout_of`. `_harness_owned` is the clearest evidence: it named all three in one
expression and resolved exactly two. This violates the WI-0029 rule the `layout_of` block
states in its own comment — *"Nothing else may name these paths as literals"* — and it is
the mechanism of the drift: `_generated_view_names()`'s docstring says it reads from CFG
"so the two cannot drift into disagreeing about what is generated", and the roadmap drifted
anyway, because it was never in the CFG pair to read.

`LAYOUT_DEFAULTS` gains `"roadmap": "ROADMAP.md"`, read through one helper that resolves
it **against `ROOT`, not `CFG`**. That distinction cost a regression on the way in and is
worth stating: `CFG`'s paths are absolutised once at import, while `ROOT` is what a caller
running in a lane has actually been repointed at, and the two disagreeing is the normal
condition in this substrate rather than an exotic one. Since `wi-render` *writes* this
path, reading the stale one is not a wrong answer but a write to the wrong tree. The
default is today's exact file, so no member's behaviour changes until it declares one.

## Alternatives considered

**Add `ROADMAP.md` to `_generated_view_names()` (the file-scoped fix).** About ten lines
shorter and the shape WI-0252 first reached for. Rejected: it trades the four hand-authored
surfaces — including 241 lines of single-copy prose — for those ten lines, and it arms
`_lane_adds_nothing_the_trunk_lacks` to delete a lane on the strength of a roadmap edit
(D4). ADR-0105 already rejected whole-file auto-resolution and nothing has changed that.

**Leave D7 standing and fix the collision structurally instead.** This is what D7 pointed
at (WI-0108), and WI-0108 *shipped* — as ADR-0105. The structural fix landed and the
refusal remained, because the refusal was keyed on a file name and not on the fact that
changed. There is nothing further to wait for.

**A git merge driver for the path.** Already rejected by ADR-0105: union-merging prose
produces text no one wrote, and a semantic conflict still needs a human. It moves the
conflict from `git` to the reader.

**Compare the two conflicting sides rather than base-vs-discarded.** Simpler to state and
wrong in the common case — see D3.

## Consequences

- A lane landing behind another lane no longer refuses on a roadmap re-render. That was
  near-deterministic with more than one lane in flight and is the whole cost WI-0252
  measured.
- A lane that hand-edits `ROADMAP.md` still refuses — the standard tells it not to, and the
  substrate now enforces that at exactly the point where ignoring it would cost prose.
- The trunk side is always the side taken, so WI-0260's destructive-render case resolves in
  the safe direction: a lane's gutted render loses to the trunk's, rather than the two being
  treated as equivalent.
- Members whose roadmap lives elsewhere can now declare it. Nothing changes for the ones
  that do not.
- `session.py` is byte-identical fleet-wide, so this reaches every member at the next
  substrate push. It needs no member action and no migration.
- If `## Now` ever *does* start producing refusals, that is evidence ADR-0105 D3 was wrong
  to keep it hand-authored — the same signal ADR-0105 nominated for itself, now with a
  place it will actually show up.
