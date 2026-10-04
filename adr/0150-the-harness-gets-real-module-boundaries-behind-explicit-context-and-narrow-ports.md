# ADR-0150: The harness gets real module boundaries behind explicit context and narrow ports

**Status:** Proposed
**Date:** 2026-10-02
**Deciders:** the operator (the direction, 2026-10-02, in the consultant chat: accept the consultant's recommendation on Astra's refactoring review; design the big redesign before 10-07, and hold all code until after the OPS-0010 soak). Federation Architect (the boundary design, the facade rule, the step order and the invariant lists recorded under Decision). **Revised 2026-10-02** by a cloud docs lane for the consultant, after an independent review (verdict *sound with fixes*). The facade guard, tripwire, cache, invariant lists and M0 were corrected. M13 and M14 were cut, the Git port narrowed, and claim checking deferred (D9). The per-finding record is `comms/2026-10-02-adr0150-review-fixes-cloud-branch.md`.
**Builds on:** [ADR-0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md), whose split was file layout only, with name resolution kept the same. This ADR is the next step ADR-0118 deferred, not a reversal of it.
**Must preserve:** [ADR-0148](0148-a-land-is-a-merge.md) D1–D5 (a land is a merge), [ADR-0122](0122-validation-reuse-and-resumable-completion.md) D2 (resume by reconciling against refs, never against receipts), and the lock ownership and order of [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md).
**Fits beside:** the refactor-cleanup brief (packages 1–5) and the Linux-support brief, both 2026-10-02. This ADR does not redo either one. Both have since landed on main: P1, P3, P4 and P5 as WI-0488 and WI-0490..WI-0492, and Linux support as WI-0468.
**Reality:** Not-built. Design only.
**Work items:** WI-0469 to WI-0481 (steps M0 to M12), all held until after the 2026-10-07 OPS-0010 soak. WI-0482 and WI-0483 (the former M13 and M14) are cut (D9). WI-0486 and WI-0487 hold the Q2 and Q3 rulings.
**Design:** [`design/harness-module-boundaries.md`](../design/harness-module-boundaries.md). It holds the module map, the interfaces, the phase semantics, the migration plan and the per-step checklist.

## Context

ADR-0118 moved 25k lines of `session.py` into `sessionlib/`, but `sessionlib.load` (`sessionlib/__init__.py:14-21`) still runs every part with `exec` into one shared namespace. The harness is now about 40k lines with one global namespace. Three things follow from that:

- **Runtime context is ambient.** The repo root (`ROOT`, `sessionlib/config.py:36`) is read by 84 functions and `CFG` (`sessionlib/config.py:561`) by 71. `CFG`'s paths are joined onto the real root at import (`_load_config`, `sessionlib/config.py:485`). There are 55 keyed `os.environ` reads of 24 names, plus 3 names read through `data_root_of`'s `env` argument. The design doc lists them exactly. The clock is read directly about 160 times. Machine identity comes from running `scutil` inside `detect_machine` (`sessionlib/config.py:729`). The 2026-09-27 nightly failure (WI-0432) came from exactly this: under launchd, `running_under_unit` (`deploy/runner.py:1959`) read `XPC_SERVICE_NAME`, and `_find_holder_journal` (`sessionlib/journal.py:562`) fell back to the working directory and reached the real checkout. A test-only fix closed that instance. The class of bug remains.
- **There are no boundaries a tool can check.** Any part can call or rebind anything. `_TRUNK_LOCK_DEPTH` is defined in config.py and mutated with `global` from coord.py (`sessionlib/coord.py:1892`). Until cleanup package 1, `_pid_alive` was defined twice and the later definition silently won. P1 left one definition (`sessionlib/config.py:6721`) and a guard on duplicate names, but nothing stops the next cross-part rebind. The land orchestrator `_land_worktree_lane` lives in `lanes.py` (`sessionlib/lanes.py:659`) but is mostly calls into `land.py`.
- **Test isolation depends on the shared namespace.** Tests patch `session` about 1,600 times: 846 `patch.object`, 485 direct assignments and 56 `setattr`. Each patch isolates the test only because every part sees the rebind. Under real modules, those patches would silently stop reaching the code and **become live writes**. That is why ADR-0118 could not go further. It is the constraint this decision has to answer. The worst case is a patch on a helper that unmoved code still uses, such as the core fixture's `_shared_work_root` patch (`tests/coord_fixture.py:182`). The patch succeeds and reaches the unmoved code, and it silently misses the moved code.

The three lifecycle functions are long and mix decisions with effects: `_cmd_start` (`sessionlib/hooks.py:227`) is about 507 lines, `cmd_end` (`sessionlib/hooks.py:841`) about 401, and `_land_worktree_lane` about 406. The end protocol asks the model to type mechanical facts (ids, commits, landed state) that code already knows.

## Decision

**D1. Explicit runtime context, as small values, not one object.** Paths (`Layout`), configuration (`Config`), identity (`Identity`), time (`Clock`) and the environment (`Env`) are frozen values passed in. Only an adapter may read `os.environ`, `Path.home()`, `Path.cwd()`, `sys.stdin` or the wall clock, and it does so once, at the entry of a verb or hook. A bundle (`RuntimeContext`) exists only to carry these values from an adapter to the top-level phase. **No function below the lifecycle layer accepts the bundle.** Each one takes only the values it uses. Identity resolution becomes a pure function, `resolve_identity(env, layout, cwd)`, so the WI-0432 fallback is testable.

**D2. Four narrow ports.**
- `Git`: **only** `update_ref_cas` (the land's CAS) and `push` returning a typed publish result (accepted / rejected / unreachable). Every other git call stays on `sh()` with literal argv (D9).
- `Proc`: process running, plus the liveness contract cleanup package 1 landed, moved as it is: `_pid_liveness` (three-state) and `_pid_alive` (False only when provably dead).
- `Git` and `Proc` share one runner whose child environment comes only from `Env.child_env()`. That makes today's whole-environment inheritance (every caller's `GIT_*` reaches about 305 git calls) explicit, **without changing it**. Stripping those names is a separate decision.
- `CoordStore`: `try_acquire` returns acquired / held / **unavailable** as a typed value, and the lock context managers are built on top of it.
- `Platform`: machine name, memory pressure, quarantine, GUI session, terminal launch, birthtime and process table, with macOS and Linux implementations. This is the seam the Linux-support brief needs.

launchd unit management is not part of `Platform`. It stays inside `deploy/runner.py`, macOS-only. The runner split that would have given it a `Units` port is cut (D9).

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

Each phase returns a structured result. **Progress is the one thing a phase emits as it happens**, through an injected `emit`, before slow work: the land-queue position (up to 90 minutes), the suite run, the integrate. Progress never goes into a returned result. The design doc lists the invariants for each orchestrator: I-S1–I-S7, I-E1–I-E6 and I-L1–I-L12. They cover:
- lock order and ownership, including the second gated section a forced resume takes, the trunk lock's longer hold in `_store_autocommit`, and the rule that no child spawned under the gate takes it;
- refusal paths, exit codes, and **which refusals write a receipt and which do not**;
- the retry boundary: the verdict (`accepted`), the one `_CasBudget` generator and the lane's rewritten HEAD all carry from attempt N to attempt N+1;
- the CAS as the only all-or-nothing point, and no rollback, **with the shared writes that happen before it named** (the verdict store, a trunk fast-forward to origin, number draws);
- `Resumed` kept distinct from `Landed`, so a resume never dispatches;
- the exceptions that escape each phase today;
- the runtime-close order against the republish;
- the receipt clock's two lifetimes (`_READY_TO_LAND` survives refusals; `_LAND_BUILD` is cleared at every lander entry).

**The invariant lists are the acceptance contract. Splitting a transaction is not a license to change its semantics.** A contract that misdescribes today's code would either block a correct split or approve a wrong one. So the review's six corrections (I-S1, I-L1, I-L3, I-L6, I-L10, I-E1) are made in the design doc, and **M0 pins every corrected invariant with a test on today's code** before any split. Today's fail-open locks stay fail-open, and the guard tests that read the lander's source are retargeted to the phases, not weakened.

**D5. The end-protocol evidence packet.**
- **Code supplies:** ids, timestamps, commits, changed paths, items touched, ADRs drawn, test verdicts, land state and receipts.
- **The model writes only meaning:** narrative, state at close, parked question, outcome, focus, blocked, title, learnings and memories.
- **Code validates and renders.** Required sections are present, unknown ids are flagged, and code renders the mechanical sections itself. **Checking prose claims about outcomes against the packet is deferred** (D9).
- **The packet keeps `command` (what a verb returned) and `outcome` (what a fresh read of refs and records shows) as separate fields that are never merged.** A successful command is not evidence that the outcome holds.
- The `standard-source.md` edit ships in the same step as the code, never before it.

**D6. Migration one subsystem at a time, behind per-call facades.**
- The moved code takes explicit arguments.
- The old name stays in the exec namespace as a wrapper, **replacing the definition in its own part**, with `__wrapped__` set. On every call, the wrapper builds the values the callee takes **from the namespace's current globals**. **`Layout` and `Config` are rebuilt on every call.** Only the subprocess-backed values (`scutil`, the git common dir) are cached, keyed on `ROOT` together with the identities of `_git_common_dir` and `_shared_work_root`. So an in-place `CFG` edit or a patched common dir is never served stale. A patch on a facade name is therefore harmless.
- Callbacks into the namespace are late-bound and declared, so a patch of such a name still reaches moved code.
- No moved code reads `__file__`. The tests dir and the entry script are computed once from `session.py`'s path and injected. Otherwise the suite detector would silently answer False and switch both guards off.
- Process-lifetime state stays process-lifetime: the lock re-entrancy registry and the receipt clock.
- **The real hazard is a patch on a name that moved code reads but unmoved code still uses.** The patch succeeds, and it silently misses the moved code. Three defenses:
  - a per-step ledger from the AST free names of the moved code, each site rewritten in the same step;
  - a guard that **computes the protected set at test time** (the free names of every moved module, minus the declared late-bound names), so tests written after a step are checked too;
  - **a runtime refusal**: moved values are deleted from the namespace, and the `session` module refuses assignment to any protected name. That also catches the dynamic `setattr` loops a static guard cannot see, and the direct `session.X = …` that would otherwise silently create a dead global.
- **A live-state tripwire** in the context builder and in the real ports checks **every path field of `Layout` and `Config`**, under the suite, against the launch checkout, the main checkout and the real home. The suite redirects `HOME` at its entry, before `session` is imported. Only then is the HOME check turned on. **`atomic_write`'s suite write floor widens** from the real `.session-state/` to any path under the launch or main checkout, so a write to the real `STATUS.md` or handoff is refused. Both guards have negative-control self-tests in M0. Tests that run `session.py` as a child go through one helper with a clean environment and a temp HOME.
- Facades only decrease. The last one goes with the assembler, and with the `curate/` importers of `session`.
- **Rollback is roll forward, or revert the newest step only.** Once a later step builds on an earlier one, reverting the earlier one is not clean. M8–M11 ship behind one milestone (Q1), so they roll back as one unit. Because M9 changes journal sections and the standard version, that rollback also needs a standard-version bump.

**D7. Order.**
1. **M0**, a test floor, tests only. It covers two landers as two processes, unreachable push through the whole lander, CAS failure, a real recover-lanes, cross-process stale-lock reclaim, a hermetic launchd-like nightly, end crashing between close and land, and golden CLI and hook output (start JSON with a pinned clock and machine). It also adds the guard self-tests, the child-runner helper, the suite-entry HOME redirect, and tests that pin each corrected invariant.
2. **M1–M7** move one layer at a time: M1 context and ports, M2 identity/clock/env, M3 process and platform, M4 coord, M5 store/journal/views/goal/grants, M6 land/trunkcheck/releases, M7 lanes and shell policy.
3. **M8–M10**, the start, end and lander phases.
4. **M11**, the adapters.
5. **M12**, retiring the assembler.

Every step is **blocked until after the 2026-10-07 OPS-0010 soak**. External dependencies are encoded as `blocked-by` links, so the board shows them, not as prose: M1 on cleanup packages 3 and 4 (WI-0490, WI-0491), and M3 on package 1 and the Linux lane (WI-0488, WI-0468). The code for all four has landed. The links clear when those items close.

**D8. Proof per step.** Each step shows the ten-point checklist in the design doc, §8:
1. The module imports clean with HOME empty and subprocess booby-trapped.
2. The layers hold.
3. No patch reaches moved code (the test-time guard and the runtime refusal), and the tripwire and floor are on.
4. No ambient reads remain below L5, including `__file__` and `sys.path`.
5. Golden outputs are byte-identical.
6. The capability manifest's wiring checks and probes are unchanged on a member.
7. A bootstrapped member starts.
8. The land floor passes (for land-touching steps).
9. The test count does not fall, and every rewritten test is shown to fail against a mutation.
10. The close note quotes outcome reads, not exit codes.

**D9. What the review cut or narrowed (2026-10-02).**
- **The `deploy/runner.py` and `poga_cli.py` splits (formerly M13, M14) are cut.** Neither file imports sessionlib, so neither is part of the shared-namespace problem. A 4k-line runner split is not worth a fleet release for a one-operator system. The `poga_cli.py` split would add an import edge from the member-restore tool (today it imports only `bootstrap` and `poga_evidence`, `poga_cli.py:103-104`) into the harness package, which is a new way for recovery to fail. Re-reading the code found no concrete reason to keep either split. The one useful piece, a typed `Env` read in `running_under_unit()`, is recorded as a hazard. It can be its own small item if wanted.
- **The `Git` port is narrowed** to `update_ref_cas` and the publish result. Typing all ~305 call sites would cost a large rewrite and more test churn for little gain. `sh()` stays the runner for everything else.
- **Prose-versus-evidence claim checking is deferred.** M9 ships the code-rendered mechanical sections and id validation. A read-only audit after M9 measures how often the prose disagrees with the packet (the Q4 measurement). Claim checking is designed only if misreports actually happen. If it ships, it warns first, as the operator ruled on Q4.

## Alternatives considered

- **Keep the exec namespace and split functions within it.** This is cheaper, but it leaves no boundary a tool can check, and the ambient-context bug class stays open. Astra §1 and the brief both rule it out as the end state. It stays, as the facade, *during* the migration.
- **One `Services`/`Context` object passed everywhere.** This replaces 27 env names and a dozen globals with one bag every function can reach into. It is the same coupling under a new name, and the brief forbids it. D1 keeps the bundle above L4 only.
- **Big-bang move to real modules, rewriting all tests at once.** That means about 1,600 patch sites and a 123-symbol cycle in one change. A miss would turn an isolating patch into a live write, silently. It is not shippable in steps and cannot be rolled back in parts.
- **Rewrite rather than extract.** The review explicitly advises against a wholesale rewrite near completion. The harness's behavior is the specification, and it is pinned by ~190 test files.
- **Fix the recorded hazards inside the refactor.** These are a fixed temp path in `atomic_write`, reclaim without a re-read, unlocked dispatch records, fail-open locks, inherited `GIT_*` variables, and the dispatched reaper racing the republish. Rejected: changing structure and behavior in one step makes a regression impossible to attribute. They are recorded for review as separate decisions.
- **Keep the full typed `Git` port, the M13/M14 splits and claim checking** (the earlier draft). Rejected after review (D9). Each one added cost and risk without serving the shared-namespace problem, or before there was evidence it was needed.

## Consequences

- **Positive.** Context bugs like WI-0432 become impossible to write below the adapter layer, not just fixed one by one. Locks, refusals and retries become phase results a test can assert directly. The Linux port gets one seam (`Platform`) instead of call sites spread across five files. The end protocol stops asking the model to type facts code already holds. M0's invariant pins make today's actual semantics explicit, including the parts the earlier draft got wrong.
- **Negative.** The work is 13 steps (M0–M12): M1–M7 with a fleet release each, then M8–M11 behind one milestone. The facade layer adds a second name for each moved function until its step completes. The runtime assignment refusal changes how `session` behaves under tests: a test that assigns a moved name now fails loudly. That is the point, but each step pays for it in rewrites. The widened write floor may refuse writes today's suite makes, and M0 measures and fixes those first. Every detector, distributor and doc check that lists harness files must learn subpackages first: the capability manifest's caller walk (`_harness_paths`, `standard_check.py:202`), `sessionlib_files` (`bootstrap.py:289`), `curate/push-substrate.py:128`, `curate/check_substrate_docs.py`.
- **Risk, top three.**
  - (1) A patch that silently stops isolating. D6's ledger, test-time guard, runtime refusal, tripwire and floor exist for this. Each has a negative control.
  - (2) A lock-order or retry change hidden inside a phase split. D4's corrected invariant lists, their M0 pins and the M0 land floor exist for this.
  - (3) Capability detection marking a healthy fleet red. Package 3's manifest has landed. Each step updates the manifest's call edges for anything it moves, and proves the probes are unchanged on a member.
- **Downstream.** Implementation work items M0–M12 (WI-0469..WI-0481), each blocked until after 2026-10-07. WI-0482 and WI-0483 are closed as cut. Open questions Q1–Q4 are in the design doc, §10. **the operator ruled all four on 2026-10-02, each as recommended:** (Q1) one release per step for M1–M7, then M8–M11 behind one milestone; (Q2) a land stays fail-open when the lock store is unavailable through the redesign, and fail-closed is decided separately (WI-0486); (Q3) `end` records its progress so the next start surfaces a crash mid-close, as its own item after M9 (WI-0487); (Q4) a prose-versus-evidence disagreement warns first and is measured before any refusal. Under D9 the measurement comes first and the check is built only if it is needed. The rulings settle these questions only; this ADR stays Proposed.

## References

- Brief: `proposed-edits/federation-arch/accepted/2026-10-02-consultant-harness-redesign-design.md`. Source review: `proposed-edits/federation-arch/accepted/2026-10-02-astra-refactoring-review.md`.
- Sibling briefs (pending, other lanes): `2026-10-02-consultant-refactor-cleanup.md` and `2026-10-02-consultant-linux-support.md`.
- [ADR-0118](0118-session-py-is-a-package-behind-a-thin-entry-point.md), [ADR-0148](0148-a-land-is-a-merge.md), [ADR-0122](0122-validation-reuse-and-resumable-completion.md), [ADR-0124](0124-the-land-lock-holds-the-merge-not-the-validation.md).
- WI-0432, the 2026-09-27 launchd nightly failure.
- Review dispositions: `comms/2026-10-02-adr0150-review-fixes-cloud-branch.md`.
