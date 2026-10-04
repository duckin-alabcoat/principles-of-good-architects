# ADR-0108: A read names the tree and commit it read — read provenance is a fact returned, never a constant assumed

**Status:** Accepted
**Date:** 2026-09-03
**Deciders:** the operator (chose the rule over the one-line banner patch, session ~187: *"do the ADR. Write it, then build the fix."*). Federation Architect (the layer choice, the three-outcome floor, the rejection of auto-integrate).
**Builds on:** [ADR-0060](0060-concurrency-substrate-is-a-git-lifecycle-wrapper.md) (lanes are isolated worktrees), [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) (cross-lane coordination lives in the git common dir), and the standing rule that a lane's claim path reads main's store, never its frozen copy (ADR-0092 D1, withheld)
**Extends:** [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) from *what a check assumes about its subject's shape* to *which copy of the subject it read*
**Reality:** Partial — D2's helper (`tree_provenance` / `provenance_clause`) and D3's lane-distance line are built and tested this session, and instance 2's own surface (`reconcile.py --status`) renders through the helper. The remaining D6 surfaces adopt it as they land; the `session.py` mirror waits for its first caller.
**Work item:** WI-0236

## Context

Three times in one session (~185) a stale view was reported as live state, and the
substrate helped each time. That is the recurrence trigger
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)):
the first miss is a miss, the repeat means discipline is not the fix.

**The three instances do not point the same way, and that is the whole finding.**

- **Instance 2.** `curate/reconcile.py` reported a newly added member off-roster immediately after
  the roster row had been applied *and verified* in the lane. `_WORK_ROOT =
  shared_work_root(FED_ROOT)` (`curate/reconcile.py:76`) forces `roster_ids()` (`:124`)
  to read `portfolio.md` — a **tracked** file the lane held a newer copy of — from the
  main checkout. The tool read the **stale** tree and said nothing about it.
- **Instance 3.** A brief's delivery was reported as pending when it had landed two hours
  before the session opened. The lane read the outbox from its **own frozen copy** while
  main and origin were ahead. The tool read the **stale** tree and said nothing about it.

Opposite directions. In instance 2 the lane was fresher; in instance 3 main was fresher.
**There is therefore no tree that is universally correct to read.** "Always anchor on
main" and "always read the lane" are each wrong half the time, which disposes of the
whole family of fixes that work by picking a side.

What is left, once picking a side is off the table, is to make the choice *visible in the
answer*. Today it is invisible by construction:

**Five independent resolvers, and every one returns a path, never a commit.**
`_git_common_dir` (`sessionlib/config.py:1765`), `_shared_work_root` (`:1797`), `_main_checkout`
(`:11426`), `_lanes_root` (`:11805`), and `curate/common.py:153`. `poga`'s `require_repo`
(`poga:88`) does the same and then `exec`s into main (`poga:790`) printing nothing about
where it went. So "which tree, at what commit" is not merely unreported on the read side
— it is never computed.

**The honest version already exists, on the write side.** `_wi_receipt_where`
(`sessionlib/store.py:298`) is a purpose-built "which tree did this land in" sentence with three
outcomes including an explicit *cannot tell*, and the comment above its call site states
the rule this ADR generalizes: *"WHERE it committed is a FACT to read, not a constant to
assert"* (`sessionlib/config.py:2980`). It was built because a **write** receipt was once wrong. The
read side never got the same treatment.

**A lane is structurally blind to its own position.** `git_sync` (`sessionlib/config.py:721`)
derives every behind/ahead number from one `rev-list … @{u}...HEAD` (`:830`). A lane
branch (`worktree-poga-N`) has no upstream, so the function returns at `:832` with
`no upstream tracking — pull/push skipped` and **never reaches** any behind/ahead
computation. Every lane takes that path, always. Verified live while writing this ADR:
this session's banner reported only `no upstream tracking`, while the lane sat **3 commits
behind `main`**. That is a `declare-what-a-check-assumes` violation inside the substrate's
own sync check — *"I cannot tell you anything about your position"* renders identically to
a benign skip.

**One correction to the work item's own account.** WI-0236 records of instance 3 that
"the session-start banner said so." No code path produces that. `_base_commit()` is
written to journal frontmatter (`sessionlib/hooks.py:244`) and never displayed; and on a clean
tree `git_sync` does not *report* being behind, it silently **ff-pulls** (`:855-863`).
Session ~185's journal (`20260903-1d4a.md`) records `base-commit:
5d6ba1cb4c76` and its body never mentions the banner or being behind. Banner output is not
retained, so which line was actually printed cannot now be established. The candidate fix
that rested on that claim — *make the existing "N behind" loud* — therefore rests on a
number that was never computed. This ADR treats it as **absent**, not as ignored.

**One instance this ADR deliberately does not fix.** Instance 1 was the Architect
repeating two consultant briefs' claim about an allow rule instead of opening the live
`settings.json`. No banner and no provenance line would have caught it; it is
[`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)
missed, and it stays a discipline failure. Naming it here keeps this ADR from being
counted as having closed it.

## Decision

**Any output that reports on shared state must name the tree and the commit it read.**
Read provenance is a fact the code fetches, exactly as write provenance already is.

### D1 — The floor: three outcomes, never two

An answer about shared state renders one of exactly three states, and the third is never
folded into either of the others:

| State | Renders as |
|---|---|
| Tree read, commit known | `per <path> at <sha>` |
| Tree read, no commit to name | `per <path>, commit UNKNOWN` |
| Tree could not be read at all | `source UNREADABLE (<path>)` |

The clause is a **fragment**, not a sentence, so a caller appends it to its own verdict
(`Reconcile: 1 off-roster (example-app) (roster per …/federation at b1b43cb) — …`) rather than
emitting a second line the reader has to join up. The middle state is the one most easily
lost: a directory that exists and simply has no commit is honestly nameable as a source,
and only its commit is missing — collapsing it into *unreadable* would claim we could not
see a tree we could see.

This is [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)
applied to *which copy*, and it is the same three-outcome shape `_wi_receipt_where`
(`sessionlib/store.py:298`) and `_attach_checkout_staleness` (`sessionlib/lanes.py:6100`) already ship. A
verdict with no provenance is not a shorter true answer; it is a different, weaker claim
being passed off as the strong one.

### D2 — The layer: one helper, rendered by the caller

This settles the question WI-0222's note parked as *"worth deciding which before
building."*

Provenance is computed **once**, in a shared helper beside the resolvers —
`tree_provenance()` / `provenance_clause()` in `curate/common.py`. It is **not** pushed
inside `shared_work_root()` itself: that
function's return value feeds many callers that are not user-facing answers (sweep
directories, spawn roots, lane paths), and widening its type to serve the handful that
are would churn every call site to fix a reporting defect.

So: **the resolvers keep returning paths; the helper turns a path into a provenance
record; the caller renders it.** Callers that do not report to a human are untouched.

**The `session.py` mirror lands with its first caller, not now.** `curate/` is
federation-only while `session.py` ships byte-identical to every member, so a copy
added there today would be dead weight in the shipped substrate and a claim, in code,
about a capability nothing uses — the same argument that kept the devbox mail drain out
of `session.py` in session ~186. The federation's own first read-side caller is
`reconcile.py`, which lives in `curate/`; when a `session.py` surface needs the clause,
the mirror lands with it.

### D3 — The reference implementation: a lane learns its distance from the trunk

`_main_checkout_lines()` (`sessionlib/lanes.py:2570`) is the one banner block that already speaks
about the main checkout from a lane, and it already prints a distinct `main: NOT CHECKED`
when it cannot ask. It gains the lane's distance from the trunk, on the D1 floor.

This is deliberately the *cheapest possible* instance of D1 — a single line, in
machinery that exists, with the three-outcome discipline already in place — so the rule
ships with a working example rather than as pure doctrine.

### D4 — Auto-integrating a behind lane is rejected

Candidate (c) from WI-0236 is not adopted. It repairs only instance 3's direction and
does nothing for instance 2's, so it cannot be the general fix; and it cuts directly
against shipped precedent — WI-0188's fix **never pulls**, pinned by a test that scans
every git argv, and WI-0222 rejected auto-pull on the ground that *"pulling under an
operator mid-fan-out is the surprise WI-0188 explicitly rejected."* Both of those are
about the operator machine rather than a lane, so the precedent does not formally forbid
this — but nothing has yet made the case for crossing it, and a fix that repairs one
direction while silently mutating the operator's tree is a poor trade against one that
makes both directions legible.

### D5 — Discipline failures stay discipline failures

Instance 1 is out of scope, per the Context. This ADR does not claim it.

### D6 — The four private answers converge on D1

WI-0205 (delivery-audit evidence scope), WI-0155 (dispatch diagnosis), WI-0191
(`work check` tri-state), and WI-0211 (curate hook third state) are each building their
own local version of this rule right now. They render through the D2 helper as they land,
rather than each inventing a phrasing. WI-0222's `require_repo` half (`poga:88`) is the
same shape and joins them.

## Alternatives Considered

- **Make the banner's "N behind" loud.** The original candidate (a). Rejected as stated:
  in a lane the number does not exist, so there is nothing to make loud. What survives of
  it is D3, which computes it first.
- **Anchor every read on the main checkout.** Fixes instance 3, causes instance 2. It is
  the rule `curate/reconcile.py` already follows, and following it is what produced the
  false off-roster verdict.
- **Anchor every read on the lane.** The mirror image: fixes instance 2, causes instance
  3, and breaks the claim-path rule (a lane reads main's store) and the work-item store's whole anchoring design.
- **Push provenance inside `shared_work_root()`.** Rejected in D2 — it would change the
  return type for a large majority of callers that never render anything, to serve a
  minority that do.
- **Auto-integrate a behind lane at start.** Rejected in D4.
- **Fix each surface separately.** The status quo, and the thing WI-0222 parked. Four
  items are already doing it; the result is four phrasings of one rule, none reusable,
  and a fifth surface arrives unguarded.

## Consequences

- A stale answer stops being indistinguishable from a fresh one. The reader no longer has
  to know which tree a tool anchors on to judge its verdict — the verdict says.
- **The verdicts get longer.** Every roster/status/store line grows a clause. That is the
  cost, and it is accepted: the failure this prevents cost four unnecessary commands on
  another machine and was caught only because the operator noticed the repetition.
- A lane that is behind the trunk now says so at start, which is a new banner line on a
  surface already dense. D3 keeps it silent in the common case (lane current), matching
  how `_main_checkout_lines` already stays silent when main is clean.
- The five resolvers remain five. This ADR does not consolidate them; it adds one helper
  beside them. Consolidation stays available and unclaimed.
- **A new failure mode is created:** provenance that is itself stale — a commit read at a
  different moment than the content. The helper reads both in one pass to keep the window
  small, but it is a window, and a caller that caches a provenance record across a fetch
  will lie in a new way.
- Nothing here detects instance 1's shape. An Architect trusting a document over the file
  it describes remains uncaught by the substrate.
- **Instance 2's routing was fixed separately, and that does not overturn "no tree is
  universally correct" — it narrows it** (WI-0246, session ~191). Naming the source made
  the wrong source *visible*; it left `reconcile.py` still measuring a lane's verdict
  against main's roster. The narrower rule that resolves that one read is **route by who
  WRITES the file**: `portfolio.md` is authored content any lane may write, so it is read
  from the tree the script is running in; `reconcile-roots.local` / `repo-paths.local` are
  excluded from version control and exist in one tree only; `STATUS.md` is a trunk-only
  generated view (ADR-0056) a lane never writes. Three files, three writers, three
  answers — which is why no *single* anchor was ever available, and why this ADR's remedy
  is still needed on top: the routing decides which tree, and the clause still says which
  one it was. The context above cites `_WORK_ROOT` at `curate/reconcile.py:76`/`:124`;
  that name and those lines are the pre-fix state, kept as the record of what was found.

## References

- WI-0236 — the pattern item; three instances, the three candidate fixes, the recurrence trigger.
- WI-0222 — `poga work` draws and lists from the main checkout; its note is where the layer question was parked and where the third surface (`reconcile.py`) was recorded.
- WI-0246 (done) — instance 2's routing half: `reconcile.py` now reads the roster from the tree it is running in, with the machine-local config and the trunk-only `STATUS.md` left on the main checkout. Pinned by `tests/test_reconcile_roster_root.py`.
- WI-0188 (done) — `_attach_checkout_staleness`, the three-outcome staleness line scoped to `attach`; the never-pulls precedent.
- WI-0205, WI-0155, WI-0191, WI-0211 — the four per-surface honesty requirements that D6 converges.
- ADR-0092 D1 (withheld) — a lane's claim path reads main's store, never its frozen copy.
- [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes), [`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report), [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence).
- Session ~185 journal `sessions/journal/20260903-1d4a.md` — instance 3's base commit, and the absence of any banner claim in its body.
