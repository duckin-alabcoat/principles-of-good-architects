"""WI-0327 — the suite must never read the record the land's record commit rewrites.

THE MEASUREMENT, from the WI-0325 land (session ~259). Its receipt said:

    suite verdict not reused: record read by suite: gate-inputs.json

ADR-0122 D1 lets a land reuse the verdict its own serial derive already produced,
instead of running the five-minute suite a second time behind the record commit. The
proof it requires is tree equivalence: the derive measured parent's tree, the record
commit changed exactly one path, and that path is one the suite never reads — so the
tested tree and the landing tree are the same tree. `_gate_validation_reuse` therefore
refuses outright when `read_paths` names the record, because a suite that reads the
record was measured against bytes the record commit then overwrote.

Three assertions in `test_gate_runs_the_store_validator.py` read the live
`ROOT/gate-inputs.json` to check what today's record declared. That put the record in
the measurement, and so **every suite-changing land ran the whole suite twice** — the
reuse path shipped and could never once fire. The three reads have moved to the
deriver's own declaration (see that file); this module is the structural guard that
keeps them from coming back
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).

WHAT THIS CHECK ASSUMES, AND WHAT IT CANNOT SEE
([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
It is a **static** scan. It reads every module under `tests/` and fails on the shape
that caused the defect: the record's name joined to a repo-root-anchored path, or
handed to `open()` / `Path()` as a bare relative literal (which resolves against the
live cwd, and the suite's cwd is the repo root unless a test moved it). It CANNOT see
a read reached dynamically — a helper called without `session.ROOT` patched to a
tmpdir, say. It does not claim to.

The authoritative detector for that residue already exists and is not duplicated here:
the derive measures the real reads, and `_gate_validation_reuse` refuses reuse and
NAMES the file when `read_paths` contains it (pinned by
`test_gate_validation_reuse.ValidationReuseTests.test_record_read_by_suite_invalidates`).
That backstop degrades — a land runs the suite twice — where this one fails loudly and
early, at the moment someone writes the line. Two different jobs, deliberately.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import session  # noqa: E402

TESTS_DIR = pathlib.Path(__file__).resolve().parent

#: Imported, never re-spelled: the land and the deriver both name the record through
#: this constant, and a second literal copy here is the drift that would make the guard
#: watch a filename nothing else uses ([P16](../principles/master.md#p16--avoid-duplication)).
RECORD_NAME = session.GATE_INPUTS_RECORD

#: Calls whose first argument is a path. A bare relative literal handed to one of these
#: is resolved against the live cwd — the repo root, for any test that has not chdir'd.
_PATH_CALLS = ("open", "Path")


def _is_record_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == RECORD_NAME


def _repo_anchored_names(tree: ast.Module) -> set[str]:
    """Every name bound anywhere in the module to an expression mentioning `__file__`.

    That is how a test module reaches the repo root: `REPO = Path(__file__).resolve()
    .parent.parent`, or a local `source = Path(__file__).resolve().parents[1]`. A name
    bound to a tmpdir never mentions `__file__`, which is exactly the distinction the
    guard needs and the reason it is drawn this way rather than by naming `REPO`.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        value = getattr(node, "value", None)
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and value is not None:
            if "__file__" not in ast.unparse(value):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _is_repo_anchored(node: ast.AST, anchored: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in anchored
    return "__file__" in ast.unparse(node)


def _offences(path: pathlib.Path) -> list[str]:
    """Lines in `path` that name the record against the repo root."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    anchored = _repo_anchored_names(tree)
    found: list[str] = []

    def flag(node: ast.AST, why: str) -> None:
        found.append(f"{path.name}:{node.lineno}: {ast.unparse(node)}  <- {why}")

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            if _is_record_literal(node.right) and _is_repo_anchored(node.left, anchored):
                flag(node, "the repo-root record")
        elif isinstance(node, ast.Call) and node.args:
            name = ast.unparse(node.func).split(".")[-1]
            if name in _PATH_CALLS and _is_record_literal(node.args[0]):
                flag(node, "a bare relative literal, resolved against the suite's cwd")
            elif name == "join" and len(node.args) >= 2 and _is_record_literal(node.args[-1]):
                if _is_repo_anchored(node.args[0], anchored):
                    flag(node, "the repo-root record")
    return found


class NoTestModuleReadsTheRepoRootRecordTest(unittest.TestCase):
    def test_no_test_module_names_the_repo_root_record(self):
        modules = sorted(TESTS_DIR.glob("*.py"))
        self.assertTrue(modules, "the scan found no test modules — it would pass vacuously")
        offences = [line for m in modules for line in _offences(m)]
        self.assertEqual(
            offences, [],
            "a test that reads " + RECORD_NAME + " from the repo root puts it in the "
            "gate's measured read set, and the land can then never prove its record "
            "commit left the tested tree unchanged — every suite-changing land runs "
            "the suite twice. Read a fixture record under a tmpdir instead:\n  "
            + "\n  ".join(offences))

    def test_the_guard_fails_on_the_shape_it_exists_to_catch(self):
        # `verify-in-the-created-configuration`: a scan that reports zero is worth
        # nothing until it has been shown it can report one.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        fixture = pathlib.Path(td.name) / "test_regression.py"
        fixture.write_text(
            "import pathlib\n"
            "REPO = pathlib.Path(__file__).resolve().parent.parent\n"
            "def test_x():\n"
            "    (REPO / " + repr(RECORD_NAME) + ").read_text()\n",
            encoding="utf-8")
        self.assertEqual(len(_offences(fixture)), 1, _offences(fixture))

    def test_a_tmpdir_record_is_not_an_offence(self):
        # The control. A fix that flagged every mention would be satisfied by deleting
        # the fixtures, which is the opposite of the point.
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        fixture = pathlib.Path(td.name) / "test_fixture.py"
        fixture.write_text(
            "import pathlib, tempfile\n"
            "def test_x():\n"
            "    root = pathlib.Path(tempfile.mkdtemp())\n"
            "    (root / " + repr(RECORD_NAME) + ").write_text('{}')\n",
            encoding="utf-8")
        self.assertEqual(_offences(fixture), [])


if __name__ == "__main__":
    unittest.main()
