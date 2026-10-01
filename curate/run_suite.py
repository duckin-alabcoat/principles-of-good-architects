#!/usr/bin/env python3
"""Run the gate's test suite sharded across cores, with unittest's own verdict.

WI-0296. The land gate's slow part is one thing: `python3 -m unittest discover -s
tests` on a single core, ~3 minutes on one machine while every other core idles. Lands
are serialized one at a time ([ADR-0114](../adr/0114-the-land-gate-is-serialized-one-at-a-time.md)),
so that cost is paid end-to-end by every close-out in the queue behind it.

WHAT THIS IS NOT. It is not a different suite, a subset, a reordering that drops
anything, or a new verdict scheme. Every test that `discover -s tests` runs, this
runs — the shard plan is a partition of the same file list, and the aggregate is
printed in unittest's exact summary shape so the two runs are indistinguishable to
anything downstream. `session._test_verdict` parses `^Ran N tests in` and the line
after it; the exit code is `0` on success and `1` otherwise, the same two values
`unittest.main` produces. A speedup that changed the verdict would be a regression
wearing a speedup's clothes.

HOW THE WORK IS SPLIT — by FILE first. `tests/` is flat: no packages, no
`__init__.py`, no `setUpModule`. So one file is a closed unit, and
`loader.discover(start_dir=tests, pattern="test_foo.py")` loads it under exactly the
module name (`test_foo`, not `tests.test_foo`) that a full discover gives it —
identical import semantics, identical `_FailedTest` synthesis when a module will not
import.

...AND BY CLASS, for the files big enough to be the critical path on their own. File
granularity alone does not finish the job, and the first measurement said so plainly:
sharded by file across the available workers the suite went 289.9s -> 95.4s, and
`test_worktree_lane.py` took 95.3s of it. Every other core waited on one file. So a file
above `SPLIT_BYTES` is cut into parts, each part a whole number of TEST CLASSES —
never a class split down the middle, because a class that shares `setUpClass` state
across its methods would then be running half a fixture in each of two processes.
The parts are packed greedily by test count, longest-first, and the packing is a pure
function of the discovered class list, so every worker for a given file computes the
same partition without anyone having to communicate it.

The union of a file's parts is the file. That is the property everything else rests
on, and it is structural rather than asserted: the parts are bins over one discovery
of one file, so a test cannot land in two of them and cannot land in none.

WHY SIZE, AND NOT A TIMINGS CACHE. Which module to start early, and which to cut up,
both want duration — which we do not have. File size is a crude proxy (`test_session.py`
is the second-largest file and nowhere near the second-slowest), but it is free,
deterministic, and present in a fresh scratch worktree, which a timings cache is not:
the gate builds a new worktree per land, so a gitignored cache would arrive empty
exactly where it was needed, and a tracked one would rewrite itself on every run.
Over-splitting a file that turns out to be fast costs a few interpreter startups;
under-splitting the slow one costs the whole speedup.

HOW THE SHARDS ARE SCHEDULED. A worker pool of `-j` processes pulls from one queue,
so a slow shard does not idle the other cores — dynamic scheduling needs no timing
data to balance. The queue is ordered largest-file-first so the long poles start
while there is still room behind them.

WHY THE PARENT DOES NOT RE-DISCOVER. The file list comes from a glob, not from
importing all ninety modules in the parent and handing out test ids. Importing them
costs about what one worker costs, buys nothing (the workers import anyway), and
would put a second copy of unittest's discovery rules in this file. The glob applies
the same rule discover does — `fnmatch(name, pattern)` over `tests/*.py` — and
nothing else.

FAIL-CLOSED, in the two places it can go wrong. A worker that dies without writing
its result (segfault, OOM, `sys.exit` from inside a test) is recorded as an ERROR
against that shard, with its stderr tail, so the run FAILS rather than quietly
reporting on the shards that survived. And the aggregate `Ran N` is the sum of what
the workers actually ran, never the count that was planned — `--expect N` turns the
difference between those two numbers into a failure, which is how a run that lost a
whole shard is caught rather than read as green.

Usage:
    python3 curate/run_suite.py                 # sharded across every core
    python3 curate/run_suite.py -j 4            # four workers
    python3 curate/run_suite.py --timings       # per-shard wall time, slowest first
    python3 curate/run_suite.py --expect 3147   # fail unless exactly N tests ran
    python3 curate/run_suite.py -p 'test_lane*.py'
"""

from __future__ import annotations

import os as _os
import sys as _sys

# WI-0328 — the interpreter pin, before this module does anything. The gate spawns
# this file with `sys.executable`, so under a land it already inherits the right
# answer; run BY HAND it inherited the operator's PATH instead, and on one machine
# those two resolve to two different Python installs at two different versions,
# which disagree about the suite's verdict. Appended to `sys.path`, never prepended:
# this is a lookup for one module, not a claim to shadow anything already importable.
_REPO = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO not in _sys.path:
    _sys.path.append(_REPO)
try:
    import interpreter as _interpreter
except Exception:  # a member without the module runs exactly as it did before
    _interpreter = None
else:
    # ONLY when this file IS the program. Re-execing a process that merely imported
    # us would replace somebody else's program with ours — and it did: `import
    # session` from a test module restarted the whole test runner under the pin.
    if __name__ == "__main__":
        _interpreter.ensure(_REPO)

import poga_evidence as evidence  # noqa: E402  (repo root is on the path, just above)

_sys.path.append(_os.path.dirname(_os.path.abspath(__file__)))
import store_guard  # noqa: E402  (curate/, just above; ADR-0148 D3)

import argparse
import fnmatch
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

#: Env override for the worker count. Explicit beats derived: a caller that knows the
#: machine's budget (a dispatcher spawning N lanes) can just say so.
JOBS_ENV = "POGA_SUITE_JOBS"


def _concurrent_suite_parents() -> int:
    """How many suite PARENTS are running on this machine, including this one.

    WHY THE PROCESS TABLE AND NOT THE COORDINATION STORE. The store answers "which
    lanes hold a claim", which is not the same population: a lane holds a claim only
    while it owns an item, and a lane can run the suite without one. What oversubscribes
    the cores is concurrent *suite* runs, so that is what gets counted — the resource is
    the thing to measure, not a proxy for it.

    `--worker` invocations are excluded deliberately: those are this pool's own children,
    and counting them would divide the width by the width.

    RETURNS 1 WHEN IT CANNOT TELL, which reproduces the historical full-width default.
    The other direction — throttling to one core because a probe failed — would turn an
    unreadable process table into a 5x slower suite with no error to explain it."""
    try:
        out = subprocess.run(["ps", "-Ao", "args="],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return 1
    if out.returncode != 0:
        return 1
    n = sum(1 for line in out.stdout.splitlines()
            if "run_suite.py" in line and "--worker" not in line)
    return max(1, n)


def default_jobs() -> int:
    """Cores, divided by the suites already running.

    MEASURED: several dispatched lanes each defaulting to `os.cpu_count()` drove
    the box to a load far above its core count. The cost is not just slowness — FL7 asserts a 0.6s land-lock hold
    and goes red under that load, which reads as a code conflict and refused an integrate
    that had nothing wrong with it. A default that is correct alone and ruinous in
    parallel is a defect of the default, not of the callers.
    """
    env = os.environ.get(JOBS_ENV, "").strip()
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass   # an unparseable override is ignored, never a reason to oversubscribe
    cores = os.cpu_count() or 1
    return max(1, cores // _concurrent_suite_parents())

#: unittest's own default discovery pattern. Kept here so the glob in `_shards` and
#: the per-worker `discover` pattern cannot drift from each other.
DEFAULT_PATTERN = "test*.py"

#: A test file gets one part per this many bytes. Tuned against the first sharded
#: measurement, where the 203 KB `test_worktree_lane.py` was the entire 95s wall and
#: everything else fitted underneath it: at 24 KB that file becomes eight parts, and
#: the next-slowest file becomes the critical path instead of a single module.
SPLIT_BYTES = 24_000

#: Ceiling on parts per file. A file cut past this point is paying more interpreter
#: startups than it saves, and the packing has run out of classes to distribute.
MAX_PARTS = 10

#: unittest.TextTestResult's rule separators, reproduced so the aggregated failure
#: report is byte-comparable with a serial run's.
SEP1 = "=" * 70
SEP2 = "-" * 70


# ── the worker half ───────────────────────────────────────────────────────────────
# Runs ONE module in its own interpreter and writes a structured result. It captures
# the runner's own stream into a throwaway buffer rather than letting it out: the
# parent re-prints the failures itself from this data, so a shard's partial `Ran N
# tests / OK` never reaches the real stderr where `_test_verdict` would read the last
# one it finds as the verdict for the whole run.


def _describe(test: unittest.TestCase) -> str:
    """`TextTestResult.getDescription` for the default `descriptions=True` runner —
    the exact string that follows `FAIL: ` / `ERROR: ` in a serial run's report."""
    first_line = test.shortDescription()
    return "\n".join((str(test), first_line)) if first_line else str(test)


def _flatten(suite) -> list:
    """Every test case in `suite`, in discovery order."""
    out = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _class_groups(tests: list) -> list:
    """`tests` grouped by owning class, groups in first-seen (discovery) order.

    The group, not the test, is the unit of splitting: a class whose `setUpClass`
    builds a fixture its methods share must not have those methods running in two
    processes against two different fixtures."""
    groups: dict = {}
    for t in tests:
        cls = type(t)
        key = "%s.%s" % (cls.__module__, cls.__qualname__)
        groups.setdefault(key, []).append(t)
    return list(groups.values())


def _bin_for_part(groups: list, part: int, parts: int) -> list:
    """The classes assigned to `part` of `parts`, packed greedily by test count.

    Pure function of `groups`, so each of a file's workers derives the identical
    partition from its own discovery — no plan has to be shipped between them. Ties
    break on the group's discovery position, which `sorted` preserves, so the result
    is stable rather than merely correct."""
    if parts <= 1:
        return groups
    order = sorted(range(len(groups)), key=lambda i: (-len(groups[i]), i))
    load = [0] * parts
    mine: list = []
    for i in order:
        bin_ = min(range(parts), key=lambda b: (load[b], b))
        load[bin_] += len(groups[i])
        if bin_ == part:
            mine.append(i)
    return [groups[i] for i in sorted(mine)]


def _worker(out_path: str, module_file: str, part: int, parts: int) -> int:
    """Run one shard — a whole test file, or `part` of `parts` of one. Writes the
    result as JSON to `out_path`. Always exits 0 on a result that was WRITTEN — a red
    shard is data, not a worker failure. A nonzero exit here means the worker itself
    could not produce a result."""
    sys.path.insert(0, str(TESTS))
    # ADR-0148 D3 (WI-0427): armed BEFORE discovery, so a read at module import is
    # seen too. See curate/store_guard.py for what it covers and what it cannot.
    guard = None
    guard_mode = store_guard.mode()
    if guard_mode != "off":
        guard = store_guard.Guard(ROOT, guard_mode)
        guard.install(pathlib.Path(out_path + ".children"))
    buf = _NullStream()
    loader = unittest.TestLoader()
    # An exact filename is still just an fnmatch pattern, so this selects one file and
    # loads it exactly as a full discover would — including synthesising a `_FailedTest`
    # when the module raises on import, which is what keeps an unimportable module a
    # counted error instead of a silently missing shard.
    discovered = loader.discover(start_dir=str(TESTS), pattern=module_file)
    groups = _bin_for_part(_class_groups(_flatten(discovered)), part, parts)
    suite = unittest.TestSuite()
    for group in groups:
        suite.addTests(group)
    runner_kw = {}
    if guard is not None:
        runner_kw["resultclass"] = store_guard.result_class(guard, unittest.TextTestResult)
    res = unittest.TextTestRunner(stream=buf, verbosity=0, **runner_kw).run(suite)
    # Reads after the last test — `tearDownClass`, `tearDownModule` — belong to no test.
    # In enforce mode they fail the shard by name rather than vanishing.
    trailing = guard.take(None) if guard is not None else []
    store_reads = dict(store_guard.REPORTED)
    if trailing:
        store_reads["<%s: outside any test>" % module_file] = trailing
    payload = {
        "module": module_file,
        "part": part,
        "parts": parts,
        "testsRun": res.testsRun,
        "failures": [[_describe(t), tb] for t, tb in res.failures],
        "errors": [[_describe(t), tb] for t, tb in res.errors],
        "skipped": [[_describe(t), r] for t, r in res.skipped],
        "expectedFailures": [[_describe(t), tb] for t, tb in res.expectedFailures],
        "unexpectedSuccesses": [_describe(t) for t in res.unexpectedSuccesses],
        "store_reads": {k: sorted(v) for k, v in store_reads.items()},
        "store_sites": dict(store_guard.SITES),
    }
    if trailing and guard_mode == "enforce":
        payload["errors"].append(["store guard: %s (after its last test)" % module_file,
                                  store_guard.violation_text(trailing)])
    pathlib.Path(out_path).write_text(json.dumps(payload), encoding="utf-8")
    return 0


class _NullStream:
    """The runner writes its own summary somewhere; this is nowhere. Implements only
    what `TextTestRunner` and `TextTestResult` touch."""

    def write(self, _s: str) -> int:
        return 0

    def writeln(self, _s: str = "") -> int:
        return 0

    def flush(self) -> None:
        pass


# ── the parent half ───────────────────────────────────────────────────────────────


def _shards(pattern: str) -> list[tuple]:
    """The shards to run as `(file, part, parts)`, long poles first.

    `tests/` is flat, so the file list is the whole suite; the parts of a file are a
    partition of that file (see `_bin_for_part`). Together: every test in exactly one
    shard, which is the invariant that lets this stand in for a serial discover."""
    names = [p.name for p in TESTS.glob("*.py") if fnmatch.fnmatch(p.name, pattern)]
    names.sort(key=lambda n: (-(TESTS / n).stat().st_size, n))
    plan = []
    for name in names:
        size = (TESTS / name).stat().st_size
        parts = max(1, min(MAX_PARTS, round(size / SPLIT_BYTES)))
        plan.extend((name, k, parts) for k in range(parts))
    return plan


def _shard_label(module_file: str, part: int, parts: int) -> str:
    return module_file if parts == 1 else "%s[%d/%d]" % (module_file, part + 1, parts)


def _run_shard(shard: tuple, tmp: pathlib.Path) -> dict:
    """Spawn one worker and return its result, or a fail-closed stand-in."""
    module_file, part, parts = shard
    label = _shard_label(module_file, part, parts)
    out = tmp / ("%s.%d.json" % (module_file, part))
    argv = [sys.executable, str(pathlib.Path(__file__).resolve()),
            "--worker", str(out), module_file, str(part), str(parts)]
    t0 = time.monotonic()
    # stdin is CLOSED, for the reason `_run_gate` closes it (WI-0154): a test that
    # reaches `_read_hook_stdin` waits on an EOF a harness socket never sends, and a
    # gate that hangs is worse than one that fails.
    proc = subprocess.run(argv, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    elapsed = time.monotonic() - t0
    try:
        rec = json.loads(out.read_text(encoding="utf-8"))
    except Exception as e:
        tail = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace")
        # The whole text survives on rec["stdout"]/rec["stderr"] below and is
        # replayed to the real streams by main(); this window is the SUMMARY that
        # rides the error, so it has to say it is one.
        tail = evidence.clip(tail.strip(), 20, keep="tail", unit="lines")
        rec = _lost_shard(label, proc.returncode, e, tail)
    rec["label"] = label
    rec["elapsed_s"] = elapsed
    rec["stdout"] = proc.stdout.decode("utf-8", "replace")
    rec["stderr"] = proc.stderr.decode("utf-8", "replace")
    return rec


def _lost_shard(module_file: str, returncode: int, err: Exception, tail: str) -> dict:
    """A shard whose worker produced no result. Counted as an ERROR so the run fails —
    the alternative is reporting green on the shards that happened to survive, which is
    the `declare-what-a-check-assumes` failure this whole file is careful about."""
    return {
        "module": module_file,
        "testsRun": 0,
        "failures": [],
        "errors": [[
            f"{module_file} (shard worker)",
            f"The worker for this shard exited {returncode} without writing a result "
            f"({type(err).__name__}: {err}). No test in this file is known to have "
            f"passed. Worker output tail:\n{tail}\n",
        ]],
        "skipped": [],
        "expectedFailures": [],
        "unexpectedSuccesses": [],
    }


def _print_error_list(flavour: str, entries: list, stream) -> None:
    """`TextTestResult.printErrorList`, verbatim in shape."""
    for desc, tb in entries:
        stream.write(SEP1 + "\n")
        stream.write("%s: %s\n" % (flavour, desc))
        stream.write(SEP2 + "\n")
        stream.write("%s\n" % tb)


def _summarize(results: list[dict], elapsed: float, stream) -> bool:
    """Print unittest's summary for the union of the shards. Returns success.

    Shape and wording are `TextTestRunner.run`'s, deliberately — `_test_verdict`
    anchors on `^Ran N tests in`, classifies the line after it as `OK(...)` or
    `FAILED (...)`, and calls anything else "did not report". Getting this block
    wrong would not produce a wrong verdict; it would produce no verdict, which is
    the honest failure but still a broken gate."""
    ran = sum(r["testsRun"] for r in results)
    failures, errors, skipped = [], [], []
    expected_fails, unexpected = [], []
    for r in sorted(results, key=lambda r: (r["module"], r.get("part", 0))):
        failures += r["failures"]
        errors += r["errors"]
        skipped += r["skipped"]
        expected_fails += r["expectedFailures"]
        unexpected += r["unexpectedSuccesses"]

    _print_error_list("ERROR", errors, stream)
    _print_error_list("FAIL", failures, stream)
    # `TextTestResult.printErrors` names each unexpected success after the two error
    # lists, under ONE rule and with no trailing separator per entry — not the
    # `_print_error_list` shape, because there is no traceback to print. Omitting the
    # block still produced the right verdict (`FAILED (unexpected successes=N)`) but
    # never said WHICH test, which is the same "the receipt lost the identity" defect
    # the gate already had for failure output.
    if unexpected:
        stream.write(SEP1 + "\n")
        for desc in unexpected:
            stream.write("UNEXPECTED SUCCESS: %s\n" % desc)
    stream.write(SEP2 + "\n")
    stream.write("Ran %d test%s in %.3fs\n" % (ran, ran != 1 and "s" or "", elapsed))
    stream.write("\n")

    ok = not failures and not errors and not unexpected
    infos = []
    if not ok:
        stream.write("FAILED")
        if failures:
            infos.append("failures=%d" % len(failures))
        if errors:
            infos.append("errors=%d" % len(errors))
    else:
        stream.write("OK")
    if skipped:
        infos.append("skipped=%d" % len(skipped))
    if expected_fails:
        infos.append("expected failures=%d" % len(expected_fails))
    if unexpected:
        infos.append("unexpected successes=%d" % len(unexpected))
    stream.write(" (%s)\n" % (", ".join(infos),) if infos else "\n")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the gate's test suite sharded across cores, with unittest's "
                    "own verdict and exit code.")
    ap.add_argument("--worker", metavar="OUT",
                    help=argparse.SUPPRESS)  # internal: run one shard, write JSON
    ap.add_argument("-j", "--jobs", type=int, default=None,
                    help="Worker processes (default: cores divided by the suites already "
                         f"running, so concurrent lanes share the machine; ${JOBS_ENV} "
                         "overrides).")
    ap.add_argument("-p", "--pattern", default=DEFAULT_PATTERN,
                    help="Discovery pattern, as `unittest discover -p` (default test*.py).")
    ap.add_argument("--timings", action="store_true",
                    help="Print per-shard wall time, slowest first, before the verdict.")
    ap.add_argument("--expect", type=int, metavar="N",
                    help="Fail unless exactly N tests ran — the guard against a run "
                         "that silently lost a shard.")
    args, rest = ap.parse_known_args(argv)

    if args.worker:
        # `--worker OUT` consumed the path; the rest is `<file> <part> <parts>`.
        return _worker(args.worker, rest[0], int(rest[1]), int(rest[2]))

    shards = _shards(args.pattern)
    if not shards:
        sys.stderr.write(SEP2 + "\nRan 0 tests in 0.000s\n\nOK\n")
        return 0

    # Resolved HERE, not at parse time: the count of concurrent suites is a fact about
    # the moment the run starts, and an argparse default is evaluated at import.
    requested = args.jobs if args.jobs is not None else default_jobs()
    jobs = max(1, min(requested, len(shards)))
    files = len({s[0] for s in shards})
    cores = os.cpu_count() or 1
    # Say when the width was REDUCED and why. A suite that silently ran at a fifth of the
    # machine looks like a suite that got slower, and the next reader measures the wrong
    # thing.
    shared = "" if (args.jobs is not None or jobs >= cores) else \
        " (shared: %d core(s), %d suite(s) running)" % (cores, _concurrent_suite_parents())
    print("suite:   %d file(s) in %d shard(s) across %d worker(s)%s"
          % (files, len(shards), jobs, shared), flush=True)

    t0 = time.monotonic()
    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="fed-suite-") as td:
        tmp = pathlib.Path(td)
        # A thread per worker slot: the threads only wait on subprocesses, so the GIL
        # is irrelevant and the pool gives dynamic scheduling for free — the next free
        # slot takes the next shard, rather than each slot owning a fixed list.
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(_run_shard, s, tmp) for s in shards]
            for fut in as_completed(futures):
                rec = fut.result()
                results.append(rec)
                mark = "F" if (rec["failures"] or rec["errors"]) else "."
                print("%s %-48s %6.1fs %4d test(s)"
                      % (mark, rec["label"], rec["elapsed_s"], rec["testsRun"]),
                      flush=True)
    elapsed = time.monotonic() - t0

    if args.timings:
        print("\nslowest shards:")
        for rec in sorted(results, key=lambda r: -r["elapsed_s"])[:15]:
            print("  %6.1fs  %s" % (rec["elapsed_s"], rec["label"]))
        print("  %6.1fs  TOTAL WALL (sum of shards: %.1fs)"
              % (elapsed, sum(r["elapsed_s"] for r in results)))

    # Whatever the tests themselves wrote goes out before the summary, so the LAST
    # `Ran N tests` line on stderr is unambiguously this run's own.
    for rec in sorted(results, key=lambda r: (r["module"], r.get("part", 0))):
        if rec.get("stdout"):
            sys.stdout.write(rec["stdout"])
        if rec.get("stderr"):
            sys.stderr.write(rec["stderr"])
    sys.stdout.flush()

    # Report mode (ADR-0148 D3): the live reads, per test, to a file the operator names.
    report_to = os.environ.get("POGA_STORE_GUARD_REPORT")
    if report_to:
        reads, sites = {}, {}
        for rec in results:
            for test_id, paths in (rec.get("store_reads") or {}).items():
                reads.setdefault(test_id, []).extend(paths)
            for site, n in (rec.get("store_sites") or {}).items():
                sites[site] = sites.get(site, 0) + n
        if sites:
            reads["<sites>"] = sorted(("%7d  %s" % (n, s) for s, n in sites.items()),
                                      reverse=True)
        pathlib.Path(report_to).write_text(json.dumps(reads, indent=1, sort_keys=True),
                                           encoding="utf-8")
        print("store guard: %d test(s) read the live store — %s" % (len(reads), report_to),
              flush=True)

    ok = _summarize(results, elapsed, sys.stderr)
    sys.stderr.flush()

    if args.expect is not None:
        ran = sum(r["testsRun"] for r in results)
        if ran != args.expect:
            sys.stderr.write(
                "\nsuite:   FAIL — %d test(s) ran, --expect %d. A shard was lost or the "
                "suite changed size; this is not a pass.\n" % (ran, args.expect))
            return 1
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
