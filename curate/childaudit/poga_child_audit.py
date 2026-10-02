#!/usr/bin/env python3
"""Measure a CHILD process's repo reads — the half `sys.addaudithook` cannot see.

WI-0337. `curate/gate_inputs.py` derives the land gate's input set by installing an
audit hook and running the suite in THAT ONE PROCESS. An audit hook is
per-interpreter: it lives on the running interpreter and a `fork` + `exec` child
starts with none. 67 of the federation's 112 test modules spawn subprocesses, several
with `cwd` at the repo root — `bootstrap.py --adopt` (tests/test_finish_line.py),
`./poga work` (tests/test_poga_fleet.py), `session.py preflight`
(tests/test_preflight.py) — so every repo path those children opened was read by the
suite and recorded by nobody.

THE DIRECTION IS WHAT MAKES IT A DEFECT RATHER THAN AN INACCURACY. The land classifies
a diff gate-neutral when it touches NO measured prefix (`_classify_gate_neutral` in
sessionlib/land.py), so a missing input does not make the gate cautious — it makes it
SKIP. Fewer measured reads means more diffs classify neutral, and the failure is a
suite that should have run and did not. It is silent in both directions: the record
looks well-formed and confident whether or not half the reads reached it.

HOW THE CHILD IS REACHED. There is no environment variable that installs an audit
hook, so the only portable hook point inside a child we do not own is `site`'s import
of `sitecustomize`, which is resolved on `sys.path` — and `PYTHONPATH` is inherited
across `exec`. The probe puts THIS directory on `PYTHONPATH`; the `sitecustomize.py`
beside this file calls `arm()`; every Python descendant of the probe therefore
measures itself and drops what it read into a collection directory the probe reads
back afterwards. Grandchildren are covered by the same inheritance, without the probe
needing to know they exist.

WHAT IT STILL CANNOT SEE, SAID OUT LOUD RATHER THAN ASSUMED AWAY
([`declare-what-a-check-assumes`](../../habits/master.md#declare-what-a-check-assumes)):

  - A NON-PYTHON child — `git`, `sh`, a shell script. Its *exec* of a repo file is
    still recorded, by the parent, off the `subprocess.Popen` audit event; anything it
    opens afterwards is not.
  - A child launched with `-I` / `-E` (which ignore `PYTHONPATH`) or with an explicit
    `env=` that drops it.
  - A child that dies without running `atexit` — `os._exit`, an uncaught signal.

None of those are left as hope. The parent counts the spawns it has reason to expect a
report from, REFUSES the whole measurement if it hears back from none of them, and
names every other spawn in the record's `unaudited` list, so the gap a reader has to
reason about is written down beside the number rather than inferred from its absence.

This module is imported by `sitecustomize.py` in the same directory (the child side)
and by `curate/gate_inputs.py` (the parent side), which uses `read_event` for its OWN
hook so the two halves of one measurement cannot drift apart
([P16](../../principles/master.md#p16--avoid-duplication)).
"""

from __future__ import annotations

import atexit
import json
import os
import pathlib
import sys

#: Where a child writes what it read. Absent => `arm()` is a no-op, which is what keeps
#: this file inert for every Python on the machine that is not a probe descendant. It is
#: deliberately the *directory* and not a single file: one file per process is the only
#: shape that survives N concurrent children with no lock.
ENV_DIR = "POGA_GATE_INPUTS_CHILD_DIR"

#: The repo root reads are classified against. PASSED, never derived: a child can be
#: running anywhere on the filesystem — the suite `chdir`s into tmpdirs constantly — and
#: must not have to find the repo in order to be measured.
ENV_ROOT = "POGA_GATE_INPUTS_ROOT"

#: Paths the measurement always drops: git's own directory (the plumbing touches it
#: constantly and it is never part of a lane's authored diff) and bytecode caches.
#: Neither is something a lane can commit. This is now the ONE definition; the parent
#: imports it rather than keeping its own copy.
IGNORED_PREFIXES = (".git/", "__pycache__/")


def rel(path, root):
    """`path` as a repo-relative POSIX path, or None if it lies outside the repo.

    Tests run overwhelmingly in tmpdirs; those are outside `root` and are exactly
    what this drops. Anything resolving inside the repo is a real read of real
    tracked content and belongs in the record.

    A RELATIVE PATH IS RESOLVED AGAINST THE LIVE CWD, never a cwd captured when the
    probe started. The first version of this captured it once, and since the suite
    `chdir`s into tmpdirs constantly, every relative path a test opened was joined to
    the REPO ROOT instead — resolving happily, because `Path.resolve()` does not
    require the file to exist. The measurement came back with 503 "read prefixes"
    including `00`, `a1`, `wt` and `repo`: git object directories and fixture folder
    names from other people's temp directories, wearing repo-relative paths. It
    failed closed (nothing would ever have been neutral) but it measured nothing.

    The same rule has to hold inside a child, where the live cwd is even less likely
    to be the repo — which is why this moved here rather than being copied.
    """
    try:
        p = pathlib.Path(os.fsdecode(path))
    except Exception:
        return None
    try:
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        r = p.resolve().relative_to(root)
    except Exception:
        return None
    s = r.as_posix()
    if s == ".":
        return None
    for bad in IGNORED_PREFIXES:
        if s == bad.rstrip("/") or s.startswith(bad):
            return None
    return s


def read_event(event, args, root):
    """The repo-relative path an audit event read, or None. ONE rule, both hooks.

    `open` covers builtins.open, io.open, os.open and pathlib; `os.scandir` and
    `os.listdir` cover the glob/iterdir traversals that make a whole directory an
    input even when no individual file inside it is opened.
    """
    if event == "open":
        return rel(args[0], root) if args else None
    if event in ("os.scandir", "os.listdir"):
        return rel(args[0], root) if args and args[0] else None
    return None


def arm():
    """Install the child-side hook and arrange for it to be written out at exit.

    Returns whether it armed, so a caller can distinguish "the shim was never found"
    from "the shim ran and the child read nothing inside the repo".

    NOTHING HERE MAY RAISE INTO THE CHILD. A measurement that changes what it measures
    is not a measurement, and a `sitecustomize` that throws takes down a process which
    has no idea why — so every failure path is swallowed and surfaces upstream as a
    missing report, which the parent already treats as grounds to refuse.
    """
    out_dir = os.environ.get(ENV_DIR)
    root_s = os.environ.get(ENV_ROOT)
    if not out_dir or not root_s:
        return False
    root = pathlib.Path(root_s)
    paths = set()

    def hook(event, args):
        r = read_event(event, args, root)
        if r:
            paths.add(r)

    try:
        sys.addaudithook(hook)
    except Exception:
        return False

    pid = os.getpid()
    token = os.urandom(8).hex()

    def flush():
        # Write beside the target and rename, so the parent can never read a half
        # written record. `collect` COUNTS a torn file rather than skipping it, and the
        # rename is what makes that count mean "a child died mid-write", not "a child
        # was still writing when I looked".
        try:
            d = pathlib.Path(out_dir)
            tmp = d / (".%d-%s.part" % (pid, token))
            tmp.write_text(json.dumps({
                "pid": pid,
                "argv": list(sys.argv),
                "executable": sys.executable,
                "cwd": os.getcwd(),
                "paths": sorted(paths),
            }), encoding="utf-8")
            tmp.replace(d / ("%d-%s.json" % (pid, token)))
        except Exception:
            pass

    atexit.register(flush)
    return True


def collect(out_dir):
    """Every child's reads unioned, the per-child records, and the torn-file count.

    A record that will not parse is COUNTED, never quietly skipped: it is one child
    whose reads are missing, and the whole reason this module exists is that a missing
    read is invisible unless something says so out loud.
    """
    paths = set()
    records = []
    torn = 0
    d = pathlib.Path(out_dir)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return paths, records, torn
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            torn += 1
            continue
        if not isinstance(rec, dict):
            torn += 1
            continue
        records.append(rec)
        for p in rec.get("paths") or []:
            paths.add(str(p))
    return paths, records, torn


def child_env(base, out_dir, root):
    """`base` plus everything a descendant needs in order to measure itself.

    THIS DIRECTORY GOES FIRST ON `PYTHONPATH` ON PURPOSE. `site` imports the first
    `sitecustomize` it finds on `sys.path`; if an existing entry carried one, ours
    would never load and the measurement would come back empty — loudly, since the
    parent refuses on zero reports, but at the cost of a wedged derive. Going first
    makes ours the one that loads, and `sitecustomize.py` beside this file chains on to
    whatever it displaced rather than swallowing it.
    """
    env = dict(base)
    here = str(pathlib.Path(__file__).resolve().parent)
    previous = env.get("PYTHONPATH")
    env["PYTHONPATH"] = here + (os.pathsep + previous if previous else "")
    env[ENV_DIR] = str(out_dir)
    env[ENV_ROOT] = str(root)
    return env


def marker_on_path(pythonpath):
    """Is this directory on `pythonpath`? The parent's test for an audited spawn.

    A spawn that passes an explicit `env=` without it is one the shim cannot reach, and
    the parent names it in `unaudited` instead of counting it as a child that should
    have reported back.
    """
    here = str(pathlib.Path(__file__).resolve().parent)
    if not pythonpath:
        return False
    return here in str(pythonpath).split(os.pathsep)
