# ADR-0150: The harness gets real module boundaries behind explicit context and narrow ports

**Status:** Proposed
**Date:** 2026-10-02
**Deciders:** the operator (the direction, 2026-10-02, in the consultant chat: accept the consultant's recommendation on Astra's refactoring review; design the big redesign before 10-07, and hold all code until after the OPS-0010 soak). Federation Architect (the boundary design, the facade rule, the step order and the invariant lists recorded under Decision).
**Builds on:** [ADR-0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md), whose split was file layout only, with name resolution kept the same. This ADR is the next step ADR-0118 deferred, not a reversal of it.
**Must preserve:** [ADR-0148](0148-a-land-is-a-merge.md) D1–D5 (a land is a merge), [ADR-0122](0122-validation-reuse-and-resumable-completion.md) D2 (resume by reconciling against refs, never against receipts), and the lock ownership and order of [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md).
**Fits beside:** the refactor-cleanup brief (packages 1–5) and the Linux-support brief, both 2026-10-02. This ADR does not redo either one.
**Reality:** Not-built. Design only.
**Work items:** WI-0469 to WI-0483 (steps M0 to M14), all held until after the 2026-10-07 OPS-0010 soak.
**Design:** [`design/harness-module-boundaries.md`](../design/harness-module-boundaries.md). It holds the module map, the interfaces, the phase semantics, the migration plan and the per-step checklist.

## Context

ADR-0118 moved 25k lines of `session.py` into `sessionlib/`, but `sessionlib/__init__.py:14-21` still runs every part with `exec` into one shared namespace. The harness is now 40.5k lines with one global namespace. Three things follow from that:

- **Runtime context is ambient.** The repo root (`sessionlib/config.py:36`) is read by 84 functions and `CFG` (`sessionlib/config.py:561`) by 71. There are 57 `os.environ` reads of 26 names. The clock is read directly about 160 times. Machine identity comes from running `scutil` inside `detect_machine` (`sessionlib/config.py:719`). The 2026-09-27 nightly failure (WI-0432) came from exactly this: under launchd, `deploy/runner.py:1959` read `XPC_SERVICE_NAME`, and `_find_holder_journal` (`sessionlib/journal.py:562`) fell back to the working directory and reached the real checkout. A test-only fix closed that instance. The class of bug remains.
- **There are no boundaries a tool can check.** Any part can call or rebind anything. `_TRUNK_LOCK_DEPTH` is defined in config.py and mutated with `global` from coord.py (`sessionlib/coord.py:1892`). `_pid_alive` is defined twice, and the later definition silently wins (`sessionlib/config.py:6669`, `sessionlib/config.py:7092`). The land orchestrator lives in `lanes.py` (`sessionlib/lanes.py:659`) but is mostly calls into `land.py`.
- **Test isolation depends on the shared namespace.** Tests patch `session` about 1,600 times: 846 `patch.object`, 485 direct assignments and 56 `setattr`. Each patch isolates the test only because every part sees the rebind. Under real modules, those patches would silently stop reaching the code and **become live writes**. That is why ADR-0118 could not go further. It is the constraint this decision has to answer.

The three lifecycle functions are long and mix decisions with effects: `_cmd_start` is 489 lines, `cmd_end` 399, and `_land_worktree_lane` 404. The end protocol asks the model to type mechanical facts (ids, commits, landed state) that code already knows.

## Decision

**D1. Explicit runtime context, as small values, not one object.** Paths (`Layout`), configuration (`Config`), identity (`Identity`), time (`Clock`) and the environment (`Env`) are frozen values passed in. Only an adapter may read `os.environ`, `Path.home()`, `Path.cwd()`, `sys.stdin` or the wall clock, and it does so once, at the entry of a verb or hook. A bundle (`RuntimeContext`) exists only to carry these values from an adapter to the top-level phase. **No function below the lifecycle layer accepts the bundle.** Each one takes only the values it uses. Identity resolution becomes a pure function, `resolve_identity(env, layout, cwd)`, so the WI-0432 fallback is testable.

**D2. Four narrow ports.**
- `Git`: typed verbs such as `update_ref_cas`, and `push` returning accepted / rejected / unreachable. There is no general `run(argv)`.
- `Proc`: process running, plus a three-state liveness answer: alive / dead / unknown.
- `CoordStore`: `try_acquire` returns acquired / held / **unavailable** as a typed value, and the lock context managers are built on top of it.
- `Platform`: machine name, memory pressure, quarantine, GUI session, terminal launch, birthtime and process table, with macOS and Linux implementations. This is the seam the Linux-support brief needs.

launchd unit management is not part of `Platform`. It is a `Units` port inside `deploy/`, and it stays macOS-only.

**D3. Layered modules; imports point down only.**

| Layer | Contents |
|---|---|
| L0 | Pure policy: shell policy, briefs, frontmatter, verdict functions |
| L1 | Context and ports |
| L2 | Repositories: coord, store, journal, views, grants |
| L3 | Transactions: land, lanes, trunkcheck, releases |
| L4 | Lifecycle orchestration: start, end, lander |
| L5 | Adapters: CLI families, hook I/O |

- `land` and `lanes` do not import each other. Orchestration that spans them moves up to L4.
- No module imports the exec assembler.
- An AST import-boundary test enforces this, with a planted upward import as its negative control.

**D4. Lifecycle as phases with invariant lists.**
- `_cmd_start` becomes Inputs → Idempotency → Sync → Mutations → Orientation → Emit.
- `cmd_end` becomes Lazy → Refusals (a pure verdict) → Evidence → Close → Land → After.
- `_land_worktree_lane` becomes Verdict → Prepare → Attempt (looped) → Advance → After → Dispatch.

Each phase returns a structured result and never prints. The design doc lists the invariants for each orchestrator: I-S1–I-S7, I-E1–I-E5 and I-L1–I-L10. They cover:
- lock order and ownership, including the second gated section a forced resume takes;
- refusal paths, exit codes and receipts;
- the retry boundary, with the verdict carried across attempts by the orchestrator;
- the CAS as the only all-or-nothing point, and no rollback;
- the receipt clock as process-lifetime state. **The invariant lists are the acceptance contract. Splitting a transaction is not a license to change its semantics.** Today's fail-open locks stay fail-open, and the guard tests that read the lander's source are retargeted to the phases, not weakened.

**D5. The end-protocol evidence packet.**
- **Code supplies:** ids, timestamps, commits, changed paths, items touched, ADRs drawn, test verdicts, land state and receipts.
- **The model writes only meaning:** narrative, state at close, parked question, outcome, focus, blocked, title, learnings and memories.
- **Code validates and renders.** Unknown ids are flagged, prose claims about outcomes are checked against the packet, and code renders the mechanical sections itself.
- **The packet keeps `command` (what a verb returned) and `outcome` (what a fresh read of refs and records shows) as separate fields that are never merged.** A successful command is not evidence that the outcome holds.
- The `standard-source.md` edit ships in the same step as the code, never before it.

**D6. Migration one subsystem at a time, behind per-call facades.**
- The moved code takes explicit arguments.
- The old name stays in the exec namespace as a wrapper, **replacing the definition in its own part**, with `__wrapped__` set. On every call, the wrapper builds the values the callee takes **from the namespace's current globals**, cached by root. A not-yet-migrated `session.ROOT = tmp` therefore still isolates.
- Callbacks into the namespace are late-bound, so a patch of a name still in the namespace still reaches moved code.
- Process-lifetime state stays process-lifetime: the lock re-entrancy registry and the receipt clock.
- Every test that patches a name **the moved code defines or uses** (found by an AST walk of its free names) is rewritten in the same step. A guard (`test_no_patch_targets_a_facade`) refuses any patch of a name in the facade registry. Dynamic `setattr` loops are a named blind spot, reviewed by hand.
- A live-state tripwire in the context builder and in the real ports refuses the operator's real checkout or home under the suite. `atomic_write` keeps its suite write floor when it moves.
- Facades only decrease. The last one goes with the assembler, and with the `curate/` importers of `session`.
- Each step is one land and is rolled back by reverting that land. **The one exception is the end-protocol step (M9).** It changes journal sections and the standard version, so its rollback also needs a standard-version bump.

**D7. Order.**
1. **M0**, a test floor, tests only. It covers two landers as two processes, unreachable push through the whole lander, CAS failure, a real recover-lanes, cross-process stale-lock reclaim, a hermetic launchd-like nightly, end crashing between close and land, and golden CLI and hook output.
2. **M1–M7** move one layer at a time: M1 context and ports, M2 identity/clock/env, M3 process and platform, M4 coord, M5 store/journal/views/goal/grants, M6 land/trunkcheck/releases, M7 lanes and shell policy.
3. **M8–M10**, the start, end and lander phases.
4. **M11**, the adapters.
5. **M12**, retiring the assembler.
6. **M13 and M14**, `deploy/runner.py` and `poga_cli.py`. Neither imports sessionlib; they come last by choice, not by dependency.

Every step is **blocked until after the 2026-10-07 OPS-0010 soak**. M1 also waits for the cleanup lane's capability description (package 3), because text-matching detectors would mark a healthy fleet as missing capabilities the moment a function moves. It waits for that lane's first real extraction (package 4) too.

**D8. Proof per step.** Each step shows the ten-point checklist in the design doc, §8. The module imports clean with HOME empty and subprocess booby-trapped. The layers hold. No patch targets a facade. No ambient reads remain below L5. Golden outputs are byte-identical. Capability probes are unchanged on a member. A bootstrapped member starts. The land floor passes (for land-touching steps). The test count does not fall, and every rewritten test is shown to fail against a mutation. The close note quotes outcome reads, not exit codes.

## Alternatives considered

- **Keep the exec namespace and split functions within it.** This is cheaper, but it leaves no boundary a tool can check, and the ambient-context bug class stays open. Astra §1 and the brief both rule it out as the end state. It stays, as the facade, *during* the migration.
- **One `Services`/`Context` object passed everywhere.** This replaces 26 env names and a dozen globals with one bag every function can reach into. It is the same coupling under a new name, and the brief forbids it. D1 keeps the bundle above L4 only.
- **Big-bang move to real modules, rewriting all tests at once.** That means about 1,600 patch sites and a 123-symbol cycle in one change. A miss would turn an isolating patch into a live write, silently. It is not shippable in steps and cannot be rolled back in parts.
- **Rewrite rather than extract.** The review explicitly advises against a wholesale rewrite near completion. The harness's behavior is the specification, and it is pinned by ~190 test files.
- **Fix the recorded hazards inside the refactor.** These are a fixed temp path in `atomic_write`, reclaim without a re-read, unlocked dispatch records, and fail-open locks. Rejected: changing structure and behavior in one step makes a regression impossible to attribute. They are recorded for review as separate decisions.

## Consequences

- **Positive.** Context bugs like WI-0432 become impossible to write below the adapter layer, not just fixed one by one. Locks, refusals and retries become phase results a test can assert directly. The Linux port gets one seam (`Platform`) instead of call sites spread across five files. The end protocol stops asking the model to type facts code already holds.
- **Negative.** The work is 15 steps (M0–M14), most with their own fleet release. The facade layer adds a second name for each moved function until its step completes. Every detector, distributor and doc check that lists harness files must learn subpackages first (`standard_check.py:93`, `bootstrap.py:257`, `curate/push-substrate.py:126`, `curate/check_substrate_docs.py`).
- **Risk, top three.**
  - (1) A patch that silently stops isolating. D6's ledger, guard and tripwire exist for this.
  - (2) A lock-order or retry change hidden inside a phase split. D4's invariant lists and the M0 land floor exist for this.
  - (3) Capability detection marking a healthy fleet red. D7 sequences M1 after cleanup package 3.
- **Downstream.** Implementation work items M0–M14, each blocked until after 2026-10-07. Open questions Q1–Q4 are in the design doc, §10. **the operator ruled all four on 2026-10-02, each as recommended:** (Q1) one release per step for M1–M7, then M8–M11 behind one milestone; (Q2) a land stays fail-open when the lock store is unavailable through the redesign, and fail-closed is decided separately (WI-0486); (Q3) `end` records its progress so the next start surfaces a crash mid-close, as its own item after M9 (WI-0487); (Q4) a prose-versus-evidence disagreement warns first and is measured before any refusal, folded into M9. The rulings settle these questions only; this ADR stays Proposed.

## References

- Brief: `proposed-edits/federation-arch/accepted/2026-10-02-consultant-harness-redesign-design.md`. Source review: `proposed-edits/federation-arch/accepted/2026-10-02-astra-refactoring-review.md`.
- Sibling briefs (pending, other lanes): `2026-10-02-consultant-refactor-cleanup.md` and `2026-10-02-consultant-linux-support.md`.
- [ADR-0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md), [ADR-0148](0148-a-land-is-a-merge.md), [ADR-0122](0122-validation-reuse-and-resumable-completion.md), [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md).
- WI-0432, the 2026-09-27 launchd nightly failure.
