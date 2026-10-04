"""WI-0293 decision 1 — the trunk lock, proved with two real processes.

WHY THIS FILE IS PROCESSES AND NOT THREADS, stated once here because every other
concurrency test in this suite made the opposite choice and was right to.
`tests/test_land_cas_budget.py`'s `ContendedLandTest` says it plainly: `session.ROOT`
and its siblings are module globals, so in-process threads race on them rather than on
the thing under test, and it therefore SIMULATES the competitor and asserts on the number
of lost swaps survived. That was the honest call for a property that is deterministic
in-process. It is not available here. The property this file exists to pin is that two
OS processes cannot both move `refs/heads/<trunk>`, and the mechanism is an `O_EXCL`
create on a shared filesystem path — there is nothing left of it once both contenders
live in one interpreter with one `_TRUNK_LOCK_DEPTH` counter. ADR-0114's author reached
the same conclusion and ran the two-process race by hand, recording it in the ADR prose
instead of the suite (`test_land_cas_budget.py:176`). This is that race, moved into the
suite where a regression can fail it.

A SECOND REASON THE CHILD IS A PROCESS, and it is the better one: `_under_test()` is
`"unittest" in sys.modules`, and `_store_autocommit` returns early under it. A child
launched as a plain `python3` script is therefore NOT under test, so it runs the real
production store-write path — the actual `git commit` — with nothing patched out. The
in-process alternative would have to disable that guard to see anything at all, and would
then be asserting on a path the operator never runs.

THE THREE PROPERTIES, and each one fails differently:

  A. A store write cannot advance the trunk while a land holds the trunk lock.
     This is the theft WI-0272 observed live. RED before this change.
  B. A store write does NOT wait for the LAND GATE. This one is green before the change
     and must stay green: WI-0272 warns in its own text that putting store writes behind
     the land gate "turns the cheapest verb in the system into the most expensive", so a
     fix that bought property A by making `poga work` queue behind a suite would be a
     regression wearing a fix's clothes. Nothing else in the suite would catch it.
  C. The land takes the lock too. A lock only one side honours is not a lock, and the
     store side alone would still let a land walk into a store write's commit.
"""
from __future__ import annotations

import contextlib
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
from datetime import timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402
from ambient_fixture import AMBIENT_VARS  # noqa: E402

GIT = shutil.which("git")
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# The child's whole job, kept in-repo rather than written at run time so it is greppable
# and so a syntax error in it is a normal review defect rather than a runtime surprise.
# It takes the repo to act on and the mode, and prints one JSON line on stdout: what the
# trunk was before, what it was after, and the wall-clock the call took. The parent
# asserts on those three facts and nothing else.
DRIVER = r'''
import datetime, json, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
import session

repo, mode = pathlib.Path(sys.argv[2]), sys.argv[3]
session.ROOT = repo
session.CFG = {"trunk": "main", "architect_name": "Test", "architect_id": "test-arch",
               "machine_map": {}, "tz": datetime.timezone.utc}
session.CFG_RAW = {"trunk": "main"}
# The import-time anchors, which `ROOT` does not move: without these the child's
# `_coord_holder` globs the RUNNER'S `sessions/journal` (ADR-0148 D3 store guard).
session.JOURNAL_DIR = repo / "sessions" / "journal"
session.ARCHIVE = repo / "sessions" / "pre-journal-archive.md"
session.SESSION_STATE_DIR = repo / ".session-state"

def tip():
    r = session.sh(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet",
                    "refs/heads/main"], check=False)
    return (r.stdout or "").strip()

before, started = tip(), time.time()
# Imports done, the timed call is next. A parent timing a hold waits for this, so the
# child's start-up cannot eat into the window it asserts on.
(repo.parent / ("ready-" + mode)).write_text("", encoding="utf-8")
if mode == "store-write":
    name = sys.argv[4]
    (repo / "work-items" / name).write_text("# a store write\n", encoding="utf-8")
    session._store_autocommit("work-items", [name],
                              "chore(work-items): a store write", "poga work commit")
elif mode == "hold-trunk-lock-and-commit":
    with session.trunk_lock():
        (repo / "held.txt").write_text("held\n", encoding="utf-8")
        session.sh(["git", "-C", str(repo), "add", "--", "held.txt"], check=False)
        session.sh(["git", "-C", str(repo), "commit", "-qm",
                    "chore: a trunk write under the lock"], check=False)
        time.sleep(float(sys.argv[4]))
else:
    raise SystemExit("unknown mode " + mode)
print(json.dumps({"before": before, "after": tip(),
                  "elapsed": time.time() - started}))
'''


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _tip(repo: pathlib.Path) -> str:
    return _git(repo, "rev-parse", "refs/heads/main")


@unittest.skipUnless(GIT, "git not available")
class TrunkLockRaceTest(unittest.TestCase):
    """Two real processes against one real coordination store."""

    def setUp(self):
        # Both are mandatory for anything touching `_coord_*` — see `coord_fixture`, and
        # the guards in test_coord_fixture_guard.py / test_dispatch_fixture_guard.py that
        # fail the suite if a module in this area omits them. `neutralize_dispatch_env`
        # matters more than usual here: `POGA_INVOKED_FROM` would send `_coord_dir` at the
        # operator's real checkout, and this test writes lock records.
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="trunk-lock-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "work-items").mkdir(parents=True)
        _git_init = [GIT, "init", "-q", "-b", "main", str(self.repo)]
        subprocess.run(_git_init, check=True, capture_output=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            _git(self.repo, "config", k, v)
        (self.repo / "work-items" / ".keep").write_text("", encoding="utf-8")
        (self.repo / "sessions" / "journal").mkdir(parents=True)
        (self.repo / "sessions" / "journal" / ".keep").write_text("", encoding="utf-8")
        (self.repo / "sessions" / "pre-journal-archive.md").write_text(
            "# archive\n", encoding="utf-8")
        (self.repo / ".gitignore").write_text(
            ".claude/worktrees/\n.session-state/\n", encoding="utf-8")
        (self.repo / "STATUS.md").write_text("---\nversion: 1.0.0\n---\n",
                                             encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "init")

        self.driver = self.tmp / "driver.py"
        self.driver.write_text(DRIVER, encoding="utf-8")

        # Point THIS process at the temp repo too, so parent and child resolve the same
        # `<repo>/.git/poga-coord`. `sh()` defaults cwd to ROOT and `_git_common_dir`
        # passes no `-C`, so ROOT is the whole redirection.
        self._saved = {k: getattr(session, k) for k in
                       ("ROOT", "CFG", "CFG_RAW", "JOURNAL_DIR", "ARCHIVE",
                        "SESSION_STATE_DIR", "CONFIG_PATH")}
        self.addCleanup(lambda: [setattr(session, k, v)
                                 for k, v in self._saved.items()])
        session.ROOT = self.repo
        session.CFG = {"trunk": "main", "architect_name": "Test",
                       "architect_id": "test-arch", "machine_map": {},
                       "tz": timezone.utc}
        session.CFG_RAW = {"trunk": "main"}
        session._SHARED_WORK_ROOT = {}

    def lane(self, n: int) -> pathlib.Path:
        path = self.repo / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.repo, "worktree", "add", "-q", "-b", f"worktree-poga-{n}",
             str(path), "main")
        return path

    def point_at(self, lane: pathlib.Path) -> None:
        session.ROOT = lane
        session.JOURNAL_DIR = lane / "sessions" / "journal"
        session.ARCHIVE = lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = lane / ".session-state"
        session.CONFIG_PATH = lane / "session.config.json"
        session.CFG = {"trunk": "main", "branch_sessions": False,
                       "architect_name": "Test", "architect_id": "test-arch",
                       "machine_map": {}, "tz": timezone.utc,
                       "gate": [["python3", "-c", "import sys; sys.exit(0)"]]}
        session.CFG_RAW = {"trunk": "main"}

    def _spawn(self, mode: str, *extra: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, str(self.driver), str(REPO_ROOT), str(self.repo),
             mode, *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            cwd=str(self.repo),
            env={**{k: v for k, v in os.environ.items() if k not in AMBIENT_VARS},
                 "PYTHONDONTWRITEBYTECODE": "1"})

    def _reap(self, proc: subprocess.Popen) -> dict:
        out, err = proc.communicate(timeout=120)
        self.assertEqual(proc.returncode, 0, f"child failed: {err}")
        return json.loads(out.strip().splitlines()[-1])

    # ── A ────────────────────────────────────────────────────────────────────────
    def test_a_store_write_cannot_move_the_trunk_inside_a_held_trunk_lock(self):
        """The theft, pinned. RED before WI-0293: `_store_autocommit` took no lock and
        did no CAS, so it advanced the trunk the instant it was asked to, and a land
        that had already proved its swap available lost it between the proving and the
        doing."""
        hold = 2.0
        before = _tip(self.repo)
        with session.trunk_lock() as locked:
            self.assertTrue(locked, "the parent must really hold it for this to mean "
                                    "anything — a fail-open hold proves nothing")
            child = self._spawn("store-write", "WI-9999-racer.md")
            # Start the hold only once the child is past its imports. Timed from spawn,
            # a loaded run (the 2026-10-02 lane suite) spent the first half in start-up
            # and the child's own clock read 0.96s against the 1.0s floor below.
            ready = self.tmp / "ready-store-write"
            deadline = time.time() + 60
            while not ready.exists() and child.poll() is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), "the child never reached the store write")
            # Give the child unambiguously long enough to have run to completion if
            # nothing were stopping it. A store write is milliseconds; this is ~50x that.
            time.sleep(hold / 2)
            during = _tip(self.repo)
            self.assertEqual(during, before,
                             "a store write moved the trunk while a land held the trunk "
                             "lock — this is WI-0272's stolen swap")
            self.assertIsNone(child.poll(),
                              "the store write finished while the lock was held; it "
                              "cannot have waited for it")
            time.sleep(hold / 2)
        result = self._reap(child)
        after = _tip(self.repo)
        self.assertNotEqual(after, before, "the store write must still land, just later")
        self.assertEqual(result["after"], after)
        # It waited for US — and that needs BOTH bounds. The lower one alone passed a
        # mutation that never released the lock at all: the child then waits out the full
        # 30s cap, fails open, and commits anyway, which satisfies "it waited" and
        # "it eventually landed" while the lock is in fact broken. The upper bound is what
        # distinguishes "handed over promptly" from "gave up".
        self.assertGreaterEqual(result["elapsed"], hold / 2,
                                "the child returned too fast to have waited on the lock")
        self.assertLess(
            result["elapsed"], session.TRUNK_LOCK_MAX_WAIT_SECONDS / 2,
            f"the child waited {result['elapsed']:.1f}s — long enough that it hit the "
            f"{session.TRUNK_LOCK_MAX_WAIT_SECONDS}s fail-open cap rather than being "
            f"handed the lock when we released it")

    # ── B ────────────────────────────────────────────────────────────────────────
    def test_b_a_store_write_does_not_wait_for_the_land_gate(self):
        """The anti-regression guard, and the reason there are two locks rather than one.

        WI-0272: making store writes take the land gate "would be wrong … a `poga work`
        write would then block for minutes behind a gate run, which turns the cheapest
        verb in the system into the most expensive". This test is what makes that
        sentence enforceable. It passes today and must keep passing."""
        # The bound is the child's own `elapsed`, which brackets the write alone. It used
        # to be the parent's clock from spawn, a fixed 3.0s. That clock also counted the
        # interpreter start and `import session`, so the loaded 2026-10-02 nightly read
        # 3.05s with no lock wait at all. A write that took the gate would fail its first
        # try and sleep a full poll before the next, so one poll is the shortest a
        # serialized write can take. A write that waits forever is caught by `_reap`.
        with session.land_gate_lock(emit=lambda *_a, **_k: None) as gate:
            self.assertTrue(gate.serialized, "the parent must really hold the land gate")
            child = self._spawn("store-write", "WI-9998-fast.md")
            result = self._reap(child)
            waited = result["elapsed"]
            self.assertLess(
                waited, session.LAND_WAIT_POLL_SECONDS,
                f"a store write waited {waited:.2f}s while only the LAND GATE was held — "
                f"it has been serialized against the suite, which is the fix WI-0272 "
                f"warns against")
        self.assertNotEqual(result["after"], result["before"],
                            "the store write should have committed immediately")

    # ── C ─────────────────────────────────────────────────────────────────────────────
    def test_c_the_real_land_swap_waits_for_a_held_trunk_lock(self):
        """A lock only one side honours is not a lock.

        THIS DRIVES THE REAL LANDER, and the first version of this test did not — it
        called `trunk_lock()` in the test process and asserted that it waited, which is a
        test of the primitive dressed up as a test of the caller. Deleting the lock from
        the land's swap site left it passing; the mutation caught the test, not the code.
        `_land_worktree_lane` is the only thing that proves the land side participates."""
        lane = self.lane(1)
        (lane / "lane-work.txt").write_text("lane work\n", encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", "work: lane-work.txt")

        hold = 2.0
        child = self._spawn("hold-trunk-lock-and-commit", str(hold))
        rec = self.repo / ".git" / "poga-coord" / session.TRUNK_LOCK_KIND / \
            f"{session.TRUNK_LOCK_NAME}.json"
        deadline = time.time() + 30
        while not rec.exists() and time.time() < deadline:
            time.sleep(0.01)
        self.assertTrue(rec.exists(), "the child never took the trunk lock")

        self.point_at(lane)
        started = time.time()
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=False)
        waited = time.time() - started
        self._reap(child)
        self.assertTrue(outcome, f"the land should still land: {buf.getvalue()}")
        self.assertGreaterEqual(
            waited, hold / 2,
            f"the real land swap completed in {waited:.2f}s while the child held the "
            f"trunk lock for {hold}s — the land side is not taking the lock")
        on_main = set(_git(self.repo, "ls-tree", "-r", "--name-only", "main").split())
        self.assertIn("lane-work.txt", on_main)
        self.assertIn("held.txt", on_main, "the child's own trunk write must survive")

    def test_c2_the_lock_is_reentrant_within_one_process(self):
        """A land holds this around its swap. If any nested step ever reaches a store
        write, a non-reentrant lock would deadlock the process against itself for the
        full 30s cap and turn a successful land into a hang — ADR-0114 D6's defect,
        rebuilt from new parts. Identity cannot substitute for the depth counter: a lane
        running `session.py wi-new` directly shares its session id AND its journal with
        its own land, so an identity check cannot tell "myself, nested" from "my sibling,
        racing"."""
        with session.trunk_lock() as outer:
            self.assertTrue(outer)
            started = time.time()
            with session.trunk_lock() as inner:
                self.assertTrue(inner)
            self.assertLess(time.time() - started, 1.0,
                            "the nested acquire queued behind its own outer hold")


if __name__ == "__main__":
    unittest.main()
