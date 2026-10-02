"""ADR-0051 C4 — work-item claims (phase 2), retargeted onto the work-item store
(ADR-0073).

Claims sit on the coordination substrate (test_coord.py) and bind a `work-items/`
item id (WI-NNNN) to the lane holding it. Until ADR-0073 the claimable list was
parsed out of ROADMAP.md's "## Next" section (hand-picked `<!-- id: slug -->`
anchors); the work-item store replaces that as the one list. These tests pin:

  1. the work-item-store parser (open/in-progress items only, done excluded);
  2. claimed-vs-available status, incl. orphan claims on ids no longer in the store;
  3. the CLI contract: claim exit 0/1/2, release holder-respect;
  4. identity = lane branch inside a poga lane.
"""

import argparse
import io
import json
import os
import pathlib
import re
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
from coord_fixture import (  # noqa: E402
    exercise_real_coord_holder,
    neutralize_coord_journal,
)

GIT = shutil.which("git")

ITEMS = {
    "WI-0001-alpha.md": ("WI-0001", "Build alpha thing", "open"),
    "WI-0002-beta.md": ("WI-0002", "Ship beta", "open"),
    "WI-0003-gamma.md": ("WI-0003", "Plain item, in progress", "in-progress"),
    "WI-0004-done.md": ("WI-0004", "Already finished", "done"),
}


def _wi_text(wid, title, status):
    # `impact` is required for the store to validate (WI-0043) — an unlabelled item is
    # reported as classification debt, so a fixture without one makes a *sound* store
    # look unsound and fails the validator tests below for the wrong reason.
    return (f"# {wid}: {title}\n\n- status: {status}\n- section: next\n"
            f"- blocked-by: \n- group: \n- impact: feature\n- version: \n\n")


def _seed_work_items(root):
    d = root / "work-items"
    d.mkdir(parents=True, exist_ok=True)
    for filename, (wid, title, status) in ITEMS.items():
        (d / filename).write_text(_wi_text(wid, title, status), encoding="utf-8")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class ClaimsBase(unittest.TestCase):
    def landed_evidence(self, wid):
        """Give a done transition real trunk evidence, independent of the store."""
        path = self.main / f"delivery-{wid}.txt"
        path.write_text("shipped\n", encoding="utf-8")
        _git(self.main, "add", path.name)
        _git(self.main, "commit", "-qm", f"feat: deliver {wid}")

    def setUp(self):
        # Fictional holders (sid-1 / sid-9 / sid-B, worktree-poga-1 / -2) must not all
        # inherit the runner's journal and read as one holder (WI-0126). The classes
        # below whose SUBJECT is holder resolution opt back out by name.
        # Start every test from a KNOWN environment. These are set in any real poga
        # session, and the holder lookup reads all of them (WI-0061) — so leaving them in
        # place would point a fixture at the DEVELOPER'S live checkout and journal, and
        # the test would pass or fail depending on who ran it. Same hazard the
        # JOURNAL_DIR note below records, one layer out.
        #
        # The POGA_* half used to be a separate `neutralize_dispatch_env` call. It is the
        # same list: `AMBIENT_VARS` is `DISPATCH_ENV_VARS` plus the identity axis, derived
        # from it and pinned by `test_ambient_fixture_guard` (WI-0275). The hand-pop that
        # preceded both named `CLAUDE_CODE_SESSION_ID` and was, like every hand-kept copy,
        # missing the rest.
        #
        # FIRST, before the fixture builds anything. This call snapshots the environment
        # and restores that snapshot wholesale at cleanup, so a variable set before it
        # gets baked in and outlives the test (WI-0275).
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        _seed_work_items(self.main)
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(self.laneB), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}

    def _journal(self, checkout, sid, *, runtime="", claude_sid="", ended=""):
        """Write an open journal into `checkout`'s journal dir and return its id."""
        jdir = checkout / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / f"{sid}.md").write_text(session.render_journal({
            "session-id": sid, "ordinal": 7, "title": "(in progress)",
            "machine": "Runner", "runtime": runtime, "role-doc-version": "v1.0.0",
            "base-commit": "abc1234", "started": "2026-08-01T03:00:00+00:00",
            "ended": ended, "claude-session-id": claude_sid,
        }), encoding="utf-8")
        return sid

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)


class ParserTest(ClaimsBase):
    def test_only_open_and_in_progress_items_are_claimable(self):
        session.ROOT = self.laneA
        items = dict(session._wi_claimable_items())
        self.assertEqual(set(items), {"WI-0001", "WI-0002", "WI-0003"})  # not WI-0004 (done)
        self.assertEqual(items["WI-0001"], "Build alpha thing")
        self.assertEqual(items["WI-0003"], "Plain item, in progress")

    def test_parse_round_trips_status_and_blocked_by(self):
        session.ROOT = self.laneA
        d = self.laneA / "work-items"
        (d / "WI-0005-blocked.md").write_text(
            "# WI-0005: Needs alpha first\n\n- status: held\n- section: backlog\n"
            "- blocked-by: WI-0001, WI-0002\n- group: infra\n\nSome notes.\n",
            encoding="utf-8")
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0005"]["status"], "held")
        self.assertEqual(items["WI-0005"]["blocked_by"], ["WI-0001", "WI-0002"])
        self.assertEqual(items["WI-0005"]["group"], "infra")
        self.assertEqual(items["WI-0005"]["notes"], "Some notes.")


class StatusTest(ClaimsBase):
    def test_claimed_available_and_orphan(self):
        session.ROOT = self.laneA
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-1", 3600)
        session._coord_try_acquire("claims", "WI-9999", "worktree-poga-1", 3600)  # not in store
        st = session._claims_status()
        self.assertIn("WI-0001", st["claimed"])
        self.assertIn("WI-9999", st["orphan"])
        avail = {i for i, _ in st["available"]}
        self.assertEqual(avail, {"WI-0002", "WI-0003"})

    def test_orientation_line(self):
        session.ROOT = self.laneA
        self.assertIn("available", session._claims_orientation_line())
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-1", 3600)
        line = session._claims_orientation_line()
        # WI-0157: the holder half named a RECYCLED slot while the record's own journal
        # id sat unread one field away. The item on the left is the address here — it is
        # what `poga work claim` is typed with — so the right-hand half is free to be
        # what it should always have been: which session actually holds it.
        self.assertIn("WI-0001→", line)
        self.assertNotIn("worktree-poga-1", line,
                         "a recycled slot must not name the holder")


class IdentityTest(ClaimsBase):
    def test_identity_is_lane_branch_in_a_lane(self):
        session.ROOT = self.laneA
        self.assertEqual(session._coord_identity(), "worktree-poga-1")


class CoordRecordIdentifiesItsHolderTest(ClaimsBase):
    """WI-0061 — a record named a CHECKOUT, never a session or a runtime.

    `session_id` on a record is an ownership KEY (`_coord_identity`), not an identity: in
    a lane it is the branch, so every record written through the main-checkout-anchored
    front door (`poga work`, `adr-next` — both correct by ADR-0073) reads as held by
    `main` whichever session actually holds it. Session ~113 hit the consequence: the
    ADR-0069 land gate refused this lane's own ADR number as reserved by `main`, because
    from the record it genuinely was. Cost a renumber and a permanent gap at ADR-0083."""

    def setUp(self):
        super().setUp()
        # This class's subject IS `_coord_holder`; it owns its inputs (JOURNAL_DIR,
        # CLAUDE_CODE_SESSION_ID) and asserts on what the real function resolves.
        exercise_real_coord_holder(self)

    def test_a_record_carries_the_runtime_and_the_journal_that_holds_it(self):
        session.ROOT = self.laneA
        jdir = self.laneA / "sessions" / "journal"
        self._journal(self.laneA, "20260801T0300Z-runner-abcd",
                      runtime="gemini-antigravity", claude_sid="sid-1")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-1"
        with mock.patch.object(session, "JOURNAL_DIR", jdir):
            rec = session._coord_record(session._coord_identity(), 900)

        self.assertEqual(rec["journal"], "20260801T0300Z-runner-abcd")
        self.assertEqual(rec["runtime"], "gemini-antigravity",
                         "a non-Claude holder must not be recorded as Claude")
        self.assertEqual(rec["runtime_source"], "journal",
                         "read off the holder's own journal — an OBSERVATION")
        self.assertEqual(rec["session_id"], "worktree-poga-1",
                         "the ownership key is UNCHANGED — these fields are additive, so "
                         "matching, release and the land gate behave exactly as before")

    def test_it_degrades_rather_than_failing_when_there_is_no_journal(self):
        """A coordination write must never fail because a journal could not be read —
        and an unknown holder must read as unknown, not as a fabricated one.

        JOURNAL_DIR is patched to an EMPTY dir deliberately: it is a module-level constant
        bound to the real repo, so an unpatched run reads the live checkout's own open
        journal. This test caught that itself — it reported this session's real id — and
        without the patch it would pass or fail depending on whether the developer running
        it happened to have a session open, which is a test that proves nothing."""
        session.ROOT = self.laneA
        empty = self.tmp / "no-journals"
        empty.mkdir()
        with mock.patch.object(session, "JOURNAL_DIR", empty):
            rec = session._coord_record(session._coord_identity(), 900)
        self.assertEqual(rec["journal"], "")
        self.assertTrue(rec["runtime"], "still says which runtime, from config/default")
        self.assertEqual(rec["runtime_source"], "default",
                         "and it SAYS the value is a fallback rather than an observation")


class HolderIsFoundFromTheAnchoredCheckoutTest(ClaimsBase):
    """WI-0061 reopened (session ~121) — the fields shipped, and neither one worked.

    Both causes share a shape: the lookup ran somewhere the answer could not be, and
    reported the miss as a value rather than as a miss.

    CAUSE 2 (the journal): a coordination write goes through the MAIN-checkout-anchored
    front door by design (ADR-0073), so `JOURNAL_DIR` is main's while the writing session
    lives in a lane. The old code looked only in `JOURNAL_DIR`, so EVERY lane record —
    Claude included — recorded `journal: ""`. Probed live: a claim taken from a lane whose
    journal was sitting on disk twelve directories away came back empty.

    CAUSE 1 (the runtime): the value was seeded from config and overwritten only when a
    journal matched on `claude-session-id`. A codex session sets no `CLAUDE_CODE_SESSION_ID`
    and its journal's `claude-session-id` is empty, so the key was empty, the guard was
    falsy, and the record kept `claude-code`. Not "sometimes wrong" — there was no route by
    which that path could ever name a non-Claude runtime.

    Every test here puts the journal in a LANE and `JOURNAL_DIR` on MAIN, because that is
    the configuration the front door actually creates. Pointing `JOURNAL_DIR` at the lane
    would confirm the code's own premise and prove nothing
    ([`verify-in-the-created-configuration`])."""

    def setUp(self):
        super().setUp()
        # Same reason as the class above: the real `_coord_holder` is the thing under
        # test, driven from inputs this class sets rather than from the ambient session.
        exercise_real_coord_holder(self)

    def _anchored(self):
        """The front door's real configuration: driving from MAIN, journal dir on MAIN."""
        session.ROOT = self.main
        return mock.patch.object(session, "JOURNAL_DIR",
                                 self.main / "sessions" / "journal")

    def test_a_lanes_journal_is_found_although_the_write_is_anchored_on_main(self):
        sid = self._journal(self.laneA, "20260807T1200Z-runner-22c9", claude_sid="sid-9")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-9"
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["journal"], sid,
                         "the holding session's journal is in the LANE; looking only in "
                         "the anchored checkout is why this field was always empty")

    def test_the_right_lane_wins_when_two_lanes_both_have_open_journals(self):
        self._journal(self.laneA, "20260807T1000Z-runner-aaaa", claude_sid="sid-A")
        mine = self._journal(self.laneB, "20260807T1100Z-runner-bbbb", claude_sid="sid-B")
        os.environ["CLAUDE_CODE_SESSION_ID"] = "sid-B"
        with self._anchored():
            rec = session._coord_record("worktree-poga-2", 900)
        self.assertEqual(rec["journal"], mine,
                         "matched on this session's own id, not on whichever lane sorts "
                         "first — two concurrent lanes is the normal case, not the edge")

    def test_a_codex_lane_is_not_recorded_as_claude(self):
        """CAUSE 1. No `CLAUDE_CODE_SESSION_ID`, and the journal's `claude-session-id` is
        empty — exactly what a non-Claude session leaves behind (verified on a real
        codex journal)."""
        sid = self._journal(self.laneA, "20260806T1200Z-laptop-9106", runtime="codex-cli")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["runtime"], "codex-cli")
        self.assertEqual(rec["runtime_source"], "journal")
        self.assertEqual(rec["journal"], sid)

    def test_the_launcher_names_the_runtime_when_no_journal_exists_yet(self):
        """A record can be written before the journal is on disk (prep, or a lane whose
        agent has not acted). `poga` exports the registry row it resolved, so there is
        still a real answer — the launcher's, not a guess."""
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        os.environ["POGA_RUNTIME_ID"] = "codex-cli"
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["runtime"], "codex-cli")
        self.assertEqual(rec["runtime_source"], "launcher")
        self.assertEqual(rec["journal"], "", "no journal exists; say so, don't invent one")

    def test_the_journal_outranks_the_launcher(self):
        """An inherited env var is intent; the journal is what the session actually is."""
        self._journal(self.laneA, "20260807T1300Z-runner-cccc", runtime="gemini-antigravity")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        os.environ["POGA_RUNTIME_ID"] = "codex-cli"
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["runtime"], "gemini-antigravity")
        self.assertEqual(rec["runtime_source"], "journal")

    def test_two_open_journals_and_no_session_id_reports_unknown_rather_than_guessing(self):
        """The one genuinely ambiguous case. A non-Claude session has no key to match on,
        so with two open journals in its checkout there is no honest answer — and a guess
        recorded as fact is the defect this whole item is about."""
        self._journal(self.laneA, "20260807T1000Z-runner-dddd", runtime="codex-cli")
        self._journal(self.laneA, "20260807T1100Z-runner-eeee", runtime="codex-cli")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["journal"], "")
        self.assertEqual(rec["runtime_source"], "default",
                         "nothing was observed, and the record says so")

    def test_a_closed_journal_is_not_this_sessions_journal(self):
        self._journal(self.laneA, "20260807T0900Z-runner-ffff", runtime="codex-cli",
                      ended="2026-08-07T10:00:00+00:00")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        with self._anchored():
            rec = session._coord_record("worktree-poga-1", 900)
        self.assertEqual(rec["journal"], "")


class AssumedRuntimeNeverPrintsAsObservedTest(ClaimsBase):
    """[`declare-what-a-check-assumes`] — "I could not tell" and "I checked" must not
    render identically. This is the half that made the reopened defect invisible: a
    `claude-code` default printed exactly like a `claude-code` observation, so the store
    looked like it was answering a question it had never asked."""

    def test_a_fallback_runtime_is_marked_assumed(self):
        label = session._coord_runtime_label(
            {"runtime": "claude-code", "runtime_source": "default"})
        self.assertIn("assumed", label)

    def test_an_observed_runtime_is_printed_bare(self):
        label = session._coord_runtime_label(
            {"runtime": "codex-cli", "runtime_source": "journal"})
        self.assertEqual(label, "codex-cli")

    def test_a_record_written_before_the_source_field_reads_unverified(self):
        label = session._coord_runtime_label({"runtime": "claude-code"})
        self.assertIn("unverified", label)

    def test_the_holder_phrase_always_names_the_session_not_only_the_checkout(self):
        who = session._coord_who({"branch": "main", "runtime": "codex-cli",
                                  "runtime_source": "journal", "journal": "J-1"})
        self.assertIn("J-1", who,
                      "`main` is what every anchored write records; without the journal "
                      "id the printed line has NO field that distinguishes two holders")
        self.assertIn("codex-cli", who)


class CliTest(ClaimsBase):
    def _run(self, func, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                func(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_claim_unknown_id_exits_2(self):
        session.ROOT = self.laneA
        code, out = self._run(session.cmd_claim, id="nope", force=False)
        self.assertEqual(code, 2)
        self.assertIn("valid ids", out)

    def test_claim_then_conflict_exits_1(self):
        session.ROOT = self.laneA
        code, _ = self._run(session.cmd_claim, id="WI-0001", force=False)
        self.assertEqual(code, 0)
        # A DIFFERENT lane tries the same claimed id.
        session.ROOT = self.laneB
        code, out = self._run(session.cmd_claim, id="WI-0001", force=False)
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("worktree-poga-1", out)   # shown the holder

    def test_claim_and_release_take_the_bare_number(self):
        # `poga work --help`: "Ids take either form: WI-0028 or 28." Found by the basic
        # acceptance drill (WI-0455): `claim 1` refused and listed WI-0001 as valid.
        session.ROOT = self.laneA
        code, out = self._run(session.cmd_claim, id="1", force=False)
        self.assertEqual(code, 0, out)
        self.assertIn("'WI-0001'", out)
        self.assertIn("WI-0001", session._coord_list("claims"))
        code, out = self._run(session.cmd_release, id="1", force=False)
        self.assertEqual(code, 0, out)
        self.assertEqual(session._coord_list("claims"), {})

    def test_a_forced_ad_hoc_claim_keeps_its_own_name(self):
        session.ROOT = self.laneA
        code, _ = self._run(session.cmd_claim, id="spike-cache", force=True)
        self.assertEqual(code, 0)
        self.assertIn("spike-cache", session._coord_list("claims"))

    def test_release_respects_holder(self):
        session.ROOT = self.laneA
        self._run(session.cmd_claim, id="WI-0001", force=False)
        session.ROOT = self.laneB
        code, _ = self._run(session.cmd_release, id="WI-0001", force=False)
        self.assertEqual(code, 1)                # laneB isn't the holder
        session.ROOT = self.laneA
        code, _ = self._run(session.cmd_release, id="WI-0001", force=False)
        self.assertEqual(code, 0)

    def test_release_identity_frees_all_lane_claims(self):
        session.ROOT = self.laneA
        self._run(session.cmd_claim, id="WI-0001", force=False)
        self._run(session.cmd_claim, id="WI-0002", force=False)
        n = len(session._lane_exit_release("worktree-poga-1"))
        self.assertEqual(n, 2)
        self.assertEqual(session._coord_list("claims"), {})


class WiNewStatusListTest(ClaimsBase):
    def _run(self, func, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                func(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_wi_new_draws_and_writes(self):
        session.ROOT = self.laneA
        # `scope` is required at creation (WI-0323) -- an item minted without one is
        # refused, so every fixture that mints one declares it, exactly as they all
        # already declare `impact` for the store to validate.
        code, out = self._run(session.cmd_wi_new, title="A new thing", section="next",
                              blocked_by="", group="", notes="detail", scope="harness")
        self.assertEqual(code, 0)
        self.assertIn("WI-0005", out)
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0005"]["title"], "A new thing")
        self.assertEqual(items["WI-0005"]["notes"], "detail")

    def test_wi_status_updates_in_place_without_touching_other_files(self):
        session.ROOT = self.laneA
        self.landed_evidence("WI-0001")
        before = (self.laneA / "work-items" / "WI-0002-beta.md").stat().st_mtime
        code, out = self._run(session.cmd_wi_status, id="WI-0001", status="done",
                              section=None, blocked_by=None)
        self.assertEqual(code, 0)
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0001"]["status"], "done")
        after = (self.laneA / "work-items" / "WI-0002-beta.md").stat().st_mtime
        self.assertEqual(before, after, "updating WI-0001 must not rewrite WI-0002's file")

    def test_wi_status_unknown_id_exits_1(self):
        session.ROOT = self.laneA
        code, out = self._run(session.cmd_wi_status, id="WI-9999", status="done",
                              section=None, blocked_by=None)
        self.assertEqual(code, 1)

    def test_wi_list_excludes_done_and_pins_a_snapshot(self):
        session.ROOT = self.laneA
        os.environ["CLAUDE_CODE_SESSION_ID"] = "test-session-1"
        code, out = self._run(session.cmd_wi_list)
        self.assertEqual(code, 0)
        self.assertIn("WI-0001", out)
        self.assertNotIn("WI-0004", out)  # done, excluded
        snap = self.laneA / ".session-state" / "wi-snapshot-test-session-1.json"
        self.assertTrue(snap.exists())


class OneListTest(ClaimsBase):
    """the operator's ruling, session ~103: one list. Nothing ever comes off it; an item is marked
    done, and any partitioned view is still more than one list.

    Two invariants. (1) An id is a PERMANENT handle — folding an item into another is a
    `superseded` status, never a deletion, because other items' notes and commit messages
    cite the id and a deleted file makes every one of those dangle. (2) The rendered list
    is ONE list carrying state as a tag, not several partitioned views the reader has to
    stitch together — and the ordinals must mean the same thing in the compact and full
    renderings, or "do 3" is ambiguous.
    """

    def _renumber(self):
        session.ROOT = self.main

    def test_superseded_is_a_valid_status(self):
        self._renumber()
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status="superseded", section=None, blocked_by=None, source=None))
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0002"]["status"], "superseded")

    def test_a_deleted_id_is_flagged_as_a_hole(self):
        self._renumber()
        (self.main / "work-items" / "WI-0002-beta.md").unlink()
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("WI-0002", holes[0])

    def test_a_complete_sequence_has_no_holes(self):
        self._renumber()
        self.assertEqual(session._wi_missing_numbers(), [])

    def test_a_superseded_item_is_not_a_hole(self):
        """The whole point: fold by STATUS and the id still resolves."""
        self._renumber()
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status="superseded", section=None, blocked_by=None, source=None))
        self.assertEqual(session._wi_missing_numbers(), [])

    def test_the_chart_is_columns_not_a_wall_of_text(self):
        """WI-0039. Completeness is about ROWS; readability is about COLUMNS. The rejected
        first cut printed every field and produced 1,200-char cells."""
        self._renumber()
        lines = session._wi_render_snapshot_lines(colour=False)
        row = [l for l in lines if "WI-0001" in l][0]
        # id, state, section, title — aligned, and no cell is a paragraph.
        self.assertRegex(row, r"^\s+WI-0001\s+\S+\s+(next|backlog)\s+\S")
        self.assertLessEqual(len(row), 120)

    def test_a_long_title_is_truncated_not_wrapped(self):
        self._renumber()
        session.cmd_wi_edit(argparse.Namespace(
            id="WI-0001", title="x" * 200, notes=None, append_notes=None, group=None))
        row = [l for l in session._wi_render_snapshot_lines(colour=False)
               if "WI-0001" in l][0]
        self.assertIn("…", row)
        self.assertLessEqual(len(row), 120)

    def test_no_escape_codes_when_colour_is_off(self):
        """The safety property. Escapes relayed through a session's tool output reach the
        user as literal garbage — worse than no colour (operator saw exactly that, twice)."""
        self._renumber()
        out = "\n".join(session._wi_render_snapshot_lines(colour=False))
        self.assertNotIn("\033", out)

    def test_colour_marks_state_on_the_text_itself(self):
        """Coloured dots were offered and rejected — the row text carries the state.

        Vocabulary is the operator's ruling (session ~105): green finished, red in flight. Pinned
        here because the two chart surfaces shipped from different lanes with different
        vocabularies for the same states, and only a test stops that re-diverging."""
        self._renumber()
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status="in-progress", section=None, blocked_by=None, source=None))
        out = "\n".join(session._wi_render_snapshot_lines(all_states=True, colour=True))
        self.assertIn(session.WI_ANSI["red"], out)        # in flight
        self.assertIn(session.WI_ANSI["dim_green"], out)  # finished, de-emphasised

    def test_no_color_env_beats_force_color(self):
        env = dict(os.environ)
        try:
            os.environ["POGA_FORCE_COLOR"] = "1"
            os.environ["NO_COLOR"] = "1"
            self.assertFalse(session._wi_colour_enabled())
            del os.environ["NO_COLOR"]
            self.assertTrue(session._wi_colour_enabled())
        finally:
            os.environ.clear()
            os.environ.update(env)

    def test_a_diverging_ordinal_is_shown_so_dispatch_cannot_pick_the_wrong_item(self):
        """`poga dispatch 1-10` resolves ordinals. They equal the WI number only while no
        id was ever skipped — a live sibling lane holding six ids breaks that. Printing a
        bare id there would make 'do 43' look like WI-0043 when it means WI-0049."""
        self._renumber()
        d = session._wi_dir()
        (d / "WI-0009-late.md").write_text(
            _wi_text("WI-0009", "Drawn after a gap", "open"), encoding="utf-8")
        rows = session._wi_render_snapshot_lines(all_states=True, colour=False)
        late = [l for l in rows if "WI-0009" in l][0]
        self.assertIn("→WI-0009", late)
        early = [l for l in rows if "WI-0001" in l][0]
        self.assertNotIn("→", early)      # matching ordinal stays quiet

    def test_blocked_reads_as_blocked_in_the_state_cell(self):
        self._renumber()
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by="WI-0001", source=None))
        row = [l for l in session._wi_render_snapshot_lines(colour=False)
               if "WI-0002" in l][0]
        self.assertIn("blocked", row)

    def test_a_claim_shows_the_holding_lane_in_the_state_cell(self):
        self._renumber()
        # The record's `branch` field is what the claim view keys on (same preference as
        # `_claims_orientation_line`), and in a real lane that IS the lane branch — so the
        # branch is what has to be simulated, not just the identity string.
        with mock.patch.object(session, "_current_branch", return_value="worktree-poga-7"):
            session._coord_try_acquire("claims", "WI-0001", "worktree-poga-7", 3600)
        row = [l for l in session._wi_render_snapshot_lines(colour=False)
               if "WI-0001" in l][0]
        self.assertIn("poga-7", row)
        self.assertNotIn("worktree-", row)     # the prefix is noise in a column

    def test_an_anchored_claim_names_the_session_not_main(self):
        """WI-0130 — the case the test above cannot reach, and the one that actually
        happens. `poga work claim` cd's to the MAIN checkout by design (ADR-0073), so the
        record's `branch` is `main` for every claim taken through the front door however
        deep in a lane the session lives. The cell rendered that verbatim as `open·main`,
        which reads as *held by main* — and a member's Architect hit the same field from the
        matching side, where its rung read a confident `none held` against a portfolio
        whose only claim was held by a lane.

        The journal is the one field that names a SESSION rather than a checkout, and the
        record has carried it since WI-0061."""
        self._renumber()
        with mock.patch.object(session, "_current_branch", return_value="main"), \
                mock.patch.object(session, "_coord_holder",
                                  return_value=("20260815T1200Z-runner-e9e5",
                                                "claude-code", "journal")):
            session._coord_try_acquire("claims", "WI-0001", "some-session-id", 3600)
        row = [l for l in session._wi_render_snapshot_lines(colour=False)
               if "WI-0001" in l][0]
        self.assertIn("runner-e9e5", row)
        self.assertNotIn("open·main", row,
                         "the anchor's branch is not the holder — that is the defect")

    def test_the_short_holder_falls_back_rather_than_printing_nothing(self):
        """Absence is not identity: a record with no journal must still name whatever it
        does know, never render an empty holder
        ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes))."""
        self.assertEqual(session._coord_short_holder(
            {"journal": "", "branch": "worktree-poga-3"}), "poga-3")
        self.assertEqual(session._coord_short_holder(
            {"journal": "", "branch": "", "session_id": "sid-9"}), "sid-9")
        self.assertEqual(session._coord_short_holder({}), "?")
        self.assertEqual(session._coord_short_holder(
            {"journal": "20260815T1200Z-runner-e9e5", "branch": "main"}), "runner-e9e5")

    def _applied_dir(self):
        # Pin ROOT to this test's scratch repo FIRST. Without it these helpers inherited
        # the module-global ROOT — the real federation checkout — and wrote fixture briefs
        # into it, which then leaked across tests and failed only under `discover`, never
        # in isolation. Test isolation is not optional when the subject reads the repo.
        session.ROOT = self.main
        d = session.ROOT / "proposed-edits" / "x-arch" / "applied"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_an_unstamped_auto_brief_in_applied_is_flagged(self):
        """WI-0041. The engine always stamps an auto brief it files, so an unstamped one
        was moved by hand — and moving it removed it from the runner queue, so the work
        failed SILENTLY instead of loudly (a member's enrollment brief, session ~104)."""
        d = self._applied_dir()
        (d / "b1.md").write_text("---\nedit-id: b1\napply: auto\n---\n\nbody\n",
                                 encoding="utf-8")
        found = session._applied_without_a_receipt(d)
        self.assertEqual(len(found), 1)
        self.assertIn("b1.md", found[0])
        self.assertIn("moved by hand", found[0])

    def test_a_stamped_auto_brief_is_quiet(self):
        d = self._applied_dir()
        (d / "b2.md").write_text(
            "---\nedit-id: b2\napply: auto\napplied: 2026-07-01\nstate: applied\n---\n",
            encoding="utf-8")
        self.assertEqual(session._applied_without_a_receipt(d), [])

    def test_an_unstamped_MANUAL_brief_is_quiet(self):
        """The false positive that would have sunk the guard: the R3 runner adopts manual
        briefs through a headless session that files them by hand, so they NEVER carry the
        engine stamp. Reporting them printed 24 lines on the first real run — one real
        finding would have been invisible among them."""
        d = self._applied_dir()
        (d / "b3.md").write_text(
            "---\nedit-id: b3\napply: manual\nmanual-reason: substrate install\n---\n",
            encoding="utf-8")
        self.assertEqual(session._applied_without_a_receipt(d), [])

    def test_a_brief_with_no_apply_header_is_quiet(self):
        """Undeclared mode is not evidence of anything; only `auto` carries the guarantee
        that makes a missing stamp meaningful."""
        d = self._applied_dir()
        (d / "b4.md").write_text("# just a brief\n\nno frontmatter at all\n",
                                 encoding="utf-8")
        self.assertEqual(session._applied_without_a_receipt(d), [])

    def test_a_missing_applied_dir_is_not_an_error(self):
        self.assertEqual(
            session._applied_without_a_receipt(session.ROOT / "nope" / "applied"), [])

    def test_a_number_held_by_a_live_concurrent_lane_is_not_a_hole(self):
        """Found live, session ~105: poga-3 held WI-0043…WI-0048 while poga-2 reported
        every one of them as a confident deletion. A drawn id's file lives on the lane
        that drew it until it lands — invisible from here, and NOT missing."""
        self._renumber()
        (session._wi_dir() / "WI-0002-beta.md").unlink()
        self.assertEqual(len(session._wi_missing_numbers()), 1)   # genuinely a hole now
        session._coord_try_acquire("wi-alloc", "0002", "worktree-poga-9", 3600)
        self.assertEqual(session._wi_missing_numbers(), [],
                         "a live wi-alloc hold must suppress the hole")

    def test_an_expired_hold_stops_shielding_its_number(self):
        """Unknown is its own answer, but an abandoned reservation is not unknown — it
        must not hide a real hole forever."""
        self._renumber()
        (session._wi_dir() / "WI-0002-beta.md").unlink()
        session._coord_try_acquire("wi-alloc", "0002", "worktree-poga-9", -1)  # already expired
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("WI-0002", holes[0])

    def test_a_drawn_but_never_created_id_is_not_a_deletion(self):
        """WI-0104 — the defect that wedged the whole fleet. WI-0092 was drawn in
        session ~125 and never used; its allocator hold expired mid-session ~129 and the
        same unchanged store began failing `poga work check`, which IS the merge gate, so
        no lane could land. The number was never an item: nothing cites it and nothing
        was lost. Git history is the discriminator — no add-commit on any ref, ever."""
        self._renumber()
        # The fixture seeds and COMMITS WI-0001..WI-0004. Removing 0002 is therefore a
        # real deletion — git has seen the file added — and must still fail.
        (self.main / "work-items" / "WI-0002-beta.md").unlink()
        _git(self.main, "rm", "-q", "--cached", "work-items/WI-0002-beta.md")
        _git(self.main, "commit", "-qm", "drop")
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1, "a file git has seen added is a deletion")
        self.assertIn("WI-0002", holes[0])
        # WI-0005 is the never-created case: creating WI-0006 raises the max, so 5 is a
        # gap — but no commit on any ref ever added a WI-0005 file. That is a consumed
        # draw, exactly WI-0092's shape.
        (self.main / "work-items" / "WI-0006-zeta.md").write_text(
            _wi_text("WI-0006", "Zeta", "open"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "add 0006")
        self.assertIn(5, session._wi_gap_numbers(), "5 is genuinely a gap")
        self.assertNotIn("WI-0005", " ".join(session._wi_missing_numbers()),
                         "WI-0005 was never created — a consumed draw, not a deletion")
        self.assertIn(5, session._wi_abandoned_draws(),
                      "and it is REPORTED as its own state, never silently dropped")
        # The deletion is untouched by the new branch — both facts coexist.
        self.assertIn("WI-0002", " ".join(session._wi_missing_numbers()))

    def test_an_unreadable_history_is_reported_as_a_hole_not_waved_through(self):
        """Fail closed. Defaulting an unknown history to 'never existed' would certify
        exactly the deletion the check exists to catch — the same
        `declare-what-a-check-assumes` trap the fix is fixing."""
        self._renumber()
        (session._wi_dir() / "WI-0002-beta.md").unlink()
        with mock.patch.object(session, "sh", side_effect=OSError("no git")):
            self.assertIsNone(session._wi_id_ever_existed(2))
            holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1, "unknown must still be reported")
        self.assertIn("WI-0002", holes[0])
        # ...and it must NOT prescribe the repair, because it could not tell which of the
        # two causes this is. Printing "restore a superseded stub" on an unprobed hole is
        # how a stub gets written over a body that is sitting on a branch (WI-0191).
        self.assertIn("UNKNOWN", holes[0])
        self.assertNotIn("must never be deleted", holes[0])

    # ── WI-0191: an id held by an unlanded lane is the THIRD state ───────────────
    #
    # `_wi_id_ever_existed` is tri-state but only `False` was routed anywhere; `True` and
    # `None` both landed on one message that says *deleted* and prescribes a `superseded`
    # stub. Found live on WI-0177/0178/0179, whose real bodies were on `worktree-poga-4`
    # along with a whole closed session, while the trunk printed three confident deletions.

    def _stranded(self, wid="WI-0005", title="Epsilon", branch="worktree-poga-1"):
        """Commit `wid` on a lane branch ONLY, so main has a gap the branch still holds."""
        lane = self.laneA if branch == "worktree-poga-1" else self.laneB
        name = f"{wid}-{title.lower()}.md"
        # WI-0006 on main raises the max so `wid` reads as a gap rather than the tail.
        (self.main / "work-items" / "WI-0006-zeta.md").write_text(
            _wi_text("WI-0006", "Zeta", "open"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "add 0006")
        (lane / "work-items" / name).write_text(_wi_text(wid, title, "open"),
                                                encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"add {wid} in the lane")
        return name

    def test_an_id_held_by_an_unlanded_lane_is_not_reported_as_a_deletion(self):
        """The defect. The body is on the branch; the message said it was gone and told
        the reader to write a stub over it."""
        self._renumber()
        self._stranded()
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        h = holes[0]
        self.assertIn("WI-0005", h)
        self.assertIn("worktree-poga-1", h, "name the branch that actually holds it")
        self.assertIn("UNLANDED LANE", h)
        self.assertNotIn("must never be deleted", h, "it was never deleted")
        self.assertNotIn("restore it with `status: superseded`", h,
                         "that remedy would write a stub over a real body")

    def test_the_unlanded_message_names_the_land_command_for_that_lane(self):
        """A verb the reader can run, not a diagnosis. `resume`+`merge` for a lane that
        still needs a mind, `recover` for one whose session is over (`poga --help`)."""
        self._renumber()
        self._stranded(branch="worktree-poga-2")
        h = session._wi_missing_numbers()[0]
        self.assertIn("poga resume 2", h)
        self.assertIn("python3 session.py merge", h)
        self.assertIn("poga recover --lane 2", h)

    def test_an_id_only_on_the_trunk_is_staleness_not_a_deletion(self):
        """WI-0239's trunk fix reaches the gap check too, since both read the same probe.
        Before it, a lane behind the trunk reported the trunk's own newer items as
        confident deletions — the WI-0191 defect one ref-namespace over, and the third
        time an id alive on a ref has been read as data loss in the trunk."""
        session.ROOT = self.laneA
        (self.laneA / "work-items" / "WI-0006-zeta.md").write_text(
            _wi_text("WI-0006", "Zeta", "open"), encoding="utf-8")
        _git(self.laneA, "add", "-A")
        _git(self.laneA, "commit", "-qm", "the lane raises the max")
        (self.laneA / "work-items" / "WI-0002-beta.md").unlink()
        _git(self.laneA, "rm", "-q", "--cached", "work-items/WI-0002-beta.md")
        _git(self.laneA, "commit", "-qm", "and drops 0002 from its own tree")
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("WI-0002", holes[0])
        self.assertIn("main", holes[0])
        self.assertIn("TRUNK", holes[0])
        self.assertNotIn("must never be deleted", holes[0])
        self.assertNotIn("UNLANDED LANE", holes[0])

    def test_a_deletion_after_the_lane_landed_still_reads_as_a_deletion(self):
        """The precision guard, and the case the live incident actually produced: WI-0192
        cherry-picked the three ids OUT of `worktree-poga-4` onto the trunk. Once a
        branch's work is on the trunk BY CONTENT, that branch holds nothing to land — so a
        file still absent here is a real deletion and must keep saying so. A SHA
        reachability test gets this exactly backwards after a cherry-pick recovery."""
        self._renumber()
        name = self._stranded()
        tip = subprocess.run([GIT, "-C", str(self.laneA), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        _git(self.main, "cherry-pick", tip)              # the lane's work lands...
        (self.main / "work-items" / name).unlink()       # ...and is then really deleted
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "delete it for real")
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("WI-0005", holes[0])
        self.assertIn("must never be deleted", holes[0])
        self.assertNotIn("UNLANDED", holes[0])

    def test_an_unprobeable_branch_list_says_unknown_rather_than_guessing(self):
        """`declare-what-a-check-assumes`: "couldn't tell" is its own printed answer,
        never folded into "no branch carries it" — which is the answer that prescribes the
        destructive repair."""
        self._renumber()
        self._stranded()
        real = session.sh

        def flaky(args, *a, **k):
            if args[:2] == ["git", "for-each-ref"]:
                raise OSError("no refs for you")
            return real(args, *a, **k)

        with mock.patch.object(session, "sh", side_effect=flaky):
            self.assertIsNone(session._wi_branch_holders([5]))
            holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("UNKNOWN", holes[0])
        self.assertNotIn("must never be deleted", holes[0])

    def test_a_partial_branch_view_is_unknown_not_a_clean_no(self):
        """One unreadable branch makes the whole answer unknown. A view that skipped the
        branch it could not read would report a confident "no branch carries it" over
        precisely the branch that does."""
        self._renumber()
        self._stranded()
        real = session.sh

        def flaky(args, *a, **k):
            if args[:2] == ["git", "ls-tree"]:
                return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
            return real(args, *a, **k)

        with mock.patch.object(session, "sh", side_effect=flaky):
            self.assertIsNone(session._wi_branch_holders([5]))

    def test_an_id_only_on_the_remote_is_a_refresh_not_a_deletion(self):
        """The instance nobody counted (WI-0177's note, 2026-08-30): WI-0175/0176 were on
        `origin/main` and not yet on the local trunk, and the same detector called it data
        loss. A remote is not a lane — it has no land verb — so it gets its own remedy."""
        self._renumber()
        for wid, title in (("WI-0006", "Zeta"), ("WI-0005", "Epsilon")):
            (self.main / "work-items" / f"{wid}-{title.lower()}.md").write_text(
                _wi_text(wid, title, "open"), encoding="utf-8")
            _git(self.main, "add", "-A")
            _git(self.main, "commit", "-qm", f"add {wid}")
        bare = self.tmp / "origin.git"
        _git(self.main, "init", "-q", "-b", "main", "--bare", str(bare))
        _git(self.main, "remote", "add", "origin", str(bare))
        _git(self.main, "push", "-q", "origin", "main")
        _git(self.main, "reset", "-q", "--hard", "HEAD~1")   # local trunk falls behind
        self.assertFalse((self.main / "work-items" / "WI-0005-epsilon.md").exists())
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        h = holes[0]
        self.assertIn("WI-0005", h)
        self.assertIn("origin/main", h)
        self.assertIn("pull --ff-only", h, "refresh, not land — a remote has no land verb")
        self.assertNotIn("UNLANDED LANE", h, "a remote is not a lane")
        self.assertNotIn("poga resume", h)
        self.assertNotIn("must never be deleted", h)

    def test_a_file_deleted_on_the_branch_you_stand_on_is_still_a_deletion(self):
        """The branch you are standing on is not "elsewhere." Without the HEAD exclusion a
        lane that deletes its own file would be told an unlanded lane — itself — is holding
        it, which is both wrong and unactionable.

        WI-0239 widened this from a NAME test to a BODY test, and the widening was forced
        by a regression it caught: skipping `here` by name does not stop an ANCESTOR of
        `here` — the trunk this lane was cut from, which carries the identical commit —
        from being named instead. The moment the trunk became nameable, this case started
        reporting as staleness. If your own HEAD has the body, nothing anywhere is
        stranded and no other ref may claim to be holding it."""
        session.ROOT = self.laneA
        (self.laneA / "work-items" / "WI-0006-zeta.md").write_text(
            _wi_text("WI-0006", "Zeta", "open"), encoding="utf-8")
        _git(self.laneA, "add", "-A")
        _git(self.laneA, "commit", "-qm", "the lane draws its own item")
        (self.laneA / "work-items" / "WI-0002-beta.md").unlink()
        holes = session._wi_missing_numbers()
        self.assertEqual(len(holes), 1)
        self.assertIn("WI-0002", holes[0])
        self.assertIn("must never be deleted", holes[0])
        self.assertNotIn("worktree-poga-1", holes[0], "do not point the lane at itself")
        self.assertNotIn("TRUNK", holes[0],
                         "nor at the trunk it was cut from — the body is on this HEAD")

    def _wi_check(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_check(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_a_lane_held_id_is_a_PROBLEM_not_a_quiet_note(self):
        """the operator's ruling, session ~187, relayed 2026-09-03: an id held by a deliberately
        stranded lane KEEPS FAILING `poga work check`.

        The tempting alternative was the `note:` channel, where `_wi_abandoned_draws`
        already lives — a consumed draw is a fact about the allocator with nothing to
        repair, so it states itself and does not fail. A lane-held id is not that: there IS
        a body, it IS off the trunk, and the remedy is a real action someone must take.
        ADR-0089 D3 makes stranded lanes deliberate and unswept, so the line recurs by
        design; recurring is the point, because quiet is how a lane holding a closed
        session gets forgotten.

        Pinned because a ruling that lives only in prose is not a ruling ([`verify-in-the-
        created-configuration`](habits/master.md#verify-in-the-created-configuration): a
        default asserted in prose but not pinned by a test is not a default). The exact
        regression this guards is a later reader deciding the lane case is benign and
        routing it to the note channel alongside the abandoned draws — which reads
        identically in the code and is the opposite decision."""
        self._renumber()
        self._stranded()
        code, out = self._wi_check(status=False)
        self.assertEqual(code, 1, "a lane-held id must FAIL the check, not merely mention it")
        self.assertIn("UNLANDED LANE", out)
        self.assertIn("problem(s):", out, "reported as a problem…")
        note = next((l for l in out.splitlines() if l.startswith("note:")), "")
        self.assertNotIn("WI-0005", note,
                         "…never demoted onto the non-failing allocator-note channel")

    def test_the_startup_line_counts_a_lane_held_id_among_the_problems(self):
        """`--status` is the surface a session actually sees at start. The ruling is
        worthless if the failure is only visible to someone who runs the check by hand."""
        self._renumber()
        self._stranded()
        code, out = self._wi_check(status=True)
        self.assertEqual(code, 0, "--status reports, it does not exit non-zero")
        self.assertIn("structural problem(s)", out)

    def test_an_abandoned_draw_stays_on_the_quiet_channel(self):
        """The other half of the same ruling: the case that genuinely has nothing to
        repair must NOT start failing. WI-0092 wedged the fleet by failing on an id that
        was never an item; this is the guard on that direction."""
        self._renumber()
        (self.main / "work-items" / "WI-0006-zeta.md").write_text(
            _wi_text("WI-0006", "Zeta", "open"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "add 0006")
        code, out = self._wi_check(status=False)
        self.assertEqual(code, 0, "a consumed draw is not a fault in the store")
        self.assertIn("WI-0005", out)
        self.assertIn("drawn but never created", out)

    def test_a_sound_store_never_pays_for_the_branch_probe(self):
        """The scan is O(branches) git calls, so it must not run on the startup path of a
        store with no gaps — the normal case."""
        self._renumber()
        with mock.patch.object(session, "_wi_branch_holders") as probe:
            self.assertEqual(session._wi_missing_numbers(), [])
        probe.assert_not_called()

    def test_the_list_shows_every_item_regardless_of_section(self):
        """Backlog is a tag, not a separate list."""
        self._renumber()
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section="backlog", blocked_by=None, source=None))
        lines = "\n".join(session._wi_render_snapshot_lines())
        self.assertIn("WI-0002", lines)
        self.assertIn("backlog", lines)

    def test_terminal_items_are_counted_not_dropped(self):
        self._renumber()
        lines = "\n".join(session._wi_render_snapshot_lines())
        self.assertNotIn("WI-0004", lines)          # done — summarised, not printed
        self.assertIn("1 done/superseded not shown", lines)
        self.assertIn("4 total", lines)             # …but still counted in the list

    def test_all_states_prints_every_row(self):
        self._renumber()
        lines = "\n".join(session._wi_render_snapshot_lines(all_states=True))
        for wid in ("WI-0001", "WI-0002", "WI-0003", "WI-0004"):
            self.assertIn(wid, lines)
        self.assertIn("done", lines)

    def test_ordinals_are_identical_in_both_renderings(self):
        """'do 3' must mean the same item whichever view produced the list — which is why
        ordinals are assigned over the whole ordered set, not over what happens to print.

        Asserted against the PINNED SNAPSHOT rather than the printed prefix: the snapshot
        mapping is what `poga dispatch` actually resolves against, and since WI-0039 the
        chart only prints an ordinal when it diverges from the id. Testing the display
        would have been testing the wrong artifact — the earlier version of this test did
        exactly that and broke on a pure formatting change while the property it cared
        about was untouched."""
        self._renumber()

        def pinned(**kw):
            session._wi_render_snapshot_lines(colour=False, **kw)
            sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "app")
            raw = json.loads((session.ROOT / ".session-state" /
                              f"wi-snapshot-{sid}.json").read_text(encoding="utf-8"))
            return raw["mapping"]

        c, f = pinned(), pinned(all_states=True)
        self.assertTrue(c)
        self.assertEqual(c, f, "the ordinal→id map must not depend on which view printed")


class CodeManagedStoreTest(ClaimsBase):
    """the operator's ruling, session ~103: the store is managed by code — the three gaps where an
    operator still had to open a file in an editor, which is how invariants get broken
    (WI-0022 was hand-deleted rather than superseded). Editing, bulk moves, and
    validation now all have commands.
    """

    def setUp(self):
        super().setUp()
        session.ROOT = self.main

    def _run(self, func, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                func(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def _item(self, wid):
        return {it["id"]: it for it in session._wi_parse()}[wid]

    def test_edit_replaces_the_title_and_renames_the_file(self):
        code, _ = self._run(session.cmd_wi_edit, id="WI-0002", title="A brand new title",
                            notes=None, append_notes=None, group=None)
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0002")["title"], "A brand new title")
        names = {p.name for p in (self.main / "work-items").glob("WI-0002-*.md")}
        self.assertEqual(len(names), 1, "a retitle must not leave two files for one id")
        self.assertIn("brand", names.pop())

    def test_append_notes_keeps_what_is_already_there(self):
        self._run(session.cmd_wi_edit, id="WI-0002", title=None, notes="first finding",
                  append_notes=None, group=None)
        self._run(session.cmd_wi_edit, id="WI-0002", title=None, notes=None,
                  append_notes="second finding", group=None)
        notes = self._item("WI-0002")["notes"]
        self.assertIn("first finding", notes)
        self.assertIn("second finding", notes)

    def test_edit_with_no_fields_is_refused(self):
        code, out = self._run(session.cmd_wi_edit, id="WI-0002", title=None, notes=None,
                              append_notes=None, group=None)
        self.assertEqual(code, 2)
        self.assertIn("nothing to change", out)

    def test_edit_unknown_id_exits_1(self):
        code, _ = self._run(session.cmd_wi_edit, id="WI-9999", title="x", notes=None,
                            append_notes=None, group=None)
        self.assertEqual(code, 1)

    def test_move_accepts_bare_numbers_and_wi_ids_together(self):
        code, _ = self._run(session.cmd_wi_move, ids=["1", "WI-0002", "3"],
                            section="backlog", status=None)
        self.assertEqual(code, 0)
        for wid in ("WI-0001", "WI-0002", "WI-0003"):
            self.assertEqual(self._item(wid)["section"], "backlog")

    def test_move_refuses_the_whole_batch_on_an_unknown_id(self):
        """Never half-apply: a typo in one id must not silently move the others."""
        code, out = self._run(session.cmd_wi_move, ids=["1", "9999"],
                              section="backlog", status=None)
        self.assertEqual(code, 1)
        self.assertIn("nothing changed", out)
        self.assertEqual(self._item("WI-0001")["section"], "next")

    def test_move_handles_a_comma_separated_list(self):
        code, _ = self._run(session.cmd_wi_move, ids=["1,2"], section="backlog", status=None)
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0002")["section"], "backlog")

    def test_validate_is_quiet_on_a_sound_store(self):
        self.assertEqual(session._wi_validate(), [])

    def test_validate_flags_a_blocked_by_pointing_at_nothing(self):
        self._run(session.cmd_wi_status, id="WI-0001", status=None, section=None,
                  blocked_by="WI-4242", source=None)
        problems = session._wi_validate()
        self.assertTrue(any("WI-4242" in p for p in problems))

    def test_validate_flags_a_self_block(self):
        self._run(session.cmd_wi_status, id="WI-0001", status=None, section=None,
                  blocked_by="WI-0001", source=None)
        self.assertTrue(any("blocked by itself" in p for p in session._wi_validate()))

    def test_validate_flags_a_superseded_item_naming_no_successor(self):
        """A superseded id must lead somewhere, or it is a dead end — which is the very
        thing that made deleting WI-0022 feel acceptable."""
        self._run(session.cmd_wi_status, id="WI-0002", status="superseded", section=None,
                  blocked_by=None, source=None)
        problems = session._wi_validate()
        self.assertTrue(any("no successor" in p for p in problems),
                        f"ROOT={session.ROOT} problems={problems}")
        self._run(session.cmd_wi_edit, id="WI-0002", title=None,
                  notes="Folded into WI-0001.", append_notes=None, group=None)
        self.assertEqual([p for p in session._wi_validate() if "successor" in p], [])

    def test_validate_flags_a_hand_broken_status(self):
        """The failure mode the edit commands exist to remove — someone typing into the
        file directly."""
        p = self.main / "work-items" / "WI-0002-beta.md"
        p.write_text(p.read_text().replace("- status: open", "- status: banana"))
        self.assertTrue(any("banana" in x for x in session._wi_validate()))

    def test_show_prints_fields_and_notes(self):
        self._run(session.cmd_wi_edit, id="WI-0002", title=None, notes="the reasoning here",
                  append_notes=None, group="infra")
        code, out = self._run(session.cmd_wi_show, id="WI-0002")
        self.assertEqual(code, 0)
        self.assertIn("Ship beta", out)
        self.assertIn("status:", out)
        self.assertIn("infra", out)
        self.assertIn("the reasoning here", out)

    def test_show_accepts_a_bare_number(self):
        code, out = self._run(session.cmd_wi_show, id="2")
        self.assertEqual(code, 0)
        self.assertIn("WI-0002", out)

    def test_show_unknown_id_exits_1(self):
        code, _ = self._run(session.cmd_wi_show, id="9999")
        self.assertEqual(code, 1)

    # --- WI-0370: one read surface, two stores --------------------------------

    def _seed_ops(self, oid="OPS-0007", title="Weekly findings review"):
        """One obligation on disk, written as the store renders it.

        Deliberately NOT `_ops_write_item`: that auto-commits and rewrites the feed, and
        neither is the subject here. What is being tested is whether an id an operator
        TYPED reaches the right store."""
        d = self.main / "ops-items"
        d.mkdir(exist_ok=True)
        (d / f"{oid}-weekly.md").write_text(session._ops_render_item({
            "id": oid, "title": title, "status": "open", "cadence": "weekly",
            "last_completed": "2026-09-11", "last_result": "findings", "due": "",
            "group": "Governance", "source": "", "notes": "the obligation body",
        }), encoding="utf-8")
        return oid

    def test_show_resolves_an_ops_id_to_the_ops_store(self):
        """THE CRASH (WI-0370 / A2). `wid = raw if raw.upper().startswith("WI-") else
        f"WI-{int(raw):04d}"` raised `ValueError: invalid literal for int()` on every OPS
        id, so the one surface built to keep a reader OUT of the item files sent every
        reviewer back into them for the whole ops half of a findings review."""
        self._seed_ops()
        code, out = self._run(session.cmd_wi_show, id="OPS-0007")
        self.assertEqual(code, 0, out)
        self.assertIn("Weekly findings review", out)
        self.assertIn("cadence:", out, "an obligation renders as an obligation, not as a WI")
        self.assertIn("the obligation body", out)

    def test_show_still_reads_a_wi_id_and_a_bare_number_from_the_work_item_store(self):
        """The half that must NOT move. An OPS store on disk does not get to change what
        `WI-0002` or `2` has always meant — a bare number is a work item."""
        self._seed_ops()
        for raw in ("WI-0002", "2"):
            code, out = self._run(session.cmd_wi_show, id=raw)
            self.assertEqual(code, 0, out)
            self.assertIn("WI-0002", out)
            self.assertNotIn("cadence:", out)

    def test_show_normalises_a_short_or_lower_case_id(self):
        """Both were silent wrong answers rather than errors. `wi-0002` found the FILE
        (the glob keys on the number) and then missed the parsed item, whose id is
        upper-case — an `AttributeError` one line on. `WI-2` found nothing at all while
        looking exactly like a typo that ought to have worked."""
        for raw in ("wi-0002", "WI-2", "wi-2"):
            code, out = self._run(session.cmd_wi_show, id=raw)
            self.assertEqual(code, 0, f"{raw}: {out}")
            self.assertIn("WI-0002", out)
        self._seed_ops()
        code, out = self._run(session.cmd_wi_show, id="ops-7")
        self.assertEqual(code, 0, out)
        self.assertIn("OPS-0007", out)

    def test_show_names_the_shapes_it_looked_for_on_an_unparseable_id(self):
        """A traceback is not an answer. The message has to say what SHOULD have been
        typed, because the reader who gets here is the reader who does not know."""
        for raw in ("banana", "ADR-0124", "", "WI-"):
            code, out = self._run(session.cmd_wi_show, id=raw)
            self.assertEqual(code, 2, f"{raw!r} -> {out}")
            self.assertIn("not an id I recognise", out)
            self.assertIn("OPS-", out)
            self.assertIn("WI-", out)
            self.assertIn("bare number", out)
            self.assertNotIn("Traceback", out)

    def test_an_ops_id_for_an_obligation_that_does_not_exist_is_reported_not_raised(self):
        """A well-formed id pointing at nothing is a MISS, and a miss names the store it
        looked in — the failure a bare traceback hid."""
        self._seed_ops()
        code, out = self._run(session.cmd_wi_show, id="OPS-9999")
        self.assertEqual(code, 1, out)
        self.assertIn("OPS-9999", out)
        self.assertIn(session.OPS_DIRNAME, out)

    def test_show_resolves_blocked_by_against_live_state(self):
        """Blocked-by is reported as open vs closed at READ time, not as the file's own
        stale opinion about other items."""
        self._run(session.cmd_wi_status, id="WI-0001", status=None, section=None,
                  blocked_by="WI-0003,WI-0004", source=None)   # 0003 open, 0004 done
        _code, out = self._run(session.cmd_wi_show, id="WI-0001")
        self.assertIn("open: WI-0003", out)
        self.assertIn("closed: WI-0004", out)

    def test_check_status_mode_is_silent_when_sound(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_wi_check(argparse.Namespace(status=True))
        self.assertEqual(buf.getvalue().strip(), "")


class SourceProvenanceTest(ClaimsBase):
    """ADR-0074: a triaged item POINTS at its source brief rather than restating it."""

    def test_source_round_trips_through_write_and_parse(self):
        session.ROOT = self.main
        session.cmd_wi_new(argparse.Namespace(
            title="From a brief", section="next", blocked_by="", group="",
            source="proposed-edits/pending/x.md", notes="", scope="harness"))
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0005"]["source"], "proposed-edits/pending/x.md")

    def test_wi_new_without_a_source_leaves_it_empty(self):
        """Items that originated as work, not as inbound mail, carry no source."""
        session.ROOT = self.main
        session.cmd_wi_new(argparse.Namespace(
            title="Native item", section="next", blocked_by="", group="", notes="",
            scope="product"))
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0005"]["source"], "")

    def test_wi_status_can_backfill_a_source(self):
        session.ROOT = self.main
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0001", status=None, section=None, blocked_by=None,
            source="proposed-edits/accepted/y.md"))
        items = {it["id"]: it for it in session._wi_parse()}
        self.assertEqual(items["WI-0001"]["source"], "proposed-edits/accepted/y.md")


class TriageDebtTest(ClaimsBase):
    """ADR-0074's structural guard. The inbox is a mailbox whose normal state is empty;
    a brief left in `pending/` past the grace period is untriaged debt."""

    def setUp(self):
        super().setUp()
        session.ROOT = self.main
        self.pending = self.main / "proposed-edits" / "pending"
        self.pending.mkdir(parents=True)
        session.CFG["inbox"] = self.pending

    def _age(self, path, days):
        old = time.time() - days * 86400
        os.utime(path, (old, old))

    def _check(self, **kw):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_inbox_check(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_empty_inbox_is_clean(self):
        overdue, broken, _untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual((overdue, broken), ([], []))

    def test_an_id_hole_is_not_filed_under_the_accepted_brief_header(self):
        """WI-0191, second half. The hole lines used to be appended to `untracked` and
        printed under *"nothing carries it. Mint one with `wi-new --source`"* — so the
        remedy an operator read for a lane-held id was *mint a duplicate*. Correcting the
        sentence inside a bucket whose header prescribes a different wrong repair fixes
        nothing the reader can see."""
        (self.main / "work-items" / "WI-0002-beta.md").unlink()
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, [], "a hole is not an untracked brief")
        code, out = self._check(status=False)
        self.assertEqual(code, 1)
        self.assertIn("WI-0002", out)
        self.assertIn("this tree does not carry", out)
        self.assertNotIn("wi-new --source", out,
                         "the mint-a-duplicate remedy must not head the hole lines")

    def test_the_status_line_counts_id_holes_as_their_own_kind(self):
        """A count folded into another bucket's label is a mislabelled count — the startup
        surface said 'accepted brief(s) with no work item' about a missing id."""
        (self.main / "work-items" / "WI-0002-beta.md").unlink()
        code, out = self._check(status=True)
        self.assertEqual(code, 0)
        self.assertIn("1 work-item id(s) not in this tree", out)
        self.assertNotIn("accepted brief(s)", out)

    def test_a_fresh_brief_is_not_yet_debt(self):
        (self.pending / "new.md").write_text("brief", encoding="utf-8")
        overdue, _broken, _untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(overdue, [])

    def test_a_brief_past_the_grace_period_is_flagged(self):
        p = self.pending / "stale.md"
        p.write_text("brief", encoding="utf-8")
        self._age(p, session.TRIAGE_GRACE_DAYS + 1)
        overdue, _broken, _untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(overdue, ["stale.md"])

    def test_a_dead_source_path_is_flagged(self):
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by=None,
            source="proposed-edits/accepted/gone.md"))
        _overdue, broken, _untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(len(broken), 1)
        self.assertIn("WI-0002", broken[0])

    def test_a_live_source_path_is_not_flagged(self):
        acc = self.main / "proposed-edits" / "accepted"
        acc.mkdir()
        (acc / "here.md").write_text("brief", encoding="utf-8")
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by=None,
            source="proposed-edits/accepted/here.md"))
        _overdue, broken, _untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(broken, [])

    def test_from_a_lane_the_store_is_read_from_the_main_checkout(self):
        """Both halves of the join must come from the SAME checkout.

        The inbox is gitignored, so `inbox_dirs()` re-bases it onto the shared work root
        and a lane reads the MAIN checkout's live mail. The store is tracked, so a lane
        carries its own copy frozen at that lane's base commit. Reading one live and one
        stale made this guard report every freshly-triaged brief as uncarried from any
        open lane — a false "nothing carries this" in the check whose whole job is to
        notice work going missing.

        No mocking: the fixture's lanes are real worktrees, and the source edit below
        lands in main AFTER they were cut, so the lane's copy genuinely lacks it.
        """
        acc = self.main / "proposed-edits" / "accepted"
        acc.mkdir()
        (acc / "carried.md").write_text("brief", encoding="utf-8")
        session.ROOT = self.main
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by=None,
            source="proposed-edits/accepted/carried.md"))

        # The lane's tracked store predates that edit — the stale half of the join.
        lane_items = {it["id"]: it for it in session._wi_parse(self.laneA)}
        self.assertEqual(lane_items["WI-0002"]["source"], "",
                         "fixture precondition: the lane's copy must not carry the edit")

        session.ROOT = self.laneA
        _overdue, broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, [],
                         "the brief IS carried in main's store; a lane must not report it "
                         "as uncarried just because its own checkout is behind")
        self.assertEqual(broken, [],
                         "the source resolves against the main checkout, not the lane root")

    def test_an_unreadable_inbox_reports_not_checked_never_clean(self):
        """[`declare-what-a-check-assumes`]: 'couldn't tell' must not read as 'fine'."""
        session.CFG["inbox"] = self.main / "does-not-exist"
        overdue, broken, _untracked, skip = session._triage_debt()
        self.assertIsNotNone(skip)
        self.assertIn("not readable", skip)
        self.assertEqual((overdue, broken), ([], []))

    def test_a_repo_with_no_work_item_store_reports_not_checked(self):
        shutil.rmtree(self.main / "work-items")
        _overdue, _broken, _untracked, skip = session._triage_debt()
        self.assertIsNotNone(skip)
        self.assertIn("work-item store", skip)

    def _accept(self, name="brief.md"):
        """File a brief into `accepted/` — the state that means 'a work item carries this'."""
        acc = self.main / "proposed-edits" / "accepted"
        acc.mkdir(exist_ok=True)
        (acc / name).write_text("brief", encoding="utf-8")
        return acc / name

    def test_an_accepted_brief_with_no_work_item_is_flagged(self):
        """The reverse direction, missing from the first cut of this guard.

        A brief filed as accepted while its work item was never minted reads as fully
        handled: it is out of `pending/` so nothing surfaces it, and no item exists to
        carry it. Checking only item->brief reported success in exactly that case.
        """
        self._accept("orphaned-brief.md")
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, ["orphaned-brief.md"])

    def test_an_accepted_brief_carried_by_a_work_item_is_not_flagged(self):
        self._accept("carried.md")
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by=None,
            source="proposed-edits/accepted/carried.md"))
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, [])

    def test_a_done_work_item_still_counts_as_carrying_its_brief(self):
        """Finished work is still tracked work — a completed item must not make its own
        brief read as orphaned."""
        self._accept("carried.md")
        self.landed_evidence("WI-0002")
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status="done", section=None, blocked_by=None,
            source="proposed-edits/accepted/carried.md"))
        _overdue, _broken, untracked, _skip = session._triage_debt()
        self.assertEqual(untracked, [])

    def test_a_source_recorded_with_a_different_path_prefix_still_counts(self):
        """Identity is the brief FILE, not the string someone stored — a lane's view of
        the same brief records a different prefix and must not read as a second brief."""
        self._accept("carried.md")
        session.cmd_wi_status(argparse.Namespace(
            id="WI-0002", status=None, section=None, blocked_by=None,
            source="some/other/prefix/carried.md"))
        _overdue, _broken, untracked, _skip = session._triage_debt()
        self.assertEqual(untracked, [])

    def test_no_accepted_dir_is_not_an_error(self):
        """A repo that has never accepted a brief has nothing to reconcile."""
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, [])

    def test_status_mode_reports_an_untracked_accepted_brief(self):
        self._accept("orphaned-brief.md")
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_inbox_check(argparse.Namespace(status=True))
        self.assertIn("no work item", buf.getvalue())

    def _carry_on_ref(self, checkout, brief_name, filename="WI-0002-beta.md"):
        """Point an item at `brief_name` IN `checkout` and commit it THERE.

        The item then exists only at that ref's tip. `_triage_debt` reads the store from
        the main checkout's working tree, so this is exactly the state it reports as
        "nothing carries this brief" — and used to prescribe minting a second item for.
        """
        f = checkout / "work-items" / filename
        text = f.read_text(encoding="utf-8")
        f.write_text(
            text.replace("- section: next",
                         f"- section: next\n- source: proposed-edits/accepted/"
                         f"{brief_name}", 1),
            encoding="utf-8")
        _git(checkout, "add", "-A")
        _git(checkout, "commit", "-qm", f"carry {brief_name}")

    def test_a_brief_carried_only_on_a_lane_reads_as_carried_not_as_a_mint(self):
        """WI-0240. A brief triaged on a lane, with its item minted there and not yet
        landed, is uncarried as far as the trunk's store can see — and the remedy printed
        for that was *"Mint one with `wi-new --source`"*, i.e. write a second item on top
        of one that already has a body on a branch. That is WI-0191's wrong-remedy shape
        with a brief where the id used to be.

        No mocking: the fixture's lanes are real worktrees, so the item genuinely lives
        only at `worktree-poga-1`'s tip.
        """
        self._accept("lane-carried.md")
        self._carry_on_ref(self.laneA, "lane-carried.md")
        session.ROOT = self.main
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, ["lane-carried.md"],
                         "fixture precondition: main's store must genuinely not carry it")
        code, out = self._check(status=False)
        self.assertEqual(code, 1, "a real body stranded off the trunk is still a finding")
        self.assertIn("lane-carried.md", out)
        self.assertIn("ANOTHER ref", out)
        self.assertIn("worktree-poga-1", out, "the holder is named, not left to the reader")
        self.assertIn("poga resume 1", out, "and so is the remedy that actually applies")
        self.assertNotIn("wi-new --source", out,
                         "minting over a body that exists on a branch is the wrong repair")

    def test_a_brief_carried_by_nothing_anywhere_still_gets_the_mint_remedy(self):
        """The other half of the acceptance: the split must not swallow the real case."""
        self._accept("orphaned.md")
        code, out = self._check(status=False)
        self.assertEqual(code, 1)
        self.assertIn("orphaned.md", out)
        self.assertIn("nothing carries it", out)
        self.assertIn("wi-new --source", out)
        self.assertNotIn("ANOTHER ref", out)

    def test_the_status_line_counts_a_ref_carried_brief_as_its_own_kind(self):
        """A count folded into another bucket's label is a mislabelled count."""
        self._accept("lane-carried.md")
        self._carry_on_ref(self.laneA, "lane-carried.md")
        session.ROOT = self.main
        code, out = self._check(status=True)
        self.assertEqual(code, 0)
        self.assertIn("1 accepted brief(s) carried only on another ref", out)
        self.assertNotIn("with no work item", out)

    def test_a_work_item_that_only_mentions_the_brief_does_not_carry_it(self):
        """Carrying is a `source:` FIELD. `git grep` finds every line containing the
        brief's name, and an item's notes routinely quote the brief they came from —
        reading a mention as provenance would silence the finding this guard exists for.
        """
        self._accept("mentioned.md")
        f = self.laneA / "work-items" / "WI-0002-beta.md"
        f.write_text(f.read_text(encoding="utf-8") +
                     "\nFound while reading proposed-edits/accepted/mentioned.md.\n",
                     encoding="utf-8")
        _git(self.laneA, "add", "-A")
        _git(self.laneA, "commit", "-qm", "mention only")
        session.ROOT = self.main
        code, out = self._check(status=False)
        self.assertEqual(code, 1)
        self.assertIn("wi-new --source", out)
        self.assertNotIn("worktree-poga-1", out)

    def test_when_the_branch_probe_cannot_run_both_remedies_are_withheld(self):
        """[`declare-what-a-check-assumes`]: "couldn't tell" is its own answer. Printing
        the mint remedy on an unanswered branch question is printing the duplicating one.
        """
        self._accept("unknowable.md")
        with mock.patch.object(session, "_brief_carriers", return_value=None):
            code, out = self._check(status=False)
        self.assertEqual(code, 1)
        self.assertIn("unknowable.md", out)
        self.assertIn("UNKNOWN", out)
        self.assertNotIn("wi-new --source", out)

    def test_an_unstamped_applied_brief_is_not_filed_under_the_mint_header(self):
        """WI-0240's class sweep. `_applied_without_a_receipt()` was the last finding
        still riding in the `untracked` bucket: an `applied/` brief moved by hand needs
        REFILING, never a work item, and the startup line was counting it as an "accepted
        brief with no work item" when it is neither.
        """
        applied = self.pending.parent / "applied"
        applied.mkdir(parents=True, exist_ok=True)
        (applied / "moved-by-hand.md").write_text(
            "---\nedit-id: b1\napply: auto\n---\n\n# B\n", encoding="utf-8")
        _overdue, _broken, untracked, skip = session._triage_debt()
        self.assertIsNone(skip)
        self.assertEqual(untracked, [],
                         "an applied/ brief is not an untracked ACCEPTED brief")
        code, out = self._check(status=False)
        self.assertEqual(code, 1)
        self.assertIn("moved-by-hand.md", out)
        self.assertNotIn("wi-new --source", out)
        _code, line = self._check(status=True)
        self.assertIn("1 applied brief(s) with no engine stamp", line)
        self.assertNotIn("accepted brief(s)", line)

    def test_status_mode_is_silent_when_clean_and_speaks_when_not(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_inbox_check(argparse.Namespace(status=True))
        self.assertEqual(buf.getvalue().strip(), "")
        p = self.pending / "stale.md"
        p.write_text("brief", encoding="utf-8")
        self._age(p, session.TRIAGE_GRACE_DAYS + 1)
        buf = io.StringIO()
        with redirect_stdout(buf):
            session.cmd_inbox_check(argparse.Namespace(status=True))
        self.assertIn("Triage debt", buf.getvalue())


class Wave1RegressionTest(ClaimsBase):
    """Four shipped defects, none of which had ANY test — which is how all four reached
    every member and stayed. Each test below was confirmed to fail against the pre-fix
    code before being kept; a regression test that passes both ways is decoration."""

    def setUp(self):
        super().setUp()
        session.ROOT = self.main

    def _capture(self, fn, *a):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                fn(*a)
            except SystemExit:
                pass
        return buf.getvalue()

    # --- WI-0071: superseded items leaked into the one human-facing file --------------
    def test_superseded_items_are_excluded_from_both_roadmap_sections(self):
        """They were hidden from wi-list and the startup snapshot while STILL rendering
        into ROADMAP.md — so the surface preferentially showed items an Architect had
        decided were WRONG. Both sections, because both hand-rolled the same narrow
        filter and fixing one would leave the other lying."""
        d = self.main / "work-items"
        (d / "WI-0005-dead.md").write_text(
            _wi_text("WI-0005", "Superseded claim that is false", "superseded"),
            encoding="utf-8")
        (d / "WI-0006-backlog-dead.md").write_text(
            _wi_text("WI-0006", "Superseded backlog item", "superseded").replace(
                "section: next", "section: backlog"), encoding="utf-8")
        nxt = session._wi_render_next_section()
        back = session._wi_render_backlog_section()
        self.assertNotIn("WI-0005", nxt, "a superseded item must not render into Next")
        self.assertNotIn("WI-0006", back, "…nor into Backlog")
        self.assertIn("WI-0001", nxt, "live items still render")

    def test_done_items_remain_excluded(self):
        """The fix widened the filter; make sure it did not invert it."""
        self.assertNotIn("WI-0004", session._wi_render_next_section())

    # --- WI-0078: `release` made --force the only exit from residue ------------------
    def test_an_expired_claim_is_reclaimed_without_force(self):
        """A member's case: a torn-down lane left claims whose holder no longer existed, so
        NOBODY could satisfy "held by you" and only --force worked. --force is for
        overriding a LIVE peer; spending it on garbage collection trains the reflex to
        use it where it is genuinely dangerous."""
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-99", ttl=1)
        rec_path = session._coord_dir("claims") / "WI-0001.json"
        rec = session._coord_read(rec_path)
        rec["expires_at"] = time.time() - 60          # expire it
        rec_path.write_text(json.dumps(rec), encoding="utf-8")
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="WI-0001", force=False))
        self.assertIn("reclaimed", out)
        self.assertIn("TTL expired", out, "name the fact that justified reclaiming")
        self.assertNotIn("no longer live", out,
                         "a record names the CHECKOUT (WI-0061), so it cannot support a "
                         "claim about whether the holding session still exists")
        self.assertFalse(rec_path.exists())

    def test_a_live_claim_held_by_someone_else_still_refuses(self):
        """The safety half. Widening release must not let a lane stomp a working peer."""
        session._coord_try_acquire("claims", "WI-0002", "worktree-poga-98", ttl=9999)
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="WI-0002", force=False))
        self.assertIn("has not expired", out)
        self.assertTrue((session._coord_dir("claims") / "WI-0002.json").exists())

    # --- WI-0164: release covers every coordination kind, not only claims -------------
    def test_a_number_reservation_can_be_released_by_the_same_verb(self):
        """Hit live: recovering a dead lane's own commits was blocked by that lane's
        `wi-alloc` reservation, and the only exits were renumbering correctly-drawn items
        or hand-editing json under `.git/` — the hand-repair class ADR-0099 exists to stop."""
        session._coord_try_acquire("wi-alloc", "0155", "worktree-poga-98", ttl=9999)
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="0155", force=True, kind="wi-alloc"))
        self.assertIn("released", out)
        self.assertFalse((session._coord_dir("wi-alloc") / "0155.json").exists())

    def test_a_live_reservation_held_elsewhere_still_refuses_without_force(self):
        session._coord_try_acquire("wi-alloc", "0156", "worktree-poga-98", ttl=9999)
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="0156", force=False, kind="wi-alloc"))
        self.assertIn("has not expired", out)
        self.assertTrue((session._coord_dir("wi-alloc") / "0156.json").exists())

    def test_an_expired_reservation_reclaims_without_force_like_a_claim(self):
        session._coord_try_acquire("wi-alloc", "0157", "worktree-poga-99", ttl=1)
        p = session._coord_dir("wi-alloc") / "0157.json"
        rec = session._coord_read(p)
        rec["expires_at"] = time.time() - 60
        p.write_text(json.dumps(rec), encoding="utf-8")
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="0157", force=False, kind="wi-alloc"))
        self.assertIn("reclaimed", out)
        self.assertIn("wi-alloc reservation", out,
                      "the noun must name what was reclaimed, not say 'claim'")
        self.assertFalse(p.exists())

    def test_an_unknown_kind_is_refused_by_name_rather_than_guessed(self):
        """It must not silently fall back to `claims` — releasing the wrong store on a
        typo is a quiet, wrong success."""
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="0158", force=True, kind="nonsense"))
        self.assertIn("not a coordination kind", out)
        self.assertIn("wi-alloc", out, "name the kinds that ARE valid")

    def test_callers_that_predate_kind_still_release_claims(self):
        """Every existing call site builds a Namespace with no `kind`. Widening the verb
        must not break them."""
        session._coord_try_acquire("claims", "WI-0003", session._coord_identity(), ttl=9999)
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="WI-0003", force=False))
        self.assertIn("released", out)

    def test_releasing_an_unknown_claim_says_so(self):
        out = self._capture(session.cmd_release,
                            argparse.Namespace(id="WI-9999", force=False))
        self.assertIn("no claim named", out)

    # --- WI-0078: `coord` printed (empty) over files on disk -------------------------
    def test_coord_reports_hidden_expired_instead_of_empty(self):
        """Two real files displayed as an empty store, so residue someone had to clear
        read as a clean store. 'None' and 'some, filtered' printed identically."""
        session._coord_try_acquire("claims", "WI-0003", "worktree-poga-97", ttl=1)
        p = session._coord_dir("claims") / "WI-0003.json"
        rec = session._coord_read(p)
        rec["expires_at"] = time.time() - 60
        p.write_text(json.dumps(rec), encoding="utf-8")
        out = self._capture(session.cmd_coord, argparse.Namespace(all=False))
        self.assertNotIn("(empty)", out, "a store with a file on disk is not empty")
        self.assertIn("expired record(s) not shown", out)

    def test_coord_says_empty_only_when_it_really_is(self):
        out = self._capture(session.cmd_coord, argparse.Namespace(all=False))
        self.assertIn("(empty)", out)

    # --- WI-0074: `claims` folded "no store" into "nothing claimed" ------------------
    def test_claims_distinguishes_a_missing_store_from_an_empty_one(self):
        """A consumer saw "no open/in-progress items" while five live claims were held.
        A check that cannot say "I had nothing to join against" reports absence."""
        shutil.rmtree(self.main / "work-items")
        session._coord_try_acquire("claims", "WI-0001", "worktree-poga-96", ttl=9999)
        out = self._capture(session.cmd_claims, argparse.Namespace())
        self.assertIn("NOT CHECKED", out)
        self.assertIn("live claim record(s) exist", out,
                      "live claims must be listed raw, not silently dropped")
        self.assertIn("WI-0001", out, "the claim itself must appear, unmatched or not")


# ── WI-0239: every OTHER id lookup was lane-blind ────────────────────────────────
#
# WI-0191 taught ONE check (`_wi_missing_numbers`) that an id can live on a branch this
# tree cannot see. Three sibling lookups keyed the same way — a set built from
# `_wi_parse()`, which is this checkout's `work-items/` and nothing else — and read a
# lane-held id as unknown. `_wi_absent_cause` is the shared answer they now all ask for;
# these pin the answer itself and the closest sibling (`blocked-by`, same function and
# same exit-1 path as the gap check).


class AbsentCauseTest(ClaimsBase):
    """The four causes of "not in my store", and the three that are not "no such item"."""

    def _lane_mints(self, wid="WI-0300", branch="worktree-poga-1"):
        lane = self.laneA if branch == "worktree-poga-1" else self.laneB
        (lane / "work-items" / f"{wid}-minted-in-the-lane.md").write_text(
            _wi_text(wid, "Minted in the lane", "open"), encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"add {wid} in the lane")

    def test_an_id_on_an_unlanded_lane_is_named_with_its_land_verb(self):
        session.ROOT = self.main
        self._lane_mints(branch="worktree-poga-2")
        cause = session._wi_absent_cause(["WI-0300"])
        self.assertIn("WI-0300", cause)
        self.assertIn("worktree-poga-2", cause["WI-0300"])
        self.assertIn("UNLANDED LANE", cause["WI-0300"])
        self.assertIn("poga resume 2", cause["WI-0300"], "a verb, not a diagnosis")
        self.assertIn("poga recover --lane 2", cause["WI-0300"])

    def test_an_id_the_TRUNK_carries_is_staleness_not_a_stranded_lane(self):
        """Found live while exercising WI-0239's own fix: this lane, 7 commits behind
        main, was told `worktree-poga-10` held two ids that were sitting on `main`.

        `_lane_work_is_on_trunk(trunk, trunk)` is VACUOUSLY TRUE, so the trunk — the ref
        most likely to be the real answer — was silently disqualified from ever being one,
        and the diagnosis fell through to whichever lane also happened to carry the file.
        A lane sits behind the trunk for its whole life by design (ADR-0108), so this is
        the common case, not an exotic one."""
        session.ROOT = self.laneA
        (self.main / "work-items" / "WI-0300-landed-on-main.md").write_text(
            _wi_text("WI-0300", "Landed on main", "open"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "add WI-0300 on the trunk")
        cause = session._wi_absent_cause(["WI-0300"])
        self.assertIn("main", cause["WI-0300"])
        self.assertIn("TRUNK", cause["WI-0300"])
        self.assertIn("ADR-0108", cause["WI-0300"])
        self.assertNotIn("UNLANDED LANE", cause["WI-0300"],
                         "nobody's lane is stranded — this checkout is behind")
        self.assertNotIn("poga recover", cause["WI-0300"],
                         "do not send a reader to land someone else's lane")

    def test_the_trunk_wins_the_remedy_even_when_lanes_also_carry_it(self):
        """A lane cut from the newer trunk carries the file too, so both are holders. The
        remedy must name the one that costs nobody a land."""
        session.ROOT = self.laneA
        for tree in (self.main, self.laneB):
            (tree / "work-items" / "WI-0300-landed-on-main.md").write_text(
                _wi_text("WI-0300", "Landed on main", "open"), encoding="utf-8")
            _git(tree, "add", "-A")
            _git(tree, "commit", "-qm", "add WI-0300")
        cause = session._wi_absent_cause(["WI-0300"])
        self.assertIn("worktree-poga-2", cause["WI-0300"], "still name every holder")
        self.assertIn("TRUNK", cause["WI-0300"])
        self.assertNotIn("poga resume 2", cause["WI-0300"])

    def test_a_genuinely_unknown_id_gets_no_entry_at_all(self):
        """A PARTIAL map is the contract: the caller already has the right sentence for
        "no such item", and inventing a fourth wording for it in here would be the
        duplicate this helper exists to avoid."""
        session.ROOT = self.main
        self.assertEqual(session._wi_absent_cause(["WI-4242"]), {})

    def test_a_live_allocator_hold_is_drawn_not_missing(self):
        """The number is reserved by a live lane whose file is not committed yet — the
        state `_wi_gap_numbers` has always subtracted, and that the siblings could not
        see because they never asked the allocator either."""
        session.ROOT = self.main
        session._coord_try_acquire("wi-alloc", "301", "worktree-poga-2", ttl=9999)
        cause = session._wi_absent_cause(["WI-0301"])
        self.assertIn("WI-0301", cause)
        self.assertIn("wi-alloc", cause["WI-0301"])
        self.assertIn("not been committed yet", cause["WI-0301"])
        self.assertNotIn("UNLANDED LANE", cause["WI-0301"], "no ref carries a body yet")

    def test_an_expired_hold_stops_shielding_the_number(self):
        """Same rule as the gap check: an abandoned reservation must not hide the answer
        forever. `_coord_list` drops expired records, so this needs no special case —
        pinned because a later reader adding a `drop_expired=False` here would silently
        resurrect the shield."""
        session.ROOT = self.main
        session._coord_try_acquire("wi-alloc", "301", "worktree-poga-2", ttl=-1)
        self.assertEqual(session._wi_absent_cause(["WI-0301"]), {})

    def test_an_unprobeable_branch_list_is_UNKNOWN_not_no_such_item(self):
        """`declare-what-a-check-assumes` — "couldn't tell" is its own printed answer
        everywhere, not just in the gap check that learned it first."""
        session.ROOT = self.main
        self._lane_mints()
        with mock.patch.object(session, "_wi_branch_holders", return_value=None):
            cause = session._wi_absent_cause(["WI-0300"])
        self.assertIn("UNKNOWN", cause["WI-0300"])
        self.assertNotIn("UNLANDED LANE", cause["WI-0300"])

    def test_a_token_that_is_not_a_work_item_id_is_never_probed(self):
        """Dispatch hands this its unresolved tokens, which include ORDINALS. An ordinal
        is a fact about the pinned snapshot, not about the store, and must not acquire a
        branch story."""
        session.ROOT = self.main
        with mock.patch.object(session, "_wi_branch_holders") as probe:
            self.assertEqual(session._wi_absent_cause(["7", "", "WI-1"]), {})
        probe.assert_not_called()

    def test_nothing_is_probed_when_nothing_is_absent(self):
        """The cost guard. Every caller is on a hot path (`wi-check` runs at startup), so
        the scan must only ever run when an id is actually missing."""
        session.ROOT = self.main
        with mock.patch.object(session, "_wi_branch_holders") as probe:
            self.assertEqual(session._wi_absent_cause([]), {})
        probe.assert_not_called()


class BlockedByIsLaneAwareTest(ClaimsBase):
    """The CLOSEST SIBLING of the gap check: same function (`_wi_validate`), same caller,
    same exit-1 path — and WI-0191 taught it nothing.

    Mint WI-0300 on a lane, name it as a blocker from the trunk, and every citing item
    reported its real dependency as a fiction. It still FAILS the check (the operator's ruling,
    session ~187, for the lane-held gap: a body off the trunk with a real action someone
    must take is not the quiet channel). Only the diagnosis changes."""

    def _cite(self, blocker="WI-0300", on="WI-0001"):
        session.ROOT = self.main
        session.cmd_wi_status(argparse.Namespace(
            id=on, status=None, section=None, blocked_by=blocker, source=None))

    def _lane_mints(self, wid="WI-0300"):
        (self.laneA / "work-items" / f"{wid}-minted-in-the-lane.md").write_text(
            _wi_text(wid, "Minted in the lane", "open"), encoding="utf-8")
        _git(self.laneA, "add", "-A")
        _git(self.laneA, "commit", "-qm", f"add {wid} in the lane")

    def test_a_lane_minted_blocker_is_not_reported_as_a_fiction(self):
        self._lane_mints()
        self._cite()
        problems = [p for p in session._wi_validate() if "blocked-by" in p]
        self.assertEqual(len(problems), 1)
        self.assertIn("worktree-poga-1", problems[0], "name the ref that holds it")
        self.assertIn("UNLANDED LANE", problems[0])
        self.assertIn("session.py merge", problems[0], "a verb, not a diagnosis")
        self.assertNotIn("dangling", problems[0])

    def test_it_is_still_a_PROBLEM_and_still_fails_the_check(self):
        """The ~187 ruling extended, not reopened. There is a body off the trunk and a
        real action; quiet is how a lane holding a closed session gets forgotten."""
        self._lane_mints()
        self._cite()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as e:
                session.cmd_wi_check(argparse.Namespace(status=False))
        self.assertEqual(e.exception.code, 1)
        self.assertIn("UNLANDED LANE", buf.getvalue())

    def test_a_blocker_that_never_existed_still_reads_as_dangling(self):
        """The other direction, and the reason the helper returns a partial map: an id
        nobody ever drew must not acquire a lane story."""
        self._cite(blocker="WI-4242")
        problems = [p for p in session._wi_validate() if "blocked-by" in p]
        self.assertEqual(len(problems), 1)
        self.assertIn("dangling", problems[0])
        self.assertNotIn("UNLANDED", problems[0])

    def test_the_branch_is_probed_once_for_many_citing_items(self):
        """Ordering and cost. The probe is O(refs) git calls, so a dozen items citing the
        same lane-minted blocker must pay for one scan, not a dozen."""
        self._lane_mints()
        for wid in ("WI-0001", "WI-0002", "WI-0003"):
            self._cite(on=wid)
        with mock.patch.object(session, "_wi_branch_holders",
                               return_value={300: ["worktree-poga-1"]}) as probe:
            problems = [p for p in session._wi_validate() if "blocked-by" in p]
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(len(problems), 3)
        self.assertEqual([p.split(":")[0] for p in problems],
                         ["WI-0001", "WI-0002", "WI-0003"],
                         "per-item order survives the batched probe")

    def test_a_sound_store_never_pays_for_the_probe(self):
        session.ROOT = self.main
        with mock.patch.object(session, "_wi_branch_holders") as probe:
            session._wi_validate()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
