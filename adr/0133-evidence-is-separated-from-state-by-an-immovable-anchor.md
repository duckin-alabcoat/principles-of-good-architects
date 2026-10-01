# ADR-0133: Evidence is separated from state by an immovable anchor and a floor at the write

**Status:** Accepted
**Date:** 2026-09-13
**Deciders:** Federation Architect (WI-0286, dispatched lane — decided in-lane per [ADR-0113](0113-a-dispatched-lane-closes-on-its-dispatch.md); the alternatives and the reasoning are recorded here for the operator's batch review rather than asked as a question). The parent rule is already canon by the operator's registry-Accept of [`evidence-is-separated-from-state-by-construction`](../habits/master.md#evidence-is-separated-from-state-by-construction) on 2026-09-11.

## Context

WI-0286 (withheld) collected six
instances in one session of a single failure — **a thing that merely looks like a valid
reading, or a valid write, is accepted as one** — and asked for a design pass rather than a
seventh item, on [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)'s
stated trigger.

**One of its four asks was already answered before this lane opened, and the item predates
the answer.** Ask (d) was *decide whether this belongs in canon*. It does, and it is there:
the habit was Accepted in the 2026-09-11 sitting, citing WI-0286's six alongside four more
that arrived independently in the same curate queue from readers who had never seen the
item. So the open question was never *whether* to name the boundary — it was that the
habit's own **Reality** line said `Discipline-only`, and this ADR is the record of making
it false.

### What had already shipped, and the asymmetry in it

Three stores on this substrate are live coordination state. Each has a shared fixture and
a derived detector; two have a refusal in production at the write itself.

| store | fixture | detector | floor at the write |
|---|---|---|---|
| runner's journal (`JOURNAL_DIR`) | `neutralize_coord_journal` | `test_coord_fixture_guard` | `_machine_close_would_hit_the_real_repo` |
| liveness sidecar (`SESSION_STATE_DIR`) | `neutralize_live_store` | `test_live_store_fixture_guard` | `_sidecar_stem_refused` (WI-0270) |
| coordination store (`poga-coord/`) | — (`ROOT` rebind) | `test_coord_fixture_guard` (journal axis only) | **none** |

All three detectors were **green with zero holes** when this lane opened, re-run
independently against the tree. That is the fact that makes this ADR necessary rather than
tidy: the rules were satisfied and the boundary was still open.

### The measurement

The suite was run under an audit hook on the `open` event, injected into every worker
process, recording any open of a path under a real `.session-state/` or the real
`poga-coord/` together with the test frames on the stack. **4,234 tests, 137.9s, 1,392
touches of real coordination state across 39 test modules.**

| | reads | writes |
|---|---|---|
| real `.session-state/` | 1,222 | **52** |
| real `poga-coord/` | 118 | 0 |

The 52 writes are the finding. **Every one of them passed the shipped floor**, because
`_sidecar_stem_refused` refuses a *forged* session id at the real store and these stems
were well-formed — one of them was this very session's own live id, inherited from the
ambient `CLAUDE_CODE_SESSION_ID`.

Thirty-five of the 52 were `announce.txt`, from `test_prep_adoption`, whose `setUp` pins
`SESSION_STATE_DIR` to a tmpdir **correctly** and which is green under the live-store
detector. The escape is one level down: `SESSION_BANNER_FILE` was a module constant
computed *from* `SESSION_STATE_DIR` at import, so redirecting the directory did not move
it. The same shape, `GUARD_FIRINGS_FILE`, had been converted to call-time resolution on
2026-08-07 after measuring that **432 of 532 records in the real denial log were test
fixtures** — a seventh instance of this class, a month before WI-0286's six, fixed on one
anchor and never carried to the other. `coord_fixture` had been patching both constants by
hand ever since, and says so in a comment.

An eighth instance turned up in the instrument: this probe's own `sitecustomize.py`
displaced the one `test_gate_inputs_child_audit` asserts on, and that test — correctly —
went red. The measuring apparatus reached into the thing it was measuring.

## Decision

**A live store is addressed through exactly one movable anchor, and the question "is this
write aimed at somebody's real state?" is answered only by handles a test cannot move.**

- **D1 — One anchor per store, not a family.** A path under a live store is a NAME joined
  to its anchor **at call time**, never a path bound at import. `SESSION_BANNER_FILE` and
  `GUARD_FIRINGS_FILE` become `BANNER_NAME` and `GUARD_FIRINGS_NAME`. A fixture that
  redirects the one directory now moves everything under it, **including whatever file gets
  added next** — which is the half that matters, since both historical escapes were files
  added after their fixture was written.
- **D2 — A floor at the write, in `atomic_write`.** A write made while the suite is running
  that targets a real store is refused. It sits at the funnel, not in each fixture's
  `setUp`, on the argument `_wi_write_item` and `_sidecar_write` already make one level
  down: *one hook point cannot be forgotten by the next verb someone adds*. 43 of the 52
  measured escapes pass through it.
- **D3 — Scoped by measurement, not by shape.** The refusal names the two directories that
  are actually somebody's live state, so the far larger number of correct writes into a
  fixture's own tmpdir is untouched. This is WI-0270's 171-to-1 finding applied on its own
  terms: a rule keyed on shape alone would break 171 correct writes to catch one.
- **D4 — Fails closed, and the direction is argued, not assumed.** What is on the other
  side is a forged beat that held four real lanes unrecoverable for 48h while nothing went
  red ([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).
  Failing closed is free of production risk *by construction*: the guard can only engage
  when `_test_suite_is_running()`, so no production write can reach it.
- **D5 — Immovable anchors only, and this ADR's own first draft got it wrong.** The first
  cut of `_real_live_stores` asked `_shared_work_root()`, which fixtures **patch** — so
  under a fixture it returned the tmpdir and the floor refused correct writes into it. It
  is pinned by a test that fails against that version. A question about what is real cannot
  be put to a handle a test is allowed to move, and the guard against this class is not
  exempt from it.
- **D6 — The detector's anchor list collapses to one name.** `LIVE_STORE_ANCHORS` was three
  globals; with D1 it is one. The list cannot drift from the file list again, because there
  is no longer a second list.

### What this deliberately does NOT decide

- **The read direction stays open.** 1,222 reads of real liveness state and 118 of the real
  coordination store are untouched here. They are latent rather than harmful today — but
  one of them is `test_claims` calling the real `cmd_wi_new`, whose coordination identity
  resolves from **this session's live `prep.json`**, which is WI-0242/WI-0250's defect one
  step before it does damage.
- **Ask (c) — extending [ADR-0108](0108-a-read-names-the-tree-and-commit-it-read.md)'s
  three-outcome rule to the by-hand paths — is refused in its general form**, and the
  reason is structural rather than a shortage of time. Nothing can force a person typing
  `git merge-base --is-ancestor` through a helper; WI-0283 had no test in it at all, and
  ADR-0108 D5 already declines to claim its own instance 1 for the same reason. What is
  *available* is naming the specific surfaces that collapse three states into two. The
  sharpest is `git_sync` in `sessionlib/config.py` (cited by symbol, not line — a lane's
  line numbers shift at every merge): on an unreadable sync state it
  returns an **empty orientation list**, so "I could not tell" renders as silence in the
  banner every session reads. That is a bounded, nameable fix and it is owed, not done.

## Alternatives Considered

- **Broaden the live-store detector to the full transitive call graph.** Rejected on
  measurement. The closure over 818 harness functions yields 159 reachers against the
  current rule's 31, and flags **30 test modules** where the runtime probe finds ~11 that
  actually touch live state — a 3× over-approximation. A guard that fires on correct code
  does not get obeyed, it gets weakened, and the live-store detector already carries a
  comment recording that exact lesson from its own first version.
- **Make each fixture pin the third constant too.** Rejected: it is the per-module pattern
  the shared fixtures exist to end, and it fails on the next file added under
  `.session-state/`. It is also what was actually being done, and it is what let 35 writes a
  run escape from a module that was doing it.
- **An environment variable that redirects the live store for tests.** Rejected for the
  reason `_test_suite_is_running` already records: a flag the runner must remember to set is
  missing exactly when someone runs the suite the unusual way — and the guard-firing log
  shows hand-run `python3 -m unittest` is routine here (at least 10 invocations across 6
  lanes in one day, from a log that records only *denied* commands, so a lower bound).
- **A write-side floor for `poga-coord/`, the store with none.** Deferred, not rejected. The
  probe measured **zero** writes to it from the suite, so a floor there would today guard a
  door nobody is walking through. It is the missing third of a shipped pattern and worth
  building; it is not worth building blind, and the measurement is now on record for
  whoever does.
- **Do nothing.** WI-0286 pre-registers the answer: defensible for any one instance, not for
  six — and the count is now eight, two of them found by this lane.

## Consequences

- A fixture author has one thing to remember instead of three, and forgetting it is refused
  at the write with a message naming the fixture to call.
- The habit's **Reality** line moves off `Discipline-only` for the sidecar axis. It is not
  `Built` outright, and the entry says which half is which.
- **Known holes, stated rather than implied** ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)):
  a test that spawns the real harness as a **subprocess** writes from a process where
  `_test_suite_is_running()` is false and the floor never runs — 3 of the 52 measured writes
  were of that kind. An **append** goes around `atomic_write` entirely —
  `curate/adopt-runner.py`'s `RUNNER_LOG`, 9 measured writes. Neither is fixed here.
- **Owed, with the numbers already paid for:** the read direction (1,222 + 118); the
  `poga-coord` floor; the subprocess and append holes; `git_sync`'s empty-list third state.

## References

- WI-0286 (withheld) — the item, its six instances, and its pre-registered counter-argument.
- [`evidence-is-separated-from-state-by-construction`](../habits/master.md#evidence-is-separated-from-state-by-construction) — the canon rule this ADR implements.
- [ADR-0108](0108-a-read-names-the-tree-and-commit-it-read.md) — the three-outcome floor; ask (c) is scoped against its D5.
- [ADR-0062](0062-cross-lane-coordination-in-the-git-common-dir.md) — why the coordination store lives in the git common dir.
- [ADR-0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md) — the one-namespace part loading that lets the floor in `config.py` call the predicate in `hooks.py`.
- `comms/2026-09-12-rehearsals-and-fixtures-reach-live-state.md` — the same class framed one level up, with the `--dry-run` family as a fourth row.
