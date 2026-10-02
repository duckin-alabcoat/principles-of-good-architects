# ADR-0119: The gate's three side doors are shut — a reservation outlives its journal, the gate sees two inputs, and the trunk writes its own views

**Status:** Proposed
**Date:** 2026-09-06
**Deciders:** the operator (dispatched this as one item — *"one item, one lane, one session; when the land is in, the session ends"* — and set the fallback: land D5 and D2 green and park D4 as a finding if it outgrows the session). Federation Architect (the restore-not-delete shape, the narrowing of the roadmap restore after the ADR-0115 control went red, the decision to guard the machine reaches in production code rather than run the audit hook as a permanent test, the completeness detector's second shape, and the two scope refusals in §"What this does not do").
**Builds on:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) (the serialized land gate), [ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) (the trunk lock), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the gate runs in a scratch worktree — D4 is the claim that ADR makes, finally made true), [ADR-0105](0105-roadmap-is-a-compiled-trunk-only-view.md) (ROADMAP is a compiled trunk-only view), [ADR-0115](0115-the-resolvable-conflict-class-is-per-region-not-per-file.md) (the per-region resolve class D5 had to stay inside)
**Bears on:** [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) (its measured input set gets smaller as the suite stops reading the machine; nothing here changes its mechanics), [ADR-0069](0069-shared-numbers-are-compiled-or-drawn-never-declared-or-picked.md) (D2 is the release half of drawn numbers)
**Amends:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) §Consequences (WI-0215's correctness surface is now closed by a refusal, not only improved as a side effect); [ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) §Consequences (its decisions 2 and 3 — live-holder lease, one lane exit — are built, by WI-0298, and recorded here as D3/D6 because no ADR was drawn for them)
**Related:** the 2026-09-05 consultant brief §3 D2/D3/D4/D5/D6; WI-0298 (D3/D6, landed at `b1f5857` with no ADR); WI-0346 (D3's read-back clause finished — the reserve stops reporting a gate it does not hold, and the release matches the record instead of trusting a boolean); WI-0273 (fleet rollout, still held)
**Reality:** Built — D2, D4 and D5 in this lane; D3 and D6 as WI-0298 landed them. 40 new tests; suite 3220 → 3260, green under `/usr/bin/python3` on the serial and the sharded runner, and green again with `POGA_GATE=1` set (skipped=46). The two scope refusals in §"What this does not do" are NOT built and are named there.
**Work item:** WI-0299

## Context

[ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) gave the trunk one door and
said plainly that the door was not the fix. WI-0298 then made the land gate's lease a live
holder rather than a clock, and gave every lane exit one path. What remained is what this
records: three ways the gate could still be wrong about its own inputs, none of which the
lock or the lease touches.

They are one property in three costumes — **the trunk moves only through the gate, and the
gate sees only its two inputs.**

- **A drawn number stopped being yours the moment you closed your journal.** Ownership is
  decided by `_coord_mine`, and inside a lane the only key that can match a record drawn
  through the main-anchored front door is the *journal* — a front-door record carries a
  Claude session id and `branch: main`, neither of which names the lane. But
  `_find_holder_journal` searched only journals with no `ended`. `end` and `merge` close
  the journal and *then* land, so the land — the one moment ownership of a number is
  decided — always asked after the answer had been thrown away. That is WI-0254, and it is
  the mechanism behind the refusal WI-0237 could only record as unexplained: *"it was hit
  at the very end of the session, which is exactly when the journal is closed."*
- **The gate's verdict was partly a reading of the operator's machine.** ADR-0058 D4 claims
  the candidate is checked out into a throwaway worktree so the gate *"sees exactly what
  the trunk would become — no in-flight noise from anyone."* WI-0271 measured that with an
  audit hook across 36 test modules inside a real scratch gate worktree, and it was false:
  the suite shelled out to `lsof`, `ps`, `tmux` and `claude`, and asserted `sound` against
  the **live** work-item store. This class has blocked a land twice.
- **A lane could author a file only the trunk may write.** `ROADMAP.md`, the compiled
  handoff and `STATUS.md` are trunk-compiled views with one writer, and a lane's copy is a
  render of a tree that is behind. Carrying one into the rebase failed both ways: WI-0262
  (refused on a file the trunk regenerates seconds later) and WI-0260 (the stale render
  *landed* — five real outcome bullets replaced by the empty placeholder, saved only by
  main having independently touched the same file).

**Two of the item's own premises were stale, and finding that out is part of the record.**
WI-0271's channel (a) — `POGA_INVOKED_FROM` missing from `DISPATCH_ENV_VARS`, so the suite
read the live checkout's and every sibling lane's journals — was closed by WI-0295 a week
ago. And WI-0262's *"`merge --resolve generated` does not recognise ROADMAP.md"* stopped
being true when [ADR-0115](0115-the-resolvable-conflict-class-is-per-region-not-per-file.md)
shipped the per-region predicate. Both were read as current because a work item is a report
and nothing updates it when the world moves
([`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report),
which is about exactly this).

## Decision

### D2 — Number reservations are gate-owned, and ownership outlives the journal.

Three parts, of which one was already built:

**The exact-key search sees closed journals.** `_journals_in` returns every journal;
`_open_journals_in` becomes a filter over it and still means *open*. Only
`_find_holder_journal`'s Claude path widens. This is WI-0254's *"two paths, two answers —
do not widen both"*, and the argument is not symmetry, it is that the two paths resolve by
different evidence: the Claude path matches an exact `claude-session-id`, which is exactly
as unambiguous for a closed journal as an open one, while the non-Claude fallback resolves
by **uniqueness within a checkout** — closed journals accumulate, so uniqueness there would
collapse on the second session any checkout ever ran. Two callers also read
`_open_journals_in` to *mean* not-yet-closed (`_resolve_orphan_candidates`,
`cmd_resolve_orphan`), so widening it in place would have broken the orphan reaper to fix
the land gate.

**The land releases the numbers its files carry.** `_release_landed_numbers` runs
immediately after the CAS succeeds and still inside `land_gate_lock`. The set is the
**diff**, never the session: it reuses `_counter_new_and_trunk`, the same scan
`_counter_land_gate` refuses on moments earlier, so the gate and the release cannot
disagree about which numbers this land is about — and a number still in flight survives its
own session's land, which is WI-0237's `(3) LIMIT` boundary. It releases with `force=True`
deliberately: ownership at that instant is unreliable by construction, and the fact that
authorises the release is not *"this is mine"* but *"the file carrying this number is on
the trunk"*, which is a property of the ref. WI-0237's release-at-commit covered
`wi-alloc` and `ops-alloc`; nothing writes an ADR through the store, so `adr-alloc` had no
release on any write path at all and sat until its 8-hour TTL lapsed.

**A dead lane's reservation goes with the lane — already true, and now tested.** WI-0298's
`_lane_exit_release` frees every `COORD_KINDS` record in one step and the allocator kinds
were always in that set. What had no test was the part that can silently stop working: a
front-door reservation carries `branch: main` and main's ownership key, so no
identity-keyed sweep can see it, and it is reachable *only* through the journals the lane
added. Membership was necessary; being **found** is the property.

### D3 — The lease is held by a live holder, not a clock. *(WI-0298, `b1f5857`.)*

Recorded here because it landed with no ADR drawn. Renewal keys on the journal
(`_coord_mine_for_refresh`), so a waiter cannot keep a dead holder's record alive — the
2026-09-06 wedge, where a closed session's gate record was renewed for 28 minutes by
the *next* session's heartbeat because both share the lane branch as `session_id`. The TTL
becomes a dead-holder detector (`_land_gate_holder_dead`: pid on the owning host, heartbeat
age off-host, each answer carrying its reason), the holder renews from a thread inside the
landing process, and `_land_gate_reserve` reports success only from a record read back off
disk. *Closes WI-0276, WI-0277.*

**Corrected 2026-09-12 (WI-0346): that last clause was half-built, and closing WI-0277 on
it is how the other half stayed open for six days.** `_land_gate_reserve` did gain the
read-back — and kept returning `True` from all three of its fail-open paths, so it
reported success from a record read back off disk *and* from three places where no record
existed at all. `land_gate_lock`'s `finally` then released, at `force=True`, under a
comment arguing that `serialized` PROVED the record was ours. It proved only that a
reserve had once answered True. Two ways that unlinks the single shared gate out from
under a live lane: a fail-open that wrote nothing, and — which no truthful return value
could have covered — a lapsed record another lane legitimately reclaimed
(`_land_gate_holder_dead`) while this one was still landing.

The fail-open POLICY this decision names is unchanged: a land that cannot serialize still
lands. What changed is that the gate stops lying about what it holds. The reserve now
answers with a third value that separates *nothing was reserved and nobody holds it —
stop waiting* from *a live lane holds it — queue*, which is what `True` had been standing
in for; and the release is `_coord_release_exact`, which frees a record only when the one
on disk is still the one we wrote (`created_at` + `session_id` + `journal`, none of which
a heartbeat touches). "We hold nothing" and "someone else holds it now" are then no-ops by
construction rather than by a caller remembering to check — the comment was the mechanism,
and a comment is not one. `_trunk_lock_reserve` / `trunk_lock` carried the identical shape
against the trunk-lock record, down to the same justifying comment, and are fixed with it.

### D4 — The gate runs on a snapshot and nothing else.

`_run_gate` sets `POGA_GATE=1` in the suite's environment, and the reaches return their
existing CANNOT-TELL answer instead of spawning: `_process_cwd` (lsof) → None,
`_lane_processes` and `_process_chain` (ps) → empty, `_dispatch_runtime_pid` → None,
`_dispatch_rebrief_tmux` → refused with a reason, and `cmd_preflight` drops its two
machine checks. Not one of them gains a fabricated answer; each returns the state its own
contract already carries for *the machine would not tell me*. Three test classes whose
**subject** is the machine (`ProcessCwdProbeTest`, `TmuxSpawnTest`,
`BriefedNotJustSpawnedTest`) skip under the gate rather than being adapted — a test about
what the OS does is a test a land should not run. `test_poga_fleet`'s two remaining
live-store assertions become routing assertions, finishing a removal that was made one
function earlier and left half-done.

**The guard goes where the reach is, and it took three passes to find that line.** The
obvious placement is the leaf that spawns, and for the four transitive reaches
(`_process_cwd`, `_lane_processes`, `_process_chain`, `_dispatch_runtime_pid`) that is
right — nothing mocks them, so a test reaching them reaches the machine. It is wrong
wherever the leaf has its own **hermetic unit test**. `_pf_auth`, `_pf_stranded_runtimes`,
`_dispatch_open_tmux`, `_attach_live_sessions` and `_attach_open` each have a class that
mocks `subprocess.run` and pins every branch of their parsing; a guard *inside* those
functions sits above that mock and fails tests that never touched the machine. The first
full gated run failed 45 such tests, the second 20. Those reaches happen when a **caller**
invokes the leaf for real — `VerbDefaultsTest` calling `cmd_preflight`, `CapBindsOnTheFirstWaveTest`
reaching `_dispatch_rebrief_tmux` — so that is where the refusal lives. A guard that deletes
coverage without removing a reach is a bad trade twice over.

`CompletenessTest` therefore carries `GUARDED_AT_THE_CALLER`, five rows each stating where
its guard actually is. An exemption list is normally the thing to refuse, and the objection
holds against entries that default to *safe* with no argument; these carry one, a test
asserts every row has one, and a second test deletes any row whose function no longer
reaches the machine — so the list cannot outlive its subject or quietly cover a future
function that takes the same name.

`session.py test` / `poga test` **refuse over an in-progress merge** (`MERGE_HEAD` in this
worktree's own git dir, via the new `_git_dir`). WI-0215's destroyer was never identified —
the reflog held no commit, reset or abort, and grepping the suite for `MERGE_HEAD` finds
nothing — so this is a refusal rather than a repair: against an unidentified destroyer the
honest move is to keep it away from the state it destroys. Exit 2, never 1, because a
refusal is DID NOT REPORT and exiting 1 would make a false claim about the code.

**The guard is in the production code, not in a rule tests must remember, and that is a
deliberate departure from what WI-0299 asked for.** The item said the audit-hook probe
should itself become the permanent test. An audit hook is per-interpreter and cannot be
uninstalled, so it can only run the suite in a child — which means every land pays for the
suite **twice** — several minutes a run, per ADR-0117 — on top of the run it is checking.
ADR-0117 exists because the operator ruled that lands should take seconds, not minutes. A guard that doubles every land in order to enforce ADR-0117's own premise is
the wrong trade in the same week. Closing the reach where it happens costs nothing at run
time and cannot be forgotten by a test that does not have to apply it — which is
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
read correctly, this being the third instance of the class after `coord_fixture` and the
dispatch-env variables.

**The completeness detector is the half that makes it a guard rather than a list**
([`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)),
and it earned its place twice in one sitting. Written to match a literal argv, it
immediately found two `ps` reaches the hand-written guard list had missed
(`_process_chain`, `_dispatch_runtime_pid`). It also reported **clean for tmux** — because
every tmux argv is built from a resolved path (`tmux = _tmux_bin()`, then `[tmux,
"send-keys", …]`), so no literal ever appears and the exact `send-keys` WI-0271 measured
against the operator's live server was invisible to the check written to catch it. A
detector reporting clean because it cannot see is
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) turned
on the detector itself, so it gained a second shape: the resolved binary appearing as
**argv[0]** of a spawn. That found three more. A **third** blind spot surfaced only when an
exemption row went stale for a function the detector had stopped seeing — an argv assigned
one line above its `subprocess.run` (`argv = ["tmux", "ls", …]`), which is the ordinary way
to write it, so the detector had been reading exactly the call sites that happened to be
untidy. Three shapes, each found by the previous one failing rather than by inspection.

### D5 — Generated views are never merged; the trunk regenerates them.

`_trunk_only_views()` is the one list — the compiled handoff, `STATUS.md` (which carries the
census in its frontmatter) and `ROADMAP.md` — and `_harness_owned` and
`_generated_view_names` derive from it. There were **four** enumerations before, and *"is
the roadmap generated?"* had a different answer depending on which you asked: which is how
WI-0262 arrived (the resolve policy said no) and WI-0260 landed (the dirt classifier said
yes and nothing stopped the write).

At land, `_restore_trunk_views` puts the lane's copy of each view back to the trunk's own
content as one commit on the lane branch, before the rebase, once per CAS attempt, covering
the fast-forward branch too. **Restore, never remove:** deleting the views would leave the
trunk with no `ROADMAP.md` between the CAS and the recompile, and the renderer splices into
an existing file — the regeneration meant to heal it would find nothing to write into, so
the human-facing view would be *gone* rather than stale.

**Restricted to render-only divergence, and the first cut was wrong.** It took the trunk
side of the whole roadmap, which is the interim ADR-0105 rejected on measurement — *"it
silently deletes authored prose on every land, with no second copy to recover it from"* —
and ADR-0115's control `test_a_lane_that_edited_the_hand_authored_now_still_refuses_by_name`
went red on it inside a minute. So a view that is generated only in regions is restored only
when the lane's divergence is confined to them (`_lane_side_is_render_only`, the
no-conflict-in-progress sibling of `_roadmap_side_is_render_only`, fail-closed in the
direction of *do not restore*). A lane that edited `## Now` is out of contract (ADR-0105 D4:
lanes never write this file) — and out of contract is an argument for someone being **told**,
never for the bytes being dropped. It lands, or it conflicts and is refused by name, and
either way the restore says in its receipt which views it held back and why.

The writer is guarded too, which is where WI-0260 actually came from: `cmd_wi_render` had no
lane check at all. `run_compile` declined to reach it from a lane, but `session.py wi-render`
and `poga work render` reach the renderer directly — a capability whose only protection was
that one of its two callers happened to check.

### D6 — Every lane exit posts capacity. *(WI-0298, `b1f5857`.)*

Recorded here for the same reason as D3. `_lane_exit_release` / `_lane_exit_verify` free the
gate record, the queue ticket, the drawn numbers, the lane reservation and the dispatch slot
in one step, and teardown *verifies* the store is empty of that session's records rather than
announcing a clean close. `dispatch-spawn` joined the exit surface, which no sweep had ever
freed. *Closes WI-0281.*

## Alternatives considered

**The audit-hook probe as the permanent D4 test**, as WI-0299 specified. Rejected on cost,
above: a second full suite run inside every land. Its mechanism survives as
`curate/gate_inputs.py`, where it runs deliberately rather than on every land.

**A hand-maintained list of the functions that reach the machine.** Rejected before it was
written and vindicated immediately: the list would have had five entries and the detector
found ten, two of them on its first run.

**Guarding the leaf rather than the reach**, in two variants. First `_tmux_bin()` instead of the tmux spawn sites — one guard covering every
`send-keys` in the tree, which is what shipped first. Rejected after it broke `poga dispatch`
**planning**: `_dispatch_surface` asks `_tmux_bin()` merely whether tmux is a usable surface,
so a None there made eight `CliTest` cases exit 2 under the gate and pass outside it. A
lookup is not a reach. Then the same error one level down — guarding every function that
*can* spawn, including five with hermetic unit tests of their own, which failed 45 tests in
the first full gated run and 20 in the second. Both variants share one lesson: a gate whose
verdict differs from reality fails correct work, which is the disease wearing the cure's
clothes, and only running the suite in the configuration the change creates showed it.

**Deleting the generated views at land instead of restoring them** — simpler, and it loses
the file. See D5.

**Widening `_open_journals_in` in place** rather than adding `_journals_in`. Rejected: it
would fix the land gate by breaking the orphan reaper, which reads that helper to mean
*not yet closed*.

## Consequences

- **A land now costs one extra commit on the lane branch** when the lane carried a divergent
  view — a `chore(views)` restore. It is visible in the lane's history and nets to no change
  in any trunk-only view on the trunk, which is the invariant the test asserts (over the
  range, not over one commit: the lane's own mid-session render commit cannot be un-made
  without rewriting history, which the destructive-ops guard refuses).
- **`poga preflight` run inside a land reports UNKNOWN for auth and stranded runtimes.** That
  is correct and it is also a real reduction in what the gate observes; the verb is unchanged
  everywhere else, which is where its answers were ever about this machine.
- **46 tests skip under the gate** that run everywhere else. A skip is not a pass and this is
  worth watching: the three classes involved are the ones whose subject is the operator's
  machine, and if that set grows the right response is to ask why a land is exercising them.
- **ADR-0117's measured input set should shrink** as the suite stops reading outside its
  fixtures, which makes more lands gate-neutral. Not measured here, and deliberately not
  claimed: re-deriving `gate-inputs.json` is its own act and the record in the tree is the
  one from before this change.
- **The fleet rollout (WI-0273) stays held.** `session.py` is byte-identical substrate, so
  all of this is a fleet change whose push is a separate decision.

## What this does not do — two refusals, named rather than left to be discovered

**`session.py test` does NOT run in a scratch worktree**, though WI-0299 asks for it
("otherwise runs in a scratch worktree exactly as the gate does"). The spec conflates two
opposite purposes. The gate is isolated so its verdict is about *the candidate commit*;
`poga test` exists to test *the tree you are standing in* — `poga`'s own comment says so in
as many words, and that is the verb's entire value during development, when the work is
uncommitted. Running it from HEAD in a scratch worktree would silently test something other
than what the operator is holding, which is a worse failure than the one it would prevent
and is not what WI-0215 is about. WI-0215's harm is `MERGE_HEAD` destruction, and the
refusal closes that completely. **Finding for review**, not a decision to skip work.

**`_land_branch` still gates in place** — `_gate_unless_neutral(parent, tip, _run_gate)`
with no `cwd`, so the suite runs in the live checkout. This is a fifth side door of exactly
the D4 shape, found while surveying and not taken: it is one of the two legacy landers
(ADR-0041's non-Claude members still use them), changing where it gates changes what it
gates, and doing that unreviewed in a lane whose item does not name it is how the twelve got
filed. **Finding for review.**

## References

- Work items: WI-0299 (this), WI-0254, WI-0271, WI-0215, WI-0262, WI-0260 (closed by this),
  WI-0276, WI-0277, WI-0281 (closed by WI-0298), WI-0237 (already done), WI-0273 (held)
- `proposed-edits/federation-arch/pending/2026-09-05-consultant-one-writer-for-the-trunk.md` §3
- `tests/test_trunk_only_views.py`, `tests/test_reservation_ownership.py`,
  `tests/test_gate_snapshot.py`
- Habits: [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes),
  [`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability),
  [`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence),
  [`probe-live-state-before-acting-on-a-report`](../habits/master.md#probe-live-state-before-acting-on-a-report)
