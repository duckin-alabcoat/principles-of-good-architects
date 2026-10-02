# Design — harness module boundaries (explicit context, narrow ports, phased lifecycle)

**Status:** DESIGN, **Proposed** with [ADR-0150](../adr/0150-the-harness-gets-real-module-boundaries-behind-explicit-context-and-narrow-ports.md). No code changes. the operator ruled on 2026-10-02 that the design is done now and **all implementation waits until after the OPS-0010 soak (due 2026-10-07)**.
**Drafted:** session ~444 (2026-10-02), Federation Architect, at trunk `dc74d848`.
**Source:** `proposed-edits/federation-arch/accepted/2026-10-02-consultant-harness-redesign-design.md` (the brief), with Astra's review `…/accepted/2026-10-02-astra-refactoring-review.md` §1, §3, §4 and §6 and its acceptance criteria.
**Fits beside, and does not redo:** `…/pending/2026-10-02-consultant-refactor-cleanup.md` (packages 1–5: one liveness helper, adoption prompt, capability detection, first brief-parsing extraction, CLI registration split) and `…/pending/2026-10-02-consultant-linux-support.md` (core on Linux, launchd stays macOS-only).

Every line number below was read at `dc74d848`. The redesign moves code, so these numbers will rot. Each implementation item re-reads them before building, and `curate/check_citations.py` gates the ones that stop resolving.

---

## 0. Where the code is today (the facts the design rests on)

### 0.1 One namespace, nine files

`sessionlib/__init__.py:11` lists nine parts: `config, goal, coord, store, journal, land, trunkcheck, lanes, hooks`. `load()` runs each one with `exec` into the caller's globals (`sessionlib/__init__.py:14-21`). `session.py` calls `sessionlib.load(globals())`, so all 40.5k lines share **one** dictionary of globals.

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
| State dirs | `SESSION_STATE_DIR` (`sessionlib/config.py:904`), `JOURNAL_DIR` (`sessionlib/config.py:1955`), `WI_DIRNAME`, `ARCHIVE`; snapshots `_LAUNCH_ROOT` (`sessionlib/config.py:916`) etc. exist *because* tests rebind the live ones | 31, 13, 29, 6 |
| Machine | `detect_machine()` runs `scutil` (`sessionlib/config.py:719`); `_coord_host()` uses `socket.gethostname()`; `os.uname()` at `sessionlib/lanes.py:7869` | 14 + 6 + 1 |
| Session identity | `_claude_session_id()` reads `CLAUDE_CODE_SESSION_ID` (`sessionlib/config.py:6244`), plus 7 direct reads | 17 |
| Clock | `_now_iso()` (`sessionlib/config.py:1026`); 25 `datetime.now`, 66 `time.time`, 65 `time.monotonic`, 10 `time.sleep`. No injectable clock | everywhere |
| Environment | 57 keyed `os.environ` reads of 26 distinct names, plus ~8 whole-env copies for child processes | everywhere |
| HOME | `data_root_of(cfg, env=os.environ)` (`sessionlib/config.py:194`) takes it as a parameter; six other sites call `Path.home()` | 7 |

**Two precedents for the target shape already exist.** `data_root_of` takes `env` as an argument. `sessionlib/registry_state.py` is an ordinary module, outside `PARTS`, whose functions take `root` and `shared` as parameters (`sessionlib/registry_state.py:23`, `sessionlib/registry_state.py:68`).

### 0.3 The worked example: the 2026-09-27 nightly (WI-0432)

The 02:30 nightly ran under the launchd job `com.federation.gate-inputs` and went red, 16 F and 56 E. There were two causes, and they share one shape:

1. **deploy/runner.py.** `running_under_unit()` (`deploy/runner.py:1959`) reads `XPC_SERVICE_NAME`. launchd sets that variable for every process it starts, and the test fixtures inherited it. `refuse_unsealed_process_root` (`deploy/runner.py:3588`) then refused the test's tree. Nothing reproduced from a shell, where the variable is unset.
2. **sessionlib.** With no `CLAUDE_CODE_SESSION_ID` set, `_find_holder_journal` (`sessionlib/journal.py:562`) falls back to the current directory and then to `POGA_INVOKED_FROM`. Under launchd that reached the real checkout's journals. The chain is `_lane_exit_epoch` → `_own_frontmatter` → `_find_holder_journal`.

Both causes took **identity from the ambient environment and the working directory** instead of having it passed in. The fix landed in tests only: `XPC_SERVICE_NAME` was added to `CLEARED_ENV`. The class of bug remains, because any new env read is a new hole. Under an explicit context, the environment is read **once**, at the adapter, into a typed value. A test supplies that value. Ambient state cannot leak in, because nothing below the adapter can see it.

### 0.4 Shelling out, and the platform

- **One wrapper runs every process: `sh()`** (`sessionlib/config.py:582`). It defaults to `cwd=ROOT`. About 305 calls are literal `git` argv (land 122, lanes 73, config 51, store 25). There are also about 38 direct `subprocess.run` calls, for `ps`, `tmux`, `osascript`, `launchctl`, `ssh`, `lsof`, `claude`, `infocmp` and `tic`.
- **Platform calls in sessionlib:**
  - `scutil` (`sessionlib/config.py:721`)
  - `sysctl` (`sessionlib/hooks.py:1756`)
  - `xattr` (`sessionlib/hooks.py:1403`)
  - `launchctl managername` (`sessionlib/lanes.py:6459`)
  - `osascript` (`sessionlib/lanes.py:6606`)
  - `lsof` (`sessionlib/config.py:6852`)
  - BSD `ps -axo`
  - `st_birthtime` (`sessionlib/config.py:7047`)
- **Not in sessionlib:** there is no `plutil`, `sandbox-exec` or `systemctl` there. launchd unit management lives in `deploy/runner.py` (`deploy/runner.py:1304-1833`).

### 0.5 Locks (coordination store)

Everything lives under `<git-common-dir>/poga-coord/<kind>/<name>.json` (`_coord_dir`, `sessionlib/coord.py:121`). There is no `fcntl`. The only primitives are an `O_EXCL` create and `atomic_write` (temp file plus `os.replace`, `sessionlib/config.py:688`).

| Lock | Where | On contention |
|---|---|---|
| `land-queue/<id>` ticket → `land-gate/trunk` | `land_gate_lock` (`sessionlib/coord.py:1697`), gate record `sessionlib/land.py:751` | FIFO wait. Polls every 5 s and gives up after 90 min, then proceeds **unserialized**. TTL 45 min, renewed by a 60 s heartbeat thread. Reclaimed only if the holder is expired **and** proven dead |
| `trunk-lock/trunk` | `trunk_lock` (`sessionlib/coord.py:1879`) | Retries for 30 s, then proceeds unlocked. TTL 120 s. Held only around `update-ref` |
| `leases/<name>` | `lease()` (`sessionlib/coord.py:1268`) | Refuses (`LeaseHeld`) |
| `lane-alloc/poga-N` | `_lane_reserve` (`sessionlib/lanes.py:199`) | Tries the next N |
| `dispatch-spawn`, `dispatch-item`, claims, number allocation | `sessionlib/lanes.py:6637`, `sessionlib/coord.py:2977`, `_counter_reserve_next` (`sessionlib/config.py:3550`) | Refuse; or n+1, up to 64 times |

**Nesting order** is queue ticket → land gate → trunk lock. `_store_autocommit` takes the trunk lock without the gate. `_ff_trunk_to_origin` runs both outside the gate (from the pre-land check, `sessionlib/land.py:477`) and inside it (from integrate, `sessionlib/land.py:3965`).

**Two trunk moves take no trunk lock:**
- Inside the gate, `_recompile_main_checkout` commits the views on the main checkout (`sessionlib/land.py:4542`).
- At start, `git_sync` fast-forwards from upstream and pushes (`sessionlib/config.py:873`, `sessionlib/config.py:884`), with neither lock.

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
| `Identity` | machine label, host name, runtime id, durable session id, the Claude session id, dispatch item, "invoked from", unattended flag | `detect_machine`, `_coord_host`, `POGA_RUNTIME_ID`, `durable_session_id` (`sessionlib/config.py:2041`), `CLAUDE_CODE_SESSION_ID`, `POGA_DISPATCH*`, `POGA_INVOKED_FROM` |
| `Clock` | `now()` (tz-aware), `monotonic()`, `sleep(s)` | `datetime.now`, `time.*` |
| `Env` | a **parsed** view of the 26 names this harness reads, as typed fields (e.g. `land_on_unverified_trunk: bool`), plus `child_env()` for the ~8 places that build a child's environment | `os.environ`, once |

`RuntimeContext` exists, but only as the **bundle an adapter builds and hands to the top-level phase**. The rule is enforced by the import check in §3.3: **no function below the lifecycle layer accepts `RuntimeContext`**. A land-gate policy function that needs the trunk name and a clock takes `trunk: str, clock: Clock`, not the bundle. The bundle is how the top of the stack carries its values, not a service locator.

### 1.3 Identity resolution becomes a pure function

`_find_holder_journal` (`sessionlib/journal.py:562`) and the other "who am I" probes become `resolve_identity(env: Env, layout: Layout, cwd: Path) -> Identity`. The fallback order (session id, then working directory, then `POGA_INVOKED_FROM`) stays the same, but it becomes **visible and testable**: a test can build an `Env` with no session id and a `cwd` in the real checkout, and assert what the function answers. That test is the regression test for cause 2 of §0.3.

---

## 2. Narrow interfaces (ports)

Each port is a small `Protocol` with a real implementation and a test fake. A port carries **verbs a caller needs, not a general `run(argv)`**. A general escape hatch is allowed only where the argv really is data (the gate command, `sessionlib/config.py:7412`).

### 2.1 `Git`

Bound to one repository path at construction. Its verbs come from the call sites (land 122, lanes 73, config 51, store 25):

- `rev_parse(ref)`, `ref_exists(ref)`, `is_ancestor(a, b)`, `merge_base(a, b)`
- `update_ref_cas(ref, new, expected)`, the CAS at the heart of a land (`sessionlib/land.py:1169`)
- `rebase(onto)`, `rebase_abort()`, `add_all()`, `commit(msg, trailers)`
- `worktree_add`, `worktree_remove`, `worktree_lock`, `worktree_unlock`, `worktree_list`
- `fetch(remote, timeout)`, `push(remote, refs, atomic, timeout)` returning a typed `PushResult` (accepted / rejected / unreachable)
- `status_porcelain()`, `diff_names(a, b)`, `log(range, fmt)`, `show(ref, path)`

`sh()` stays as the real implementation's single process runner. The port's job is that **no caller builds git argv by hand**, so a fake can answer at the level of meaning ("this push was rejected"). Today, tests that fake git have to fake text output.

### 2.2 `Proc`

- `run(argv, cwd, env, timeout, stdin)` for the non-git programs
- `liveness(pid) -> Alive | Dead | Unknown`, a **three-state** answer
- `list_processes()`, `process_cwd(pid)`, `signal(pid, sig)`

The cleanup brief's package 1 settles the liveness contract. Today there are two `_pid_alive` definitions (`sessionlib/config.py:6669` and `sessionlib/config.py:7092`, where the later one silently wins), plus `_process_alive` (`sessionlib/config.py:1539`). **The port takes the shape package 1 lands, whatever it is, and only moves it.** If package 1 lands a bool with a safe direction per caller, the port returns that bool. The three-state shape above is shown only as the natural reading of "do not answer dead when unsure". It is not a second decision. Callers that remove things on a dead answer: `_scan_lanes` → `_reap_lane` (`sessionlib/lanes.py:2117`, `sessionlib/lanes.py:3739`) and `_unlock_if_owner_is_dead` (`sessionlib/coord.py:2805`).

### 2.3 `CoordStore`

The coordination store becomes one module (`coord/store.py`, §3) behind this port:

- `try_acquire(kind, name, owner, ttl) -> Acquired | Held(record) | Unavailable`
- `release_exact(record)`, `refresh(record)`, `read`, `list`, `reap(now)`

The locks are built on it as context managers that keep **today's exact semantics** (§4.4):
- `land_gate(…)` (queue → gate)
- `trunk_lock(…)`
- `lease(name)`

**`Unavailable` is a typed value, not a silent `(True, {})`.** The fail-open behavior is kept, but the caller decides it in its own code where a reviewer can see it. Today it is buried in `_coord_try_acquire` (`sessionlib/coord.py:351`).

Re-entrancy moves from the process globals `_LAND_GATE_DEPTH` and `_TRUNK_LOCK_DEPTH` to a `LockRegistry`. **It is a module-level singleton in `coord/locks.py`, not owned by any store instance.** Facades build ports per call (§6.2), so a registry per instance would start every nested acquire at depth 0. The nested acquire would then wait on its own record: 30 s and then unlocked for the trunk lock, up to 90 minutes for the gate. The semantics stay the same: re-entrant per process, not keyed on identity. The registry is no longer reachable from another module by `global`.

Tests that set or assert the depth counters are on M4's patched-name ledger (§6.2): `session._LAND_GATE_DEPTH = 0` in four files, `_TRUNK_LOCK_DEPTH` in one, and the assert at `tests/test_land_gate_lock.py:137`. Left alone, they would read and write a dead name and pass vacuously.

The land-gate context manager **keeps the name `land_gate_lock`**. The section-opener tables in `tests/test_integrate_holds_no_suite.py` match on that name, and `_land_gate` is already a different function (the cheap-check gate).

M4 also checks whether the gate's nesting is still reachable after WI-0378 before preserving it "exactly". The re-entrancy comment at `sessionlib/coord.py:1712` predates that item.

### 2.4 `Platform`

This is the **seam the Linux-support brief needs**:

- `machine_name()` (today `scutil`)
- `memory_pressure()` (today `sysctl`)
- `quarantine(path)` (today `xattr`)
- `gui_session()` (today `launchctl managername`)
- `open_terminal(cmd)` (today `osascript` / Ghostty)
- `file_birthtime(path)` (today `st_birthtime`)
- `process_table()` (today BSD `ps -axo`)
- `process_cwd(pid)` (today `lsof -a -d cwd`, `sessionlib/config.py:6852`)

`Proc.process_cwd` delegates to the `Platform` method.

There are two implementations: `MacPlatform` and `LinuxPlatform`. **The Linux lane is doing the portability fixes now.** This design does not redo them. When a step here reaches a platform call, it moves the call, **including whatever fallback the Linux lane gave it**, behind `Platform`. "Not available on this OS" becomes the typed answer `Unsupported(reason)`, never a guess.

**launchd unit management is not part of `Platform`.** It belongs to the deploy runner, which stays macOS-only per the Linux ruling. It gets its own `Units` port inside `deploy/` (§7.1).

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
| `policy/shell.py` (L0) | Bash-command segmentation and guard verdicts: `top_level_segments`, `_split_top_level`, `destructive_git_violation`, `peer_injection_violation`, `safe_git_auto_allow`, `false_green_violation`, `compound_violation` | stdlib | anything L1+ | `sessionlib/config.py:7446-8212`, already pure (no env, clock or subprocess) |
| `briefs/` (L0 + one L2 seam) | Brief parsing, op planning, text transforms (`parse_frontmatter`, `split_op_sections`, `apply_replace`, `apply_version_bump`, `Plan`, `evaluate_brief`, `apply_changelog_prepend`), and the shared frontmatter parser | stdlib; apply step takes `CoordStore` (lease) + `Layout` | git, land, lanes | `sessionlib/config.py:8277-8548`, `sessionlib/lanes.py:4636-4936`, `sessionlib/journal.py:2614`. **This is cleanup package 4. This design only records where it sits** |
| `context.py` (L1) | `Layout`, `Config`, `Identity`, `Clock`, `Env`, `RuntimeContext`; `resolve_identity` | stdlib, L0 | L2+ | the globals in §0.2 |
| `ports/git.py`, `ports/proc.py`, `ports/platform.py` (L1) | §2.1, §2.2, §2.4 | stdlib, `context` | L2+ | `sh` (`sessionlib/config.py:582`), process and platform helpers |
| `fs.py` (L1) | `atomic_write`, sidecar read/write | stdlib | L2+ | `sessionlib/config.py:688`, `sessionlib/config.py:1136` |
| `coord/` (L2) | `CoordStore`, record format, reap, locks (`land_gate`, `trunk_lock`, `lease`), lane slot allocation, claims, counter allocation (`_counter_reserve_next`) | L0, L1 | journal, store, land, lanes | `sessionlib/coord.py:1-1700`, `sessionlib/coord.py:1697-1935`, `sessionlib/config.py:2154-3692` |
| `store/` (L2) | Work-item and ops repositories (one file per item, the sole writer, autocommit through `Git`, feed), inbox state, attention and escalation records | L0, L1, `coord` (allocator) | journal, land, lanes | `sessionlib/store.py`, `sessionlib/config.py:3693-4676` |
| `journal/` (L2) | Journal render, parse, finalize, resolve; banners | L0, L1 | land, lanes, views | `sessionlib/journal.py:44-405`, `sessionlib/journal.py:1579-1630`, `sessionlib/journal.py:2289-2612` |
| `views/` (L2) | Generated views: handoff compile, ROADMAP regions, STATUS stamp, ledgers. **Pure renderers over records passed in.** Writing them is done by the caller | L0, L1, `journal` (read), `store` (read) | land, lanes | `sessionlib/journal.py:608-1950`, `sessionlib/journal.py:2166-2254` |
| `releases/` (L3) | Release cut and its preflight and tag | L0–L2 | land, lanes | `sessionlib/config.py:4677-5258`, `sessionlib/coord.py:2206-2492` |
| `grants/` (L2) | Authorization grants (mint, resolve, cite) | L0, L1 | L3+ | `sessionlib/config.py:8549-8992` |
| `land/` (L3) | Gate and verdict policy (ADR-0148 store), `serialized_advance`, publication (owed/resume), push outcomes, integrate transaction, main-checkout materialization, rebase-conflict policy, receipts | L0–L2 | lanes, lifecycle | `sessionlib/land.py` (ranges in §4.3) |
| `trunkcheck/` (L3) | Detached post-land trunk check, culprit, revert proposal | L0–L2 | lanes, lifecycle | `sessionlib/trunkcheck.py` |
| `lanes/` (L3) | Lane identity and slots, scan and classify (pure verdicts over facts), reap, janitor, teardown, parked refs, dispatch (plan pure / spawn impure), attach, drill, supervise | L0–L2, `land` **read-only queries** passed in as functions | lifecycle | `sessionlib/lanes.py` minus 659-1062 |
| `lifecycle/start.py`, `lifecycle/end.py` (L4) | The phases of §4.1 and §4.2; the evidence packet of §5 | L0–L3 | adapters | `sessionlib/hooks.py:172-706`, `sessionlib/hooks.py:821-1219`, `_complete_lazy_start` (`sessionlib/config.py:6293`) |
| `land/lander.py` (L4) | The phases of §4.3 | L0–L3 | adapters | `_land_worktree_lane` (`sessionlib/lanes.py:659-1062`) |
| `cli/` (L5) | Per-family parser registration (12 families, 93 verbs), choke points before dispatch | everything | — | `hooks.main` (`sessionlib/hooks.py:3098-4426`). **The split itself is cleanup package 5. If package 5 lands, this step only moves it to L5** |
| `hooks/` (L5) | Hook stdin/stdout adapters: `_read_hook_stdin`, `_emit_start`, check-bash and check-question deny/allow output, the heartbeat's Stop decision | everything | — | `sessionlib/hooks.py:14-62`, `sessionlib/config.py:8247`, `sessionlib/hooks.py:2858`, `sessionlib/hooks.py:2944` |

`goal.py` and `registry_state.py` sit in L2, beside `store/`. `registry_state.py` already conforms.

### 3.3 Breaking the 123-symbol cycle

The cycle ADR-0118 measured is the reason a real-module split could not be ordered. It is broken **edge by edge, from the leaves inward**, with three tools:

1. **Move orchestration up.** A function that calls into two sibling subsystems moves to the layer above both. `_land_worktree_lane` is the biggest case: it lives in `lanes.py` but is mostly `land.py` calls, so it moves to `L4 land/lander.py`.
2. **Pass a query, not a module.** Where a lower layer needs one answer from a higher one, it takes it as a value or a callable parameter. Example: coord reaping needs "is this branch on trunk?". Every such parameter is listed in the module's docstring. **A callable parameter is a debt, not a pattern.** Each one has a removal note.
3. **Turn a decision into a pure verdict.** For example: the reclaim decision inside `_coord_try_acquire`, the lane classification in `_scan_lanes` (`sessionlib/lanes.py:2135-2190`), the end refusals, and the land refusals. Each becomes `verdict(facts) -> Decision` in L0. The impure caller gathers the facts and then acts.

Step M1 (§6) computes the call graph again at the starting commit and records the extraction order. **The order is derived, not guessed.** Each step's item names the edges it removes.

---

## 4. Lifecycle as phases

Each of the three big functions becomes a short orchestrator over named phases. Each phase **returns a structured result** (a frozen dataclass) and never prints. The orchestrator decides what to print, using a renderer, and what exit code to return.

**Splitting does not license changing semantics.** For each orchestrator below, the invariant list is the acceptance contract for its step. A behavior not in the list is preserved by the existing tests. The list names the behaviors that are easy to break *by the act of splitting*.

### 4.1 Start (`_cmd_start`, `sessionlib/hooks.py:218-706`, 489 lines)

| Phase | Today | Result |
|---|---|---|
| S0 Inputs | Hook stdin (`sessionlib/hooks.py:222`), banner file, dispatch brief and receipt (`sessionlib/hooks.py:233-241`), machine and stamp | `StartInputs` |
| S1 Idempotency | `_my_open_journal` (`sessionlib/hooks.py:251`); already-open → print and return; prep adoption via `_claim_prepped_journal` (`sessionlib/hooks.py:292`) | `AlreadyOpen \| Adopted \| Fresh` |
| S2 Sync | Read-only git status; `git_sync` (`sessionlib/config.py:832`), skipped on a dirty tree, push only on a manual run | `SyncResult` |
| S3 Mutations | Three branches: adopted (`.live` sidecar only), lazy (sidecars only, real work deferred to `_complete_lazy_start`), eager (freeze → reap journals → janitor → write journal → `.live` → supersede → session branch → compile → prep marker), `sessionlib/hooks.py:349-431` | `StartWrites` (a list of what was written) |
| S4 Orientation | About 50 optional lines (`sessionlib/hooks.py:448-667`), almost all fail-open | `Orientation` (typed lines, each with its own failure captured) |
| S5 Emit | `--dry-run` / `--prep` payload / hook JSON via `_emit_start` | adapter output |

**Invariants that must not move:**
- **I-S1.** Every refusal and early return happens before S3 writes anything. The exception is the dispatch receipt, which today is written *before* any early return (`sessionlib/hooks.py:241`). That ordering is kept on purpose.
- **I-S2.** The lazy path writes only the two sidecars. Everything else waits for the first heartbeat (`_complete_lazy_start`, `sessionlib/config.py:6293`).
- **I-S3.** The eager path runs its write steps in the order listed above.
- **I-S4.** Orientation lines fail open, one by one. One failing line never suppresses the others or the emit. In the phase version, each line's exception is **recorded in the result** rather than swallowed, and the renderer decides to drop it. Behavior is the same, and the failure is visible to tests.
- **I-S5.** `_PREP_PAYLOAD_WRITTEN` (`sessionlib/hooks.py:102`) and the nonzero exit when a prep run writes no payload (`sessionlib/hooks.py:211`) stay. They become a field of the S5 result, not a module global.
- **I-S6.** A dirty tree never blocks a start.
- **I-S7.** `start` takes no lock. Its writes are separate atomic files, and none of them depends on another's success. This includes `git_sync`'s fast-forward and push, which run with neither the gate nor the trunk lock. **The redesign preserves this.** It is a recorded hazard (§9), not an endorsement. Adding a lock here is a behavior change, so it needs its own decision.

### 4.2 End (`cmd_end`, `sessionlib/hooks.py:821-1219`, 399 lines)

| Phase | Today | Result |
|---|---|---|
| E0 Lazy completion | `_complete_lazy_start` runs first (`sessionlib/hooks.py:848`), so it can write before any refusal | `LazyResult` |
| E1 Refusals | No config; journal unresolvable; no frontmatter; already ended; no authorization (`--confirm` → dispatch record → unattended record); contradicting dispatched close (`sessionlib/hooks.py:833-954`) | `EndRefused(reason)` or `EndAuthorized(how)`, as a **pure verdict over gathered facts** |
| E2 Evidence | *New.* Build the evidence packet (§5) | `EndEvidence` |
| E3 Close | Clear attention → write the closed journal once (`sessionlib/hooks.py:995-1009`) → publish `session-close` (landed unknown) → registry `record_end` → `end-ran` sidecar → release the lane reservation | `CloseWrites` |
| E4 Land | Lane: `land/lander` (§4.3). Legacy branch: `_land_branch`. Trunk: compile → `_stamp_status` → `_land_candidate` | `LandOutcome` |
| E5 After | Lane-exit report, close runtime, republish the close with the real `landed`, summary, `_exit_on_failed_land` | exit code 0 / 1 / 2 |

**Invariants that must not move:**
- **I-E1.** E1 refusals fire before the E3 writes, with **two exceptions that exist today and stay**:
  - E0 (lazy completion) runs first.
  - `_attention_clear()` (`sessionlib/hooks.py:875`) runs **after** the journal refusals (no config, unresolvable, no frontmatter, already ended) but **before** the authorization refusals (`--confirm`, dispatched close). A refused unauthorized close has therefore already cleared the attention record.

  The phase split keeps that order exactly. Whether it is right is a §9 finding, not a refactor change.
- **I-E2.** The journal is closed and the close published **before** the land (docstring `sessionlib/hooks.py:824-826`). A failed land leaves the journal stamped and the exit nonzero. A second `end` is refused as already closed, and the recovery path is `merge --continue`.
- **I-E3.** The exit codes are those of the land: 0 landed or nothing to land, 1 refused, 2 trunk moved or contended.
- **I-E4.** `end` does not commit store notes. The store autocommits on its own.
- **I-E5.** Focus and blocked reach STATUS only through `_recompile_main_checkout` / `_stamp_status`, written all-or-nothing (`sessionlib/journal.py:2166`).

**One gap the phases make visible (recorded, not fixed by the refactor):** a crash between E3 and E4 leaves a closed but unlanded journal, and no single record says how far the close got. The start-time scan (`sessionlib/hooks.py:509-518`) catches only the opposite case. The phase version gives E3 a durable `close-progress` sidecar **only if the operator rules for it** (open question Q3). The refactor itself must not add it silently.

### 4.3 Land (`_land_worktree_lane`, `sessionlib/lanes.py:659-1062`, 404 lines)

Today's order, with the phase each step joins:

| Phase | Steps (today's lines) | Mutates | Result |
|---|---|---|---|
| L-A Verdict | `_ensure_lane_verdict` (`sessionlib/land.py:2842`) may run the suite in the lane; `mark_ready_to_land` | verdict file; `_READY_TO_LAND`, `_LAND_BUILD` globals | `Verdict \| Refused(gate)` |
| L-B Prepare | Absorb stop-checkpoint; `_reap_dead_journals`; `git add -A` + close commit with grant trailer (`sessionlib/lanes.py:714-756`) | the lane branch only | `PreparedLane` |
| L-C Attempt (looped) | Trunk current (`_require_trunk_current_for_land`, `sessionlib/land.py:523`) → read parent → restore views → rebase or fast-forward → conflict resolve (≤ 50 steps) → nothing-to-land / resume owed publication (which takes the gate itself, through `_serialized_publish`) → renumber → counter gates (`sessionlib/land.py:2185`) → `_land_gate` cheap checks in a scratch worktree (`sessionlib/land.py:2906`). **When no verdict exists, `_land_gate` runs the full suite once here, outside the gate** (`sessionlib/land.py:2966`), and records it in `accepted` | the lane branch; scratch worktree (removed in `finally`); may fast-forward the shared trunk to origin under the trunk lock only; **updates `accepted`** | `Ready(tip, parent, accepted) \| Refused(reason) \| Nothing \| Resumed` |
| L-D Advance | `_serialized_advance` (`sessionlib/land.py:1129`): land gate → re-read trunk → `update-ref` CAS under the trunk lock → `publish` closure (release numbers → sync main checkout → recompile views, **which commits on the main checkout without the trunk lock**, `sessionlib/land.py:4542` → push) | the trunk ref (CAS); a second trunk commit from the recompile; the main checkout; origin | `Landed(stages) \| Moved \| Failed` |
| L-E After | Escalate a rejected push to integrate (outside the lock); spawn the trunk check (detached); `_land_receipt` (every attempt); resume owed publication if `publish_error`; retry on `Moved/Failed` within `_CasBudget` (`sessionlib/land.py:3255`) | receipts log; integrate's own lock and push | `LandOutcome` |
| L-F Dispatch | `_dispatch_after_land` on full success only | dispatch records | — |

**Invariants that must not move:**
- **I-L1, lock order.** Queue ticket → land gate → trunk lock, and nothing else is ever taken inside the gate. The trunk lock is held **only** around `update-ref`. The gate is held from the trunk re-read through `publish`. No suite and no lane-side work runs while the gate is held. **One attempt can take the gate twice**: `_serialized_advance`, then `_serialized_publish` (`sessionlib/land.py:1551`) through a forced resume. The resume can also run at the start of an attempt when HEAD equals parent. Both sections follow the same order and release rules. This is guarded today by `tests/test_integrate_holds_no_suite.py` and `tests/test_land_lock_holds_only_the_merge.py`.
- **I-L2, release.** Each lock's `finally` releases **exactly the record it wrote** (`_coord_release_exact`), so a reclaimed record is never unlinked. The gate is released before the ticket. The heartbeat thread stops in the inner `finally`.
- **I-L3, the CAS is the only all-or-nothing point.** Everything before it writes only to the lane, which the lane owns. Everything after it is reconciled against refs (`_publication_owed`, `sessionlib/land.py:1452`), never against receipts ([ADR-0122](../adr/0122-validation-reuse-and-resumable-completion.md) D2). **No phase may add a rollback.** There is none today, and the design depends on there being none.
- **I-L4, moved means nothing written.** If the trunk is no longer `parent` under the gate, the outcome is `Moved` and nothing is written.
- **I-L5, the retry boundary.** Only `Moved` and `Failed` retry. Each attempt redoes L-C completely. The suite verdict (`accepted`) carries across attempts and is **never** re-run ([ADR-0148](../adr/0148-a-land-is-a-merge.md) D4). Today that works because `_land_gate` mutates one shared dict in place (`sessionlib/land.py:2924-2977`). In the phase version, L-C returns the updated `accepted`, and **the orchestrator threads it into the next attempt**. A phase result that drops it would re-run the full suite on attempt 2. The budget is at least 3 and at most 12 attempts within 1200 s, sized by the slowest attempt. `attempts=N` pins it.
- **I-L6, refusals are the same set with the same exit codes:**
  - `gate`, `diverged`, `unverified`, `trunk`, `conflict`, `counter`, `trunk-red`, `delivery`, `gone`: exit 1
  - `contended`: exit 2
  - `nothing`: exit 0
  
  `_require_trunk_current_for_land` can also pass through its own state string (`sessionlib/land.py:576`). Receipts: `_land_receipt` writes one on every attempt. The `trunk-red` refusal writes its own. `_resume_owed_publication` writes a `resume-lane` receipt on `resumed`, `delivery` and `gone` (`sessionlib/land.py:1677`). Every return stays a literal `LandOutcome(...)` (the guard at `tests/test_worktree_lane.py:3942`).
- **I-L7, publish failure.** A `publish` exception after the CAS records `publish_error`, and a forced resume runs. If the resume still owes something, the lander returns `delivery` or `gone` (exit 1) even though the trunk has advanced (`sessionlib/lanes.py:1036-1047`). The session stays open over it ([ADR-0122](../adr/0122-validation-reuse-and-resumable-completion.md) D2). The phase version keeps both halves: the trunk did move, and the land is not reported as complete.
- **I-L8, fail-open stays fail-open.** No store, a filesystem error, or the 90-minute wait cap means the land proceeds unserialized. The phase version makes this a typed `Unavailable` (§2.3) that the orchestrator turns into the same behavior. **Changing it to fail closed is a separate decision** (Q2).
- **I-L9, the source-level guards are retargeted, not weakened.** The tests that read the lander's own source must follow the code to the orchestrator and phase functions, and keep both their assertions and their negative controls:
  - `_land_worktree_lane` must contain `_serialized_advance`, `_land_receipt`, `mark_ready_to_land` and `_require_trunk_current_for_land`, and must not contain `land_gate_lock` (`tests/test_land_lock_holds_only_the_merge.py`, `tests/test_land_refuses_a_stale_trunk.py`).
  - The lander does not push on its own (`tests/test_land_publishes.py`).
  - `MUST_BE_WATCHED` resolves `_land_worktree_lane` and `publish` (`tests/test_integrate_holds_no_suite.py`).
  
  A guard that looked inside one function body must still be able to fail after the split. Each retargeted guard is proven by a mutation that removes the guarded call.

  Three ways a guard can go blind, each closed in the step that creates the risk:
  1. `_harness_trees` in `tests/test_integrate_holds_no_suite.py` keys the call graph by bare name and keeps the first definition. A facade with the same name as the real function can shadow it. **Guards resolve functions by module-qualified name.**
  2. Four tests read `inspect.getsource(session.cmd_start)` and rely on `cmd_start.__wrapped__` (`sessionlib/hooks.py:714`). **Every facade sets `__wrapped__` to the real function**, or the guard reads the wrapper and an `assertNotIn` passes vacuously.
  3. The section-opener table matches `land_gate_lock` by name. **The name is kept** (§2.3).
- **I-L10, the receipt clock.** `_READY_TO_LAND` and `_LAND_BUILD` are process globals. `mark_ready_to_land` is idempotent: the first mark wins and survives refusals and retries within the process. Only a `landed` receipt clears them (`sessionlib/land.py:943-958`). The receipt's `phases` stay an exact partition of `total_seconds` (ADR-0148 D7, FL7). **These stay process-lifetime state**, moved into one module-level clock object. Unlike `_PREP_PAYLOAD_WRITTEN` (I-S5), they must not become per-call result fields. A refused-then-retried land in the same process would otherwise restart its clock.

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
3. `end` validates:
   - Required semantic sections are present and not template text.
   - Every WI, ADR or OPS id the prose names exists in the packet or the store. An unknown id is flagged.
   - **Outcome claims are checked against evidence.** Where the prose says something landed, was tested or was pushed, the packet must agree. If it does not, the close **prints the disagreement** and proceeds. It does not refuse.
4. Code renders the mechanical sections (`Evidence`, `Commits`, `Changed paths`, `Land`) into the journal itself. The model never types them.

**Q4 for the operator:** whether a disagreement in step 3 should refuse instead of warn.

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
cleanup lane (separate, after Linux):  P1 liveness   P3 capability description   P4 brief extraction
                                          │                 │                          │
M0  test floor (tests only) ──────────────┼─────────────────┼──────────────────────────┤
M1  context + ports skeleton, guards, recursive harness_files  ◄── needs P3 and P4 ─────┘
M2  identity, clock, env through the context builder
M3  Proc + Platform ports, `sh` behind them  ◄── needs P1 and the Linux lane landed
M4  coord/ (store, locks, allocator)           ◄── needs M0 lock tests
M5  store/, journal/, views/, goal, grants/    (L2 repositories)
M6  land/, trunkcheck/, releases/ on the Git port   ◄── needs M0 land floor
M7  lanes/ (scan/reap verdicts, dispatch, attach) and policy/shell.py
M8  start phases
M9  end phases + evidence packet + standard-source edit
M10 lander phases                               ◄── needs M0 land floor, all five scenarios
M11 adapters (cli families → L5, hook I/O)
M12 retire the exec assembler, and the curate/ importers of `session`
M13 deploy/runner.py split    } independent of M1–M12 (neither file imports sessionlib);
M14 poga_cli.py split         } sequenced last by choice, to keep one refactor in flight
```

**Every step is blocked until after the 2026-10-07 OPS-0010 soak.** M0 is tests only and needs nothing else. **M1 waits for the cleanup lane's packages 3 and 4.** Package 3 matters because the capability detector must stop matching text before any file moves (§6.4). Package 4 matters because it is the first real extraction and sets the facade precedent.

**What "below L5" means before M11.** Until the adapters exist as modules, "the adapter" is the `cmd_*` entry function of a verb plus `_context_from_namespace()`. M2 moves the ambient reads it names into that builder. Each later step removes ambient reads from the code it moves. Check §8.4 applies to **the modules that step created**, not to the whole namespace.

### 6.2 The compatibility facade (the answer to §0.1)

For each subsystem moved out of the exec namespace, in one step:

1. **New module, explicit arguments.** The code moves into an ordinary module that takes `Layout`, `Clock`, ports and so on as parameters. It imports nothing from the namespace.
2. **The facade replaces the definition in its own part.** The old name stays in the exec namespace as a thin wrapper, in the same part file where the `def` was. It is not a second definition elsewhere, which would trip cleanup package 1's duplicate-name check. The wrapper sets `__wrapped__` to the real function (I-L9). On **every call** it builds values from the namespace's *current* globals, through one function, `_context_from_namespace()`, and calls the new module. So `session.ROOT = tmp` keeps working for every test not yet migrated, because the wrapper reads `ROOT` at call time.
   - **Build only what the callee takes, cached on the current root.** `detect_machine` runs `scutil` and `_git_common_dir` runs git on every call (`sessionlib/config.py:719`, `sessionlib/config.py:2272`), and the trunk lock polls every 0.02 s. The builder caches each value keyed on the current `ROOT`, following the `_SHARED_WORK_ROOT` pattern (`sessionlib/config.py:2323`), never in a global cache.
   - **Callbacks into the namespace are late-bound.** Where moved code still needs a name that lives in the namespace, the facade passes `lambda *a, **k: ns["X"](*a, **k)`, never the function object. A test that patches `session.X` then still reaches it.
   - **Process-lifetime state stays process-lifetime.** The lock registry (§2.3) and the receipt clock (I-L10) are module singletons. They are never rebuilt per call.
   - **Caches of late-binding lambdas are rebuilt from `Layout`.** `_COUNTERS` (`sessionlib/config.py:3292-3358`) holds lambdas such as `lambda: ROOT / …` that resolve in the shared namespace today. Moved into `coord/`, they would bind the new module's globals instead. M4 keys the counter cache on the `Layout`, and adds a test that rebinds `ROOT` between two draws.
3. **Patched-name ledger, built from free names.** Before the move, the step lists every test that patches a name **defined in, or used by,** the code being moved. The list comes from an AST walk of the moved code's free names, not just its definitions.
   - This covers patched functions the moved code calls, e.g. `sh` (48 sites), `_pid_alive` (11), `_git_common_dir` (8), `_coord_dir` (7) and `_under_test` (6).
   - It also covers patched values that are neither context nor functions, e.g. `_TRUNK_CHECK_POPEN`, `RUNTIMES`, `PREFLIGHT_GATE_CHECKS`, `PROC_SIGNAL_GRACE_SEC`, `HOOK_STDIN_TIMEOUT_SEC`, `CONFIG_PATH`, `USER_SETTINGS_PATH`, `_SHARED_WORK_ROOT`, `_LAUNCH_JOURNAL_DIR`, and the lock depth counters.
   - **Each listed site is either rewritten in the same step** to inject a fake port or value, **or reached through a late-bound callback**.
   - A guard test, `test_no_patch_targets_a_facade`, fails if any test patches a name listed in `sessionlib/_facades.py`.
   - **Its known blind spot:** dynamic `getattr`/`setattr(session, k, …)` save-and-restore loops, e.g. `tests/test_land_gate_liveness.py:78`. The guard cannot see these statically. Each step lists them by grep and reviews them by hand, and the tripwire in step 4 is the backstop.
4. **Live-state tripwire, and the write floor kept.** Under the suite, `_context_from_namespace()` and every real port constructor refuse a `Layout` whose root, shared root or HOME resolves to the operator's real checkout or home, unless a test opts in by name. **`atomic_write` keeps its suite write-floor when it moves to `fs.py`.** Today it calls `_suite_write_refused` (`sessionlib/config.py:637`), which depends on `_real_live_stores` (`sessionlib/config.py:598`) and `_LAUNCH_ROOT`, and its docstring credits that floor with 43 of the 52 measured escapes. So `fs.py` takes the floor as a refusal function installed at import, built from a launch root snapshotted **once at import, never per call**. A facade cannot then rebind it away.
5. **Removal.** A facade is deleted when nothing calls it: no namespace function, no test, and **no `curate/` or `bootstrap.py` importer of `session`**. Those importers are `curate/gate_inputs.py`, `metrics.py`, `finish_line.py`, `common.py`, `push-substrate.py`, `deliver.py`, `check-brief.py`, `check_substrate_docs.py`, and `bootstrap.py`. `sessionlib/_facades.py` lists each facade with the step that removes it. The facade count only goes down. M12 deletes the last one together with the assembler, and moves those importers to the real modules.

**Tests move, they are not weakened.** A test that patched a global is rewritten to pass a fake port or a built `Layout`. Its assertions and its negative control stay. A step's item lists the test files it rewrites and shows each one still fails against a mutation of the behavior it guards.

### 6.3 Per-step contents

Each step's work item carries: the behavior preserved, the dependency removed, the facade and how it is removed, the tests that move, the integration tests that must exist first, and the rollback.

**Rollback.** For M0–M8 and M10–M14, a step is one land of one subsystem behind facades, so the rollback is a revert of that land, and those steps change no on-disk format. **M9 is the one exception.** It renders new journal sections and ships a standard-version bump. Its rollback is a revert **plus a standard-version bump that restores the old end protocol**, because members that already took the new version keep it until they take the next one. Any other step that would change an on-disk format is out of design and needs its own ADR.

| Step | Preserves | Removes | Integration tests that must exist first |
|---|---|---|---|
| M0 | — (tests only) | — | see §6.5 |
| M1 | all | nothing yet; adds `context.py`, `ports/` (interfaces and real implementations wrapping today's helpers), the import-boundary test, `_facades.py` and its guard, the live-state tripwire, the recursive `harness_files()` | M0 |
| M2 | all output, including every timestamp format | ambient clock, env, `scutil` and `CLAUDE_CODE_SESSION_ID` reads inside the functions it routes (`_now_iso`, `detect_machine`, `_claude_session_id`, `_coord_host`, `_find_holder_journal`); `resolve_identity` becomes pure | M0's hermetic-nightly test |
| M3 | reaper and lock behavior per P1's contract | `subprocess`/`os.kill` outside `ports/` for moved code; platform calls outside `Platform`; `sh` becomes the real ports' runner and a facade | P1's liveness tests; the Linux lane's CI job |
| M4 | §0.5 table exactly, I-L1, I-L2, I-L8 | `global _TRUNK_LOCK_DEPTH` across files; coord's reach into journal (`_find_holder_journal` via `_own_frontmatter`, `sessionlib/coord.py:1379`); `_COUNTERS` bound to namespace lambdas | M0 lock tests (two processes) |
| M5 | file formats; the sole-writer rule; autocommit scope | views writing files themselves; store reading `ROOT`; `goal`, `grants` reading globals | existing store tests, made hermetic |
| M6 | every git effect; I-L1–I-L10 untouched | hand-built git argv outside `ports/git.py` in land, trunkcheck and releases; `BOOKKEEPING_PATHS` moved with `curate/store_guard.py` | M0 land floor |
| M7 | lane scan and reap decisions; dispatch locking and refusals; attach and drill output; shell-policy verdicts byte for byte | lanes reading `ROOT`/`CFG`; `_scan_lanes`' inline decision (`sessionlib/lanes.py:2135-2190`) | M0 stale-lock and recover-lanes tests; the existing check-bash corpus |
| M8 | I-S1 … I-S7 | `_cmd_start` as one function; `_PREP_PAYLOAD_WRITTEN` global | start-path tests in a real temp repo |
| M9 | I-E1 … I-E5 | model-typed mechanical sections | end-path tests including failed land and crash after close |
| M10 | I-L1 … I-L10 | `_land_worktree_lane` as one function; the land↔lanes import edge | M0 land floor, all five scenarios |
| M11 | CLI names, flags, defaults and help text byte for byte; hook output byte for byte | parser and hook I/O inside `hooks.py` | golden help and golden hook-output tests |
| M12 | everything | `sessionlib.load`, `PARTS`, `exec`; `curate/` importing `session` | full suite + capability probes on a member |
| M13 | §7.1 | `_deploy` as one function | runner rollback and canary tests |
| M14 | §7.2 | mixed concerns in `poga_cli.py` | restore/init end to end |

**Work items.** M0 through M14 are WI-0469 through WI-0483, in order. Each is blocked by the one before it, and each is marked held until after the 10-07 soak. Their notes state the behavior preserved, the dependency removed and the validation for that step.

| M0 | M1 | M2 | M3 | M4 | M5 | M6 | M7 | M8 | M9 | M10 | M11 | M12 | M13 | M14 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| WI-0469 | WI-0470 | WI-0471 | WI-0472 | WI-0473 | WI-0474 | WI-0475 | WI-0476 | WI-0477 | WI-0478 | WI-0479 | WI-0480 | WI-0481 | WI-0482 | WI-0483 |

### 6.4 Distribution, bootstrap, export and detection

Moving code to subpackages touches every reader that lists harness files. None of these may be left to discover the move on a member:

- `standard_check.py` concatenates `sorted(sessionlib/*.py)` with a **non-recursive** `iterdir` (`standard_check.py:93`), so a subpackage is invisible to it. About 18 detectors match text, and some count calls (`standard_check.py:583-586`). **The cleanup lane's package 3 replaces this with a versioned capability description plus wiring checks and probes. M1 waits for it.**
- `bootstrap.py:257-268` (`sessionlib_files`), `curate/push-substrate.py:126-131`, `curate/standard_version.py:143-161`, `tests/harness_fixture.py:52-81` and `curate/finish_line.py:545` all iterate `sessionlib/*.py` at top level only. **M1 makes them recursive in one place** (one shared `harness_files()` helper), before any subpackage exists.
- `poga:1950` copies `sessionlib` recursively and leaves stale modules on purpose (`poga:2038`). Stale modules are harmless only while nothing imports them by walking the directory, and the M1 import check ensures that.
- `curate/check_substrate_docs.py` requires every `sessionlib/*.py` to be named in `POGA-OVERVIEW-AND-SCALABILITY.md` and `federation-arch.md` §7. Each step updates both docs.
- `curate/store_guard.py:60-75` AST-parses `sessionlib/config.py` for `BOOKKEEPING_PATHS`. M6 moves that constant **and** the reader together.
- `curate/standard_version.py:353-372` already imports `sessionlib.config` for real, and notes that `CFG` is then `None`. After M2 it builds a `Config` instead.
- `curate/public_cut_manifest.json` exports the whole directory, so no change is needed.
- **Members upgrade at different times.** Every detector keeps accepting the old shape until the fleet has the new one (the dual-shape contract, `standard_check.py:67-74`).

### 6.5 M0: the test floor that must exist before any extraction

These are the gaps the mapping found. Each must exist and pass on today's code first, so it can prove the refactor did not change anything:

1. **Two landers as two processes** racing on one bare origin: exactly one CAS wins, the other retries and lands, and neither runs a suite inside the gate. Today concurrency is simulated inside a patched `_run_gate`.
2. **An unreachable push driven through the whole lander.** Today it is covered only at `_push_trunk` level (`tests/test_land_publishes.py:299`).
3. **A CAS `Failed` outcome injected** and retried.
4. **`recover-lanes` driving a real lane `merge` subprocess.** Today `_drive_lane_merge` is patched (`tests/test_finish_line.py:502`).
5. **Stale-lock reclaim across two processes**, holder dead versus alive.
6. **A hermetic nightly:** run the suite's entry under a launchd-like environment (`XPC_SERVICE_NAME` set, no `CLAUDE_CODE_SESSION_ID`, cwd in a scratch copy of a checkout). Assert no read of the real checkout. This is the §0.3 regression, run at the harness level instead of patched per test.
7. **End crash between close and land:** the journal is closed, the land never ran, and the next start reports it. Today that case is invisible (§4.2). This test **pins today's behavior**, and Q3 decides whether that behavior changes.
8. **Golden outputs:** `session.py --help` and every subcommand's `--help`; the SessionStart hook JSON for a fixed fixture; the check-bash allow/deny JSON for a corpus of commands.

Already covered, and kept as is: conflicts (`RebaseResolveTest`, `RoadmapResolveTest`, `ViewAmendConflictTest`); failed gates; rejected push escalated to integrate (`tests/test_integrate_holds_no_suite.py`); resume after a publish exception (`tests/test_resume_at_the_failed_stage.py`).

---

## 7. deploy/runner.py and poga_cli.py (M13, M14)

### 7.1 deploy/runner.py (4,138 lines)

It does not import sessionlib, and it reads its paths late through functions (`deploy/runner.py:110-162`), which is already close to the target. Its seams:

| Module | Owns | From |
|---|---|---|
| `deploy/paths.py` | registry, trees, roots (pure over an `Env`) | `deploy/runner.py:103-293` |
| `deploy/ledger.py` | ledger and status persistence, refusal records | `deploy/runner.py:298-500` |
| `deploy/contract.py` | contract read and validate | `deploy/runner.py:505-748` |
| `deploy/seed.py` | state vault, seeding, member roots | `deploy/runner.py:825-1214` |
| `deploy/units.py` | **the `Units` port**: plist render and bind, `launchctl` bootstrap / bootout / enable / kickstart, `plutil`. **macOS only**, per the Linux ruling | `deploy/runner.py:1304-1833`, `deploy/runner.py:2341-2458` |
| `deploy/cutover.py` | self-cutover, CLI repoint | `deploy/runner.py:1840-2338` |
| `deploy/report.py` | escalate, publish, result brief | `deploy/runner.py:2463-2945` |
| `deploy/pipeline.py` | `_deploy` as phases (resolve → stage → switch → verify-or-rollback), canary, drill | `deploy/runner.py:2973-3959` |
| `deploy/runner.py` | `main`, sweep, status, unattended gate | the rest |

**Invariants:**
- **I-D1.** The two-stage rollback. A smoke failure restores the old checkout (`deploy/runner.py:3396-3421`). A verify failure rolls back, restarts the old version and re-verifies it, or raises `RollbackFailed` with exit 2 (`deploy/runner.py:3482-3528`).
- **I-D2.** `write_ledger` refuses a status without a status tag (`deploy/runner.py:330`).
- **I-D3.** `running_under_unit()` keeps its "empty means not ours" direction (`deploy/runner.py:1959`). It now reads an `Env` built at `main`.

**Constraints:**
- `tests/test_deploy_runner.py:1487` derives env names from a hard-coded `SOURCES` tuple. It must list the new files.
- 95 `patch.object(runner, …)` calls and four `runner.REPO_ROOT` patches follow the same ledger rule as §6.2.

### 7.2 poga_cli.py (1,707 lines)

It imports neither sessionlib nor session. Its seams:
- acquisition and restoration as its existing seven phases (`poga_cli.py:264-871`)
- federation registration (`poga_cli.py:1018-1107`)
- lesson sharing (`poga_cli.py:1115-1236`)
- interview and `init` (`poga_cli.py:1244-1477`)
- parser and main

It already has a `Ctx` (`poga_cli.py:106`), so it adopts `context.Layout`/`Env` rather than a second context type.

---

## 8. Proof the design holds: a checklist for every step

A step's item is not done until each line is shown, with the evidence written into the item:

1. **Imports cleanly.** Every module the step created imports in a fresh interpreter, `python3 -c "import sessionlib.<mod>"`, with HOME pointed at an empty temp dir and the cwd outside any repo. It must not read config, run a subprocess or touch the clock. Checked by a test that imports with `subprocess`, `os.environ` and `Path.home` booby-trapped.
2. **Respects the layers.** The import-boundary test (an AST walk of each module's imports against the §3.1 table) passes. Its negative control is a planted upward import, which must fail it.
3. **No unpatched global path.** `test_no_patch_targets_a_facade` passes, and the live-state tripwire is on for the whole suite.
4. **Explicit dependencies.** The step's modules contain no `os.environ`, `Path.home`, `Path.cwd`, `datetime.now`, `time.time` or `subprocess` outside L5 and `ports/` (grep-checked by the boundary test). No function below L4 accepts `RuntimeContext`.
5. **Compatible.** CLI help and hook output golden tests are byte-identical. Every refusal and degraded path named in the step's invariant list has a test that still passes.
6. **Detection.** The cleanup lane's capability probes report the same capability set on a member before and after the step. **A moved function must not mark the fleet as missing a capability.**
7. **Distribution.** A member built by `bootstrap.py` and refreshed by `push-substrate` from the step's tree runs `session.py start --dry-run`.
8. **Landing (M4, M6, M10 only).** The M0 land floor passes, with all five scenarios: concurrent advancement, conflicts, failed gates, push failures, recovery. Lock order is asserted by test, not by reading.
9. **Coverage kept.** The suite's test count does not fall. Every rewritten test is shown to fail against a mutation of what it guards.
10. **Evidence, not success.** The item's close note quotes the outcome reads (suite result, probe output), not the exit code of the command that produced them.

---

## 9. Findings recorded during mapping (not in this design's scope)

The mapping found these. **They are not fixed by the refactor, and the refactor must not change them silently.** They are recorded here and in the session journal for a review with the operator:

- `atomic_write` uses a fixed `<name>.tmp` path (`sessionlib/config.py:688`). Two concurrent writers of the same record share one temp file.
- `_coord_try_acquire` reclaims an expired, corrupt or "mine" record with a non-exclusive write and **does not re-read** to confirm it won (`sessionlib/coord.py:351`). `_lane_reserve` and `_trunk_lock_reserve` do re-read.
- Dispatch records are read, modified and written with no lock (`_dispatch_write`, `sessionlib/lanes.py:4963`). Two concurrent advancers could lose an update.
- `cmd_release_cut` (`sessionlib/coord.py:2283`) takes no coord lock. It relies on the atomic push. This matches the known release-cut race with concurrent store writes.
- Every lock fails open (I-L8).
- Two trunk moves take no trunk lock (§0.5):
  - the views commit on the main checkout inside the gate (`sessionlib/land.py:4542`);
  - `git_sync`'s fast-forward and push at start (`sessionlib/config.py:873`, `sessionlib/config.py:884`), outside both locks.
- `end` clears the attention record before its authorization refusals (`sessionlib/hooks.py:875`, I-E1). A refused, unauthorized close has already cleared it.

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

## 10. Open questions for the operator (strategy only)

- **Q1.** Fleet roll-out. The redesign changes every member's harness files over 15 steps (M0–M14). Ship them one release per step, or batch them behind a milestone? *Recommendation: one release per step for M1–M7, so a fleet regression points to one subsystem; batch M8–M11 behind one milestone, because the three lifecycle phases and the adapters change together what a session sees.*
- **Q2.** Fail-open locks. Keep the land proceeding unserialized when the store is unavailable (today), or make that a refusal once the store is behind a port? *Recommendation: keep it as is during the redesign and decide it separately. The redesign should not change two things at once.*
- **Q3.** Should `end` record its own progress, so a crash between close and land is visible at the next start? *Recommendation: yes, but as its own item after M9, not inside it.*
- **Q4.** Should a disagreement between the model's prose and the evidence packet refuse the close, or only warn? *Recommendation: warn first and measure how often it fires, then decide.*
