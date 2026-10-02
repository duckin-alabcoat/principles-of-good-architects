"""WI-0245 — the OPS counter can renumber its own collisions, like WI already could.

WHAT WAS MISSING. WI-0169 shipped the renumberer as a `renumberer` field on the counter
registry row, so a land remaps every trunk-colliding number it introduces for each row
that knows how. `wi` knew how. `ops` carried `renumberer=None` and the land refused those
honestly — correct, and still a hand-repair for anyone who hit it. The store shapes are
nearly the same, so the fix was mostly parameterisation; the part worth pinning is
exactly the part that is NOT the same.

The properties pinned here:

  A. THE LAND CLEARS AN OPS COLLISION — with the negative control that the identical
     fixture, with the row's renumberer taken away, still refuses. A green gate proves
     nothing unless the red is shown to be real, and here the red is one field.
  B. THE WHOLE IDENTITY MOVES — file, H1, notes directory, the reservation. The same
     three artifacts as the WI store, moved by the same code path rather than by a
     second one that has to agree with the first.
  C. THE TWO PARTS OPS DOES NOT HAVE ARE NOT INVENTED. Ops items carry no `blocked-by`
     edge and are not claimable (`_wi_claimable_items` reads the WI store plus live
     drills, so an OPS id can never hold a claim record). A renumberer generalised by
     branching on the store would be free to acquire one anyway; this asserts it does
     not, because a claim record on an id nothing can claim is invisible garbage in the
     shared coordination store rather than a loud failure.
  D. CITATIONS FOLLOW LANE PROVENANCE FOR OPS IDS TOO, in both directions — the citation
     pass is store-neutral and runs over the whole tree, so a remapped OPS number cited
     from a work item follows, and a trunk line that means the trunk's obligation does
     not.
  E. THE ADR ROW STILL REFUSES, and says why. The registry's whole point is that a row
     without a renumberer is refused by construction; generalising the renumberer must
     not have quietly made every row renumberable.

stdlib unittest: python3 -B -m unittest discover -s tests
"""

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
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _ops_text(oid, title):
    return (f"# {oid}: {title}\n\n"
            f"- status: open\n- cadence: monthly\n- last-completed: \n"
            f"- last-result: \n- due: \n- group: \n- source: \n\n"
            f"the body of {oid}.\n")


@unittest.skipUnless(GIT, "git required")
class OpsRenumberBase(unittest.TestCase):
    """A real main checkout, a real linked lane worktree, and a real OPS collision.

    Deliberately not mocked, for the same reason the WI suite is not: every property
    here is a property of what git actually reports about which lines a lane added and
    which files the trunk already carries, and a fake would assert my model of git.
    """

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        (self.main / "ops-items").mkdir(parents=True)
        (self.main / "work-items").mkdir(parents=True)
        (self.main / "sessions" / "journal").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")

        # BASE — what both sides share. `trunkdoc.md` cites OPS-0002 from before the lane
        # existed, so it is the negative control for the citation rule.
        (self.main / "ops-items" / "OPS-0001-first.md").write_text(
            _ops_text("OPS-0001", "first"), encoding="utf-8")
        (self.main / "trunkdoc.md").write_text(
            "trunk prose that cites OPS-0002 and means the trunk's obligation.\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "base")

        # The lane branches HERE — before the trunk lands its own OPS-0002.
        self.lane = self.main / ".claude" / "worktrees" / "poga-9"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-9",
             str(self.lane), "HEAD")

        # TRUNK advances: it lands its own OPS-0002, invisible to the lane's tree.
        (self.main / "ops-items" / "OPS-0002-trunk-side.md").write_text(
            _ops_text("OPS-0002", "the trunk's own second obligation"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk lands OPS-0002")
        self.parent = subprocess.run(
            ["git", "-C", str(self.main), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True).stdout.strip()

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.lane
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "opsrenumbertest"

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
        """The lane files its own OPS-0002. Returns the lane tip."""
        self._lane_write("ops-items/OPS-0002-lane-side.md",
                         _ops_text("OPS-0002", "the lane's own second obligation"))
        return self._lane_commit("lane files OPS-0002")

    def _read(self, rel):
        return (self.lane / rel).read_text(encoding="utf-8")

    def _names(self):
        return sorted(p.name for p in (self.lane / "ops-items").glob("OPS-*.md"))


class TheLandClearsAnOpsCollisionTest(OpsRenumberBase):
    """Property A — and the control that proves the red was real."""

    def test_without_a_renumberer_the_gate_still_refuses(self):
        # THE NEGATIVE CONTROL, and it runs against the SAME fixture with the row's one
        # new field removed — so what it demonstrates is that this field is the whole
        # difference, not merely that some refusal exists somewhere.
        tip = self._collide()
        row = session._counter("ops")
        saved = row.renumberer
        row.renumberer = None
        try:
            with redirect_stdout(io.StringIO()):
                newtip, _rep = session._auto_renumber_collisions(tip, self.parent)
            self.assertIsNone(newtip, "with no renumberer nothing may be remapped")
            ok, msg = session._all_counter_land_gates(tip, self.parent)
            self.assertFalse(ok)
            self.assertIn("OPS", msg)
            self.assertIn("0002", msg)
        finally:
            row.renumberer = saved

    def test_the_remap_clears_it_and_the_gate_then_passes(self):
        tip = self._collide()
        with redirect_stdout(io.StringIO()):
            session._auto_renumber_collisions(tip, self.parent)
        # Under the test guard nothing commits, so the remap lives in the working tree;
        # commit it the way the land's own `add -A` would and re-gate.
        tip = self._lane_commit("remap")
        ok, msg = session._all_counter_land_gates(tip, self.parent)
        self.assertTrue(ok, f"the remap should have cleared the collision: {msg}")
        self.assertNotIn("OPS-0002-lane-side.md", self._names())

    def test_a_remap_never_reuses_the_trunks_number(self):
        tip = self._collide()
        with redirect_stdout(io.StringIO()):
            session._auto_renumber_collisions(tip, self.parent)
        drawn = [n for n in self._names() if not n.startswith("OPS-0001")]
        self.assertTrue(drawn)
        self.assertFalse(any(n.startswith("OPS-0002") for n in drawn),
                         "the replacement must not be the number that collided")

    def test_the_blocked_land_message_now_names_the_automatic_path(self):
        # WI-0158's subject: an advice string the reader cannot execute. Before this the
        # OPS branch told you to redraw by hand; a row with a renumberer must say the
        # thing that is actually true of it.
        tip = self._collide()
        row = session._counter("ops")
        saved = row.renumberer
        row.renumberer = None
        try:
            _ok, without = session._counter_land_gate(row, tip, self.parent)
        finally:
            row.renumberer = saved
        _ok, with_it = session._counter_land_gate(row, tip, self.parent)
        self.assertIn("ops-renumber --all", with_it)
        self.assertNotIn("ops-renumber --all", without)


class TheWholeOpsIdentityMovesTest(OpsRenumberBase):
    """Properties B and C — what moves, and what must NOT be invented."""

    def setUp(self):
        super().setUp()
        self._collide()
        self._lane_write("ops-items/notes/OPS-0002/20260101T000000000000Z-t-aa.md",
                         "a run record that belongs to the lane's OPS-0002.\n")
        self.tip = self._lane_commit("notes")

    def _run(self):
        with redirect_stdout(io.StringIO()):
            return session._counter("ops").renumberer([("OPS-0002", 7)], self.parent)

    def test_the_file_and_its_h1_move_together(self):
        self._run()
        self.assertIn("OPS-0007-the-lane-s-own-second-obligation.md", self._names())
        self.assertNotIn("OPS-0002-lane-side.md", self._names())
        self.assertTrue(
            self._read("ops-items/OPS-0007-the-lane-s-own-second-obligation.md")
            .startswith("# OPS-0007: the lane's own second obligation"))

    def test_the_ops_fields_survive_the_re_render(self):
        # The renumber re-renders the file through the store's own writer, so a field the
        # generic path did not know about would be silently dropped rather than error.
        self._run()
        text = self._read("ops-items/OPS-0007-the-lane-s-own-second-obligation.md")
        self.assertIn("- cadence: monthly", text)
        self.assertIn("- last-result: ", text)

    def test_the_notes_directory_travels_with_the_id(self):
        self._run()
        self.assertFalse((self.lane / "ops-items/notes/OPS-0002").exists())
        moved = sorted((self.lane / "ops-items/notes/OPS-0007").glob("*.md"))
        self.assertTrue(any("belongs to the lane's OPS-0002"
                            in p.read_text(encoding="utf-8") for p in moved))

    def test_a_provenance_record_says_what_it_was_filed_as(self):
        self._run()
        notes = sorted((self.lane / "ops-items/notes/OPS-0007").glob("*.md"))
        blob = "\n".join(p.read_text(encoding="utf-8") for p in notes)
        self.assertIn("RENUMBERED OPS-0002 -> OPS-0007", blob)

    def test_no_claim_record_is_invented_for_an_unclaimable_store(self):
        # Property C. An OPS id can never hold a claim (`_wi_claimable_items` reads the
        # WI store plus live drills), so a renumberer that moved claims unconditionally
        # would write a record nothing ever reads and nothing ever releases — invisible
        # garbage in the shared coordination store, not a loud failure.
        self._run()
        claims = session._coord_list("claims", include_expired=True)
        self.assertNotIn("OPS-0007", claims)
        self.assertNotIn("OPS-0002", claims)

    def test_the_reservation_released_is_the_ops_namespace(self):
        identity = session._coord_identity()
        session._coord_try_acquire("ops-alloc", "0002", identity,
                                   session.OPS_ALLOC_TTL_SECONDS)
        _mapping, report = self._run()
        self.assertNotIn("0002", session._coord_list("ops-alloc"))
        self.assertTrue(any("ops-alloc/0002" in line for line in report),
                        f"the release must name the ops namespace: {report}")


class OpsCitationsFollowLaneProvenanceTest(OpsRenumberBase):
    """Property D — the citation pass is store-neutral, in both directions."""

    def setUp(self):
        super().setUp()
        self._collide()
        # A WORK ITEM citing the ops id: the citation pass runs over the whole tree, and
        # scoping it to one store directory would follow half the citations.
        self._lane_write("work-items/WI-0009-cites-ops.md",
                         "# WI-0009: cites ops\n\n- status: open\n\n"
                         "this item is the reason OPS-0002 exists.\n")
        self._lane_write("trunkdoc.md",
                         "trunk prose that cites OPS-0002 and means the trunk's "
                         "obligation.\nlane-appended line citing OPS-0002.\n")
        self._lane_write("sessions/journal/20260101T0000Z-t-aa.md",
                         "the journal says OPS-0002 and is record, not guidance.\n")
        self.tip = self._lane_commit("citations")

    def _run(self):
        with redirect_stdout(io.StringIO()):
            return session._counter("ops").renumberer([("OPS-0002", 7)], self.parent)

    def test_a_lane_authored_citation_follows_the_remap(self):
        self._run()
        self.assertIn("OPS-0007", self._read("work-items/WI-0009-cites-ops.md"))
        self.assertNotIn("OPS-0002", self._read("work-items/WI-0009-cites-ops.md"))

    def test_a_trunk_line_is_left_alone(self):
        # THE OTHER DIRECTION, and the one a blanket replace gets wrong: after the remap
        # OPS-0002 still names a real obligation on the trunk, and this line means it.
        self._run()
        first = self._read("trunkdoc.md").splitlines()[0]
        self.assertIn("OPS-0002", first)

    def test_only_the_lane_added_line_moves_inside_a_shared_file(self):
        self._run()
        lines = self._read("trunkdoc.md").splitlines()
        self.assertIn("OPS-0002", lines[0])
        self.assertIn("OPS-0007", lines[1])

    def test_history_is_reported_and_never_rewritten(self):
        _mapping, report = self._run()
        self.assertIn("OPS-0002",
                      self._read("sessions/journal/20260101T0000Z-t-aa.md"))
        self.assertTrue(any("NOT rewritten" in line for line in report))


@unittest.skipUnless(GIT, "git required")
class TheAdrRowStillRefusesTest(unittest.TestCase):
    """Property E — generalising the renumberer must not have made every row
    renumberable. The registry's by-construction refusal is the thing being protected.

    WI-0290 gave the ADR row a renumberer of its OWN, so the property this class now
    pins is the narrower one that still matters: the store path did not absorb it."""

    def test_the_adr_row_is_not_on_the_store_path(self):
        row = session._counter("adr")
        self.assertIsNotNone(row.renumberer)
        self.assertIs(row.renumberer.func, session._adr_renumber_batch)
        self.assertIsNot(row.renumberer.func, session._store_renumber_batch)

    def test_the_wi_and_ops_rows_do(self):
        self.assertIsNotNone(session._counter("wi").renumberer)
        self.assertIsNotNone(session._counter("ops").renumberer)

    def test_the_adr_row_declares_no_store_shape(self):
        # The refusal is not a missing lambda someone forgot: the ADR row answers none of
        # the four store hooks, which is what makes "it is a different job" checkable
        # rather than a claim in a comment.
        row = session._counter("adr")
        self.assertIsNone(row.filename_for)
        self.assertIsNone(row.parse_text)
        self.assertIsNone(row.render_item)


if __name__ == "__main__":
    unittest.main()
