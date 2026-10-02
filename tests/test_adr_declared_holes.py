"""WI-0379 — a burned ADR number is never handed out again.

THE DEFECT, MEASURED RATHER THAN REASONED. The allocator's floor is a scan of the
counter's directory plus sibling lane tips plus held reservations. All three answer *what
is taken*. A number that was drawn and then never spent is taken and has nothing in any of
them to say so: no file on disk, no lane tip, and — once its reservation lapses out of the
coordination store — no hold either. It is a hole, and a hole is exactly what `max + 1`
walks into.

`adr/README.md` already records the consequence in its own table: **ADR-0138 was drawn
2026-09-13, left to expire, and handed out AGAIN on 2026-09-17** to a different session
four days later. It was caught by a person reading that row. The row's own conclusion is
the item this module pins — *"the hole is therefore self-refilling ... this will recur for
0072, 0083, 0086, 0121 and 0138 until the allocator's floor reads this table as well as the
directory."*

The properties pinned here:

  A. THE LIVE TABLE IS READABLE, and all five holes come out of it. Not a fixture copy:
     the real `adr/README.md` this repo ships. Reformat the table so the parser stops
     matching and this module goes red — which is the whole reason the "a section with no
     parseable rows" answer is loud rather than an empty dict
     ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

  B. NONE OF THE FIVE IS EVER ALLOCATED — each one tested in the configuration that
     actually produces it, with the directory topped out one below the hole so `max + 1`
     lands exactly on it. WITH ITS NEGATIVE CONTROL: the identical fixture, with the hole
     read removed, hands out the hole every time. A green allocator proves nothing unless
     the red is shown to be real ([`a-detector-proves-itself-on-the-real-defect`]).

  C. THE SKIP IS REPORTED, never silent. An invisible skip leaves the next reader with the
     same ungrounded gap the table exists to explain, and the acceptance names this
     separately from the refusal for that reason.

  D. THE PARSER DECLARES ITS STATE, rather than spelling four outcomes as one empty
     dict — no index, no section, a table, and a table it could not read. The last is the
     reformat case, and the two callers hold opposite policies on the middle two: the
     fleet-shipped allocator must read "no table" as "declares no holes", while this
     repo's own doc check must read it as a finding. One reader, two policies.

  D2. THERE IS ONLY ONE READER. `curate/check_substrate_docs.py` had its own parser of
     the same table. Two parsers of one table is how the check and the allocator come to
     disagree about which numbers are spent — silently, and in the direction that reissues
     a number ([P16](../principles/master.md#p16--avoid-duplication)).

  E. THE SECTION CLOSES AT SAME-OR-SHALLOWER. A `###` subsection inside the hole section
     does not end it; stopping at a heading of any level would silently truncate the table
     and under-report the holes, which is the direction that costs a number.

  F. THE LAND GATE IS THE SECOND DOOR. Fixing the allocator removes the way a burned
     number is handed out and does nothing about one written by hand, or one drawn before
     this shipped and still sitting in a lane. A declared hole is trunk-free and may be
     held by the very lane filing it, so it passed both of the gate's existing tests
     ([`retire-the-class-not-the-instance`](../habits/master.md#retire-the-class-not-the-instance)).

  G. A COUNTER THAT DECLARES NO INDEX DECLARES NO HOLES. `wi` and `ops` are unchanged, and
     a store whose numbers have never been burned is not a store with an empty table.

stdlib unittest: python3 -m unittest discover -s tests
"""

import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (neutralize_coord_journal,  # noqa: E402
                           neutralize_dispatch_env)

GIT = shutil.which("git")
REPO = pathlib.Path(__file__).resolve().parent.parent
#: The real index this repo ships — the authority, read rather than restated.
LIVE_INDEX = REPO / "adr" / "README.md"

#: A row the public copy adds for a SPENT number whose record it withholds (WI-0449). It is
#: declared so the allocator never reissues it, but it is not a drawn-and-unspent hole, and
#: the fixture below leaves it out: it sits beside 0072 and would move every ceiling.
WITHHELD_MARK = "| **Withheld.**"


def _withheld(index):
    return {int(ln.split("|")[1]) for ln in index.read_text(encoding="utf-8").splitlines()
            if WITHHELD_MARK in ln}


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class TheLiveTableIsTheAuthorityTest(unittest.TestCase):
    """Property A. The subjects come from the authority, never from this module's own
    idea of what the holes are
    ([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority))."""

    def test_the_shipped_index_parses_and_declares_every_known_hole(self):
        holes, problem, state = session._declared_holes(LIVE_INDEX)
        self.assertEqual((problem, state), ("", session.HOLES_OK))
        withheld = _withheld(LIVE_INDEX)
        self.assertTrue(withheld <= set(holes), "a withheld row must still be read as taken")
        holes = sorted(set(holes) - withheld)
        # The five the table itself names, in its own prose, as of WI-0379. Listed here so
        # that REMOVING a row is as visible as reformatting the table; a bare "not empty"
        # would pass on a table that had lost four of its five rows.
        self.assertEqual(sorted(holes), [72, 83, 86, 121, 138])

    def test_every_declared_hole_really_is_absent_from_the_directory(self):
        """The premise the whole mechanism rests on. A number in the table that DOES have
        a file is not a hole, and the table would be describing something else."""
        on_disk = {int(p.name[:4]) for p in (REPO / "adr").glob("[0-9]*.md")}
        holes, _p, _s = session._declared_holes(LIVE_INDEX)
        self.assertEqual(sorted(set(holes) & on_disk), [])


class TheParserHasThreeAnswersTest(unittest.TestCase):
    """Properties D and E."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write(self, text):
        p = self.tmp / "README.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_a_missing_file_is_told_apart_from_a_file_with_no_section(self):
        """The two quiet answers stay distinguishable even though both mean no holes —
        `check_substrate_docs` refuses on either, the allocator proceeds on either, and a
        third caller must not have to guess which it got."""
        holes, problem, state = session._declared_holes(self.tmp / "nope.md")
        self.assertEqual((holes, problem, state), ({}, "", session.HOLES_NO_INDEX))

        p = self._write("# Index\n\n| [0001](0001-x.md) | a row | Accepted |\n")
        self.assertEqual(session._declared_holes(p), ({}, "", session.HOLES_NO_SECTION))

    def test_a_section_with_no_table_in_it_is_reported_not_swallowed(self):
        # THE REFORMAT CASE. Spelling this the same way as "no table" is how a check goes
        # quiet and keeps certifying.
        p = self._write("## Unused numbers\n\nNone of them is a lost ADR.\n")
        holes, problem, state = session._declared_holes(p)
        self.assertEqual(holes, {})
        self.assertEqual(state, session.HOLES_UNREADABLE)
        self.assertIn("NOT consulted", problem)
        self.assertIn(str(p), problem)

    def test_an_EMPTY_table_is_a_table_saying_none_not_a_broken_one(self):
        """The case the first cut got wrong, and the doc check's own fixture caught: a
        member that has the heading and has burned nothing yet is a real state, and
        reading it as the reformat case turns a working check into one that refuses to
        run. A header and a separator ARE a table."""
        p = self._write("## Unused numbers\n\n| # | Drawn | Why |\n|---|---|---|\n")
        self.assertEqual(session._declared_holes(p), ({}, "", session.HOLES_OK))

    def test_a_section_with_rows_yields_the_numbers_and_when_they_were_drawn(self):
        p = self._write("## Unused numbers\n\n"
                        "| # | Drawn | Why |\n|---|---|---|\n"
                        "| 0083 | 2026-07-31 | drawn from main |\n"
                        "| 0138 | 2026-09-13 | spent to learn |\n")
        self.assertEqual(session._declared_holes(p),
                         ({83: "2026-07-31", 138: "2026-09-13"}, "", session.HOLES_OK))

    def test_a_subsection_does_not_close_the_section(self):
        """Property E. `###` is DEEPER, so the table below it is still inside."""
        p = self._write("## Unused numbers\n\n"
                        "| 0083 | 2026-07-31 | first |\n"
                        "### The long-form accounts\n"
                        "| 0138 | 2026-09-13 | second |\n"
                        "## Pending\n"
                        "| 0999 | 2026-01-01 | NOT a hole — different section |\n")
        holes, _p, _s = session._declared_holes(p)
        self.assertEqual(sorted(holes), [83, 138])

    def test_a_sibling_heading_does_close_it(self):
        p = self._write("## Unused numbers\n\n| 0083 | 2026-07-31 | first |\n"
                        "## Pending\n| 0999 | 2026-01-01 | not a hole |\n")
        holes, _p, _s = session._declared_holes(p)
        self.assertEqual(sorted(holes), [83])


@unittest.skipUnless(GIT, "git required")
class HoleBase(unittest.TestCase):
    """A real repo whose ADR directory tops out wherever the case wants it.

    Deliberately not mocked below the allocator: the property is about what
    `_counter_reserve_next` returns with all four of its inputs live, and stubbing the
    first three would be asserting my model of them rather than the allocator."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "adr").mkdir(parents=True)
        _git(self.tmp, "init", "-q", "-b", "main", str(self.repo))
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        # THE LIVE INDEX, copied rather than invented. The acceptance asks for a test that
        # carries the real hole table, so the table under test is the shipped one.
        # Withheld rows are dropped: see WITHHELD_MARK.
        self.index = self.repo / "adr" / "README.md"
        self.index.write_text("".join(
            ln for ln in LIVE_INDEX.read_text(encoding="utf-8").splitlines(True)
            if WITHHELD_MARK not in ln), encoding="utf-8")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._shared = session._SHARED_WORK_ROOT
        self._env = dict(os.environ)
        self.addCleanup(self._restore)
        session.ROOT = self.repo
        session._SHARED_WORK_ROOT = None
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "holetest"

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        session._SHARED_WORK_ROOT = self._shared
        os.environ.clear()
        os.environ.update(self._env)

    # -- helpers ---------------------------------------------------------------
    def _fill_to(self, top):
        """Every ADR number up to `top` on disk, minus the declared holes — the shape the
        real directory has, and the one that puts a hole at `max + 1`.

        Idempotent and additive, so a case may raise the ceiling in steps against one
        repo. The holes are SKIPPED rather than written: a declared hole with a file is
        not a hole, and writing one would make the fixture assert something else."""
        holes, _p, _s = session._declared_holes(self.index)
        for n in range(1, top + 1):
            if n in holes:
                continue
            f = self.repo / "adr" / f"{n:04d}-stub.md"
            if not f.exists():
                f.write_text("stub\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        if subprocess.run(["git", "-C", str(self.repo), "diff", "--cached", "--quiet"],
                          capture_output=True).returncode:
            _git(self.repo, "commit", "-qm", f"adrs up to {top:04d}")
        self.assertEqual(session._counter_existing_max(session._counter("adr")), top,
                         "the fixture must actually top out where the case says it does")

    def _draw(self):
        c = session._counter("adr")
        n = session._counter_reserve_next(c)
        if n is not None:
            session._coord_release(c.namespace, f"{n:04d}", session._coord_identity(),
                                   force=True, reason="test")
        return n


class NoDeclaredHoleIsEverAllocatedTest(HoleBase):
    """Property B — the acceptance, stated as the acceptance states it."""

    def test_none_of_the_five_is_ever_handed_out(self):
        holes, _p, _s = session._declared_holes(self.index)
        self.assertTrue(holes, "the live table must carry rows or this proves nothing")
        # Walked in ASCENDING order in one repo, so each case only ever adds files: the
        # directory is topped out at `hole - 1` when the draw is made, which is the
        # configuration that puts the hole at `max + 1`. The alternative — a fresh repo
        # per hole by re-entering setUp — is correct only by the ordering of accumulated
        # cleanups, and a fixture that is right for a reason nobody can see is one that
        # breaks silently later.
        for hole in sorted(holes):
            with self.subTest(hole=hole):
                self._fill_to(hole - 1)
                drawn = self._draw()
                self.assertNotIn(drawn, holes)
                self.assertEqual(drawn, hole + 1,
                                 "the allocator steps PAST the hole, it does not stop")

    def test_without_the_hole_read_the_same_fixture_hands_out_every_one(self):
        """THE NEGATIVE CONTROL. Without it, the test above could equally mean the fixture
        never put a hole at `max + 1` in the first place."""
        holes, _p, _s = session._declared_holes(self.index)
        for hole in sorted(holes):
            with self.subTest(hole=hole):
                self._fill_to(hole - 1)
                with mock.patch.object(session, "_skip_declared_holes",
                                       side_effect=lambda c, n, h, s, a: n):
                    self.assertEqual(self._draw(), hole)

    def test_consecutive_holes_are_stepped_over_together(self):
        """0083 sits directly below nothing, but the mechanism must survive a table that
        burns two in a row — a `while`, not an `if`."""
        (self.repo / "adr" / "README.md").write_text(
            "## Unused numbers\n\n| 0005 | 2026-01-01 | a |\n| 0006 | 2026-01-02 | b |\n",
            encoding="utf-8")
        for n in (1, 2, 3, 4):
            (self.repo / "adr" / f"{n:04d}-stub.md").write_text("s\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "four")
        self.assertEqual(self._draw(), 7)

    def test_a_directory_below_every_hole_is_unaffected(self):
        """The common case: nothing in the table is anywhere near `max + 1`, and the
        allocator must behave exactly as it did before. A guard that fires on correct code
        gets deleted."""
        self._fill_to(40)
        self.assertEqual(self._draw(), 41)


class TheSkipIsReportedTest(HoleBase):
    """Property C — reported as reserved, naming the hole, rather than skipped silently."""

    def test_the_line_names_the_number_the_source_and_when_it_was_drawn(self):
        self._fill_to(137)
        err = io.StringIO()
        with redirect_stderr(err):
            drawn = self._draw()
        self.assertEqual(drawn, 139)
        out = err.getvalue()
        self.assertIn("0138", out)
        self.assertIn("RESERVED", out)
        self.assertIn("README.md", out)
        self.assertIn("2026-09-13", out)

    def test_the_number_itself_still_goes_to_stdout_alone(self):
        """`N=$(python3 session.py adr-next)` must keep capturing only the number — the
        report is on stderr for the same reason the basis line is."""
        self._fill_to(137)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stderr(err):
            from contextlib import redirect_stdout
            with redirect_stdout(out):
                session.cmd_adr_next(mock.Mock())
        self.assertEqual(out.getvalue().strip(), "0139")
        self.assertIn("0138", err.getvalue())
        c = session._counter("adr")
        session._coord_release(c.namespace, "0139", session._coord_identity(),
                               force=True, reason="test")

    def test_an_unreadable_table_is_announced_rather_than_treated_as_no_holes(self):
        (self.repo / "adr" / "README.md").write_text(
            "## Unused numbers\n\nthe rows were reformatted away\n", encoding="utf-8")
        self._fill_to(137)
        err = io.StringIO()
        with redirect_stderr(err):
            drawn = self._draw()
        # It proceeds — bricking the draw would be worse than the bug — but it SAYS so.
        self.assertEqual(drawn, 138)
        self.assertIn("NOT consulted", err.getvalue())


class ACounterWithNoIndexDeclaresNoHolesTest(HoleBase):
    """Property G."""

    def test_wi_and_ops_are_untouched(self):
        for key in ("wi", "ops"):
            with self.subTest(counter=key):
                self.assertIsNone(session._counter(key).hole_index)
                self.assertEqual(session._counter_holes(session._counter(key)),
                                 ({}, "", ""))

    def test_a_hole_index_that_raises_degrades_to_no_holes_and_names_itself(self):
        c = session._counter("adr")
        with mock.patch.object(c, "hole_index", side_effect=RuntimeError("boom")):
            holes, problem, _src = session._counter_holes(c)
        self.assertEqual(holes, {})
        self.assertIn("boom", problem)


class TheLandGateIsTheSecondDoorTest(HoleBase):
    """Property F — the hand-picked burned number the allocator fix cannot reach."""

    def _land(self, name):
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "base")
        parent = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                                capture_output=True, text=True,
                                check=True).stdout.strip()
        (self.repo / "adr" / name).write_text("# an ADR\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "files an ADR")
        tip = subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True,
                             check=True).stdout.strip()
        return session._counter_land_gate(session._counter("adr"), tip, parent)

    def test_a_hand_written_burned_number_is_refused_by_name(self):
        ok, msg = self._land("0138-written-by-hand.md")
        self.assertFalse(ok)
        self.assertIn("declared permanently unused", msg)
        self.assertIn("0138", msg)
        self.assertIn("2026-09-13", msg)
        self.assertIn("ADR-0069", msg)

    def test_a_number_that_is_not_a_hole_still_lands(self):
        """The negative control the refusal needs: the same fixture, one number over."""
        ok, msg = self._land("0140-perfectly-fine.md")
        self.assertTrue(ok, msg)


if __name__ == "__main__":
    unittest.main()
