"""ADR-0051 Rule 3 — concurrent ADR-number allocation (phase 4).

Two lanes each drafting an ADR must never grab the same number. `adr-next` reserves the
next free number atomically across existing adr/*.md files AND live reservations. These pin:

  1. the next number is max(existing files) + 1;
  2. two sequential reservations (as two lanes would) get DISTINCT numbers;
  3. a reservation counts even before its ADR file exists (no reuse until it lapses);
  4. an existing file's number is never reused even after a reservation lapses.
"""

import argparse
import contextlib
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class AdrAllocTest(unittest.TestCase):
    def setUp(self):
        # Two fictional lanes draw and release ADR numbers here; the release must be
        # refused for the lane that does not hold the reservation, which only works while
        # they are distinguishable — i.e. not both carrying the runner's journal (WI-0126).
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        (self.main / "adr").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        for n in ("0001-a.md", "0002-b.md", "0061-c.md"):
            (self.main / "adr" / n).write_text("# adr\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(self.laneB), "main")
        for lane in (self.laneA, self.laneB):
            (lane / "adr").mkdir(exist_ok=True)
            for n in ("0001-a.md", "0002-b.md", "0061-c.md"):
                (lane / "adr" / n).write_text("# adr\n", encoding="utf-8")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_existing_max_plus_one(self):
        session.ROOT = self.laneA
        self.assertEqual(session._adr_existing_max(), 61)

    def test_two_lanes_get_distinct_numbers(self):
        session.ROOT = self.laneA
        a = session._adr_reserve_next("worktree-poga-1")
        session.ROOT = self.laneB
        b = session._adr_reserve_next("worktree-poga-2")
        self.assertEqual(a, 62)
        self.assertEqual(b, 63)        # laneB sees laneA's live reservation and bumps
        self.assertNotEqual(a, b)

    def test_reservation_blocks_reuse_before_file_exists(self):
        session.ROOT = self.laneA
        first = session._adr_reserve_next("worktree-poga-1")
        # Same lane asks again (a second ADR) — must NOT hand back the same number.
        second = session._adr_reserve_next("worktree-poga-1")
        self.assertEqual((first, second), (62, 63))

    def test_landed_file_number_never_reused(self):
        session.ROOT = self.laneA
        n = session._adr_reserve_next("worktree-poga-1")     # 62
        (self.laneA / "adr" / f"{n:04d}-new.md").write_text("# adr\n", encoding="utf-8")
        # Let the reservation lapse; the file scan still keeps 62 out of circulation.
        session._coord_release("adr-alloc", f"{n:04d}", "worktree-poga-1")
        self.assertEqual(session._adr_reserve_next("worktree-poga-1"), 63)


@unittest.skipUnless(GIT, "git not available")
class PrefixedAdrFilenamesTest(AdrAllocTest):
    """WI-0128 — a member may declare WHERE its ADRs live but not what they are CALLED.

    A member declared `layout.adr_dir` correctly and was still invisible: its ADR
    files are `ADR-024-slug.md` and the pattern was `^(\\d{4})-`, anchored on a bare
    number. `_adr_existing_max()` returned 0 against them, so the allocator confidently
    answered `0001` — a number already cited across its documents — and the land gate
    built to catch exactly that collision reads the same `_Counter` and certified clean.
    Second member to hit the two-layers-blind-on-one-assumption class, through a
    different door: the first moved the directory, this one renamed the files.

    Padding was not the fix and the brief says the reporter initially thought it was:
    `ADR-0024-slug.md` fails the same pattern. The prefix is the fault.
    """

    def _prefixed(self, lane, *names):
        d = lane / "adr"
        for p in d.glob("*.md"):
            p.unlink()
        for n in names:
            (d / n).write_text("# adr\n", encoding="utf-8")

    def test_prefixed_files_are_invisible_without_the_declaration(self):
        """Pinned FAILING-shaped first: this is the live defect, and a fix that could not
        reproduce it would be a fix for nothing."""
        session.ROOT = self.laneA
        self._prefixed(self.laneA, "ADR-024-a.md", "ADR-029-b.md")
        self.assertEqual(session._adr_existing_max(), 0,
                         "undeclared prefix — the counter genuinely cannot see them")

    def test_declaring_the_prefix_makes_them_countable(self):
        session.ROOT = self.laneA
        session.CFG["adr_prefix"] = "ADR-"
        self._prefixed(self.laneA, "ADR-024-a.md", "ADR-029-b.md")
        self.assertEqual(session._adr_existing_max(), 29)

    def test_the_next_draw_sorts_after_the_legacy_three_digit_ids(self):
        """Read tolerantly, write canonically: three-digit legacy names are counted, and
        the number drawn is four-digit — `0030` sorts after `ADR-029` and cannot collide,
        so the store converges on one width instead of preserving a legacy one forever."""
        session.ROOT = self.laneA
        session.CFG["adr_prefix"] = "ADR-"
        self._prefixed(self.laneA, "ADR-024-a.md", "ADR-029-b.md")
        self.assertEqual(session._adr_reserve_next("worktree-poga-1"), 30)

    def test_the_land_gate_sees_the_same_files_the_allocator_does(self):
        """The half that made this dangerous rather than merely wrong. Both layers read
        one `_Counter`, so a narrow pattern blinded the allocator AND the gate meant to
        catch it — the failure mode `_adr_dir`'s own docstring records another member hitting."""
        session.ROOT = self.laneA
        session.CFG["adr_prefix"] = "ADR-"
        self._prefixed(self.laneA, "ADR-024-a.md")
        c = session._counter("adr")
        self.assertEqual(
            session._counter_num_in_path(c, "adr/ADR-024-a.md"), 24,
            "the gate resolves a prefixed path to its number, or it filters on nothing")

    def test_an_undeclared_member_behaves_exactly_as_before(self):
        """No existing member changes: the quantifier is greedy, so a four-digit name
        still yields all four digits under `^(\\d{3,4})-`."""
        session.ROOT = self.laneA
        self.assertEqual(session._adr_existing_max(), 61)
        self.assertEqual(session._counter("adr").basename_re.match("0061-c.md").group(1),
                         "0061")

    def test_the_pattern_is_not_frozen_by_the_registry_cache(self):
        """The registry is built once and cached, so a pattern resolved at first use would
        outlive the config — the same trap `dirname` is a callable to avoid."""
        session.ROOT = self.laneA
        c = session._counter("adr")
        before = c.basename_re.pattern
        session.CFG["adr_prefix"] = "ADR-"
        self.assertNotEqual(c.basename_re.pattern, before)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(GIT, "git not available")
class UndrawnNumberIsRefusedTest(unittest.TestCase):
    """WI-0057. The allocator's one unarbitrated path used to issue `max+1` anyway and
    warn on stderr — the single channel the caller provably does not read, because
    `adr-next` puts the number on stdout and everything else on stderr precisely so
    `N=$(session.py adr-next)` captures cleanly. So a script capturing the number never
    saw the warning, and a hook usually discards stderr outright.

    That is the session-93 five-way collision in miniature: two lanes that both cannot
    reach the store both compute the same `max+1`, confidently and silently.
    """

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "adr").mkdir(parents=True)
        (self.repo / "work-items").mkdir()
        (self.repo / "ops-items").mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "adr" / "0007-x.md").write_text("# adr\n", encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        session.ROOT = self.repo
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unreachable_store(self):
        """The real failure mode: `_coord_dir` cannot resolve or create the store."""
        return mock.patch.object(session, "_coord_dir", lambda *a, **k: None)

    def _run(self, fn, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as e:
                fn(args)
        return e.exception.code, out.getvalue(), err.getvalue()

    def test_adr_next_refuses_rather_than_issuing_an_unarbitrated_number(self):
        with self._unreachable_store():
            code, out, err = self._run(session.cmd_adr_next, argparse.Namespace())
        self.assertEqual(code, 3, "a distinct exit code, not argparse's 2")
        self.assertEqual(out, "", "a capture must come back EMPTY, never holding a "
                                  "number nobody arbitrated")
        self.assertIn("REFUSING", err)

    def test_the_refusal_names_the_real_cause_not_the_shared_volume(self):
        """The item filed this as an availability trade — fail-closed 'bricks number
        drawing whenever the shared store is unreachable'. The store is in the repo's own
        .git (ADR-0062), so that scenario does not exist, and the message must not
        repeat the wrong premise to the next reader."""
        with self._unreachable_store():
            _code, _out, err = self._run(session.cmd_adr_next, argparse.Namespace())
        self.assertIn("poga-coord", err)
        self.assertIn(".git", err)

    def test_wi_next_refuses_too(self):
        with self._unreachable_store():
            code, out, _err = self._run(session.cmd_wi_next, argparse.Namespace())
        self.assertEqual(code, 3)
        self.assertEqual(out, "")

    def test_ops_next_refuses_too(self):
        with self._unreachable_store():
            code, out, _err = self._run(session.cmd_ops_next, argparse.Namespace())
        self.assertEqual(code, 3)
        self.assertEqual(out, "")

    def test_wi_new_refuses_before_writing_any_file(self):
        """The creating verbs matter more than the printing ones: an unarbitrated number
        there lands as a FILE that a concurrent lane will collide with at land."""
        # `scope` is required at creation (WI-0323) and validated BEFORE the draw, so
        # this fixture must carry one to reach the undrawn-store refusal it is about.
        args = argparse.Namespace(title="a new item", section="next", blocked_by="",
                                  impact="fix", group="", source="", notes="",
                                  scope="harness")
        with self._unreachable_store():
            code, _out, _err = self._run(session.cmd_wi_new, args)
        self.assertEqual(code, 3)
        self.assertEqual(list((self.repo / "work-items").glob("*.md")), [],
                         "nothing may be written on the refused path")

    def test_a_reachable_store_still_draws_normally(self):
        """The guard must not become a refusal to draw at all."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            session.cmd_adr_next(argparse.Namespace())
        self.assertEqual(out.getvalue().strip(), "0008")
