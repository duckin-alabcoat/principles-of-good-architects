"""session.py — trunk check. Part of the harness namespace; loaded by sessionlib.load().

WI-0427 / ADR-0148 D5: THE TRUNK VERIFIES ITSELF AFTER THE MERGE. The land verb stops
running the test suite — a lane is validated in its own tree, and a land is a merge. What
that gives up is the one thing only the merged tree can show: two lanes, each green alone,
that are red together. This part is where that is caught instead. After each land a
BACKGROUND runner checks the sharded suite on the new trunk tip; red marks the trunk red,
the next CODE land refuses with the failing tests named, and the runner proposes reverting
the land that turned it red, bisecting the coalesced range by land boundaries if several
lands arrived during one run.

The stated tradeoff (the brief says operator should know it): the trunk can be red for minutes
between a bad land and the check. Bookkeeping lands never refuse on a red trunk — they
cannot move a test verdict, and blocking them would stall the store behind a code defect.

Do not import this file directly — it is executed into the session namespace by
sessionlib/__init__.py, after `land` (whose gate helpers it reuses) and before `lanes`.
"""

# Per-compilation-unit directive: each part is compiled on its own.
from __future__ import annotations


TRUNK_CHECK_STATE_NAME = "trunk-check.json"
TRUNK_CHECK_LOCK_NAME = "trunk-check.lock"
TRUNK_CHECK_LOG_NAME = "trunk-check.log"
#: The log is evidence for a human reading why the trunk went red, never state; it is
#: trimmed rather than rotated ([`retention-enforced-by-code`]).
TRUNK_CHECK_LOG_KEEP_LINES = 2000
#: Failing identity lines kept in the state ([`cap-what-can-run-away`]).
TRUNK_CHECK_MAX_FAILING = 20
#: Named in a refusal; the rest are counted, never dropped silently.
TRUNK_CHECK_REFUSAL_NAMED = 10
#: A runner that re-reads the tip after every check could in principle chase a trunk that
#: never stops moving. Ten checks is ~12 minutes of suite; past that the runner stops and
#: the next land's spawn picks the newest tip up.
TRUNK_CHECK_MAX_ROUNDS = 10
#: One suite run longer than this is a hung suite, not a slow one (the gate is ~70s).
TRUNK_CHECK_SUITE_TIMEOUT = 1800
#: A lock file whose body cannot be parsed is a writer caught between create and write;
#: it is reclaimable only once it is older than this.
TRUNK_CHECK_UNREADABLE_LOCK_GRACE = 60
TRUNK_CHECK_OFF_ENV = "POGA_TRUNK_CHECK"

#: The spawn goes through this global so a test can stub it and prove the caller never
#: waits. `_TRUNK_CHECK_ALLOW_UNDER_TEST` is the matching opt-in: under a test framework
#: the spawn is a no-op unless a test sets BOTH, so no suite run can ever start a real
#: background runner against the real repo.
_TRUNK_CHECK_POPEN = subprocess.Popen
_TRUNK_CHECK_ALLOW_UNDER_TEST = False


def _trunk_check_stamp() -> str:
    """UTC, and deliberately NOT `_now_iso`: that reads the config's timezone, and the
    runner must be able to write its log from a process whose config is partial."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trunk_check_paths() -> "dict | None":
    """state/lock/log in the git COMMON dir: local to this machine, shared by every lane
    on it — the same home as the land receipts, for the same reason. None where there is
    no common dir, and every caller treats that as "no check has run" (fail-open)."""
    common = _git_common_dir()
    if common is None:
        return None
    return {"state": common / TRUNK_CHECK_STATE_NAME,
            "lock": common / TRUNK_CHECK_LOCK_NAME,
            "log": common / TRUNK_CHECK_LOG_NAME}


def _trunk_check_state() -> dict:
    """The recorded state, or {} when there is none or it cannot be read."""
    paths = _trunk_check_paths()
    if paths is None:
        return {}
    try:
        data = json.loads(paths["state"].read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _trunk_check_write(state: dict) -> None:
    paths = _trunk_check_paths()
    if paths is None:
        return
    try:
        atomic_write(paths["state"], json.dumps(state, indent=2, sort_keys=True) + "\n")
    except Exception:                              # pragma: no cover - evidence, not a gate
        pass


def _trunk_check_log(msg: str, echo: bool = False) -> None:
    """Append one stamped line to the log. Never raises."""
    line = f"{_trunk_check_stamp()} [{os.getpid()}] {msg}"
    if echo:
        print(line)
    paths = _trunk_check_paths()
    if paths is None:
        return
    try:
        with open(paths["log"], "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _trunk_check_log_trim() -> None:
    paths = _trunk_check_paths()
    if paths is None:
        return
    try:
        lines = paths["log"].read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > TRUNK_CHECK_LOG_KEEP_LINES:
            atomic_write(paths["log"],
                         "\n".join(lines[-TRUNK_CHECK_LOG_KEEP_LINES:]) + "\n")
    except Exception:
        pass


def _trunk_check_effective_state(state: dict | None = None) -> str:
    """The state a READER should act on: a "running" record whose runner is provably dead
    on this host reads as "unknown", because a crashed runner must not look like a check
    in progress forever. A runner on ANOTHER host cannot be probed from here and is taken
    at its word (the common dir is per machine, so that is a copied repo, not a race)."""
    state = _trunk_check_state() if state is None else state
    s = state.get("state") or "unknown"
    if s == "running":
        pid, host = state.get("pid"), state.get("host") or ""
        if isinstance(pid, int) and (not host or host == _coord_host()) \
                and not _process_alive(pid):
            return "unknown"
    return s if s in ("green", "red", "running") else "unknown"


# --- the lock ----------------------------------------------------------------------
#
# Exclusive create or lose, the `_land_gate_reserve` shape: losing means another runner
# is live, and that runner re-reads the trunk tip after every check, so it will cover
# whatever this caller came to check — that IS the coalescing. The only reclaim is of a
# lock whose holder is provably dead on this host. The reclaim (re-read, compare, unlink,
# re-create exclusively) has a narrow window in which two reclaimers could both win; the
# cost of that is two concurrent checks writing the same atomic state file, which is
# waste, not corruption, so it is named here rather than engineered away.

def _trunk_check_lock_acquire() -> bool:
    paths = _trunk_check_paths()
    if paths is None:
        return False
    path = paths["lock"]
    payload = json.dumps({"pid": os.getpid(), "host": _coord_host(),
                          "at": time.time()}) + "\n"
    for _attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            return True
        except FileExistsError:
            pass
        except Exception:
            return False
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue                               # released between our two calls
        except Exception:
            return False
        try:
            holder = json.loads(raw)
            pid, host = holder.get("pid"), holder.get("host") or ""
            dead = (isinstance(pid, int) and host == _coord_host()
                    and not _process_alive(pid))
        except Exception:
            try:
                dead = time.time() - path.stat().st_mtime > TRUNK_CHECK_UNREADABLE_LOCK_GRACE
            except Exception:
                dead = False
        if not dead:
            return False
        try:
            if path.read_text(encoding="utf-8") != raw:
                return False                       # someone else reclaimed it first
            path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            return False
    return False


def _trunk_check_lock_release() -> None:
    paths = _trunk_check_paths()
    if paths is None:
        return
    try:
        holder = json.loads(paths["lock"].read_text(encoding="utf-8"))
        if holder.get("pid") == os.getpid() and (holder.get("host") or "") == _coord_host():
            paths["lock"].unlink()
    except Exception:
        pass


# --- running the suite ---------------------------------------------------------------

def _trunk_check_tip(trunk: str) -> str:
    r = sh(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{trunk}^{{commit}}"],
           check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _trunk_check_is_ancestor(a: str, b: str) -> bool:
    """a is an ancestor of (or equal to) b."""
    if not a or not b:
        return False
    return sh(["git", "merge-base", "--is-ancestor", a, b], check=False).returncode == 0


def _trunk_check_suite(sha: str, files: list | None = None) -> tuple:
    """Run the gate's SUITE command — only that one; the `--check` scripts are not what a
    semantic conflict between two lanes breaks — on `sha` in a throwaway detached worktree,
    exactly as `_gate_commit` stages a candidate. Returns (rc, output), rc None when the
    run could not even be staged.

    `files` narrows the run for bisection: one `-p <file>` run per failing module that
    EXISTS at `sha`. A module absent at `sha` is skipped rather than run, because
    `run_suite.py -p` over a pattern matching nothing reports green on zero tests — and
    here "absent" is the true answer anyway: a commit cannot fail a test it does not have,
    which is what makes the land that ADDED a failing test the culprit."""
    cmd = _gate_suite_command()
    if cmd is None:
        return None, "no sharded suite command in the gate list"
    base = _gate_argv(cmd)
    tmp = tempfile.mkdtemp(prefix=GATE_WORKTREE_PREFIX)
    wt = Path(tmp) / "wt"
    env = {**os.environ, GATE_ENV_VAR: "1"}
    try:
        r = sh(["git", "worktree", "add", "--detach", "--quiet", str(wt), sha], check=False)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout).strip()
        runs = [base] if files is None else \
            [base + ["-p", Path(f).name] for f in files if (wt / f).is_file()]
        out, rc = [], 0
        for argv in runs:
            try:
                p = subprocess.run(argv, cwd=wt, stdin=subprocess.DEVNULL, env=env,
                                   text=True, capture_output=True,
                                   timeout=TRUNK_CHECK_SUITE_TIMEOUT)
                out.append((p.stderr or "") + (p.stdout or ""))
                rc = rc or p.returncode
            except subprocess.TimeoutExpired:
                out.append(f"suite timed out after {TRUNK_CHECK_SUITE_TIMEOUT}s")
                rc = rc or 124
        return rc, "\n".join(out)
    except Exception as e:
        return None, f"scratch worktree error: {e}"
    finally:
        sh(["git", "worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def _trunk_check_failing(output: str) -> list:
    return [l.strip() for l in (output or "").splitlines()
            if l.startswith(GATE_IDENTITY_PREFIXES)][:TRUNK_CHECK_MAX_FAILING]


def _trunk_check_module_files(failing: list) -> list:
    """`FAIL: test_x (test_mod.C)` -> `tests/test_mod.py`, deduplicated in order. The
    module is the first dotted part inside the parentheses, which covers both the
    `(mod.Class)` and the 3.11+ `(mod.Class.test)` spellings."""
    out = []
    for line in failing:
        m = re.search(r"\(([^)]+)\)", line)
        if not m:
            continue
        mod = m.group(1).split(".")[0].strip()
        if mod and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", mod):
            f = f"tests/{mod}.py"
            if f not in out:
                out.append(f)
    return out


# --- attributing the red -------------------------------------------------------------

def _trunk_check_lands(trunk: str, since: str, tip: str) -> list:
    """The lands between `since` (exclusive) and `tip` (inclusive), oldest first.

    A land is a contiguous first-parent segment `parent..tip`, and the receipt log
    (`land-receipts.jsonl`) is the record of where each one starts and ends — as 12-char
    prefixes. Matched against the first-parent walk of the range, so a receipt from
    another trunk, an older range or a rewound history simply does not match."""
    walk = sh(["git", "rev-list", "--first-parent", "--reverse", f"{since}..{tip}"],
              check=False)
    if walk.returncode != 0:
        return []
    order = walk.stdout.split()
    index = {sha[:12]: i for i, sha in enumerate(order)}
    found = {}
    path = _land_receipts_path()
    for rec in (land_receipts(path=path) if path is not None else []):
        if rec.get("outcome") != "landed" or rec.get("trunk") not in (trunk, None):
            continue
        t = (rec.get("tip") or "")[:12]
        if t in index:
            found[index[t]] = {"parent": (rec.get("parent") or "")[:12], "tip": t,
                               "sha": order[index[t]], "lane": rec.get("lane") or ""}
    return [found[i] for i in sorted(found)]


def _trunk_check_subject(sha: str) -> str:
    r = sh(["git", "log", "-1", "--format=%s", sha], check=False)
    return r.stdout.strip() if r.returncode == 0 else ""


def _trunk_check_culprit(trunk: str, tip: str, last_green: str, failing: list,
                         say=lambda _m: None) -> dict | None:
    """The land that turned the trunk red. One land in the range: that land. Several:
    bisect by land boundaries, running only the failing tests' modules at each candidate
    land's tip (the full suite when no test was named). No green baseline or no receipt
    covering the range: the newest land, marked unbisected — a guess stated as one."""
    def pack(land, how):
        return {"parent": land["parent"], "tip": land["tip"], "lane": land["lane"],
                "subject": _trunk_check_subject(land["sha"]), "found_by": how}

    lands = []
    if last_green and _trunk_check_is_ancestor(last_green, tip):
        lands = _trunk_check_lands(trunk, last_green, tip)
    if not lands:
        path = _land_receipts_path()
        # Only a land the checked tip CONTAINS can have broken it. The first real run
        # blamed a land that arrived while the check was running — after the red tip.
        recs = [r for r in (land_receipts(path=path) if path is not None else [])
                if r.get("outcome") == "landed" and r.get("trunk") in (trunk, None)
                and r.get("tip") and _trunk_check_is_ancestor(r["tip"], tip)]
        if not recs:
            return None
        r = recs[-1]
        full = sh(["git", "rev-parse", "--verify", "--quiet",
                   f"{r.get('tip', '')}^{{commit}}"], check=False).stdout.strip()
        return pack({"parent": (r.get("parent") or "")[:12], "tip": (r.get("tip") or "")[:12],
                     "sha": full or r.get("tip", ""), "lane": r.get("lane") or ""},
                    "unbisected")
    if len(lands) == 1:
        return pack(lands[0], "only land in range")
    files = _trunk_check_module_files(failing) or None

    def red(land) -> bool:
        rc, _ = _trunk_check_suite(land["sha"], files)
        say(f"bisect: {land['tip']} (lane {land['lane'] or '?'}) -> "
            f"{'RED' if rc else 'green' if rc == 0 else 'could not run'}")
        return rc != 0          # a candidate that could not be staged counts as red:
                                # blaming too early is corrected by the revert's own run

    lo, hi = 0, len(lands) - 1
    if lands[-1]["sha"] != tip and not red(lands[-1]):
        # Every land tip is green and the red arrived after the last land — a direct
        # commit to the trunk (a store write), which no land owns.
        return {"parent": lands[-1]["tip"], "tip": tip[:12], "lane": "",
                "subject": _trunk_check_subject(tip), "found_by": "after the last land"}
    while lo < hi:
        mid = (lo + hi) // 2
        if red(lands[mid]):
            hi = mid
        else:
            lo = mid + 1
    return pack(lands[lo], "bisected")


def _trunk_check_proposal(culprit: dict | None) -> str:
    """What a SESSION should do about a red trunk — not the user (Git never reaches
    operator). The revert is a land like any other: built in a lane, verified there."""
    if not culprit:
        return ("the trunk is red and no land could be named: find the breaking commit "
                "with the failing tests above, fix or revert it in a lane, run the suite, "
                "land it.")
    rng = f"{culprit['parent']}..{culprit['tip']}"
    who = f"lane {culprit['lane']}" if culprit.get("lane") else "a direct trunk commit"
    return (f"revert the land from {who} ({rng}): in a lane, "
            f"`git revert --no-edit {rng}`, run the suite, land it.")


# --- the runner ----------------------------------------------------------------------

def _trunk_check_run(trunk: str | None = None, echo: bool = False) -> dict:
    """Check the trunk until the newest tip has a verdict. Returns the final state.

    Takes the lock or returns at once: a live runner will reach this caller's tip,
    because it re-reads the trunk after every check and only ever checks the NEWEST tip.
    After releasing it reads the tip once more and goes round again if a land arrived in
    the gap between its last read and the release — otherwise that land's own spawn,
    which lost the lock race, would be the one land nobody checks."""
    trunk = trunk or _trunk()
    say = lambda m: _trunk_check_log(m, echo=echo)
    rounds = 0
    try:
        while rounds < TRUNK_CHECK_MAX_ROUNDS:
            if not _trunk_check_lock_acquire():
                say("another trunk check holds the lock — it will cover the newest tip")
                return _trunk_check_state()
            try:
                while rounds < TRUNK_CHECK_MAX_ROUNDS:
                    tip = _trunk_check_tip(trunk)
                    state = _trunk_check_state()
                    if not tip:
                        say(f"no local {trunk} ref — nothing to check")
                        return state
                    if state.get("tip") == tip and (
                            state.get("state") in ("green", "red")
                            # a check THIS runner could not stage is not retried on
                            # the same tip: it would fail the same way ten times.
                            or (state.get("state") == "unknown"
                                and state.get("pid") == os.getpid())):
                        break
                    rounds += 1
                    _trunk_check_one(trunk, tip, state, say)
            finally:
                _trunk_check_lock_release()
            final = _trunk_check_state()
            if _trunk_check_tip(trunk) == final.get("tip") or not final.get("tip"):
                return final
        say(f"stopped after {TRUNK_CHECK_MAX_ROUNDS} checks; the next land re-spawns")
        return _trunk_check_state()
    finally:
        _trunk_check_log_trim()


def _trunk_red_record(state: dict) -> dict | None:
    """The red a reader must still honour: the state's own when it IS red, else the one
    carried forward from an earlier check. Found live on the first real run (WI-0427):
    a runner that went red and moved straight on to a newer tip wrote "running", and the
    refusal read "running" as nothing to refuse — the red lasted zero seconds."""
    if state.get("state") == "red":
        return {k: state.get(k) for k in ("tip", "failing", "culprit", "proposal",
                                          "checked_at")}
    red = state.get("red")
    return red if isinstance(red, dict) and red.get("tip") else None


def _trunk_check_one(trunk: str, tip: str, prior: dict, say) -> dict:
    last_green = prior.get("last_green_tip") or ""
    # A red stays red until a check comes back GREEN — not until a check merely starts.
    carried = _trunk_red_record(prior)
    base = {"trunk": trunk, "tip": tip, "last_green_tip": last_green,
            "pid": os.getpid(), "host": _coord_host(), "red": carried}
    _trunk_check_write({**base, "state": "running", "checked_at": time.time(),
                        "seconds": None, "failing": [], "culprit": None, "proposal": ""})
    say(f"checking {trunk} at {tip[:12]}")
    started = time.monotonic()
    rc, output = _trunk_check_suite(tip)
    seconds = round(time.monotonic() - started, 1)
    if rc is None:
        state = {**base, "state": "unknown", "checked_at": time.time(), "seconds": seconds,
                 "failing": [], "culprit": None,
                 "proposal": f"the check could not run: {output[:300]}"}
        say(f"could not run at {tip[:12]}: {output[:300]}")
    elif rc == 0:
        state = {**base, "state": "green", "checked_at": time.time(), "seconds": seconds,
                 "last_green_tip": tip, "failing": [], "culprit": None, "proposal": "",
                 "red": None}
        say(f"GREEN at {tip[:12]} in {seconds}s")
    else:
        failing = _trunk_check_failing(output)
        say(f"RED at {tip[:12]} in {seconds}s — {len(failing)} failing named")
        for line in failing:
            say(f"  {line}")
        # A red carried from an earlier tip keeps ITS culprit: the land that first turned
        # the trunk red is still the one to revert, not whatever landed while it stayed red.
        if carried and carried.get("culprit") and _trunk_check_is_ancestor(
                carried.get("tip") or "", tip):
            culprit = carried["culprit"]
        else:
            culprit = _trunk_check_culprit(trunk, tip, last_green, failing, say)
        state = {**base, "state": "red", "checked_at": time.time(), "seconds": seconds,
                 "failing": failing, "culprit": culprit,
                 "proposal": _trunk_check_proposal(culprit)}
        state["red"] = _trunk_red_record(state)
        say(f"culprit: {json.dumps(culprit)}; proposal: {state['proposal']}")
    _trunk_check_write(state)
    return state


def _trunk_check_spawn(trunk: str | None = None, tip: str | None = None) -> dict:
    """Start the runner DETACHED — its own session, stdin closed, output to the log — so
    a land never waits on the suite it no longer runs (ADR-0148 D5). Never raises.

    NOT UNDER TEST, NOT INSIDE THE GATE, NOT WHEN SWITCHED OFF. A suite run that lands in
    a fixture must not start a real runner against the real main checkout, and a runner's
    own suite (POGA_GATE=1) that exercises a land must not start another runner. The
    runner is started from the MAIN checkout's `session.py`, because the state it writes
    is per machine and the lane that landed is about to be retired."""
    try:
        if _under_test() and not _TRUNK_CHECK_ALLOW_UNDER_TEST:
            return {"spawned": False, "reason": "under test"}
        if os.environ.get(GATE_ENV_VAR) == "1":
            return {"spawned": False, "reason": f"inside the gate ({GATE_ENV_VAR}=1)"}
        if os.environ.get(TRUNK_CHECK_OFF_ENV) == "off":
            return {"spawned": False, "reason": f"{TRUNK_CHECK_OFF_ENV}=off"}
        paths = _trunk_check_paths()
        if paths is None:
            return {"spawned": False, "reason": "no git common dir"}
        main = _shared_work_root()
        entry = main / "session.py"
        if not entry.is_file():
            return {"spawned": False, "reason": f"no session.py in {main}"}
        with open(paths["log"], "a", encoding="utf-8") as log:
            log.write(f"{_trunk_check_stamp()} spawn for {trunk or _trunk()} at "
                      f"{(tip or '?')[:12]}\n")
            log.flush()
            proc = _TRUNK_CHECK_POPEN(
                [sys.executable, str(entry), "trunk-check", "--run"],
                cwd=str(main), start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log)
        return {"spawned": True, "pid": getattr(proc, "pid", None)}
    except Exception as e:
        return {"spawned": False, "reason": f"spawn failed: {e}"}


# --- readers -------------------------------------------------------------------------

def _trunk_check_refusal(code_change: bool, lane_head: str | None = None) -> str:
    """"" when the land may proceed; otherwise the refusal text.

    Refuses ONLY a code land onto a trunk whose recorded red tip is still in its history.
    Not a bookkeeping land (it cannot move a verdict). Not when the red tip is off the
    trunk's history (the record is about some other line). Not a lane built ON the red
    tip — that lane ran the suite on a tree containing the breakage and came out green,
    so it may well be the fix, and refusing it would leave the trunk red with its repair
    queued behind the refusal. A dead runner's "running" is unknown, and unknown never
    refuses: the check is a net under the land, not a second gate."""
    if not code_change:
        return ""
    full = _trunk_check_state()
    state = _trunk_red_record(full)
    if not state:
        return ""
    red_tip = state.get("tip") or ""
    trunk = full.get("trunk") or _trunk()
    if trunk != _trunk():
        return ""
    current = _trunk_check_tip(trunk)
    if not _trunk_check_is_ancestor(red_tip, current):
        return ""
    if lane_head and _trunk_check_is_ancestor(red_tip, lane_head):
        return ""
    failing = state.get("failing") or []
    named = failing[:TRUNK_CHECK_REFUSAL_NAMED]
    lines = [f"trunk check: {trunk} is RED at {red_tip[:12]} — this code land is refused "
             f"until it is green (ADR-0148 D5). Bookkeeping lands still go through."]
    lines += [f"  {l}" for l in named]
    if len(failing) > len(named):
        lines.append(f"  … and {len(failing) - len(named)} more")
    if not failing:
        lines.append("  (the suite failed without naming a test — see "
                     f"{TRUNK_CHECK_LOG_NAME} in the git common dir)")
    c = state.get("culprit")
    if c:
        lines.append(f"  culprit: lane {c.get('lane') or '?'} "
                     f"({c.get('parent')}..{c.get('tip')}) {c.get('subject', '')} "
                     f"[{c.get('found_by', '')}]")
    if state.get("proposal"):
        lines.append(f"  proposal: {state['proposal']}")
    lines.append("  A lane rebased onto the red trunk and green on its own tree may land "
                 "(it may be the fix).")
    return "\n".join(lines)


def _trunk_check_age(at) -> str:
    try:
        s = max(0, int(time.time() - float(at)))
    except Exception:
        return "?"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h"


def trunk_check_line() -> str:
    state = _trunk_check_state()
    eff = _trunk_check_effective_state(state)
    tip = (state.get("tip") or "")[:12]
    when = f"checked {_trunk_check_age(state.get('checked_at'))} ago"
    if eff == "green":
        return f"trunk check: GREEN at {tip} ({when}, {state.get('seconds')}s)"
    if eff == "red":
        c = state.get("culprit") or {}
        who = f", culprit lane {c.get('lane')}" if c.get("lane") else ""
        return (f"trunk check: RED at {tip} — {len(state.get('failing') or [])} failing"
                f"{who} ({when})")
    red = _trunk_red_record(state)
    if eff == "running":
        return (f"trunk check: RUNNING on {tip} (started "
                f"{_trunk_check_age(state.get('checked_at'))} ago, pid {state.get('pid')})"
                + (f" — still RED from {str(red.get('tip'))[:12]}, "
                   f"{len(red.get('failing') or [])} failing; code lands refused"
                   if red else ""))
    if state:
        return f"trunk check: UNKNOWN at {tip or '?'} — {state.get('proposal') or 'the last runner died'}"
    return "trunk check: UNKNOWN — no check has run"


def cmd_trunk_check(args) -> None:
    """`session.py trunk-check [--run | --clear]` (WI-0427 / ADR-0148 D5)."""
    if getattr(args, "clear", False):
        paths = _trunk_check_paths()
        if paths is not None:
            try:
                paths["state"].unlink()
            except FileNotFoundError:
                pass
        print("trunk check: state cleared")
        return
    if getattr(args, "run", False):
        _trunk_check_run(_trunk(), echo=True)
    print(trunk_check_line())
    state = _trunk_check_state()
    if state:
        print(json.dumps(state, indent=2, sort_keys=True))
