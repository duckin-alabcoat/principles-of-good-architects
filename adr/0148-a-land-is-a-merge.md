# ADR-0148: A land is a merge — validation leaves the land path

**Status:** Proposed
**Date:** 2026-09-25
**Deciders:** the operator (the direction, 2026-09-25: fix the land before the next fan-out, because lands were taking more than a minute; go on the consultant's brief in-session; and measure under live load rather than waiting for a quiet window). Federation Architect (the code-tree key, the pre-clock build, the static allowlist's guard and its reach into child processes, the trunk-check refusal rules, the retirement of the measured classifier, and the other choices recorded under Decision).
**Supersedes:** [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md) **"validation runs before queueing" only** (D1's placement of the suite in the lander, and its retention of ADR-0114 D7's "every rebase re-gates"). The serialized section itself (`_serialized_advance`, the parent-equals-trunk check, the receipt) stands unchanged. [ADR-0117](0117-a-land-classifies-its-own-diff-before-it-gates.md) **D1–D3's dependency on the derived record**, narrowly: gate-neutrality is now declared, not measured.
**Amends:** [ADR-0140](0140-the-integrate-validates-outside-the-land-gate.md) D1 (the integrate no longer runs the suite at all, only the cheap checks); OPS-0009 (the nightly derive is the order-contamination detector only).
**Builds on:** [ADR-0119](0119-the-gates-three-side-doors-are-shut.md) D4 (`POGA_GATE=1` tells the suite it is the gate), [ADR-0127](0127-the-gate-input-measurement-follows-its-own-subprocesses.md) (the child-process audit shim this reuses the shape of), [ADR-0122](0122-validation-reuse-and-resumable-completion.md) D1's evidence standard (a reused verdict must agree on tree, interpreter, runner and environment).
**Reality:** Built — see WI-0427.
**Work item:** WI-0427

## Context

The land receipts (`.git/land-receipts.jsonl`) showed the shape: the lock was held for seconds, while validation took minutes — its tail past ten minutes — and so did the land as a whole. The design budget is a land measured in seconds.

- Validation was ~98% of every land. ADR-0124 moved it out of the lock, as it said it would, and left it on the land path. FL7 measured the lock, so it read green while lands took minutes.
- 144 receipts (38%) were `moved`: the trunk advanced during validation, and the lane re-validated from scratch.
- 75 of 231 landed diffs (32%) touched only bookkeeping. Each paid a full validation of minutes. The gate-neutral skip (ADR-0117) fired **zero** times after WI-0329, because it needed a fresh record from the scheduled derive, and that record went stale whenever the serial suite was red.

This is the second failure at the land interface after a fix was declared (ADR-0114, then ADR-0124). Redesign, not patch.

## Decision

**D1. A land is a merge.** The land verb checks, merges and pushes. It never runs the suite. What still runs at every land are the cheap gate checks (`wi-check`, the generated-file `--check`s, citations — about 8 s together), in the candidate's scratch worktree, because several of them read exactly the bookkeeping a neutral diff changes. `_land_gate` is the one helper every lander (lane, candidate, branch) calls; the integrate runs the same cheap checks.

**D2. Validation is the lane's, bound to its tree.** A verdict is `{tree, green, suite, source, seconds, at}`, filed in the git common dir (`land-verdicts/<tree>.json`, local, 14-day retention). `tree` is the **code tree**: the lane's tree with every `BOOKKEEPING_PATHS` entry removed, built in a private index. Keyed that way, the verdict `poga test` files over the working tree and the tree the land commits (plus the closed journal) are the same key by construction. `suite` fingerprints the suite command(s), interpreter and gate environment, so a green from another Python or runner does not count.
- A full sharded `poga test` files a verdict, and runs with `POGA_GATE=1` so it is the same run the gate would have made. The tree is read before and after; a run that raced an edit files nothing.
- A lane with no verdict for its tree is told so, and the land verb runs the suite once **in the lane, before the land clock starts**. That time is recorded on the receipt as `build_seconds`, not dropped: it is the session's build time, and the receipt says so.
- A red lane is refused with its failing tests named.

**D3. Bookkeeping never validates.** `BOOKKEEPING_PATHS` (config.py): `work-items/ sessions/ ops-items/ proposed-edits/ comms/ outbox/ releases/ ROADMAP.md STATUS.md session-handoff.md gate-inputs.json`. A diff whose code tree is unchanged is gate-neutral, with no dependency on any derived record. **What makes a declared list safe is its guard:** `curate/store_guard.py` arms an audit hook in every `run_suite.py` worker, and through `curate/storeguard_child/sitecustomize.py` in every Python child, and fails by name any test that opens, lists or scans those paths under the real checkout or the main checkout. The first measurement found **2,771 of 6,234 tests** reading the live store; after removing a false positive (`shutil.rmtree`'s `dir_fd`-relative opens) it was **104 tests in 16 modules**, and they were made hermetic in this item. `tests/coord_fixture.py` `point_store_at` is the named fixture.

**D4. `moved` does not re-validate.** When the trunk advances under a ready lane, the lane rebases; if the rebase is clean, it lands on the verdict it already has. The receipt says `validate 0` and why. Two independently-green lanes that are red together are D5's to catch.

**D5. The trunk verifies itself after the merge.** After every successful advance the lander spawns a detached `session.py trunk-check --run` and does not wait for it. One runner at a time (an O_EXCL lock in the common dir); coalescing (the runner re-reads the trunk after each check and checks only the newest tip). Red → `trunk-check.json` names the failing tests and the land that turned it red (bisected over land boundaries, running only the failing tests' modules, when several lands were coalesced), and proposes reverting it. The next **code** land is refused with those names; a bookkeeping land still goes through, and so does a lane whose tree was built on the red tip (it may be the fix). **The tradeoff, for the operator:** main can be red for the minutes between a bad land and its check. With ~10 code lands a day on one machine and every lane green on its own tree, that is the right trade.

**D6. The nightly derive stops touching land speed.** Nothing on the land path reads `gate-inputs.json` any more. `_gate_record_freshness`, `_classify_gate_neutral`, `_gate_skip_plan`, `_gate_command_prefixes`, `_gate_skip_receipt` and `_gate_unless_neutral` are deleted rather than left unreachable (ADR-0124's precedent). The serial derive remains the only detector of order-dependent test pollution; red at night is a comms note and an item (OPS-0009).

**D7. FL7 measures what the operator waits for.** FL7 = `total_seconds` p90 over the last 7 days ≤ 60 s, split code / bookkeeping. Lock hold stays in the receipt as a stage.

**Receipt fields added** (the existing stages and phases are kept): `verdict` (source, tree, suite, rebased, reason), `gate_neutral`, `diff_class`, `checks_seconds` and `phases.checks`, `build_seconds`, `trunk_check` (spawned, pid or reason), and `refused` on a trunk-check refusal. `validate_seconds` is now suite time only.

## Alternatives considered

- **Keep ADR-0117's measured set and fix the nightly.** Rejected: it ties land speed to the one run whose job is to be red when order-dependence appears. D6 makes that independence structural.
- **Key verdicts on the raw tree.** Rejected: the close commit always changes the tree, so every verdict a session earned would be orphaned at the moment it was needed.
- **Re-run the suite on every rebase (ADR-0114 D7).** Rejected per D4; it is what made 38% of lands pay twice.
- **Run the missing-verdict build inside the land clock.** Rejected: the brief defines it as build time. It is recorded, not hidden: `build_seconds` is on the receipt.
- **Raise from the audit hook.** Rejected: the harness is full of fail-open `except Exception` blocks, and a refusal raised inside one is read as success. The hook records and the worker fails the test.

## Consequences

- A land's cost is the checks (~8 s), the rebase, and the lock (~2 s). The suite runs in the lane during work, and on the trunk after the merge.
- Main can be red between a bad land and its trunk check. The next code land is refused until it is fixed or reverted.
- The guard's known blind spots: a non-Python child (git, sh), a child that drops `PYTHONPATH` or runs `-I`/`-E`, a child killed without `atexit`, and a bare relative filename opened with the real repo as its working directory. Whatever those still read, D5 is the backstop.
- Members ship `sessionlib/` byte-identical. A member's gate whose suite is `-m unittest` gets the same behaviour; a gate with no suite command runs everything as checks (`no-suite`).

## References

- Brief: `proposed-edits/federation-arch/pending/2026-09-25-consultant-a-land-is-a-merge.md`
- WI-0427; WI-0325, WI-0329 journals; ADR-0114, ADR-0117, ADR-0122, ADR-0124, ADR-0140
