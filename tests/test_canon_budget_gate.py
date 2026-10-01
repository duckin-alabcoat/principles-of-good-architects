"""WI-0208 / ADR-0137 — the canon budget refuses a land that grows it past the ceiling.

The check has to be right in BOTH directions, and the wrong one is the expensive one. A
guard that refuses a change made under the ceiling gets routed around and then deleted;
a guard that never refuses is the report we already had, which watched the set go from
1,034 over to 19,928 over in fourteen days.

The last test is the one that matters: a detector proves itself on the real defect, not
on its author's fixtures. `THE REAL DEFECT` below runs the check against the actual
pre-ADR-0136 content of this repo's own doctrine set.

stdlib unittest: python3 -m unittest discover -s tests
"""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "curate"))

spec = importlib.util.spec_from_file_location("ckb", ROOT / "curate" / "check_canon_budget.py")
ckb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ckb)


def _git(repo, *args):
    # -c commit.gpgsign=false: a fixture that inherits the operator's signing config
    # breaks the suite on a machine configured to sign (test_fixture_git_defaults.py).
    return subprocess.run(["git", "-c", "commit.gpgsign=false", *args],
                          cwd=str(repo), capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                               "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)})


class BudgetRepo:
    """A two-file repo with a trunk, so 'before' and 'after' are both real."""

    def __init__(self, tmp, trunk_sizes):
        self.root = Path(tmp)
        _git(self.root, "init", "-q", "-b", "main")
        self._write(trunk_sizes)
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "trunk")

    def _write(self, sizes):
        for name, n in zip(("CANON.md", "STANDARD.md"), sizes):
            (self.root / name).write_bytes(b"x" * n)

    def stage(self, sizes):
        """Leave the working tree at `sizes` while main stays where it was — exactly the
        shape the gate sees in its detached worktree at the candidate tip."""
        self._write(sizes)
        return ckb.evaluate(self.root)


class TheRefusalIsNarrowTest(unittest.TestCase):
    def setUp(self):
        self.budget = 1000
        patch = mock.patch.object(ckb, "CANON_BUDGET_CHARS", self.budget)
        patch.start()
        self.addCleanup(patch.stop)

    def _in_repo(self, trunk, tree):
        with tempfile.TemporaryDirectory() as tmp:
            return BudgetRepo(tmp, trunk).stage(tree)

    def test_under_the_ceiling_it_fits_however_much_the_change_grew(self):
        """The constraint operator stated in the ruling itself: silent below the ceiling. A
        guard that fires on correct code is the one lanes delete."""
        v = self._in_repo((100, 100), (400, 400))
        self.assertEqual(v["verdict"], "fits")
        self.assertIsNone(v["before"], "the trunk is not even read when the set fits")

    def test_exactly_at_the_ceiling_fits(self):
        """A ceiling is a limit, not a limit minus one. An off-by-one here refuses the
        change that lands precisely on budget."""
        self.assertEqual(self._in_repo((100, 100), (500, 500))["verdict"], "fits")

    def test_over_and_grown_is_refused(self):
        v = self._in_repo((400, 400), (700, 700))
        self.assertEqual(v["verdict"], "grew")
        self.assertEqual(v["before"], 800)
        self.assertEqual(v["after"], 1400)

    def test_over_but_shrunk_is_not_refused(self):
        """'unless it carries its own consolidation', made mechanical. Without this the
        guard blocks the consolidation pass that would clear the debt — the one land that
        must always be able to get through."""
        v = self._in_repo((900, 900), (700, 700))
        self.assertEqual(v["verdict"], "in-debt-but-not-grown")

    def test_over_but_untouched_is_not_refused(self):
        """A land that never touched the doctrine set did not add the bytes. Refusing it
        freezes the whole repository for as long as the set is in debt."""
        v = self._in_repo((900, 900), (900, 900))
        self.assertEqual(v["verdict"], "in-debt-but-not-grown")

    def test_a_single_byte_over_and_grown_still_refuses(self):
        """The boundary from the other side. 1001 of 1000 is over."""
        self.assertEqual(self._in_repo((400, 400), (500, 501))["verdict"], "grew")


class TheThirdAnswerTest(unittest.TestCase):
    def test_an_unreadable_set_is_not_a_pass(self):
        """`metrics.mine_canon_size` is fail-soft — an unreadable file contributes 0 —
        which is right for a report and catastrophic for a gate: a missing CANON.md would
        read as a very comfortable pass. The check must raise instead."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ckb.CannotCheck):
                ckb.evaluate(Path(tmp))

    def test_it_exits_two_not_zero_when_it_cannot_measure(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ckb, "ROOT", Path(tmp)), \
                 mock.patch("sys.argv", ["check", "--check"]):
                self.assertEqual(ckb.main(), 2)

    def test_an_unresolvable_trunk_cannot_block_a_land_that_fits(self):
        """The before-side is read only when the after-side is already over, so a repo
        with no main still lands anything under the ceiling."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CANON.md").write_bytes(b"x")
            (root / "STANDARD.md").write_bytes(b"x")
            self.assertEqual(ckb.evaluate(root)["verdict"], "fits")

    def test_status_never_fails_a_session(self):
        """The reporting surface. --status is for a hook, where blocking is wrong."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(ckb, "ROOT", Path(tmp)), \
                 mock.patch("sys.argv", ["check", "--status"]):
                self.assertEqual(ckb.main(), 0)


class TheReceiptNamesTheBytesTest(unittest.TestCase):
    def test_a_refusal_names_the_bytes_added_and_the_ceiling_crossed(self):
        """the operator's wording: the land refuses 'naming the bytes it added and the ceiling it
        crossed'. A refusal that says only 'too big' sends the lane to re-derive what the
        check already computed."""
        text = ckb._report({"verdict": "grew", "after": 1400, "before": 800,
                            "budget": 1000, "ref": "main"})
        self.assertIn("1400", text)
        self.assertIn("1000", text)
        self.assertIn("600", text)          # the bytes this change added
        self.assertIn("OVER by 400", text)  # how far past the ceiling it lands
        self.assertIn("consolidation", text)


class TheRealDefectTest(unittest.TestCase):
    """A detector proves itself on the defect that caused the item, not on fixtures."""

    def _pre_split_sizes(self):
        out = {}
        for name in ("CANON.md", "STANDARD.md"):
            r = subprocess.run(["git", "log", "--format=%H", "-1",
                                "--before=2026-09-13T20:00:00", "--", name],
                               cwd=str(ROOT), capture_output=True, text=True)
            sha = (r.stdout or "").strip().splitlines()
            if not sha:
                return None
            b = subprocess.run(["git", "show", f"{sha[0]}:{name}"], cwd=str(ROOT),
                               capture_output=True)
            if b.returncode != 0:
                return None
            out[name] = b.stdout
        return out

    def test_it_refuses_the_growth_that_produced_this_item(self):
        """The real pre-consolidation doctrine set of THIS repo — 67,928 bytes against a
        48,000 ceiling — with one byte added on top. That is the land the old soft budget
        waved through fourteen times in four weeks."""
        pre = self._pre_split_sizes()
        if pre is None:
            self.skipTest("pre-split blobs not reachable from this checkout")
        total = sum(len(v) for v in pre.values())
        self.assertGreater(total, ckb.CANON_BUDGET_CHARS,
                           "fixture premise: the pre-split set was over its ceiling")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _git(root, "init", "-q", "-b", "main")
            for name, blob in pre.items():
                (root / name).write_bytes(blob)
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "the set as it actually stood")

            # unchanged: in debt, but this land did not cause it
            self.assertEqual(ckb.evaluate(root)["verdict"], "in-debt-but-not-grown")

            # one byte more: refused
            (root / "STANDARD.md").write_bytes(pre["STANDARD.md"] + b"x")
            self.assertEqual(ckb.evaluate(root)["verdict"], "grew")

    def test_todays_set_would_not_have_been_refused(self):
        """And the other half — after ADR-0136 the real set fits, so the gate this change
        installs does not refuse the very next land."""
        self.assertEqual(ckb.evaluate(ROOT)["verdict"], "fits")


if __name__ == "__main__":
    unittest.main()
