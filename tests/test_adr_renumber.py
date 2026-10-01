"""WI-0290 — the ADR counter's renumberer (ADR-0120).

The WI and OPS counters have remapped their own trunk collisions at the land since WI-0169
and WI-0245. The ADR counter refused instead, because it is a different job: an ADR is
prose that names itself, and a landed one is never renumbered. ADR-0120 settled the
doctrine; this pins the code that follows it.

  A. DUPLICATE-NUMBER DETECTION. A lane ADR on a number the trunk landed meanwhile is
     found, and the gate refuses it (the negative control for C).
  B. A RETITLE IS NOT A COLLISION. A renamed ADR keeps its number and is never handed to
     the renumberer.
  C. THE LAND CLEARS ITS OWN COLLISION onto a DRAWN number, and the gate then passes.
  D. THE RECORD STOPS MISIDENTIFYING ITSELF — H1, filename and body move together (D4).
  E. CITATIONS FOLLOW LANE PROVENANCE across adr/, habits/, principles/ and work-items/;
     a trunk line is left alone, and history is reported, never rewritten.
  F. PROVENANCE survives the citation pass and names both numbers.
  G. A VACATED NUMBER GETS A TOMBSTONE ROW in `## Unused numbers` that the allocator
     reads; a number the trunk still holds gets none.
  H. A LANDED ADR IS NEVER REWRITTEN IN PLACE, Accepted or not (D3), and an unlanded
     Accepted ADR still moves (D2).
  I. NEGATIVE CONTROLS: a collision the tool cannot resolve refuses with a named reason
     and changes nothing.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (neutralize_coord_journal,  # noqa: E402
                           neutralize_dispatch_env)

GIT = shutil.which("git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _adr(num, title, status="Accepted", body=""):
    return (f"# ADR-{num:04d}: {title}\n\n"
            f"**Status:** {status}\n"
            f"**Date:** 2026-09-24\n"
            f"**Deciders:** test\n\n"
            f"## Decision\n\n"
            f"ADR-{num:04d} decides {title}.{body}\n")


INDEX = ("# ADRs\n\n## Index\n\n"
         "| # | Title |\n|---|---|\n"
         "| [0001](0001-first.md) | **first** |\n"
         "{rows}"
         "\n## Unused numbers\n\n"
         "| # | Drawn | Why it is unused |\n|---|---|---|\n"
         "| 0000 | never | a fixture row, so the table is not empty |\n"
         "\n## After\n\ntrailing section.\n")


@unittest.skipUnless(GIT, "git required")
class AdrRenumberBase(unittest.TestCase):
    """A real main checkout, a real lane worktree, and a real ADR collision — not mocked,
    because every property here is a property of what git reports."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        for d in ("adr", "habits", "principles", "work-items", "sessions/journal"):
            (self.main / d).mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")

        (self.main / "adr" / "0001-first.md").write_text(_adr(1, "first"),
                                                          encoding="utf-8")
        (self.main / "adr" / "README.md").write_text(INDEX.format(rows=""),
                                                     encoding="utf-8")
        # Predates the lane: it cites ADR-0002 and means the TRUNK's record.
        (self.main / "habits" / "master.md").write_text(
            "trunk habit that cites ADR-0002 and means the trunk's record.\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "base")

        self.lane = self.main / ".claude" / "worktrees" / "poga-9"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-9",
             str(self.lane), "HEAD")

        # TRUNK advances with its own ADR-0002, invisible to the lane's tree.
        (self.main / "adr" / "0002-trunk-side.md").write_text(
            _adr(2, "the trunk's own second decision"), encoding="utf-8")
        (self.main / "adr" / "README.md").write_text(
            INDEX.format(rows="| [0002](0002-trunk-side.md) | **trunk** |\n"),
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands ADR-0002")
        self.parent = self._rev(self.main)

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.lane
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "adrrenumbertest"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _rev(cwd):
        return subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    def _write(self, rel, text):
        p = self.lane / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _read(self, rel):
        return (self.lane / rel).read_text(encoding="utf-8")

    def _commit(self, msg="lane work"):
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", msg)
        return self._rev(self.lane)

    def _adrs(self):
        return sorted(p.name for p in (self.lane / "adr").glob("0*.md"))

    def _collide(self, status="Accepted"):
        """The lane files its own ADR-0002 — Accepted, and unlanded — with an index row
        and citations across the four directories the item names."""
        self._write("adr/0002-lane-side.md",
                    _adr(2, "the lane's own decision", status,
                         " See also [itself](0002-lane-side.md)."))
        self._write("adr/README.md", self._read("adr/README.md").replace(
            "| [0001](0001-first.md) | **first** |\n",
            "| [0001](0001-first.md) | **first** |\n"
            "| [0002](0002-lane-side.md) | **lane** |\n"))
        self._write("habits/master.md", self._read("habits/master.md")
                    + "lane-appended habit citing ADR-0002.\n")
        self._write("principles/master.md", "principle grounded in ADR-0002.\n")
        self._write("work-items/WI-0001-x.md",
                    "# WI-0001: x\n\nbuilds [ADR-0002](../adr/0002-lane-side.md).\n")
        self._write("sessions/journal/20260101T0000Z-t-aa.md",
                    "the journal says ADR-0002 and is record, not guidance.\n")
        return self._commit("lane files ADR-0002")

    def _renumber(self, pairs, parent="default"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            return session._counter("adr").renumberer(
                pairs, self.parent if parent == "default" else parent)


class ADuplicateNumberIsDetectedTest(AdrRenumberBase):

    def test_the_collision_is_found(self):
        tip = self._collide()
        found = session._counter_trunk_collisions(session._counter("adr"), tip,
                                                  self.parent)
        self.assertEqual([n for n, _ in found], [2])
        self.assertTrue(found[0][1].endswith("0002-lane-side.md"))

    def test_without_the_remap_the_gate_refuses(self):
        # THE NEGATIVE CONTROL for C, and it runs on the same fixture.
        tip = self._collide()
        ok, msg = session._all_counter_land_gates(tip, self.parent)
        self.assertFalse(ok)
        self.assertIn("0002", msg)


class BARetitleIsNotACollisionTest(AdrRenumberBase):

    def test_a_renamed_adr_keeps_its_number(self):
        _git(self.lane, "mv", "adr/0001-first.md", "adr/0001-first-retitled.md")
        tip = self._commit("retitle")
        c = session._counter("adr")
        self.assertEqual(session._counter_trunk_collisions(c, tip, self.parent), [])
        with redirect_stdout(io.StringIO()):
            newtip, _rep = session._auto_renumber_collisions(tip, self.parent)
        self.assertIsNone(newtip, "a retitle must never reach the renumberer")
        self.assertIn("0001-first-retitled.md", self._adrs())


class CTheLandClearsItsOwnCollisionTest(AdrRenumberBase):

    def test_the_remap_clears_it_and_the_gate_then_passes(self):
        tip = self._collide()
        with redirect_stdout(io.StringIO()):
            session._auto_renumber_collisions(tip, self.parent)
        tip = self._commit("remap")
        ok, msg = session._all_counter_land_gates(tip, self.parent)
        self.assertTrue(ok, f"the remap should have cleared the collision: {msg}")
        self.assertNotIn("0002-lane-side.md", self._adrs())
        moved = [n for n in self._adrs() if n.endswith("-lane-side.md")]
        self.assertEqual(len(moved), 1)
        self.assertFalse(moved[0].startswith("0002"),
                         "the replacement must not be the number that collided")


class DTheRecordNamesItselfTest(AdrRenumberBase):

    def setUp(self):
        super().setUp()
        self._collide()
        self.mapping, self.report = self._renumber([("ADR-0002", 7)])

    def test_filename_h1_and_body_move_together(self):
        self.assertEqual(self.mapping, {"ADR-0002": "ADR-0007"})
        self.assertIn("0007-lane-side.md", self._adrs())
        text = self._read("adr/0007-lane-side.md")
        self.assertTrue(text.startswith("# ADR-0007: the lane's own decision"))
        self.assertIn("ADR-0007 decides", text)
        self.assertIn("[itself](0007-lane-side.md)", text)

    def test_an_unlanded_accepted_adr_still_moves(self):
        # D2: immutability attaches at the land. Status is not the test.
        self.assertIn("**Status:** Accepted", self._read("adr/0007-lane-side.md"))


class ECitationsFollowLaneProvenanceTest(AdrRenumberBase):

    def setUp(self):
        super().setUp()
        self._collide()
        self.mapping, self.report = self._renumber([("ADR-0002", 7)])

    def test_the_index_row_this_lane_added_follows(self):
        idx = self._read("adr/README.md")
        self.assertIn("| [0007](0007-lane-side.md) | **lane** |", idx)
        self.assertNotIn("0002-lane-side", idx)

    def test_habits_principles_and_work_items_follow(self):
        self.assertIn("ADR-0007", self._read("habits/master.md").splitlines()[1])
        self.assertIn("ADR-0007", self._read("principles/master.md"))
        wi = self._read("work-items/WI-0001-x.md")
        self.assertIn("[ADR-0007](../adr/0007-lane-side.md)", wi)

    def test_a_trunk_line_is_left_alone(self):
        first = self._read("habits/master.md").splitlines()[0]
        self.assertIn("ADR-0002", first)
        self.assertIn("means the trunk's record", first)

    def test_history_is_reported_and_never_rewritten(self):
        self.assertIn("ADR-0002",
                      self._read("sessions/journal/20260101T0000Z-t-aa.md"))
        self.assertTrue(any("NOT rewritten" in ln for ln in self.report))


class FProvenanceSurvivesTest(AdrRenumberBase):

    def test_the_moved_adr_records_what_it_was_filed_as(self):
        self._collide()
        self._renumber([("ADR-0002", 7)])
        text = self._read("adr/0007-lane-side.md")
        line = next(ln for ln in text.splitlines() if ln.startswith("**Renumbered:**"))
        self.assertIn("ADR-0002", line)
        self.assertIn("ADR-0007", line)
        # In the header block, after Deciders and before the first section.
        lines = text.splitlines()
        self.assertLess(lines.index(line), lines.index("## Decision"))
        self.assertGreater(lines.index(line),
                           next(i for i, ln in enumerate(lines)
                                if ln.startswith("**Deciders:**")))


class GTombstoneTest(AdrRenumberBase):

    def test_a_vacated_number_is_declared_unused(self):
        # ADR-0003 exists nowhere on the trunk, so moving it vacates the number.
        self._write("adr/0003-lane-third.md", _adr(3, "a third"))
        self._commit()
        mapping, report = self._renumber([("ADR-0003", 8)])
        self.assertEqual(mapping, {"ADR-0003": "ADR-0008"})
        holes, problem, _state = session._declared_holes(self.lane / "adr/README.md")
        self.assertEqual(problem, "")
        self.assertIn(3, holes, "the allocator must read the vacated number as spent")
        row = next(ln for ln in self._read("adr/README.md").splitlines()
                   if ln.startswith("| 0003 |"))
        self.assertIn("ADR-0008", row)
        # The row sits inside the Unused numbers table, not after the next heading.
        idx = self._read("adr/README.md")
        self.assertLess(idx.index("| 0003 |"), idx.index("## After"))
        self.assertTrue(any("tombstone" in ln for ln in report))

    def test_no_tombstone_while_the_trunk_holds_the_number(self):
        # THE NEGATIVE CONTROL for the above: in a real collision the trunk's ADR-0002 is
        # live, and a row declaring 0002 unused would be a false statement about it.
        self._collide()
        self._renumber([("ADR-0002", 7)])
        holes, _p, _s = session._declared_holes(self.lane / "adr/README.md")
        self.assertNotIn(2, holes)


class HALandedAdrIsNeverRewrittenTest(AdrRenumberBase):

    def test_a_trunk_adr_is_refused_and_left_byte_identical(self):
        before = self._read("adr/0001-first.md")
        mapping, report = self._renumber([("ADR-0001", 9)])
        self.assertEqual(mapping, {})
        self.assertEqual(self._read("adr/0001-first.md"), before)
        self.assertNotIn("0009-first.md", self._adrs())
        text = "\n".join(report)
        self.assertIn("ADR-0120 D3", text)
        self.assertIn("Accepted", text)

    def test_the_verb_refuses_it_too(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit) as cm:
            session.cmd_adr_renumber(argparse.Namespace(ids=["1"], all=False,
                                                        dry_run=False))
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("ADR-0120 D3", buf.getvalue())
        self.assertIn("0001-first.md", self._adrs())

    def test_a_refusal_draws_no_number_even_on_a_dry_run(self):
        # The shared verb draws before the renumberer runs, so a refusal found after the
        # draw would burn a number. It must be found first.
        for dry in (True, False):
            buf = io.StringIO()
            with redirect_stdout(buf), self.assertRaises(SystemExit):
                session.cmd_adr_renumber(argparse.Namespace(ids=["1"], all=False,
                                                            dry_run=dry))
            self.assertNotIn("would draw", buf.getvalue())
            self.assertEqual(session._counter_alloc_holds(session._counter("adr")), {},
                             "a refused renumber must not have drawn a number")


class IUnresolvableCollisionsRefuseTest(AdrRenumberBase):
    """Every refusal names its reason and changes nothing."""

    def _unchanged(self, before):
        self.assertEqual(self._adrs(), before)

    def test_two_unlanded_files_on_one_number(self):
        self._write("adr/0003-one.md", _adr(3, "one"))
        self._write("adr/0003-two.md", _adr(3, "two"))
        self._commit()
        before = self._adrs()
        mapping, report = self._renumber([("ADR-0003", 8)])
        self.assertEqual(mapping, {})
        self._unchanged(before)
        self.assertIn("no telling which one to move", "\n".join(report))

    def test_the_target_number_is_already_taken(self):
        self._collide()
        before = self._adrs()
        mapping, report = self._renumber([("ADR-0002", 1)])
        self.assertEqual(mapping, {})
        self._unchanged(before)
        self.assertIn("never moves onto a live number", "\n".join(report))

    def test_no_trunk_to_compare_against(self):
        self._collide()
        before = self._adrs()
        mapping, report = self._renumber([("ADR-0002", 7)], parent=None)
        self.assertEqual(mapping, {})
        self._unchanged(before)
        self.assertIn("cannot be told from a draft", "\n".join(report))

    def test_an_unreadable_trunk_fails_closed(self):
        self._collide()
        before = self._adrs()
        mapping, report = self._renumber([("ADR-0002", 7)], parent="0" * 40)
        self.assertEqual(mapping, {})
        self._unchanged(before)
        self.assertIn("could not be read", "\n".join(report))


class TheVerbTest(AdrRenumberBase):

    def test_dry_run_names_the_id_and_draws_nothing(self):
        # The ADR row declares no `filename_for`; the verb must still find the file.
        self._collide()
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_adr_renumber(argparse.Namespace(ids=["2"], all=False,
                                                        dry_run=True))
        self.assertIn("ADR-0002", buf.getvalue())
        self.assertIn("0002-lane-side.md", self._adrs())

    def test_an_unknown_id_is_refused(self):
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            session.cmd_adr_renumber(argparse.Namespace(ids=["42"], all=False,
                                                        dry_run=True))
        self.assertIn("unknown id", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
