"""The published wi-feed and the board-viewer startup gate.

The board renders a generated, schema-versioned feed — never the raw store, which it
once parsed and broke silently at a format change. These tests pin:

  1. the feed regenerates on every mutation of what a viewer should see — item writes,
     ops writes, claim acquire, and every way a hold ends (the tombstone);
  2. a lane's claim path publishes MAIN's store, never the lane's frozen copy
     (the two-stores split — publishing the frozen copy is the stale-mirror defect);
  3. the hash is over content, not the stamp — "regenerated" is distinguishable
     from "changed";
  4. a feed failure never fails the store write it rides on;
  5. the pure gate decision (`_board_gate`) branch by branch — each refusal is its
     own sentence, and fail-open means the FULL list, never a blank;
  6. the short view's candidate filter — overdue/never-run ops first, then unclaimed,
     unblocked `next` dev items, capped, with the drop counted out loud;
  7. the call site: `cmd_start` actually consults the gate (a wiring-removed
     regression keeps every unit test green — the ADR-0089 lesson);
  8. WI-0408 — the feed DECLARES that this member writes the waiting marker, so a
     consumer can tell "nobody is waiting here" from "this member cannot answer that
     question". Those are the same empty list without the declaration, which is why the
     board viewer's days pane rendered `cannot tell` across the whole portfolio.
"""

import argparse
import inspect
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _wi_text(wid, title, status, section="next", blocked="", waiting=""):
    # No `scope` line and, by default, no `waiting-on` line: this is deliberately an item
    # file as it was written BEFORE either field existed, which is what the feed has to
    # keep publishing correctly for every member's legacy backlog.
    return (f"# {wid}: {title}\n\n- status: {status}\n- section: {section}\n"
            f"- blocked-by: {blocked}\n- group: \n- impact: feature\n"
            + (f"- waiting-on: {waiting}\n" if waiting else "")
            + f"- version: \n\n")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class FeedBase(unittest.TestCase):
    def setUp(self):
        # The feed renders who holds what; a fixture claim must not carry the runner's
        # journal, or the rendered holder is the developer's session (WI-0126).
        # FIRST, before the fixture builds anything — this snapshots the environment and
        # restores that snapshot wholesale at cleanup, so a variable set before it is
        # baked in and outlives the test (WI-0275). It clears the POGA_* dispatch handles
        # too: `AMBIENT_VARS` is `DISPATCH_ENV_VARS` plus the identity axis, derived from
        # it rather than restated, so this one call replaces the two that used to sit
        # here. `BOARD_STATUS_ENV` is popped below instead, because it is a session
        # constant rather than part of the process's ambient identity.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        d = self.main / "work-items"
        d.mkdir()
        (d / "WI-0001-alpha.md").write_text(_wi_text("WI-0001", "Build alpha", "open"),
                                            encoding="utf-8")
        (d / "WI-0002-beta.md").write_text(_wi_text("WI-0002", "Ship beta", "open"),
                                           encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        os.environ.pop(session.BOARD_STATUS_ENV, None)
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _feed(self):
        p = self.main / ".session-state" / session.WI_FEED_NAME
        self.assertTrue(p.exists(), f"no feed at {p}")
        return json.loads(p.read_text(encoding="utf-8"))


class FeedRegenerationTest(FeedBase):
    def test_item_write_regenerates_the_feed(self):
        session.ROOT = self.main
        session._wi_write_item({"id": "WI-0003", "title": "Gamma work",
                                "status": "open", "section": "backlog"})
        feed = self._feed()
        self.assertEqual(feed["schema"], session.WI_FEED_SCHEMA)
        self.assertEqual(feed["system"], "test")  # architect_id minus -arch (ADR-0006)
        self.assertIn("WI-0003", [it["id"] for it in feed["items"]])

    def test_ops_write_regenerates_the_feed(self):
        session.ROOT = self.main
        session._ops_write_item({"id": "OPS-0001", "title": "Check the backups",
                                 "status": "open", "due": "2020-01-01"})
        feed = self._feed()
        ops = [it for it in feed["items"] if it["kind"] == "ops"]
        self.assertEqual([o["id"] for o in ops], ["OPS-0001"])
        self.assertEqual(ops[0]["state"], "overdue")

    def test_claim_acquire_and_release_both_regenerate(self):
        session.ROOT = self.lane
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-1", 3600,
                                   branch="worktree-poga-1")
        feed = self._feed()
        self.assertIn("WI-0001", feed["claims"])
        self.assertTrue(feed["claims"]["WI-0001"]["expires_epoch"] > time.time())
        session._coord_release("claims", "WI-0001", "worktree-poga-1")
        feed = self._feed()
        self.assertNotIn("WI-0001", feed["claims"])

    def test_a_drill_claim_is_not_published_to_the_board(self):
        """WI-0174 — the feed is a published surface and `items` only ever comes from
        the real store, so a drill's claim can render as nothing but a claim on an item
        that does not exist. A real claim taken in the same breath still publishes."""
        session.ROOT = self.lane
        session._coord_try_acquire("claims", "DRILL-1", "worktree-poga-1", 3600)
        session._coord_try_acquire("claims", "WI-0002", "worktree-poga-1", 3600)
        feed = self._feed()
        self.assertNotIn("DRILL-1", feed["claims"])
        self.assertIn("WI-0002", feed["claims"])

    def test_lane_claim_publishes_mains_store_not_the_frozen_copy(self):
        # An item added only in the lane's checkout must NOT appear in the feed a
        # lane-side claim regenerates — the feed is the one real (main) store.
        session.ROOT = self.lane
        (self.lane / "work-items" / "WI-0009-lane-only.md").write_text(
            _wi_text("WI-0009", "Lane-only item", "open"), encoding="utf-8")
        session._coord_try_acquire("claims", "WI-0002", "worktree-poga-1", 3600)
        ids = [it["id"] for it in self._feed()["items"]]
        self.assertIn("WI-0001", ids)
        self.assertNotIn("WI-0009", ids)

    def test_hash_is_content_not_stamp(self):
        session.ROOT = self.main
        a = session._wi_feed_build(self.main)
        time.sleep(0.01)
        b = session._wi_feed_build(self.main)
        self.assertEqual(a["store_hash"], b["store_hash"])
        self.assertNotEqual(a["generated_epoch"], b["generated_epoch"])
        (self.main / "work-items" / "WI-0003-new.md").write_text(
            _wi_text("WI-0003", "New row", "open"), encoding="utf-8")
        self.assertNotEqual(a["store_hash"],
                            session._wi_feed_build(self.main)["store_hash"])

    def test_feed_failure_never_fails_the_store_write(self):
        session.ROOT = self.main
        with mock.patch.object(session, "_wi_feed_build",
                               side_effect=RuntimeError("boom")):
            session._wi_write_item({"id": "WI-0004", "title": "Survives",
                                    "status": "open", "section": "next"})
        names = [p.name for p in (self.main / "work-items").glob("WI-0004-*.md")]
        self.assertEqual(len(names), 1, "store write must succeed despite feed failure")


class WaitingMarkerDeclarationTest(FeedBase):
    """WI-0408 half two — the half the brief says is easy to drop, and matters more.

    The board viewer's Architect built the reader first and reported what it found: without a
    declaration, "no item in this feed names a person" and "this member does not write
    the marker" are the same empty list. So the board must render `cannot tell`
    (ADR-0101's three-state rule), and it does, for the entire portfolio.

    Inferring participation from the presence of a marker is the trap this class exists
    to keep shut. It reads as a fact while being evidence, and it flips back to
    `cannot tell` the moment a member clears its last waiting item — a pane that goes
    blind as a side effect of good news.
    """

    def test_the_feed_declares_that_this_member_writes_the_marker(self):
        session.ROOT = self.main
        self.assertIs(session._wi_feed_build(self.main)["writes_waiting_marker"], True)

    def test_the_declaration_is_there_when_nobody_is_waiting(self):
        """THE WHOLE POINT. The fixture store carries no marker at all, which is the
        state every member is in on the day this ships and the state a healthy member
        returns to. `writes_waiting_marker` + an empty result is the readable answer
        "nothing is waiting on you here"; without the declaration it is unreadable."""
        feed = session._wi_feed_build(self.main)
        self.assertEqual([it for it in feed["items"] if it["waiting_on"]], [])
        self.assertIs(feed["writes_waiting_marker"], True)

    def test_the_declaration_does_not_move_when_the_last_marker_clears(self):
        """It declares what the WRITER can do, never what the data happens to contain —
        so it is a constant, not a count. This is the test that fails if someone
        "simplifies" it to `any(it["waiting_on"] for it in items)`."""
        (self.main / "work-items" / "WI-0001-alpha.md").write_text(
            _wi_text("WI-0001", "Build alpha", "open", waiting="operator"), encoding="utf-8")
        with_marker = session._wi_feed_build(self.main)
        self.assertEqual([it["id"] for it in with_marker["items"] if it["waiting_on"]],
                         ["WI-0001"])
        (self.main / "work-items" / "WI-0001-alpha.md").write_text(
            _wi_text("WI-0001", "Build alpha", "open"), encoding="utf-8")
        cleared = session._wi_feed_build(self.main)
        self.assertEqual([it for it in cleared["items"] if it["waiting_on"]], [])
        self.assertIs(with_marker["writes_waiting_marker"], True)
        self.assertIs(cleared["writes_waiting_marker"], True)

    def test_the_marker_reaches_the_feed_on_the_row(self):
        (self.main / "work-items" / "WI-0002-beta.md").write_text(
            _wi_text("WI-0002", "Ship beta", "open", waiting="operator"), encoding="utf-8")
        rows = {it["id"]: it for it in session._wi_feed_build(self.main)["items"]}
        self.assertEqual(rows["WI-0002"]["waiting_on"], "operator")

    def test_an_unwaiting_row_carries_an_empty_string_not_a_missing_key(self):
        """The board reads this feed BY KEY. A missing key and an empty one
        are different bugs on the other side of that contract, and only one is ours."""
        for it in session._wi_feed_build(self.main)["items"]:
            self.assertIn("waiting_on", it)
            self.assertEqual(it["waiting_on"], "")

    def test_an_obligation_row_reads_as_not_waiting(self):
        """A standing obligation is owed on a DATE, never waiting on a person's answer,
        so the ops namespace grows no field — and the row must still carry the key."""
        session.ROOT = self.main
        session._ops_write_item({"id": "OPS-0001", "title": "Check the backups",
                                 "status": "open", "due": "2020-01-01"})
        ops = [it for it in self._feed()["items"] if it["kind"] == "ops"]
        self.assertEqual([o["waiting_on"] for o in ops], [""])

    def test_the_schema_is_not_bumped_to_carry_the_declaration(self):
        """Pinned as a DECISION, not an accident. An additive key is ignorable by a
        consumer that does not know it, and an ABSENT declaration already reads as the
        honest answer for an older feed — "this member does not write the marker".
        Bumping would force every consumer to re-verify against a new schema number in
        order to learn one boolean it can read directly. A future bump is then a
        deliberate act that edits this test, rather than something that rides along."""
        feed = session._wi_feed_build(self.main)
        self.assertEqual(feed["schema"], 1)
        self.assertIn("writes_waiting_marker", feed)

    def test_the_declaration_is_not_folded_into_the_store_hash(self):
        """`store_hash` answers "did the STORE change". A build-time constant in it would
        be a value that can never vary pretending to be content."""
        import hashlib as _h
        import json as _j
        feed = session._wi_feed_build(self.main)
        expected = _h.sha256(_j.dumps({"items": feed["items"], "claims": feed["claims"]},
                                      sort_keys=True).encode("utf-8")).hexdigest()[:16]
        self.assertEqual(feed["store_hash"], expected)

    def test_the_rebuild_receipt_reports_the_declaration_and_the_count(self):
        """`ship-the-detector-with-the-capability`: a capability nothing reports is one
        nobody can see was shipped. The count beside it is the fact the declaration makes
        readable — with it, `0` means nobody is waiting."""
        session.ROOT = self.main
        (self.main / "work-items" / "WI-0002-beta.md").write_text(
            _wi_text("WI-0002", "Ship beta", "open", waiting="operator"), encoding="utf-8")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_feed(argparse.Namespace())
        out = buf.getvalue()
        self.assertIn("waiting-marker declared", out)
        self.assertIn("1 item(s) waiting", out)


class BoardGateTest(unittest.TestCase):
    """The pure decision, branch by branch. `now` is pinned; no filesystem."""

    NOW = 1_000_000.0

    def _feed(self, gen=None, system="test"):
        return {"schema": 1, "system": system,
                "generated_epoch": self.NOW if gen is None else gen}

    def _status(self, written=None, poll=30, echo_gen=None, system="test"):
        st = {"schema": 1, "url": "http://127.0.0.1:5200",
              "written_epoch": self.NOW - 5 if written is None else written,
              "poll_seconds": poll}
        if echo_gen is not None:
            st["workitems"] = {system: {"feed_generated_epoch": echo_gen}}
        return st

    def test_live_when_fresh_and_echo_current(self):
        ok, why = session._board_gate(self._status(echo_gen=self.NOW), self._feed(),
                                      self.NOW)
        self.assertTrue(ok, why)

    def test_echo_within_one_poll_of_lag_is_live(self):
        ok, _ = session._board_gate(self._status(echo_gen=self.NOW - 100),
                                    self._feed(), self.NOW)
        self.assertTrue(ok)  # 100s behind < max(2*30, 120) allowance

    def test_unknown_schema_refuses_with_its_own_sentence(self):
        ok, why = session._board_gate({"schema": 99}, self._feed(), self.NOW)
        self.assertFalse(ok)
        self.assertIn("schema", why)

    def test_missing_feed_is_its_own_refusal(self):
        ok, why = session._board_gate(self._status(echo_gen=self.NOW), None, self.NOW)
        self.assertFalse(ok)
        self.assertIn("feed", why)

    def test_stale_status_file_refuses(self):
        ok, why = session._board_gate(
            self._status(written=self.NOW - 500, echo_gen=self.NOW),
            self._feed(), self.NOW)
        self.assertFalse(ok)
        self.assertIn("stale", why)

    def test_board_not_rendering_this_system_refuses(self):
        ok, why = session._board_gate(self._status(echo_gen=self.NOW, system="other"),
                                      self._feed(), self.NOW)
        self.assertFalse(ok)
        self.assertIn("test", why)

    def test_board_rendering_an_old_feed_refuses(self):
        ok, why = session._board_gate(self._status(echo_gen=self.NOW - 10_000),
                                      self._feed(), self.NOW)
        self.assertFalse(ok)
        self.assertIn("has not picked up the current feed", why)

    def test_quiet_period_is_never_reported_as_the_boards_lag(self):
        """WI-0121, from the live 2026-08-13 case: the store sat unwritten for ~2h, a
        write regenerated the feed, and the gate announced "6906s behind" about a board
        that was under 1s behind. The only difference available here is between two
        CONSECUTIVE feed generations, which after a quiet store IS the quiet period — so
        the refusal must not report it as a duration at all. Written to fail against the
        old sentence: a test derived from the code's own premise asserts 6906 happily."""
        quiet = 6906
        board_wrote_ago = 5                    # board is alive and polling normally
        ok, why = session._board_gate(
            self._status(written=self.NOW - board_wrote_ago,
                         echo_gen=self.NOW - quiet),
            self._feed(), self.NOW)
        self.assertFalse(ok, "a board echoing a superseded generation is not showing")
        self.assertNotIn(str(quiet), why,
                         "the quiet period is not the board's lag — it must not be "
                         f"reported as one. Got: {why}")
        self.assertNotIn("behind", why)
        # It names both generations, so a reader can see WHICH feed the board is on...
        self.assertIn(str(int(self.NOW - quiet)), why)
        self.assertIn(str(int(self.NOW)), why)
        # ...and the one duration it does print is the one actually measured: the
        # board's own write age, which is what says the board itself is alive.
        self.assertIn(f"{board_wrote_ago}s ago", why)

    def test_missing_poll_seconds_gets_the_floor_not_a_crash(self):
        st = self._status(echo_gen=self.NOW)
        del st["poll_seconds"]
        ok, _ = session._board_gate(st, self._feed(), self.NOW)
        self.assertTrue(ok)
        st["written_epoch"] = self.NOW - session.BOARD_FRESH_FLOOR_SECONDS - 1
        ok, _ = session._board_gate(st, self._feed(), self.NOW)
        self.assertFalse(ok)


class BoardLiveTest(FeedBase):
    def _write_status(self, **kw):
        p = self.tmp / "board-status.json"
        feed_gen = kw.pop("feed_gen", time.time())
        st = {"schema": 1, "url": "http://127.0.0.1:5200",
              "written_epoch": time.time(), "poll_seconds": 30,
              "workitems": {"test": {"feed_generated_epoch": feed_gen}}}
        st.update(kw)
        p.write_text(json.dumps(st), encoding="utf-8")
        os.environ[session.BOARD_STATUS_ENV] = str(p)
        return p

    def test_no_status_file_is_not_live_and_says_so(self):
        session.ROOT = self.main
        os.environ[session.BOARD_STATUS_ENV] = str(self.tmp / "absent.json")
        board, why = session._board_live()
        self.assertIsNone(board)
        self.assertIn("no board status", why)

    def test_live_end_to_end_through_files(self):
        session.ROOT = self.main
        session._wi_feed_write()
        feed = self._feed()
        self._write_status(feed_gen=feed["generated_epoch"])
        board, why = session._board_live()
        self.assertIsNotNone(board, why)
        self.assertEqual(why, "live")

    def test_no_feed_published_is_not_live(self):
        session.ROOT = self.main
        self._write_status()
        board, why = session._board_live()
        self.assertIsNone(board)
        self.assertIn("feed", why)


class ShortViewTest(FeedBase):
    def _status(self):
        return {"schema": 1, "url": "http://127.0.0.1:5200",
                "written_epoch": time.time(), "poll_seconds": 30,
                "workitems": {"test": {"feed_generated_epoch": time.time()}}}

    def test_candidates_are_unclaimed_unblocked_next_with_ops_first(self):
        session.ROOT = self.main
        d = self.main / "work-items"
        (d / "WI-0003-blocked.md").write_text(
            _wi_text("WI-0003", "Blocked row", "open", blocked="WI-0001"),
            encoding="utf-8")
        (d / "WI-0004-backlog.md").write_text(
            _wi_text("WI-0004", "Backlog row", "open", section="backlog"),
            encoding="utf-8")
        session._ops_write_item({"id": "OPS-0001", "title": "Overdue duty",
                                 "status": "open", "due": "2020-01-01"})
        session._coord_try_acquire("claims", "WI-0002", "worktree-poga-1", 3600)
        lines = session._wi_short_lines(self._status())
        body = "\n".join(lines)
        self.assertIn("http://127.0.0.1:5200", lines[0])
        self.assertIn("OPS-0001", body)          # overdue ops always surface
        self.assertIn("WI-0001", body)           # open, next, unclaimed, unblocked
        self.assertNotIn("WI-0002", body)        # claimed → the board's story now
        self.assertNotIn("WI-0003", body)        # blocked by a live item
        self.assertNotIn("WI-0004", body)        # backlog is not a recommendation
        self.assertLess(body.index("OPS-0001"), body.index("WI-0001"))

    def test_cap_reports_what_it_dropped(self):
        session.ROOT = self.main
        d = self.main / "work-items"
        for n in range(3, 18):
            (d / f"WI-{n:04d}-row.md").write_text(
                _wi_text(f"WI-{n:04d}", f"Row {n}", "open"), encoding="utf-8")
        lines = session._wi_short_lines(self._status())
        shown = [ln for ln in lines if ln.strip().startswith(("WI-", "OPS-"))]
        self.assertEqual(len(shown), 10)
        self.assertIn("more candidate", lines[-1])


class StartWiringTest(unittest.TestCase):
    def test_cmd_start_consults_the_gate_and_short_view(self):
        # The unit tests above stay green if the gate is never wired into startup —
        # the exact regression shape that left _auto_reap_lanes dead for a day
        # (ADR-0089). Pin the call sites in the one function that owns the banner.
        src = inspect.getsource(session.cmd_start)
        self.assertIn("_board_live()", src)
        self.assertIn("_wi_short_lines(", src)
        self.assertIn("_wi_render_snapshot_lines()", src)


if __name__ == "__main__":
    unittest.main()
