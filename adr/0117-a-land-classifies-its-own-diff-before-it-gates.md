# ADR-0117: A land classifies its own diff before it gates, and the input set is measured

**Status:** Proposed
**Date:** 2026-09-05
**Deciders:** the operator (reopened the gate-neutral half as its own item, 2026-09-05, answering option 1 of `comms/2026-09-05-the-trunk-lock-alone-does-not-close-the-theft.md` (withheld); set the acceptance: a journal-only close-out lands in under a minute with the gate reported SKIPPED, and a code change still runs the full gate). Federation Architect (the measured-not-listed input set, the directory grain, the fail-closed branches, and the decision to wire all three landers rather than one).
**Builds on:** [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) (the serialized land gate — this shortens its queue, it does not amend it), [ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) (the trunk lock a neutral land rides on), [ADR-0058](0058-land-per-session-with-an-isolated-gate.md) (the gate runs in a scratch worktree)
**Bears on:** ADR-0056 (withheld) (the gate's fail-closed contract, unchanged for anything it still runs)
**Related:** the 2026-09-05 consultant brief D1a; WI-0263, WI-0271, WI-0272 (all still open — this closes none of them)
**Reality:** Built — classifier + measured record + 12 tests; 6 mutations each caught by exactly one test.
**Work item:** WI-0294

## Context

**A land runs the whole suite whatever the lane changed.** It takes the serialized gate, rebases onto the trunk tip, runs `python3 -m unittest discover -s tests` in a scratch worktree, then CASes. The suite is the long part by an order of magnitude, and because [ADR-0114](0114-the-land-gate-is-serialized-one-at-a-time.md) made lands one-at-a-time, four close-outs in a row is four suites in a row. the operator ruled, closing out 2026-09-04, that lands were taking up to half an hour and should take seconds, not minutes.

**The brief said most of those suites cannot change their own verdict. The measurement says otherwise, and this is the central finding.** Of the last 80 trunk commits, **63 are gate-neutral under the set the brief assumed** (journals, work items, comms, generated views) and **0 are gate-neutral under the set actually measured.** The suite reads `work-items/` (43 of those 80 commits touch it), `sessions/` (27), `ops-items/` and `ROADMAP.md`. It reads them because tests read the **live store**: 162 files under `sessions/journal/` and 109 under `work-items/`, the production directories, not fixtures.

So the classifier is correct, safe, and today skips nothing. That is not a reason to withhold it — it is the mechanism, and the mechanism is what makes the real blocker visible and worth fixing. The real blocker is that a handful of tests are non-hermetic against the repo's own store, which is a defect in its own right and one this repo already has open items about. Fix those and `sessions/` and `work-items/` leave the input set; the 63-in-80 land becomes real, and it becomes real *by evidence* rather than by assertion.

The brief's premise was not idly wrong. It is the same mistake as a hand-written list, made one level up: it named the directories that *ought* to be irrelevant, and nobody had checked.

**The reason this had not already been fixed is that the safe version is not obvious.** Skipping a gate is not a performance tweak, it is a decision to land untested code — and it fails *silently*, which is the worst property a check can have. Everything below exists to make the skip decidable from evidence rather than from a list somebody maintained.

## Decision

### D1 — The input set is MEASURED, never listed.

`curate/gate_inputs.py --derive` installs a `sys.addaudithook`, runs the suite exactly as the gate does, and records every repo-relative path the suite actually opened — `open`, plus `os.scandir` and `os.listdir`, because a glob makes a whole directory an input even when no individual file is read. The record (`gate-inputs.json`) is committed, so a lane and the trunk classify against the same measurement.

A hand-maintained list was the obvious cheap alternative and it is exactly the [`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) failure: a wrong entry skips a gate that was needed, and nothing says so. It is not hypothetical here. Session ~206, reasoning by hand about which paths were inert, first classified `sessions/journal/` as neutral and then found `_attention_session_is_over` globs and reads every file in it. The most confident hand-classification available got the single most important entry wrong. The consultant brief's own D1 reached the same conclusion independently and struck the config-list alternative in its second revision.

**The record stores what was READ, not what is neutral.** The neutral set is the complement, computed at classification time. Storing the complement would mean a path nobody has ever measured defaults to *safe*, which is the wrong direction for the one decision in this system that trades correctness for speed.

**The first measurement was wrong, and it is worth recording how.** The probe captured the working directory once at start and resolved relative paths against it. The suite `chdir`s into tmpdirs constantly, so every relative path a test opened was joined to the **repo root** instead — and `Path.resolve()` does not require a file to exist, so those joins succeeded and `relative_to(ROOT)` accepted them. The record came back with **503 read prefixes**, among them `00`, `a1`, `wt`, `repo` and `Library`: git object directories and fixture folder names from other people's temp directories, wearing repo-relative paths. It was a confident, well-formed measurement of nothing. Two guards now, not one: relative paths resolve against the **live** cwd, and the final prefix set is intersected with `git ls-files`' real top-level entries — a lane's diff can only name tracked paths, so that discards every remaining phantom without dropping a single genuine read. The second guard exists because the first is a fix, and a fix is a belief about what went wrong.

The failure direction was the safe one — with `sessions/` and `work-items/` both in a 503-entry set, no land would ever have been classified neutral — but "fails closed" is what made it survivable, not what made it correct. A measurement that cannot be wrong in the dangerous direction is still the thing this decision rests on, and it was wrong.

### D2 — Membership is decided at the directory, never at the leaf.

`sessions/journal/x.md` classifies as `sessions/`; `session.py` as itself. A per-file set would be enormous, would churn on every added fixture, and would let an unlisted *file* inside a read *directory* read as neutral — which it is not, because a suite that globs a directory reads whatever later appears in it. That is the `sessions/journal/` trap in its general form, and the grain makes it impossible to express rather than merely discouraged.

The rule lives in `session.py` (`_gate_top_prefix`) and `curate/gate_inputs.py` imports it. The deriver writes prefixes and the land compares against them, so the two must agree exactly; a second copy of a three-line rule is precisely the drift [P16](../principles/master.md#p16--avoid-duplication) names.

### D3 — Every branch fails CLOSED, and each one prints.

`_classify_gate_neutral` returns "skip" only when it positively knows the answer: a usable measurement exists, the diff was read, it was non-empty, and every changed path lies outside the measured set. No measurement, an unknown schema, a record measured over zero tests, an unreadable diff, an empty diff — all gate, each with its own line saying why.

The asymmetry is the design. Skipping a gate that was needed lands untested code and says nothing; running a gate that was not needed costs five minutes. Those costs are not comparable, so nothing is permitted to guess in the cheap direction.

**An empty diff gates.** It reads like the safest possible case and is not: a lane with genuinely nothing to land is refused earlier by its own ahead-of-trunk check, so by the time the classifier runs, "no changed paths" is a symptom of a bad read, never a licence.

### D4 — All three landers, not one.

`_land_candidate`, `_land_branch` and `_land_worktree_lane` share one helper (`_gate_unless_neutral`). [ADR-0116](0116-the-trunk-has-two-writers-and-now-one-door.md) D1 shipped covering one of four ref-advancing paths and had to be written up as a known gap the same day; doing that twice in two days would be a choice, not an oversight. `_integrate_trunk_with_remote` is deliberately excluded — the diff it gates is the remote's, not a lane's.

### D5 — The skip is a receipt, not a silence.

`gate:    SKIPPED — diff is gate-neutral: sessions/ (measured input set: N prefixes, none touched)`. A skip the operator cannot see in the land's own output is a silent behaviour change, and [`a-close-is-the-banner-not-the-sentence`](../habits/master.md#a-close-is-the-banner-not-the-sentence) applies to the gate as much as to a session close: the receipt is what makes "the suite did not run" a fact rather than an inference.

## Alternatives considered

**A `--skip-gate` flag the operator passes.** The brief believed one already existed; it does not — that help text belongs to `preflight --quick`. Rejected on its merits anyway: a human deciding from memory whether a diff is gate-neutral is the hand-list failure with an extra step, and the one person who would use it is the one whose time this is meant to save.

**Classify at re-gate only** (the consultant's original D1, without D1a). That skips the *second* suite when the trunk moves under a land. It leaves the first suite — the one that runs on every land — in place, so it does nothing for the 65-in-80 case and nothing for the thirty-minute close-out.

**Keep gating everything.** Honest, and what shipped until now. Rejected because the cost is not neutral: a five-minute tax on the cheapest lands is what makes operators avoid landing, and a lane that does not land is the stranded-work failure this repo keeps filing items about.

## Consequences

- **On the day this landed it skipped nothing, and the acceptance criterion was not met.** the operator's bar was that a journal-only close-out lands in under a minute with the gate reported SKIPPED. A journal-only land touches `sessions/`; `sessions/` was in the measured input set; so it gated. Only comms-only, ADR-only and handoff-only lands skipped — a small minority.
- **The unblocking work was making the suite hermetic against its own store**, and it was a specific, evidenced list rather than a suspicion: tests read 162 files under `sessions/journal/` and 109 under `work-items/`.

  **Closed the same day by WI-0295 — measured, not asserted.** `sessions/`, `work-items/`, `ops-items/` and `ROADMAP.md` are all out of the record; it went 24 read prefixes → 20. Of the last 80 trunk commits, **62 are now gate-neutral under the MEASURED set, exactly matching the 62 the brief assumed** — the assumption became true once the suite stopped reading its own store. Every remaining blocker is real code: `tests/` (14 of 80), `session.py` (12), `curate/` (2).

  It took **four** anchors, and the count is the lesson. `_shared_work_root()`, `JOURNAL_DIR` and `ROOT` removed `work-items/`, `ops-items/` and `ROADMAP.md` and left `sessions/` completely untouched — because the fourth route, `_find_holder_journal`'s `_journal_in_checkout(Path.cwd())` (sessionlib/journal.py:435), goes through none of the other three. The repo had already written that down: `exercise_real_coord_holder`'s docstring says *"The CWD is the fourth input, and it was missed"* and parks the CWD in an empty directory for precisely this reason. That guard was built for one opt-out case and never generalised, and the fixture written for the same hazard repeated the same omission.
- **Amended by [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) D2/D3 (2026-09-10):** the follow-on this bullet asks for happened twice. WI-0310 took the "fail the record when the suite's own hash moves" option; ADR-0124 replaces it with the "re-derive on a cadence" option this bullet names first, and reverses the refusal. The record's source is now the nightly OPS-0009 (withheld), no land derives or commits it, and a stale record **warns and disqualifies the neutral-skip** rather than refusing the land — so a stale record makes a land gate MORE, never less. The reasoning is in ADR-0124 D2; the short version is that a refusal was only tenable while the land carried its own deriver.
- **The measurement goes stale as the suite changes**, and nothing yet re-derives it. A record measured before a test learned to read a new directory would classify that directory as neutral. This is the one real hazard the design does not close, and it is named rather than hidden: refreshing the record is not wired to anything, and a follow-on should either re-derive on a cadence or fail the record when the suite's own hash moves.
- **The hook sees only the probe's own process.** `sys.addaudithook` is per-interpreter, so reads performed by processes the suite *spawns* — the two-process trunk-lock tests, any test shelling out to `session.py` or `git` against the real checkout — do not appear in the record. Most such tests operate on tmpdir fixtures and are correctly invisible either way, but this is an under-measurement, not a neutral simplification, and under-measuring is the direction that produces a wrong skip. Stated here because a record that silently omits a whole class of reads while looking complete is the exact failure D1 exists to prevent, turned on the deriver itself. The honest reading of `read_prefixes` is *"paths the suite reads in-process"*, and a follow-on should either audit the children too (an `PYTHONdev`-style wrapper on spawned interpreters) or declare the subprocess-heavy modules' fixture roots by hand and gate on them. **WI-0338 (2026-09-11) closes the adjacent hole rather than this one:** the same fact — a hook does not follow children — is why the deriver must never be pointed at the sharded `curate/run_suite.py`, and that consequence was prose in three files and enforced in none. `tests/test_gate_input_deriver_is_serial.py` now measures it: the probe's measurement, the command it declares, and the ordering of `_probe` around `sys.addaudithook`. The under-measurement this bullet names is unchanged.
- The classifier is entry-only. D1's re-gate half — skipping the *second* suite when a gate-neutral commit lands under a running land — is still unbuilt, as are WI-0293's decisions 2 and 3.
- Nothing here closes WI-0263, WI-0271 or WI-0272. It shortens the queue those items are about; it does not change their mechanics.
