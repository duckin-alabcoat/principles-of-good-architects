"""ADR-0060 — terminal worktree-lane landing.

A `poga` session runs ALONE in a linked git worktree on a `worktree-<name>` branch.
`session.py end` lands it by plumbing: rebase the lane branch onto the local trunk
tip, gate it, then compare-and-swap the trunk ref forward to the lane tip — WITHOUT
checking out the trunk (the main worktree holds it) or the branch (the lane holds it).

The load-bearing empirical question these tests settle: `git update-ref refs/heads/main`
SUCCEEDS while `main` is checked out in another (the main) worktree. If it didn't, the
whole ADR-0060 land model would be wrong. `test_cas_advances_trunk_while_checked_out`
is that assertion.
"""

import argparse
import contextlib
import datetime
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
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import harness_fixture  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import (exercise_real_coord_holder,  # noqa: E402
                           neutralize_coord_journal, point_store_at)

GIT = shutil.which("git")
# Comfortably past session.LANE_REAP_MIN_AGE_MIN, for backdating orphan fixtures.
LANE_AGED = 180
PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]
FAIL_GATE = [["python3", "-c", "import sys; sys.exit(1)"]]


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _out(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def _files_on(repo, ref):
    r = subprocess.run([GIT, "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
                       capture_output=True, text=True)
    return set(r.stdout.split())


@unittest.skipUnless(GIT, "git not available")

def _make_live_sibling(main, lane="poga-9"):
    """A sibling lane that is ACTUALLY LIVE: a branch, a worktree, and a fresh heartbeat.

    WI-0164 gave the land gate a second death proof — a parked branch whose worktree holds
    no live session — so a reservation attributed to a lane that does not exist is now
    correctly treated as dead and released rather than blocking. These fixtures used to
    name `worktree-poga-9` with no branch behind it at all, which was never a live sibling;
    the invariant they protect (a hand-picked number must not land over a LIVE sibling's
    reservation) needs a sibling that is live by the same evidence the substrate uses.
    """
    subprocess.run(["git", "-C", str(main), "branch", f"worktree-{lane}"],
                   check=True, capture_output=True, text=True)
    state = main / ".claude" / "worktrees" / lane / ".session-state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "sibling.live").write_text(
        json.dumps({"last_beat": session._coord_iso(session.time.time())}),
        encoding="utf-8")


class WorktreeLaneBase(unittest.TestCase):
    def setUp(self):
        # The allocation-gate tests below turn on whether a drawn number is held by THIS
        # lane or another; a shared ambient journal makes every fixture lane the same
        # holder and quietly disarms the gate they exist to pin (WI-0126).
        # WI-0249: `cmd_end`'s lane path can now end a lane's runtime, and this
        # suite runs from inside one. Clear the handles it keys on — WI-0275 moved
        # that clear into the ambient neutraliser, which is `neutralize_dispatch_env`
        # plus the identity axis and therefore does both in one call.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        # A cleanup registered FIRST, so it runs LAST: the fixtures below park the CWD
        # inside this tree and restore it in their own cleanups, and a tearDown rmtree
        # would delete the directory `exercise_real_coord_holder` chdirs back into.
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.argv_log = self.tmp / "main-session-argv.log"
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        # The lane pool lives INSIDE the checkout, so the real repo ignores it; without
        # this the lanes read as untracked files in the main tree and every
        # nothing-of-its-own check (MainCheckoutSyncTest) would false-negative.
        (self.main / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\n", encoding="utf-8")
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        (self.main / "role.md").write_text("**Version:** v1.0.0\n", encoding="utf-8")
        (self.main / "sessions" / "journal").mkdir(parents=True)
        (self.main / "sessions" / "pre-journal-archive.md").write_text("", encoding="utf-8")
        (self.main / "STATUS.md").write_text(
            "---\nid: test\nversion: v0\nlast_active: 2026-01-01\nfocus: x\nblocked: false\n---\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")

        # The lane: a linked worktree on branch worktree-poga-1 (mirrors `claude --worktree`).
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(self.lane), "main")

        # ADR-0148 D3 / WI-0427: the land and ordinal paths resolve the holder journal
        # through `_find_holder_journal`, whose FIRST tier is `Path.cwd()` — the suite's
        # own checkout — so `_merge` and the lane banner globbed the real
        # `sessions/journal`. Pin every route at this fixture's main checkout. Called
        # before `_save`, so the tearDown restore lands on the fixture's values and the
        # fixture's own cleanups then put the real ones back.
        point_store_at(self, self.main)

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR", "CONFIG_PATH", "CFG")}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def _point_session_at_lane(self):
        """Make session.py operate as if it were the session running INSIDE the lane."""
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.lane / "sessions" / "journal"
        session.ARCHIVE = self.lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = self.lane / ".session-state"
        session.CONFIG_PATH = self.lane / "session.config.json"
        session.CFG = {
            "handoff": self.lane / "session-handoff.md",
            "status": self.lane / "STATUS.md",
            "role_doc": self.lane / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect", "architect_id": "test-arch",
            "machine_map": {}, "user_name": "operator", "inbox": None,
            "trunk": "main", "branch_sessions": False, "gate": PASS_GATE,
        }

    def _install_main_compiler(self, exit_code=0, marker="RECOMPILED", stamp_exit=0):
        """Put a stand-in `session.py compile` in the MAIN checkout and commit it.

        `_recompile_main_checkout` runs the main checkout's OWN `session.py compile` in a
        subprocess (see its docstring for why it cannot compile in-process from a lane).
        Until this existed the fixture's main checkout had no `session.py` at all — so the
        subprocess failed, the helper committed nothing, and every test covering the land
        path passed while the new code was INERT. That is the same false-green shape this
        repo keeps finding, so the fixture now supplies the missing half.

        This stub exercises the WIRING — that a land invokes the main checkout's compiler
        and commits what it wrote. It is deliberately not the real renderer, which is
        covered by the compile/handoff tests; `exit_code` lets a test assert the
        failure path commits nothing.

        It also RECORDS its argv (to a log outside the repo, so the main checkout stays
        clean for the sync tests). Without that, the land's second subprocess — the
        WI-0114 `stamp-status` half — could be absent entirely and every test here would
        still pass, since a stub that ignores argv answers 0 to a call that was never
        made. `stamp_exit` fails only that half, for the fail-open assertion.
        """
        (self.main / "session.py").write_text(
            "import sys, pathlib\n"
            f"pathlib.Path(r'{self.argv_log}').open('a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            f"sys.exit({stamp_exit}) if ('stamp-status' in sys.argv and {stamp_exit}) else None\n"
            "sys.exit(0) if 'stamp-status' in sys.argv else None\n"
            f"sys.exit({exit_code}) if {exit_code} else None\n"
            f"pathlib.Path(__file__).parent.joinpath('session-handoff.md')"
            f".write_text('{marker}\\n')\n",
            encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "main compiler")

    def _trunk_files(self, rev="main"):
        """Paths touched by the commit at `rev` (its diff against its own parent)."""
        r = subprocess.run(
            [GIT, "-C", str(self.main), "diff-tree", "--no-commit-id", "-r",
             "--name-only", rev], capture_output=True, text=True)
        return set(r.stdout.split())

    def _stage_lane_work(self, workfile="work.txt"):
        (self.lane / workfile).write_text("lane work\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "lane work")

    def _write_lane_journal(self, did, title, ended="2026-07-22T11:00:00+00:00"):
        """A CLOSED journal in the lane (uncommitted, as `end` leaves it), so a compile
        has a row to render."""
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": title,
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": "2026-07-22T10:00:00+00:00", "ended": ended,
            "claude-session-id": "csid-" + did,
        })


class DetectionTest(WorktreeLaneBase):
    def test_lane_is_detected(self):
        self._point_session_at_lane()
        self.assertTrue(session._is_linked_worktree())
        self.assertTrue(session._on_worktree_lane())
        self.assertEqual(session._current_branch(), "worktree-poga-1")

    def test_main_checkout_is_not_a_lane(self):
        self._point_session_at_lane()
        session.ROOT = self.main            # pretend we're in the main checkout
        self.assertFalse(session._is_linked_worktree())
        self.assertFalse(session._on_worktree_lane())

    def test_surface_label_names_lane_vs_app(self):
        """Item 7: the start block + banner name which concurrency surface a session runs
        on, so the two-surface model isn't invisible at the command line."""
        self._point_session_at_lane()
        line, tag = session._session_surface()
        self.assertIn("poga lane", line)
        self.assertEqual(tag, "lane worktree-poga-1")
        # From the main checkout it reads as the app surface and points at `poga`.
        session.ROOT = self.main
        with mock.patch.object(session, "_on_session_branch", return_value=False):
            line, tag = session._session_surface()
        self.assertIn("app surface", line)
        self.assertIn("poga", line)
        self.assertEqual(tag, "app surface")


class TheLandCommitCarriesTheGrantTrailerTest(WorktreeLaneBase):
    """CALL SITE 2 OF 2 for ADR-0112 D9 (WI-0280). Call site 1 — `_store_autocommit`, the
    store-write path — is in `test_grants.py`, with the rest of the grant machinery; this
    half lives here because `_land_worktree_lane` needs THIS module's fixture and copying
    it across would be the maintained duplicate P16 forbids.

    WHAT D9 PROMISES, and what had never been exercised: the land's close commit carries
    `Authorized-By: G-xxxx` when the lane cites a grant that actually authorizes the land,
    and carries NOTHING otherwise. The second half is the load-bearing one — a trailer on a
    commit the grant did not cover is an unbacked provenance claim in tracked history,
    which is worse than no trailer at all, because a reader takes it for a receipt.

    The work is left UNCOMMITTED on purpose. The trailer rides the land's own close commit
    (`git add -A` then commit), so a fixture that pre-commits its work — as every other
    land test here does — never reaches the line under test at all."""

    def setUp(self):
        super().setUp()
        self.addCleanup(session.cite_grant, "")
        self._point_session_at_lane()

    def _uncommitted_lane_work(self, name="work.txt"):
        (self.lane / name).write_text("lane work\n", encoding="utf-8")

    def _mint(self, **scope):
        """A grant in the fixture's own coordination store. ROOT is the lane, so
        `_git_common_dir` resolves to the fixture `.git` and the real store is untouched."""
        rec = session._grant_mint(items=list(scope.get("items", [])),
                                  lanes=list(scope.get("lanes", [])),
                                  verbs=list(scope.get("verbs", ["land"])),
                                  ttl=3600, note="WI-0280 fixture")
        self.assertTrue(rec.get("written"), "the fixture's grant did not land on disk")
        return rec["grant_id"]

    def _land(self, gid=""):
        """Run the land with `gid` cited. Returns the land's stderr."""
        session.cite_grant(gid)
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "precondition, not the subject: the lane must land")
        return err.getvalue()

    def _trailer(self, rev="worktree-poga-1"):
        """The `Authorized-By` value on the close commit, via git's own trailer parser —
        how an auditor would ask. A substring search over the message would also match a
        body mention, which is not what D9 claims to write."""
        return _out(self.main, "log", "-1",
                    f"--format=%(trailers:key={session.GRANT_TRAILER_KEY},valueonly)",
                    rev).strip()

    # ── state 1 of 4: no grant named ────────────────────────────────────────────
    def test_a_land_without_a_grant_carries_no_trailer(self):
        self._uncommitted_lane_work()
        self.assertEqual(self._land(), "")
        self.assertIn("work.txt", _files_on(self.main, "main"),
                      "precondition: the uncommitted work reached the trunk")
        self.assertEqual(self._trailer(), "")

    # ── state 2 of 4: a resolving, in-scope grant ───────────────────────────────
    def test_a_resolving_grant_lands_in_the_close_commit(self):
        gid = self._mint(verbs=["land"])
        self._uncommitted_lane_work()
        self._land(gid)
        self.assertEqual(self._trailer(), gid,
                         "D9's whole claim: the grant id is readable off tracked history "
                         "after the coordination store is gone")

    def test_a_grant_scoped_to_this_lane_resolves(self):
        """The lane axis WI-0280 wired at this call site, proved in the direction that
        must keep working — the branch name is the lane name the operator types."""
        gid = self._mint(verbs=["land"], lanes=["worktree-poga-1"])
        self._uncommitted_lane_work()
        self._land(gid)
        self.assertEqual(self._trailer(), gid)

    # ── state 3 of 4: found and live, but out of scope ──────────────────────────
    def test_a_grant_for_another_lane_does_not_stamp_this_land(self):
        """THE REGRESSION WI-0280 FIXED HERE, and the reason this call site got a third
        scope axis. The land passed `verb="land"` and nothing else, so a grant minted
        `--lane worktree-poga-9` — a DIFFERENT lane, possibly somebody else's — resolved
        for a land out of poga-1 and wrote a trailer that truthfully cited a record
        authorizing a different act. Before the fix this test reads back the grant id."""
        gid = self._mint(verbs=["land"], lanes=["worktree-poga-9"])
        self._uncommitted_lane_work()
        err = self._land(gid)
        self.assertEqual(self._trailer(), "",
                         "a grant scoped to worktree-poga-9 authorized a land out of "
                         "worktree-poga-1 (WI-0280)")
        self.assertIn(session.GRANT_OUT_OF_SCOPE, err)
        self.assertIn("worktree-poga-9", err, "the refusal must name the axis that missed")
        self.assertIn("work.txt", _files_on(self.main, "main"),
                      "and the land must still happen — a refused authorization strands "
                      "no work; it only declines to claim one")

    def test_a_grant_for_another_verb_class_does_not_stamp_this_land(self):
        """D6's carve-out at the commit: a `--for note` grant does not cover a land."""
        gid = self._mint(verbs=["note"])
        self._uncommitted_lane_work()
        err = self._land(gid)
        self.assertEqual(self._trailer(), "")
        self.assertIn(session.GRANT_OUT_OF_SCOPE, err)
        self.assertIn("'land'", err)

    # ── state 4 of 4: an unknown id ─────────────────────────────────────────────
    def test_an_unknown_id_does_not_stamp_this_land(self):
        self._uncommitted_lane_work()
        err = self._land("G-000000")
        self.assertEqual(self._trailer(), "")
        self.assertIn(session.GRANT_NO_SUCH, err)
        self.assertIn("work.txt", _files_on(self.main, "main"))

    # ── WI-0280's decision, at the artifact ─────────────────────────────────────
    def test_an_exported_variable_does_not_stamp_this_land(self):
        """`_grant_trailer_args` used to fall through to `os.environ.get("POGA_GRANT")`,
        so an operator with it exported stamped every land in that shell. It does not any
        more, and this is one of the two tests that fail if it is put back (the other is
        in `test_grants.py`, over the note path and the builder's own source)."""
        os.environ["POGA_GRANT"] = self._mint(verbs=["land"])
        self._uncommitted_lane_work()
        self.assertEqual(self._land(), "")
        self.assertEqual(self._trailer(), "",
                         "an exported POGA_GRANT stamped a provenance line onto a real "
                         "land commit — the ambient channel is back (WI-0280)")


class LandTest(WorktreeLaneBase):
    def test_cas_advances_trunk_while_checked_out(self):
        """THE load-bearing fact: update-ref moves main even though the main worktree
        holds it. If this fails, ADR-0060's plumbing land is invalid."""
        self._point_session_at_lane()
        self._stage_lane_work()
        landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "lane should land")
        # Trunk ref advanced to include the lane's work.
        self.assertIn("work.txt", _files_on(self.main, "main"))
        # Lane branch tip == trunk tip (a pure ff — no merge commit).
        self.assertEqual(_out(self.main, "rev-parse", "worktree-poga-1"),
                         _out(self.main, "rev-parse", "main"))

    def test_the_close_commit_carries_no_bytecode_and_main_syncs(self):
        """WI-0461, the reported shape. No bytecode rule in `.gitignore`, `.pyc` beside the
        lane's work AND untracked at the same path in main. The close commit used to take
        the `.pyc` to trunk, the main sync then refused over main's copy, and the landed
        file was in HEAD but not on disk. Now trunk gets no bytecode and main materializes."""
        self._install_main_compiler()
        self._point_session_at_lane()
        pyc = pathlib.Path("__pycache__") / "greet.cpython-39.pyc"
        for tree in (self.lane, self.main):
            (tree / "__pycache__").mkdir()
            (tree / pyc).write_bytes(b"\x00" + tree.name.encode())
        (self.lane / "greet.py").write_text("def greet():\n    return 'hi'\n",
                                            encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            landed = session._land_worktree_lane(None, None, "1.0.0",
                                                 commit_msg="add greet", push=False)
        self.assertTrue(landed)
        files = _files_on(self.main, "main")
        self.assertIn("greet.py", files)
        self.assertEqual([f for f in files if f.endswith(".pyc")], [],
                         "no bytecode reached the trunk")
        self.assertTrue((self.main / "greet.py").is_file(),
                        "the landed file is on disk in the main checkout")
        self.assertEqual([l for l in _out(self.main, "status", "--porcelain").splitlines()
                          if not l.startswith("??")], [],
                         "main shows no staged deletions after the land")

    def test_landed_branch_is_deletable_and_lane_clean(self):
        self._point_session_at_lane()
        self._stage_lane_work()
        session._land_worktree_lane(None, None, "1.0.0", push=False)
        # The lane worktree is clean (nothing uncommitted) — Claude can remove it.
        self.assertEqual(_out(self.lane, "status", "--porcelain"), "",
                         "lane should be clean after landing")
        # The merged branch deletes with -d (the WorktreeRemove hook's safe delete).
        _git(self.main, "worktree", "remove", "--force", str(self.lane))
        r = subprocess.run([GIT, "-C", str(self.main), "branch", "-d", "worktree-poga-1"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"merged branch should -d cleanly: {r.stderr}")

    def test_gate_failure_blocks_and_preserves_lane(self):
        self._point_session_at_lane()
        session.CFG["gate"] = FAIL_GATE
        self._stage_lane_work()
        landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed)
        self.assertNotIn("work.txt", _files_on(self.main, "main"), "nothing reached trunk")
        # Work is safe on the lane branch (the commit still exists).
        self.assertIn("work.txt", _files_on(self.main, "worktree-poga-1"))

    def test_a_lane_that_merged_the_trunk_lands_without_replaying_it(self):
        """A lane holding a genuine merge of a diverged trunk must land by fast-forward.

        The land path rebased UNCONDITIONALLY, so a lane that had merged the trunk in got
        that merge flattened — re-applying both sides of an already-settled merge and
        reporting conflicts on paths neither side had touched since. That is precisely
        the replay ADR-0102 forbids and that such a lane merges in order to avoid, which
        made the sanctioned shape unlandable by the verb built to land it. Found for real
        landing the ADR-0103/0106 promotion reconciliation.

        Pinned on the OUTCOME, not the mechanism: the trunk ends up containing both sides
        AND the merge commit survives with both its parents. Asserting merely that it
        landed would pass against the flattening it exists to forbid."""
        self._point_session_at_lane()

        # Trunk moves on its own, in a file the lane never touches.
        (self.main / "trunk-only.txt").write_text("from the trunk\n", encoding="utf-8")
        _git(self.main, "add", "trunk-only.txt")
        _git(self.main, "commit", "-m", "trunk work")

        # The lane does its own work, then MERGES the trunk in rather than rebasing.
        self._stage_lane_work("lane-only.txt")
        _git(self.lane, "merge", "main", "-m", "merge: bring the trunk into the lane")
        merge_tip = _out(self.lane, "rev-parse", "HEAD")
        self.assertEqual(len(_out(self.lane, "rev-list", "--parents", "-1", "HEAD").split()),
                         3, "precondition: the lane tip is a real two-parent merge")

        landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "a lane containing the trunk must land")

        # Both sides reached the trunk.
        on_main = _files_on(self.main, "main")
        self.assertIn("lane-only.txt", on_main)
        self.assertIn("trunk-only.txt", on_main)

        # And the merge was PRESERVED, not replayed: the lane's merge commit is itself
        # an ancestor of the trunk, which a rebase would have replaced with new SHAs.
        anc = subprocess.run([GIT, "-C", str(self.main), "merge-base",
                              "--is-ancestor", merge_tip, "main"],
                             capture_output=True, text=True)
        self.assertEqual(anc.returncode, 0,
                         "the lane's merge commit must survive the land, not be rebuilt")

    def test_second_lane_loses_cas_then_rebases_and_lands(self):
        """Two lanes racing: the first lands; the second's CAS fails, it rebases on the
        new trunk tip and lands — the ADR-0051 C3 loser-rebases rule, ref-enforced."""
        # Lane 1 lands.
        self._point_session_at_lane()
        self._stage_lane_work("a.txt")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))

        # Lane 2 on a second worktree, cut from the ORIGINAL main (now stale).
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        (lane2 / "b.txt").write_text("lane 2\n", encoding="utf-8")
        _git(lane2, "add", "-A")
        _git(lane2, "commit", "-qm", "lane 2 work")
        session.ROOT = lane2
        session.JOURNAL_DIR = lane2 / "sessions" / "journal"
        session.ARCHIVE = lane2 / "sessions" / "pre-journal-archive.md"
        session.CFG["handoff"] = lane2 / "session-handoff.md"
        session.CFG["status"] = lane2 / "STATUS.md"
        session.CFG["role_doc"] = lane2 / "role.md"
        landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "second lane should land after rebasing on the new tip")
        # Both lanes' work is on trunk; history is linear.
        self.assertIn("a.txt", _files_on(self.main, "main"))
        self.assertIn("b.txt", _files_on(self.main, "main"))


class RoledocConformanceWarnTest(WorktreeLaneBase):
    """ADR-0053 R3 `versioned-role-doc-and-changelog`, folded into the land
    (phase2-followons) as a WARN scoped to this land's own new commits — never a
    block, and never re-flagging a violation already sitting on the trunk before
    this land started (the roadmap's own 'new vs pre-existing' framing)."""

    def _bump_role_doc(self, lane, version_line="**Version:** v1.0.0\n", extra=""):
        (lane / "role.md").write_text(version_line + extra, encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", "edit role doc")

    def test_no_role_doc_rel_declared_is_a_silent_noop(self):
        self._point_session_at_lane()
        # CFG carries role_doc (a Path) but not role_doc_rel — the fixture default.
        self._bump_role_doc(self.lane, extra="changed, no version bump\n")
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(self.lane, "rev-parse", "HEAD")
        self.assertIsNone(session._new_roledoc_violation(parent, tip))

    def test_role_doc_changed_without_version_bump_warns(self):
        self._point_session_at_lane()
        session.CFG["role_doc_rel"] = "role.md"
        self._bump_role_doc(self.lane, extra="changed, no version bump\n")
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(self.lane, "rev-parse", "HEAD")
        warn = session._new_roledoc_violation(parent, tip)
        self.assertIsNotNone(warn)
        self.assertIn("role.md", warn)
        self.assertIn("no", warn.lower())

    def test_role_doc_changed_with_version_bump_is_silent(self):
        self._point_session_at_lane()
        session.CFG["role_doc_rel"] = "role.md"
        self._bump_role_doc(self.lane, version_line="**Version:** v1.1.0\n")
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(self.lane, "rev-parse", "HEAD")
        self.assertIsNone(session._new_roledoc_violation(parent, tip))

    def test_role_doc_untouched_is_silent(self):
        self._point_session_at_lane()
        session.CFG["role_doc_rel"] = "role.md"
        self._stage_lane_work()  # touches work.txt, never role.md
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(self.lane, "rev-parse", "HEAD")
        self.assertIsNone(session._new_roledoc_violation(parent, tip))

    def test_pre_existing_trunk_violation_is_not_re_flagged(self):
        """A violation already on the trunk before this lane branched is NOT this
        lane's fault — scoping to parent..tip must not see history before parent."""
        # Land one lane's role-doc-bump-free edit first (a pre-existing violation).
        self._point_session_at_lane()
        session.CFG["role_doc_rel"] = "role.md"
        self._bump_role_doc(self.lane, extra="first bad edit\n")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))

        # A second, unrelated lane cut from the NEW (already-violating) trunk tip.
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        (lane2 / "unrelated.txt").write_text("x\n", encoding="utf-8")
        _git(lane2, "add", "-A")
        _git(lane2, "commit", "-qm", "unrelated work")
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(lane2, "rev-parse", "HEAD")
        # role.md is untouched in parent..tip (only unrelated.txt changed) -> silent,
        # even though the trunk it's building on already carries the violation.
        self.assertIsNone(session._new_roledoc_violation(parent, tip))

    def test_warn_does_not_block_the_land(self):
        self._point_session_at_lane()
        session.CFG["role_doc_rel"] = "role.md"
        self._bump_role_doc(self.lane, extra="changed, no version bump\n")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "a role-doc conformance warning must never block a land")
        self.assertIn("warn:", out.getvalue())
        self.assertIn("role.md", _files_on(self.main, "main"))


class AcceptLagTest(WorktreeLaneBase):
    """ADR-0060 (revised, session-83 drill): a lane lands ONLY its real work — the closed
    journal + any files it changed — and NEVER the generated views. Folding a freshly
    compiled handoff/STATUS into the rebased close commit made a CAS-losing lane collide on
    those generated files (see ViewAmendConflictTest). The views are derived and heal at
    the next session start (accept-lag), exactly like the ADR-0056 D2 branch model. So the
    load-bearing invariant is: no generated view ever appears in a lane's landed commit."""

    def _landed_commit_files(self, before):
        """Paths touched by the lane's LANDING commit — the first commit to arrive on the
        trunk after `before`.

        Anchored on the landing commit itself rather than on the trunk tip. The tip is no
        longer the same thing: since session ~103 a successful land is followed by a
        separate view-recompile commit (`_recompile_main_checkout`), so diffing the tip
        would report the recompile's files and silently stop testing this invariant.
        """
        r = subprocess.run(
            [GIT, "-C", str(self.main), "rev-list", "--reverse", f"{before}..main"],
            capture_output=True, text=True)
        first = r.stdout.split()[0]
        d = subprocess.run(
            [GIT, "-C", str(self.main), "diff-tree", "--no-commit-id", "-r",
             "--name-only", first], capture_output=True, text=True)
        return set(d.stdout.split())

    def test_lane_lands_journal_not_generated_views(self):
        self._install_main_compiler()      # so the recompile really runs; see the helper
        self._point_session_at_lane()
        before = subprocess.run([GIT, "-C", str(self.main), "rev-parse", "main"],
                                capture_output=True, text=True).stdout.strip()
        did = "20260722T1500Z-runner-abcd"
        self._write_lane_journal(did, "a distinctive lane title")
        landed = session._land_worktree_lane(None, None, "9.9.9",
                                             commit_msg="close lane", push=False)
        self.assertTrue(landed)
        touched = self._landed_commit_files(before)
        # The journal landed…
        self.assertIn(f"sessions/journal/{did}.md", touched, "journal landed on trunk")
        # …but the GENERATED views did NOT ride the landing commit (accept-lag; heal next start).
        self.assertNotIn("session-handoff.md", touched,
                         "handoff must NOT be folded into the landed commit (view-amend hazard)")
        self.assertNotIn("STATUS.md", touched,
                         "STATUS must NOT be folded into the landed commit (view-amend hazard)")


class ViewRefreshAfterLandTest(WorktreeLaneBase):
    """The compiled views must be REFRESHED on the trunk after a lane lands.

    The bug (found session ~103, reported by operator: every session named the same stale
    session as the previous one): `session-handoff.md` had not regenerated since the last
    close that ran from a main checkout. Two mechanisms each correctly declined to
    compile — the land keeps generated views out of its rebased history
    (`AcceptLagTest` / `ViewAmendConflictTest`), and the next-start heal no-ops on a
    lane (ADR-0056 D2) — so once every session became a lane, nobody compiled and
    startup kept injecting a days-old session as "the prior session."

    These pin BOTH halves together: the views must not ride the landing commit (that
    invariant still holds, see `AcceptLagTest`) AND they must be current on the trunk
    once the land returns.
    """

    def _handoff(self):
        return (self.main / "session-handoff.md").read_text(encoding="utf-8")

    def test_views_are_recompiled_and_committed_after_a_land(self):
        self._install_main_compiler(marker="FRESH-VIEW")
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane(None, None, "9.9.9",
                                                    commit_msg="close lane", push=False))
        self.assertIn("FRESH-VIEW", self._handoff(),
                      "the trunk's handoff must be regenerated after a lane lands")
        self.assertIn("session-handoff.md", self._trunk_files("main"),
                      "the regenerated view must be COMMITTED, not left dirty — a dirty "
                      "main checkout blocks the next land's fast-forward sync")

    def test_the_recompile_is_a_separate_commit_from_the_landing_commit(self):
        """Both invariants at once: views current on the trunk, but never inside the
        lane's own landed history (which is what made a CAS-loser collide)."""
        self._install_main_compiler(marker="FRESH-VIEW")
        self._point_session_at_lane()
        before = subprocess.run([GIT, "-C", str(self.main), "rev-parse", "main"],
                                capture_output=True, text=True).stdout.strip()
        did = "20260722T1500Z-runner-abcd"
        self._write_lane_journal(did, "landed work")
        self.assertTrue(session._land_worktree_lane(None, None, "9.9.9",
                                                    commit_msg="close lane", push=False))
        landing = subprocess.run(
            [GIT, "-C", str(self.main), "rev-list", "--reverse", f"{before}..main"],
            capture_output=True, text=True).stdout.split()
        self.assertEqual(len(landing), 2, "expected the landing commit + a view commit")
        first = subprocess.run(
            [GIT, "-C", str(self.main), "diff-tree", "--no-commit-id", "-r",
             "--name-only", landing[0]], capture_output=True, text=True).stdout.split()
        self.assertIn(f"sessions/journal/{did}.md", first)
        self.assertNotIn("session-handoff.md", first)
        self.assertIn("session-handoff.md", self._trunk_files(landing[1]))

    def test_a_failing_compile_commits_nothing_and_does_not_fail_the_land(self):
        """Fail-open: the land already succeeded. A broken compiler must leave the trunk
        alone and report, never abort a landed session."""
        self._install_main_compiler(exit_code=3)
        self._point_session_at_lane()
        before = subprocess.run([GIT, "-C", str(self.main), "rev-parse", "main"],
                                capture_output=True, text=True).stdout.strip()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane(None, None, "9.9.9",
                                                    commit_msg="close lane", push=False),
                        "a compile failure must not fail the land")
        landed = subprocess.run(
            [GIT, "-C", str(self.main), "rev-list", f"{before}..main"],
            capture_output=True, text=True).stdout.split()
        self.assertEqual(len(landed), 1, "only the landing commit; no view commit")

    def test_no_session_py_in_the_main_checkout_is_reported_not_silent(self):
        """The exact false-green that hid this: with no compiler present the helper must
        say so rather than return quietly as if it had refreshed."""
        self._point_session_at_lane()
        report = session._recompile_main_checkout("main")
        self.assertIn("NOT refreshed", report)

    def test_a_dirty_main_checkout_is_refused(self):
        self._point_session_at_lane()
        (self.main / "trunkfile.txt").write_text("local edit\n", encoding="utf-8")
        report = session._recompile_main_checkout("main")
        self.assertIn("NOT refreshed", report)
        self.assertIn("local changes", report)

    def test_a_main_checkout_on_another_branch_is_refused(self):
        self._point_session_at_lane()
        _git(self.main, "checkout", "-q", "-b", "sidetrack")
        report = session._recompile_main_checkout("main")
        self.assertIn("NOT refreshed", report)
        self.assertIn("sidetrack", report)

    def _argv_lines(self):
        if not self.argv_log.exists():
            return []
        return [l for l in self.argv_log.read_text(encoding="utf-8").splitlines() if l]

    def test_a_lane_land_stamps_status_in_the_main_checkout(self):
        """WI-0114. The land recompiled the views and never stamped STATUS.md's header
        fields, so `last_active` sat frozen for a week while the system ran nightly —
        and `curate/reconcile.py` mines exactly that field as every member's staleness
        signal. Assert the land actually invokes the stamp, with the judgment fields it
        was given."""
        self._install_main_compiler()
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane("a focus line", "false", "9.9.9",
                                                    commit_msg="close lane", push=False))
        stamps = [l for l in self._argv_lines() if l.startswith("stamp-status")]
        self.assertEqual(len(stamps), 1,
                         f"the land must invoke stamp-status exactly once; got {self._argv_lines()}")
        self.assertIn("--version 9.9.9", stamps[0])
        self.assertIn("--focus a focus line", stamps[0])
        self.assertIn("--blocked false", stamps[0])

    def test_the_stamp_targets_the_trunk_copy_not_the_lane_copy(self):
        """The half that made the call site wrong rather than merely missing:
        `_stamp_status` resolves `CFG["status"]`, which inside a lane is the lane's own
        STATUS.md — torn down minutes later. The stamp must therefore run in the main
        checkout, leaving the lane's copy untouched."""
        self._install_main_compiler()
        self._point_session_at_lane()
        before = (self.lane / "STATUS.md").read_text(encoding="utf-8")
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane(None, None, "9.9.9",
                                                    commit_msg="close lane", push=False))
        self.assertEqual(before, (self.lane / "STATUS.md").read_text(encoding="utf-8"),
                         "nothing may stamp the lane's doomed copy of STATUS.md")

    def test_a_failing_stamp_is_named_and_does_not_fail_the_land(self):
        """Fail-open like the compile half — but never silently. An unstamped STATUS.md
        is the wrong-answer-shaped-like-a-right-one this item exists to remove, so it is
        reported rather than swallowed."""
        self._install_main_compiler(stamp_exit=4)
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "9.9.9",
                                                 commit_msg="close lane", push=False)
        self.assertTrue(landed, "a stamp failure must not fail a land that already succeeded")
        self.assertIn("NOT stamped", buf.getvalue())
        self.assertNotIn("STATUS.md recompiled", buf.getvalue(),
                         "a land that could not stamp must not report a clean refresh")


class StatusStampAfterLandTest(WorktreeLaneBase):
    """WI-0127 — the worktree-lane land was the one close path that never stamped
    `STATUS.md`, reported by a member's Architect.

    `_stamp_status` had exactly two call sites: the plain `end` path and `_land_branch`.
    A poga lane lands through `_land_worktree_lane` → `_recompile_main_checkout`, which
    runs `compile` (the census line only) and then commits `STATUS.md` — so `version` and
    `last_active` froze at whatever the last NON-lane close left behind, under a commit
    titled as a views recompile that genuinely did modify the file.

    Why it hid, which is the part these tests exist to keep fixed: every available signal
    said the file was maintained. It was — just not the two fields other systems read.
    `STATUS.md`'s own prose makes `last_active` the freshness signal, so the frozen value
    tells every consumer to discount a status block that is current, and does so most
    reliably for the members with the most lane traffic. Observed in one member
    (`version 0.1.0` against a `1.1.1` role doc, `last_active` five sessions stale) and in
    the federation itself (`2.52.0` / `2026-08-07` beside a census refreshed every land).
    """

    STATUS = ("---\nid: test\nphase: active\nversion: 0.0.1\n"
              "last_active: 2020-01-01\ncensus: 0 sessions\n"
              "focus: stale focus\nblocked: false\n---\n\n# test — status\n")

    def _seed_status(self):
        """A STATUS.md in BOTH trees — the whole question is which one gets written."""
        (self.main / "STATUS.md").write_text(self.STATUS, encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed status")
        (self.lane / "STATUS.md").write_text(self.STATUS, encoding="utf-8")

    def _today(self):
        return datetime.datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%d")

    def _land(self, focus=None, blocked=None):
        self._install_main_compiler(marker="FRESH-VIEW")
        self._seed_status()
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane(focus, blocked, "9.9.9",
                                                    commit_msg="close lane", push=False))

    def test_the_land_stamps_the_main_checkouts_status(self):
        self._land()
        text = (self.main / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("version: 9.9.9", text)
        self.assertIn(f"last_active: {self._today()}", text)

    def test_it_stamps_main_not_the_lanes_own_copy(self):
        """`CFG["status"]` resolves INSIDE the lane — the copy nobody reads and which is
        torn down with the worktree. A stamp written there would satisfy a marker check
        and change nothing an operator or a sibling system ever sees."""
        self._land()
        lane_text = (self.lane / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("version: 0.0.1", lane_text)
        self.assertIn("last_active: 2020-01-01", lane_text)

    def test_the_stamp_is_committed_on_the_trunk(self):
        """Written-but-uncommitted is not fixed: a dirty main checkout blocks the next
        land's fast-forward sync, so the stamp has to ride the commit this path makes."""
        self._land()
        self.assertIn("STATUS.md", self._trunk_files("main"))
        self.assertEqual(
            subprocess.run([GIT, "-C", str(self.main), "status", "--porcelain",
                            "--", "STATUS.md"], capture_output=True, text=True).stdout,
            "", "STATUS.md left dirty in the main checkout")

    def test_focus_and_blocked_ride_through_from_the_lanes_end(self):
        """They arrive on the lane's `end` invocation and used to be dropped silently —
        the same defect wearing a judgment-field hat."""
        self._land(focus="the current focus line", blocked="waiting on a decision")
        text = (self.main / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn("focus: the current focus line", text)
        self.assertIn("blocked: waiting on a decision", text)

    def test_an_omitted_focus_leaves_the_existing_line_alone(self):
        """`None` means "not supplied", never "clear it" — a close that says nothing about
        focus must not erase the last one."""
        self._land()
        self.assertIn("focus: stale focus",
                      (self.main / "STATUS.md").read_text(encoding="utf-8"))

    def test_a_failing_compile_stamps_nothing(self):
        """Fail-open in the other direction too: if the views could not be regenerated the
        path returns before the stamp, so STATUS is never advanced past a compile that
        did not happen."""
        self._install_main_compiler(exit_code=3)
        self._seed_status()
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1500Z-runner-abcd", "landed work")
        self.assertTrue(session._land_worktree_lane(None, None, "9.9.9",
                                                    commit_msg="close lane", push=False))
        self.assertIn("version: 0.0.1",
                      (self.main / "STATUS.md").read_text(encoding="utf-8"))


class ViewAmendConflictTest(WorktreeLaneBase):
    """Regression for the session-83 drill's view-amend hazard, deterministically forced.

    A lane amending its freshly-compiled views into the rebased close commit collides with
    the winner's own view edit when it LOSES a compare-and-swap and must rebase onto the new
    trunk tip: both close commits touched the same generated handoff/STATUS. The 10 green
    unit tests missed it because their lanes had no distinct journals -> identical views ->
    no conflict. Here a racing competitor advances the trunk (with its own view edit) between
    the lane's gate and its CAS, so the lane's first CAS fails and it must rebase onto a tip
    that also edited the views. With the fix (views never ride the close commit) the rebase
    carries only the distinct journal file and lands cleanly."""

    def test_cas_losing_lane_does_not_conflict_on_views(self):
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1600Z-runner-bbbb", "beta lane title")

        real_gate = session._gate_commit
        state = {"raced": False}

        def racing_gate(sha, skip=frozenset()):
            # WI-0347 gave `_gate_commit` a per-command skip plan. Passed straight
            # through rather than dropped: this spy is here to race the CAS, not to
            # change what the gate under it decides to run.
            ok, rep = real_gate(sha, skip)
            if not state["raced"]:
                state["raced"] = True
                # A competitor lands between our gate and our CAS: it advances the trunk
                # AND edits the generated handoff — exactly what a sibling lane's view-amend
                # would do. Our pending CAS (expecting the old tip) will now fail, forcing a
                # rebase onto this competitor tip.
                (self.main / "session-handoff.md").write_text(
                    "competitor handoff EDIT\n", encoding="utf-8")
                _git(self.main, "add", "session-handoff.md")
                _git(self.main, "commit", "-qm", "competitor view edit")
            return ok, rep

        with mock.patch.object(session, "_gate_commit", side_effect=racing_gate):
            landed = session._land_worktree_lane(None, None, "1.0.0",
                                                 commit_msg="close beta", push=False)
        self.assertTrue(landed, "CAS-losing lane must rebase past the competitor and land, "
                                "not conflict on the generated views")
        trunk_files = _files_on(self.main, "main")
        self.assertIn("sessions/journal/20260722T1600Z-runner-bbbb.md", trunk_files,
                      "the lane's journal reached the trunk")
        self.assertIn("session-handoff.md", trunk_files, "competitor's file is intact on trunk")


class WorktreeRemoveHookTest(WorktreeLaneBase):
    def test_branch_from_path_payload(self):
        self.assertEqual(
            session._worktree_branch_from_payload({"worktree_path": "/x/.claude/worktrees/poga-3"}),
            "worktree-poga-3")

    def test_branch_from_explicit_field(self):
        self.assertEqual(
            session._worktree_branch_from_payload({"branch": "worktree-poga-9"}),
            "worktree-poga-9")

    def test_empty_payload_is_none(self):
        self.assertIsNone(session._worktree_branch_from_payload({}))

    def _fire_hook(self, payload):
        """Drive cmd_worktree_remove as Claude does: hook JSON on stdin, from the main
        checkout. It always sys.exit(0) (fail-open), so the SystemExit is expected."""
        session.ROOT = self.main
        session.CFG = {"trunk": "main"}
        buf = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with mock.patch.object(sys, "stdin", buf):
                with self.assertRaises(SystemExit) as e:
                    session.cmd_worktree_remove(argparse.Namespace())
        self.assertEqual(e.exception.code, 0, "the hook must always fail open")
        return out.getvalue()

    def test_hook_removes_the_worktree_not_just_the_branch(self):
        """The session-89 teardown gap. Claude DELEGATES removal to a configured hook
        (falling back to `git worktree remove` only when none exists), so a hook that
        merely prunes leaves the lane dir + lock behind — and the still-checked-out
        branch then refuses `git branch -d`, which strands the branch and its
        coordination holds too. One root cause, three symptoms."""
        self._stage_lane_work()
        _git(self.main, "merge", "-q", "--no-ff", "-m", "land", "worktree-poga-1")
        self.assertTrue(self.lane.exists())

        self._fire_hook({"worktree_path": str(self.lane)})

        self.assertFalse(self.lane.exists(), "the lane worktree directory must be removed")
        branches = subprocess.run([GIT, "-C", str(self.main), "branch", "--format=%(refname:short)"],
                                  capture_output=True, text=True).stdout.split()
        self.assertNotIn("worktree-poga-1", branches,
                         "the merged lane branch must be deleted once the worktree is gone")

    def test_hook_keeps_a_dirty_lane_and_its_branch(self):
        """Bare `git worktree remove` (never --force) refuses a lane with uncommitted
        work, so nothing is destroyed: Claude logs it as kept and the reaper surfaces it.
        The unmerged branch survives with the work still on it (P9)."""
        self._stage_lane_work()
        (self.lane / "uncommitted.txt").write_text("in flight\n", encoding="utf-8")

        self._fire_hook({"worktree_path": str(self.lane)})

        self.assertTrue(self.lane.exists(), "a dirty lane is never force-removed")
        branches = subprocess.run([GIT, "-C", str(self.main), "branch", "--format=%(refname:short)"],
                                  capture_output=True, text=True).stdout.split()
        self.assertIn("worktree-poga-1", branches, "unmerged work stays on its branch")


class TornDownLaneClosesItsJournalsTest(WorktreeLaneBase):
    """WI-0118. A lane keeps its liveness sidecar inside its own worktree, so teardown
    destroys the evidence the reaper reads and the journal falls to the janitor's aged-out
    path — 7 days of waiting, `duration: unknown`, for a fact already on screen (a fleet
    dashboard printed "lane gone" and "lane torn down" on the same row the whole time).

    The item guessed the blocker was that a journal does not record which worktree it ran
    in. The real blocker was one layer down and worse: `_lane_journal_ids` answers a
    `{trunk}...{branch}` symmetric difference, which is EMPTY once the branch is merged —
    the state every successful teardown is in, since `git branch -d` only succeeds on a
    merged branch. Measured directly: the same lane yields ['J1'] before the merge and []
    after. The association therefore comes from the lane's own sidecar.
    """

    def _open_lane_journal(self, did="20260808T1200Z-runner-8929", title=None,
                           started="2026-08-08T10:00:00+00:00"):
        """An OPEN journal committed on the lane branch — what a session that never ran
        its close leaves behind."""
        self._point_session_at_lane()
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1,
            "title": title or session.JOURNAL_STUB_TITLE,
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": started, "ended": "",
            "claude-session-id": "csid-" + did,
        })
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "journal")
        for k, v in self._save.items():
            setattr(session, k, v)
        return did

    def _write_lane_sidecar(self, did, last_beat):
        """The sidecar a real lane session writes at its first heartbeat."""
        d = self.lane / ".session-state"
        d.mkdir(exist_ok=True)
        (d / ("csid-" + did + ".live")).write_text(
            json.dumps({"session_id": did, "last_beat": last_beat}), encoding="utf-8")

    def _land(self):
        _git(self.main, "merge", "-q", "--no-ff", "-m", "land", "worktree-poga-1")

    def _fire(self, payload):
        """Drive the hook from the main checkout, with a REALISTIC config.

        `tz` is load-bearing and was the whole reason the first cut of these tests read
        as a broken feature: `_now_iso()` is `datetime.now(CFG["tz"])`, a stub config
        without it raises `KeyError`, and the hook is fail-open — so the close became a
        silent no-op and the fixture blamed the code. Production `CFG` always carries a
        tz; a fixture that omits it is testing a configuration nothing runs."""
        session.ROOT = self.main
        session.JOURNAL_DIR = self.main / "sessions" / "journal"
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}
        buf = io.StringIO(json.dumps(payload))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            with mock.patch.object(sys, "stdin", buf):
                with self.assertRaises(SystemExit):
                    session.cmd_worktree_remove(argparse.Namespace())
        return out.getvalue()

    def _main_journal(self, did):
        text = (self.main / "sessions" / "journal" / (did + ".md")).read_text(encoding="utf-8")
        fm, _body = session.parse_journal(text)
        return fm

    def test_the_branch_cannot_supply_the_association_once_it_is_merged(self):
        """The defect underneath this item, pinned directly. WI-0123 reads these ids "while
        the branch still exists" to release the lane's coordination holds — and gets an
        empty tuple every time on the path that matters."""
        self._open_lane_journal()
        session.ROOT = self.main          # the hook runs from the main checkout
        session.CFG = {"trunk": "main"}
        self.assertEqual(len(session._lane_journal_ids("worktree-poga-1")), 1,
                         "before the merge the branch does answer")
        self._land()
        self.assertEqual(session._lane_journal_ids("worktree-poga-1"), [],
                         "merged is exactly when a successful teardown reads this")

    def test_the_sidecar_supplies_it_regardless_of_the_merge(self):
        did = self._open_lane_journal()
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        self._land()
        self.assertEqual(session.lane_session_ids(self.lane / ".session-state"), (did,))

    def test_teardown_closes_the_lanes_open_journal(self):
        did = self._open_lane_journal()
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        self._land()

        out = self._fire({"worktree_path": str(self.lane)})

        self.assertFalse(self.lane.exists())
        fm = self._main_journal(did)
        self.assertTrue(fm["ended"].strip(), "the journal must not be left for the janitor")
        self.assertIn("worktree-remove", fm["closed-by"],
                      "a machine close must say which machine closed it")
        self.assertIn("torn down", fm["title"])
        self.assertIn(did, out, "the hook reports what it closed")

    def test_a_kept_dirty_lane_never_closes_its_journal(self):
        """The proof is the ABSENCE of the directory, not the removal command. Bare
        `git worktree remove` refuses a dirty lane by design, and on that path the session
        may still be running — closing there would manufacture a death."""
        did = self._open_lane_journal()
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        self._land()
        (self.lane / "uncommitted.txt").write_text("in flight", encoding="utf-8")

        self._fire({"worktree_path": str(self.lane)})

        self.assertTrue(self.lane.exists(), "precondition: the dirty lane survives")
        self.assertEqual(self._main_journal(did)["ended"].strip(), "",
                         "a lane that is still on disk has not proved anything")

    def test_the_last_beat_is_preferred_over_the_teardown_instant(self):
        did = self._open_lane_journal(started="2026-08-08T10:00:00+00:00")
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        self._land()

        self._fire({"worktree_path": str(self.lane)})

        fm = self._main_journal(did)
        self.assertEqual(fm["ended"], "2026-08-08T12:30:00+00:00")
        self.assertNotEqual(fm["duration"], "unknown",
                            "a real beat yields a real duration")

    def test_no_sidecar_leaves_the_journal_to_the_janitor(self):
        """The limit of this mechanism, pinned deliberately rather than papered over. With
        no sidecar and a merged branch there is NO evidence linking the lane to a journal,
        and the right answer is to leave it rather than close something on a guess.

        WI-0118's instance reported "no sidecar anywhere" — but that was checked AFTER
        teardown, which proves nothing about what was there before it."""
        did = self._open_lane_journal()
        self._land()

        self._fire({"worktree_path": str(self.lane)})

        self.assertFalse(self.lane.exists(), "precondition: the lane was removed")
        self.assertEqual(self._main_journal(did)["ended"].strip(), "",
                         "no evidence means no close — the janitor's aged-out path owns it")

    def test_an_already_closed_journal_is_left_alone(self):
        did = self._open_lane_journal()
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        jp = self.main / "sessions" / "journal" / (did + ".md")
        self._land()
        jp.write_text(session.finalize_journal(
            jp.read_text(encoding="utf-8"), "2026-08-08T11:00:00+00:00", "1h 0m",
            title="Real work"), encoding="utf-8")

        self._fire({"worktree_path": str(self.lane)})

        fm = self._main_journal(did)
        self.assertEqual(fm["title"], "Real work", "a real close is never restated")
        self.assertEqual(fm["duration"], "1h 0m")

    def test_a_session_authored_title_survives_the_close(self):
        """WI-0137's rule: stamp the death class only over the birth placeholder. A title
        the session wrote mid-flight is the one piece of judgment in the file."""
        did = self._open_lane_journal(title="Landed the delivery guard")
        self._write_lane_sidecar(did, "2026-08-08T12:30:00+00:00")
        self._land()

        self._fire({"worktree_path": str(self.lane)})

        fm = self._main_journal(did)
        self.assertEqual(fm["title"], "Landed the delivery guard")
        self.assertTrue(fm["ended"].strip(), "it is still closed, just not retitled")


class LaneInboxVisibilityTest(WorktreeLaneBase):
    """Session 90: `proposed-edits/` is gitignored, so it does not exist in a lane's
    checkout — every lane start read its inbox as EMPTY and `apply-briefs` silently
    no-opped, over briefs that were sitting in the main checkout the whole time. The
    inbox now resolves against the shared work root (the git common dir's parent), the
    same resolution the coordination store uses."""

    def _make_main_inbox(self, filename="2026-07-23-a-brief.md"):
        pending = self.main / "proposed-edits" / "test-arch" / "pending"
        pending.mkdir(parents=True)
        (pending / filename).write_text("---\napply: manual\nmanual-reason: attended\n---\n",
                                        encoding="utf-8")
        return pending

    def test_lane_resolves_the_inbox_to_the_main_checkout(self):
        main_pending = self._make_main_inbox()
        self._point_session_at_lane()
        # As config load builds it: the configured path, relative to the LANE.
        session.CFG["inbox"] = self.lane / "proposed-edits" / "test-arch" / "pending"
        self.assertFalse(session.CFG["inbox"].exists(), "gitignored — absent in the lane")

        pending, applied = session.inbox_dirs()

        # resolve() both sides — the shared root comes back in macOS's /private/var form.
        self.assertEqual(pending.resolve(), main_pending.resolve())
        self.assertTrue(session.inbox_visible(pending))
        self.assertEqual([p.name for p in session.pending_briefs(pending)],
                         ["2026-07-23-a-brief.md"],
                         "the lane must see the brief waiting in the main checkout")
        self.assertEqual(applied.resolve(), (main_pending.parent / "applied").resolve())

    def test_main_checkout_resolution_is_unchanged(self):
        """The shared root IS ROOT in the main checkout, so nothing about the existing
        (working) behaviour moves."""
        main_pending = self._make_main_inbox()
        session.ROOT = self.main
        session.CFG = {"inbox": main_pending}
        pending, _ = session.inbox_dirs()
        self.assertEqual(pending, main_pending)

    def test_configured_but_missing_inbox_is_not_reported_as_empty(self):
        """An unreadable inbox must be distinguishable from an empty one — reporting
        'empty' over an inbox nobody could read is what kept this invisible."""
        session.ROOT = self.main
        session.CFG = {"inbox": self.main / "proposed-edits" / "nope" / "pending"}
        pending, _ = session.inbox_dirs()
        self.assertFalse(session.inbox_visible(pending))
        self.assertEqual(session.pending_briefs(pending), [])

    def test_unconfigured_inbox_stays_a_silent_noop(self):
        """A fresh Architect declares no inbox; that is not a fault and must stay quiet."""
        session.ROOT = self.main
        session.CFG = {"inbox": None}
        pending, applied = session.inbox_dirs()
        self.assertIsNone(pending)
        self.assertIsNone(applied)
        self.assertFalse(session.inbox_visible(pending))

    # --- WI-0027: the BANNER line, which had no test and so kept the old resolution ---
    # `inbox_dirs()` was fixed in session 90 and covered above; the start banner went on
    # composing its line from `CFG["inbox"]` raw. Same session, two answers: the engine
    # read the brief, the banner announced UNREADABLE — and the banner is what a person
    # reads. These pin the four states against `_inbox_orientation_line()` directly.

    def test_the_banner_names_the_brief_the_engine_can_already_see(self):
        """The failing case operator reported from a member's lane: a brief sitting in the main
        checkout, announced as unreadable by the lane that could act on it."""
        self._make_main_inbox()
        self._point_session_at_lane()
        # As config load builds it: the configured path, relative to the LANE.
        session.CFG["inbox"] = self.lane / "proposed-edits" / "test-arch" / "pending"
        self.assertFalse(session.CFG["inbox"].exists(), "gitignored — absent in the lane")

        line = session._inbox_orientation_line()

        self.assertIn("2026-07-23-a-brief.md", line,
                      "the banner must name what the lane's own apply-briefs can read")
        self.assertNotIn("UNREADABLE", line)

    def test_a_genuinely_absent_inbox_is_still_unreadable_not_empty(self):
        """The three-state honesty must survive the re-resolution — re-basing the path
        must not turn 'I cannot see it' back into 'there is nothing there'."""
        session.ROOT = self.main
        session.CFG = {"inbox": self.main / "proposed-edits" / "nope" / "pending"}
        line = session._inbox_orientation_line()
        self.assertIn("UNREADABLE", line)
        self.assertNotIn("(empty)", line)

    def test_a_readable_but_empty_inbox_reads_empty(self):
        """The designed normal state of a mailbox — distinct from unreadable."""
        pending = self.main / "proposed-edits" / "test-arch" / "pending"
        pending.mkdir(parents=True)
        session.ROOT = self.main
        session.CFG = {"inbox": pending}
        line = session._inbox_orientation_line()
        self.assertIn("(empty)", line)
        self.assertNotIn("UNREADABLE", line)

    def test_an_undeclared_inbox_stays_quiet_in_the_banner(self):
        session.ROOT = self.main
        session.CFG = {"inbox": None}
        self.assertEqual(session._inbox_orientation_line(), "inbox:   (none declared)")


class ReapLanesTest(WorktreeLaneBase):
    """ADR-0060 Q5 — the crash-safety reaper for orphaned poga worktree lanes.

    Reconstructs the four real orphan classes found in `.claude/worktrees/` (a live
    locked lane, a merged-branch orphan, an unmerged-branch orphan, and a branchless
    directory residue) and asserts the reaper reaps only what is safe and surfaces the
    rest — never touching a live lane, never discarding unmerged work."""

    def _wt_root(self):
        return self.main / ".claude" / "worktrees"

    def _use_main(self):
        """Drive session.py as if invoked from the MAIN checkout (where the reaper
        normally runs), with a minimal CFG so `_trunk()` resolves to main."""
        session.ROOT = self.main
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}

    def _build_orphans(self):
        wt = self._wt_root()
        # (1) LIVE lane: lock poga-1 with THIS process's pid — provably alive here.
        _git(self.main, "worktree", "lock", "--reason",
             f"claude session poga-1 (pid {os.getpid()})", str(self.lane))
        # (2) MERGED orphan: a `claude/<name>` branch at the trunk tip + a residue dir.
        _git(self.main, "branch", "claude/merged-orphan", "main")
        (wt / "merged-orphan").mkdir(parents=True)
        # (3) UNMERGED orphan: a branch carrying a commit NOT on the trunk + a residue dir.
        build = wt / "_build_unmerged"
        _git(self.main, "worktree", "add", "-q", "-b", "claude/unmerged-orphan", str(build), "main")
        (build / "u.txt").write_text("unlanded\n", encoding="utf-8")
        _git(build, "add", "-A")
        _git(build, "commit", "-qm", "unmerged work")
        _git(self.main, "worktree", "remove", "--force", str(build))
        (wt / "unmerged-orphan").mkdir()
        # (4) Branchless directory RESIDUE.
        (wt / "dir-residue").mkdir()

    def _age_orphans(self, minutes=LANE_AGED):
        """Backdate every lane fixture that exists NOW past LANE_REAP_MIN_AGE_MIN.

        Fixtures are seconds old, which is precisely the just-born sibling the guard
        exists to spare — and since WI-0257 that guard lives in `_scan_lanes`, so it
        binds the loud `reap-lanes` command too, not just the silent sweep. A test about
        any OTHER property therefore has to age its lanes first, or it is really
        exercising the birth-window guard by accident.

        Ages what is on disk at call time, so a test that wants one young lane beside
        aged ones just creates it after calling this."""
        old = time.time() - minutes * 60
        for child in self._wt_root().iterdir():
            if child.is_dir():
                os.utime(child, (old, old))

    def test_scan_classifies_the_four_orphan_classes(self):
        self._use_main()
        self._build_orphans()
        self._age_orphans()     # WI-0257: unaged, every one of these reads 'young'
        lanes = {l["name"]: l for l in session._scan_lanes()}
        self.assertEqual(lanes["poga-1"]["status"], "live", "locked+alive lane is protected")
        self.assertEqual(lanes["merged-orphan"]["status"], "reap")
        self.assertEqual(lanes["merged-orphan"]["branch"], "claude/merged-orphan")
        self.assertEqual(lanes["unmerged-orphan"]["status"], "unmerged")
        self.assertEqual(lanes["dir-residue"]["status"], "reap")
        self.assertIsNone(lanes["dir-residue"]["branch"], "no branch — pure dir residue")

    def test_dead_pid_lock_is_an_orphan_not_live(self):
        """A crashed lane keeps its lock, but the pid is dead — the crash class. It must
        classify as an orphan (here: reap, since poga-1's branch is at the trunk tip),
        NOT as live. Guards against 'locked == live' mistaking a crash for a live lane."""
        self._use_main()
        # Lock poga-1 with a pid that cannot be alive (0 is never a real user process).
        _git(self.main, "worktree", "lock", "--reason",
             "claude session poga-1 (pid 2147483646)", str(self.lane))
        self._age_orphans()     # WI-0257: the question here is the LOCK, not the age
        lanes = {l["name"]: l for l in session._scan_lanes()}
        self.assertEqual(lanes["poga-1"]["status"], "reap",
                         "a dead-pid lock is the crash class, not a live lane")

    def test_reap_removes_merged_and_residue_keeps_unmerged_and_live(self):
        self._use_main()
        self._build_orphans()
        self._age_orphans()     # WI-0257: the loud path spares a newborn too
        wt = self._wt_root()
        session.cmd_reap_lanes(argparse.Namespace(dry_run=False))
        # Merged orphan: dir gone, merged branch deleted.
        self.assertFalse((wt / "merged-orphan").exists(), "merged orphan dir reaped")
        self.assertNotIn("claude/merged-orphan", _out(self.main, "branch", "--format=%(refname:short)"))
        # Dir residue: gone.
        self.assertFalse((wt / "dir-residue").exists(), "residue dir reaped")
        # Unmerged orphan: dir AND branch fully intact — never auto-discarded.
        self.assertTrue((wt / "unmerged-orphan").exists(), "unmerged dir kept")
        self.assertIn("claude/unmerged-orphan", _out(self.main, "branch", "--format=%(refname:short)"))
        # Live lane poga-1: worktree still registered, branch intact.
        self.assertTrue((wt / "poga-1").exists(), "live lane untouched")
        self.assertIn(str(self.lane), _out(self.main, "worktree", "list"))
        self.assertIn("worktree-poga-1", _out(self.main, "branch", "--format=%(refname:short)"))

    # --- WI-0038: the reaper reported attempts as successes -----------------------
    # Filed session ~102, hit again ~111 with byte-identical output in BOTH directions:
    # one run printed `reaped 2` having failed every operation, the next printed `reaped 2`
    # having actually worked. A summary that cannot express its own failure is the defect;
    # the recurrence is what makes the fix structural rather than a counter tweak.

    def _reap_output(self, dry_run=False):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_reap_lanes(argparse.Namespace(dry_run=dry_run))
        return buf.getvalue()

    def test_a_dead_pid_lock_no_longer_blocks_the_reap(self):
        """Half two. `_scan_lanes` already classified this as the crash class, but git
        refuses to remove a LOCKED worktree even with --force — so the reap failed and a
        human had to `git worktree unlock` by hand, forever, on a lane nothing was using.
        Classifying it reapable and then failing to reap it is the worst of both."""
        self._use_main()
        _git(self.main, "worktree", "lock", "--reason",
             "claude session poga-1 (pid 2147483646)", str(self.lane))
        self._age_orphans()     # WI-0257: the question here is the LOCK, not the age
        out = self._reap_output()
        self.assertFalse((self._wt_root() / "poga-1").exists(),
                         "a corpse's lock must not make a lane un-reapable forever")
        self.assertIn("unlocked", out, "say that the lock was cleared, and whose it was")
        self.assertIn("reaped 1 of 1", out)

    def test_a_live_lanes_lock_is_never_touched(self):
        """The unlock must only ever reach corpses. A lock held by a LIVE pid classifies
        the lane 'live', so it never reaches the reaper at all."""
        self._use_main()
        self._build_orphans()          # locks poga-1 with THIS process's pid
        out = self._reap_output()
        self.assertNotIn("unlocked poga-1", out)
        self.assertTrue((self._wt_root() / "poga-1").exists())
        self.assertIn(str(self.lane), _out(self.main, "worktree", "list"))

    def test_the_summary_counts_successes_not_attempts(self):
        self._use_main()
        self._build_orphans()
        self._age_orphans()
        out = self._reap_output()
        self.assertIn("reaped 2 of 2", out, "both orphans genuinely went")
        self.assertNotIn("FAILED to reap", out)

    def test_a_failed_reap_is_reported_and_never_counted_as_success(self):
        """The half that was actually broken: with every operation failing, the old line
        printed the same sentence as a clean run. Now the count is 0 and the failures are
        named, so the two runs can never again be confused for one another."""
        self._use_main()
        self._build_orphans()
        self._age_orphans()
        with mock.patch.object(session, "_reap_lane",
                               side_effect=lambda l, root: (False, [f"  FAILED {l['name']}"])):
            out = self._reap_output()
        self.assertIn("reaped 0 of 2", out)
        self.assertIn("FAILED to reap 2", out)
        self.assertIn("still present", out)

    def test_a_path_outside_the_lane_pool_is_a_failure_not_a_success(self):
        """The refusal guard returns ok=False, so a skipped lane cannot inflate the count
        either — 'refused to touch it' is not 'reaped it'."""
        self._use_main()
        lane = {"name": "elsewhere", "path": (self.main / "not-a-lane").resolve(),
                "branch": None, "status": "reap", "registered": False,
                "is_self": False, "age_min": 999, "locked": False, "lock_pid": None}
        ok, lines = session._reap_lane(lane, self._wt_root())
        self.assertFalse(ok)
        self.assertIn("refusing to touch", lines[0])

    def test_dry_run_reaps_nothing(self):
        self._use_main()
        self._build_orphans()
        wt = self._wt_root()
        session.cmd_reap_lanes(argparse.Namespace(dry_run=True))
        for d in ("merged-orphan", "dir-residue", "unmerged-orphan", "poga-1"):
            self.assertTrue((wt / d).exists(), f"dry-run must not touch {d}")
        self.assertIn("claude/merged-orphan", _out(self.main, "branch", "--format=%(refname:short)"))

    # --- WI-0291: `--dry-run` was not dry -----------------------------------------
    # `sweep_orphan_pads()` was called at the TOP of `cmd_reap_lanes`, above the guard
    # its two neighbours shared, so the one flag whose entire promise is "reap nothing"
    # deleted pads and reported it in the past tense. There was no test on this path at
    # all — which is how the call drifted outside the guard and stayed there — so these
    # pin BOTH halves: the dry run leaves the pad and still names it, and the real run
    # removes it. Testing only the dry half would leave "report nothing, ever" passing.

    def _build_pads(self):
        """A pad pool beside the checkout: one pad for the LIVE lane poga-1, one for a
        lane that no longer exists. Mirrors the real layout — `pad_dir` is repo-relative
        and pads are siblings of the main pad, named `<pad>-<lane>`."""
        session.CFG["pad_dir"] = "../architect"
        pads = self.tmp
        (pads / "architect").mkdir()
        for name in ("architect-poga-1", "architect-ghost"):
            (pads / name).mkdir()
            (pads / name / "CLAUDE.md").write_text("pad\n", encoding="utf-8")
        return pads

    def test_dry_run_leaves_an_orphan_pad_on_disk_and_still_names_it(self):
        """The defect itself. `--dry-run` must not remove the pad — and must not go
        silent about it either: an operator reaching for the flag because they do not
        trust the sweep needs the COMPLETE list of what a real run would do."""
        self._use_main()
        pads = self._build_pads()
        out = self._reap_output(dry_run=True)
        self.assertTrue((pads / "architect-ghost").is_dir(),
                        "--dry-run removed an orphan pad — the flag promises to reap nothing")
        self.assertIn("would remove orphaned lane pad architect-ghost", out)
        self.assertNotIn("removed orphaned lane pad architect-ghost", out.replace("would remove", ""),
                         "the dry run must never report the removal in the past tense")

    def test_the_real_run_removes_the_orphan_pad_and_reports_it(self):
        """The other half of the contract: gating the sweep must not disable it."""
        self._use_main()
        pads = self._build_pads()
        out = self._reap_output(dry_run=False)
        self.assertFalse((pads / "architect-ghost").exists(), "the real run removes the pad")
        self.assertIn("removed orphaned lane pad architect-ghost", out)

    def test_neither_run_touches_the_pad_of_a_lane_that_still_exists(self):
        """Both branches read the same predicate, so a live lane's pad is out of scope of
        both. If these ever diverge, the dry run is reporting a set the real run would not
        act on — which is the same lie as the one being fixed, pointed the other way."""
        self._use_main()
        pads = self._build_pads()
        dry = self._reap_output(dry_run=True)
        self.assertTrue((pads / "architect-poga-1").is_dir())
        self.assertNotIn("architect-poga-1", dry)
        self._reap_output(dry_run=False)
        self.assertTrue((pads / "architect-poga-1").is_dir(),
                        "poga-1's worktree is right there — its pad is not an orphan")

    def test_the_silent_start_time_sweep_still_removes_pads_unconditionally(self):
        """`_auto_reap_lanes` has no dry-run mode and never had the defect. Gating the
        COMMAND must not gate the automatic path, whose whole job is to clear this litter
        without anyone asking."""
        self._use_main()
        pads = self._build_pads()
        session._auto_reap_lanes()
        self.assertFalse((pads / "architect-ghost").exists(), "the auto sweep still sweeps")
        self.assertTrue((pads / "architect-poga-1").is_dir())

    def test_a_pad_that_could_not_be_removed_is_not_reported_as_removed(self):
        """`rmtree(ignore_errors=True)` swallows the failure, so the removal line was
        printed from the ATTEMPT, not the outcome — the WI-0038 shape. The line now
        follows the pad actually being gone."""
        self._use_main()
        pads = self._build_pads()
        with mock.patch.object(session.shutil, "rmtree", lambda *a, **k: None):
            out = self._reap_output(dry_run=False)
        self.assertTrue((pads / "architect-ghost").is_dir(), "the fixture must leave it there")
        self.assertNotIn("removed orphaned lane pad", out,
                         "nothing was removed, so nothing may say it was")

    def test_auto_reap_sweeps_safe_orphans_from_main_keeps_the_rest(self):
        """ADR-0060 Q5 auto-wire: the silent start-time sweep reaps the SAFE subset
        (merged branch / dir residue) and leaves live + unmerged lanes fully intact —
        the same safety envelope as the loud command, minus the surfacing."""
        self._use_main()
        self._build_orphans()
        self._age_orphans()
        wt = self._wt_root()
        session._auto_reap_lanes()
        self.assertFalse((wt / "merged-orphan").exists(), "merged orphan swept")
        self.assertFalse((wt / "dir-residue").exists(), "residue dir swept")
        self.assertTrue((wt / "unmerged-orphan").exists(), "unmerged kept — never auto-discarded")
        self.assertIn("claude/unmerged-orphan", _out(self.main, "branch", "--format=%(refname:short)"))
        self.assertTrue((wt / "poga-1").exists(), "live lane untouched")

    def test_auto_reap_runs_from_inside_a_lane(self):
        """Session 90 — the reaper used to no-op unless run from the MAIN checkout, which
        made it dead code once ADR-0059's detect-and-refuse steered every session into a
        lane: `worktree-poga-3` sat merged and reapable for a full day while the reaper
        'ran' at every start. Any session sweeps now; the birth race it was guarding is
        handled by age instead (see the next test)."""
        self._use_main()
        self._build_orphans()
        self._age_orphans()
        wt = self._wt_root()
        with mock.patch.object(session, "_on_worktree_lane", return_value=True):
            session._auto_reap_lanes()
        self.assertFalse((wt / "merged-orphan").exists(), "a lane sweeps merged orphans")
        self.assertFalse((wt / "dir-residue").exists(), "a lane sweeps dir residue")
        self.assertTrue((wt / "unmerged-orphan").exists(), "unmerged still never discarded")

    def test_auto_reap_spares_a_just_born_sibling_lane(self):
        """The hazard the main-checkout gate used to stand in for, now handled directly:
        a lane created seconds ago has not taken its lock yet, and its branch — freshly
        cut from the trunk — is trivially merged, so it classifies 'reap'. Age is what
        keeps the sweep off it, and age is a property of the lane rather than of whoever
        is sweeping, so it holds from every surface."""
        self._use_main()
        self._build_orphans()
        self._age_orphans()
        wt = self._wt_root()
        # A brand-new sibling: merged branch at the trunk tip, no lock, mtime = now.
        _git(self.main, "branch", "claude/newborn", "main")
        (wt / "newborn").mkdir()

        lanes = {l["name"]: l for l in session._scan_lanes()}
        self.assertEqual(lanes["newborn"]["status"], "young",
                         "WI-0257: age is part of the CLASSIFICATION now, not a filter "
                         "each sweep has to remember to apply")

        session._auto_reap_lanes()
        self.assertTrue((wt / "newborn").exists(), "just-born sibling spared")
        self.assertIn("claude/newborn", _out(self.main, "branch", "--format=%(refname:short)"))
        self.assertFalse((wt / "merged-orphan").exists(), "aged orphans still swept")

    # --- WI-0257: the age guard was on the AUTO path only ------------------------
    # `_auto_reap_lanes` filtered on `_lane_too_young`; `cmd_reap_lanes` had no age term
    # anywhere, and `_auto_reap_lanes`'s own docstring asserted the opposite — that the
    # birth race was "handled where it belongs, by LANE_REAP_MIN_AGE_MIN in `_scan_lanes`,
    # which excludes a young lane structurally". `_scan_lanes` excluded nothing; it only
    # RECORDED `age_min`. So the loud operator-invoked sweep, and `discard-phantom-lanes`,
    # inherited none of it. A lane in its birth window is the WORST case to get wrong: the
    # directory exists, the branch is freshly cut from the trunk so trivially merged, the
    # tree is clean because nothing has run, and the child has not locked yet — every
    # predicate answers "safe" about a lane that is still being born.

    def _newborn(self):
        """An aged pool plus one brand-new sibling: merged branch, no lock, mtime = now."""
        self._build_orphans()
        self._age_orphans()
        _git(self.main, "branch", "claude/newborn", "main")
        (self._wt_root() / "newborn").mkdir()

    def test_the_loud_reap_command_spares_a_just_born_lane(self):
        """THE DEFECT. `reap-lanes` would `worktree remove` a sibling mid-birth — and
        succeed, because the tree is clean. The aged orphans must still go, so this is the
        guard working rather than the sweep being broken."""
        self._use_main()
        self._newborn()
        wt = self._wt_root()
        self._reap_output()
        self.assertTrue((wt / "newborn").exists(), "loud sweep must spare a newborn too")
        self.assertIn("claude/newborn", _out(self.main, "branch", "--format=%(refname:short)"))
        self.assertFalse((wt / "merged-orphan").exists(), "aged orphans still reaped")

    def test_a_held_back_newborn_is_named_not_silently_dropped(self):
        """WI-0082's lesson, reapplied: an operator seeing a lane simply ABSENT from the
        report cannot tell "reaped" from "spared" from "never seen". Dropping young lanes
        out of the reapable list without saying so would be that defect in a safer coat."""
        self._use_main()
        self._newborn()
        out = self._reap_output()
        self.assertIn("newborn", out, "the held-back lane is named")
        self.assertIn("birth window", out, "and the reason is given")
        self.assertIn("--include-young", out, "and a route past it, for someone who knows")
        self.assertIn("held 1 as too young", out, "and the summary counts it")

    def test_include_young_is_the_opt_out(self):
        """The safe behaviour is the DEFAULT and the escape is an opt-out — not a guard
        the operator must remember to ask for. An operator who watched the session die
        should not have to wait out the window."""
        self._use_main()
        self._newborn()
        wt = self._wt_root()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_reap_lanes(argparse.Namespace(dry_run=False, include_young=True))
        self.assertFalse((wt / "newborn").exists(), "--include-young reaps it")
        self.assertIn("--include-young", buf.getvalue(), "and says that it did")

    def test_no_sweep_can_see_a_young_lane_as_reapable(self):
        """The structural invariant, and the reason `_auto_reap_lanes` no longer carries
        an age test of its own: 'reap' MEANS old enough. Duplicating the check at each
        consumer is exactly how the two paths drifted apart, so it is not duplicated back
        — which makes this pin the thing holding the auto path safe."""
        self._use_main()
        self._newborn()
        # Compared against the CONSTANT, never through `_lane_too_young`. Asserting via
        # the function under question makes the pin vacuous exactly when it is needed:
        # neutering `_lane_too_young` to reproduce the old behaviour also neuters the
        # assertion, and this test passed against the unfixed code until that showed up
        # in the probe.
        seen = {l["name"]: l["status"] for l in session._scan_lanes()}
        self.assertEqual(seen["newborn"], "young", "premise: the newborn is in-window")
        for l in session._scan_lanes():
            if l["status"] == "reap":
                self.assertIsNotNone(l["age_min"], f"{l['name']}: unknown age reads young")
                self.assertGreaterEqual(
                    l["age_min"], session.LANE_REAP_MIN_AGE_MIN,
                    f"{l['name']} classified reapable while inside the birth window")

    def test_discard_phantom_lanes_refuses_a_newborn_by_name(self):
        """`discard-phantom-lanes` never reached a newborn either — but only by accident:
        a lane with no commits ahead and a clean tree makes all four phantom predicates
        answer False, so it fell out of BOTH lists, unmentioned. Correct outcome, no
        reasoning behind it, and nothing said. Now it is refused for the actual reason."""
        self._use_main()
        self._newborn()
        wt = self._wt_root()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_discard_phantom_lanes(argparse.Namespace(dry_run=False))
        out = buf.getvalue()
        self.assertTrue((wt / "newborn").exists(), "a newborn is never discarded")
        self.assertIn("refused: newborn", out, "and it is refused BY NAME")
        self.assertIn("birth window", out, "for the age reason, not by predicate luck")

    def test_unknown_age_is_treated_as_young(self):
        """Refuse to reap what we cannot prove is stale."""
        self.assertTrue(session._lane_too_young({"age_min": None}))
        self.assertTrue(session._lane_too_young({}))
        self.assertFalse(session._lane_too_young({"age_min": LANE_AGED}))


# --- WI-0082: 'merged' answers a question about COMMITS, not about WORK ---------
# The classification was purely `merge-base --is-ancestor branch trunk`, with no
# dirty-tree check anywhere, and `_reap_lane` removed with `git worktree remove
# --force` — precisely the flag that overrides git's refusal to remove a worktree
# holding uncommitted changes. So a lane whose process was gone, whose branch was
# merged, and which held uncommitted edits had them DELETED by an unrelated
# session's startup, silently, after LANE_REAP_MIN_AGE_MIN.
#
# The sibling teardown path already had this right and said so: the WorktreeRemove
# hook omits --force deliberately (test_hook_keeps_a_dirty_lane_and_its_branch),
# and its docstring delegates the leftover to "the reaper" — which force-removed
# it. Two paths, opposite policies, the safe one citing the unsafe one as its net.
#
# Why no test caught it: the hook test reasons about an UNMERGED lane, where the
# branch survives with the work on it. The lethal combination is a MERGED branch
# plus a dirty tree — there is no commit to survive on — and that configuration
# had no test at all. `verify-in-the-created-configuration`, exactly.
class DirtyLaneIsNeverAutoReapedTest(WorktreeLaneBase):
    """poga-1 is registered, unlocked, and its branch sits at the trunk tip — so it
    classifies `reap` on the old branch-only rule. Add uncommitted work and age it,
    and the automatic sweep is the thing standing between that work and deletion."""

    def _wt_root(self):
        return self.main / ".claude" / "worktrees"

    def _use_main(self):
        session.ROOT = self.main
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}

    def _dirty_and_age_the_lane(self, name="in-flight.txt", body="uncommitted work\n"):
        """The exact lethal configuration: merged branch + dirty tree + aged + unlocked."""
        (self.lane / name).write_text(body, encoding="utf-8")
        old = time.time() - LANE_AGED * 60
        os.utime(self.lane, (old, old))
        return self.lane / name

    def test_a_merged_lane_holding_uncommitted_work_classifies_dirty_not_reap(self):
        self._use_main()
        self._dirty_and_age_the_lane()
        lane = {l["name"]: l for l in session._scan_lanes()}["poga-1"]
        self.assertEqual(lane["status"], "dirty",
                         "a merged branch says nothing about the working tree")
        self.assertTrue(lane["dirty"], "the dirty flag is carried, not just the status")

    def test_the_silent_sweep_never_touches_it_and_the_work_survives(self):
        """THE REGRESSION. Against the pre-fix code this fails: the lane is force-removed
        and the file is gone."""
        self._use_main()
        work = self._dirty_and_age_the_lane()
        session._auto_reap_lanes()
        self.assertTrue(self.lane.exists(), "a dirty lane is never silently reaped")
        self.assertTrue(work.exists(), "uncommitted work survives the sweep")
        self.assertEqual(work.read_text(encoding="utf-8"), "uncommitted work\n",
                         "and survives intact, not merely present")
        self.assertIn(str(self.lane), _out(self.main, "worktree", "list"),
                      "the worktree registration is left alone")

    def test_the_loud_command_names_it_instead_of_silently_skipping(self):
        """Surfacing is the whole point: `declare-what-a-check-assumes` — 'held back
        because it has uncommitted work' must not read the same as 'nothing to do'."""
        self._use_main()
        self._dirty_and_age_the_lane()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            session.cmd_reap_lanes(argparse.Namespace(dry_run=False))
        printed = out.getvalue()
        self.assertIn("poga-1", printed, "the held-back lane is named")
        self.assertIn("uncommitted", printed.lower(),
                      "and the REASON is stated, not just the name")
        self.assertTrue(self.lane.exists(), "naming it does not reap it")

    def test_a_clean_merged_lane_is_still_reaped(self):
        """The guard must not turn the reaper off. A genuinely clean merged orphan is
        exactly what the sweep exists for and still goes."""
        self._use_main()
        old = time.time() - LANE_AGED * 60
        os.utime(self.lane, (old, old))
        self.assertEqual({l["name"]: l for l in session._scan_lanes()}["poga-1"]["status"],
                         "reap", "clean + merged is still reapable")
        session._auto_reap_lanes()
        self.assertFalse(self.lane.exists(), "the clean lane is still swept")

    def test_an_untracked_file_alone_is_enough_to_hold_it(self):
        """`git status --porcelain` reports untracked files, and it must — the original
        loss was uncommitted edits, some of them new files that had never been
        added. A guard that only saw MODIFIED tracked files would have missed it."""
        self._use_main()
        self._stage_lane_work()          # give the lane a commit, so tracked state is clean
        _git(self.main, "merge", "-q", "--ff-only", "worktree-poga-1")
        stray = self.lane / "never-added.txt"
        stray.write_text("brand new\n", encoding="utf-8")
        old = time.time() - LANE_AGED * 60
        os.utime(self.lane, (old, old))
        lane = {l["name"]: l for l in session._scan_lanes()}["poga-1"]
        self.assertEqual(lane["status"], "dirty", "an untracked file is uncommitted work")
        session._auto_reap_lanes()
        self.assertTrue(stray.exists(), "the never-added file survives")

    def test_an_unreadable_tree_is_treated_as_dirty(self):
        """Cautious direction: if `git status` cannot answer, we do not get to assume
        the tree is clean — the whole defect was an unasked question read as a 'no'."""
        self._use_main()
        old = time.time() - LANE_AGED * 60
        os.utime(self.lane, (old, old))
        with mock.patch.object(session, "_lane_is_dirty", return_value=True):
            lane = {l["name"]: l for l in session._scan_lanes()}["poga-1"]
            self.assertEqual(lane["status"], "dirty")


# --- ADR-0089 / WI-0083: no keystroke outside git for longer than one session stop ---
class LaneStopCheckpointTest(WorktreeLaneBase):
    """D1: a lane that stops with uncommitted work commits it to its own private branch.
    D2: that commit dissolves on resume and is absorbed at land — it never reaches trunk.
    """

    def _fire_record_end(self, sid="sess-1", reason="clear"):
        """Drive cmd_record_end as SessionEnd does: hook JSON on stdin. Always exits 0."""
        buf = io.StringIO(json.dumps({"session_id": sid, "reason": reason}))
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(sys, "stdin", buf):
                with self.assertRaises(SystemExit) as e:
                    session.cmd_record_end(argparse.Namespace())
        self.assertEqual(e.exception.code, 0, "record-end must always fail open")

    def _head_subject(self, repo=None):
        return _out(repo or self.lane, "log", "-1", "--format=%s")

    def test_a_clean_stop_checkpoints_uncommitted_lane_work(self):
        """ACCEPTANCE 1 (clean half): after the stop, zero uncommitted files remain."""
        self._point_session_at_lane()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self._fire_record_end()
        self.assertTrue(self._head_subject().startswith(session.CHECKPOINT_PREFIX),
                        "the stop left an identifiable checkpoint commit")
        self.assertEqual(_out(self.lane, "status", "--porcelain"), "",
                         "nothing is left uncommitted in the lane")

    def test_an_untracked_file_is_checkpointed_too(self):
        """The original loss included files never `git add`ed — staging only tracked
        changes would have preserved exactly the wrong half."""
        self._point_session_at_lane()
        (self.lane / "never-added.txt").write_text("new\n", encoding="utf-8")
        self._fire_record_end()
        self.assertIn("never-added.txt", _files_on(self.lane, "HEAD"))

    def test_bytecode_is_never_checkpointed(self):
        """WI-0461. The fixture's `.gitignore` has no bytecode rule, like a newcomer
        project's did: the checkpoint must drop `.pyc` anyway, at any depth, while still
        taking the untracked work beside it."""
        self._point_session_at_lane()
        (self.lane / "in-flight.py").write_text("x = 1\n", encoding="utf-8")
        (self.lane / "pkg" / "__pycache__").mkdir(parents=True)
        (self.lane / "pkg" / "__pycache__" / "m.cpython-39.pyc").write_bytes(b"\x00")
        (self.lane / "__pycache__").mkdir()
        (self.lane / "__pycache__" / "top.cpython-39.pyc").write_bytes(b"\x00")
        (self.lane / "stray.pyc").write_bytes(b"\x00")
        self._fire_record_end()
        files = _files_on(self.lane, "HEAD")
        self.assertIn("in-flight.py", files)
        self.assertEqual([f for f in files if f.endswith(".pyc") or "__pycache__" in f], [],
                         f"no bytecode in the checkpoint: {sorted(files)}")

    def test_a_clean_lane_stop_writes_no_commit(self):
        """No work, no checkpoint — the mechanism must not manufacture empty commits."""
        self._point_session_at_lane()
        before = _out(self.lane, "rev-parse", "HEAD")
        self._fire_record_end()
        self.assertEqual(_out(self.lane, "rev-parse", "HEAD"), before)

    def test_the_ended_marker_is_written_even_if_the_checkpoint_cannot_be(self):
        """The `.ended` marker is the clean-vs-crash discriminator the whole orphan model
        rests on. It must land first and survive a failing checkpoint."""
        self._point_session_at_lane()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        with mock.patch.object(session, "_checkpoint_lane_work",
                               side_effect=RuntimeError("boom")):
            self._fire_record_end(sid="sess-9")
        self.assertTrue((self.lane / ".session-state" / "sess-9.ended").is_file(),
                        "the ended marker survives a failed checkpoint")

    def test_the_main_checkout_is_never_checkpointed(self):
        """Lanes only. Committing a shared tree at stop would write work other sessions
        are holding — the interactive-close guard owns that case."""
        session.ROOT = self.main
        session.SESSION_STATE_DIR = self.main / ".session-state"
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}
        (self.main / "untidy.txt").write_text("dirty main\n", encoding="utf-8")
        before = _out(self.main, "rev-parse", "HEAD")
        self._fire_record_end(sid="sess-main")
        self.assertEqual(_out(self.main, "rev-parse", "HEAD"), before,
                         "no commit was made in the main checkout")
        self.assertIn("untidy.txt", _out(self.main, "status", "--porcelain"))

    def test_resume_dissolves_the_checkpoint_and_restores_the_working_tree(self):
        """D2 first half: the safety net covers stop→resume, then gets out of the way."""
        self._point_session_at_lane()
        self._stage_lane_work()                     # give the branch a parent commit
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self._fire_record_end()
        self.assertTrue(self._head_subject().startswith(session.CHECKPOINT_PREFIX))

        self.assertTrue(session._dissolve_lane_checkpoint())
        self.assertFalse(self._head_subject().startswith(session.CHECKPOINT_PREFIX),
                         "the checkpoint commit is gone")
        self.assertEqual((self.lane / "in-flight.txt").read_text(encoding="utf-8"),
                         "mid-edit\n", "the file is back exactly as it was")
        self.assertIn("in-flight.txt", _out(self.lane, "status", "--porcelain"),
                      "and it is uncommitted again, as the operator left it")

    def test_dissolve_refuses_a_commit_it_did_not_write(self):
        """It moves a commit off the branch, so it must only ever move one of ours."""
        self._point_session_at_lane()
        self._stage_lane_work()
        before = _out(self.lane, "rev-parse", "HEAD")
        self.assertFalse(session._dissolve_lane_checkpoint())
        self.assertEqual(_out(self.lane, "rev-parse", "HEAD"), before)

    def test_dissolve_refuses_when_the_checkpoint_is_the_only_commit(self):
        """No parent to reset onto. A lane whose whole history is one checkpoint is a
        case for a human, not for the substrate to unpick.

        Built directly rather than through `record-end`: on an UNBORN branch
        `rev-parse --abbrev-ref HEAD` errors, so `_on_worktree_lane()` reads False and
        record-end correctly declines to checkpoint at all — which means the drive-it-
        end-to-end route cannot reach this state, and pretending otherwise would be a
        test that passes for the wrong reason."""
        self._point_session_at_lane()
        _git(self.lane, "checkout", "-q", "--orphan", "worktree-orphan-lane")
        _git(self.lane, "rm", "-rq", "--cached", ".")
        (self.lane / "only.txt").write_text("sole\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm",
             f"{session.CHECKPOINT_PREFIX} session sess-1 stopped with uncommitted work")
        self.assertTrue(self._head_subject().startswith(session.CHECKPOINT_PREFIX))
        self.assertEqual(_out(self.lane, "rev-list", "--count", "HEAD"), "1")
        self.assertFalse(session._dissolve_lane_checkpoint(),
                         "refuses rather than destroying the branch's only commit")
        self.assertTrue(self._head_subject().startswith(session.CHECKPOINT_PREFIX),
                        "and the commit is still there")

    def test_a_checkpoint_never_reaches_the_trunk(self):
        """ACCEPTANCE 3, and the one wrong answer the brief names: a landed lane must
        leave no wip(checkpoint) commit on the trunk."""
        self._point_session_at_lane()
        self._stage_lane_work()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self._fire_record_end()
        self.assertTrue(self._head_subject().startswith(session.CHECKPOINT_PREFIX))

        with contextlib.redirect_stdout(io.StringIO()) as out:
            landed = session._land_worktree_lane(None, None, "v1.0.0",
                                                 commit_msg="close lane", push=False)
        self.assertTrue(landed, "the lane still lands")
        subjects = _out(self.main, "log", "--format=%s", "main").splitlines()
        self.assertFalse([s for s in subjects if s.startswith(session.CHECKPOINT_PREFIX)],
                         f"no checkpoint commit on the trunk: {subjects}")
        self.assertIn("absorbed", out.getvalue(),
                      "the absorption is reported, not silent")
        self.assertIn("in-flight.txt", _files_on(self.main, "main"),
                      "and the work itself landed")


class LandClosesAProvablyDeadJournalTest(WorktreeLaneBase):
    """WI-0110 — the fleet's single largest red on the fleet dashboard.

    A journal is TRACKED and rides the land onto the trunk. Its liveness evidence
    (`.session-state/<csid>.ended|.gone`) is GITIGNORED and per-worktree and does not.
    So a journal landed without being closed can never afterwards be PROVEN closeable
    from the trunk — the reaper matches by claude-session-id against `ROOT/.session-state`,
    which on main holds nothing for a session that lived in a lane — and it renders as an
    unstamped false close to every observer forever. The land is the last moment the
    proof exists, and the next step the banner recommends destroys it.
    """

    def _dead_journal(self, did="20260808T1200Z-runner-8929", csid="dead-csid"):
        """An OPEN journal in the lane plus a clean-exit marker for its session."""
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": "(in progress)",
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": "2026-08-08T10:14:00+00:00", "ended": "",
            "claude-session-id": csid,
        })
        # Real narrative, so the reaper closes it rather than deleting it as a phantom.
        p = session.JOURNAL_DIR / f"{did}.md"
        p.write_text(p.read_text(encoding="utf-8")
                     .replace("- Session opened.", "- Did real work that must survive."),
                     encoding="utf-8")
        session.SESSION_STATE_DIR.mkdir(parents=True, exist_ok=True)
        (session.SESSION_STATE_DIR / f"{csid}.ended").write_text(
            json.dumps({"ended": "2026-08-08T10:55:00+00:00"}), encoding="utf-8")
        return did

    def test_a_dead_lane_journal_is_closed_at_land_not_left_unstamped(self):
        self._point_session_at_lane()
        self._install_main_compiler()
        did = self._dead_journal()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            landed = session._land_worktree_lane(None, None, "v1.0.0",
                                                 commit_msg="close lane", push=False)
        self.assertTrue(landed, "the lane still lands")
        self.assertIn("closed:", out.getvalue(), "the close is reported, not silent")
        on_trunk = _out(self.main, "show", f"main:sessions/journal/{did}.md")
        self.assertNotRegex(on_trunk, r"(?m)^ended:\s*$",
                            "the journal reaching the trunk carries a real end stamp")
        self.assertIn("Did real work that must survive.", on_trunk,
                      "and its narrative is intact")

    def test_a_live_sessions_own_journal_is_left_open_by_a_mid_flight_land(self):
        """The inverse, and the one that matters: `merge` is also the retry path for a
        session that is still running. Closing that journal would stamp a false end."""
        self._point_session_at_lane()
        self._install_main_compiler()
        did = self._dead_journal(did="20260808T1600Z-runner-live", csid="live-csid")
        # Same session, still alive: a fresh beat and NO exit marker.
        (session.SESSION_STATE_DIR / "live-csid.ended").unlink()
        (session.SESSION_STATE_DIR / "live-csid.live").write_text(
            json.dumps({"last_beat": datetime.datetime.now(
                ZoneInfo("UTC")).isoformat()}), encoding="utf-8")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "live-csid"}):
            landed = session._land_worktree_lane(None, None, "v1.0.0",
                                                 commit_msg="close lane", push=False)
        self.assertTrue(landed)
        on_trunk = _out(self.main, "show", f"main:sessions/journal/{did}.md")
        self.assertRegex(on_trunk, r"(?m)^ended:\s*$",
                         "a live session's journal must land still open")


def _stranded_names(lines: str, slot: str) -> list:
    """The `stranded:` lines reporting this lane, found by its ADDRESS.

    WI-0157: a lane is named by its session and addressed by its slot, so the slot moved
    out of the reading position into brackets. These tests care whether the lane is
    REPORTED, which is what this answers — and answering it by substring on the whole
    blob would match the routing half of some other lane's line."""
    return [l for l in lines.splitlines()
            if l.startswith("stranded:") and f"[{slot}]" in l]


class StrandedLaneOrientationTest(WorktreeLaneBase):
    """D3: the repo that owns a lane names it at session start — no external observer.
    A member's stranded work was found on day 8 by another member's Architect happening to look."""

    def _use_main(self):
        session.ROOT = self.main
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}

    def test_an_unmerged_lane_is_named_with_the_command_that_lands_it(self):
        self._use_main()
        (self.lane / "work.txt").write_text("unlanded\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "unlanded work")
        lines = "\n".join(session._stranded_lane_lines())
        self.assertIn("poga-1", lines)
        self.assertIn("NOT on main", lines)
        self.assertIn("session.py merge", lines, "the resolution is named, not implied")

    def test_a_dirty_lane_is_named_distinctly_from_an_unmerged_one(self):
        """Different resolutions, so folding them into one count would hide that."""
        self._use_main()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        lines = "\n".join(session._stranded_lane_lines())
        self.assertIn("UNCOMMITTED", lines)
        self.assertNotIn("NOT on main", lines)

    def test_this_sessions_own_lane_is_never_reported_as_stranded(self):
        """A live session's dirty tree is work in progress."""
        session.ROOT = self.lane
        session.CFG = {"trunk": "main", "tz": ZoneInfo("UTC")}
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self.assertEqual(session._stranded_lane_lines(), [])

    def test_silent_when_there_is_nothing_stranded(self):
        self._use_main()
        self.assertEqual(session._stranded_lane_lines(), [])

    def _beat(self, tree, csid="sess-1", age_min=0.0):
        """Give `tree` a `.live` sidecar with a heartbeat `age_min` minutes old."""
        state = tree / ".session-state"
        state.mkdir(parents=True, exist_ok=True)
        beat = (datetime.datetime.now(ZoneInfo("UTC"))
                - datetime.timedelta(minutes=age_min)).isoformat()
        (state / f"{csid}.live").write_text(
            json.dumps({"last_beat": beat, "started": beat}), encoding="utf-8")

    def test_a_lane_with_a_live_session_is_active_not_stranded(self):
        """Session ~129, operator reported that every new poga session raised false
        complaints. The exclusion was `is_self` only, so a session saw every OTHER
        running session as stranded litter. A sibling holds its own journal uncommitted
        for its whole life, so under parallel lanes this fired constantly."""
        self._use_main()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self._beat(self.lane)
        lines = "\n".join(session._stranded_lane_lines())
        self.assertIn("active:", lines)
        self.assertIn("poga-1", lines)
        self.assertFalse(_stranded_names(lines, "poga-1"),
                         "a live sibling must not be called stranded")

    def test_a_stale_heartbeat_does_not_buy_the_active_exemption(self):
        """The inverse direction — a beat old enough to prove nothing must leave the
        lane reported, or the fix would silence real stranding."""
        self._use_main()
        (self.lane / "in-flight.txt").write_text("mid-edit\n", encoding="utf-8")
        self._beat(self.lane, age_min=session.HEARTBEAT_STALE_MIN + 5)
        lines = "\n".join(session._stranded_lane_lines())
        self.assertTrue(_stranded_names(lines, "poga-1"),
                        f"the lane must still be reported stranded: {lines!r}")

    def test_a_lane_holding_only_an_empty_stub_is_not_stranded_work(self):
        """poga-5, session ~129: an abandoned `codex` lane whose only uncommitted file
        was a journal that never left the stub body. The reaper is allowed to delete a
        phantom journal outright, but cannot reach one that is UNTRACKED IN A LANE — so
        the one piece of debris it may discard is the one it never sees, and it nagged
        at every start instead."""
        self._use_main()
        jdir = self.lane / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "20260808T1210Z-runner-f958.md").write_text(
            session.render_journal({
                "session-id": "20260808T1210Z-runner-f958", "ordinal": 1,
                "title": "(in progress)", "machine": "Runner", "runtime": "codex",
                "role-doc-version": "v1.0.0", "base-commit": "abc1234",
                "started": "2026-08-08T10:14:56+00:00", "ended": "",
                "claude-session-id": "",
            }), encoding="utf-8")
        lines = "\n".join(session._stranded_lane_lines())
        self.assertIn("empty:", lines)
        self.assertFalse(_stranded_names(lines, "poga-1"))
        self.assertNotIn("resume or discard is your call", lines,
                         "an empty lane is not an obligation")

    def test_a_journal_with_real_narrative_is_still_stranded(self):
        """The load-bearing inverse: `empty` must key on the body actually being a stub,
        not merely on the file being a journal — or a crashed session's real notes would
        be dismissed as debris."""
        self._use_main()
        jdir = self.lane / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        text = session.render_journal({
            "session-id": "20260808T1600Z-runner-aaaa", "ordinal": 1,
            "title": "(in progress)", "machine": "Runner", "runtime": "",
            "role-doc-version": "v1.0.0", "base-commit": "abc1234",
            "started": "2026-08-08T16:00:00+00:00", "ended": "", "claude-session-id": "",
        }).replace("- Session opened.", "- Shipped the thing operator actually wanted.")
        (jdir / "20260808T1600Z-runner-aaaa.md").write_text(text, encoding="utf-8")
        lines = "\n".join(session._stranded_lane_lines())
        self.assertTrue(_stranded_names(lines, "poga-1"),
                        f"the lane must still be reported stranded: {lines!r}")
        self.assertNotIn("empty:", lines)

    def test_a_lane_dirty_with_a_non_journal_file_is_still_stranded(self):
        """Conservative in the direction that cannot lose work: any path that is not a
        stub journal disqualifies the lane."""
        self._use_main()
        jdir = self.lane / "sessions" / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "20260808T1210Z-runner-f958.md").write_text(
            session.render_journal({
                "session-id": "20260808T1210Z-runner-f958", "ordinal": 1,
                "title": "(in progress)", "machine": "Runner", "runtime": "",
                "role-doc-version": "v1.0.0", "base-commit": "abc1234",
                "started": "2026-08-08T10:14:56+00:00", "ended": "",
                "claude-session-id": "",
            }), encoding="utf-8")
        (self.lane / "real-work.txt").write_text("not debris\n", encoding="utf-8")
        lines = "\n".join(session._stranded_lane_lines())
        self.assertTrue(_stranded_names(lines, "poga-1"),
                        f"the lane must still be reported stranded: {lines!r}")

    def test_a_scan_failure_is_silent_not_fatal(self):
        """A start must never break on this."""
        self._use_main()
        with mock.patch.object(session, "_scan_lanes", side_effect=RuntimeError("boom")):
            self.assertEqual(session._stranded_lane_lines(), [])

    def test_the_lines_actually_reach_the_start_banner(self):
        """THE WIRING, not the composer. Every other test here calls
        `_stranded_lane_lines()` directly, so all of them would pass just as happily if
        the call were never added to `cmd_start` — which is exactly how
        `_auto_reap_lanes` sat gated to the main checkout as dead code for a day while
        its own tests stayed green (session 90). A capability nobody calls reads as
        absent, so the call site gets its own assertion."""
        self._point_session_at_lane()
        # A SIBLING lane holding unlanded commits — not this session's own.
        sibling = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(sibling), "main")
        (sibling / "stranded.txt").write_text("never landed\n", encoding="utf-8")
        _git(sibling, "add", "-A")
        _git(sibling, "commit", "-qm", "unlanded work")

        with contextlib.redirect_stdout(io.StringIO()) as out:
            session.cmd_start(argparse.Namespace(dry_run=True))
        banner = out.getvalue()

        self.assertIn("stranded:", banner, "the start banner carries the stranded lines")
        self.assertIn("poga-2", banner, "and names the lane")


class MainCheckoutSyncTest(WorktreeLaneBase):
    """Consultant F1 (session 90): the CAS advances refs/heads/main with no checkout, so
    the MAIN checkout keeps the pre-advance content while the ref moves ahead. Session 89
    left it 5 commits behind for ~4 hours, showing a phantom staged deletion of ~2,032
    lines — while the scheduled adopt-runner ran with WorkingDirectory set to that tree.

    The land path owns the fix because the land path caused it, but it may only write into
    a tree with provably nothing of its own to lose.
    """

    def _cas_forward(self):
        """Do what the land does: advance the trunk ref to the lane tip with NO checkout.
        Returns (parent, tip)."""
        parent = _out(self.main, "rev-parse", "main")
        tip = _out(self.lane, "rev-parse", "HEAD")
        _git(self.main, "update-ref", "refs/heads/main", tip, parent)
        return parent, tip

    def test_clean_main_checkout_is_fast_forwarded(self):
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        parent, tip = self._cas_forward()
        # Precondition: the ref moved but the tree did not — the defect signature. Note
        # `git status` is LOUD here (it reports the whole delta as a staged deletion),
        # which is exactly why "clean tree" cannot be the sync precondition.
        self.assertFalse((self.main / "landed.txt").exists())
        self.assertTrue(_out(self.main, "status", "--porcelain"),
                        "the phantom staged deletion is present — the F1 signature")

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("fast-forwarded", report)
        self.assertTrue((self.main / "landed.txt").exists(), "lane work materialized")
        self.assertEqual(_out(self.main, "status", "--porcelain"), "", "tree is clean")

    # --- WI-0040 / WI-0067: bailing out is not neutral ----------------------------
    # These two used to assert the checkout was LEFT ALONE when dirty. That looked
    # conservative and was the defect: leaving it alone leaves HEAD at `tip` with the
    # index and tree at `parent`, so git reports the whole advance as a STAGED REVERSAL
    # and the next ordinary `git commit` there commits a revert of the landed work.
    # WI-0040 is that firing on another session's journal — 49 lines of a concurrent
    # session's own checkpoint reverted, then `ended` stamped onto the reverted body so it
    # looked properly closed. Three occurrences in one session, all via sanctioned paths.

    def test_a_dirty_main_checkout_is_synced_and_keeps_its_own_work(self):
        """The common real case: main is dirty on files this land never touched (the
        work-item store, a scratch file). It must be brought forward AND keep its work —
        the old all-or-nothing rule abandoned it half-advanced instead."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        (self.main / "operators-wip.txt").write_text("do not lose me\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("fast-forwarded", report)
        self.assertIn("kept", report, "say that its own content was preserved")
        self.assertEqual((self.main / "operators-wip.txt").read_text(encoding="utf-8"),
                         "do not lose me\n", "uncommitted work survives untouched")
        self.assertTrue((self.main / "landed.txt").exists(), "and the land materialized")

    def test_no_phantom_reversal_is_left_behind(self):
        """The trap itself, stated as a property: after a sync, nothing in the checkout
        may look like a staged deletion of the landed work. That phantom is what turns a
        routine `git commit` into a revert of the session."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        (self.main / "operators-wip.txt").write_text("wip\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        session._sync_main_checkout("main", parent, tip)

        status = _out(self.main, "status", "--porcelain")
        self.assertNotIn("landed.txt", status,
                         "the landed file must not read as pending deletion")
        self.assertIn("operators-wip.txt", status, "only the checkout's own work stays pending")

    def test_staged_content_of_its_own_is_preserved_across_the_sync(self):
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        (self.main / "own.txt").write_text("own work\n", encoding="utf-8")
        _git(self.main, "add", "own.txt")
        parent, tip = self._cas_forward()

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("fast-forwarded", report)
        self.assertTrue((self.main / "own.txt").exists())
        self.assertIn("own.txt", _out(self.main, "diff", "--cached", "--name-only"),
                      "its staged content is still staged")
        self.assertTrue((self.main / "landed.txt").exists())

    def test_a_genuine_overlap_still_refuses_and_says_so(self):
        """The safety envelope is unchanged where it matters: when the checkout has local
        changes to a file this land also changed, nothing is overwritten."""
        self._point_session_at_lane()
        # The lane modifies a file that already exists on the trunk...
        contested = self.lane / "contested.txt"
        contested.write_text("lane version\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "lane edits contested")
        # ...and the main checkout has its own uncommitted edit to that same file.
        (self.main / "contested.txt").write_text("the operator's version\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("NOT synced", report)
        self.assertIn("overlap", report)
        self.assertEqual((self.main / "contested.txt").read_text(encoding="utf-8"),
                         "the operator's version\n", "never overwritten")
        banner = self.main / ".session-state" / "stale-checkout.txt"
        self.assertTrue(banner.is_file(), "divergence is surfaced, not silent")

    def test_live_session_in_main_checkout_defers(self):
        """A tree someone is actively working in is not ours to rewrite, even clean."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        parent, tip = self._cas_forward()
        state = self.main / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "csid-live.live").write_text(
            f'{{"last_beat": "{session._now_iso()}"}}', encoding="utf-8")

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("NOT synced", report)
        self.assertIn("live", report)
        self.assertFalse((self.main / "landed.txt").exists())

    def test_clean_exited_session_does_not_block_the_sync(self):
        """A `.live` with a matching `.ended` is a session that is GONE — it must not
        pin the checkout stale forever."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        parent, tip = self._cas_forward()
        state = self.main / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "csid-done.live").write_text(
            f'{{"last_beat": "{session._now_iso()}"}}', encoding="utf-8")
        (state / "csid-done.ended").write_text("{}", encoding="utf-8")

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("fast-forwarded", report)
        self.assertTrue((self.main / "landed.txt").exists())

    def test_landing_from_the_main_checkout_materialises_that_checkout(self):
        """CORRECTED BY WI-0359. This asserted the sync was a no-op when ROOT is the main
        checkout — "a non-lane close has no other tree to bring along". The premise is
        true and the conclusion was wrong: there is no OTHER tree, but the tree it is
        standing in is exactly the one that needs materialising, because the trunk advances
        by a CAS `update-ref` that moves HEAD and leaves the index and working tree behind.

        The failure: integrate run in the process checkout reports success with the ref
        level with origin and leaves tracked files stale — so the scheduled jobs keep
        running pre-merge code. The positive case is pinned in detail by
        `TheCheckoutYouAreStandingInIsMaterialisedTooTest` in test_main_materialization;
        what this keeps is the narrow claim that the own-tree path is no longer a silent
        no-op."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        parent, tip = self._cas_forward()
        session.ROOT = self.main.resolve()

        report = session._sync_main_checkout("main", parent, tip)

        self.assertNotEqual(report, "", "the own-tree case must report, not return silently")
        self.assertTrue((self.main / "landed.txt").exists(),
                        "the checkout being stood in was left stale — the Runner defect")

    def test_stale_banner_is_cleared_once_the_sync_succeeds(self):
        """A banner from a previous deferred land must not outlive the condition it
        described — a stale warning trains you to ignore warnings."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        state = self.main / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "stale-checkout.txt").write_text("old warning\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        session._sync_main_checkout("main", parent, tip)

        self.assertFalse((state / "stale-checkout.txt").exists())

    def test_a_checkout_left_behind_by_a_missed_sync_converges(self):
        """WI-0133, reported by a member's Architect — the compounding case.

        `read-tree -m -u <parent> <tip>` preserves whatever the checkout has of its own.
        Correct while the checkout sits at `parent`; but once one sync is missed the tree
        is OLDER than `parent`, the whole un-materialized gap reads as the checkout's own
        content, and every subsequent land carefully protects it. The repair mechanism
        becomes what holds the staleness in place, and clearing that member's took a human at
        a plain terminal.

        Two lands are the minimum that can show it: the first leaves the checkout behind,
        the second must merge from where the tree ACTUALLY is rather than from its own
        parent."""
        self._point_session_at_lane()
        # Land one — the checkout is deliberately NOT synced, which is the state a
        # refused/interrupted sync leaves behind.
        self._stage_lane_work("first.txt")
        parent1, tip1 = self._cas_forward()
        # Land two, from a checkout still materialized at the pre-land-one commit.
        self._stage_lane_work("second.txt")
        parent2, tip2 = self._cas_forward()

        report = session._sync_main_checkout("main", parent2, tip2)

        # BOTH lands' content must be present. Before the fix, `first.txt` read as the
        # checkout's own deletion and was preserved as absent — forever.
        self.assertTrue((self.main / "second.txt").exists(), report)
        self.assertTrue((self.main / "first.txt").exists(),
                        f"the missed land was preserved as absent: {report}")
        self.assertIn("behind the trunk", report)

    def test_a_checkout_at_the_parent_with_its_own_edits_is_untouched_by_the_base_search(self):
        """The healthy, common case must not change. It matches no commit exactly — local
        edits see to that — so the base search must fall back to `parent` rather than
        walking back and merging from somewhere older, which would resurrect content the
        checkout deliberately changed."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        (self.main / "mine.txt").write_text("my own work\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        base, note = session._checkout_sync_base(self.main, parent)

        self.assertEqual(base, parent)
        self.assertEqual(note, "")

    def test_the_banner_is_cleared_even_when_the_checkout_kept_its_own_content(self):
        """WI-0134 — the OTHER success path, which is the common one.

        A sync that fast-forwards while preserving the checkout's own untracked content
        has succeeded just as completely as a pristine one, but the unlink used to sit
        after that path's early return, so the banner survived the repair and went on
        asserting a failure that was over: a converged checkout could carry the marker
        while its index matched HEAD, and every such instance is false. The pristine sibling above passes either way, which is
        exactly why this went unseen."""
        self._point_session_at_lane()
        self._stage_lane_work("landed.txt")
        state = self.main / ".session-state"
        state.mkdir(exist_ok=True)
        (state / "stale-checkout.txt").write_text("old warning\n", encoding="utf-8")
        # Something of the checkout's OWN, on a path this land does not touch — so
        # read-tree preserves it and the sync still succeeds.
        (self.main / "its-own-note.txt").write_text("mine\n", encoding="utf-8")
        parent, tip = self._cas_forward()

        report = session._sync_main_checkout("main", parent, tip)

        self.assertIn("fast-forwarded", report)
        self.assertIn("content kept", report)
        self.assertTrue((self.main / "its-own-note.txt").exists())
        self.assertFalse((state / "stale-checkout.txt").exists())


class SharedDataLinkTest(WorktreeLaneBase):
    """Session 90 — a lane materializes TRACKED files only, so the gitignored shared data
    (inbox, user profile, upstream mirrors, machine-local config) is simply absent. The
    session that found this opened announcing `inbox: (empty)` while two briefs were
    pending, one of them time-boxed."""

    def _seed_main(self):
        (self.main / "proposed-edits" / "test-arch" / "pending").mkdir(parents=True)
        (self.main / "proposed-edits" / "test-arch" / "pending" / "brief.md").write_text(
            "a real pending brief\n", encoding="utf-8")
        (self.main / "users" / "operator").mkdir(parents=True)
        (self.main / "users" / "operator" / "profile.md").write_text("# prefs\n", encoding="utf-8")
        (self.main / "reconcile-roots.local").write_text("/some/root\n", encoding="utf-8")

    def test_lane_sees_the_shared_data_after_linking(self):
        self._seed_main()
        self._point_session_at_lane()
        self.assertFalse((self.lane / "proposed-edits").exists(), "absent before — the defect")

        session._link_shared_data()

        brief = self.lane / "proposed-edits" / "test-arch" / "pending" / "brief.md"
        self.assertTrue(brief.is_file(), "the lane can now read the pending brief")
        self.assertEqual(brief.read_text(encoding="utf-8"), "a real pending brief\n")
        self.assertTrue((self.lane / "users" / "operator" / "profile.md").is_file())
        self.assertTrue((self.lane / "reconcile-roots.local").is_file())

    def test_a_write_from_the_lane_reaches_the_one_real_copy(self):
        """Links, not copies — a second copy would drift (P16). Filing a brief from a
        lane must file it for everyone."""
        self._seed_main()
        self._point_session_at_lane()
        session._link_shared_data()

        (self.lane / "proposed-edits" / "test-arch" / "pending" / "brief.md").unlink()

        self.assertFalse(
            (self.main / "proposed-edits" / "test-arch" / "pending" / "brief.md").exists(),
            "the main checkout's copy is the same file, not a duplicate")

    def test_session_state_is_never_shared(self):
        """The liveness sidecar is per-tree BY DESIGN — it is what makes a lane invisible
        to its siblings. Sharing it would break isolation outright."""
        self.assertNotIn(".session-state", session.SHARED_LANE_PATHS)
        (self.main / ".session-state").mkdir(exist_ok=True)
        self._point_session_at_lane()
        session._link_shared_data()
        self.assertFalse((self.lane / ".session-state").is_symlink())

    def test_absent_upstream_paths_are_not_shelled_out(self):
        """Members carry different gitignored sets; never invent an empty `inputs/`."""
        self._point_session_at_lane()
        session._link_shared_data()
        self.assertFalse((self.lane / "inputs").exists())

    def test_existing_lane_content_is_never_replaced(self):
        self._seed_main()
        self._point_session_at_lane()
        (self.lane / "users").mkdir()
        (self.lane / "users" / "own.md").write_text("lane's own\n", encoding="utf-8")

        session._link_shared_data()

        self.assertFalse((self.lane / "users").is_symlink(), "not clobbered")
        self.assertEqual((self.lane / "users" / "own.md").read_text(encoding="utf-8"),
                         "lane's own\n")

    def test_is_idempotent(self):
        self._seed_main()
        self._point_session_at_lane()
        session._link_shared_data()
        session._link_shared_data()
        self.assertTrue((self.lane / "users" / "operator" / "profile.md").is_file())

    def test_noop_from_the_main_checkout(self):
        self._seed_main()
        self._point_session_at_lane()
        session.ROOT = self.main
        session._link_shared_data()
        self.assertFalse((self.main / "proposed-edits").is_symlink(),
                         "the main checkout holds the real dirs — never link over them")


class LaneOrdinalTest(WorktreeLaneBase):
    """R4 (the session-90 five-way collision): the ordinal is COMPILED, never declared.

    Every lane cut from one base derives the same lane-local ordinal — five concurrent
    lanes all announced and committed 'close session 90'. So a lane must never assert a
    final number: its derived close-commit message carries the session-id, and the trunk
    compile assigns the real ordinals over the landed union (frozen_max + rank by
    `started`). The acceptance drill from the 2026-07-24 landing brief §4."""

    def _end_args(self, did, title):
        # `confirm` since ADR-0113: a hand-opened lane is no longer exempt from the close
        # guard, and these lanes are hand-opened (no dispatch env — the suite neutralizes
        # it). The subject here is ordinal derivation, not the guard, so the close is
        # given the agreement a real one would carry rather than being routed around it.
        return argparse.Namespace(title=title, session_id=did, commit="", push=False,
                                  dry_run=False, focus=None, blocked=None, no_merge=False,
                                  confirm="close it out")

    def _point_session_at(self, lane):
        session.ROOT = lane
        session.JOURNAL_DIR = lane / "sessions" / "journal"
        session.ARCHIVE = lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = lane / ".session-state"
        session.CONFIG_PATH = lane / "session.config.json"
        session.CFG.update({"handoff": lane / "session-handoff.md",
                            "status": lane / "STATUS.md",
                            "role_doc": lane / "role.md"})

    def test_two_lanes_from_one_base_land_distinct_ordinals_no_colliding_claims(self):
        # Lane 2 is cut from the SAME base as lane 1, before anything lands — the
        # concurrent-night topology.
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        did1, did2 = "20260723T2142Z-runner-aaaa", "20260723T2143Z-runner-bbbb"

        # Lane 1 closes with the DERIVED message (bare --commit) and lands.
        self._point_session_at_lane()
        self._write_lane_journal(did1, "(in progress)", ended="")
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_end(self._end_args(did1, "first lane"))
        msg1 = _out(self.main, "log", "-1", "--format=%s", "main")

        # Lane 2 (same base) closes the same way; its land rebases onto lane 1's tip.
        self._point_session_at(lane2)
        self._write_lane_journal(did2, "(in progress)", ended="")
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_end(self._end_args(did2, "second lane"))
        msg2 = _out(self.main, "log", "-1", "--format=%s", "main")

        # R4b: neither close-commit asserts a final ordinal; each carries its session-id.
        for msg, did in ((msg1, did1), (msg2, did2)):
            self.assertIn(did, msg, "the derived lane close message carries the session-id")
            self.assertNotIn("close session", msg,
                             "a lane close-commit must not claim an ordinal")

        # Both journals landed on the trunk ref.
        self.assertIn(f"sessions/journal/{did1}.md", _files_on(self.main, "main"))
        self.assertIn(f"sessions/journal/{did2}.md", _files_on(self.main, "main"))

        # R4a: the TRUNK compile assigns distinct consecutive ordinals over the union.
        _git(self.main, "checkout", "HEAD", "--", "sessions")
        session.ROOT = self.main
        session.JOURNAL_DIR = self.main / "sessions" / "journal"
        session.ARCHIVE = self.main / "sessions" / "pre-journal-archive.md"
        self.assertEqual((session.compiled_ordinal(did1), session.compiled_ordinal(did2)),
                         (1, 2), "distinct, consecutive, started-ordered")

    def test_lane_banner_shows_estimate_not_assertion(self):
        self._point_session_at_lane()
        did = "20260723T2142Z-runner-cccc"
        self._write_lane_journal(did, "(in progress)", ended="")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session.cmd_end(self._end_args(did, "banner check"))
        banner = next(l for l in buf.getvalue().splitlines() if " end · " in l)
        self.assertIn("Session ~", banner, "lane end banner shows ~N, not a final claim")
        self.assertIn(session.LANE_ORDINAL_NOTE, banner)
        # R2b: a real `end` leaves the end-ran marker — "closed?" is a file test now.
        self.assertTrue((self.lane / ".session-state" / f"{did}.end-ran").is_file(),
                        "cmd_end must leave the end-ran receipt")


class CloseHonestyReportTest(WorktreeLaneBase):
    """R2a (the poga-5 gap): an unlanded lane's journal exists only on its branch —
    invisible to the trunk journal reap — so a session that claimed a close its `end`
    never performed was perfectly silent. The reaper and the start orientation now
    report 'journal present but UNSTAMPED' per unmerged lane."""

    def _commit_lane_journal(self, ended):
        did = "20260724T1200Z-runner-d312"
        self._point_session_at_lane()
        self._write_lane_journal(did, "(in progress)", ended=ended)
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "lane journal")
        return did

    def test_unstamped_lane_journal_is_named(self):
        did = self._commit_lane_journal(ended="")
        self.assertEqual(session._lane_unstamped_journals("worktree-poga-1"), [did])
        # And the loud reaper surfaces it (lane must read as an aged orphan first).
        buf = io.StringIO()
        with mock.patch.object(session, "_scan_lanes", return_value=[{
                "name": "poga-1", "path": self.lane, "branch": "worktree-poga-1",
                "status": "unmerged", "registered": True, "is_self": False,
                "age_min": LANE_AGED}]):
            with contextlib.redirect_stdout(buf):
                session.cmd_reap_lanes(argparse.Namespace(dry_run=False))
        self.assertIn(f"journal {did}: present but UNSTAMPED", buf.getvalue())

    def test_a_stamped_lane_journal_is_not_flagged(self):
        self._commit_lane_journal(ended="2026-07-23T22:52:00+00:00")
        self.assertEqual(session._lane_unstamped_journals("worktree-poga-1"), [])


class MergedLaneJournalEvidenceTest(WorktreeLaneBase):
    """WI-0136. The R2a warning above is wired into the UNMERGED block only, and the
    resolver behind it (`_lane_journal_ids`) asks `git diff trunk...branch` — which is
    empty by construction once a branch is merged. Merged lanes are exactly the set
    `reap-lanes` REMOVES, so the teardown path had no unstamped-journal check that could
    ever answer.

    What that costs is not a missing warning. A lane's `.session-state` is not on the
    branch, so it does not travel with the merge; removing the worktree deletes the only
    record of when those sessions ended. A journal that was closeable at a real timestamp
    becomes one that can only ever be closed by inventing one — which is the permanent
    board warn we already carry for one member's `20260813T1200Z-runner-d45d`, whose
    evidence went the same way."""

    def _lane_with_open_journal(self, ended_marker=True):
        did = "20260815T1210Z-runner-b294"
        self._point_session_at_lane()
        self._write_lane_journal(did, "(in progress)", ended="")
        state = self.lane / ".session-state"
        state.mkdir(exist_ok=True)
        csid = "csid-" + did
        (state / f"{csid}.live").write_text(json.dumps(
            {"session_id": did, "started": "2026-08-15T12:10:47+00:00",
             "last_beat": "2026-08-15T16:00:13+00:00"}), encoding="utf-8")
        if ended_marker:
            (state / f"{csid}.ended").write_text(json.dumps(
                {"ended": "2026-08-15T16:00:14+00:00", "reason": "other"}), encoding="utf-8")
        return did

    def _reap(self, status="reap", dry_run=False):
        # The reaper runs from the main checkout, never from inside the lane it removes
        # (`is_self` is False below). Left at the lane, a reap that really frees the
        # worktree then runs git with a deleted cwd. Only Linux got that far: on macOS
        # the tmp dir's /var -> /private/var spelling makes the reaper refuse the lane
        # as "not under" the worktrees dir before removing anything (WI-0468).
        buf = io.StringIO()
        with mock.patch.object(session, "ROOT", self.main), \
             mock.patch.object(session, "_scan_lanes", return_value=[{
                "name": "poga-1", "path": self.lane, "branch": "worktree-poga-1",
                "status": status, "registered": True, "is_self": False,
                "age_min": LANE_AGED}]):
            with contextlib.redirect_stdout(buf):
                session.cmd_reap_lanes(argparse.Namespace(dry_run=dry_run))
        return buf.getvalue()

    def test_the_branch_diff_cannot_see_it_but_the_sidecar_can(self):
        """The two resolvers on the same lane, side by side — this is the whole defect."""
        did = self._lane_with_open_journal()
        self.assertEqual(session._lane_unstamped_journals("worktree-poga-1"), [],
                         "precondition: nothing is committed on the branch beyond the "
                         "trunk, which is the merged-lane shape")
        got = session._lane_sidecar_unstamped_journals(self.lane)
        self.assertEqual([sid for sid, _ev in got], [did])
        self.assertTrue(got[0][1]["has_ended"])

    def test_a_merged_lane_holding_one_is_held_not_reaped(self):
        self._lane_with_open_journal()
        out = self._reap()
        self.assertIn("HELD BACK", out)
        self.assertIn("closeable NOW", out)
        # Deliberately NOT `self.lane.is_dir()`. Under this fixture the temp dir is a
        # symlinked path that `_reap_lane` refuses on its own ("not under … refusing to
        # touch"), so the directory survives with or without the hold and the assertion
        # would pass against the broken version — a check that cannot fail
        # ([`verify-in-the-created-configuration`](habits/master.md#verify-in-the-created-configuration)).
        # What discriminates is that removal is never ATTEMPTED.
        self.assertNotIn("attempting", out,
                         "the lane reached the reap loop — the hold did not divert it")

    def test_the_reason_distinguishes_a_clean_exit_from_no_marker_at_all(self):
        """`declare-what-a-check-assumes`. 'I can close this right now' and 'no end time
        exists for this yet' are different states and lead to different next moves."""
        self._lane_with_open_journal(ended_marker=False)
        out = self._reap()
        self.assertIn("holds no exit marker", out)
        self.assertNotIn("closeable NOW", out)

    def test_a_stamped_journal_does_not_hold_the_lane(self):
        """The hold must be caused by the open journal and nothing else — otherwise it
        would be a blanket refusal to reap wearing a specific reason."""
        did = "20260815T1210Z-runner-b294"
        self._point_session_at_lane()
        self._write_lane_journal(did, "done", ended="2026-08-15T16:00:14+00:00")
        state = self.lane / ".session-state"
        state.mkdir(exist_ok=True)
        (state / f"csid-{did}.live").write_text(json.dumps(
            {"session_id": did, "last_beat": "2026-08-15T16:00:13+00:00"}), encoding="utf-8")
        self.assertEqual(session._lane_sidecar_unstamped_journals(self.lane), [])
        out = self._reap()
        self.assertNotIn("HELD BACK", out)

    def test_a_lane_with_no_sidecar_does_not_hold(self):
        """No sidecar is not evidence of an open journal — it is the ordinary state of a
        lane whose sessions all closed properly, and it must reap."""
        self._point_session_at_lane()
        self.assertEqual(session._lane_sidecar_unstamped_journals(self.lane), [])
        self.assertNotIn("HELD BACK", self._reap())

    def test_the_dry_run_never_promises_to_reap_a_held_lane(self):
        """The dry run is what an operator reads before committing to the destructive run.
        Listing the lane under "would reap" and then holding it is the WI-0038 shape — two
        different outcomes printing one sentence."""
        self._lane_with_open_journal()
        out = self._reap(dry_run=True)
        self.assertIn("HELD BACK", out)
        self.assertNotIn("would reap", out)

    def test_a_held_lane_is_not_counted_as_reapable_alongside_a_real_one(self):
        """With a genuinely reapable lane present the summary line IS reached, and the two
        must land in different counters."""
        self._lane_with_open_journal()
        other = self.main / ".claude" / "worktrees" / "poga-7"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-7",
             str(other), "main")
        buf = io.StringIO()
        with mock.patch.object(session, "_scan_lanes", return_value=[
                {"name": "poga-1", "path": self.lane, "branch": "worktree-poga-1",
                 "status": "reap", "registered": True, "is_self": False,
                 "age_min": LANE_AGED},
                {"name": "poga-7", "path": other, "branch": "worktree-poga-7",
                 "status": "reap", "registered": True, "is_self": False,
                 "age_min": LANE_AGED}]):
            with contextlib.redirect_stdout(buf):
                session.cmd_reap_lanes(argparse.Namespace(dry_run=True))
        out = buf.getvalue()
        self.assertIn("1 reapable", out)
        self.assertIn("1 held on an unstamped journal", out)
        self.assertIn("poga-7", out.split("would reap")[1].split("reap-lanes:")[0])
        self.assertNotIn("poga-1", out.split("would reap")[1].split("reap-lanes:")[0])


class AdrAllocationGateTest(WorktreeLaneBase):
    """R5 (the session-90 duplicate-0067): the allocator did not fail — it was BYPASSED.
    poga-8 hand-picked 0067 with no reservation while poga-6 held it via adr-next.
    Drawing is now enforced at the land gate (R5a), reservations survive TTL while their
    lane lives (R5b), and the allocator scans sibling lane tips (R5c)."""

    def _commit_adr(self, lane, name):
        (lane / "adr").mkdir(exist_ok=True)
        (lane / "adr" / name).write_text("# adr\n", encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"adr {name}")

    def test_hand_picked_number_against_a_sibling_reservation_blocks_the_land(self):
        """The R5d acceptance drill: create an ADR in a lane without a reservation while
        a sibling holds that number — the land must block."""
        self._point_session_at_lane()
        _make_live_sibling(self.main)
        session._coord_try_acquire("adr-alloc", "0007", "worktree-poga-9", 3600,
                                   branch="worktree-poga-9")
        self._commit_adr(self.lane, "0007-hand-picked.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "hand-picked reserved number must not land")
        self.assertIn("BLOCKED", buf.getvalue())
        self.assertIn("reserved by worktree-poga-9", buf.getvalue())
        self.assertNotIn("adr/0007-hand-picked.md", _files_on(self.main, "main"))

    def test_a_dead_siblings_reservation_is_released_and_the_land_proceeds(self):
        """WI-0164's acceptance, and the twin of the test above.

        Same configuration except the holder is a lane that no longer has a live session.
        operator ruled in session ~173 that the system must release from dead lanes on its own,
        not hand the job to the operator. Refusing here left renumbering correctly-drawn items or
        hand-editing json under `.git/` as the only ways out.
        """
        self._point_session_at_lane()
        session._coord_try_acquire("adr-alloc", "0007", "worktree-poga-9", 3600,
                                   branch="worktree-poga-9")     # no lane behind it
        self._commit_adr(self.lane, "0007-recovered.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        out = buf.getvalue()
        self.assertTrue(landed, out)
        self.assertIn("released ADR 0007", out)
        self.assertIn("adr/0007-recovered.md", _files_on(self.main, "main"))

    def test_number_already_used_on_the_trunk_blocks_the_land(self):
        """The real-world shape: the trunk already carries NNNN under another name."""
        (self.main / "adr").mkdir()
        (self.main / "adr" / "0007-first.md").write_text("# adr\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "trunk adr 0007")
        self._point_session_at_lane()
        self._commit_adr(self.lane, "0007-second.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "a trunk-duplicate ADR number must not land")
        self.assertIn("already uses", buf.getvalue())
        self.assertNotIn("adr/0007-second.md", _files_on(self.main, "main"))

    def test_a_drawn_number_lands(self):
        self._point_session_at_lane()
        n = session._adr_reserve_next()
        self.assertIsNotNone(n)
        self._commit_adr(self.lane, f"{n:04d}-drawn-properly.md")
        with contextlib.redirect_stdout(io.StringIO()):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "a number drawn via adr-next lands")
        self.assertIn(f"adr/{n:04d}-drawn-properly.md", _files_on(self.main, "main"))

    def test_reserve_next_sees_a_sibling_lane_tip(self):
        """R5c: a committed-but-unlanded ADR on a sibling tip (even with its reservation
        lapsed) can never be re-issued."""
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        self._commit_adr(lane2, "0009-sibling-unlanded.md")
        self._point_session_at_lane()
        self.assertEqual(session._adr_reserve_next(), 10,
                         "the allocator must scan sibling lane tips, not just adr/ files")

    def test_expired_reservation_with_a_living_lane_still_holds_its_number(self):
        """R5b: TTL alone must not free a number while the holder's lane branch exists;
        once the branch is gone the TTL backstop reaps it."""
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        self._point_session_at_lane()
        d = session._coord_dir("adr-alloc", create=True)
        rec = session._coord_record("worktree-poga-2", ttl=-100, name="0005",
                                    kind="adr-alloc", branch="worktree-poga-2")
        (d / "0005.json").write_text(json.dumps(rec), encoding="utf-8")
        # Expired-but-branch-alive: still holds, is not reaped, and pushes the allocator past it.
        self.assertIn("0005", session._adr_alloc_holds())
        session._coord_reap(("adr-alloc",))
        self.assertTrue((d / "0005.json").exists(),
                        "reap must not free a number whose lane still exists")
        self.assertEqual(session._adr_reserve_next(), 6)
        # Branch gone -> the TTL backstop frees it.
        _git(self.main, "worktree", "remove", "--force", str(lane2))
        _git(self.main, "branch", "-D", "worktree-poga-2")
        session._coord_reap(("adr-alloc",))
        self.assertFalse((d / "0005.json").exists(),
                         "with the lane gone, the expired reservation reaps")


    # ── WI-0093: the gate compares session-grained, not checkout-grained ────────────

    def _lane_journal_for(self, csid="csid-front-door"):
        """An OPEN journal in the lane, keyed by this session's claude-session-id, so
        `_coord_holder` can resolve WHICH SESSION is acting. Returns its session-id.

        Opts out of the journal neutraliser by name: the base fixture patches
        `_coord_holder` to return an empty journal so that two fictional holders in one
        test cannot both inherit the ambient session's real one (WI-0126). These two tests
        are the case that opt-out exists for — their SUBJECT is holder resolution — and
        they own every input they read (`JOURNAL_DIR`, `CLAUDE_CODE_SESSION_ID`), so they
        never touch the developer's live session."""
        exercise_real_coord_holder(self)
        did = "20260818T1200Z-runner-fd01"
        self._point_session_at_lane()
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": session.JOURNAL_STUB_TITLE,
            "machine": "Runner", "runtime": session.CLAUDE_RUNTIME,
            "role-doc-version": "v1.0.0", "base-commit": "0" * 12,
            "started": "2026-08-18T12:00:00+00:00", "ended": "",
            "claude-session-id": csid,
        })
        return did

    def test_a_number_this_lane_drew_through_the_front_door_can_land(self):
        """THE WI-0093 CASE, and the one the pre-existing drill never reached.

        `test_a_drawn_number_lands` draws from INSIDE the lane, so the record carries the
        lane's own branch and the old branch-equality check already passed it. The real
        shape is different: `poga work` / `adr-next` anchor on the MAIN checkout by design
        (ADR-0073), so the record a lane's draw produces carries `branch: main` and main's
        ownership key — while its `journal` is the lane session's. Under the old
        comparison the lane was then refused its OWN land, because from the record the
        number genuinely was reserved by someone else. That cost a renumber and the
        permanent gap at ADR-0083.
        """
        did = self._lane_journal_for()
        d = session._coord_dir("adr-alloc", create=True)
        rec = session._coord_record("main-session-key", ttl=3600, name="0009",
                                    kind="adr-alloc", branch="main")
        rec["journal"] = did          # drawn BY this lane's session, THROUGH main
        rec["session_id"] = "main-session-key"
        (d / "0009.json").write_text(json.dumps(rec), encoding="utf-8")

        self._commit_adr(self.lane, "0009-drawn-via-the-front-door.md")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "csid-front-door"}):
            with contextlib.redirect_stdout(io.StringIO()):
                landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "a lane must be able to land the number it itself drew")
        self.assertIn("adr/0009-drawn-via-the-front-door.md", _files_on(self.main, "main"))

    def test_a_number_another_session_drew_still_blocks(self):
        """The other half of the acceptance, and the one that matters: loosening the
        comparison must not let a genuine collision through. Same shape as above — a
        record on `branch: main`, so the branch check cannot save it — but the journal
        belongs to somebody else."""
        self._lane_journal_for()
        d = session._coord_dir("adr-alloc", create=True)
        rec = session._coord_record("main-session-key", ttl=3600, name="0009",
                                    kind="adr-alloc", branch="main")
        rec["journal"] = "20260818T0000Z-runner-9999"      # a DIFFERENT session
        rec["session_id"] = "someone-else"
        (d / "0009.json").write_text(json.dumps(rec), encoding="utf-8")

        self._commit_adr(self.lane, "0009-hand-picked.md")
        buf = io.StringIO()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "csid-front-door"}):
            with contextlib.redirect_stdout(buf):
                landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "another session's reservation must still block")
        self.assertIn("BLOCKED", buf.getvalue())

    def test_an_unresolvable_journal_degrades_to_the_old_behaviour_not_to_allow(self):
        """`declare-what-a-check-assumes`, on the safety-critical side. With no journal to
        match on, the gate must fall back to the key it always used — never to "allow when
        unknown", which is the direction that re-opens the hole the gate exists to close.

        No journal is written here at all, so `_coord_my_journal()` returns "".

        `_point_session_at_lane()` comes FIRST and that ordering is load-bearing:
        `_coord_dir` resolves through the module-global ROOT, so touching the coord store
        before repointing writes the record into whatever repo ROOT currently names — in
        a test run, the real one. The first cut of this test did exactly that and left a
        live reservation on ADR 0009 in the federation's own `.git/poga-coord/`, which
        would have blocked a genuine land."""
        self._point_session_at_lane()
        d = session._coord_dir("adr-alloc", create=True)
        rec = session._coord_record("someone-else", ttl=3600, name="0009",
                                    kind="adr-alloc", branch="main")
        rec["journal"] = ""
        rec["session_id"] = "someone-else"
        (d / "0009.json").write_text(json.dumps(rec), encoding="utf-8")

        self._commit_adr(self.lane, "0009-no-journal.md")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "an empty journal must never match — absence is not identity")
        self.assertIn("BLOCKED", buf.getvalue())


class WiAllocationGateTest(WorktreeLaneBase):
    """ADR-0073: the work-item store's WI-NNNN allocator + land gate, built as a direct
    mirror of the ADR-number mechanism (AdrAllocationGateTest above) since it exists
    precisely to fix the same failure class — a hand-picked number bypassing the draw."""

    def _commit_wi(self, lane, filename, wid, title="item"):
        (lane / "work-items").mkdir(exist_ok=True)
        (lane / "work-items" / filename).write_text(
            f"# {wid}: {title}\n\n- status: open\n- section: backlog\n- blocked-by: \n- group: \n\n",
            encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"wi {wid}")

    def test_hand_picked_number_against_a_sibling_reservation_blocks_the_land(self):
        self._point_session_at_lane()
        _make_live_sibling(self.main)
        session._coord_try_acquire("wi-alloc", "0007", "worktree-poga-9", 3600,
                                   branch="worktree-poga-9")
        self._commit_wi(self.lane, "WI-0007-hand-picked.md", "WI-0007", "hand-picked")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "hand-picked reserved WI number must not land")
        self.assertIn("BLOCKED", buf.getvalue())
        self.assertIn("reserved by worktree-poga-9", buf.getvalue())

    def test_number_already_used_on_the_trunk_blocks_the_land(self):
        self._commit_wi(self.main, "WI-0007-first.md", "WI-0007", "first")
        self._point_session_at_lane()
        self._commit_wi(self.lane, "WI-0007-second.md", "WI-0007", "second")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertFalse(landed, "a trunk-duplicate WI number (different filename) must not land")
        self.assertIn("already uses", buf.getvalue())

    def test_a_drawn_number_lands(self):
        self._point_session_at_lane()
        n = session._wi_reserve_next()
        self.assertIsNotNone(n)
        self._commit_wi(self.lane, f"WI-{n:04d}-drawn-properly.md", f"WI-{n:04d}", "drawn properly")
        with contextlib.redirect_stdout(io.StringIO()):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        self.assertTrue(landed, "a WI number drawn via wi-next lands")
        self.assertTrue((self.main / "work-items" / f"WI-{n:04d}-drawn-properly.md").exists())

    def test_reserve_next_sees_a_sibling_lane_tip(self):
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        self._commit_wi(lane2, "WI-0009-sibling-unlanded.md", "WI-0009", "sibling unlanded")
        self._point_session_at_lane()
        self.assertEqual(session._wi_reserve_next(), 10,
                         "the allocator must scan sibling lane tips, not just the file on disk")

    def test_expired_reservation_with_a_living_lane_still_holds_its_number(self):
        lane2 = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2", str(lane2), "main")
        self._point_session_at_lane()
        d = session._coord_dir("wi-alloc", create=True)
        rec = session._coord_record("worktree-poga-2", ttl=-100, name="0005",
                                    kind="wi-alloc", branch="worktree-poga-2")
        (d / "0005.json").write_text(json.dumps(rec), encoding="utf-8")
        self.assertIn("0005", session._wi_alloc_holds())
        session._coord_reap(("wi-alloc",))
        self.assertTrue((d / "0005.json").exists(),
                        "reap must not free a number whose lane still exists")
        self.assertEqual(session._wi_reserve_next(), 6)
        _git(self.main, "worktree", "remove", "--force", str(lane2))
        _git(self.main, "branch", "-D", "worktree-poga-2")
        session._coord_reap(("wi-alloc",))
        self.assertFalse((d / "0005.json").exists(),
                         "with the lane gone, the expired reservation reaps")


class IntegrateWithRemoteTest(WorktreeLaneBase):
    """WI-0143 / ADR-0097 — a lane that lands while origin advances can still publish.

    The lane land is a CAS on the LOCAL trunk ref: no checkout, and therefore no way to
    rebase onto a peer's push, because the trunk is checked out in the main checkout that
    a lane's isolation guard refuses to reach into. Before `integrate` the work simply
    stayed on one disk and the land told a human to `git push` — advice that cannot
    succeed from a lane. These tests pin the five cases the verb distinguishes, and that
    every refusal leaves the local trunk exactly where it was.
    """

    def setUp(self):
        super().setUp()
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)
        _git(self.main, "remote", "add", "origin", str(self.origin))
        self._install_main_compiler()
        _git(self.main, "push", "-q", "origin", "main")
        self._point_session_at_lane()

    # -- helpers ---------------------------------------------------------------
    def _origin_files(self):
        r = subprocess.run([GIT, "-C", str(self.origin), "ls-tree", "-r",
                            "--name-only", "main"], capture_output=True, text=True)
        return set(r.stdout.split())

    def _origin_tip(self):
        return _out(self.origin, "rev-parse", "main")

    def _peer_pushes(self, filename="peer.txt", body="peer\n"):
        """A concurrent session on another machine lands and pushes first — the exact
        shape that made our push non-ff. Done through a real clone so origin advances by
        a commit whose sha we could never have."""
        other = self.tmp / f"other-{filename}"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "peer@t")
        _git(other, "config", "user.name", "peer")
        _git(other, "config", "commit.gpgsign", "false")
        (other / filename).write_text(body, encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", f"peer work {filename}")
        _git(other, "push", "-q", "origin", "main")
        return _out(other, "rev-parse", "HEAD")

    def _land(self, workfile="work.txt"):
        self._stage_lane_work(workfile)
        return session._land_worktree_lane(None, None, "1.0.0", push=False)

    # -- the five cases --------------------------------------------------------
    def test_diverged_merges_origin_in_regates_and_pushes(self):
        """The reported case. Our work is on the local trunk, a peer pushed while we
        landed, and the push is non-ff. Integrate MERGES origin in and publishes — both
        machines' work reaches origin, and NEITHER side's commits are rewritten, which is
        what makes the push a fast-forward and the reconciliation convergent."""
        self.assertTrue(self._land())
        peer = self._peer_pushes()
        local_before = _out(self.main, "rev-parse", "main")
        self.assertNotEqual(local_before, peer)

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("gated in: isolated ", report)
        self.assertIn("work.txt", self._origin_files(), report)
        self.assertIn("peer.txt", self._origin_files(), report)
        # BOTH tips are ancestors of the published trunk — WI-0153's "nothing rewritten".
        # The peer half held under the replay too; OUR half is the one that did not, and
        # is the whole reason the two machines could never converge.
        for name, sha in (("the peer's tip", peer), ("our own pre-integrate tip", local_before)):
            self.assertEqual(0, subprocess.run(
                [GIT, "-C", str(self.main), "merge-base", "--is-ancestor", sha, "main"]
            ).returncode, f"{name} ({sha[:7]}) must survive as an ancestor\n{report}")
        # Local and origin now agree, so the next plain push is a no-op.
        self.assertEqual(_out(self.main, "rev-parse", "main"), self._origin_tip())

    def test_a_view_only_conflict_is_resolved_not_refused(self):
        """The likeliest divergence, and the one a naive rebase dies on: BOTH sides carry
        a `_recompile_main_checkout` commit rewriting the same generated handoff. The merge
        takes our side rather than refusing, because the views are derived and are
        regenerated immediately afterwards — and unlike the replay it drops no COMMIT, so
        anything else that commit carried still lands."""
        self.assertTrue(self._land())
        # Our side already carries a generated-view commit from the land's recompile.
        self.assertIn("session-handoff.md", self._trunk_files("main"))
        self._peer_pushes("session-handoff.md", "PEER VIEW\n")
        local_before = _out(self.main, "rev-parse", "main")

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("took our side of session-handoff.md", report)
        self.assertIn("work.txt", self._origin_files(), report)
        self.assertEqual(0, subprocess.run(
            [GIT, "-C", str(self.main), "merge-base", "--is-ancestor", local_before, "main"]
        ).returncode, report)

    def test_resolve_none_refuses_the_view_conflict_the_default_resolves(self):
        """The policy is a real switch, not a label. The same view-only conflict the
        `generated` default resolves must REFUSE under `none` — otherwise "bounded policy"
        is a claim nothing tests, and a member could not opt out of it if it wanted to."""
        self.assertTrue(self._land())
        self._peer_pushes("session-handoff.md", "PEER VIEW\n")
        local_before = _out(self.main, "rev-parse", "main")

        ok, lines = session._integrate_trunk_with_remote(resolve="none")
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertIn("REFUSED", report)
        self.assertIn("session-handoff.md", report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before)

    def test_a_conflict_a_replay_could_not_survive_merges_cleanly(self):
        """WI-0153's other half: a merge conflicts far less than a replay, because a
        cherry-pick's base is one commit's parent rather than the true merge base.

        Both sides edit the SAME authored file in different regions, on top of a shared
        version of it. That is a textbook 3-way merge and a coin-flip for a sequential
        replay. The point of the assertion is the CONTENT: both edits are present in what
        reaches origin, so nothing was quietly chosen away to make the merge succeed."""
        base = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"
        (self.lane / "shared.txt").write_text(base, encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "shared base")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=True))
        self.assertIn("shared.txt", self._origin_files())

        # Peer rewrites the LAST line; we rewrite the FIRST. One file, no overlap.
        self._peer_pushes("shared.txt", base.replace("ten\n", "TEN-theirs\n"))
        (self.lane / "shared.txt").write_text(base.replace("one\n", "ONE-ours\n"),
                                              encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "our edit high in the file")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        published = _out(self.origin, "show", "main:shared.txt")
        self.assertIn("ONE-ours", published, report)
        self.assertIn("TEN-theirs", published, report)

    def test_acceptance_two_checkouts_reconcile_with_integrate_alone(self):
        """WI-0153's stated acceptance, end to end: diverge two checkouts with real work
        on both sides and reconcile with `integrate` alone — no hand-run git, nothing
        rewritten, both sides' content preserved.

        The peer is a real clone pushing real commits, so its shas are ones we could never
        have produced. Every assertion below is about the PUBLISHED result, not the
        banner."""
        self.assertTrue(self._land("ours-a.txt"))
        self._stage_lane_work("ours-b.txt")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))
        ours = _out(self.main, "rev-parse", "main")
        theirs = self._peer_pushes("theirs-a.txt")

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)

        published = self._origin_files()
        for f in ("ours-a.txt", "ours-b.txt", "theirs-a.txt"):
            self.assertIn(f, published, f"{f} must survive the reconcile\n{report}")
        for name, sha in (("ours", ours), ("theirs", theirs)):
            self.assertEqual(0, subprocess.run(
                [GIT, "-C", str(self.main), "merge-base", "--is-ancestor", sha, "main"]
            ).returncode, f"{name} ({sha[:7]}) was rewritten\n{report}")
        self.assertEqual(_out(self.main, "rev-parse", "main"), self._origin_tip(), report)
        # And the reconcile CONVERGED: a second run has nothing left to do. A replay's
        # rewritten shas are exactly what made this the state that never settled.
        ok2, lines2 = session._integrate_trunk_with_remote()
        self.assertTrue(ok2, "\n".join(lines2))
        self.assertIn("already", "\n".join(lines2))

    def test_the_merge_keeps_first_parent_on_our_own_history(self):
        """Parent order is load-bearing, not cosmetic: `_checkout_merge_base` and
        `_main_restore_verdict` both walk `--first-parent` to find where the main checkout
        was last materialized, and `_sync_main_checkout` needs `local -> merge` to be a
        strict fast-forward. Merging the other way round leaves this machine's history on a
        side branch and defeats all three silently."""
        self.assertTrue(self._land())
        ours = _out(self.main, "rev-parse", "main")
        theirs = self._peer_pushes()

        ok, lines = session._integrate_trunk_with_remote()
        self.assertTrue(ok, "\n".join(lines))
        self.assertEqual(ours, _out(self.main, "rev-parse", "main^1"))
        self.assertEqual(theirs, _out(self.main, "rev-parse", "main^2"))

    def test_a_real_conflict_refuses_and_changes_nothing(self):
        """Two genuine edits to the same authored file. The verb must refuse rather than
        pick a side — and refusing has to be inert: the local trunk stays exactly where
        it was, and origin keeps only the peer's work."""
        (self.lane / "trunkfile.txt").write_text("ours\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "our edit")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))
        local_before = _out(self.main, "rev-parse", "main")
        peer = self._peer_pushes("trunkfile.txt", "theirs\n")

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertIn("REFUSED", report)
        self.assertIn("trunkfile.txt", report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before,
                         "a refusal must leave the local trunk untouched")
        self.assertEqual(self._origin_tip(), peer, "origin must be untouched too")
        # And the work is parked off-machine rather than left on one disk.
        parked = _out(self.origin, "for-each-ref", "--format=%(refname)", "refs/heads/parked/")
        self.assertIn(local_before[:12], parked, report)

    def test_behind_fast_forwards_the_local_trunk_and_pushes_nothing(self):
        """We hold nothing of our own; origin is simply ahead. The trunk ref moves
        forward and the main checkout comes with it — no replay, no push."""
        peer = self._peer_pushes()
        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), peer, report)
        self.assertIn("fast-forwarded", report)
        # The main checkout's tree was brought along, not left reporting a phantom delta.
        self.assertTrue((self.main / "peer.txt").exists(), report)

    def test_ahead_pushes_without_replaying(self):
        """The ordinary case: origin is an ancestor of us. Nothing is replayed and
        nothing is rewritten — the push is already a fast-forward."""
        self.assertTrue(self._land())
        tip = _out(self.main, "rev-parse", "main")
        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertEqual(self._origin_tip(), tip, report)
        self.assertNotIn("replayed", report)

    def test_already_in_sync_is_said_out_loud(self):
        ok, lines = session._integrate_trunk_with_remote()
        self.assertTrue(ok)
        self.assertIn("already", "\n".join(lines))

    def test_gate_failure_after_the_replay_refuses(self):
        """The replay merged cleanly but the combined tree does not pass the gate — a
        real conflict between the two machines' work, not a merge artefact. Refuse, and
        leave the local trunk where it was."""
        self.assertTrue(self._land())
        self._peer_pushes()
        local_before = _out(self.main, "rev-parse", "main")
        session.CFG["gate"] = FAIL_GATE
        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before)
        self.assertNotIn("work.txt", self._origin_files(), report)

    def test_a_number_collision_with_origin_blocks_the_integrate(self):
        """The counter land gate runs against ORIGIN's tree, not the tree we already
        landed on — so a WI number that only collides once the two trunks are combined is
        caught here rather than published."""
        self.assertTrue(self._land())
        # Both sides add the same drawn number, which no single land could have seen.
        (self.lane / "work-items").mkdir(exist_ok=True)
        (self.lane / "work-items" / "WI-0500-ours.md").write_text("# ours\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", "our item")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))
        local_before = _out(self.main, "rev-parse", "main")
        other = self.tmp / "other-wi"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(other)],
                       check=True, capture_output=True, text=True)
        _git(other, "config", "user.email", "peer@t")
        _git(other, "config", "user.name", "peer")
        _git(other, "config", "commit.gpgsign", "false")
        (other / "work-items").mkdir(exist_ok=True)
        (other / "work-items" / "WI-0500-theirs.md").write_text("# theirs\n", encoding="utf-8")
        _git(other, "add", "-A")
        _git(other, "commit", "-qm", "peer item")
        _git(other, "push", "-q", "origin", "main")

        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertIn("0500", report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before)

    def test_offline_refuses_without_touching_the_trunk(self):
        self.assertTrue(self._land())
        local_before = _out(self.main, "rev-parse", "main")
        _git(self.main, "remote", "set-url", "origin", str(self.tmp / "does-not-exist.git"))
        ok, lines = session._integrate_trunk_with_remote()
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertIn("OFFLINE", report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before)

    def test_dry_run_names_the_divergence_and_writes_nothing(self):
        self.assertTrue(self._land())
        self._peer_pushes()
        local_before = _out(self.main, "rev-parse", "main")
        origin_before = self._origin_tip()
        ok, lines = session._integrate_trunk_with_remote(dry_run=True)
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("DIVERGED", report)
        self.assertEqual(_out(self.main, "rev-parse", "main"), local_before)
        self.assertEqual(self._origin_tip(), origin_before)

    def test_land_integrates_itself_when_the_push_is_rejected(self):
        """The wiring, and the whole point of the item: the LAND does this for itself.
        A land whose push is rejected must not print `run git push` — an instruction that
        cannot succeed from a lane — it must integrate and publish.

        THE PEER NOW PUSHES MID-LAND, AND THAT IS THE POINT OF THE EDIT (WI-0356).
        This test used to push the peer BEFORE the land, because that was enough to
        make our post-CAS push non-ff. It is not enough any more, and the reason is a
        fix rather than a regression: the land now refuses to build on a trunk that is
        behind origin, so a peer who pushed first is fast-forwarded in before we rebase
        and our push is an ordinary fast-forward. That whole class of rejection is
        gone, and a test that kept asserting it would be asserting the defect.

        What WI-0356 CANNOT close is the window it does not span: the peer pushes
        after our check and before our push. That window is real and it is wide — the
        gate is the long pole of a land, minutes of it — so the peer pushes from
        inside the gate, which is where it actually happens. WI-0143's self-integrate
        is exactly as load-bearing as it was; it is reached by a narrower door."""
        self._stage_lane_work()
        real_gate = session._gate_commit
        pushed = []

        def peer_pushes_while_we_gate(sha, skip=frozenset()):
            if not pushed:
                pushed.append(self._peer_pushes())
            return real_gate(sha, skip)

        buf = io.StringIO()
        with mock.patch.object(session, "_gate_commit",
                               side_effect=peer_pushes_while_we_gate), \
                contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=True)
        report = buf.getvalue()
        self.assertTrue(pushed, "control: the peer must actually have pushed mid-land")
        self.assertTrue(landed, report)
        self.assertNotIn("run `git push`", report)
        self.assertIn("integrating with origin", report)
        self.assertIn("work.txt", self._origin_files(), report)
        self.assertIn("peer.txt", self._origin_files(), report)

    def test_a_peer_who_pushed_first_is_folded_in_before_we_ever_push(self):
        """The sibling of the test above, and the reason it had to change (WI-0356).

        Same setup the old version used — peer pushes, then we land — and the
        rejection never happens: the pre-land check sees behind-only, fast-forwards
        the trunk, and the land publishes in one pass. Pinned here, next to the case
        it was carved out of, so that if the check is ever removed this test goes red
        in the same file that explains why."""
        self._peer_pushes()
        self._stage_lane_work()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=True)
        report = buf.getvalue()
        self.assertTrue(landed, report)
        self.assertIn("fast-forwarded", report)
        self.assertNotIn("integrating with origin", report,
                         "the rejection-and-repair path must not have been needed")
        self.assertIn("work.txt", self._origin_files(), report)
        self.assertIn("peer.txt", self._origin_files(), report)

class MainRestoreTest(WorktreeLaneBase):
    """WI-0097 / ADR-0098 — a lane can repair the main checkout's working tree.

    The reported instance: main's working `ROADMAP.md` was STALE, so committing it would
    have reverted prose a concurrent lane had landed. operator authorised the repair and the
    lane could not run it — the isolation guard refuses `git -C` at the shared checkout,
    correctly — so an authorised repair went back to his shell.

    These tests pin the PREDICATE, which is the whole item. Restore only where the working
    copy is byte-identical to some ancestor commit's version of that path: provably content
    the trunk already superseded, still reachable, so the discard cannot lose anything even
    in principle. A human's own edit matches no commit and is KEPT — which is the case the
    weaker "no added lines" subset test would have silently destroyed.
    """

    def setUp(self):
        super().setUp()
        self._install_main_compiler()
        self._point_session_at_lane()

    def _land_a_change(self, path="trunkfile.txt", body="new\n"):
        """Land a real change from the lane so main has a HEAD newer than its own tree,
        and `_sync_main_checkout` materializes it — the healthy starting state."""
        (self.lane / path).write_text(body, encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"lane changes {path}")
        self.assertTrue(session._land_worktree_lane(None, None, "1.0.0", push=False))

    def _main_status(self):
        return _out(self.main, "status", "--porcelain")

    def test_stale_content_is_restored_and_the_proof_is_named(self):
        """The reported case. main's tree holds the PRE-land content of a file the trunk
        has since moved past — exactly what a missed sync leaves behind."""
        self._land_a_change()
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "new\n")
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")   # stale again
        self.assertNotEqual(self._main_status(), "")

        ok, lines = session._main_restore()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("restored trunkfile.txt", report)
        self.assertIn("byte-identical to its version at", report)
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "new\n")
        self.assertEqual(self._main_status(), "", "main is clean after the repair")

    def test_a_human_edit_is_kept_not_discarded(self):
        """The false positive the specified predicate would have had. A deliberate deletion
        produces a diff with no added lines — 'a strict subset of HEAD' — and discarding it
        destroys real work. Matching no commit is what tells the two apart."""
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("a paragraph nobody committed\n",
                                                 encoding="utf-8")
        ok, lines = session._main_restore()
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("KEPT trunkfile.txt", report)
        self.assertIn("it holds something of its own", report)
        self.assertEqual((self.main / "trunkfile.txt").read_text(),
                         "a paragraph nobody committed\n", "the human's work survives")

    def test_a_pure_deletion_of_committed_lines_is_still_kept(self):
        """Sharper form of the same trap: the working copy IS a strict subset of HEAD (only
        lines removed, none added) and is still a human's edit, because no commit ever held
        exactly that text."""
        self._land_a_change(body="one\ntwo\nthree\n")
        (self.main / "trunkfile.txt").write_text("one\nthree\n", encoding="utf-8")
        added = [l for l in _out(self.main, "diff", "HEAD", "--", "trunkfile.txt").splitlines()
                 if l.startswith("+") and not l.startswith("+++")]
        self.assertEqual(added, [], "fixture really is a subset of HEAD")
        ok, lines = session._main_restore()
        self.assertIn("KEPT trunkfile.txt", "\n".join(lines))
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "one\nthree\n")

    def test_untracked_is_never_deleted(self):
        (self.main / "someones-notes.md").write_text("mine\n", encoding="utf-8")
        ok, lines = session._main_restore()
        report = "\n".join(lines)
        self.assertIn("KEPT someones-notes.md", report)
        self.assertIn("untracked", report)
        self.assertTrue((self.main / "someones-notes.md").exists())

    def test_a_live_session_in_main_refuses_everything(self):
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=True):
            ok, lines = session._main_restore()
        report = "\n".join(lines)
        self.assertFalse(ok, report)
        self.assertIn("REFUSED", report)
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "base\n",
                         "nothing touched while someone may be working there")

    def test_harness_records_are_left_to_main_sync(self):
        """Two verbs writing the same files is the P13 violation this substrate keeps
        finding — the harness half is `main-sync`'s, by name."""
        (self.main / "STATUS.md").write_text("---\nid: test\n---\nchanged\n", encoding="utf-8")
        ok, lines = session._main_restore()
        report = "\n".join(lines)
        self.assertIn("harness-written record(s) left alone", report)
        self.assertIn("main-sync", report)
        self.assertNotIn("restored STATUS.md", report)

    def test_dry_run_writes_nothing(self):
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        ok, lines = session._main_restore(dry_run=True)
        report = "\n".join(lines)
        self.assertTrue(ok, report)
        self.assertIn("(dry-run) would restore trunkfile.txt", report)
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "base\n")

    def test_show_prints_what_a_kept_file_holds(self):
        """So the judgement call is made from the LANE. A verb that says 'I cannot decide'
        without showing the evidence just relocates the trip to the main checkout."""
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("something of my own\n", encoding="utf-8")
        ok, lines = session._main_restore(show=True)
        report = "\n".join(lines)
        self.assertIn("something of my own", report)

    def _point_session_at_main(self):
        session.ROOT = self.main
        session.SESSION_STATE_DIR = self.main / ".session-state"

    def _live_sidecar_in_main(self, csid):
        state = self.main / ".session-state"
        state.mkdir(exist_ok=True)
        (state / f"{csid}.live").write_text(
            json.dumps({"last_beat": session._coord_iso(session.time.time())}),
            encoding="utf-8")

    def test_it_runs_from_the_main_checkout(self):
        """WI-0461. The land prints `main-restore --dry-run` as the fix and a newcomer
        reads it in main, where the verb used to answer "not on a worktree lane"."""
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        self._point_session_at_main()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "me"}):
            self._live_sidecar_in_main("me")          # the asker's own session
            ok, lines = session._main_restore(dry_run=True)
            report = "\n".join(lines)
            self.assertTrue(ok, report)
            self.assertIn("(dry-run) would restore trunkfile.txt", report)
            self.assertEqual((self.main / "trunkfile.txt").read_text(), "base\n")
            ok, lines = session._main_restore()
        self.assertTrue(ok, "\n".join(lines))
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "new\n")
        self.assertNotIn("trunkfile.txt", self._main_status())

    def test_from_main_another_live_session_still_refuses(self):
        """Only the asker's own sidecar is excluded; a sibling live in main still stops
        the whole repair."""
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        self._point_session_at_main()
        self._live_sidecar_in_main("someone-else")
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "me"}):
            ok, lines = session._main_restore()
        self.assertFalse(ok, "\n".join(lines))
        self.assertIn("REFUSED", "\n".join(lines))
        self.assertEqual((self.main / "trunkfile.txt").read_text(), "base\n")

    def test_the_banner_names_the_verb_not_a_session_in_main(self):
        """The defect in one sentence (operator, ~145): the old banner told the operator to open
        a session in the main checkout, which the lane workflow does not use. A nag whose
        only remedy is a place the reader will not go trains them to skim the block."""
        self._land_a_change()
        (self.main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        with mock.patch.object(session, "_tree_has_live_session", return_value=False):
            lines = session._main_checkout_lines()
        report = "\n".join(lines)
        self.assertIn("main-restore", report)
        self.assertNotIn("open one in the main checkout", report)


class RebaseResolveTest(WorktreeLaneBase):
    """WI-0109 / ADR-0098 — a blocked rebase has a next verb.

    `merge` rebased, conflicted, aborted cleanly and said "resolve it in this lane" — which
    means opening a session in a lane whose session may be over. That is what left poga-2
    unmerged after poga-1 and poga-3 landed. The bounded policy clears the one class it can
    prove safe and refuses, by name, on everything else.
    """

    def setUp(self):
        super().setUp()
        self._install_main_compiler()
        self._point_session_at_lane()

    def _conflict_on(self, path, trunk_body, lane_body):
        """Make the trunk and the lane change the same file differently, from one base."""
        second = self.main / ".claude" / "worktrees" / "poga-9"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-9", str(second), "main")
        (second / path).write_text(trunk_body, encoding="utf-8")
        _git(second, "add", "-A")
        _git(second, "commit", "-qm", f"trunk side of {path}")
        _git(self.main, "update-ref", "refs/heads/main", _out(second, "rev-parse", "HEAD"))
        (self.lane / path).write_text(lane_body, encoding="utf-8")
        # Real work in the SAME commit. Without it, resolving the conflict empties the
        # commit and the land correctly reports "nothing to land" — true, but not the case
        # this policy exists for: a lane always carries its journal and its work alongside.
        (self.lane / "work.txt").write_text("lane work\n", encoding="utf-8")
        _git(self.lane, "add", "-A")
        _git(self.lane, "commit", "-qm", f"lane side of {path}")

    def test_a_generated_file_conflict_is_cleared_and_the_trunk_side_wins(self):
        self._conflict_on("STATUS.md", "---\nid: trunk\n---\n", "---\nid: lane\n---\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertTrue(landed, report)
        self.assertIn("cleared a rebase conflict", report)
        self.assertIn("STATUS.md", report)
        # The lane's real work landed, and the TRUNK's generated view is what survived.
        self.assertIn("work.txt", _files_on(self.main, "main"))
        self.assertIn("id: trunk", _out(self.main, "show", "main:STATUS.md"))

    def test_an_authored_conflict_refuses_by_name_and_offers_a_verb(self):
        self._conflict_on("trunkfile.txt", "trunk side\n", "lane side\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertFalse(landed, report)
        self.assertIn("REFUSED", report)
        self.assertIn("trunkfile.txt", report)
        self.assertIn("poga resume", report, "names a verb, not a place")
        # Nothing half-applied: no rebase left in progress, work still on the branch.
        self.assertFalse(session._rebase_in_progress())
        self.assertIn("trunkfile.txt", _files_on(self.main, "worktree-poga-1"))

    def test_policy_none_disables_the_resolution(self):
        """The bound is a choice, and a caller can decline it."""
        self._conflict_on("STATUS.md", "---\nid: trunk\n---\n", "---\nid: lane\n---\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False,
                                                 resolve="none")
        report = buf.getvalue()
        self.assertFalse(landed, report)
        self.assertNotIn("cleared a rebase conflict", report)
        self.assertFalse(session._rebase_in_progress())

    def test_an_unknown_policy_is_refused_rather_than_treated_as_off(self):
        cleared, lines = session._resolve_rebase_conflict("whatever")
        self.assertFalse(cleared)
        self.assertIn("unknown policy", "\n".join(lines))

def _roadmap(now, rendered, tail="Open policy questions: none.\n"):
    """A ROADMAP.md of the real shape: hand-authored surfaces on both sides of a
    generated region. The tail matters — the file's hand-authored content is not all
    ABOVE the markers, so a predicate that only looked at a prefix would pass wrongly."""
    return (f"# Roadmap\n\n"
            f"## Now — where we are\n\n{now}\n\n"
            f"## Next — immediate\n\n"
            f"{session.WI_GEN_BEGIN}\n\n{rendered}\n\n{session.WI_GEN_END}\n\n"
            f"{tail}")


class RoadmapResolveTest(RebaseResolveTest):
    """WI-0252 — the roadmap joins the resolvable class per REGION, not per file.

    ADR-0098 D7 kept `ROADMAP.md` out on the grounds that it is "authored prose with one
    writer per lane". ADR-0105 D4 reversed that premise eight days later: the file became
    trunk-only and every lane was told not to touch it. The exclusion outlived its reason,
    and by then a lane's roadmap divergence was almost always the harness's OWN re-render —
    so landing behind another lane refused, near-deterministically, on a file the trunk
    regenerates seconds later, and printed "this one needs a mind" at a mechanical no-op.

    The widening is only safe because it is CONDITIONAL, so both directions are pinned
    here: the render case resolves, and a lane that touched a hand-authored surface still
    refuses by name. The second test is the one that earns the first.
    """

    def setUp(self):
        super().setUp()
        # The file must exist in the COMMON BASE. Added-on-both-sides leaves no stage 1,
        # which the predicate fail-closes on — correct, but not the case under test.
        (self.main / "ROADMAP.md").write_text(
            _roadmap("NOW-BASE", "render base"), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed roadmap")
        _git(self.lane, "reset", "--hard", "--quiet", "main")

    def test_two_lanes_re_rendering_the_roadmap_resolve_instead_of_refusing(self):
        """The WI-0252 case itself, measured in session ~190: a lane whose only authored
        work was elsewhere refused to land because a sibling had re-rendered ROADMAP.md."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-BASE", "render trunk"),
                          _roadmap("NOW-BASE", "render lane"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertTrue(landed, report)
        self.assertIn("cleared a rebase conflict", report)
        self.assertIn("ROADMAP.md", report)
        # The lane's real work landed, and the TRUNK's render is what survived.
        self.assertIn("work.txt", _files_on(self.main, "main"))
        self.assertIn("render trunk", _out(self.main, "show", "main:ROADMAP.md"))

    def test_a_lane_that_edited_the_hand_authored_now_still_refuses_by_name(self):
        """The control that makes the widening honest. `## Now` is hand-authored and
        trunk-side (ADR-0105 D3); alongside it sit the as-of block, the open-policy
        questions and the frozen earlier-outcomes prose, which exists in NO other file.
        Whole-file membership would take the trunk side of all of them silently."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-BASE", "render trunk"),
                          _roadmap("NOW-EDITED-BY-THE-LANE", "render lane"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertFalse(landed, report)
        self.assertIn("REFUSED", report)
        self.assertIn("ROADMAP.md", report)
        self.assertIn("poga resume", report, "names a verb, not a place")
        self.assertFalse(session._rebase_in_progress())
        # Nothing half-applied: the lane's prose is still on the branch.
        self.assertIn("NOW-EDITED-BY-THE-LANE",
                      _out(self.main, "show", "worktree-poga-1:ROADMAP.md"))

    def test_an_edit_BELOW_the_generated_region_also_refuses(self):
        """The hand-authored content is on both sides of the markers. A predicate that
        compared only what precedes them would resolve this one away."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-BASE", "render trunk"),
                          _roadmap("NOW-BASE", "render lane",
                                   tail="Open policy questions: the lane added one.\n"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertFalse(landed, report)
        self.assertIn("REFUSED", report)
        self.assertIn("ROADMAP.md", report)

    def test_a_trunk_side_edit_to_now_does_not_make_the_lane_refuse(self):
        """Why the comparison is against the BASE and not against the trunk side.

        The trunk is where `## Now` is edited (ADR-0105 D3), so a lane landing behind a
        session that refreshed it will routinely see the two sides disagree on authored
        prose while the LANE authored none of it. Asking "do the two sides agree?" would
        refuse here — and would refuse most often on exactly the trunk this policy exists
        to let lanes land onto. Asking "did the side we are DISCARDING author anything?"
        is the question that matches the safety argument."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-REFRESHED-ON-THE-TRUNK", "render trunk"),
                          _roadmap("NOW-BASE", "render lane"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False)
        report = buf.getvalue()
        self.assertTrue(landed, report)
        self.assertIn("cleared a rebase conflict", report)
        # The trunk's own prose survived, which is the whole point of taking that side.
        self.assertIn("NOW-REFRESHED-ON-THE-TRUNK",
                      _out(self.main, "show", "main:ROADMAP.md"))

    def test_the_predicate_itself_admits_nothing_under_a_non_generated_policy(self):
        """The foot-gun the `policy` argument closes, exercised where it actually bites.

        The roadmap branch is keyed on the member's declared roadmap NAME, not on the
        generated-view set — so the pre-existing "hand it an empty set under `none`" shape
        would have left the roadmap resolving while its siblings were switched off. The
        land test below cannot see this: `_resolve_rebase_conflict` returns early on
        `none` and never reaches the predicate, so the branch is live only through
        `_merge_onto`, which has no such early return. A mutation run caught exactly that
        — the land test passed against a build with the guard deleted."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-BASE", "render trunk"),
                          _roadmap("NOW-BASE", "render lane"))
        r = subprocess.run([GIT, "-C", str(self.lane), "rebase", "main"],
                           capture_output=True, text=True)
        self.addCleanup(subprocess.run,
                        [GIT, "-C", str(self.lane), "rebase", "--abort"],
                        capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0, "the fixture must actually stop a rebase")
        self.assertEqual(session._unmerged_paths(), ["ROADMAP.md"])
        self.assertTrue(session._conflict_is_resolvable("ROADMAP.md", "generated"),
                        "a render-only side is admitted under the generated policy")
        self.assertFalse(session._conflict_is_resolvable("ROADMAP.md", "none"),
                         "`none` resolves nothing — including the roadmap")

    def test_policy_none_does_not_resolve_the_roadmap_either(self):
        """The same rule at the land surface: declining the policy declines it whole."""
        self._conflict_on("ROADMAP.md",
                          _roadmap("NOW-BASE", "render trunk"),
                          _roadmap("NOW-BASE", "render lane"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            landed = session._land_worktree_lane(None, None, "1.0.0", push=False,
                                                 resolve="none")
        report = buf.getvalue()
        self.assertFalse(landed, report)
        self.assertNotIn("cleared a rebase conflict", report)
        self.assertFalse(session._rebase_in_progress())

    def test_the_predicate_fails_closed_with_no_conflict_in_progress(self):
        """No stages to read is not "nothing was authored". Every unreadable case must
        answer False — the cost of a wrong True is prose that exists in no other file."""
        self.assertFalse(session._roadmap_side_is_render_only("ROADMAP.md"))

    def test_the_roadmap_is_not_in_the_generated_view_set(self):
        """The structural guard, and the reason this is a predicate rather than a third
        entry in `_generated_view_names()`. That set has a second reader —
        `_lane_adds_nothing_the_trunk_lacks` — which DELETES a lane branch on the strength
        of it. An unconditional roadmap there would make a lane holding a real `## Now`
        edit read as contributing nothing, and the widening would become a way to lose the
        exact prose the conditional membership exists to protect."""
        self.assertNotIn("ROADMAP.md", session._generated_view_names())
        lane = {"branch": "worktree-poga-1", "path": self.lane}
        (self.lane / "ROADMAP.md").write_text(
            _roadmap("NOW-EDITED-BY-THE-LANE", "render base"), encoding="utf-8")
        _git(self.lane, "commit", "-qam", "lane edits Now")
        self.assertFalse(session._lane_adds_nothing_the_trunk_lacks(lane),
                         "a lane holding a hand-authored roadmap edit is NOT empty")


class RoadmapPathIsDeclaredTest(unittest.TestCase):
    """WI-0252's root cause: the roadmap path was a literal in six call sites while its
    two sibling views resolved through `layout_of`. `_harness_owned` named all three in
    one expression and resolved exactly two — so a policy that asked CFG "what does this
    member generate?" got an answer with a hole in it, and the hole was this file."""

    def setUp(self):
        self._save = {k: getattr(session, k) for k in ("CFG", "CFG_RAW", "ROOT")}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def test_the_default_is_todays_exact_file(self):
        """An untouched config must behave precisely as it does now."""
        self.assertEqual(session.LAYOUT_DEFAULTS["roadmap"], "ROADMAP.md")
        session.CFG_RAW = {}
        self.assertEqual(session._roadmap_rel(), "ROADMAP.md")

    def test_the_path_follows_ROOT_not_a_config_snapshot(self):
        """Shipped wrong once this session, and worth a test rather than a memory.

        `CFG`'s paths are absolutised at import; `ROOT` is what a caller running in a lane
        has actually been repointed at, and the two disagreeing is this substrate's normal
        condition. A first cut preferred the CFG entry, so `wi-render` — which WRITES this
        path — opened the main checkout's roadmap while standing in a lane. That is not a
        wrong answer, it is a write to the wrong tree. The CFG entry was then removed
        outright, so the divergence is now unrepresentable rather than merely untaken."""
        session.CFG_RAW = {}
        session.ROOT = pathlib.Path("/tmp/somewhere-else")
        self.assertEqual(session._roadmap_path(),
                         pathlib.Path("/tmp/somewhere-else/ROADMAP.md"))
        self.assertNotIn("roadmap", session._load_config.__doc__ or "",
                         "no CFG entry to drift from ROOT")

    def test_a_declared_layout_moves_every_reader_at_once(self):
        session.CFG_RAW = {"layout": {"roadmap": "docs/ROADMAP.md"}}
        session.CFG = None          # the helper must not need one
        session.ROOT = pathlib.Path("/tmp/member")
        self.assertEqual(session._roadmap_rel(), "docs/ROADMAP.md")
        self.assertEqual(session._roadmap_path(), pathlib.Path("/tmp/member/docs/ROADMAP.md"))
        self.assertEqual(session._roadmap_name(), "ROADMAP.md")
        self.assertTrue(session._harness_owned("docs/ROADMAP.md"))
        self.assertFalse(session._harness_owned("ROADMAP.md"),
                         "the literal must no longer be what decides this")


class GateStdinTest(unittest.TestCase):
    """WI-0168 — the gate cannot be hung by a child that reads stdin.

    A land hung for 33 minutes at 0% CPU with no output and no timeout. SIGINT named the
    frame: `_read_hook_stdin` → `sys.stdin.read()`, waiting on an EOF that the session's
    inherited harness socket never sends. `sys.stdin.read()` waits for EOF, not for data,
    and the old guard covered only the tty case — but a tty is not the only stdin that
    never closes.

    These tests use a REAL pipe that is deliberately left open, because that is the whole
    defect. A mock that returns immediately would pass against the broken code.
    """

    def setUp(self):
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def test_the_gate_gives_its_child_a_closed_stdin(self):
        """The structural half: `_run_gate`'s child must not inherit the session's stdin,
        so it can never reach the blocking read at all."""
        seen = {}
        real = session.sh

        def record(argv, **kw):
            seen.update(kw)
            return real(["python3", "-c", "pass"], check=False)

        session.CFG = {"gate": [["python3", "-c", "pass"]]}
        with mock.patch.object(session, "sh", record):
            ok, _report = session._run_gate()
        self.assertTrue(ok)
        self.assertEqual(seen.get("stdin"), subprocess.DEVNULL,
                         "the gate must close its child's stdin, not inherit the session's")

    def test_a_never_closing_stdin_does_not_block_the_hook_read(self):
        """The general half. A real pipe with a writer that never closes is exactly the
        harness socket's shape; the old body waits on it forever."""
        r, w = os.pipe()                       # writer end stays open for the whole test
        self.addCleanup(os.close, w)
        try:
            reader = os.fdopen(r, "r")
        except Exception:                      # pragma: no cover - defensive
            os.close(r)
            raise
        self.addCleanup(reader.close)
        with mock.patch.object(session, "HOOK_STDIN_TIMEOUT_SEC", 0.3), \
                mock.patch.object(sys, "stdin", reader):
            started = time.monotonic()
            got = session._read_hook_stdin()
            elapsed = time.monotonic() - started
        self.assertEqual(got, {}, "no payload arrived, so there is nothing to report")
        self.assertLess(elapsed, 5, "it must give up, not wait on an EOF that never comes")

    def test_a_real_payload_is_still_read(self):
        """The guard must not cost the thing it guards: a hook that writes and closes is
        read in full, promptly."""
        r, w = os.pipe()
        os.write(w, b'{"session_id": "abc"}')
        os.close(w)                            # a real hook closes; that is the EOF
        reader = os.fdopen(r, "r")
        self.addCleanup(reader.close)
        with mock.patch.object(sys, "stdin", reader):
            got = session._read_hook_stdin()
        self.assertEqual(got, {"session_id": "abc"})

    def test_a_stringio_stdin_still_works(self):
        """Every existing hook test drives this with an `io.StringIO`, which has no real
        fd. That path must keep working or the fix breaks its own callers."""
        with mock.patch.object(sys, "stdin", io.StringIO('{"session_id": "xyz"}')):
            self.assertEqual(session._read_hook_stdin(), {"session_id": "xyz"})


class LandExitStatusTest(WorktreeLaneBase):
    """WI-0263 (d) — a land that landed nothing must not exit 0.

    The measured failure: a lane ran the full gate three times, green every time, printed
    "land: BLOCKED — main kept moving under us after 3 attempts. Nothing landed." and
    exited **0**. Both `cmd_end` and `cmd_merge` discarded the lander's boolean, so every
    caller that reads `$?` — a script, a dispatched lane's wrapper, an agent — was told a
    total failure to land was a success. The WI-0150 lane then burned turns verifying its
    work against a pre-rebase SHA.

    The convention is `cmd_integrate`'s, not a new one: 0 landed or nothing to do, 1
    refused with the trunk untouched, 2 the trunk kept moving.
    """

    def _merge_exit(self):
        """The exit status `python3 session.py merge` would carry, driving the real
        command rather than the lander — the defect lived in the command, so a test that
        stops at the lander cannot see it."""
        # `keep_open` since WI-0288 R2: `merge` closes by default now. The subject of
        # this class is the LANDER's exit contract — the defect WI-0263 measured lived in
        # the land, not the close — so it drives the continuing form, which is the one
        # that reaches the lander without a journal close in front of it. The closing
        # form's own exit codes are the same ones, because it runs through `cmd_end`,
        # whose call sites carry the identical LandOutcome.
        args = argparse.Namespace(dry_run=False, commit=None, push=False, focus=None,
                                  blocked=None, resolve="generated", no_renumber=False,
                                  keep_open=True)
        with mock.patch.object(session, "_on_worktree_lane", return_value=True), \
                mock.patch.object(session, "_refuse_git_on_fixture"), \
                mock.patch.object(session, "role_doc_version", return_value="1.0.0"):
            try:
                session.cmd_merge(args)
            except SystemExit as e:
                return e.code
        return 0

    def test_a_land_that_loses_every_cas_exits_2(self):
        """THE WI-0263 CASE, reproduced rather than described: a competitor advances the
        trunk after every gate, so every attempt loses the swap and nothing lands.

        WI-0266 later made the number of attempts derived rather than fixed at 3, so this
        no longer asserts a count — see the control at the bottom."""
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1600Z-runner-cccc", "starved lane")
        real_gate = session._gate_commit
        runs = {"n": 0}

        def racing_gate(sha, skip=frozenset()):
            # WI-0347 gave `_gate_commit` a per-command skip plan. Passed straight
            # through rather than dropped: this spy is here to race the CAS, not to
            # change what the gate under it decides to run.
            ok, rep = real_gate(sha, skip)
            runs["n"] += 1
            # A competitor lands between our gate and our CAS, every time — the
            # work-item-commit arrival stream WI-0263 measured on the real trunk.
            (self.main / "trunkfile.txt").write_text(
                "competitor %d\n" % runs["n"], encoding="utf-8")
            _git(self.main, "add", "trunkfile.txt")
            _git(self.main, "commit", "-qm", "competitor %d" % runs["n"])
            return ok, rep

        with mock.patch.object(session, "_gate_commit", side_effect=racing_gate):
            code = self._merge_exit()
        self.assertEqual(code, 2,
                         "a land starved out by trunk contention must exit 2, not 0 — "
                         "this is the exact status WI-0263 recorded as a false success")
        self.assertGreater(runs["n"], 1,
                           "control: the gate really did run on more than one attempt, "
                           "so the test exercised the retry loop rather than an early "
                           "return. Deliberately not a count any more: WI-0266 derives "
                           "the budget from the gate's measured cost, and this fixture's "
                           "gate is instant, so a real run here stops at the attempt CAP "
                           "rather than at 3. What THIS test owns is the exit status; "
                           "the budget's size is pinned in tests/test_land_cas_budget.py, "
                           "which is where a change to it should have to argue with a "
                           "test.")

    def test_a_land_blocked_by_its_own_gate_exits_1(self):
        """A red gate is refused-with-the-trunk-untouched, which is 1 — distinct from 2,
        because the two need different responses: fix the work, versus retry the land."""
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1600Z-runner-dddd", "red gate lane")
        with mock.patch.object(session, "_gate_commit",
                               return_value=(False, "gate:    FAIL (exit 1)")):
            self.assertEqual(self._merge_exit(), 1)

    def test_a_successful_land_returns_normally(self):
        """Deliberately NOT `sys.exit(0)`: that still raises `SystemExit`, and both
        commands are called in-process by tests asserting the close completes without
        one. Exiting only on failure keeps every one of those meaningful."""
        self._point_session_at_lane()
        self._write_lane_journal("20260722T1600Z-runner-eeee", "clean lane")
        self.assertEqual(self._merge_exit(), 0)
        self.assertIn("sessions/journal/20260722T1600Z-runner-eeee.md",
                      _files_on(self.main, "main"),
                      "control: the land really did land, so 0 means success here")

    def test_nothing_to_land_is_not_a_failure(self):
        """`integrate`'s rule, kept: 0 covers "landed" and "there was nothing to do".
        A lane with no commits ahead of the trunk has not failed at anything."""
        self._point_session_at_lane()
        self.assertEqual(self._merge_exit(), 0)

    def test_every_lander_return_carries_a_reason(self):
        """The structural half ([`add-structural-guard-on-recurrence`]).

        The defect was one bare `bool` per lander, and there are THREE landers — so a
        fix applied by hand to each is a fix that comes back the next time someone adds
        a return. This walks the AST of all three and refuses any `return` that is not a
        `LandOutcome(...)`, which is a rule a new early-exit cannot pass by accident."""
        import ast
        tree = ast.parse(harness_fixture.harness_source())
        landers = {"_land_candidate", "_land_branch", "_land_worktree_lane"}
        seen = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name in landers):
                continue
            seen.add(node.name)
            for sub in ast.walk(node):
                if not isinstance(sub, ast.Return) or sub.value is None:
                    continue
                self.assertTrue(
                    isinstance(sub.value, ast.Call)
                    and getattr(sub.value.func, "id", "") == "LandOutcome",
                    "%s line %d returns a bare value; every land result must be a "
                    "LandOutcome so the command can carry it into the exit status "
                    "(WI-0263)" % (node.name, sub.lineno))
        self.assertEqual(seen, landers, "control: all three landers were actually walked")

    def test_the_exit_code_mapping(self):
        """The three answers, pinned one at a time."""
        self.assertEqual(session.LandOutcome(True).exit_code, 0)
        self.assertEqual(session.LandOutcome(False, "nothing").exit_code, 0)
        self.assertEqual(session.LandOutcome(False, "contended").exit_code, 2)
        for reason in ("gate", "counter", "conflict", "trunk", "build", "ff-merge"):
            self.assertEqual(session.LandOutcome(False, reason).exit_code, 1, reason)

    def test_a_land_outcome_is_still_a_boolean_to_everyone_who_reads_it_as_one(self):
        """The widening must not disturb the ~45 existing `assertTrue(_land_...())`
        call sites, nor the `bool(landed)` guarding `_dispatch_close_runtime` — which
        SIGTERMs a lane's runtime and must never fire on a failed land."""
        self.assertTrue(session.LandOutcome(True))
        self.assertFalse(session.LandOutcome(False, "contended"))
        self.assertIs(bool(session.LandOutcome(False, "nothing")), False)


class MergeClosesByDefaultTest(WorktreeLaneBase):
    """WI-0288 R2 (1) — `merge` ends the session by DEFAULT and takes an explicit flag
    to carry on.

    The doctrine half landed in 769178b ("open a lane, land one work item, and close")
    while `merge` still left the journal open, and that commit said so on purpose rather
    than shipping a claim the code did not honour. This is the code half.

    WHY THE CLOSING HALF IS GATED. Once `merge` closes, an ungated `merge` is a clean
    bypass of the ADR-0113 guard `end` carries — the same act, one verb over, with no
    receipt. So the close answers to exactly the condition `cmd_end` answers to:
    `--confirm` with what the user said, or a resolved dispatch authorization. The
    CONTINUING half is not a close and needs nothing.
    """

    def _open_journal(self, did, title="(in progress)", narrative=None):
        """An OPEN journal — `_write_lane_journal` stamps `ended`, and a stamped journal
        is refused by the close as already closed.

        `narrative` replaces the opening stub with a line of real session narrative. A
        DISPATCHED close needs it (WI-0340): a dispatch authorizes a close but does not
        evidence one, so `merge`'s closing half refuses a journal that never left the
        stub, exactly as `end` does — the two verbs reach one `cmd_end` and must not
        disagree about what a close requires."""
        session.write_start_journal(did, {
            "session-id": did, "ordinal": 1, "title": title, "machine": "Runner",
            "runtime": session.CLAUDE_RUNTIME, "role-doc-version": "v1.0.0",
            "base-commit": "0" * 12, "started": "2026-07-22T16:00:00+00:00", "ended": "",
            "claude-session-id": "csid-" + did,
        })
        if narrative:
            p = session.journal_path(did)
            fm, body = session.parse_journal(p.read_text(encoding="utf-8"))
            p.write_text(session._rerender_journal(
                fm, body.replace("- Session opened.", narrative)), encoding="utf-8")

    def _merge(self, **kw):
        args = argparse.Namespace(dry_run=False, commit=None, push=False, focus=None,
                                  blocked=None, resolve="generated", no_renumber=False,
                                  keep_open=False, confirm=None, title=None)
        for k, v in kw.items():
            setattr(args, k, v)
        with contextlib.redirect_stdout(io.StringIO()):
            session.cmd_merge(args)

    def _ended_on_trunk(self, did):
        """The `ended:` value on the landed journal, read off the trunk ref — the record
        as anyone else would find it, not the lane's local copy."""
        text = _out(self.main, "show", f"main:sessions/journal/{did}.md")
        for line in text.splitlines():
            if line.startswith("ended:"):
                return line.split(":", 1)[1].strip()
        return ""

    def test_a_plain_merge_closes_the_session(self):
        """The default, and the whole of R2 (1): no flag, and the journal comes back
        stamped."""
        self._point_session_at_lane()
        did = "20260906T1300Z-devbox-0001"
        self._open_journal(did)
        self._merge(confirm="close it out")
        self.assertTrue(self._ended_on_trunk(did),
                        "a plain `merge` must leave the journal ENDED")
        self.assertTrue((self.lane / ".session-state" / f"{did}.end-ran").is_file(),
                        "a closing merge leaves the same end-ran receipt `end` does — "
                        "`a-close-is-the-banner-not-the-sentence`: that receipt is the "
                        "only proof the close ran")

    def test_the_continue_flag_leaves_the_journal_open(self):
        """The explicit opt-out — the behaviour `merge` had unconditionally until now."""
        self._point_session_at_lane()
        did = "20260906T1300Z-devbox-0002"
        self._open_journal(did)
        self._merge(keep_open=True)
        self.assertEqual(self._ended_on_trunk(did), "",
                         "`merge --continue` must leave the journal OPEN")
        self.assertFalse((self.lane / ".session-state" / f"{did}.end-ran").is_file(),
                         "nothing closed, so there is no close receipt to leave")

    def test_a_closing_merge_refuses_without_confirm(self):
        """The gate. A hand-opened lane closing through `merge` asks exactly as it would
        through `end` (ADR-0113 D1) — otherwise the guard is one verb wide."""
        self._point_session_at_lane()
        did = "20260906T1300Z-devbox-0003"
        self._open_journal(did)
        with self.assertRaises(SystemExit) as cm:
            self._merge()
        self.assertIn("--confirm", str(cm.exception))
        self.assertNotIn(f"sessions/journal/{did}.md", _files_on(self.main, "main"),
                         "a refused close lands nothing — the refusal is before the land")

    def test_a_continuing_merge_needs_no_confirm(self):
        """`--continue` is not a close, so the close gate has nothing to say about it."""
        self._point_session_at_lane()
        did = "20260906T1300Z-devbox-0004"
        self._open_journal(did)
        self._merge(keep_open=True)          # no confirm, and this must not raise
        self.assertIn(f"sessions/journal/{did}.md", _files_on(self.main, "main"),
                      "the land itself still happened")

    def test_a_dispatched_lane_closes_on_its_dispatch(self):
        """ADR-0113 D2 carried onto the new verb: a dispatched lane has nobody to ask,
        and its dispatch is the authorization — for `merge`'s close as for `end`'s.

        The journal is AUTHORED here, and that is a second assertion riding along: what
        WI-0340 added is a check on the record, not a narrowing of who may close. A
        dispatched lane that did its work and wrote it down still closes with no
        `--confirm`, through `merge` as through `end`."""
        self._point_session_at_lane()
        did = "20260906T1300Z-devbox-0005"
        self._open_journal(did, narrative="- Landed WI-0288 and wrote it down.")
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-abc123",
                                          "POGA_DISPATCH_ITEM": "WI-0288"}), \
                mock.patch.object(session, "_dispatch_close_runtime"):
            self._merge()
        self.assertTrue(self._ended_on_trunk(did),
                        "a dispatched lane needs no --confirm to close through merge")

    def test_recovery_still_drives_a_merge_that_does_not_close(self):
        """THE REGRESSION THIS CHANGE INVITES, pinned because it would be silent.

        `recover-lanes` lands a lane whose session is provably DEAD by running that
        lane's own `session.py merge` (P16 — one land path, never a weaker copy). Under
        the new default that invocation would try to CLOSE a session nobody is attached
        to and nothing authorized, so it would simply start refusing — in the one code
        path whose entire purpose is that stranded work is not lost. Recovery must ask
        for the continuing form by name."""
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            session._drive_lane_merge(self.lane)
        self.assertIn("--continue", seen["cmd"],
                      "recovery lands a dead lane; it must not attempt a close it has "
                      "no authorization for")


if __name__ == "__main__":
    unittest.main()
