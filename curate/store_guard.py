#!/usr/bin/env python3
"""Fail any test that reads the live store (ADR-0148 D3, WI-0427).

A land whose diff touches only bookkeeping — work items, journals, ops items, comms,
the generated views — lands without a suite verdict. That is safe on exactly one
condition: no test's outcome can depend on those files as they sit in the real
checkout. This module makes the condition a checked fact instead of a hope. It installs
an audit hook in every `curate/run_suite.py` worker; a test that opens, lists or scans
a path under `BOOKKEEPING_PATHS` in the REAL root is failed by name, with the paths it
read.

WHICH ROOTS ARE REAL. Two, both captured before any test runs:

  - the checkout the runner lives in (a lane, the main checkout, or the land's
    scratch worktree), and
  - the main checkout, found from git's common dir. A lane reaches it through
    `_shared_work_root()`, and through the `users/` and `proposed-edits/` symlinks a
    lane carries (the 09-05 finding, architect-learnings.md) — a read through those is
    a live read even though the test never named the main checkout.

Both spellings of each root are matched (as given and `realpath`), so `/tmp` versus
`/private/tmp` on macOS cannot hide one.

WHY IT RECORDS AND DOES NOT RAISE. Raising inside the hook would abort the call being
watched, and the harness is full of fail-open `except Exception` blocks written for
production; the refusal would be swallowed and read as success (journal.py's
`_refuse_journal_write_on_fixture` documents the same trap). So the hook only records,
and the worker turns what it recorded into a failure after the test has run.

CHILDREN. An audit hook is per-interpreter. The worker puts `curate/storeguard_child`
first on `PYTHONPATH`; the `sitecustomize.py` there arms the same match in every Python
descendant and writes what it saw at exit, and the worker attributes those reports to
the test that was running when they arrived. What it still cannot see, said out loud:
a non-Python child (git, sh), a child that drops `PYTHONPATH` or runs `-I`/`-E`, and a
child that dies without `atexit`.

MODES, from `POGA_STORE_GUARD`: `enforce` (default) fails the test; `report` records
without failing (how the offenders were first counted); `off` installs nothing.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import subprocess
import sys

ENV_MODE = "POGA_STORE_GUARD"
#: Child side: JSON list of forbidden prefixes, and the directory reports go to.
ENV_CHILD_PREFIXES = "POGA_STORE_GUARD_PREFIXES"
ENV_CHILD_DIR = "POGA_STORE_GUARD_DIR"
CHILD_SHIM_DIR = pathlib.Path(__file__).resolve().parent / "storeguard_child"

WATCHED_EVENTS = ("open", "os.scandir", "os.listdir")


def bookkeeping_paths(root: pathlib.Path) -> tuple:
    """`BOOKKEEPING_PATHS`, read from sessionlib/config.py WITHOUT importing it.

    One definition (P16): the land and the guard must agree on the list, or a path
    the land treats as bookkeeping is a path the guard does not police. Parsed rather
    than imported because importing `session` executes the whole harness before a
    single test has had the chance to set up its own fixtures."""
    config = root / "sessionlib" / "config.py"
    if not config.is_file():
        # Not a harness checkout (a test's miniature repo): there is no store to guard.
        return ()
    src = config.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "BOOKKEEPING_PATHS" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise RuntimeError("BOOKKEEPING_PATHS not found in sessionlib/config.py")


def main_checkout(root: pathlib.Path) -> pathlib.Path | None:
    """The main checkout that owns `root`'s repository, or None outside git."""
    try:
        r = subprocess.run(["git", "rev-parse", "--path-format=absolute",
                            "--git-common-dir"], cwd=str(root), capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, timeout=10)
    except Exception:
        return None
    common = (r.stdout or "").strip()
    if r.returncode != 0 or not common:
        return None
    return pathlib.Path(common).parent


def forbidden_prefixes(root: pathlib.Path) -> list:
    """Absolute path strings a test may not touch. Directory entries end in "/"."""
    roots = {str(root), os.path.realpath(str(root))}
    main = main_checkout(root)
    if main is not None:
        roots |= {str(main), os.path.realpath(str(main))}
    out = set()
    for r in roots:
        r = r.rstrip("/")
        for p in bookkeeping_paths(root):
            out.add(r + "/" + p)
    return sorted(out)


def matches(path, prefixes) -> str | None:
    """The absolute path if it falls under a forbidden prefix, else None.

    String matching only — no `resolve()`, which would cost a stat per path component
    on every open the suite makes. Symlinks are covered by listing both spellings of
    each root, and a lane's symlinked directories by the main checkout being a root."""
    if isinstance(path, int) or path is None:
        return None
    try:
        raw = os.fsdecode(path)
        # A bare name with no separator is how `os.open(name, dir_fd=fd)` reports —
        # shutil.rmtree walks a tree that way — and the event carries no dir_fd, so
        # resolving it against the cwd would charge a tmpdir teardown to the real repo.
        if not os.path.isabs(raw) and os.sep not in raw:
            return None
        s = os.path.abspath(raw)
    except Exception:
        return None
    for p in prefixes:
        if p.endswith("/"):
            if s.startswith(p) or s == p[:-1]:
                return s
        elif s == p:
            return s
    return None


class Guard:
    """The worker-side half: one hook, violations keyed by the running test."""

    def __init__(self, root: pathlib.Path, mode: str):
        self.mode = mode
        self.prefixes = forbidden_prefixes(root)
        self.current = None
        self.reads: dict = {}
        self.child_dir = None

    def install(self, tmp: pathlib.Path) -> None:
        prefixes = self.prefixes

        stacks = os.environ.get("POGA_STORE_GUARD_STACKS")

        def hook(event, args):
            if event in WATCHED_EVENTS and args:
                hit = matches(args[0], prefixes)
                if hit:
                    self.reads.setdefault(self.current, set()).add(hit)
                    if stacks:
                        site = _call_site()
                        SITES[site] = SITES.get(site, 0) + 1

        sys.addaudithook(hook)
        self.child_dir = tmp
        tmp.mkdir(parents=True, exist_ok=True)
        previous = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(CHILD_SHIM_DIR) + (
            os.pathsep + previous if previous else "")
        os.environ[ENV_CHILD_PREFIXES] = json.dumps(prefixes)
        os.environ[ENV_CHILD_DIR] = str(tmp)

    def drain_children(self) -> None:
        """Attribute every child report that has arrived to the running test."""
        if self.child_dir is None:
            return
        for f in sorted(self.child_dir.glob("*.json")):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                f.unlink()
            except (OSError, ValueError):
                continue
            for p in rec.get("paths") or []:
                self.reads.setdefault(self.current, set()).add(
                    "%s  (child: %s)" % (p, " ".join(rec.get("argv") or [])[:120]))

    def take(self, key) -> list:
        self.drain_children()
        return sorted(self.reads.pop(key, set()))


def result_class(guard: Guard, base):
    """A `TextTestResult` that fails a test for the live reads it made."""

    class StoreGuardResult(base):
        def startTest(self, test):
            # Reads made while no test was running — module import, `setUpClass` — are
            # charged to the first test that follows, labelled, rather than dropped.
            before = guard.take(None)
            guard.current = test.id()
            if before:
                guard.reads.setdefault(test.id(), set()).update(
                    "%s  (before this test: import or setUpClass)" % p for p in before)
            super().startTest(test)

        def stopTest(self, test):
            reads = guard.take(test.id())
            guard.current = None
            if reads and guard.mode == "enforce":
                try:
                    raise AssertionError(violation_text(reads))
                except AssertionError:
                    self.addFailure(test, sys.exc_info())
            elif reads:
                REPORTED.setdefault(test.id(), []).extend(reads)
            super().stopTest(test)

    return StoreGuardResult


#: Report-mode findings, per test id, plus reads made outside any test.
REPORTED: dict = {}
#: Diagnostic (POGA_STORE_GUARD_STACKS=1): live reads counted by the innermost
#: non-test repo frame and the test frame that led to it.
SITES: dict = {}


def _call_site() -> str:
    import traceback
    frames = traceback.extract_stack()[:-3]
    code = test = None
    for f in reversed(frames):
        fn = f.filename
        if "/tests/" in fn:
            test = test or "%s:%s" % (os.path.basename(fn), f.name)
        elif code is None and ("/sessionlib/" in fn or "/curate/" in fn
                               or fn.endswith(("session.py", "poga_cli.py")))\
                and "store_guard" not in fn:
            code = "%s:%d:%s" % (os.path.basename(fn), f.lineno, f.name)
    return "%s  <-  %s" % (code, test)


def violation_text(reads: list) -> str:
    shown = reads[:10]
    more = len(reads) - len(shown)
    return ("store guard (ADR-0148 D3): this test read the LIVE store — "
            "bookkeeping lands skip the suite, so a test that depends on these files "
            "can go red without any land touching code. Point it at a fixture "
            "(tests/coord_fixture.py `point_store_at`).\n  "
            + "\n  ".join(shown)
            + ("\n  … and %d more" % more if more else ""))


def mode() -> str:
    m = os.environ.get(ENV_MODE, "enforce").strip().lower()
    return m if m in ("enforce", "report", "off") else "enforce"
