"""WI-0299 D2 — number reservations are gate-owned, and ownership outlives the journal.

A drawn number is a coordination record. Three things have to be true of it and only one
was:

  * **It must still be recognisable as YOURS after your journal closes.** It was not.
    `_counter_land_gate` decides ownership with `_coord_mine(holder, ident, my_journal)`,
    and inside a lane `ident` is the lane branch while a record drawn through the
    main-anchored front door (`adr-next`, `poga work` — both correct per ADR-0073) carries
    a Claude session id and `branch: main`. So the JOURNAL is the only key that can match.
    `_coord_my_journal` → `_find_holder_journal` → `_open_journals_in`, and that last one
    returns only journals **with no `ended`**. A session's own reservations therefore turn
    into a stranger's at the exact moment it closes — which is the moment `merge` runs.
    That is WI-0254, and it is the mechanism behind the unexplained refusal WI-0237
    recorded: *"it was hit at the very end of the session, which is exactly when the
    journal is closed."*

  * **It must be RELEASED by the land that makes it real.** WI-0237 released numbers at
    the store commit, which covers `wi-alloc` and `ops-alloc` — but nothing writes ADRs
    through `_store_autocommit`, so an `adr-alloc` record had no write-path release at all
    and survived to expire eight hours later, blocking its own number in the meantime.

  * **A dead lane's reservation must go with the lane.** The WI-0298 exit path already
    frees every `COORD_KINDS` record in one step; what was untested is that a reservation
    is genuinely among them, and that it is matched by the JOURNAL that drew it rather than
    only by the lane's branch — a front-door record carries neither the lane's branch nor
    its identity.

The fix to the first is deliberately NOT to widen `_open_journals_in`. Two callers rely on
its open-only filter to mean open (`_resolve_orphan_candidates`, `cmd_resolve_orphan`), and
the non-Claude fallback resolves by UNIQUENESS within a checkout — closed journals
accumulate, so uniqueness would collapse on the first repeat. The Claude path matches an
EXACT key that is unambiguous whether or not the journal is still open, so only that path
widens. WI-0254 argued exactly this shape and said to argue it rather than assume it;
`OnlyTheExactKeyPathWidensTest` is that argument made checkable.
"""

import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (exercise_real_coord_holder,  # noqa: E402
                           neutralize_coord_journal)
from test_worktree_lane import WorktreeLaneBase, _git  # noqa: E402

GIT = shutil.which("git")
CSID = "csid-a-closed-session"


@unittest.skipUnless(GIT, "git not available")
class ReservationBase(WorktreeLaneBase):
    def setUp(self):
        # `WorktreeLaneBase.setUp` applies `neutralize_coord_journal(self)`, and applying it
        # a SECOND time here leaks: the helper keeps one patch per case, so the later call
        # overwrites the earlier and only one of the two is ever stopped. One call, from the
        # base; the classes below opt out by name with `exercise_real_coord_holder`, which
        # is the sanctioned shape for tests whose subject IS holder resolution.
        super().setUp()
        self._point_session_at_lane()

    def _journal(self, did, csid=CSID, ended="2026-09-06T14:00:00+00:00"):
        """This lane's own journal, CLOSED — the state `merge` runs in. `end` stamps
        `ended` before the land, so every land after WI-0051 reads a closed journal."""
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": "t",
            "machine": "DevBox", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": "2026-09-06T13:00:00+00:00", "ended": ended,
            "claude-session-id": csid,
        })
        return did

    def _front_door_reservation(self, kind, num, journal, session_id="main-session-key"):
        """A record as the MAIN-ANCHORED front door writes it: main's ownership key,
        `branch: main`, and the drawing session's journal. Neither of the first two can
        identify the lane, which is why the journal is load-bearing."""
        d = session._coord_dir(kind, create=True)
        rec = session._coord_record(session_id, ttl=3600, name=num, kind=kind,
                                    branch="main")
        rec["journal"] = journal
        rec["session_id"] = session_id
        (d / f"{num}.json").write_text(json.dumps(rec), encoding="utf-8")
        return d / f"{num}.json"

    def _commit_adr(self, name):
        (self.lane / "adr").mkdir(exist_ok=True)
        (self.lane / "adr" / name).write_text("# adr\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"adr {name}")

    def _land(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = session._land_worktree_lane(None, None, "1.0.0", push=False)
        return ok, buf.getvalue()


class ClosedJournalStillOwnsItsNumbersTest(ReservationBase):
    """WI-0254, in the smallest form that reproduces it: the same scenario the existing
    `test_a_number_this_lane_drew_through_the_front_door_can_land` covers, with the one
    difference that makes it real — the journal is CLOSED, as it is at every land."""

    def test_a_number_drawn_by_this_session_lands_after_its_journal_closed(self):
        exercise_real_coord_holder(self)
        did = self._journal("20260906T1300Z-devbox-aaaa")
        self._front_door_reservation("adr-alloc", "0009", did)
        self._commit_adr("0009-drawn-then-closed.md")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": CSID}):
            ok, out = self._land()
        self.assertTrue(ok, "a session was refused its own reservation because it had "
                            "closed its journal — WI-0254, and the unexplained refusal "
                            f"WI-0237 recorded:\n{out}")
        self.assertIn("adr/0009-drawn-then-closed.md",
                      subprocess.run([GIT, "-C", str(self.main), "ls-tree", "-r",
                                      "--name-only", "main"],
                                     capture_output=True, text=True).stdout)

    def test_another_sessions_reservation_still_blocks_after_it_closed(self):
        """The half that matters. Widening WHO is recognisable must not make everyone
        recognisable — a closed stranger is still a stranger."""
        exercise_real_coord_holder(self)
        self._journal("20260906T1300Z-devbox-aaaa")
        self._front_door_reservation("adr-alloc", "0009",
                                     "20260906T0100Z-devbox-9999",
                                     session_id="someone-else")
        self._commit_adr("0009-hand-picked.md")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": CSID}):
            ok, out = self._land()
        self.assertFalse(ok, "another session's reservation must still block")
        self.assertIn("BLOCKED", out)


class OnlyTheExactKeyPathWidensTest(unittest.TestCase):
    """WI-0254's *"two paths, two answers — do not widen both"*, made checkable.

    `_open_journals_in` keeps meaning OPEN, because two callers depend on that
    (`_resolve_orphan_candidates` and `cmd_resolve_orphan` both use it to mean *not yet
    closed*), and the non-Claude fallback resolves by uniqueness within a checkout — a
    filter that admitted closed journals would collapse to ambiguity the second time any
    checkout ran a session."""

    def setUp(self):
        # The one place this module names the guard in its own source, and it is a real
        # application rather than a nod to the detector: these journals are fixture
        # journals, and an ambient `_coord_holder` reading the runner's live session is
        # exactly the coin-flip `coord_fixture` exists to remove. The classes above reach
        # the same guard through `WorktreeLaneBase` and then opt out by name.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(__import__("tempfile").mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for did, ended in (("20260906T0100Z-devbox-1111", "2026-09-06T02:00:00+00:00"),
                           ("20260906T0300Z-devbox-2222", "")):
            (self.tmp / f"{did}.md").write_text(
                f"---\nsession-id: {did}\nended: {ended}\n"
                f"claude-session-id: csid-{did[-4:]}\n---\n", encoding="utf-8")

    def test_open_journals_in_still_means_open(self):
        got = {fm["session-id"] for _p, fm in session._open_journals_in(self.tmp)}
        self.assertEqual(got, {"20260906T0300Z-devbox-2222"},
                         "_open_journals_in admitted a CLOSED journal — the orphan "
                         "resolver reads this to mean 'not yet closed'")

    def test_the_exact_key_search_sees_closed_journals_too(self):
        got = {fm["session-id"] for _p, fm in session._journals_in(self.tmp)}
        self.assertEqual(got, {"20260906T0100Z-devbox-1111",
                               "20260906T0300Z-devbox-2222"})


class LandReleasesWhatItMadeRealTest(ReservationBase):
    """A drawn number becomes real at the land that carries its file, and the same land
    releases the reservation. Until now `adr-alloc` had no release on any write path —
    `_release_committed_numbers` fires from `_store_autocommit`, and no ADR is written
    through the store — so the record sat until its 8-hour TTL lapsed."""

    def test_the_land_releases_the_adr_number_its_file_carries(self):
        exercise_real_coord_holder(self)
        did = self._journal("20260906T1300Z-devbox-aaaa")
        rec = self._front_door_reservation("adr-alloc", "0009", did)
        self._commit_adr("0009-drawn-then-closed.md")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": CSID}):
            ok, out = self._land()
        self.assertTrue(ok, out)
        self.assertFalse(rec.exists(),
                         "the land carried adr/0009 onto the trunk and left its "
                         "reservation held — the number is spent but still blocked")

    def test_a_number_this_land_did_not_carry_is_left_alone(self):
        """Release is tied to the file that landed, never to the session that happens to
        be landing. A reservation for a number still in flight must survive its own
        session's land — that is WI-0237's `(3) LIMIT` boundary."""
        exercise_real_coord_holder(self)
        did = self._journal("20260906T1300Z-devbox-aaaa")
        carried = self._front_door_reservation("adr-alloc", "0009", did)
        inflight = self._front_door_reservation("adr-alloc", "0010", did)
        self._commit_adr("0009-drawn-then-closed.md")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": CSID}):
            ok, out = self._land()
        self.assertTrue(ok, out)
        self.assertFalse(carried.exists())
        self.assertTrue(inflight.exists(),
                        "the land released a number whose file it never carried")


class DeadLanesReservationGoesWithTheLaneTest(ReservationBase):
    """D2's third part, and the one WI-0298 mostly built: one exit path frees the gate
    record, the queue ticket, the dispatch slot AND the drawn numbers in a single step.

    What was never pinned is the MATCH. A front-door reservation carries `branch: main`
    and main's ownership key, so a sweep keyed on the lane's identity cannot see it at
    all; it is reachable only through the journals the lane added. Membership in
    `LANE_EXIT_KINDS` is necessary and was already true — being FOUND is the part that
    had no test."""

    def test_the_exit_path_frees_a_front_door_reservation_by_its_journal(self):
        exercise_real_coord_holder(self)
        did = self._journal("20260906T1300Z-devbox-aaaa")
        rec = self._front_door_reservation("adr-alloc", "0009", did)
        ok, left = session._lane_exit_verify("worktree-poga-1", journals=(did,),
                                             reason="teardown")
        self.assertTrue(ok, f"records survived the lane exit: {left}")
        self.assertFalse(rec.exists(),
                         "a dead lane's drawn number outlived its lane — nothing else "
                         "frees a record carrying branch 'main'")

    def test_a_live_strangers_reservation_is_not_swept_with_the_lane(self):
        exercise_real_coord_holder(self)
        did = self._journal("20260906T1300Z-devbox-aaaa")
        mine = self._front_door_reservation("adr-alloc", "0009", did)
        theirs = self._front_door_reservation("adr-alloc", "0011",
                                              "20260906T0100Z-devbox-9999",
                                              session_id="someone-else")
        session._lane_exit_release("worktree-poga-1", journals=(did,), reason="teardown")
        self.assertFalse(mine.exists())
        self.assertTrue(theirs.exists(),
                        "the lane exit swept a reservation belonging to another session")


if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
