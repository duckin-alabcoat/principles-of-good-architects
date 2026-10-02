"""ADR-0051 C4/C5 — cross-lane coordination substrate (phase 1).

The coordination store lives in the git COMMON dir (`<common>/poga-coord/<kind>/`), so a
record written from one worktree lane is visible from a sibling lane and the main checkout
— the cross-tree visibility ADR-0057 D3 deferred and the per-tree `.session-state/`
deliberately does NOT provide. These tests pin:

  1. the common dir resolves to the SAME physical path from every lane + the main checkout;
  2. O_EXCL makes an AVAILABLE name a single-winner race (the load-bearing guarantee);
  3. a record written in lane A is seen in lane B (cross-tree visibility);
  4. expired records are reclaimable and reaped; live ones are not;
  5. refresh/release respect the holder;
  6. no git common dir → fail-OPEN (coordination degrades to a no-op, never bricks).
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402

# The journal-neutralising fixture guard, shared with every module that writes
# coordination records (WI-0126) — one definition, so a new coord test cannot inherit
# the ambient session's journal by forgetting a copy.
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (
    exercise_real_coord_holder, neutralize_coord_journal,
    neutralize_dispatch_env)  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class CoordBase(unittest.TestCase):
    def setUp(self):
        # Records carry no journal unless a test asks for one (WI-0123). Without this the
        # fixture inherits whatever REAL session runs the suite, so two fictional sessions
        # ("sess-A", "sess-B") silently share one journal and read as the same holder.
        # The guard moved to `coord_fixture` in WI-0126 — it was copied here and in
        # test_leases and missing from six other coord-writing modules.
        neutralize_coord_journal(self)
        # WI-0242: this module reaches `_holder_journal_dirs` (via `_coord_reap` /
        # `_attention_write`), which appends `POGA_INVOKED_FROM` to the journal
        # directories it scans — the operator's REAL checkout and every live sibling
        # lane. `neutralize_coord_journal` does not cover this route: it patches
        # `_coord_holder`, and nothing here goes through it.
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "f.txt").write_text("base\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        # Two lanes: sibling worktrees of the same repo (mirrors two `poga` sessions).
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(self.laneB), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        session.CFG = {
            "tz": ZoneInfo("UTC"), "machine_map": {},
            "architect_name": "Test", "architect_id": "test-arch",
        }

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _at(self, root):
        session.ROOT = root


class CommonDirTest(CoordBase):
    def test_common_dir_is_shared_across_lanes_and_main(self):
        self._at(self.main)
        cm = session._git_common_dir()
        self._at(self.laneA)
        ca = session._git_common_dir()
        self._at(self.laneB)
        cb = session._git_common_dir()
        self.assertIsNotNone(cm)
        self.assertEqual(cm, ca)
        self.assertEqual(cm, cb)
        self.assertEqual(cm, (self.main / ".git").resolve())

    def test_no_git_repo_fails_open(self):
        # Point at a non-git dir: the common dir can't resolve, so acquire degrades to a
        # no-op success (True, {}) — isolation still holds, coordination just goes dark.
        self._at(self.tmp)
        ok, holder = session._coord_try_acquire("claims", "x", "sess-1", 3600)
        self.assertTrue(ok)
        self.assertEqual(holder, {})
        self.assertIsNone(session._coord_dir("claims"))

    def test_a_relative_answer_is_no_answer(self):
        """`--path-format=absolute` was asked for, so a relative reply is not this
        question's answer — and `.resolve()` would silently make it one by anchoring on
        the process CWD, putting the "shared coordination store" INSIDE the working tree.

        Not hypothetical (WI-0267): serializing the land made the land paths write
        coordination records for the first time, and a fixture that stubs `sh` so every
        `rev-parse` returns a fixed short sha created `beef9999/poga-coord/` in the real
        checkout, where a `git add -A` committed it. Absence of a common dir is a case
        every caller already handles; a plausible WRONG path is one none of them can."""
        self._at(self.main)

        def fake_sh(argv, check=True, cwd=None, env=None, **kw):
            return types.SimpleNamespace(returncode=0, stdout="beef9999\n", stderr="")

        with unittest.mock.patch.object(session, "sh", fake_sh):
            self.assertIsNone(session._git_common_dir())
            self.assertIsNone(session._coord_dir("claims"))


class AcquireRaceTest(CoordBase):
    def test_available_name_is_single_winner(self):
        self._at(self.laneA)
        ok1, rec1 = session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        # A DIFFERENT session tries the same free name from the sibling lane.
        self._at(self.laneB)
        ok2, holder = session._coord_try_acquire("claims", "item-x", "sess-B", 3600)
        self.assertTrue(ok1)
        self.assertFalse(ok2)                       # second is refused
        self.assertEqual(holder["session_id"], "sess-A")   # ...and shown the holder

    def test_reacquire_by_same_session_refreshes(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        ok, rec = session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        self.assertTrue(ok)                         # my own name → re-acquire succeeds
        self.assertEqual(rec["session_id"], "sess-A")

    def test_expired_name_is_reclaimable_by_another(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        # Force sess-A's record to be already-expired on disk.
        p = session._coord_dir("claims") / "item-x.json"
        rec = json.loads(p.read_text())
        rec["expires_at"] = time.time() - 1
        p.write_text(json.dumps(rec))
        self._at(self.laneB)
        ok, out = session._coord_try_acquire("claims", "item-x", "sess-B", 3600)
        self.assertTrue(ok)                         # dead holder → reclaimable
        self.assertEqual(out["session_id"], "sess-B")


class VisibilityTest(CoordBase):
    def test_claim_in_lane_a_is_seen_in_lane_b(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        self._at(self.laneB)
        live = session._coord_list("claims")
        self.assertIn("item-x", live)               # cross-tree visibility
        self.assertEqual(live["item-x"]["session_id"], "sess-A")

    def test_list_excludes_expired(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "live", "sess-A", 3600)
        session._coord_try_acquire("claims", "dead", "sess-A", 3600)
        p = session._coord_dir("claims") / "dead.json"
        rec = json.loads(p.read_text())
        rec["expires_at"] = time.time() - 1
        p.write_text(json.dumps(rec))
        live = session._coord_list("claims")
        self.assertIn("live", live)
        self.assertNotIn("dead", live)
        self.assertIn("dead", session._coord_list("claims", include_expired=True))


class RefreshReleaseTest(CoordBase):
    def test_refresh_only_by_holder_and_bumps_expiry(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "item-x", "sess-A", 100)
        p = session._coord_dir("claims") / "item-x.json"
        before = json.loads(p.read_text())["expires_at"]
        time.sleep(0.01)
        self.assertFalse(session._coord_refresh("claims", "item-x", "sess-B", 3600))  # not holder
        self.assertTrue(session._coord_refresh("claims", "item-x", "sess-A", 3600))
        after = json.loads(p.read_text())["expires_at"]
        self.assertGreater(after, before)

    def test_release_respects_holder_unless_forced(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "item-x", "sess-A", 3600)
        self.assertFalse(session._coord_release("claims", "item-x", "sess-B"))  # not holder
        self.assertTrue(session._coord_release("claims", "item-x", "sess-A"))
        session._coord_try_acquire("claims", "item-y", "sess-A", 3600)
        self.assertTrue(session._coord_release("claims", "item-y", "sess-B", force=True))


class JournalOwnershipTest(CoordBase):
    """WI-0123 — ownership matches on the journal too, not only on the key derived from
    whichever checkout executed the write.

    The live failure: `poga work claim` anchors on the main checkout by design
    (ADR-0073), so a lane's claim is recorded under the Claude session id; the same
    lane's `release` computes its branch, the two never meet, and the session is told
    its own claim is `held by main` with `--force` offered as the way out. Every test
    below is written so it FAILS on session_id-only matching."""

    JOURNAL = "20260814T1200Z-runner-282d"
    FRONT_DOOR_ID = "880bed0d-3656-40aa-aa0a-283ef983e272"   # the Claude session id
    LANE_ID = "worktree-poga-2"                              # the lane's branch

    def _holding(self, journal):
        """Pin what `_coord_record`/`_coord_my_journal` observe as the holding session."""
        return unittest.mock.patch.object(session, "_coord_holder",
                                          return_value=(journal, "claude-code", "journal"))

    def test_a_lane_releases_the_claim_it_took_through_the_front_door(self):
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "WI-0121", self.FRONT_DOOR_ID, 3600)
            rec = json.loads((session._coord_dir("claims") / "WI-0121.json").read_text())
            # The record genuinely says someone else owns it, by the old key...
            self.assertEqual(rec["session_id"], self.FRONT_DOOR_ID)
            self.assertNotEqual(rec["session_id"], self.LANE_ID)
            # ...and the journal it carries is what makes it ours.
            self.assertEqual(rec["journal"], self.JOURNAL)
            self.assertTrue(session._coord_release("claims", "WI-0121", self.LANE_ID),
                            "a session must be able to release its own claim without --force")

    def test_another_sessions_claim_is_still_refused(self):
        """The fix must not become 'everyone owns everything'."""
        self._at(self.laneA)
        with self._holding("20260814T1210Z-runner-3aa8"):        # a concurrent session
            session._coord_try_acquire("claims", "WI-0090", "other-session", 3600)
        with self._holding(self.JOURNAL):                        # us, different journal
            self.assertFalse(session._coord_release("claims", "WI-0090", self.LANE_ID))

    def test_an_absent_journal_never_matches(self):
        """Absence is not identity — an unresolvable journal degrades to the old
        session_id-only behaviour rather than matching anything that also lacks one."""
        self._at(self.laneA)
        with self._holding(""):
            session._coord_try_acquire("claims", "WI-0005", "someone-else", 3600)
            self.assertFalse(session._coord_release("claims", "WI-0005", self.LANE_ID))
        self.assertFalse(session._coord_mine({"session_id": "a", "journal": ""}, "b", ""))
        self.assertFalse(session._coord_mine({"session_id": "a"}, "b", "J"))

    def test_refresh_matches_on_the_journal_too(self):
        """The heartbeat keep-alive has the same key, so a front-door claim would have
        expired mid-session while its holder was alive and beating."""
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "WI-0058", self.FRONT_DOOR_ID, 100)
            p = session._coord_dir("claims") / "WI-0058.json"
            before = json.loads(p.read_text())["expires_at"]
            time.sleep(0.01)
            self.assertTrue(session._coord_refresh("claims", "WI-0058", self.LANE_ID, 3600))
            self.assertGreater(json.loads(p.read_text())["expires_at"], before)

    def test_teardown_frees_front_door_claims_by_journal(self):
        """The second half: teardown matched only branch-keyed records, so a lane's
        front-door claims sat out the full 8h TTL with the board showing a held item
        nobody was working on."""
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "WI-0116", self.FRONT_DOOR_ID, 3600)
        # Branch alone finds nothing — this is the bug, pinned.
        self.assertEqual(session._lane_exit_release(self.LANE_ID), [])
        self.assertIn("WI-0116", session._coord_list("claims"))
        # The lane's journal is the link the record actually carries.
        self.assertEqual(
            len(session._lane_exit_release(self.LANE_ID, journals=(self.JOURNAL,))), 1)
        self.assertNotIn("WI-0116", session._coord_list("claims"))

    def test_teardown_still_frees_branch_keyed_records_without_journals(self):
        """Back-compat: a claim taken from inside the lane is keyed by the branch and
        must still be freed when no journals are passed at all."""
        self._at(self.laneA)
        session._coord_try_acquire("claims", "WI-0099", self.LANE_ID, 3600)
        self.assertEqual(len(session._lane_exit_release(self.LANE_ID)), 1)
        self.assertNotIn("WI-0099", session._coord_list("claims"))


class HarvestWorkItemsTest(CoordBase):
    """ADR-0093 D1/D3 — the close harvests what the 8h TTL is about to erase."""

    JOURNAL = "20260814T1200Z-runner-282d"
    OTHER = "20260814T1210Z-runner-3aa8"

    def _holding(self, journal):
        return unittest.mock.patch.object(session, "_coord_holder",
                                          return_value=(journal, "claude-code", "journal"))

    def test_harvests_live_claims_and_released_ones(self):
        """A session that claimed an item and released it mid-session did that work just
        as much as one still holding at close — so tombstones count too."""
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "WI-0121", "front-door", 3600)
            session._coord_try_acquire("claims", "WI-0116", "front-door", 3600)
            session._coord_release("claims", "WI-0116", "front-door")   # released early
        self.assertEqual(session._harvest_session_work_items(self.JOURNAL),
                         ["WI-0116", "WI-0121"])

    def test_another_sessions_claims_are_not_harvested(self):
        self._at(self.laneA)
        with self._holding(self.OTHER):
            session._coord_try_acquire("claims", "WI-0090", "other", 3600)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "WI-0121", "mine", 3600)
        self.assertEqual(session._harvest_session_work_items(self.JOURNAL), ["WI-0121"])

    def test_held_nothing_is_empty_not_absent(self):
        self._at(self.laneA)
        self.assertEqual(session._harvest_session_work_items(self.JOURNAL), [])

    def test_a_drill_claim_is_not_harvested_into_the_journal(self):
        """WI-0174 — session ~173's journal landed carrying `work-items: DRILL-1`.

        The stamp was accurate and that is the problem: the drill claims through the
        production path on purpose, so its synthetic id reaches the harvest like any
        other, and the journal is tracked. A real claim held in the same session must
        still be harvested — the exclusion is on the id, not on the session.
        """
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "DRILL-1", "drill", 3600)
            session._coord_try_acquire("claims", "WI-0121", "front-door", 3600)
        self.assertEqual(session._harvest_session_work_items(self.JOURNAL), ["WI-0121"])

    def test_a_drill_only_session_harvests_empty_not_absent(self):
        """Empty means "held nothing real", which is the honest answer here. Absent
        means "could not tell" and would be a different, wrong claim."""
        self._at(self.laneA)
        with self._holding(self.JOURNAL):
            session._coord_try_acquire("claims", "DRILL-2", "drill", 3600)
        self.assertEqual(session._harvest_session_work_items(self.JOURNAL), [])

    def test_unreadable_store_is_absent_not_empty(self):
        """D3's whole point: "could not tell" must not print as "held nothing", because
        this field exists to be counted and the miscount would enter the record as fact."""
        self._at(self.laneA)
        with unittest.mock.patch.object(session, "_coord_dir", return_value=None):
            self.assertIsNone(session._harvest_session_work_items(self.JOURNAL))
        with unittest.mock.patch.object(session, "_coord_list",
                                        side_effect=RuntimeError("boom")):
            self.assertIsNone(session._harvest_session_work_items(self.JOURNAL))
        self.assertIsNone(session._harvest_session_work_items(""))

    def test_absent_and_empty_render_differently_in_the_journal(self):
        """The two states must survive into the file, not just the function."""
        text = session.render_journal({"session-id": "s", "started": "t"})
        empty = session.finalize_journal(text, "e", "1m", work_items=[])
        self.assertIn("work-items:", empty)
        absent = session.finalize_journal(text, "e", "1m", work_items=None)
        self.assertNotIn("work-items:", absent)

    def test_a_journal_closed_before_this_shipped_gains_no_field(self):
        """`work-items` is deliberately NOT in JOURNAL_FM_KEYS: in the canonical list
        every re-rendered journal would grow an empty `work-items:`, and empty MEANS
        "held nothing" — the record would assert a fact about sessions that predate the
        mechanism."""
        self.assertNotIn("work-items", session.JOURNAL_FM_KEYS)
        old = session.render_journal({"session-id": "old", "started": "t"})
        self.assertNotIn("work-items:", session.finalize_journal(old, "e", "1m"))


class ReapTest(CoordBase):
    def test_reap_removes_expired_keeps_live(self):
        self._at(self.laneA)
        session._coord_try_acquire("claims", "live", "sess-A", 3600)
        session._coord_try_acquire("leases", "dead", "sess-A", 3600)
        p = session._coord_dir("leases") / "dead.json"
        rec = json.loads(p.read_text())
        rec["expires_at"] = time.time() - 1
        p.write_text(json.dumps(rec))
        removed = session._coord_reap()
        self.assertEqual(removed, 1)
        self.assertIn("live", session._coord_list("claims"))
        self.assertEqual(session._coord_list("leases"), {})


class NonClaudeIdentityTest(CoordBase):
    """WI-0107 — the ownership key of a session with no runtime-supplied session id.

    The front door (`poga work`) anchors on the MAIN checkout by design (ADR-0073), so a
    session driving it is NOT `_on_worktree_lane()` even when the operator stands in a
    lane. A non-Claude runtime also exports no `CLAUDE_CODE_SESSION_ID`. Both fallbacks
    therefore miss, and the key used to fall to the literal `"app"` — shared by every
    codex/antigravity session on the machine. Since the key is what release and refresh
    match on, one agent could release another's claim; reproduced live in session ~129
    with a codex lane running.

    The Claude family was never affected, which is why it survived: a Claude session gets
    the lane branch or its own unique session id. The bug reached only the callers doing
    the right thing — using the front door from a non-Claude lane."""

    def setUp(self):
        super().setUp()
        self._env = {k: os.environ.get(k) for k in ("CLAUDE_CODE_SESSION_ID", "POGA_INVOKED_FROM")}
        # WI-0250: one list, not a name popped here. `neutralize_ambient_env` covers
        # `CLAUDE_CODE_SESSION_ID` and the colour switches, and restores the whole
        # environment at cleanup.
        neutralize_ambient_env(self)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def _prep(self, lane, sid):
        d = lane / ".session-state"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prep.json").write_text(
            json.dumps({"session_id": sid, "runtime": "codex"}), encoding="utf-8")

    def test_two_non_claude_lanes_do_not_share_one_key(self):
        self._at(self.main)                       # anchored front door — not on a lane
        self._prep(self.laneA, "20260808T1220Z-runner-dbd5")
        self._prep(self.laneB, "20260808T1600Z-runner-9f21")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        a = session._coord_identity()
        os.environ["POGA_INVOKED_FROM"] = str(self.laneB)
        b = session._coord_identity()
        self.assertNotEqual(a, b, "two non-Claude lanes must not share an ownership key")
        self.assertEqual(a, "20260808T1220Z-runner-dbd5")

    def test_one_non_claude_lane_cannot_release_anothers_claim(self):
        self._at(self.main)
        self._prep(self.laneA, "sess-codex-A")
        self._prep(self.laneB, "sess-codex-B")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        session._coord_try_acquire("claims", "WI-0033", session._coord_identity(), 3600)
        os.environ["POGA_INVOKED_FROM"] = str(self.laneB)
        self.assertFalse(session._coord_release("claims", "WI-0033", session._coord_identity()),
                         "lane B released lane A's claim")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        self.assertTrue(session._coord_release("claims", "WI-0033", session._coord_identity()))

    def test_lane_path_is_the_key_when_no_prep_marker_exists(self):
        self._at(self.main)
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        self.assertEqual(session._coord_identity(), "worktree-poga-1")

    def test_claude_session_id_still_wins_over_the_lane_fallback(self):
        self._at(self.main)
        self._prep(self.laneA, "sess-codex-A")
        os.environ["POGA_INVOKED_FROM"] = str(self.laneA)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "claude-abc"
        self.assertEqual(session._coord_identity(), "claude-abc")

    def test_last_resort_is_still_app_with_no_lane_and_no_id(self):
        self._at(self.main)
        os.environ["POGA_INVOKED_FROM"] = str(self.main)
        self.assertEqual(session._coord_identity(), "app")


if __name__ == "__main__":
    unittest.main()


class NonClaudeJournalProvenanceTest(CoordBase):
    """WI-0106 — a lane-created non-Claude session recorded no journal id, so its runtime
    was ASSUMED rather than observed.

    `poga` exports `POGA_INVOKED_FROM` as the directory the OPERATOR was standing in when
    they typed the command — the main checkout — and then creates the lane and runs the
    runtime inside it. So the one checkout the resolver consulted is the one that provably
    does not hold this session's journal, while the lane's own journal sat unread a
    directory away. Two costs: `runtime_source` read `launcher` (an assumption from the
    registry row) instead of `journal` (an observation), and the `journal` field — which
    `_coord_who` documents as never omitted, because a branch alone cannot distinguish two
    holders — came back empty.
    """

    def setUp(self):
        super().setUp()
        exercise_real_coord_holder(self)
        self._env = {k: os.environ.get(k)
                     for k in ("CLAUDE_CODE_SESSION_ID", "POGA_INVOKED_FROM")}
        # WI-0250: one list, not a name popped here. `neutralize_ambient_env` covers
        # `CLAUDE_CODE_SESSION_ID` and the colour switches, and restores the whole
        # environment at cleanup.
        neutralize_ambient_env(self)
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def _lane_journal(self, lane, sid, runtime="codex", prep=True):
        jdir = lane / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / (sid + ".md")).write_text(
            "---\n"
            "session-id: " + sid + "\n"
            "ordinal: 1\n"
            "title: (in progress)\n"
            "machine: Runner\n"
            "runtime: " + runtime + "\n"
            "role-doc-version: v1.0.0\n"
            "base-commit: 000000000000\n"
            "started: 2026-08-08T12:20:00+00:00\n"
            "ended: \n"
            "claude-session-id: \n"
            "---\n\nbody\n", encoding="utf-8")
        if prep:
            d = lane / ".session-state"
            d.mkdir(parents=True, exist_ok=True)
            (d / "prep.json").write_text(
                json.dumps({"session_id": sid, "runtime": runtime}), encoding="utf-8")

    def test_the_lanes_journal_is_found_though_invoked_from_names_main(self):
        """THE LIVE CASE: codex lane poga-6, POGA_INVOKED_FROM = the main checkout."""
        sid = "20260808T1220Z-runner-dbd5"
        self._lane_journal(self.laneA, sid)
        os.environ["POGA_INVOKED_FROM"] = str(self.main)   # where the OPERATOR stood
        os.chdir(self.laneA)                               # where the RUNTIME runs
        journal_id, runtime, source = session._coord_holder()
        self.assertEqual(journal_id, sid, "the record must say WHICH SESSION holds it")
        self.assertEqual(runtime, "codex")
        self.assertEqual(source, "journal",
                         "observed from the journal, not assumed from the registry row")

    def test_the_prep_marker_disambiguates_two_open_journals(self):
        """Uniqueness is the fallback, not the rule. `poga` writes the session id it
        prepped for this lane, so the answer is unambiguous by construction even when a
        second open journal is present."""
        sid = "20260808T1220Z-runner-dbd5"
        self._lane_journal(self.laneA, sid)
        self._lane_journal(self.laneA, "20260808T1600Z-runner-9f21", prep=False)
        os.environ["POGA_INVOKED_FROM"] = str(self.main)
        os.chdir(self.laneA)
        journal_id, _runtime, source = session._coord_holder()
        self.assertEqual(journal_id, sid)
        self.assertEqual(source, "journal")

    def test_two_open_journals_and_no_prep_marker_is_honestly_unknown(self):
        """The exactly-one rule survives. Two open journals in one checkout is genuinely
        ambiguous, and None is the honest answer rather than a coin flip recorded as
        fact."""
        self._lane_journal(self.laneA, "sess-one", prep=False)
        self._lane_journal(self.laneA, "sess-two", prep=False)
        os.environ["POGA_INVOKED_FROM"] = str(self.main)
        os.chdir(self.laneA)
        journal_id, _runtime, source = session._coord_holder()
        self.assertEqual(journal_id, "")
        self.assertNotEqual(source, "journal", "no journal answered, so it must not claim one")

    def test_invoked_from_still_answers_when_the_cwd_holds_nothing(self):
        """The old path is a fallback, not a casualty — a caller standing somewhere with
        no journal still resolves through the checkout it was launched from."""
        sid = "sess-from-invoked"
        self._lane_journal(self.laneB, sid)
        os.environ["POGA_INVOKED_FROM"] = str(self.laneB)
        os.chdir(self.main)
        journal_id, _runtime, source = session._coord_holder()
        self.assertEqual(journal_id, sid)
        self.assertEqual(source, "journal")


class ForeignJournalProvenanceTest(CoordBase):
    """WI-0106's SECOND path, split out at session ~157/158 and closed at ~174.

    When a session in one lane reaches sideways into ANOTHER lane and draws a record
    there, it matches on its own env session id and records its own journal — so the
    runtime was observed of the READER, not of the lane the record is about, and
    `runtime_source: journal` printed that as a plain observation. Confidently wrong
    rather than silent.

    The reason this waited for its own pass is the reason these tests are careful: the
    obvious guard — "the journal must live in the checkout being written from" — is TRUE
    OF THE LEGITIMATE CASE the land gate depends on. `poga work claim` anchors on the
    main checkout by design and execs main's session.py, so a lane session's journal is
    routinely resolved from a process standing in main. Guarding on locality alone would
    refuse provenance for every front-door write and silently re-break what WI-0093 had
    just fixed. So the ownership key (`journal`) is untouched and only the PROVENANCE
    CLAIM changes.
    """

    def setUp(self):
        super().setUp()
        exercise_real_coord_holder(self)
        self._env = {k: os.environ.get(k)
                     for k in ("CLAUDE_CODE_SESSION_ID", "POGA_INVOKED_FROM")}
        # WI-0250: one list, not a name popped here. `neutralize_ambient_env` covers
        # `CLAUDE_CODE_SESSION_ID` and the colour switches, and restores the whole
        # environment at cleanup.
        neutralize_ambient_env(self)
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def _journal(self, checkout, sid, runtime="codex"):
        jdir = checkout / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / f"{sid}.md").write_text(
            f"---\nsession-id: {sid}\nordinal: 1\ntitle: (in progress)\n"
            f"runtime: {runtime}\nclaude-session-id:\nended:\n---\n\n- Session opened.\n",
            encoding="utf-8")
        state = checkout / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "prep.json").write_text(
            f'{{"session_id": "{sid}", "runtime": "{runtime}"}}', encoding="utf-8")

    def test_a_journal_from_another_lane_is_marked_foreign(self):
        laneA, laneB = self.laneA, self.laneB
        self._journal(laneA, "sess-in-lane-a")
        os.environ["POGA_INVOKED_FROM"] = str(laneA)
        os.chdir(laneB)                       # reading from B, journal lives in A
        _jid, _rt, source = session._coord_holder()
        self.assertEqual(source, "journal-foreign")

    def test_the_ownership_key_is_unchanged_by_the_marking(self):
        """The gate matches on `journal`. Weakening it here is the failure mode this
        whole split-out exists to avoid."""
        laneA, laneB = self.laneA, self.laneB
        self._journal(laneA, "sess-in-lane-a")
        os.environ["POGA_INVOKED_FROM"] = str(laneA)
        os.chdir(laneB)
        jid, _rt, _source = session._coord_holder()
        self.assertEqual(jid, "sess-in-lane-a", "the holder identity must still be recorded")

    def test_a_lanes_own_journal_is_not_foreign(self):
        lane = self.laneA
        self._journal(lane, "sess-own")
        os.environ["POGA_INVOKED_FROM"] = str(self.main)
        os.chdir(lane)
        _jid, _rt, source = session._coord_holder()
        self.assertEqual(source, "journal")

    def test_the_front_door_from_main_is_never_foreign(self):
        """THE case that made this dangerous. Reading a lane's journal while standing in
        main is `poga work claim` working exactly as designed (ADR-0073), not a session
        reaching sideways — and marking it would break the land gate."""
        lane = self.laneA
        self._journal(lane, "sess-in-lane")
        os.environ["POGA_INVOKED_FROM"] = str(lane)
        os.chdir(self.main)
        _jid, _rt, source = session._coord_holder()
        self.assertEqual(source, "journal")

    def test_the_label_distinguishes_foreign_from_observed(self):
        """Two different questions must not print the same answer — the same argument
        `(assumed)` already carries for the config/default tiers."""
        plain = session._coord_runtime_label({"runtime": "codex",
                                              "runtime_source": "journal"})
        foreign = session._coord_runtime_label({"runtime": "codex",
                                                "runtime_source": "journal-foreign"})
        self.assertEqual(plain, "codex")
        self.assertNotEqual(foreign, plain)
        self.assertIn("reader", foreign)

    def test_an_unanswerable_question_claims_nothing_extra(self):
        """A path git cannot report on must not be marked suspect — `False` claims
        nothing, which is the direction that cannot invent a defect."""
        self.assertFalse(
            session._journal_is_from_another_lane(pathlib.Path("/nonexistent/x.md")))


if __name__ == "__main__":
    unittest.main()
