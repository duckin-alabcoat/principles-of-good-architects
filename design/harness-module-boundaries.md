# Design — harness module boundaries (explicit context, narrow ports, phased lifecycle)

**Status:** DESIGN, **Proposed** with [ADR-0150](../adr/0150-the-harness-gets-real-module-boundaries-behind-explicit-context-and-narrow-ports.md). No code changes. the operator ruled on 2026-10-02 that the design is done now and **all implementation waits until after the OPS-0010 soak (due 2026-10-07)**.
**Drafted:** session ~444 (2026-10-02), Federation Architect, at trunk `dc74d848`. **Revised:** 2026-10-02, cloud docs lane, at trunk `c38a844b`, after an independent review (verdict *sound with fixes*). Each finding was re-checked against the code. The disposition of each one is in `comms/2026-10-02-adr0150-review-fixes-cloud-branch.md`, and §11 below summarizes what changed.
**Source:** `proposed-edits/federation-arch/accepted/2026-10-02-consultant-harness-redesign-design.md` (the brief), with Astra's review `…/accepted/2026-10-02-astra-refactoring-review.md` §1, §3, §4 and §6 and its acceptance criteria.
**Fits beside, and does not redo:** the refactor-cleanup brief (packages 1–5: one liveness helper, adoption prompt, capability detection, first brief-parsing extraction, CLI registration split) and the Linux-support brief (core on Linux, launchd stays macOS-only). **Both have since landed on main:** P1 `4ff38e61`, P3 `f32f59b6`/`ebadc621`, P4 `58ed9d99`, P5 `7cac0e98` (WI-0488..WI-0492), and Linux support as WI-0468 (done). This design now builds on what they shipped rather than waiting for them.

Every citation below names the symbol first. The line number is only a hint, refreshed at `c38a844b`. The redesign moves code, so these numbers will rot. Each implementation item re-reads them before building. `curate/check_citations.py` gates the ones in the ADR that stop resolving.

---

## 0. Where the code is today (the facts the design rests on)

### 0.1 One namespace, nine files

`sessionlib/__init__.py:11` lists nine parts: `config, goal, coord, store, journal, land, trunkcheck, lanes, hooks`. `load()` runs each one with `exec` into the caller's globals (`sessionlib/__init__.py:14-21`). `session.py` calls `sessionlib.load(globals())`, so all ~40k lines share **one** dictionary of globals. Two ordinary modules already sit outside `PARTS`: `registry_state.py` and, since P4, `brief.py`.

[ADR-0118](../adr/0118-session-py-is-a-package-behind-a-thin-entry-point.md) chose this on purpose, for two reasons it measured:
1. A 123-symbol strongly connected call graph. Land, coord, journal, lanes and start/end all call each other.
2. Tests patch module globals hundreds of times and expect every function to see the new value.

Both reasons still hold, and the second has grown. Tests now use `mock.patch.object(session, …)` **846** times in 69 files, assign `session.X = …` directly **485** times, and use `setattr(session, …)` **56** times. `session.ROOT` alone appears 298 times and `session.CFG` 236 times. The fixtures (`tests/coord_fixture.py`, `tests/ambient_fixture.py`) isolate a test from live state **only because** a rebind on `session` reaches every part.

**This is the central constraint of the redesign.** Under real modules, `session.ROOT = tmp` would not reach `sessionlib.config.ROOT`. About 1,600 patch sites would silently change from *isolating* to *live*, and nothing would fail. Section 6.2 is the answer to it.

### 0.2 Runtime context is ambient

| Context | Today | Readers |
|---|---|---|
| Repo root | `ROOT = Path(__file__).resolve().parent` (`sessionlib/config.py:36`), correct only because `__file__` is `session.py`'s under exec | 84 functions |
| Config | `CFG = _load_config()` at import (`sessionlib/config.py:561`), `CFG_RAW` (`sessionlib/config.py:568`) | 71 + 7 |
| State dirs | `SESSION_STATE_DIR` (`sessionlib/config.py:928`), `JOURNAL_DIR` (`sessionlib/config.py:1979`), `WI_DIRNAME`, `ARCHIVE`; snapshots `_LAUNCH_ROOT` (`sessionlib/config.py:940`) etc. exist *because* tests rebind the live ones | 31, 13, 29, 6 |
| Machine | `detect_machine()` (`sessionlib/config.py:729`) runs `scutil`; `_coord_host()` (`sessionlib/coord.py:503`) uses `socket.gethostname()`; `os.uname()` in the attach hint (`sessionlib/lanes.py:7756`) | 14 + 6 + 1 |
| Session identity | `_claude_session_id()` (`sessionlib/config.py:6268`) reads `CLAUDE_CODE_SESSION_ID`, plus 7 direct reads | 17 |
| Clock | `_now_iso()` (`sessionlib/config.py:1050`); 25 `datetime.now`, 66 `time.time`, 65 `time.monotonic`, 10 `time.sleep`. No injectable clock | everywhere |
| Environment | 55 keyed `os.environ` reads of **24** names (exact list below), plus 3 more names read through `data_root_of`'s `env` argument, plus ~8 whole-env copies for child processes | everywhere |
| HOME | `data_root_of(cfg, env=os.environ)` (`sessionlib/config.py:194`) takes it as a parameter; six other sites call `Path.home()`; `USER_SETTINGS_PATH` (`sessionlib/config.py:8754`) is fixed from the real home **at import** | 8 |
| Working directory | `Path.cwd()` below any adapter: `_find_holder_journal` (`sessionlib/journal.py:597`), the lane-root resolver in `land.py` (`sessionlib/land.py:696`), and the lane resolver in `lanes.py` (`sessionlib/lanes.py:172`) | 3 |
| Child environment | `sh()` (`sessionlib/config.py:582`) passes `env=None` by default, so about 305 git calls inherit **every** `GIT_*` variable the caller has set | ~305 |
| Import-time values | `CANON_PATH`, `STANDARD_PATH` (`sessionlib/config.py:43`, `:49`), `_REAL_JOURNAL_DIR` (`sessionlib/config.py:2149`), and `ANNOUNCE_WAIT_S`/`ANNOUNCE_POLL_S`, read from the environment once (`sessionlib/config.py:1019`) | — |
| `sys.path` | the hooks part appends `ROOT` (`sessionlib/hooks.py:2448`), so the first root to load `interpreter` wins for the whole process | 1 |

**The environment names, exactly** (AST scan of `sessionlib/*.py` at `c38a844b`; M1's inventory test regenerates this list and fails on a new name that is not added to `Env`):
- Literal keys (16 names, 46 reads): `CLAUDE_CODE_SESSION_ID`, `NO_COLOR`, `POGA_DISPATCH`, `POGA_DISPATCH_ITEM`, `POGA_FORCE_COLOR`, `POGA_INVOKED_FROM`, `POGA_LANE_CLOSE`, `POGA_RUNTIME_ID`, `POGA_TURN_BUDGET`, `SESSION_ANNOUNCE_POLL_S`, `SESSION_ANNOUNCE_WAIT_S`, `TERM`, `TERMINFO`, `TERMINFO_DIRS`, `TERM_PROGRAM`, `TMUX`.
- Keys held in a constant (8 names, 9 reads): `POGA_GATE` (`GATE_ENV_VAR`), `POGA_UNATTENDED_RUN`, `POGA_LAND_ON_UNVERIFIED_TRUNK`, `POGA_TRUNK_CHECK`, `POGA_SKIP_PREFLIGHT_CHECKS`, `POGA_NO_PREFLIGHT_REPAIR`, `POGA_BOARD_STATUS`, `POGA_ALLOW_DEV_ON_PROD`.
- Through `data_root_of`'s `env` (3 names): `POGA_DATA_ROOT`, `XDG_DATA_HOME`, `HOME`.

The earlier draft said "57 reads of 26 names". The review counted 25 + 3. This scan finds 24 + 3. The counts differ because each method treats constant-keyed reads and writes into a child env differently. That is why the list is generated by a test and not kept by hand.

**Two precedents for the target shape already exist.** `data_root_of` takes `env` as an argument. `sessionlib/registry_state.py` is an ordinary module, outside `PARTS`, whose functions take `root` and `shared` as parameters (`sessionlib/registry_state.py:23`, `sessionlib/registry_state.py:68`).

### 0.3 The worked example: the 2026-09-27 nightly (WI-0432)

The 02:30 nightly ran under the launchd job `com.federation.gate-inputs` and went red, 16 F and 56 E. There were two causes, and they share one shape:

1. **deploy/runner.py.** `running_under_unit()` (`deploy/runner.py:1959`) reads `XPC_SERVICE_NAME`. launchd sets that variable for every process it starts, and the test fixtures inherited it. `refuse_unsealed_process_root` (`deploy/runner.py:3588`) then refused the test's tree. Nothing reproduced from a shell, where the variable is unset.
2. **sessionlib.** With no `CLAUDE_CODE_SESSION_ID` set, `_find_holder_journal` (`sessionlib/journal.py:562`) falls back to the current directory and then to `POGA_INVOKED_FROM`. Under launchd that reached the real checkout's journals. The chain is `_lane_exit_epoch` → `_own_frontmatter` → `_find_holder_journal`.

Both causes took **identity from the ambient environment and the working directory** instead of having it passed in. The fix landed in tests only: `XPC_SERVICE_NAME` was added to `CLEARED_ENV`. The class of bug remains, because any new env read is a new hole. Under an explicit context, the environment is read **once**, at the adapter, into a typed value. A test supplies that value. Ambient state cannot leak in, because nothing below the adapter can see it.

### 0.4 Shelling out, and the platform

- **One wrapper runs every process: `sh()`** (`sessionlib/config.py:582`). It defaults to `cwd=ROOT` and to the caller's whole environment. About 305 calls are literal `git` argv (land 122, lanes 73, config 51, store 25). There are also about 38 direct `subprocess.run` calls, for `ps`, `tmux`, `osascript`, `launchctl`, `ssh`, `lsof`, `claude`, `infocmp` and `tic`.
- **Platform calls in sessionlib:**
  - `scutil` in `detect_machine` (`sessionlib/config.py:731`)
  - `sysctl` in the memory-pressure sample (`sessionlib/hooks.py:1828`)
  - `xattr` in the quarantine check (`sessionlib/hooks.py:1440`)
  - `launchctl managername` (`sessionlib/lanes.py:6346`)
  - `osascript` (`sessionlib/lanes.py:6493`, plus two in attach)
  - `lsof` in the process-cwd probe (`sessionlib/config.py:6906`)
  - BSD `ps -axo`
  - `st_birthtime` (`sessionlib/config.py:7110`)
- **Not in sessionlib:** there is no `plutil`, `sandbox-exec` or `systemctl` there. launchd unit management lives in `deploy/runner.py` (`deploy/runner.py:1304-1833`).

### 0.5 Locks (coordination store)

Everything lives under `<git-common-dir>/poga-coord/<kind>/<name>.json` (`_coord_dir`, `sessionlib/coord.py:121`). There is no `fcntl`. The only primitives are an `O_EXCL` create and `atomic_write` (temp file plus `os.replace`, `sessionlib/config.py:698`).

| Lock | Where | On contention |
|---|---|---|
| `land-queue/<id>` ticket → `land-gate/trunk` | `land_gate_lock` (`sessionlib/coord.py:1697`), gate record `sessionlib/land.py:751` | FIFO wait. Polls every 5 s and gives up after 90 min, then proceeds **unserialized**. TTL 45 min, renewed by a 60 s heartbeat thread. Reclaimed only if the holder is expired **and** proven dead |
| `trunk-lock/trunk` | `trunk_lock` (`sessionlib/coord.py:1879`) | Retries for 30 s, then proceeds unlocked. TTL 120 s. Held around the land's `update-ref` CAS, around `_ff_trunk_to_origin`'s `update-ref`, **and around `_store_autocommit`'s whole `git commit`** (`sessionlib/config.py:3895`, the `with trunk_lock()` near `:3995`), which runs the repo's commit hooks while holding it |
| `leases/<name>` | `lease()` (`sessionlib/coord.py:1268`) | Refuses (`LeaseHeld`) |
| `lane-alloc/poga-N` | `_lane_reserve` (`sessionlib/lanes.py:199`) | Tries the next N |
| `dispatch-spawn`, `dispatch-item`, claims, number allocation | the dispatch spawn lock in `lanes.py`, the claim refusal in `coord.py` (`sessionlib/coord.py:2977`), `_counter_reserve_next` (`sessionlib/config.py:3574`) | Refuse; or n+1, up to 64 times. Claims and ADR reservations have an 8-hour TTL (`CLAIM_TTL_SECONDS`, `ADR_ALLOC_TTL_SECONDS`, `sessionlib/config.py:2206`, `:2232`) |

**Nesting order** is queue ticket → land gate → trunk lock. `_store_autocommit` takes the trunk lock without the gate. `_ff_trunk_to_origin` (`sessionlib/land.py:459`) runs both outside the gate (from `_require_trunk_current_for_land`, the pre-land check) and inside it (from integrate, `sessionlib/land.py:3965`).

**Two trunk moves take no trunk lock:**
- Inside the gate, `_recompile_main_checkout` (`sessionlib/land.py:4394`) commits the views on the main checkout (`sessionlib/land.py:4542`).
- At start, `git_sync` (`sessionlib/config.py:856`) fetches and fast-forwards from upstream and pushes (`sessionlib/config.py:873`, `sessionlib/config.py:908`), with neither lock.

**Two subprocesses run inside the gate.** `_recompile_main_checkout` runs `session.py compile` and then `session.py stamp-status` as child processes of the lander while it holds the gate. The re-entrancy counters are per process, so they do not cross into those children. Today neither child takes the gate. If either ever did, it would wait on its own parent for up to 90 minutes (I-L1).

Both are today's behavior. The redesign preserves them, and §9 records them as hazards. Both locks are re-entrant per process through the counters `_LAND_GATE_DEPTH` and `_TRUNK_LOCK_DEPTH`. The second counter is defined in config.py and mutated by `global` in coord.py (`sessionlib/coord.py:1892`), which works **only** because the parts share one namespace. **Every lock fails open** when there is no store or on a filesystem error.

---

## 1. Explicit runtime context

### 1.1 The rule

> Paths, configuration, time, machine/session identity and the environment are **values passed in**. Only an **adapter** (a CLI verb's entry, a hook's stdin reader, the nightly's main) may read `os.environ`, `Path.home()`, `Path.cwd()`, `sys.stdin`, `socket.gethostname()` or the wall clock directly, and it does so exactly once, to build the values.

### 1.2 The values (small, separate, frozen)

The brief says not to swap the global namespace for one unrestricted service object. So context is **several small frozen dataclasses**, and a function takes only the ones it uses:

| Value | Holds | Built from (today's source) |
|---|---|---|
| `Layout` | repo root, shared work root, git common dir, session-state dir, journal dir, work-item and ops dirs, archive, data root, home | `ROOT`, `_shared_work_root`, `_git_common_dir`, `SESSION_STATE_DIR`, `JOURNAL_DIR`, `WI_DIRNAME`, `ARCHIVE`, `data_root_of`, `layout_of` (`sessionlib/config.py:124`) |
| `Config` | the parsed `session.config.json` as typed fields (tz, trunk, role_doc, machine_map, inbox, runtime, gate commands…) plus the raw dict for unknown keys | `_load_config` (`sessionlib/config.py:485`), `read_member_config` |
| `Identity` | machine label, host name, runtime id, durable session id, the Claude session id, dispatch item, "invoked from", unattended flag | `detect_machine`, `_coord_host`, `POGA_RUNTIME_ID`, `durable_session_id` (`sessionlib/config.py:2065`), `CLAUDE_CODE_SESSION_ID`, `POGA_DISPATCH*`, `POGA_INVOKED_FROM` |
| `Clock` | `now()` (tz-aware), `monotonic()`, `sleep(s)` | `datetime.now`, `time.*` |
| `Env` | a **parsed** view of the 26 names this harness reads, as typed fields (e.g. `land_on_unverified_trunk: bool`), plus `child_env()` for the ~8 places that build a child's environment | `os.environ`, once |

`RuntimeContext` exists, but only as the **bundle an adapter builds and hands to the top-level phase**. The rule is enforced by the import check in §3.3: **no function below the lifecycle layer accepts `RuntimeContext`**. A land-gate policy function that needs the trunk name and a clock takes `trunk: str, clock: Clock`, not the bundle. The bundle is how the top of the stack carries its values, not a service locator.

### 1.3 Identity resolution becomes a pure function

`_find_holder_journal` (`sessionlib/journal.py:562`) and the other "who am I" probes become `resolve_identity(env: Env, layout: Layout, cwd: Path) -> Identity`. The fallback order (session id, then working directory, then `POGA_INVOKED_FROM`) stays the same, but it becomes **visible and testable**: a test can build an `Env` with no session id and a `cwd` in the real checkout, and assert what the function answers. That test is the regression test for cause 2 of §0.3.

---

## 2. Narrow interfaces (ports)

Each port is a small `Protocol` with a real implementation and a test fake. A port carries **verbs a caller needs, not a general `run(argv)`**. A general escape hatch is allowed only where the argv really is data (the gate command).

### 2.1 `Git` (narrow by decision)

**Decision (review, 2026-10-02): the typed port covers only what a fake must answer at the level of meaning.** The earlier draft typed every git verb across all ~305 call sites. That is a large rewrite whose only gain at most sites is cosmetic. It also multiplies the test rewrites each step must carry. The port is cut to two things:

- `update_ref_cas(ref, new, expected) -> Advanced | Moved`, the CAS at the heart of a land (`_serialized_advance`, the `update-ref` under `trunk_lock`, `sessionlib/land.py:1169`), and the same CAS in `_ff_trunk_to_origin`.
- `push(remote, refs, atomic, timeout) -> PushResult` (accepted / rejected / unreachable), the **publish result** a land and integrate route on (`_push_trunk`, `sessionlib/land.py:1371`).

**Everything else stays on `sh()`**, with literal argv, as today. The step that moves a subsystem keeps its `sh` calls and injects the runner instead of reaching for the global. M6 removes hand-built argv only for those two verbs.

**`sh()` gets an explicit environment policy** (it is the runner behind both `Git` and `Proc`). Today `env=None` passes the caller's whole environment to every child, so a `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` or `GIT_OBJECT_DIRECTORY` set by a caller (a git hook, a test, a launchd job) silently retargets about 305 git calls. Under the redesign the runner takes its child environment **only** from `Env.child_env()`, built once at the adapter. That makes the inheritance visible and testable. **The policy keeps today's behavior: it passes everything through.** Stripping the repository-selecting `GIT_*` names would be a behavior change, so it is recorded in §9 for its own decision, and M0 pins today's behavior (a test that sets `GIT_DIR` and shows which repository a harness call reaches).

### 2.2 `Proc`

- `run(argv, cwd, env, timeout, stdin)` for the non-git programs, under the same environment policy as §2.1
- `liveness(pid) -> True | False | None` and `alive(pid) -> bool`
- `list_processes()`, `process_cwd(pid)`, `signal(pid, sig)`

**Cleanup package 1 has landed and settled the liveness contract** (`4ff38e61`). There is now one `_pid_alive` (`sessionlib/config.py:6721`), a bool that is False only when the pid is provably dead, over the three-state `_pid_liveness`, plus `_process_alive` (`sessionlib/config.py:1563`). A static guard refuses duplicate part names. **The port moves that contract as it is**: `liveness` is `_pid_liveness`, and `alive` is `_pid_alive`. Callers that remove things on a dead answer: `_scan_lanes` → `_reap_lane` (`sessionlib/lanes.py:2117`, `sessionlib/lanes.py:3744`) and `_unlock_if_owner_is_dead` (`sessionlib/coord.py:2805`).

### 2.3 `CoordStore`

The coordination store becomes one module (`coord/store.py`, §3) behind this port:

- `try_acquire(kind, name, owner, ttl) -> Acquired | Held(record) | Unavailable`
- `release_exact(record)`, `refresh(record)`, `read`, `list`, `reap(now)`

The locks are built on it as context managers that keep **today's exact semantics** (§0.5):
- `land_gate(…)` (queue → gate)
- `trunk_lock(…)`
- `lease(name)`

**`Unavailable` is a typed value, not a silent `(True, {})`.** The fail-open behavior is kept, but the caller decides it in its own code where a reviewer can see it. Today it is buried in `_coord_try_acquire` (`sessionlib/coord.py:351`).

Re-entrancy moves from the process globals `_LAND_GATE_DEPTH` and `_TRUNK_LOCK_DEPTH` to a `LockRegistry`. **It is a module-level singleton in `coord/locks.py`, not owned by any store instance.** Facades build ports per call (§6.2), so a registry per instance would start every nested acquire at depth 0. The nested acquire would then wait on its own record: 30 s and then unlocked for the trunk lock, up to 90 minutes for the gate. The semantics stay the same: re-entrant per process, not keyed on identity. The registry is no longer reachable from another module by `global`.

Tests that set or assert the depth counters are on M4's patched-name ledger (§6.2): `session._LAND_GATE_DEPTH = 0` in five files (for example `tests/test_land_gate_liveness.py:81`, inside a `getattr`/`setattr` save-and-restore loop), `_TRUNK_LOCK_DEPTH` in one, and the asserts at `tests/test_land_gate_lock.py:137` and `:356`. Left alone, they would read and write a dead name and pass vacuously. A direct assignment is worse than `patch.object`, which at least raises on a missing name: `session._LAND_GATE_DEPTH = 0` would silently create a fresh global that nothing reads. **So the depth counters are deleted from the namespace at M4, and the session module refuses assignment to them** (§6.2 item 3, the runtime refusal). The ledger is how they are found, and the refusal is what fails if one is missed.

The land-gate context manager **keeps the name `land_gate_lock`**. The section-opener tables in `tests/test_integrate_holds_no_suite.py` match on that name, and `_land_gate` is already a different function (the cheap-check gate).

M4 also checks whether the gate's nesting is still reachable after WI-0378 before preserving it "exactly". The re-entrancy comment at `sessionlib/coord.py:1712` predates that item.

**The gate is never taken inside a child process the gate holder spawned.** Re-entrancy is per process, so a child of the holder that asked for the gate would queue behind its own parent for up to 90 minutes. Today the two children spawned under the gate (`session.py compile` and `session.py stamp-status`, from `_recompile_main_checkout`) do not take it. I-L1 now states this. M0 adds a test that runs both children while a gate record is held and asserts that neither one tries to acquire it. The lander asserts that the gate depth is 0 when it enters, so it is never nested inside another gated section of the same process.

### 2.4 `Platform`

This is the **seam the Linux-support brief needs**:

- `machine_name()` (today `scutil`)
- `memory_pressure()` (today `sysctl`)
- `quarantine(path)` (today `xattr`)
- `gui_session()` (today `launchctl managername`)
- `open_terminal(cmd)` (today `osascript` / Ghostty)
- `file_birthtime(path)` (today `st_birthtime`)
- `process_table()` (today BSD `ps -axo`)
- `process_cwd(pid)` (today `lsof -a -d cwd`, `sessionlib/config.py:6906`)

`Proc.process_cwd` delegates to the `Platform` method.

There are two implementations: `MacPlatform` and `LinuxPlatform`. **The Linux lane is doing the portability fixes now.** This design does not redo them. When a step here reaches a platform call, it moves the call, **including whatever fallback the Linux lane gave it**, behind `Platform`. "Not available on this OS" becomes the typed answer `Unsupported(reason)`, never a guess.

**launchd unit management is not part of `Platform`.** It belongs to the deploy runner, which stays macOS-only per the Linux ruling. It stays inside `deploy/runner.py`. The split that would have given it a `Units` port is cut (§7).

---

## 3. Module map

### 3.1 Layers (imports only point down)

```
L5 adapters     cli/ (per-family registration), hooks/ (stdin/stdout adapters), session.py entry
L4 lifecycle    lifecycle/start.py, lifecycle/end.py, land/lander.py      (phase orchestration)
L3 transactions land/ (gate policy, advance, publication, materialize), lanes/ (reap, dispatch, attach)
L2 repositories coord/, store/ (work items, ops, inbox), journal/ (persistence), views/ (generated)
L1 context+ports context.py, ports/{git,proc,coordstore,platform}.py, fs.py (atomic_write)
L0 pure         policy/shell.py, briefs/, frontmatter.py, verdicts (land, lane scan, reclaim)
```

**Rules, each checked by the import-boundary test (§8):**
- A module imports only from its own layer or below. Within L3, `land` and `lanes` do not import each other. Where they cooperate today (`_land_worktree_lane` lives in `lanes.py` but calls about 25 `land.py` symbols), the orchestration moves **up** to `L4 land/lander.py`.
- Only L5 reads `os.environ`, `sys.stdin`, `Path.home()`, `Path.cwd()` or the wall clock.
- Only `ports/*` (real implementations) call `subprocess` or `os.kill`.
- **No module imports `session` or `sessionlib` (the assembler).** That is the "no namespace magic" rule from Astra §1.

### 3.2 Target modules

| Module | Owns | Imports | Must never import | From today |
|---|---|---|---|---|
| `policy/shell.py` (L0) | Bash-command segmentation and guard verdicts: `top_level_segments`, `_split_top_level`, `destructive_git_violation`, `peer_injection_violation`, `safe_git_auto_allow`, `false_green_violation`, `compound_violation` | stdlib | anything L1+ | `sessionlib/config.py`, from `top_level_segments` (`:7535`) through `compound_violation` (`:8237`), already pure (no env, clock or subprocess) |
| `brief.py` (L0 + one L2 seam) | Brief parsing, op planning, text transforms, and the shared frontmatter reader | stdlib; the apply step takes `CoordStore` (lease) + `Layout` | git, land, lanes | **Already extracted by cleanup package 4** (`sessionlib/brief.py`, `58ed9d99`), with its binding facade in config.py's "Brief parsing" block. This design only places it in L0 and removes that facade at M12 |
| `context.py` (L1) | `Layout`, `Config`, `Identity`, `Clock`, `Env`, `RuntimeContext`; `resolve_identity` | stdlib, L0 | L2+ | the globals in §0.2 |
| `ports/git.py`, `ports/proc.py`, `ports/platform.py` (L1) | §2.1, §2.2, §2.4 | stdlib, `context` | L2+ | `sh` (`sessionlib/config.py:582`), process and platform helpers |
| `fs.py` (L1) | `atomic_write` and its suite write floor, sidecar read/write | stdlib | L2+ | `atomic_write` (`sessionlib/config.py:698`), `_suite_write_refused` (`:647`), `_sidecar_write` (`:1160`) |
| `coord/` (L2) | `CoordStore`, record format, reap, locks (`land_gate`, `trunk_lock`, `lease`), lane slot allocation, claims, counter allocation (`_counter_reserve_next`) | L0, L1 | journal, store, land, lanes | `sessionlib/coord.py:1-1935`; config.py's coordination block, from the "Cross-lane coordination substrate" banner (`sessionlib/config.py:2178`) to the work-item store banner (`:3717`) |
| `store/` (L2) | Work-item and ops repositories (one file per item, the sole writer, autocommit, feed), inbox state, attention and escalation records | L0, L1, `coord` (allocator) | journal, land, lanes | `sessionlib/store.py`; config.py from the "Work-item store" banner (`sessionlib/config.py:3717`) to the "releases" banner (`:4701`) |
| `journal/` (L2) | Journal render, parse, finalize, resolve; banners | L0, L1 | land, lanes, views | `sessionlib/journal.py:44-405`, `sessionlib/journal.py:1579-1630`, `sessionlib/journal.py:2289-2611` |
| `views/` (L2) | Generated views: handoff compile, ROADMAP regions, STATUS stamp, ledgers. **Pure renderers over records passed in.** Writing them is done by the caller | L0, L1, `journal` (read), `store` (read) | land, lanes | `sessionlib/journal.py:608-1950`, `sessionlib/journal.py:2166-2254` |
| `releases/` (L3) | Release cut and its preflight and tag | L0–L2 | land, lanes | config.py from the "releases" banner (`sessionlib/config.py:4701`) to the startup-chart banner (`:5283`), `sessionlib/coord.py:2206-2492` |
| `grants/` (L2) | Authorization grants (mint, resolve, cite), including the per-verb citation flag `_GRANT_CITED` | L0, L1 | L3+ | config.py from the "Relay-authorization grants" banner (`sessionlib/config.py:8550`) to the dispatch banner (`:8994`) |
| `land/` (L3) | Gate and verdict policy (ADR-0148 store), `serialized_advance`, publication (owed/resume), push outcomes, integrate transaction, main-checkout materialization, rebase-conflict policy, receipts | L0–L2 | lanes, lifecycle | `sessionlib/land.py` (ranges in §4.3) |
| `trunkcheck/` (L3) | Detached post-land trunk check, culprit, revert proposal | L0–L2 | lanes, lifecycle | `sessionlib/trunkcheck.py` |
| `lanes/` (L3) | Lane identity and slots, scan and classify (pure verdicts over facts), reap, janitor, teardown, parked refs, dispatch (plan pure / spawn impure), attach, drill, supervise | L0–L2, `land` **read-only queries** passed in as functions | lifecycle | `sessionlib/lanes.py` minus `_land_worktree_lane` (659-1064) |
| `lifecycle/start.py`, `lifecycle/end.py` (L4) | The phases of §4.1 and §4.2; the evidence packet of §5 | L0–L3 | adapters | `cmd_start`/`_cmd_start` (`sessionlib/hooks.py:181-734`), `cmd_end` (`sessionlib/hooks.py:841-1241`), `_complete_lazy_start` (`sessionlib/config.py:6317`) |
| `land/lander.py` (L4) | The phases of §4.3 | L0–L3 | adapters | `_land_worktree_lane` (`sessionlib/lanes.py:659-1064`) |
| `cli/` (L5) | Per-family parser registration, choke points before dispatch | everything | — | `hooks.main` (`sessionlib/hooks.py:4554`) and the per-family registration **package 5 already landed** (`7cac0e98`). This step only moves it to L5 |
| `hooks/` (L5) | Hook stdin/stdout adapters: `_read_hook_stdin`, `_emit_start`, check-bash and check-question deny/allow output, the heartbeat's Stop decision | everything | — | `_read_hook_stdin` (`sessionlib/hooks.py:14`), `_emit_start` (`sessionlib/config.py:8311`), `cmd_check_bash` (`sessionlib/hooks.py:2967`), the check-question adapter |

`goal.py` and `registry_state.py` sit in L2, beside `store/`. `registry_state.py` already conforms.

### 3.3 Breaking the 123-symbol cycle

The cycle ADR-0118 measured is the reason a real-module split could not be ordered. It is broken **edge by edge, from the leaves inward**, with three tools:

1. **Move orchestration up.** A function that calls into two sibling subsystems moves to the layer above both. `_land_worktree_lane` is the biggest case: it lives in `lanes.py` but is mostly `land.py` calls, so it moves to `L4 land/lander.py`.
2. **Pass a query, not a module.** Where a lower layer needs one answer from a higher one, it takes it as a value or a callable parameter. Example: coord reaping needs "is this branch on trunk?". Every such parameter is listed in the module's docstring. **A callable parameter is a debt, not a pattern.** Each one has a removal note.
3. **Turn a decision into a pure verdict.** For example: the reclaim decision inside `_coord_try_acquire`, the lane classification in `_scan_lanes` (`sessionlib/lanes.py:2048`, the classify block near `:2117-2190`), the end refusals, and the land refusals. Each becomes `verdict(facts) -> Decision` in L0. The impure caller gathers the facts and then acts.

Step M1 (§6) computes the call graph again at the starting commit and records the extraction order. **The order is derived, not guessed.** Each step's item names the edges it removes.

---

## 4. Lifecycle as phases

Each of the three big functions becomes a short orchestrator over named phases. Each phase **returns a structured result** (a frozen dataclass). The orchestrator decides what to print at the end, using a renderer, and what exit code to return.

**Progress is the one exception to "phases do not print".** Some lines must reach the operator *before* a slow operation, not after it: the land-queue position while it waits for the gate (`land_gate_lock`, the `land-gate: queued — position …` line near `sessionlib/coord.py:1775`, up to 90 minutes), the `verdict: … the suite once` line before `_ensure_lane_verdict` runs the suite (near `sessionlib/land.py:2882`), and the `integrating with origin` line before integrate (near `sessionlib/land.py:3820`). A phase that only returned a result would leave the operator watching silence for the whole wait. **So every phase that can block takes an injected `emit` callable, and progress goes through it the moment it happens.** Many of these functions already take `emit=print` today, so this generalizes the existing pattern. Progress never goes into a returned result, and a result never replays progress. The renderer owns only the final summary.

**Splitting does not license changing semantics.** For each orchestrator below, the invariant list is the acceptance contract for its step. A behavior not in the list is preserved by the existing tests. The list names the behaviors that are easy to break *by the act of splitting*. **Each invariant below was re-checked against `c38a844b` after review.** Six in the earlier draft misdescribed today's code: I-S1, I-L1's trunk-lock clause, I-L3, the I-L6 receipt clause, I-L10, and I-E1. They are corrected here. **M0 pins each corrected invariant with a test on today's code** (§6.5, item 10), so a split that keeps it passes, and a split that changes it fails.

### 4.1 Start (`_cmd_start`, `sessionlib/hooks.py:227-733`, ~507 lines, behind the `cmd_start` wrapper at `:181`)

| Phase | Today | Result |
|---|---|---|
| S0 Inputs | Hook stdin (`_read_hook_stdin`), banner path, dispatch brief, and **the dispatch receipt write** (`_dispatch_write_receipt`, near `sessionlib/hooks.py:250`), machine and stamp | `StartInputs` |
| S1 Idempotency | `_my_open_journal` (near `sessionlib/hooks.py:260`); already-open → **stage the banner sentinel**, print and return (near `:290`); prep adoption via `_claim_prepped_journal` (near `:301`, which claims the prepped journal); already lazy-started → **stage the banner sentinel**, print and return (near `:310`). Under `--prep`, `_refuse_prep_early_return` (`:157`) refuses before either return | `AlreadyOpen \| Adopted \| Fresh` |
| S2 Sync | Read-only git status; `git_sync` (`sessionlib/config.py:856`), skipped on a dirty tree, push only on a manual run | `SyncResult` |
| S3 Mutations | Three branches: adopted (`.live` sidecar only), lazy (sidecars only, real work deferred to `_complete_lazy_start`), eager (freeze → reap journals → write journal → `.live` → supersede → session branch → compile → prep marker), near `sessionlib/hooks.py:349-458` | `StartWrites` (a list of what was written) |
| S4 Orientation | About 50 optional lines (near `sessionlib/hooks.py:465-706`), almost all fail-open, including the ADR-0059 `REFUSE:` line (near `:484-491`) | `Orientation` (typed lines, each with its own failure captured) |
| S5 Emit | `--dry-run` / `--prep` payload / hook JSON via `_emit_start` | adapter output |

**Invariants that must not move:**
- **I-S1 (corrected).** Start has **no refusal that stops it after S3**, and **its early returns are not write-free**. What happens before S3 writes:
  - The dispatch receipt is written in S0, before either early return. That order is kept on purpose.
  - **Both early returns stage the banner sentinel** (`_stage_banner(banner_path, [])`, near `sessionlib/hooks.py:290` and `:310`), unless `--dry-run`.
  - Prep adoption claims the prepped journal (`_claim_prepped_journal`) before S3.
  - The only refusal that stops start is the `--prep` early-return refusal (`_refuse_prep_early_return`), plus the wrapper's nonzero exit (I-S5).

  **The ADR-0059 `REFUSE:` line is not a refusal.** It is an orientation line in S4, printed after S3 has already written the journal, cut the session branch and compiled (near `sessionlib/hooks.py:484-491`). The session goes on. The phase split keeps all of this exactly. Whether `REFUSE:` should stop the start is a §9 finding, not a refactor change.
- **I-S2.** The lazy path writes only the two sidecars. Everything else waits for the first heartbeat (`_complete_lazy_start`, `sessionlib/config.py:6317`).
- **I-S3.** The eager path runs its write steps in the order listed above.
- **I-S4.** Orientation lines fail open, one by one. One failing line never suppresses the others or the emit. In the phase version, each line's exception is **recorded in the result** rather than swallowed, and the renderer decides to drop it. Behavior is the same, and the failure is visible to tests.
- **I-S5.** `_PREP_PAYLOAD_WRITTEN` (`sessionlib/hooks.py:111`) and the nonzero exit when a prep run writes no payload (in `cmd_start`, near `:220`) stay. They become a field of the S5 result, not a module global.
- **I-S6.** A dirty tree never blocks a start.
- **I-S7.** `start` takes no lock. Its writes are separate atomic files, and none of them depends on another's success. This includes `git_sync`'s fast-forward and push, which run with neither the gate nor the trunk lock. **The redesign preserves this.** It is a recorded hazard (§9), not an endorsement. Adding a lock here is a behavior change, so it needs its own decision.

### 4.2 End (`cmd_end`, `sessionlib/hooks.py:841-1241`, ~401 lines)

| Phase | Today | Result |
|---|---|---|
| E0 Lazy completion | `_complete_lazy_start` runs first (near `sessionlib/hooks.py:868`), so it can write before any refusal | `LazyResult` |
| E1 Refusals | No config (`:854`); journal unresolvable; no frontmatter; already ended; then `_attention_clear()` (near `:895`); then no authorization (`--confirm` → dispatch record → unattended record) and a contradicting dispatched close (near `:947`) | `EndRefused(reason)` or `EndAuthorized(how)`, as a **pure verdict over gathered facts** |
| E1b Dry run | `--dry-run` prints and returns (near `:981`), **after** E0 and `_attention_clear()` | `DryRun` |
| E2 Evidence | *New.* Build the evidence packet (§5) | `EndEvidence` |
| E3 Close | Write the closed journal once (`finalize_journal` + `atomic_write`, near `:1023`) → publish `session-close` with landed unknown (`_session_close_publish`, near `:1043`) → registry `record_end` → `end-ran` sidecar → release the lane reservation | `CloseWrites` |
| E3b Fixture refusal | `_refuse_git_on_fixture("end")` (near `:1114`, defined at `sessionlib/config.py:2120`) raises `SystemExit` **after** every E3 write when the run is against a test fixture | `FixtureRefused` |
| E4 Land | Lane: `land/lander` (§4.3), or `--no-merge` (commit and return). Legacy branch: `_land_branch`. Trunk: compile → `_stamp_status` → `_land_candidate` | `LandOutcome` |
| E5 After | Lane-exit report (on a land) → `_dispatch_close_runtime` → republish the close with the real `landed` → summary → `_attended_close_runtime` → `_exit_on_failed_land` | exit code 0 / 1 / 2 |

**Invariants that must not move:**
- **I-E1 (corrected).** E1 refusals fire before the E3 writes, with **three exceptions that exist today and stay**:
  - E0 (lazy completion) runs first.
  - `_attention_clear()` runs **after** the journal refusals (no config, unresolvable, no frontmatter, already ended) but **before** the authorization refusals (`--confirm`, dispatched close). A refused unauthorized close has therefore already cleared the attention record.
  - `_refuse_git_on_fixture` refuses **after** E3 has written the closed journal and published the close. Under a fixture, that leaves a closed journal and a nonzero exit.

  **`end --dry-run` is not write-free either.** It returns after E0 and `_attention_clear()` have run. The phase split keeps all of this exactly. Whether it is right is a §9 finding, not a refactor change.
- **I-E2.** The journal is closed and the close published **before** the land (see `cmd_end`'s docstring). A failed land leaves the journal stamped and the exit nonzero. A second `end` is refused as already closed, and the recovery path is `merge --continue`.
- **I-E3.** The exit codes are those of the land: 0 landed or nothing to land, 1 refused, 2 trunk moved or contended.
- **I-E4.** `end` does not commit store notes. The store autocommits on its own.
- **I-E5.** Focus and blocked reach STATUS only through `_recompile_main_checkout` / `_stamp_status`, written all-or-nothing (`sessionlib/journal.py:2166`).
- **I-E6 (new), the runtime-close order.** On a lane close, E5 runs in this order: lane-exit report → `_dispatch_close_runtime` → republish (`_session_close_publish(…, landed=…)`) → summary lines → `_attended_close_runtime` → `_exit_on_failed_land` (near `sessionlib/hooks.py:1160-1187`). **A dispatched lane spawns its detached reaper with no read pause** (`_lane_exit_spawn(pid, tmux_session, 0.0)`, inside `_dispatch_close_runtime`, `sessionlib/lanes.py:5870`) **before the republish runs.** An attended close republishes first and only then spawns its reaper, with a pause. The phase version keeps this order, including the dispatched race. Whether the dispatched reaper should wait for the republish is a §9 finding.

**One gap the phases make visible:** a crash between E3 and E4 leaves a closed but unlanded journal, and no single record says how far the close got. The start-time scan (near `sessionlib/hooks.py:524`) catches only the opposite case. **the operator ruled on this (Q3):** `end` records its progress, as its own item (WI-0487) after M9. The refactor itself must not add it.

### 4.3 Land (`_land_worktree_lane`, `sessionlib/lanes.py:659-1064`, ~406 lines)

Today's order, with the phase each step joins:

| Phase | Steps (today's lines) | Mutates | Result |
|---|---|---|---|
| L-A Verdict | `_ensure_lane_verdict` (`sessionlib/land.py:2842`) **clears `_LAND_BUILD` unconditionally on entry** (near `:2855`), may run the suite in the lane (with a progress line first), and files the verdict with `record_verdict` (`sessionlib/land.py:2768`) into the **shared** verdict store under the git common dir; then `mark_ready_to_land` | the shared verdict store; `_READY_TO_LAND`, `_LAND_BUILD` globals | `Verdict \| Refused(gate)` |
| L-B Prepare | Absorb stop-checkpoint (near `sessionlib/lanes.py:714`); `_reap_dead_journals`; `sh(SWEEP_ADD)` (near `:743`, `check=True`) + close commit with grant trailer | the lane branch; dead journals | `PreparedLane` |
| L-C Attempt (looped) | Trunk current (`_require_trunk_current_for_land`, `sessionlib/land.py:523`) → read parent → restore views (near `sessionlib/lanes.py:811`) → rebase or fast-forward → conflict resolve (≤ 50 steps, near `:825`) → HEAD equals parent: resume owed publication (which takes the gate itself, through `_serialized_publish`) or nothing-to-land → renumber (near `:882`) → counter gates (`_all_counter_land_gates`, `sessionlib/land.py:2185`) → `_land_gate` cheap checks in a scratch worktree (`sessionlib/land.py:2906`). **When no verdict exists, `_land_gate` runs the full suite once here, outside the gate**, and records it in `accepted` | the lane branch; scratch worktree (removed in `finally`); **outside the gate:** may fast-forward the shared trunk to origin under the trunk lock and sync the main checkout; renumbering **draws numbers from the shared allocator**; **updates `accepted`** | `Ready(tip, parent, accepted) \| Refused(reason) \| Nothing \| Resumed(outcome)` |
| L-D Advance | `_serialized_advance` (`sessionlib/land.py:1129`): land gate → re-read trunk → `update-ref` CAS under the trunk lock → `publish` closure (release numbers → sync main checkout → recompile views, **which commits on the main checkout without the trunk lock and runs `session.py compile` and `stamp-status` as children** → push) | the trunk ref (CAS); a second trunk commit from the recompile; the main checkout; origin | `Landed(stages) \| Moved \| Failed` |
| L-E After | Escalate a rejected push to integrate (outside the lock); spawn the trunk check (detached); `_land_receipt` (near `sessionlib/lanes.py:1032`, **only for an attempt that reached `_serialized_advance`**); forced resume of owed publication if `publish_error`; retry on `Moved/Failed` within `_CasBudget` (`sessionlib/land.py:3255`) | receipts log; integrate's own lock and push | `LandOutcome` |
| L-F Dispatch | `_dispatch_after_land` (`sessionlib/lanes.py:6931`) on full success only. **Not on a `Resumed` outcome** | dispatch records | — |

**Invariants that must not move:**
- **I-L1, lock order (corrected).** Queue ticket → land gate → trunk lock, and nothing else is ever taken inside the gate. **Inside a land**, the trunk lock is held only around the `update-ref` CAS. **Elsewhere it is held longer:** `_ff_trunk_to_origin` holds it around its own `update-ref`, and `_store_autocommit` holds it around a whole `git commit` that runs the repo's commit hooks (§0.5). The gate is held from the trunk re-read through `publish`. No suite and no lane-side work runs while the gate is held. **One attempt can take the gate twice**: `_serialized_advance`, then `_serialized_publish` (`sessionlib/land.py:1551`) through a forced resume. The resume can also run at the start of an attempt when HEAD equals parent. Both sections follow the same order and release rules. **No child process spawned under the gate takes the gate** (`session.py compile` and `stamp-status` today, §2.3). Re-entrancy does not cross processes, so a child that did would wait 90 minutes on its own parent. The lander asserts that the gate depth is 0 when it enters. This is guarded today by `tests/test_integrate_holds_no_suite.py` and `tests/test_land_lock_holds_only_the_merge.py`.
- **I-L2, release.** Each lock's `finally` releases **exactly the record it wrote** (`_coord_release_exact`, `sessionlib/coord.py:631`), so a reclaimed record is never unlinked. The gate is released before the ticket. The heartbeat thread stops in the inner `finally`.
- **I-L3, the CAS is the only all-or-nothing point (corrected).** **No phase may add a rollback.** There is none today, and the design depends on there being none. Everything after the CAS is reconciled against refs (`_publication_owed`, `sessionlib/land.py:1452`), never against receipts ([ADR-0122](../adr/0122-validation-reuse-and-resumable-completion.md) D2). **Before the CAS, the land does not write only to the lane.** These shared writes happen before it and outside the gate, and none is undone by a later refusal:
  - L-A files the lane's verdict into the shared verdict store (`record_verdict`).
  - L-C's `_require_trunk_current_for_land` can fast-forward the **shared trunk ref** to origin (under the trunk lock only) and sync the main checkout (`_ff_trunk_to_origin` → `_sync_main_checkout`).
  - L-C's renumbering draws numbers from the shared allocator (`_counter_reserve_next`), which holds an 8-hour reservation per number.
  - L-B reaps dead journals.

  The phase split keeps each of these in the phase where it runs today.
- **I-L4, moved means nothing written.** If the trunk is no longer `parent` under the gate, the outcome is `Moved` and nothing is written by L-D.
- **I-L5, the retry boundary.** Only `Moved` and `Failed` retry. Each attempt redoes L-C completely. The verdict and the budget carry across attempts, and so does the lane's state:
  - **`accepted` is threaded unchanged.** The suite verdict is **never** re-run ([ADR-0148](../adr/0148-a-land-is-a-merge.md) D4). Today that works because `_land_gate` mutates one shared dict in place (near `sessionlib/land.py:2924-2977`). In the phase version, L-C returns the whole updated `accepted` dict, and **the orchestrator passes that same dict into the next attempt**. A phase result that drops it, or rebuilds it with fewer keys, would re-run the full suite on attempt 2.
  - **The budget is one `_CasBudget` instance for the whole loop.** `attempts()` is a generator that records each attempt's duration **when the loop resumes it** (the `yield` near `sessionlib/land.py:3335`). The orchestrator must keep driving that one generator across attempts. It must not rebuild it, and it must not wrap each attempt in a fresh call. Otherwise the budget is never sized by the slowest attempt. The budget is at least 3 and at most 12 attempts within 1200 s, and `attempts=N` pins it.
  - **Attempt N+1 starts from attempt N's rewritten lane HEAD**, not from L-B's prepared tip. That HEAD includes the views restore, the rebase, and the renumber commit. Resetting to the prepared tip would draw new numbers on every attempt and leak an 8-hour reservation for each number drawn before.
- **I-L6, refusals are the same set with the same exit codes (receipt clause corrected):**
  - `gate`, `diverged`, `unverified`, `trunk`, `conflict`, `counter`, `trunk-red`, `delivery`, `gone`: exit 1
  - `contended`: exit 2
  - `nothing`: exit 0

  `_require_trunk_current_for_land` can also pass through its own state string (near `sessionlib/land.py:576`). **Receipts are not written on every attempt.** `_land_receipt` (`sessionlib/land.py:1709`) writes a `land-lane` row only (a) after an attempt reaches `_serialized_advance` (`sessionlib/lanes.py:1032`), whatever its outcome, and (b) on the `trunk-red` refusal (near `sessionlib/lanes.py:922`). **The `gate`, `conflict`, `counter`, `trunk`, `diverged` and `unverified` refusals write no receipt.** `_resume_owed_publication` writes a `resume-lane` receipt on `resumed`, `delivery` and `gone` (near `sessionlib/land.py:1683`). Every return stays a literal `LandOutcome(...)` (the guard in `tests/test_worktree_lane.py`, near `:4040`).
- **I-L7, publish failure.** A `publish` exception after the CAS records `publish_error`, and a forced resume runs. If the resume still owes something, the lander returns `delivery` or `gone` (exit 1) even though the trunk has advanced (near `sessionlib/lanes.py:1036-1047`). The session stays open over it ([ADR-0122](../adr/0122-validation-reuse-and-resumable-completion.md) D2). The phase version keeps both halves: the trunk did move, and the land is not reported as complete.
- **I-L8, fail-open stays fail-open.** No store, a filesystem error, or the 90-minute wait cap means the land proceeds unserialized. The phase version makes this a typed `Unavailable` (§2.3) that the orchestrator turns into the same behavior. **Changing it to fail closed is a separate decision** (Q2, WI-0486).
- **I-L9, the source-level guards are retargeted, not weakened.** The tests that read the lander's own source must follow the code to the orchestrator and phase functions, and keep both their assertions and their negative controls:
  - `_land_worktree_lane` must contain `_serialized_advance`, `_land_receipt`, `mark_ready_to_land` and `_require_trunk_current_for_land`, and must not contain `land_gate_lock` (`tests/test_land_lock_holds_only_the_merge.py`, `tests/test_land_refuses_a_stale_trunk.py`).
  - The lander does not push on its own (`tests/test_land_publishes.py`).
  - `MUST_BE_WATCHED` resolves `_land_worktree_lane` and `publish` (`tests/test_integrate_holds_no_suite.py`).

  A guard that looked inside one function body must still be able to fail after the split. Each retargeted guard is proven by a mutation that removes the guarded call.

  Three ways a guard can go blind, each closed in the step that creates the risk:
  1. `_harness_trees` in `tests/test_integrate_holds_no_suite.py` keys the call graph by bare name and keeps the first definition. A facade with the same name as the real function can shadow it. **Guards resolve functions by module-qualified name.**
  2. Four tests read `inspect.getsource(session.cmd_start)` and rely on `cmd_start.__wrapped__` (`sessionlib/hooks.py:734`). **Every facade sets `__wrapped__` to the real function**, or the guard reads the wrapper and an `assertNotIn` passes vacuously.
  3. The section-opener table matches `land_gate_lock` by name. **The name is kept** (§2.3).
- **I-L10, the receipt clock (corrected).** `_READY_TO_LAND` (`sessionlib/land.py:940`) and `_LAND_BUILD` (`sessionlib/land.py:2678`) are process globals with **different lifetimes**:
  - `_READY_TO_LAND`: `mark_ready_to_land` (`sessionlib/land.py:943`) is idempotent. The first mark wins and survives refusals and retries within the process. Only a `landed` receipt clears it (`_clear_ready_to_land`, `:955`).
  - `_LAND_BUILD`: **cleared unconditionally at every lander entry**, by `_ensure_lane_verdict`. It does **not** survive a refused land into the next `_land_worktree_lane` call. Within one call it survives the retry loop.

  The receipt's `phases` stay an exact partition of `total_seconds` (ADR-0148 D7, FL7). **Both stay process-lifetime state**, moved into one module-level clock object that keeps the two lifetimes separate. Unlike `_PREP_PAYLOAD_WRITTEN` (I-S5), they must not become per-call result fields. A refused-then-retried land in the same process would otherwise restart its clock.
- **I-L11 (new), `Resumed` is not `Landed`.** When HEAD already equals parent at the start of an attempt, or after a forced resume, the lander returns the resume's own outcome (`LandOutcome(finished.landed, finished.reason)`, near `sessionlib/lanes.py:862` and `:1047`). It **skips `_dispatch_after_land`**, even when the resume reports landed. The phase result keeps `Resumed(outcome)` as its own variant, so that an orchestrator matching on "landed" cannot run L-F for it.
- **I-L12 (new), the exceptions that escape, per phase.** Today these propagate out of the lander uncaught. The phase version lets exactly the same ones escape from the same phases, and does not catch them in a new place:
  - L-B: `sh(SWEEP_ADD)` uses `check=True`, so a failing `git add` raises `CalledProcessError`. In `end`, this happens **after E3** has closed the journal.
  - L-C: an exception from the conflict resolver (`_resolve_rebase_conflict`) propagates with **a rebase still in progress** in the lane.
  - L-D: `publish` is wrapped in `except Exception` only (`sessionlib/land.py:1177`). A `KeyboardInterrupt` or `SystemExit` after the CAS escapes **with no receipt written** and no `publish_error` recorded. The trunk has moved, and `_publication_owed` still reconciles it at the next resume.
  - Any phase: `KeyboardInterrupt` unwinds through the lock context managers, and their `finally` blocks release exactly what they hold (I-L2).

---

## 5. End-protocol evidence packet

### 5.1 The split

Today the model writes the journal's `What happened`, `State at close`, `Parked question`, `Notes filed` and `Outcome` sections, plus `--focus`, `--blocked`, `--title`, learnings and memories (`STANDARD.md` session end, generated from `standard-source.md:295-326`). Code already computes the frontmatter, ended, duration, ordinal, work items, escalations and goal disposition.

**Code supplies** (`EndEvidence`, built in phase E2):

| Field | Source |
|---|---|
| session id, ordinal, machine, runtime, started, ended, duration | journal frontmatter, `compiled_ordinal`, `Clock` |
| base commit, head commit, commits since base (subject + id) | `Git` |
| changed paths, grouped (code / tests / docs / store / generated) | `Git.diff_names(base, head)` |
| work items claimed, created, status-changed, noted this session | claims harvest, store |
| ADR numbers drawn and whether each file exists | allocator records, `Layout` |
| test evidence: lane verdict (tree id, result, when), any suite runs recorded | ADR-0148 verdict store |
| escalations raised, goal disposition | existing folders |
| **land state**, filled after E4 | `LandOutcome` + `_publication_owed`, i.e. trunk contains head, origin contains head |
| receipts | `land-receipts.jsonl` rows for this session |

**The model supplies only meaning:** `what_happened` (narrative), `state_at_close`, `parked_question`, `outcome`, `focus`, `blocked`, `title`, learnings, and the memory selection.

### 5.2 Validate, then render

1. `end --evidence` prints the packet as Markdown and JSON, without closing anything.
2. The model writes its semantic fields where they go today: the journal sections and the `--focus`, `--blocked` and `--title` flags. **No new file format is needed.**
3. `end` validates **structure and ids only**:
   - Required semantic sections are present and not template text.
   - Every WI, ADR or OPS id the prose names exists in the packet or the store. An unknown id is flagged, and the close proceeds.
4. Code renders the mechanical sections (`Evidence`, `Commits`, `Changed paths`, `Land`) into the journal itself. The model never types them.

**Decision (review, 2026-10-02): prose-versus-evidence claim checking is deferred.** The earlier draft also checked outcome claims in the prose ("landed", "tested", "pushed") against the packet. That needs a claim parser over free prose, and no one has yet shown that the model misreports outcomes once the mechanical sections are rendered by code. So M9 ships steps 1–4 and nothing more. **The Q4 measurement comes first:** after M9, a read-only audit (a `curate/` report, not in the close path) compares each closed journal's prose with its rendered `Land` and `Evidence` sections, and counts disagreements over a window of real closes. Claim checking is designed only if that count shows misreports actually happen. If it ships, it follows the operator's Q4 ruling: it warns first and is measured before it can refuse. The deferral changes when the check is built. It does not change the ruling.

### 5.3 Command success is not outcome evidence

The packet keeps two different fields apart, and the renderer never merges them:

- `command`: what a verb returned, e.g. `land: exit 0, reason landed`.
- `outcome`: what the refs and records say now, e.g. `trunk contains head: yes`, `origin contains head: yes`, `publication owed: none`.

An outcome field is filled **only** from a fresh read after the command. It is never copied from the command's return value. The land is the case that motivates this: `Landed` with a `publish_error` means *the command succeeded, the outcome is incomplete* (I-L7).

### 5.4 What changes in `standard-source.md`

The session-end steps 3–5 change from "write these sections" to "read the evidence packet; write the meaning; the close validates it". This is an edit to `standard-source.md`, regenerated into `STANDARD.md` by `curate/standardize.py`, and it ships as a standard-version bump. **It lands in the same step as the code (M9), never before it.** A prompt that describes a packet code does not yet emit is the prompt/code mismatch Astra §4 is about.

---

## 6. Migration plan

### 6.1 Ordering

```
cleanup lane (LANDED on main):  P1 liveness (WI-0488)  P3 capability manifest (WI-0490)  P4 brief.py (WI-0491)  P5 CLI families (WI-0492)
Linux lane   (DONE):            WI-0468
                                   │                         │                              │
M0  test floor (tests only; includes the guard self-tests and the child-runner helper, §6.5)
M1  context + ports skeleton, guards, recursive harness_files  ◄── blocked-by WI-0490, WI-0491
M2  identity, clock, env through the context builder
M3  Proc + Platform ports, `sh` behind them, explicit child-env policy  ◄── blocked-by WI-0488, WI-0468
M4  coord/ (store, locks, allocator)           ◄── needs M0 lock tests
M5  store/, journal/, views/, goal, grants/    (L2 repositories)
M6  land/, trunkcheck/, releases/; the narrow Git port (CAS + publish result)  ◄── needs M0 land floor
M7  lanes/ (scan/reap verdicts, dispatch, attach) and policy/shell.py
M8  start phases                     ┐
M9  end phases + evidence packet     │ one milestone (Q1): built and landed one step at a time,
M10 lander phases                    │ released to the fleet together
M11 adapters (cli families → L5)     ┘
M12 retire the exec assembler, and the curate/ importers of `session`
```

**Cut (review, 2026-10-02): the `deploy/runner.py` and `poga_cli.py` splits (formerly M13 and M14).** Neither file imports sessionlib, so neither is part of the problem this ADR solves (the shared exec namespace). Their only link to it was being sequenced after M12, and that was by choice, not by dependency. For a one-operator system, a 4,138-line runner split is not worth a fleet release. The `poga_cli.py` split would also add an import edge from `poga_cli.py` into `sessionlib.context`. Today `poga_cli.py` imports only `bootstrap` and `poga_evidence` (`poga_cli.py:103-104`), and it is the tool that restores a broken member. A new edge into the harness package is a new way for recovery to fail. The code gave no concrete reason to keep either split. The one useful piece, reading `XPC_SERVICE_NAME` through a typed `Env` in `running_under_unit()` (`deploy/runner.py:1959`), is the WI-0432 class. It is recorded in §9 as a hazard. If the operator wants it, it is a small standalone item, not a redesign step. §7 keeps only what the remaining steps need from those files.

**Every step is blocked until after the 2026-10-07 OPS-0010 soak.** M0 is tests only and needs nothing else. **The external dependencies are encoded as `blocked-by` links, not prose**, so the board shows them: M1 is blocked by WI-0490 (P3) and WI-0491 (P4), and M3 by WI-0488 (P1) and WI-0468 (Linux). The code for all four is already on main (see the header of this doc). The links clear when devbox closes those items. P3 matters because the capability detector must stop matching text before any file moves (§6.4). P4 matters because it is the first real extraction and set the facade precedent. Its facade is a **binding** (`session.<name>` is the module's object), not a per-call wrapper, which is right for a pure module with no namespace reads.

**Q1, as ruled.** One release per step for M1–M7. M8–M11 are built and landed one step at a time, then released to the fleet together behind one milestone. §6.3 says what that does to rollback.

**What "below L5" means before M11.** Until the adapters exist as modules, "the adapter" is the `cmd_*` entry function of a verb plus `_context_from_namespace()`. M2 moves the ambient reads it names into that builder. Each later step removes ambient reads from the code it moves. Check §8.4 applies to **the modules that step created**, not to the whole namespace.

### 6.2 The compatibility facade (the answer to §0.1)

For each subsystem moved out of the exec namespace, in one step:

1. **New module, explicit arguments.** The code moves into an ordinary module that takes `Layout`, `Clock`, ports and so on as parameters. It imports nothing from the namespace.
2. **The facade replaces the definition in its own part.** The old name stays in the exec namespace as a thin wrapper, in the same part file where the `def` was. It is not a second definition elsewhere, which would trip cleanup package 1's duplicate-name guard. The wrapper sets `__wrapped__` to the real function (I-L9). On **every call** it builds values from the namespace's *current* globals, through one function, `_context_from_namespace()`, and calls the new module. So `session.ROOT = tmp` keeps working for every test not yet migrated, because the wrapper reads `ROOT` at call time. **A patch on a facade name is therefore harmless.** It still reaches every caller that goes through the namespace. The danger is elsewhere (item 3).
   - **`Layout` and `Config` are rebuilt on every call. Only subprocess-backed values are cached.** The earlier draft cached the built values keyed on `ROOT`. That goes stale in three ways the suite uses today, all without moving `ROOT`:
     - fixtures edit `CFG` in place (`patch.dict(session.CFG, moved)` in `neutralize_live_store`, `tests/coord_fixture.py:231`), and tests assign `session.CFG[k] = v` about 70 times;
     - tests patch `_git_common_dir` to return `None` while `ROOT` stays the same (`store_unreachable`, `tests/test_grants.py:147`);
     - the core fixture patches `_shared_work_root` (`tests/coord_fixture.py:182`).

     Building a `Layout` or `Config` from globals is a few attribute reads and dict copies, so it is cheap enough to do per call. **Only the values that need a subprocess are cached**: `detect_machine` (`scutil`, `sessionlib/config.py:729`) and the git common dir (`_git_common_dir`, `sessionlib/config.py:2296`). The trunk lock polls every 0.02 s, which is why those two must not run per call. The cache key is `(ROOT, id(ns["_git_common_dir"]), id(ns["_shared_work_root"]))`. Patching either function changes its object identity, which misses the cache. The cache lives in the builder, following the `_SHARED_WORK_ROOT` pattern (`sessionlib/config.py:2325`), never in a module global of the moved code.
   - **Callbacks into the namespace are late-bound.** Where moved code still needs a name that lives in the namespace, the facade passes `lambda *a, **k: ns["X"](*a, **k)`, never the function object. A test that patches `session.X` then still reaches it. **Every late-bound name is declared in `sessionlib/_facades.py`**, because the guards in item 3 subtract exactly that set.
   - **Process-lifetime state stays process-lifetime.** The lock registry (§2.3) and the receipt clock (I-L10) are module singletons. They are never rebuilt per call. The other process caches go with their owners and keep their keys as they are (each one listed in M1's inventory):
     - `_RECEIPT_CLASS_CACHE` (`sessionlib/land.py:961`), keyed without a root;
     - `_REAL_LIVE_STORES` (`sessionlib/config.py:605`), built once from the launch root by design;
     - `_GRANT_CITED` (`sessionlib/config.py:8978`), a per-verb flag that `_store_autocommit` reads to stamp the grant trailer. It moves into `grants/` and is reached from `store/` through a passed-in reader, never a `global`.
   - **Tables of late-binding lambdas are rebuilt from `Layout`.** `_COUNTERS` (`sessionlib/config.py:3316`) holds lambdas such as `lambda: ROOT / …` that resolve in the shared namespace today. Moved into `coord/`, they would bind the new module's globals instead. M4 keys the counter table on the `Layout`, and adds a test that rebinds `ROOT` between two draws. **The same applies to `_ROADMAP_REGIONS`** (`sessionlib/journal.py:1463`), whose renderers are lambdas (`"now": lambda: _render_finish_line()`), and M5 treats it the same way. `PREFLIGHT_REPAIRS` (`sessionlib/hooks.py:2171`) is the opposite case: it holds `_repair_terminfo` **directly**, so a patch of `session._repair_terminfo` already fails to reach it today. That table is not a facade risk to preserve. M11 records it so that no one "fixes" it inside the refactor.
   - **`__file__` is never read by moved code.** Two functions derive a path from their own file, which is `session.py`'s directory only because the parts run under `exec`:
     - `_test_suite_is_running` (`sessionlib/hooks.py:81`) builds the tests dir from `Path(__file__).parent / "tests"` (near `:95`). Copied into `fs.py` or `context.py`, it would look in `sessionlib/tests`, **always answer False, and silently switch off both the write floor and the tripwire.**
     - `_lane_exit_spawn` (`sessionlib/lanes.py:5778`) builds its argv from `str(Path(__file__).resolve())` (near `:5789`). As a real module, it would spawn the module file instead of `session.py`.

     **The entry computes both paths once, from `session.py`'s own path, and injects them**: `Layout.tests_dir` and `Layout.entry_script`. `ROOT` (`sessionlib/config.py:36`) is the third `__file__` site, and the two docstrings at `sessionlib/journal.py:2262` and `sessionlib/land.py:4422` explain why. **All `__file__` sites are on the ledger** (item 3), and the boundary test (§8.4) bans `__file__` in moved modules.
3. **Patched-name guard, computed at test time, plus a runtime refusal.** The real hazard is not a patch on a facade name, since item 2 makes that harmless. **It is a patch on a helper or value that moved code now reads from inside its own module.** Unmoved code still uses that name, so the name still exists in the namespace, and the patch succeeds without error. It reaches the unmoved readers and silently misses the moved ones. The canonical case is the core fixture's shared-root isolation, `patch.object(session, "_shared_work_root", …)` (`tests/coord_fixture.py:182`). The same applies to `sh`, `_git_common_dir`, `_coord_dir`, `_pid_alive` and `_under_test`, and to values such as `_TRUNK_CHECK_POPEN`, `RUNTIMES`, `PREFLIGHT_GATE_CHECKS`, `PROC_SIGNAL_GRACE_SEC`, `HOOK_STDIN_TIMEOUT_SEC`, `CONFIG_PATH`, `USER_SETTINGS_PATH`, `_SHARED_WORK_ROOT`, `_LAUNCH_JOURNAL_DIR` and the lock depth counters. The defense has three parts:
   - **The ledger.** Before the move, the step lists every test that patches a name **defined in, or used by,** the code being moved, from an AST walk of the moved code's free names. Each site is rewritten in the same step to inject a fake port or value, **or** is reached through a declared late-bound callback.
   - **The static guard, computed at test time, never frozen.** `test_no_patch_reaches_moved_code` replaces the earlier `test_no_patch_targets_a_facade`. Each time it runs, it computes the **protected set**: the union of the AST free names of every module already moved (the list of moved modules lives in `sessionlib/_facades.py`), **minus** the names declared there as late-bound callbacks, **plus** every name deleted from the namespace (next part). It then fails any test, including one written after the step landed, that patches a protected name with `patch.object`, `patch("session.X")`, a direct `session.X = …` or a literal `setattr(session, "X", …)`. The earlier ledger was built once per step, so tests written later were never checked. Its negative control is a planted test that patches a protected name, and the guard must fail it.
   - **The runtime refusal, which also covers what static analysis cannot see.** After each step:
     - **moved values are deleted from the namespace**, so `getattr(session, "_LAND_GATE_DEPTH")` raises instead of reading a dead name;
     - **the `session` module refuses assignment to any protected name.** The entry gives the module a `ModuleType` subclass whose `__setattr__` and `__delattr__` raise for names in the protected set. Code inside the parts writes the globals dict directly (`global X; X = …`), so it is unaffected. Only outside writers go through the module attribute: `session.X = …`, `setattr`, and `patch.object`'s start and stop.

     This is what catches the cases the static guard is blind to: the dynamic `getattr`/`setattr` save-and-restore loops (for example `tests/test_land_gate_liveness.py:78-81`, which also does `session._LAND_GATE_DEPTH = 0`). A direct assignment to a moved value is otherwise worse than `patch.object`: `patch.object` on a missing name at least raises, but `session._LAND_GATE_DEPTH = 0` after M4 would silently create a global nothing reads. With the refusal, both fail loudly at the line that does it.
4. **Live-state tripwire and write floor, widened to every live path.** The earlier draft checked only the root, the shared root and HOME. Real checkout paths also travel in `Config`. `_load_config` joins `handoff`, `role_doc`, `inbox` and `status` onto the real `ROOT` **at import** (`sessionlib/config.py:485`, the joins near `:501-519`), and the fixture says so itself (`tests/coord_fixture.py`, the `CFG` comment near `:207-233`). Many test files rebind `ROOT` and never touch `CFG`. `_stamp_status` then writes `CFG["status"]` through `atomic_write` (`sessionlib/journal.py:2166`). Today's write floor refuses only writes into a real `.session-state/` (`_suite_write_refused`, `sessionlib/config.py:647`, over `_real_live_stores`, `:608`). So a write to the real `STATUS.md` or handoff passes both the floor and the tripwire. Four fixes:
   - **The tripwire checks every path-typed field of both `Layout` and `Config`**, found by type at build time and not from a list kept by hand. Under the suite, `_context_from_namespace()` and every real port constructor refuse a value whose path resolves inside the operator's launch checkout, main checkout or real home, unless a test opts in by name.
   - **The write floor widens to the same two roots.** `atomic_write` refuses any write under the launch checkout or the main checkout while the suite runs, not just under `.session-state/`. Both roots come from `_LAUNCH_ROOT`, snapshotted **once at import, never per call**, and the floor is installed into `fs.py` at import. A facade cannot then rebind it away. The docstring credits the floor with 43 of the 52 measured escapes, so it moves with `atomic_write` and does not shrink. **Widening it is a behavior change under the suite only.** M0 measures what it would refuse on today's suite before M1 turns it on. Each hit is a real live write, and it is fixed in a test, not allowed.
   - **HOME is redirected at the suite's entry, before `session` is imported, and only then is the HOME check turned on.** No fixture redirects HOME today, and `USER_SETTINGS_PATH` is fixed from the real home at import (`sessionlib/config.py:8754`). A HOME check without the redirect would refuse every `Layout` built under the suite, and in practice that ends in a blanket opt-in. The suite's entry (`curate/run_suite.py`, and a new `tests/__init__.py` for a direct `unittest` run) sets `HOME` to a fresh temp dir for the whole run. The tripwire's HOME clause is enabled in the same step.
   - **The tests dir is injected, not derived** (item 2), so the suite detector cannot silently turn both guards off when it moves.
5. **Negative controls for the guards themselves.** M0 adds a self-test that must fail if either guard is off: a test that points a `Layout` at the real checkout must be refused by the tripwire, and a test that writes the real `STATUS.md` path must be refused by the floor. Run with `_test_suite_is_running` forced False, both must report "would have refused". That proves the detector is what turns them on, and that it is on.
6. **Removal.** A facade is deleted when nothing calls it: no namespace function, no test, and **no `curate/` or `bootstrap.py` importer of `session`**. Those importers are `curate/gate_inputs.py`, `metrics.py`, `finish_line.py`, `common.py`, `push-substrate.py`, `deliver.py`, `check-brief.py`, `check_substrate_docs.py`, and `bootstrap.py`. `sessionlib/_facades.py` lists each facade with the step that removes it. The facade count only goes down. M12 deletes the last one together with the assembler, and moves those importers to the real modules.

**Tests move, they are not weakened.** A test that patched a global is rewritten to pass a fake port or a built `Layout`. Its assertions and its negative control stay. A step's item lists the test files it rewrites and shows each one still fails against a mutation of the behavior it guards.

**Child processes go through one helper.** 45 test files run `session.py` as a child. A child process does not run the suite detector, so neither guard protects it (the `_suite_write_refused` docstring says this: three of the 52 measured escapes were of that kind). M0 adds one helper, `run_session_child(argv, root)`, which runs `session.py` with a clean environment, a temp HOME and the fixture as cwd. A guard test bans a bare `subprocess.run` of `session.py` in `tests/`. The existing call sites move to the helper in M0, which is tests only.

### 6.3 Per-step contents

Each step's work item carries: the behavior preserved, the dependency removed, the facade and how it is removed, the tests that move, the integration tests that must exist first, and the rollback.

**Rollback: roll forward, or revert the newest step only.** The earlier draft said each step "is rolled back by reverting that land". That holds only for the newest step. Once M5 builds on `coord/`, reverting M4 is no longer a clean revert: M5's modules import `coord/`, and its tests inject `coord` fakes. And with M8–M11 released together behind one milestone (the Q1 ruling), M9's rollback is no longer independent of M8, M10 and M11 on a member. So the rule is:
- **Before the next step lands**, a step can be reverted by reverting its land.
- **After that**, a regression is fixed forward, or the newest step is reverted, and then the next newest, in order. Never a step from the middle.
- **The M8–M11 milestone rolls back as one unit**, by reverting the milestone's release on the fleet. Members go back to the last M7 release.
- **M9 changes on-disk format.** It renders new journal sections and ships a standard-version bump. Its rollback, and so the milestone's, is a revert **plus a standard-version bump that restores the old end protocol**, because members that already took the new version keep it until they take the next one.

Any other step that would change an on-disk format is out of design and needs its own ADR.

| Step | Preserves | Removes | Integration tests that must exist first |
|---|---|---|---|
| M0 | — (tests only) | — | see §6.5; this step also adds the guard self-tests, the child-runner helper and the tests that pin the corrected invariants |
| M1 | all | nothing yet; adds `context.py`, `ports/` (interfaces and real implementations wrapping today's helpers), the import-boundary test, `_facades.py`, the test-time patch guard and the runtime assignment refusal, the widened tripwire and floor, the suite-entry HOME redirect, the env-name inventory test, the recursive `harness_files()` | M0; WI-0490 and WI-0491 closed |
| M2 | all output, including every timestamp format | ambient clock, env, `scutil` and `CLAUDE_CODE_SESSION_ID` reads inside the functions it routes (`_now_iso`, `detect_machine`, `_claude_session_id`, `_coord_host`, `_find_holder_journal`); `resolve_identity` becomes pure | M0's hermetic-nightly test |
| M3 | reaper and lock behavior per P1's contract; every child's environment, byte for byte | `subprocess`/`os.kill` outside `ports/` for moved code; platform calls outside `Platform`; `sh` becomes the real ports' runner and a facade, taking its env only from `Env.child_env()` | P1's liveness tests; the Linux lane's CI job; WI-0488 and WI-0468 closed |
| M4 | §0.5 table exactly, I-L1, I-L2, I-L8 | `global _TRUNK_LOCK_DEPTH` across files (the depth names are deleted and refused, §6.2 item 3); coord's reach into journal (`_find_holder_journal` via `_own_frontmatter`, `sessionlib/coord.py:1379`); `_COUNTERS` bound to namespace lambdas | M0 lock tests (two processes) |
| M5 | file formats; the sole-writer rule; autocommit scope; the trunk lock around `_store_autocommit`'s commit | views writing files themselves; store reading `ROOT`; `goal`, `grants` reading globals; `_ROADMAP_REGIONS` bound to namespace lambdas; `_GRANT_CITED` read by `global` | existing store tests, made hermetic |
| M6 | every git effect; I-L1–I-L12 untouched | hand-built argv for the CAS and the push only (the rest stays on `sh`, §2.1); `BOOKKEEPING_PATHS` moved with `curate/store_guard.py` | M0 land floor |
| M7 | lane scan and reap decisions; dispatch locking and refusals; attach and drill output; shell-policy verdicts byte for byte | lanes reading `ROOT`/`CFG`; `_scan_lanes`' inline decision (`sessionlib/lanes.py:2135-2190`) | M0 stale-lock and recover-lanes tests; the existing check-bash corpus |
| M8 | I-S1 … I-S7 | `_cmd_start` as one function; `_PREP_PAYLOAD_WRITTEN` global | start-path tests in a real temp repo; golden start JSON with a pinned clock and machine (M0) |
| M9 | I-E1 … I-E6 | model-typed mechanical sections (claim checking deferred, §5.2) | end-path tests including failed land and crash after close |
| M10 | I-L1 … I-L12 | `_land_worktree_lane` as one function; the land↔lanes import edge | M0 land floor, all five scenarios |
| M11 | CLI names, flags, defaults and help text byte for byte; hook output byte for byte | parser and hook I/O inside `hooks.py`; the `sys.path` append of `ROOT` (`sessionlib/hooks.py:2448`) moves to the entry | golden help and golden hook-output tests |
| M12 | everything | `sessionlib.load`, `PARTS`, `exec`; `curate/` importing `session` | full suite + capability probes on a member |

**Work items.** M0 through M12 are WI-0469 through WI-0481, in order. Each is blocked by the one before it, and each is marked held until after the 10-07 soak. Their notes state the behavior preserved, the dependency removed and the validation for that step. WI-0482 and WI-0483 (the cut M13 and M14) are to be closed as not doing. The WI store is devbox's, so the edits are listed in the comms note, not made here.

| M0 | M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 | M9 | M10 | M11 | M12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WI-0469 | WI-0470 | WI-0471 | WI-0472 | WI-0473 | WI-0474 | WI-0475 | WI-0476 | WI-0477 | WI-0478 | WI-0479 | WI-0480 | WI-0481 |

### 6.4 Distribution, bootstrap, export and detection

Moving code to subpackages touches every reader that lists harness files. None of these may be left to discover the move on a member:

- `standard_check.py` **no longer matches source text.** Cleanup package 3 (`f32f59b6`) replaced that with the versioned capability manifest `standard-capabilities.json`, plus wiring checks against the loaded namespace and pure probes. Two parts of it still see the move, and M1 deals with both:
  - `_harness_paths` (`standard_check.py:202`) scopes the AST and caller walks to `sessionlib/*.py` with a **non-recursive** `iterdir`. A caller that moves into a subpackage drops out of the walk.
  - The manifest's `calls` and `references` entries (12 of them) assert that a caller's compiled code references its callee, with named arguments (for example `_resolve_rebase_conflict` called with `resolve`). A facade's wrapper references the new module, not the old callee. **Each step that moves a caller or callee named in the manifest updates the manifest in the same step**, and §8 item 6 (probes unchanged on a member) is what proves it.
- `bootstrap.py` (`sessionlib_files`, `:289`), `curate/push-substrate.py` (`federation_sessionlib_files`, `:128`), `curate/standard_version.py` (`:152-165`), `tests/harness_fixture.py` (`:59-77`) and `curate/finish_line.py` (`_HARNESS_PATH`, `:545`) all list `sessionlib/*.py` at top level only. **M1 makes them recursive in one place** (one shared `harness_files()` helper), before any subpackage exists.
- `poga` names its harness roots in `POGA_HARNESS_ROOTS` (`poga:2006`). It copies `sessionlib` recursively and leaves stale modules on purpose (the comment near `poga:2094`). Stale modules are harmless only while nothing imports them by walking the directory, and the M1 import check ensures that.
- `curate/check_substrate_docs.py` requires every `sessionlib/*.py` to be named in `POGA-OVERVIEW-AND-SCALABILITY.md` and `federation-arch.md` §7. Each step updates both docs.
- `curate/store_guard.py:60-75` AST-parses `sessionlib/config.py` for `BOOKKEEPING_PATHS`. M6 moves that constant **and** the reader together.
- `curate/standard_version.py` already imports `sessionlib.config` for real (`_pure_residency`, near `:479`), and notes that `CFG` is then `None`. After M2 it builds a `Config` instead.
- `curate/public_cut_manifest.json` exports the whole directory, so no change is needed.
- **Members upgrade at different times.** Every detector keeps accepting the old shape until the fleet has the new one (the dual-shape contract the manifest's `manifest_version` carries).

### 6.5 M0: the test floor that must exist before any extraction

These are the gaps the mapping found. Each must exist and pass on today's code first, so it can prove the refactor did not change anything:

1. **Two landers as two processes** racing on one bare origin: exactly one CAS wins, the other retries and lands, and neither runs a suite inside the gate. Today concurrency is simulated inside a patched `_run_gate`.
2. **An unreachable push driven through the whole lander.** Today it is covered only at `_push_trunk` level (`tests/test_land_publishes.py:299`).
3. **A CAS `Failed` outcome injected** and retried.
4. **`recover-lanes` driving a real lane `merge` subprocess.** Today `_drive_lane_merge` is patched (`tests/test_finish_line.py:502`).
5. **Stale-lock reclaim across two processes**, holder dead versus alive.
6. **A hermetic nightly:** run the suite's entry under a launchd-like environment (`XPC_SERVICE_NAME` set, no `CLAUDE_CODE_SESSION_ID`, cwd in a scratch copy of a checkout). Assert no read of the real checkout. This is the §0.3 regression, run at the harness level instead of patched per test.
7. **End crash between close and land:** the journal is closed, the land never ran, and the next start reports it. Today that case is invisible (§4.2). This test **pins today's behavior**. WI-0487 (the Q3 ruling) changes it later, after M9, and updates this test then.
8. **Golden outputs:** `session.py --help` and every subcommand's `--help`; the SessionStart hook JSON for a fixed fixture; the check-bash allow/deny JSON for a corpus of commands.

9. **Guard self-tests (negative controls).** The tripwire refuses a `Layout` pointed at the real checkout. The floor refuses a write to the real `STATUS.md` path. Both report "would have refused" when the suite detector is forced off (§6.2 item 5). A planted patch of a protected name fails the test-time guard. The env-name inventory test fails on a planted new `os.environ` read.
10. **Tests that pin the corrected invariants** (§4), on today's code:
    - **I-S1:** both start early returns stage the banner sentinel; `REFUSE:` is printed after the journal exists.
    - **I-E1:** `end --dry-run` runs lazy completion and clears attention; `_refuse_git_on_fixture` exits after the closed journal is written.
    - **I-E6:** the dispatched close spawns its reaper before the republish; the attended close republishes first.
    - **I-L1:** `_store_autocommit` holds the trunk lock across its commit; neither child spawned under the gate acquires it.
    - **I-L3:** a refused land after `_require_trunk_current_for_land` fast-forwarded leaves the shared trunk advanced and the verdict filed.
    - **I-L5:** attempt 2 starts from attempt 1's rewritten HEAD and draws no new numbers; `accepted` and the `_CasBudget` instance are the same objects across attempts.
    - **I-L6:** the `gate`, `conflict` and `counter` refusals write no receipt; `trunk-red` writes one.
    - **I-L10:** `_LAND_BUILD` is empty at the start of a second lander call after a refusal, and `_READY_TO_LAND` is not.
    - **I-L11:** a `Resumed` outcome does not call `_dispatch_after_land`.
    - **I-L12:** a `KeyboardInterrupt` raised from `publish` after the CAS escapes, and writes no receipt.
    - **Progress:** the queue-position line is emitted while the waiter is still waiting.
11. **The child-runner helper** (§6.2) and the guard that bans a bare `subprocess.run` of `session.py` in `tests/`. The 45 existing call sites move to it.
12. **The suite-entry HOME redirect**, and a measurement of what the widened write floor (§6.2 item 4) would refuse on today's suite. Each hit is fixed in its test before M1 turns the floor on.
13. **Golden start JSON with a pinned clock and machine.** Item 8's SessionStart golden output is only byte-stable with the time and machine fixed. M2 does not exist yet, so M0 pins them through today's names (`_now_iso`, `detect_machine`), and M2 must then reproduce the same bytes through `Clock` and `Identity`.
14. **Child environment:** a harness git call with `GIT_DIR` set in the caller's environment, pinning which repository it reaches today (§2.1).

Already covered, and kept as is: conflicts (`RebaseResolveTest`, `RoadmapResolveTest`, `ViewAmendConflictTest`); failed gates; rejected push escalated to integrate (`tests/test_integrate_holds_no_suite.py`); resume after a publish exception (`tests/test_resume_at_the_failed_stage.py`).

---

## 7. deploy/runner.py and poga_cli.py (not split: cut)

The earlier draft split both files as M13 and M14. **Both splits are cut** (§6.1, and ADR-0150 D7). What remains here is only what the kept steps need from these files:

- **Neither file imports sessionlib**, and nothing in M0–M12 changes that. The import-boundary test (§8 item 2) covers `sessionlib/` only.
- **`poga_cli.py` keeps its import surface.** It imports `bootstrap` and `poga_evidence` (`poga_cli.py:103-104`), and it is the tool that restores a broken member. No step may add an import from it into `sessionlib`. A recovery tool that depends on the package it recovers fails exactly when it is needed.
- **`deploy/runner.py` keeps launchd unit management where it is.** It stays in the runner, macOS-only, per the Linux ruling (§2.4). There is no `Units` port, because there is no split to put one in.
- **The WI-0432 cause in the runner stays a recorded hazard** (§9): `running_under_unit()` (`deploy/runner.py:1959`) reads `XPC_SERVICE_NAME` from the ambient environment. Today a test-side fix covers it (`CLEARED_ENV`, `tests/test_deploy_runner.py:49`).

---

## 8. Proof the design holds: a checklist for every step

A step's item is not done until each line is shown, with the evidence written into the item:

1. **Imports cleanly.** Every module the step created imports in a fresh interpreter, `python3 -c "import sessionlib.<mod>"`, with HOME pointed at an empty temp dir and the cwd outside any repo. It must not read config, run a subprocess or touch the clock. Checked by a test that imports with `subprocess`, `os.environ` and `Path.home` booby-trapped.
2. **Respects the layers.** The import-boundary test (an AST walk of each module's imports against the §3.1 table) passes. Its negative control is a planted upward import, which must fail it.
3. **No unpatched global path.** `test_no_patch_reaches_moved_code` passes on the protected set computed at test time. The runtime assignment refusal is installed for every moved name. The live-state tripwire (every path field of `Layout` and `Config`) and the widened write floor are on for the whole suite, and their self-tests (§6.5 item 9) pass.
4. **Explicit dependencies.** The step's modules contain no `os.environ`, `Path.home`, `Path.cwd`, `datetime.now`, `time.time`, `subprocess`, `sys.path` or `__file__` outside L5 and `ports/` (checked by the boundary test over the AST). No function below L4 accepts `RuntimeContext`.
5. **Compatible.** CLI help and hook output golden tests are byte-identical. Every refusal and degraded path named in the step's invariant list has a test that still passes.
6. **Detection.** The capability manifest's wiring checks and probes (package 3) report the same capability set on a member before and after the step, with the manifest updated in the same step for any moved caller or callee it names (§6.4). **A moved function must not mark the fleet as missing a capability.**
7. **Distribution.** A member built by `bootstrap.py` and refreshed by `push-substrate` from the step's tree runs `session.py start --dry-run`.
8. **Landing (M4, M6, M10 only).** The M0 land floor passes, with all five scenarios: concurrent advancement, conflicts, failed gates, push failures, recovery. Lock order is asserted by test, not by reading.
9. **Coverage kept.** The suite's test count does not fall. Every rewritten test is shown to fail against a mutation of what it guards.
10. **Evidence, not success.** The item's close note quotes the outcome reads (suite result, probe output), not the exit code of the command that produced them.

---

## 9. Findings recorded during mapping (not in this design's scope)

The mapping found these. **They are not fixed by the refactor, and the refactor must not change them silently.** They are recorded here and in the session journal for a review with the operator:

- `atomic_write` uses a fixed `<name>.tmp` path (`sessionlib/config.py:698`). Two concurrent writers of the same record share one temp file.
- `_coord_try_acquire` reclaims an expired, corrupt or "mine" record with a non-exclusive write and **does not re-read** to confirm it won (`sessionlib/coord.py:351`). `_lane_reserve` and `_trunk_lock_reserve` do re-read.
- Dispatch records are read, modified and written with no lock (`_dispatch_write`, `sessionlib/lanes.py:4850`). Two concurrent advancers could lose an update.
- `cmd_release_cut` (`sessionlib/coord.py:2283`) takes no coord lock. It relies on the atomic push. This matches the known release-cut race with concurrent store writes.
- Every lock fails open (I-L8).
- Two trunk moves take no trunk lock (§0.5):
  - the views commit on the main checkout inside the gate (`sessionlib/land.py:4542`);
  - `git_sync`'s fast-forward and push at start (`sessionlib/config.py:873`, `sessionlib/config.py:908`), outside both locks.
- `end` clears the attention record before its authorization refusals (near `sessionlib/hooks.py:895`, I-E1). A refused, unauthorized close has already cleared it. `end --dry-run` also clears it.
- `_store_autocommit` holds the trunk lock across a full `git commit`, including the repo's commit hooks (§0.5). A slow hook delays every land's CAS behind it.
- `sh()` passes the caller's whole environment to every child (§2.1). A `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` or `GIT_OBJECT_DIRECTORY` set by a caller retargets the harness's git calls. Stripping them is a separate decision.
- A dispatched lane's close spawns its detached reaper, with no pause, before it republishes the close with the real `landed` (I-E6). The republish can race the reaper that ends the process.
- The ADR-0059 `REFUSE:` start line refuses nothing. It prints after the journal is written, and the session continues (I-S1).
- `publish` catches only `Exception` (I-L12). An interrupt after the CAS leaves no receipt. The refs still reconcile it at the next resume, so this is a visibility gap, not a lost land.
- `running_under_unit()` in `deploy/runner.py` reads `XPC_SERVICE_NAME` from the ambient environment (§7, the WI-0432 class). It is covered by a test-side fix only.

**Adversarial review.** One reviewing agent attacked this design before it landed. Its findings were folded in:
- the lock registry as a singleton;
- the free-name ledger and late-bound callbacks;
- the write floor kept with `atomic_write`;
- the `_COUNTERS` cache;
- per-call cost;
- `accepted` threaded through attempts;
- the corrected I-L7 and I-L6 receipts;
- the second gated section;
- the unlocked trunk moves;
- the I-E1 exceptions;
- I-L10;
- guard blindness;
- the missing steps M5–M7;
- the M9 rollback exception;
- the `curate/` importers;
- the fit with cleanup package 1.

It spot-checked about 55 citations and found all of them correct.

**Second review (2026-10-02, independent, cloud).** It found that the first review's spot-check had missed what mattered. Its cited lines were right, but six invariants described the code wrongly, and the facade guard protected the wrong set of names. Its findings and how each was handled are in §11.

## 10. Open questions for the operator (strategy only)

**Ruled 2026-10-02.** the operator answered all four in the consultant chat with *"design questions - yes"*, replying to `comms/2026-10-02-harness-redesign-design-ready.md`. The brief is `proposed-edits/federation-arch/accepted/2026-10-02-consultant-adr0150-rulings.md`. Each question keeps its text. The **Ruling** line under it is what binds.

- **Q1.** Fleet roll-out. The redesign changes every member's harness files over 15 steps (M0–M14). Ship them one release per step, or batch them behind a milestone? *Recommendation: one release per step for M1–M7, so a fleet regression points to one subsystem; batch M8–M11 behind one milestone, because the three lifecycle phases and the adapters change together what a session sees.*
  - **Ruling:** yes. One release per step for M1–M7, then M8–M11 together behind one milestone.
  - *Note (2026-10-02 revision):* after the M13/M14 cut there are 13 steps (M0–M12). The ruling is unchanged. Its effect on rollback is spelled out in §6.3: the milestone rolls back as one unit.
- **Q2.** Fail-open locks. Keep the land proceeding unserialized when the store is unavailable (today), or make that a refusal once the store is behind a port? *Recommendation: keep it as is during the redesign and decide it separately. The redesign should not change two things at once.*
  - **Ruling:** yes. A land still goes ahead when the lock store is unavailable, all through the redesign (I-L8 holds). Fail-open versus fail-closed is a separate strategy question, held as its own work item (WI-0486).
- **Q3.** Should `end` record its own progress, so a crash between close and land is visible at the next start? *Recommendation: yes, but as its own item after M9, not inside it.*
  - **Ruling:** yes. `end` records how far it got, and the next start surfaces it. This is its own work item (WI-0487), sequenced after M9. The `close-progress` sidecar in §4.2 belongs to that item, not to the refactor.
- **Q4.** Should a disagreement between the model's prose and the evidence packet refuse the close, or only warn? *Recommendation: warn first and measure how often it fires, then decide.*
  - **Ruling:** warn first, measure how often it fires, then decide whether to refuse. Folded into M9's work item.
  - *Note (2026-10-02 revision):* the claim check itself is deferred (§5.2). M9 renders the mechanical sections and validates ids. A read-only audit after M9 measures how often the prose disagrees with the packet. The check is built only if misreports show up, and then it warns first, as ruled.

## 11. Revision after the second review (2026-10-02)

Each finding was re-checked against the code at `c38a844b`. The per-finding record (held / partly / did not hold, with evidence) is `comms/2026-10-02-adr0150-review-fixes-cloud-branch.md`. What changed in this doc:

| Area | Change | Where |
|---|---|---|
| Facade guard | The protected set is computed at test time from the free names of moved modules, minus declared late-bound names. Moved values are deleted, and the `session` module refuses assignment to protected names | §6.2 item 3 |
| Tripwire and floor | Check every path field of `Layout` and `Config`. The floor refuses any write under the launch or main checkout | §6.2 item 4 |
| Cache | `Layout`/`Config` are rebuilt per call. Only `scutil` and the common dir are cached, keyed on `ROOT` and the identities of `_git_common_dir` and `_shared_work_root` | §6.2 item 2 |
| `__file__` | The tests dir and entry script are injected from `session.py`'s path. `__file__` is banned in moved modules | §6.2 item 2, §8 item 4 |
| HOME | Redirected at the suite's entry before import, and only then is the HOME check on | §6.2 item 4 |
| Invariants | I-S1, I-L1, I-L3, I-L6, I-L10 and I-E1 corrected. I-E6, I-L11 and I-L12 added. I-L5 extended (budget generator, attempt base). Progress goes through an injected `emit` | §4 |
| Hidden state | Process caches, the lambda tables, `Path.cwd()` sites, child env, import-time values, `sys.path`, and the exact env-name list | §0.2, §6.2 |
| M0 | Guard self-tests, child-runner helper, invariant pins, HOME redirect, golden start JSON with a pinned clock and machine | §6.5 items 9–14 |
| Rollback | Roll forward, or revert the newest step only. M8–M11 roll back as one unit | §6.3 |
| Dependencies | Encoded as `blocked-by` (WI-0488, WI-0490, WI-0491, WI-0468), not prose. Cleanup P1/P3/P4/P5 and Linux have landed | §6.1 |
| Cuts | M13 and M14 cut. The Git port is narrowed to `update_ref_cas` and the publish result. Prose-vs-evidence claim checking is deferred until the Q4 measurement | §6.1, §2.1, §5.2, §7 |
| Line numbers | Refreshed at `c38a844b`. Citations name the symbol first | throughout |

