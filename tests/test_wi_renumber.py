"""WI-0169 — a colliding WI number is REMAPPED by the land, not handed back as an errand.

THE FAILURE, three times. Two lanes draw the same WI number independently; the land's
counter gate catches it — correctly, because a number drawn inside a lane cannot see what
a peer lands between the draw and the land. But the gate's only remedy was `wi-next`,
which hands you a number and leaves everything else by hand. Session ~174 paid the full
price: nine colliding ids, nine sequential land attempts (the land names one, you rename
it, you re-run, it names the next), and a script written for the occasion.

The properties pinned here:

  A. THE DRAW SEES THE TRUNK. `_counter_existing_max` scans the lane's WORKING TREE, which
     is frozen at the lane's base commit, so a draw from a lane could return a number the
     trunk had already landed — the allocator manufacturing the very collision the gate
     exists to catch. Pinned WITH ITS NEGATIVE CONTROL: the same fixture, with the trunk
     scan disabled, draws the colliding number.
  B. THE GATE REPORTS EVERY COLLISION AT ONCE. Nine collisions are one message naming
     nine, not nine messages naming one each. The information was always there on the
     first pass; only the report was sequential.
  C. THE WHOLE IDENTITY MOVES — file, H1, notes directory, `blocked-by` edges, the
     coordination claim, the reservation. Each one is separately load-bearing; the claim
     and the notes are the two a person doing this by hand misses every time.
  D. CITATIONS FOLLOW LANE PROVENANCE, LINE BY LINE. After a remap the old number still
     names a real item on the trunk, so a blanket replace is wrong in BOTH directions.
     A line THIS LANE ADDED means the lane's numbering; a line already on the trunk means
     the trunk's item. Pinned in both directions, and at line granularity inside one file.
  E. HISTORY IS REPORTED, NEVER REWRITTEN. Journals and the compiled handoff are record,
     not guidance.
  F. THE PROVENANCE RECORD SURVIVES THE CITATION PASS. It names the OLD id on purpose;
     written before the pass it would rewrite itself into "WI-0003 -> WI-0003" and destroy
     the one artifact whose whole job is to survive the renumber. Ordering bug by
     construction, so it is pinned rather than trusted.
  G. THE LAND CLEARS ITS OWN COLLISION — with the negative control that without the
     remap the same fixture still refuses. A green gate proves nothing unless the red is
     shown to be real.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
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


def _item_text(wid, title, blocked=""):
    return (f"# {wid}: {title}\n\n"
            f"- status: open\n- section: next\n- blocked-by: {blocked}\n"
            f"- group: \n- source: \n- impact: fix\n- version: \n\n"
            f"the body of {wid}.\n")


@unittest.skipUnless(GIT, "git required")
class RenumberBase(unittest.TestCase):
    """A real main checkout, a real linked lane worktree, and a real collision.

    Deliberately not mocked. Every property here is a property of what git actually
    reports — which lines a lane added, which files the trunk already carries — and a
    fake would be asserting my model of git rather than git.
    """

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        (self.main / "work-items").mkdir(parents=True)
        (self.main / "sessions" / "journal").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")

        # BASE — what both sides share. `trunkdoc.md` carries a WI-0002 citation that
        # predates the lane, so it is the negative control for the citation rule.
        (self.main / "work-items" / "WI-0001-first.md").write_text(
            _item_text("WI-0001", "first"), encoding="utf-8")
        (self.main / "trunkdoc.md").write_text(
            "trunk prose that cites WI-0002 and means the trunk's item.\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "base")

        # The lane branches HERE — before the trunk lands its own WI-0002.
        self.lane = self.main / ".claude" / "worktrees" / "poga-9"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-9",
             str(self.lane), "HEAD")

        # TRUNK advances: it lands its own WI-0002, invisible to the lane's tree.
        (self.main / "work-items" / "WI-0002-trunk-side.md").write_text(
            _item_text("WI-0002", "the trunk's own second item"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands WI-0002")
        self.parent = subprocess.run(
            ["git", "-C", str(self.main), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.lane
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "renumbertest"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers -------------------------------------------------------
    def _lane_write(self, rel, text):
        p = self.lane / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def _lane_commit(self, msg="lane work"):
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", msg)
        return subprocess.run(["git", "-C", str(self.lane), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    def _collide(self):
        """The lane files its own WI-0002. Returns the lane tip."""
        self._lane_write("work-items/WI-0002-lane-side.md",
                         _item_text("WI-0002", "the lane's own second item"))
        return self._lane_commit("lane files WI-0002")

    def _read(self, rel):
        return (self.lane / rel).read_text(encoding="utf-8")

    def _names(self):
        return sorted(p.name for p in (self.lane / "work-items").glob("WI-*.md"))


class TheDrawSeesTheTrunkTest(RenumberBase):
    """Property A — and the control that proves the fixture really is exposed."""

    def test_a_draw_from_a_lane_skips_a_number_the_trunk_already_landed(self):
        c = session._counter("wi")
        # The lane's own tree tops out at WI-0001; the trunk is at WI-0002.
        self.assertEqual(session._counter_existing_max(c), 1)
        self.assertEqual(session._counter_trunk_max(c), 2)
        self.assertEqual(session._counter_reserve_next(c), 3)

    def test_without_the_trunk_scan_the_same_draw_collides(self):
        # THE NEGATIVE CONTROL. Without it, the test above could equally mean "the
        # fixture never had a trunk-only number in the first place".
        c = session._counter("wi")
        with mock.patch.object(session, "_counter_trunk_max", return_value=0):
            self.assertEqual(session._counter_reserve_next(c), 2)   # collides


class TheGateReportsEveryCollisionAtOnceTest(RenumberBase):
    """Property B — the nine-sequential-refusals defect."""

    def _collide_many(self):
        for n in (2, 3, 4):
            (self.main / "work-items" / f"WI-{n:04d}-trunk.md").write_text(
                _item_text(f"WI-{n:04d}", f"trunk {n}"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands three")
        parent = subprocess.run(["git", "-C", str(self.main), "rev-parse", "HEAD"],
                                capture_output=True, text=True,
                                check=True).stdout.strip()
        for n in (2, 3, 4):
            self._lane_write(f"work-items/WI-{n:04d}-lane.md",
                             _item_text(f"WI-{n:04d}", f"lane {n}"))
        return self._lane_commit("lane files three"), parent

    def test_one_message_names_all_three(self):
        tip, parent = self._collide_many()
        ok, msg = session._all_counter_land_gates(tip, parent)
        self.assertFalse(ok)
        for n in (2, 3, 4):
            self.assertIn(f"WI-{n:04d}-lane.md", msg,
                          f"the refusal must name every collision, and it omitted {n}")
        self.assertIn("3 WI number(s)", msg)

    def test_the_collision_set_is_computed_in_one_pass(self):
        tip, parent = self._collide_many()
        found = session._counter_trunk_collisions(session._counter("wi"), tip, parent)
        self.assertEqual([n for n, _ in found], [2, 3, 4])


class TheWholeIdentityMovesTest(RenumberBase):
    """Property C — every field the hand-repair misses."""

    def setUp(self):
        super().setUp()
        self._collide()
        # a note record, a blocking sibling, and a live claim on the colliding id
        self._lane_write("work-items/notes/WI-0002/20260101T000000000000Z-t-aa.md",
                         "a note that belongs to the lane's WI-0002.\n")
        self._lane_write("work-items/WI-0005-blocked.md",
                         _item_text("WI-0005", "waits on it", blocked="WI-0002"))
        self.tip = self._lane_commit("notes + blocker")
        self.identity = session._coord_identity()
        session._coord_try_acquire("claims", "WI-0002", self.identity,
                                   session.CLAIM_TTL_SECONDS, title="the lane's item")

    def _run(self):
        with redirect_stdout(io.StringIO()):
            mapping, report = session._counter("wi").renumberer([("WI-0002", 7)], self.parent)
        return mapping, report

    def test_the_file_and_its_h1_move_together(self):
        self._run()
        self.assertIn("WI-0007-the-lane-s-own-second-item.md", self._names())
        self.assertNotIn("WI-0002-lane-side.md", self._names())
        self.assertTrue(self._read("work-items/WI-0007-the-lane-s-own-second-item.md")
                        .startswith("# WI-0007: the lane's own second item"))

    def test_the_notes_directory_travels_with_the_id(self):
        self._run()
        self.assertFalse((self.lane / "work-items/notes/WI-0002").exists())
        moved = sorted((self.lane / "work-items/notes/WI-0007").glob("*.md"))
        self.assertTrue(any("belongs to the lane's WI-0002"
                            in p.read_text(encoding="utf-8") for p in moved))

    def test_the_blocked_by_edge_follows(self):
        # The only MACHINE-VALIDATED cross-reference in the store: miss it and
        # `wi-check` goes red on a store the renumber just claimed to fix.
        self._run()
        name = session._wi_filename_for("WI-0005")
        self.assertIn("- blocked-by: WI-0007",
                      self._read(f"work-items/{name}"))

    def test_the_claim_follows_the_id(self):
        self._run()
        claims = session._coord_list("claims")
        self.assertIn("WI-0007", claims)
        self.assertNotIn("WI-0002", claims)

    def test_another_lanes_claim_is_never_touched(self):
        session._coord_release("claims", "WI-0002", self.identity, force=True)
        d = session._coord_dir("claims", create=True)
        (d / "WI-0002.json").write_text(json.dumps({
            "session_id": "worktree-poga-4", "journal": "someone-else",
            "branch": "worktree-poga-4", "expires_at": 9e18, "name": "WI-0002",
        }), encoding="utf-8")
        _mapping, report = self._run()
        self.assertIn("WI-0002", session._coord_list("claims"),
                      "a renumber must never release another lane's claim")
        self.assertTrue(any("LEFT ALONE" in line for line in report))


class CitationsFollowLaneProvenanceTest(RenumberBase):
    """Property D — the half that makes this a fix rather than a cost transfer."""

    def setUp(self):
        super().setUp()
        self._collide()
        self._lane_write("lanedoc.md",
                         "lane prose citing WI-0002, which means the lane's item.\n")
        # A file that EXISTED at the trunk and to which the lane appends one line. Only
        # the appended line may move: file-level provenance would rewrite both.
        self._lane_write("trunkdoc.md",
                         "trunk prose that cites WI-0002 and means the trunk's item.\n"
                         "lane-appended line citing WI-0002.\n")
        self._lane_write("sessions/journal/20260101T0000Z-t-aa.md",
                         "the journal says WI-0002 and is record, not guidance.\n")
        self.tip = self._lane_commit("citations")

    def _run(self):
        with redirect_stdout(io.StringIO()):
            return session._counter("wi").renumberer([("WI-0002", 7)], self.parent)

    def test_a_lane_authored_citation_follows_the_remap(self):
        self._run()
        self.assertIn("WI-0007", self._read("lanedoc.md"))
        self.assertNotIn("WI-0002", self._read("lanedoc.md"))

    def test_a_trunk_line_is_left_alone(self):
        # THE OTHER DIRECTION, and the one a blanket replace gets wrong: after the remap
        # WI-0002 still names a real item on the trunk, and this line means that one.
        self._run()
        first = self._read("trunkdoc.md").splitlines()[0]
        self.assertIn("WI-0002", first)
        self.assertIn("means the trunk's item", first)

    def test_only_the_lane_added_line_moves_inside_a_shared_file(self):
        self._run()
        lines = self._read("trunkdoc.md").splitlines()
        self.assertIn("WI-0002", lines[0])          # trunk's line, untouched
        self.assertIn("WI-0007", lines[1])          # the lane's appended line, remapped

    def test_history_is_reported_and_never_rewritten(self):
        _mapping, report = self._run()
        j = self._read("sessions/journal/20260101T0000Z-t-aa.md")
        self.assertIn("WI-0002", j)
        self.assertTrue(any("history file(s)" in line for line in report),
                        "history left alone must be SAID, not silently skipped")

    def test_the_provenance_record_still_names_the_old_id(self):
        # Property F. Written before the citation pass, this record would rewrite itself
        # into "WI-0007 -> WI-0007" — destroying the only thing that lets a reader
        # arriving from a journal resolve the old number.
        self._run()
        recs = sorted((self.lane / "work-items/notes/WI-0007").glob("*.md"))
        self.assertTrue(recs, "a renumbered item must carry a provenance record")
        body = "\n".join(p.read_text(encoding="utf-8") for p in recs)
        self.assertIn("WI-0002", body)
        self.assertIn("WI-0007", body)


class TheLandClearsItsOwnCollisionTest(RenumberBase):
    """Property G — with the control that proves the red was real."""

    def test_without_the_remap_the_gate_refuses(self):
        # THE NEGATIVE CONTROL, and it runs first on purpose.
        tip = self._collide()
        ok, msg = session._all_counter_land_gates(tip, self.parent)
        self.assertFalse(ok)
        self.assertIn("0002", msg)

    def test_the_remap_clears_it_and_the_gate_then_passes(self):
        tip = self._collide()
        with redirect_stdout(io.StringIO()):
            newtip, _report = session._auto_renumber_collisions(tip, self.parent)
        # Under the test guard nothing commits, so the remap lives in the working tree;
        # commit it the way the land's own `add -A` would and re-gate.
        tip = self._lane_commit("remap")
        ok, msg = session._all_counter_land_gates(tip, self.parent)
        self.assertTrue(ok, f"the remap should have cleared the collision: {msg}")
        self.assertNotIn("WI-0002-lane-side.md", self._names())

    def test_a_remap_never_reuses_the_trunks_number(self):
        tip = self._collide()
        with redirect_stdout(io.StringIO()):
            session._auto_renumber_collisions(tip, self.parent)
        drawn = [n for n in self._names() if not n.startswith("WI-0001")]
        self.assertTrue(drawn)
        self.assertFalse(any(n.startswith("WI-0002") for n in drawn),
                         "the replacement must not be the number that collided")

    def test_a_number_held_by_a_live_sibling_is_not_remapped(self):
        # Only TRUNK duplicates are the renumber's business. A live sibling's reservation
        # may still land, so redrawing around it is a guess about someone else's work.
        self._lane_write("work-items/WI-0006-lane.md",
                         _item_text("WI-0006", "sixth"))
        tip = self._lane_commit("sixth")
        found = session._counter_trunk_collisions(session._counter("wi"), tip,
                                                  self.parent)
        self.assertEqual(found, [])


class RenameDetectionCannotHideACollisionTest(RenumberBase):
    """A hole in the EXISTING gate, found by building this suite's fixture.

    `_counter_new_and_trunk` asked git for `--diff-filter=A`, and rename detection is on
    by default. The collision scenario is exactly the shape that trips it: the trunk
    landed its own WI-0002 after this lane branched, so against the trunk tip the trunk's
    file reads as DELETED and the lane's same-numbered file reads as ADDED — and git,
    seeing two work items with the same field skeleton and a similar body, pairs them as
    one rename. `--diff-filter=A` then matches nothing and the gate passes a real
    collision.

    Content-dependent, which is what makes it dangerous rather than merely wrong: two
    dissimilar items are caught and two similar ones are waved through. The likeliest way
    for two lanes to collide is filing the same KIND of item, which is also the likeliest
    way for their files to pair.
    """

    def _near_identical(self):
        # Same title as the trunk's item but for one character — realistic for two lanes
        # filing the same defect, and comfortably over git's similarity threshold.
        self._lane_write("work-items/WI-0002-lane-side.md",
                         _item_text("WI-0002", "the trunk's own second item too"))
        return self._lane_commit("near-identical filing")

    def test_a_near_identical_item_is_still_caught(self):
        tip = self._near_identical()
        found = session._counter_trunk_collisions(session._counter("wi"), tip,
                                                  self.parent)
        self.assertEqual([n for n, _ in found], [2],
                         "rename detection must not hide a real collision")

    def test_the_control_shows_what_rename_detection_does(self):
        # THE CONTROL, and it names the mechanism rather than asserting the story: with
        # renames left ON, git reports the pair as a rename and the add is invisible.
        # If this ever stops being true the fix above has become unnecessary, and that is
        # worth being told rather than carrying forever.
        tip = self._near_identical()
        r = session.sh(["git", "diff", "--name-only", "--diff-filter=A",
                        self.parent, tip, "--", "work-items/"], check=False)
        self.assertEqual(r.stdout.strip(), "",
                         "control: with rename detection on, the add reports as a rename")


class TheOptOutIsRespectedTest(unittest.TestCase):
    """`--no-renumber` restores the refusal. Auto is the default; the flag is the escape,
    not the other way round — the operator's call, and the reason is that a fix ending in "and
    then run X" leaves X as the remaining defect."""

    def setUp(self):
        self._save = {k: getattr(session, k) for k in ("CFG",)}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "T", "architect_id": "t"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def _merge(self, **kw):
        # `keep_open` since WI-0288 R2: `merge` closes by default, and the subject here
        # is what the LANDER is asked to do about colliding numbers — not the close. The
        # continuing form is the one that reaches `_land_worktree_lane` directly.
        args = argparse.Namespace(dry_run=False, commit=None, push=False, focus=None,
                                  blocked=None, resolve="generated", keep_open=True, **kw)
        with mock.patch.object(session, "_land_worktree_lane") as land, \
                mock.patch.object(session, "_on_worktree_lane", return_value=True), \
                mock.patch.object(session, "_refuse_git_on_fixture"), \
                mock.patch.object(session, "role_doc_version", return_value="1.0.0"), \
                mock.patch.object(session, "_current_branch",
                                  return_value="worktree-poga-9"):
            session.cmd_merge(args)
        return land.call_args.kwargs

    def test_a_plain_merge_renumbers(self):
        self.assertIs(self._merge(no_renumber=False)["renumber"], True)

    def test_no_renumber_turns_it_off(self):
        self.assertIs(self._merge(no_renumber=True)["renumber"], False)


if __name__ == "__main__":
    unittest.main()
