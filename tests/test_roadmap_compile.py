"""ROADMAP.md is a compiled, trunk-only view (WI-0108 / ADR-0105).

`ROADMAP.md` was the one generated-ish view ADR-0056 left out, while being subject to
exactly the forces that ADR was about: the session-end protocol told EVERY session to
refresh it, so every lane edited one shared file at close, and it is half rendered and
half hand-authored — so the rendered halves regenerate identically while the prose halves
diverge. Two lanes closing on the same day conflicted BY CONSTRUCTION. Session ~129 hit
it landing four stranded lanes: three landed, poga-2 blocked on a rebase conflict whose
only conflicting file was this one.

The acceptance test the item asks for by name — *two lanes closing on the same day
without touching the same file* — is `TwoLanesTest` below, and it is exercised against
real git worktrees rather than asserted.

The other thing pinned here is the region mapping. Regions used to be filled by document
POSITION, and `Recently shipped` sits BEFORE `Next`, so adding it would have silently
shifted every existing region onto the wrong renderer. That is a wrong answer shaped like
a right one, so the mapping is keyed on the heading and there is a test for the shift.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402

GIT = shutil.which("git")
BEGIN = session.WI_GEN_BEGIN
END = session.WI_GEN_END


class OutcomeExtractionTest(unittest.TestCase):
    """The journal's `## Outcome` section — the per-session home for prose that used to
    exist only inside the shared file."""

    def test_the_outcome_paragraph_is_extracted(self):
        body = ("### What happened\n\n- Detail.\n\n"
                "### Outcome\n\nThe fleet can no longer be fooled by a stale mirror.\n\n"
                "## Open questions\n\n- None.\n")
        self.assertEqual(session._journal_outcome(body),
                         "The fleet can no longer be fooled by a stale mirror.")

    def test_it_stops_at_the_next_heading(self):
        """Or the outcome swallows the rest of the journal into the roadmap."""
        body = "### Outcome\n\nOne line.\n\n## Notes\n\n- Not the outcome.\n"
        self.assertNotIn("Not the outcome", session._journal_outcome(body))

    def test_multiple_paragraphs_survive(self):
        body = "### Outcome\n\nFirst para.\n\nSecond para.\n\n## Notes\n\n- x\n"
        got = session._journal_outcome(body)
        self.assertIn("First para.", got)
        self.assertIn("Second para.", got)

    def test_no_outcome_section_is_empty_not_an_error(self):
        """A session that shipped nothing worth stating contributes no bullet. A missing
        bullet is honest; an invented one is the failure there is a habit against."""
        self.assertEqual(session._journal_outcome("### What happened\n\n- Stuff.\n"), "")

    def test_an_empty_outcome_section_is_empty(self):
        self.assertEqual(session._journal_outcome("### Outcome\n\n\n## Notes\n"), "")

    def test_the_heading_match_is_case_insensitive(self):
        self.assertEqual(session._journal_outcome("### outcome\n\nDone.\n"), "Done.")

    def test_a_similar_heading_is_not_mistaken_for_it(self):
        """`## Outcomes` or `## Outcome of the drill` are different sections."""
        self.assertEqual(session._journal_outcome("### Outcome of the drill\n\nx\n"), "")

    def test_both_heading_levels_are_accepted(self):
        """THE BUG THIS FILE ALMOST SHIPPED WITH. The extractor was written to match
        `## Outcome` while the house journal format uses `###`, so it found nothing — and
        found nothing SILENTLY: the region read "no outcomes yet" while every session
        dutifully wrote one, with nothing anywhere erroring. Caught only by writing this
        session's own outcome and watching it fail to appear
        (`verify-in-the-created-configuration`). Both levels are accepted so a member
        whose template differs cannot hit it either."""
        self.assertEqual(session._journal_outcome("## Outcome\n\nTwo hashes.\n"),
                         "Two hashes.")
        self.assertEqual(session._journal_outcome("### Outcome\n\nThree hashes.\n"),
                         "Three hashes.")

    def test_a_deeper_subheading_inside_the_outcome_survives(self):
        """Stopping at ANY heading would truncate an outcome that organises itself."""
        body = ("### Outcome\n\nLead paragraph.\n\n#### A detail\n\nStill the outcome.\n\n"
                "### Notes\n\n- not the outcome\n")
        got = session._journal_outcome(body)
        self.assertIn("Still the outcome.", got)
        self.assertNotIn("not the outcome", got)

    def test_a_shallower_heading_ends_the_section(self):
        body = "### Outcome\n\nMine.\n\n## A new top section\n\nNot mine.\n"
        self.assertNotIn("Not mine", session._journal_outcome(body))

    def test_the_real_journal_format_is_the_one_that_works(self):
        """Driven against the ACTUAL section names this repo's journals use, so a change
        to the house format cannot silently empty the roadmap again."""
        body = ("### What happened\n\n- Did things.\n\n"
                "### Outcome\n\nThe system changed like so.\n\n"
                "### What's next\n\n- More.\n\n### Open questions\n\n- Any?\n\n"
                "### Notes\n\n- Aside.\n")
        self.assertEqual(session._journal_outcome(body),
                         "The system changed like so.")


class RecentlyShippedRenderTest(unittest.TestCase):
    def _journals(self, *pairs):
        return [({"session-id": sid, "started": started, "title": sid},
                 body) for sid, started, body in pairs]

    def test_outcomes_render_newest_first(self):
        js = self._journals(
            ("older", "2026-08-01T00:00:00Z", "### Outcome\n\nOlder thing.\n"),
            ("newer", "2026-08-20T00:00:00Z", "### Outcome\n\nNewer thing.\n"))
        with mock.patch.object(session, "_load_journals", return_value=js):
            out = session._render_recently_shipped()
        self.assertLess(out.index("Newer thing."), out.index("Older thing."))

    def test_a_session_without_an_outcome_contributes_nothing(self):
        js = self._journals(
            ("quiet", "2026-08-01T00:00:00Z", "### What happened\n\n- Stuff.\n"),
            ("loud", "2026-08-02T00:00:00Z", "### Outcome\n\nSomething changed.\n"))
        with mock.patch.object(session, "_load_journals", return_value=js):
            out = session._render_recently_shipped()
        self.assertIn("Something changed.", out)
        self.assertNotIn("quiet", out)

    def test_no_outcomes_at_all_says_so_rather_than_rendering_empty(self):
        """An empty region is indistinguishable from a broken renderer."""
        js = self._journals(("q", "2026-08-01T00:00:00Z", "### What happened\n"))
        with mock.patch.object(session, "_load_journals", return_value=js):
            out = session._render_recently_shipped()
        self.assertIn("No session has recorded", out)

    def test_the_section_is_capped(self):
        js = self._journals(*[(f"s{i}", f"2026-08-{i:02d}T00:00:00Z",
                               f"### Outcome\n\nThing {i}.\n") for i in range(1, 20)])
        with mock.patch.object(session, "_load_journals", return_value=js):
            out = session._render_recently_shipped()
        self.assertEqual(out.count("### Outcome"), 0)
        self.assertLessEqual(sum(1 for ln in out.splitlines() if ln.startswith("**")),
                             session.ROADMAP_OUTCOME_KEEP)

    def test_an_unreadable_journal_set_degrades_to_empty(self):
        """This runs inside the close. It must never be the thing that fails one."""
        with mock.patch.object(session, "_load_journals", side_effect=OSError("boom")):
            self.assertEqual(session._render_recently_shipped(), "")


class RegionMappingTest(unittest.TestCase):
    """Regions are filled by HEADING, not by position — the change that made inserting
    `Recently shipped` safe."""

    def test_each_known_heading_maps(self):
        for h, key in (("## Now — where we are", "now"),
                       ("## Recently shipped — outcomes", "recently shipped"),
                       ("## Next — immediate", "next"),
                       ("## Backlog — the full plan", "backlog"),
                       ("## Operational obligations — what's owed", "operational obligations")):
            self.assertEqual(session._roadmap_region_key(h), key, h)

    def test_an_unknown_heading_maps_to_nothing(self):
        self.assertIsNone(session._roadmap_region_key("## Some new section"))

    def test_inserting_a_region_does_not_shift_the_others(self):
        """The whole reason for heading-keyed mapping. Under positional mapping, adding
        Recently-shipped (which sits FIRST) would render Next's content into it and shift
        everything down a slot — every section wrong, nothing erroring."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / "ROADMAP.md").write_text(
                f"# R\n\n## Recently shipped — outcomes\n\n{BEGIN}\n{END}\n\n"
                f"## Next — immediate\n\n{BEGIN}\n{END}\n\n"
                f"## Backlog — the full plan\n\n{BEGIN}\n{END}\n", encoding="utf-8")
            with mock.patch.object(session, "ROOT", root), \
                 mock.patch.object(session, "_render_recently_shipped",
                                   return_value="RECENT\n"), \
                 mock.patch.object(session, "_wi_render_next_section",
                                   return_value="NEXT\n"), \
                 mock.patch.object(session, "_wi_render_backlog_section",
                                   return_value="BACKLOG\n"), \
                 mock.patch.object(session, "_wi_only_generated_differs",
                                   return_value=False):
                with redirect_stdout(io.StringIO()):
                    session.cmd_wi_render(argparse.Namespace())
            text = (root / "ROADMAP.md").read_text(encoding="utf-8")
            self.assertLess(text.index("RECENT"), text.index("NEXT"))
            self.assertLess(text.index("NEXT"), text.index("BACKLOG"))
            # and each sits under its own heading
            self.assertIn("## Recently shipped — outcomes\n\n" + BEGIN + "\n\nRECENT", text)

    def test_a_region_under_an_unrecognised_heading_refuses_and_writes_nothing(self):
        """A region it cannot name is one it must not fill — the same
        never-invent-structure rule the renderer already had for marker counts."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            p = root / "ROADMAP.md"
            p.write_text(f"# R\n\n## Mystery section\n\n{BEGIN}\n{END}\n",
                         encoding="utf-8")
            before = p.read_text(encoding="utf-8")
            buf = io.StringIO()
            with mock.patch.object(session, "ROOT", root):
                with redirect_stdout(buf):
                    session.cmd_wi_render(argparse.Namespace())
            self.assertEqual(p.read_text(encoding="utf-8"), before)
            self.assertIn("does not recognise", buf.getvalue())


class FinishLineRegionTest(unittest.TestCase):
    """WI-0285 — `## Now` opens with the finish-line board's verdict.

    The board landed trustworthy on 2026-09-04 and then sat behind a command nobody runs:
    its answer to "is the harness mature yet" existed, was correct, and reached no one. The
    fix is a surface, and the surface is a generated region — which means it inherits every
    property the other four regions already have (trunk-only writer, heading-keyed,
    optional) and must not break the one property `## Now` has and they do not: its prose
    is hand-authored, and the lane predicates use "did anything outside the markers move?"
    as their proxy for "did this lane author prose?".
    """

    def _render(self, **kw):
        with mock.patch.object(session.subprocess, "run", **kw) as m:
            return session._render_finish_line(), m

    def test_the_boards_own_line_is_what_renders(self):
        """VERBATIM, not re-implemented. `curate/finish_line.py` is the one formatter for
        this verdict; a second copy of the wording here would drift from the board it
        claims to summarise and the drift would be silent."""
        done = subprocess.CompletedProcess([], 0, stdout="finish-line: 2/6 green\n", stderr="")
        with mock.patch.object(session.Path, "is_file", return_value=True):
            out, m = self._render(return_value=done)
        self.assertEqual(out, "finish-line: 2/6 green\n")
        self.assertEqual(m.call_args.kwargs["timeout"], session.FINISH_LINE_TIMEOUT_S)

    def test_it_is_bounded_in_wall_clock(self):
        """The board caps each git call at 30s and makes no promise about the total — it
        shells git once per fleet member. This producer runs inside `run_compile`, which
        every land calls on the trunk, so an unbounded probe here would turn a slow fleet
        walk into a slow land: the exact number the board's own FL8 row measures."""
        boom = subprocess.TimeoutExpired(cmd="finish_line", timeout=30)
        buf = io.StringIO()
        with mock.patch.object(session.Path, "is_file", return_value=True):
            with redirect_stdout(buf):
                out, _ = self._render(side_effect=boom)
        self.assertIsNone(out)
        self.assertIn("did not answer", buf.getvalue())

    def test_the_cap_is_strictly_under_the_land_lock_budget(self):
        """WI-0357, and the defect is that the old literal was EQUAL to the budget.

        `run_compile` is called from inside the land's serialized section, so this
        producer spends the section's allowance. At 30 it could spend all of it on one
        region of one generated view before the land had merged anything — a ceiling that
        permits a total breach of the budget it sits inside is not a ceiling. The
        assertion is on the RELATIONSHIP, not on 10: the number has to keep moving with
        the budget, which is why it is derived rather than written twice."""
        self.assertLess(session.FINISH_LINE_TIMEOUT_S,
                        session.LAND_LOCK_HOLD_BUDGET_SECONDS)
        # And not merely under it — the section still has to merge, sync and push after
        # this producer returns, so no single one of them may own most of the budget.
        self.assertLessEqual(session.FINISH_LINE_TIMEOUT_S,
                             session.LAND_LOCK_HOLD_BUDGET_SECONDS / 2)

    def test_a_timeout_keeps_the_verdict_the_region_already_carries(self):
        """The acceptance for the None contract, driven through the real renderer.

        Not just "returns None": what a reader and the trunk actually see is the previous
        verdict, and the file is UNCHANGED — which is what stops a busy machine authoring
        one `docs(roadmap)` commit per land as a placeholder and a verdict take turns."""
        with tempfile.TemporaryDirectory() as tmp:
            rootd = pathlib.Path(tmp)
            p = rootd / "ROADMAP.md"
            before = (f"# R\n\n## Now — where we are\n\n{BEGIN}\n\n"
                      f"finish-line: 2/6 capabilities green\n\n{END}\n\nProse.\n")
            p.write_text(before, encoding="utf-8")
            with mock.patch.object(session, "ROOT", rootd), \
                 mock.patch.object(session, "_render_finish_line", return_value=None), \
                 mock.patch.object(session, "_wi_only_generated_differs",
                                   return_value=False):
                with redirect_stdout(io.StringIO()):
                    session.cmd_wi_render(argparse.Namespace())
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_a_stalled_board_leaves_the_trunks_roadmap_untouched_end_to_end(self):
        """The whole chain, mocked only at the process boundary.

        The two tests either side of this one each prove half — the producer returns None,
        and the splice honours None — and a mutation run showed that is not the same
        thing: replacing the producer's `return None` with a placeholder passed the
        keeps-the-verdict test, because that test hands the renderer a None it did not
        have to decide on. Here the only thing patched is `subprocess.run`, so the
        producer really has to choose and the splice really has to act on the choice."""
        with tempfile.TemporaryDirectory() as tmp:
            rootd = pathlib.Path(tmp)
            (rootd / "curate").mkdir()
            (rootd / "curate" / "finish_line.py").write_text("", encoding="utf-8")
            p = rootd / "ROADMAP.md"
            before = (f"# R\n\n## Now — where we are\n\n{BEGIN}\n\n"
                      f"finish-line: 2/6 capabilities green\n\n{END}\n\nProse.\n")
            p.write_text(before, encoding="utf-8")
            real_run = subprocess.run

            def stall_only_the_board(cmd, *a, **kw):
                # SELECTIVE, because `sh` runs git through this same name: a blanket
                # side_effect stalls the renderer's own branch check and the test then
                # proves nothing about the board.
                if any(session.FINISH_LINE_SCRIPT.split("/")[-1] in str(c) for c in cmd):
                    raise subprocess.TimeoutExpired(
                        cmd=cmd, timeout=session.FINISH_LINE_TIMEOUT_S)
                return real_run(cmd, *a, **kw)

            with mock.patch.object(session, "ROOT", rootd), \
                 mock.patch.object(session, "_compile_would_noop", return_value=""), \
                 mock.patch.object(session.subprocess, "run",
                                   side_effect=stall_only_the_board), \
                 mock.patch.object(session, "_wi_only_generated_differs",
                                   return_value=False):
                with redirect_stdout(io.StringIO()):
                    session.cmd_wi_render(argparse.Namespace())
            self.assertEqual(p.read_text(encoding="utf-8"), before)

    def test_an_empty_string_still_empties_the_region(self):
        """`None` is a THIRD answer, not a soft empty string. A producer that genuinely
        has nothing to show must still be able to clear a region — collapsing the two
        would make a real emptying indistinguishable from a transient failure, and would
        pin whatever stale content happened to be there the last time one occurred."""
        with tempfile.TemporaryDirectory() as tmp:
            rootd = pathlib.Path(tmp)
            p = rootd / "ROADMAP.md"
            p.write_text(f"# R\n\n## Now — where we are\n\n{BEGIN}\n\nOLD VERDICT\n\n"
                         f"{END}\n\nProse.\n", encoding="utf-8")
            with mock.patch.object(session, "ROOT", rootd), \
                 mock.patch.object(session, "_render_finish_line", return_value=""), \
                 mock.patch.object(session, "_wi_only_generated_differs",
                                   return_value=False):
                with redirect_stdout(io.StringIO()):
                    session.cmd_wi_render(argparse.Namespace())
            self.assertNotIn("OLD VERDICT", p.read_text(encoding="utf-8"))

    def test_a_kept_region_round_trips_through_the_writer(self):
        """The framing-strip is exact, or "keep" would author a commit every time it fired.

        `_roadmap_region_content` removes the two leading newlines and the one trailing
        newline the splice itself writes — never `.strip()`, which would also eat the
        newline the producer's own last line ends with and so shift the file by a byte on
        every kept render."""
        content = "finish-line: 2/6 capabilities green\n"
        body = "\n\n" + content + "\n"          # exactly what the splice emits
        self.assertEqual(session._roadmap_region_content(body), content)

    def test_a_spawn_failure_is_a_line_not_an_exception(self):
        """`run_compile` suppresses exceptions around the WHOLE render, so an escape from
        here would drop every other region too — the roadmap losing its compiled outcome
        history because a subprocess would not start."""
        with mock.patch.object(session.Path, "is_file", return_value=True):
            out, _ = self._render(side_effect=OSError("no such interpreter"))
        self.assertIn("could not be run", out)
        self.assertIn("OSError", out)

    def test_a_member_without_the_board_is_told_so(self):
        """`sessionlib` is byte-identical fleet substrate; `curate/` is not shipped. A
        member that adds the markers without the board gets a sentence, not a traceback."""
        with mock.patch.object(session.Path, "is_file", return_value=False):
            with mock.patch.object(session.subprocess, "run") as m:
                out = session._render_finish_line()
        m.assert_not_called()
        self.assertIn(session.FINISH_LINE_SCRIPT, out)
        self.assertIn("federation-only", out)

    def test_the_rendered_region_carries_nothing_that_moves_on_its_own(self):
        """THE CHURN PROPERTY, and it is why there is no timestamp. `cmd_wi_render` commits
        whenever the file differs from HEAD, and `run_compile` runs at every session start,
        every session end and every land — so a date or an age in this region would author
        a ROADMAP commit on every one of them, forever, saying nothing."""
        done = subprocess.CompletedProcess([], 0, stdout="finish-line: 2/6 green\n", stderr="")
        with mock.patch.object(session.Path, "is_file", return_value=True):
            first, _ = self._render(return_value=done)
            second, _ = self._render(return_value=done)
        self.assertEqual(first, second)

    def test_the_hand_authored_prose_under_now_survives_a_render(self):
        """The acceptance, stated as bytes. The region sits ABOVE the prose, so everything
        outside the markers — the as-of block, the `## Now` paragraphs, the tail — is
        byte-identical before and after."""
        with tempfile.TemporaryDirectory() as tmp:
            rootd = pathlib.Path(tmp)
            p = rootd / "ROADMAP.md"
            before = (f"# R\n\n## Now — where we are\n\n{BEGIN}\n\n_placeholder_\n\n{END}\n\n"
                      f"The federation has all four muscles working.\n\n"
                      f"## Next — immediate\n\n{BEGIN}\n{END}\n\nOpen policy questions: none.\n")
            p.write_text(before, encoding="utf-8")
            with mock.patch.object(session, "ROOT", rootd), \
                 mock.patch.object(session, "_render_finish_line",
                                   return_value="finish-line: 2/6 green\n"), \
                 mock.patch.object(session, "_wi_render_next_section", return_value="NEXT\n"), \
                 mock.patch.object(session, "_wi_only_generated_differs", return_value=False):
                with redirect_stdout(io.StringIO()):
                    session.cmd_wi_render(argparse.Namespace())
            after = p.read_text(encoding="utf-8")
            self.assertIn("finish-line: 2/6 green", after)
            self.assertEqual(session._strip_generated_regions(before),
                             session._strip_generated_regions(after))

    def test_a_lane_renders_no_finish_line_either(self):
        """The region is new; the writer guard is not. Pinned here anyway because this is
        the first region under a heading whose other content a lane is allowed to be wrong
        about — WI-0260 was a lane committing its own narrower view of a generated
        region, and the board reads a FLEET, which a lane sees even less of."""
        with tempfile.TemporaryDirectory() as tmp:
            rootd = pathlib.Path(tmp)
            p = rootd / "ROADMAP.md"
            before = f"# R\n\n## Now — where we are\n\n{BEGIN}\n{END}\n\nProse.\n"
            p.write_text(before, encoding="utf-8")
            buf = io.StringIO()
            with mock.patch.object(session, "ROOT", rootd), \
                 mock.patch.object(session, "_compile_would_noop",
                                   return_value="worktree-poga-7"), \
                 mock.patch.object(session, "_render_finish_line") as producer:
                with redirect_stdout(buf):
                    session.cmd_wi_render(argparse.Namespace())
            producer.assert_not_called()
            self.assertEqual(p.read_text(encoding="utf-8"), before)
            self.assertIn("NOT rendered", buf.getvalue())


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          check=True, capture_output=True, text=True)


@unittest.skipIf(GIT is None, "git unavailable")
class TwoLanesTest(unittest.TestCase):
    """THE ACCEPTANCE TEST, named by the item: *two lanes closing on the same day without
    touching the same file* — "exercise it, do not assert it".

    Before ADR-0105 both lanes wrote a Recently-shipped bullet into `ROADMAP.md`, so
    their branches carried divergent edits to one file and the second to land conflicted.
    Now each writes its outcome into its OWN journal, which cannot collide because a
    journal is per-session. This drives real git worktrees and a real merge."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "sessions" / "journal").mkdir(parents=True)
        # Tracked, or git will not materialise the directory in either lane worktree and
        # both closes fail on a missing path rather than on the thing under test.
        (self.main / "sessions" / "journal" / ".keep").write_text("", encoding="utf-8")
        (self.main / "ROADMAP.md").write_text(
            f"# R\n\n## Recently shipped — outcomes\n\n{BEGIN}\n{END}\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _lane(self, n):
        path = self.main / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.main, "worktree", "add", "-q", "-b", f"lane-{n}", str(path), "main")
        return path

    def _close_writing_journal(self, lane, sid, outcome):
        """A close under ADR-0105: the outcome goes in the lane's OWN journal."""
        j = lane / "sessions" / "journal" / f"{sid}.md"
        j.write_text(f"---\nsession-id: {sid}\n---\n\n### Outcome\n\n{outcome}\n",
                     encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"close {sid}")

    def _close_writing_roadmap(self, lane, bullet):
        """A close under the OLD rule: every session hand-edits the shared file."""
        p = lane / "ROADMAP.md"
        p.write_text(p.read_text(encoding="utf-8").replace(
            END, f"\n- {bullet}\n{END}"), encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", "roadmap")

    def _merge_both(self, laneA, laneB):
        """Land A, then B. Returns B's merge returncode — 0 means no conflict."""
        _git(self.main, "merge", "--no-edit", "-q", "lane-1")
        r = subprocess.run([GIT, "-C", str(self.main), "merge", "--no-edit", "-q",
                            "lane-2"], capture_output=True, text=True)
        return r.returncode

    def test_the_old_rule_conflicts_which_is_the_defect(self):
        """Pins the DEFECT, so this file proves the fix changed something. Without it,
        the passing test below could pass for reasons unrelated to the change."""
        a, b = self._lane(1), self._lane(2)
        self._close_writing_roadmap(a, "A shipped a thing.")
        self._close_writing_roadmap(b, "B shipped a thing.")
        self.assertNotEqual(self._merge_both(a, b), 0,
                            "two lanes editing ROADMAP.md must conflict — that is WI-0108")

    def test_two_lanes_closing_the_same_day_no_longer_touch_the_same_file(self):
        a, b = self._lane(1), self._lane(2)
        self._close_writing_journal(a, "20260830T0900Z-devbox-aaaa", "A changed a thing.")
        self._close_writing_journal(b, "20260830T1000Z-devbox-bbbb", "B changed a thing.")
        self.assertEqual(self._merge_both(a, b), 0,
                         "outcomes live in per-session journals; nothing collides")

    def test_both_outcomes_survive_the_land(self):
        """Not merely 'no conflict' — the point is that neither lane's prose is lost,
        which is what the rejected trunk-side auto-resolve would have done."""
        a, b = self._lane(1), self._lane(2)
        self._close_writing_journal(a, "20260830T0900Z-devbox-aaaa", "A changed a thing.")
        self._close_writing_journal(b, "20260830T1000Z-devbox-bbbb", "B changed a thing.")
        self._merge_both(a, b)
        jdir = self.main / "sessions" / "journal"
        bodies = "\n".join(p.read_text(encoding="utf-8") for p in jdir.glob("*.md"))
        self.assertIn("A changed a thing.", bodies)
        self.assertIn("B changed a thing.", bodies)


if __name__ == "__main__":
    unittest.main()
