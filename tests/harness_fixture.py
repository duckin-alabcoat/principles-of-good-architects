"""One guard, one place: what a test ASSUMES the harness is shaped like (ADR-0118).

The sibling of [`coord_fixture`](coord_fixture.py) and [`ambient_fixture`](ambient_fixture.py),
one layer further out again. Those two stop a fixture inheriting the runner's *journal*
and the runner's *environment*. This one stops a fixture inheriting a belief about the
harness's **file layout** that stopped being true underneath it.

WHAT THE DEFECT LOOKS LIKE. For years the harness was one file, so a test that needed it
said `session.py` and was right. Two kinds of fixture leaned on that:

  copy    build a synthetic member or lane repo in a tmpdir and run the harness inside it
          as a subprocess — `(repo / "session.py").write_text(SESSION_PY.read_text())`
  read    grep or AST-walk the harness's own source for a marker: the argparse verb set,
          an advice string, a lander's `return`, the gate tuple

ADR-0118 moved the code into `sessionlib/` and left `session.py` as a 121-line entry
point. Both kinds broke, and they broke in DIFFERENT directions, which is what makes a
per-call-site repair the wrong repair. The copy fixtures died loudly —
`ModuleNotFoundError: No module named 'sessionlib'` — because a member repo carrying only
`session.py` is not a member that can run. The read fixtures went quiet: they found a
121-line stub, matched nothing, and reported the capability missing or the verb
non-existent. That second half is `declare-what-a-check-assumes` exactly — a check whose
assumption about the SHAPE of its subject silently stopped holding, failing in the
direction that looks like a real regression in the code under test.

WHY A SHARED MODULE RATHER THAN A REPAIR PER CALL SITE. Because the assumption is one
assumption, held in ~20 places across 10 modules, and a fact restated per module is a fact
that diverges — the same argument `coord_fixture` makes from six modules and
`ambient_fixture` from three divergent variable lists. `standard_check._harness_src` (now `_harness_paths`) had
already been forced to state it once for the SHIPPED detectors; this module states it once
for the tests ([`single-source-and-deliver`](../habits/master.md#single-source-and-deliver)),
with the same contract, so the two cannot drift apart.

THE PRE-SPLIT SHAPE STILL PASSES. Every function here works when `sessionlib/` does not
exist: the file list is just `session.py`, the source is just its text, and a copy
installs one file. That is not politeness toward old fixtures — a member that has not yet
received the package is a real member, `standard_check` must read it correctly, and the
fixtures that describe one must keep describing one. Both shapes work, which is what lets
the package roll out across the fleet a member at a time.

Not named `test_*`, so `unittest discover` does not collect it. Both entry points —
`python3 -m unittest discover -s tests` and running a test file directly — put this
directory on `sys.path`, so a plain `import harness_fixture` resolves in either.
"""

import pathlib
import shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: The thin entry point. Stays at the repo root and stays byte-identical fleet-wide.
HARNESS_ENTRY = "session.py"
#: The interpreter pin, imported by the entry point before anything else (WI-0328). Part
#: of the harness for every purpose this module serves: it ships fleet-wide with
#: `session.py`, a member without it cannot honour its own `interpreter` declaration, and
#: a fixture member that runs the harness as a subprocess needs it on disk to do so.
HARNESS_PIN = "interpreter.py"
#: The concern parts. Absent on a member that predates ADR-0118.
HARNESS_PKG = "sessionlib"


def harness_files(root=ROOT):
    """Every file that IS the harness, as repo-relative POSIX strings, entry point first.

    Sorted by name within the package — the same order `standard_check._harness_paths`
    reads them in, so a marker scoped to one function's own text terminates at the same
    `def` on both sides. Only files that exist are listed: on a pre-split member the
    answer is `["session.py"]`."""
    root = pathlib.Path(root)
    out = []
    if (root / HARNESS_ENTRY).is_file():
        out.append(HARNESS_ENTRY)
    if (root / HARNESS_PIN).is_file():
        out.append(HARNESS_PIN)
    try:
        mods = sorted(p.name for p in (root / HARNESS_PKG).iterdir()
                      if p.suffix == ".py" and p.is_file())
    except OSError:
        mods = []
    out.extend(HARNESS_PKG + "/" + name for name in mods)
    return out


def harness_source(root=ROOT):
    """The harness source as ONE string — `session.py` plus every `sessionlib/*.py`.

    This is the text a test means when it says "the harness source": the thing that greps
    for a verb, an advice string or a gate tuple, and the thing an `ast.parse` walks
    looking for a lander's returns. Joined with a newline in `harness_files` order, so
    line numbers within the concatenation are self-consistent for a caller that splits it
    itself.

    Mirrors `standard_check._harness_paths`'s file set deliberately: the shipped detector
    and the tests that pin it must not be able to disagree about what the harness is."""
    root = pathlib.Path(root)
    return "\n".join((root / rel).read_text(encoding="utf-8")
                     for rel in harness_files(root))


def harness_subject(path, root=ROOT):
    """The repo-relative name of the harness file `path` lives in.

    For the tests that read a live function's `__code__.co_filename` and then need the
    matching source file — `cmd_reap_lanes` answers `sessionlib/lanes.py` now and
    answered `session.py` before, and the caller should not have to know which."""
    return pathlib.Path(path).resolve().relative_to(
        pathlib.Path(root).resolve()).as_posix()


def install_harness(repo, root=ROOT):
    """Copy the real harness into `repo` so it can be RUN there. Returns `repo`.

    A fixture member or lane invokes its own copy of `session.py` as a subprocess — that
    is the path that matters, since the harness resolves the repo it drives from its own
    file location. `session.py` alone is not runnable after ADR-0118: it imports
    `sessionlib`, so the package has to travel with it. `__pycache__` deliberately does
    not — a fixture repo carrying stale bytecode is a fixture repo that can pass while the
    source it claims to be running is wrong."""
    repo = pathlib.Path(repo)
    repo.mkdir(parents=True, exist_ok=True)
    root = pathlib.Path(root)
    for rel in harness_files(root):
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / rel, dst)
    return repo


def patch_harness(repo, old, new, count=1):
    """Rewrite `old` -> `new` in whichever harness file under `repo` carries it.

    The deliberate-defect half: several fixtures build a member whose harness is real
    except for one broken line — an unwired gate tuple, a shrunk injection cap — and then
    assert the member reads as deficient. Which FILE the line sits in is exactly the
    detail ADR-0118 changed, and exactly the detail those tests are not about.

    Returns the number of files changed, so a caller keeps the sanity check it already
    had: 0 means the line's shape moved and the fixture is now describing nothing."""
    repo = pathlib.Path(repo)
    changed = 0
    for rel in harness_files(repo):
        p = repo / rel
        src = p.read_text(encoding="utf-8")
        if old not in src:
            continue
        p.write_text(src.replace(old, new, count), encoding="utf-8")
        changed += 1
    return changed
