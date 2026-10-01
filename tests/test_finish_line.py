"""The finish line — seven capability-level statements, each pinned end to end.

The rest of this suite pins PIECES: a gate lock, a claim, a brief parser, a CAS advance.
None of it says whether the harness, as a whole, does the seven things the federation
exists to do. This file does, one class per capability, each docstring the sentence it
pins. When all seven hold and the scoreboard's exit criteria hold
(`curate/finish_line.py`), the harness is mature by definition; until then it is not,
and "harness work" means an item that makes one of these fail.

Temp repos throughout, against the real `session.py`, `bootstrap.py`, and curate
modules. Two deliberate exceptions, both in FL1, because it drives the REAL
`bootstrap.py --adopt`: it writes a pid-named throwaway profile under `users/`, and
adopt files a roster brief into `proposed-edits/federation-arch/pending/`. In a lane
BOTH of those paths are symlinks to the shared main checkout, so both are cleaned up
here by name — an uncleaned roster brief would surface at the next federation session
start as a request to put a fictional system on the roster. FL1 also sets `repo_owner`
in its spec so `detect_gh_account()` never shells out to `gh`; with that set, the suite
is offline. Nothing else touches the live checkout, the coordination store, or a
journal. Anything that can only be answered against the
live fleet (byte identity across members, the injected budget of a real start, the
created-vs-closed trend) lives in `curate/finish_line.py`, which REPORTS and never
blocks a land — a finish line that fails the suite would stop the work that reaches it.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import contextlib
import datetime as _dt
import importlib.util
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

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "curate"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import gather  # noqa: E402
import distill  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from member_fixture import cfg as member_cfg, make_repo  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "standard_version", ROOT / "curate" / "standard_version.py")
sv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sv)

GIT = shutil.which("git")
PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _out(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


def _files_on(repo, ref):
    r = subprocess.run([GIT, "-C", str(repo), "ls-tree", "-r", "--name-only", ref],
                       capture_output=True, text=True)
    return set(r.stdout.split())


def _init_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    _git(path, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        p = path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "init")
    _git(path, "branch", "-M", "main")


# The smallest repo `session.py` will land a lane in: the views it recompiles, a
# journal dir, a role doc carrying a version line, and the lane pool ignored.
_MEMBER_FILES = {
    ".gitignore": ".claude/worktrees/\n.session-state/\n",
    "role.md": "**Version:** v1.0.0\n",
    "sessions/pre-journal-archive.md": "",
    "sessions/journal/.keep": "",
    "STATUS.md": ("---\nid: test\nversion: v0\nlast_active: 2026-01-01\n"
                  "focus: x\nblocked: false\n---\n"),
    "trunkfile.txt": "base\n",
}


class _LaneRepo(unittest.TestCase):
    """A main checkout plus N linked worktree lanes, and `session` pointed wherever a
    test needs it. The same shape `test_worktree_lane.py` uses, without inheriting
    its class so this file stays readable on its own."""

    def setUp(self):
        # WI-0432. A KNOWN identity, not the launcher's: with no session id,
        # `_find_holder_journal` falls back to the journals of `Path.cwd()` — the REAL
        # checkout — so under launchd (no Claude session) FL5 read the live store and
        # the sharded gate's store guard failed it, while every in-session run passed.
        # A fictional id matches no fixture journal, which is what those runs saw.
        neutralize_ambient_env(self)
        os.environ["CLAUDE_CODE_SESSION_ID"] = "fixture-finish-line"
        neutralize_coord_journal(self)
        neutralize_dispatch_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "repo"
        _init_repo(self.main, _MEMBER_FILES)
        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "ARCHIVE", "SESSION_STATE_DIR",
                       "CONFIG_PATH", "CFG")}
        for k, v in self._save.items():
            self.addCleanup(setattr, session, k, v)

    def lane(self, n):
        path = self.main / ".claude" / "worktrees" / f"poga-{n}"
        _git(self.main, "worktree", "add", "-q", "-b", f"worktree-poga-{n}",
             str(path), "main")
        return path

    def point_at(self, lane):
        session.ROOT = lane
        session.JOURNAL_DIR = lane / "sessions" / "journal"
        session.ARCHIVE = lane / "sessions" / "pre-journal-archive.md"
        session.SESSION_STATE_DIR = lane / ".session-state"
        session.CONFIG_PATH = lane / "session.config.json"
        session.CFG = {
            "handoff": lane / "session-handoff.md",
            "status": lane / "STATUS.md",
            "role_doc": lane / "role.md",
            "tz": ZoneInfo("UTC"),
            "architect_name": "Test Architect", "architect_id": "test-arch",
            "machine_map": {}, "user_name": "operator", "inbox": None,
            "trunk": "main", "branch_sessions": False, "gate": PASS_GATE,
        }

    def stage(self, lane, rel, text="lane work\n"):
        p = lane / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        _git(lane, "add", "-A")
        _git(lane, "commit", "-qm", f"work: {rel}")

    def land(self, lane, gate=None):
        """Land `lane`. `gate` REPLACES the gate for this land only.

        The parameter exists because `point_at` rebuilds `session.CFG` wholesale, including
        `"gate": PASS_GATE` — so a caller that set `session.CFG["gate"]` before calling this
        had its choice silently discarded on the first line. FL7 did exactly that and
        therefore never once ran the slow gate it was written to measure (WI-0397)."""
        self.point_at(lane)
        if gate is not None:
            session.CFG["gate"] = gate
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome = session._land_worktree_lane(None, None, "1.0.0", push=False)
        return outcome, buf.getvalue()


# =============================================================================
# 1. BOOTSTRAP — empty folder to the first landed work item, one command + one session.
# =============================================================================

@unittest.skipUnless(GIT, "git not available")
class FL1_Bootstrap(_LaneRepo):
    """A system with real content and no Architect is adopted by ONE `bootstrap.py`
    command, and the substrate it installs can land that system's first work item
    from a lane without any further hand-installed piece."""

    def setUp(self):
        super().setUp()
        # THE FEDERATION ROOT BOOTSTRAP RUNS FROM IS A FIXTURE (ADR-0148 D3, WI-0427).
        # `bootstrap.py` takes its federation root from its own file location, and
        # `adopt` reads the profile from `<root>/users/` and files a roster brief into
        # `<root>/proposed-edits/federation-arch/pending/` (bootstrap.py
        # `request_portfolio_registration`). Run from the real checkout, that wrote into
        # the operator's live inbox — in a lane a symlink to the main checkout, the inbox
        # the session-start sweep reads — and the test had to snapshot-and-sweep it. The
        # store guard fails that read outright, because a bookkeeping-only land skips the
        # suite. So: a COPY of bootstrap.py in a tmp root, every other top-level entry
        # symlinked to the real one EXCEPT the bookkeeping store and the data
        # directories adopt writes. A symlinked store entry would hide a live read from
        # the guard's path match, hence withholding rather than linking it.
        import store_guard
        self.fed = self.tmp / "federation"
        self.fed.mkdir()
        withheld = {p.split("/")[0] for p in store_guard.bookkeeping_paths(ROOT)}
        withheld |= {"bootstrap.py", "users", "proposed-edits", ".git",
                     ".session-state", ".claude"}
        for entry in ROOT.iterdir():
            if entry.name not in withheld:
                (self.fed / entry.name).symlink_to(entry)
        shutil.copyfile(ROOT / "bootstrap.py", self.fed / "bootstrap.py")
        self.user_id = f"_test_fl_user_{os.getpid()}"
        prof = self.fed / "users" / self.user_id
        prof.mkdir(parents=True)
        (prof / "profile.md").write_text("# test profile\n", encoding="utf-8")
        self.target = self.tmp / "adopted-system"
        _init_repo(self.target, {"server.js": "// the system, already built\n"})
        self.spec = self.tmp / "spec.json"
        self.spec.write_text(json.dumps({
            "system_name": "Finish Line Sys", "user_id": self.user_id,
            "user_name": "operator", "federation_repo_url": "https://example/blob/main",
            # Pinned so `adopt` uses it instead of calling `detect_gh_account()`, which
            # shells out to `gh auth status` (bootstrap.py:416, :732). Without this the
            # suite is not offline.
            "repo_owner": "test-owner",
            "mission_prose": "Keep the thing running.",
        }), encoding="utf-8")

    def _adopt(self):
        return subprocess.run(
            [sys.executable, str(self.fed / "bootstrap.py"), "--spec", str(self.spec),
             "--adopt", str(self.target)],
            capture_output=True, text=True, cwd=str(self.fed))

    def test_one_command_installs_a_substrate_that_lands_the_first_work_item(self):
        proc = self._adopt()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The roster brief went to the FIXTURE federation's inbox — the proof the run
        # never reached the operator's.
        self.assertTrue(list((self.fed / "proposed-edits" / "federation-arch" / "pending")
                             .glob("*-register-*.md")),
                        "adopt filed no roster brief in the fixture federation's inbox")
        for marker in ("session.py", "CANON.md", "STANDARD.md", "session.config.json"):
            self.assertTrue((self.target / marker).is_file(), f"adopt must install {marker}")
        # The installed harness is the federation's, byte for byte (ADR-0023).
        self.assertEqual((self.target / "session.py").read_bytes(),
                         (ROOT / "session.py").read_bytes())
        # The adoption is committed, not left dirty in someone's tree.
        self.assertEqual(_out(self.target, "status", "--porcelain"), "")

        # Now the first work item, from a lane, through the installed harness's land.
        self.main = self.target
        lane = self.lane(1)
        self.stage(lane, "work-items/WI-0001-first-item.md",
                   "# WI-0001: first item\n\n- status: done\n- section: next\n")
        outcome, out = self.land(lane)
        self.assertTrue(outcome, f"first item should land: {out}")
        self.assertIn("work-items/WI-0001-first-item.md", _files_on(self.target, "main"))


# =============================================================================
# 2. CURATE — a learning written by any member surfaces for review, verbatim.
# =============================================================================

class FL2_Curate(unittest.TestCase):
    """A learning appended to a producer file is laid out in the next curate REVIEW
    verbatim, and is not marked reviewed until an accept names that exact review."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "curate").mkdir()
        (self.tmp / "inputs").mkdir()
        self._saved = (gather.ROOT, gather.SEEN_PATH, gather.RUNS_DIR)
        self.addCleanup(lambda: setattr(gather, "ROOT", self._saved[0]))
        self.addCleanup(lambda: setattr(gather, "SEEN_PATH", self._saved[1]))
        self.addCleanup(lambda: setattr(gather, "RUNS_DIR", self._saved[2]))
        gather.ROOT = self.tmp
        gather.SEEN_PATH = self.tmp / "curate" / "seen.json"
        gather.RUNS_DIR = self.tmp / "curate-runs"

    def _run(self, *argv):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["gather.py", *argv]), \
             contextlib.redirect_stdout(buf):
            gather.main()
        return buf.getvalue()

    def test_a_member_learning_surfaces_for_review_and_stays_until_accepted(self):
        # A member's mirrored producer file, not the federation's own.
        member = self.tmp / "inputs" / "example-app-learnings.md"
        member.write_text("## 2026-09-04 — lanes that block on a question go idle\n\n"
                          "Body: park the question on the item and end.\n",
                          encoding="utf-8")
        self._run()
        reviews = sorted(gather.RUNS_DIR.glob("REVIEW-*.md"))
        self.assertEqual(len(reviews), 1, "one gather, one review")
        text = reviews[0].read_text(encoding="utf-8")
        self.assertIn("lanes that block on a question go idle", text)
        self.assertIn("park the question on the item and end.", text)
        # Not reviewed yet — it must surface again until an accept names this review.
        self.assertEqual([e["title"] for e in gather.gather()],
                         ["lanes that block on a question go idle"])
        self._run("--accept")
        self.assertEqual(gather.gather(), [])


# =============================================================================
# 3. REDISTRIBUTE — an accepted canon change reaches a member's injected context.
# =============================================================================

class FL3_Redistribute(unittest.TestCase):
    """A principle accepted in the federation's registry is in the generated CANON.md,
    the kit copy stays identical to it, and a member whose CANON.md is that file has
    the new line in what `start` injects — with no hand edit anywhere."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        fed = self.tmp / "federation"
        (fed / "principles").mkdir(parents=True)
        (fed / "habits").mkdir()
        (fed / "bootstrap-kit").mkdir()
        self._saved = {k: getattr(distill, k) for k in
                       ("ROOT", "PRINCIPLES", "HABITS", "OUT", "KIT_OUT", "TARGETS")}
        for k, v in self._saved.items():
            self.addCleanup(setattr, distill, k, v)
        distill.ROOT = fed
        distill.PRINCIPLES = fed / "principles" / "master.md"
        distill.HABITS = fed / "habits" / "master.md"
        distill.OUT = fed / "CANON.md"
        distill.KIT_OUT = fed / "bootstrap-kit" / "CANON.md"
        distill.TARGETS = [(distill.OUT, "federation"), (distill.KIT_OUT, "architect")]
        distill.PRINCIPLES.write_text(
            "# Principles\n\n## P1 — one-item-one-session\n\n- **Status:** Accepted\n\n"
            "**Statement.** A session takes one item to the trunk and ends.\n", encoding="utf-8")
        distill.HABITS.write_text(
            "# Universal habits\n\n## park-dont-block\n\n- **Status:** Accepted\n"
            "- **Parent:** [P1 — one-item-one-session](../principles/master.md#p1)\n\n"
            "**Statement.** A lane that needs a decision parks it and ends.\n", encoding="utf-8")
        self.member = self.tmp / "member"
        self.member.mkdir()
        self.addCleanup(setattr, session, "CANON_PATH", session.CANON_PATH)

    def _distill(self):
        with mock.patch.object(sys, "argv", ["distill.py"]), \
             contextlib.redirect_stdout(io.StringIO()):
            distill.main()

    def test_an_accepted_principle_reaches_the_members_injected_canon(self):
        self._distill()
        canon = distill.OUT.read_text(encoding="utf-8")
        self.assertIn("one-item-one-session", canon)
        self.assertIn("park-dont-block", canon)
        # The kit ships the same digest under its own header — a fresh member cannot
        # start one line behind an existing one.
        kit = distill.KIT_OUT.read_text(encoding="utf-8")
        self.assertIn("one-item-one-session", kit)
        self.assertIn("park-dont-block", kit)
        self.assertEqual(kit.split("\n", 1)[1].split("\n\n", 1)[1],
                         canon.split("\n", 1)[1].split("\n\n", 1)[1],
                         "kit and root digests differ only in their header paragraph")
        # The member holds the generated file (what push-substrate copies, byte for byte)
        # and `start`'s injection carries it.
        (self.member / "CANON.md").write_bytes(distill.OUT.read_bytes())
        session.CANON_PATH = self.member / "CANON.md"
        injected = session._canon_block()
        self.assertIn("one-item-one-session", injected)
        self.assertIn("park-dont-block", injected)

    def test_a_class_bound_habit_never_leaks_into_the_universal_digest(self):
        distill.HABITS.write_text(
            distill.HABITS.read_text(encoding="utf-8") +
            "\n---\n\n## cloud-only-thing\n\n- **Status:** Accepted\n"
            "- **Parent:** [P1 — one-item-one-session](../principles/master.md#p1)\n"
            "- **Binds-to:** cloud-deployed\n\n**Statement.** Not for everyone.\n",
            encoding="utf-8")
        self._distill()
        self.assertNotIn("cloud-only-thing", distill.OUT.read_text(encoding="utf-8"))


# =============================================================================
# 4. STANDARDIZE — one substrate, byte-identical, and briefs adopt themselves.
# =============================================================================

class FL4_Standardize(unittest.TestCase):
    """The fleet parity tool reads a member whose harness matches the federation's as
    current and one that differs as stale — the two axes it must never conflate —
    and a conforming brief in a member's inbox applies literally with no hand."""

    def test_byte_identity_is_the_parity_tools_verdict_not_a_version_string(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            r = sv.evaluate_member(repo, member_cfg())
            self.assertTrue(r["harness_stale"], "a differing session.py reads stale")
            self.assertEqual(r["status"], "clean", "capability version is a separate axis")
            (repo / "session.py").write_bytes((ROOT / "session.py").read_bytes())
            r = sv.evaluate_member(repo, member_cfg())
            self.assertFalse(r["harness_stale"], "the federation's own bytes read current")

    def test_a_conforming_brief_applies_itself_and_a_drifted_one_only_surfaces(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        target = tmp / "member-arch.md"
        target.write_text("\n**Version:** 1.0.0\n**Status:** Active\n\n\n"
                          "Original anchor text here.\n\n\n- **1.0.0** (2026-01-01) — initial.\n",
                          encoding="utf-8")
        brief = tmp / "pending" / "2026-09-04-x.md"
        brief.parent.mkdir()
        header = ("---\nedit-id: 2026-09-04-x\ntarget-file: member-arch.md\n"
                  "expected-base-version: {base}\nproposed-new-version: 1.1.0\napply: auto\n---\n\n")
        ops = ("## op: version-bump\n\n## op: replace\n~~~before\nOriginal anchor text here.\n~~~\n"
               "~~~after\nReplaced text here.\n~~~\n")
        saved = session.ROOT
        self.addCleanup(setattr, session, "ROOT", saved)
        session.ROOT = tmp
        brief.write_text(header.format(base="1.0.0") + ops, encoding="utf-8")
        plan = session.evaluate_brief(brief)
        self.assertEqual(plan.action, "apply", plan.reason)
        out = plan.touched[target.resolve()]
        self.assertIn("Replaced text here.", out)
        self.assertIn("**Version:** 1.1.0", out)
        # Version drift: the same brief against a target that moved on SURFACES, untouched.
        brief.write_text(header.format(base="0.9.0") + ops, encoding="utf-8")
        plan = session.evaluate_brief(brief)
        self.assertEqual(plan.action, "surface")
        # Assert the PLAN is empty, not that the file on disk is unchanged: `evaluate_brief`
        # is documented "Returns a Plan; never writes" (session.py:18166), so a disk
        # assertion here passes whatever the plan says. `touched` is what an apply would
        # write, and a surface must propose no write at all.
        self.assertFalse(plan.touched, "a surfaced brief proposes no write")


# =============================================================================
# 5. SESSION HARNESS — one item to the trunk, safe beside other lanes and a kill.
# =============================================================================

@unittest.skipUnless(GIT, "git not available")
class FL5_SessionHarness(_LaneRepo):
    """Three lanes that diverged from the same trunk all land, each rebased over the
    ones before it, with every commit reachable and nothing blended; and a lane whose
    session died mid-work is landed by recovery through the lane's OWN merge — the
    same path a live session uses, not a second one."""

    def test_three_diverged_lanes_all_land_and_nothing_is_lost(self):
        lanes = [self.lane(n) for n in (1, 2, 3)]
        for n, lane in enumerate(lanes, 1):
            self.stage(lane, f"lane{n}.txt", f"work from lane {n}\n")
        tips = {n: _out(lane, "rev-parse", "HEAD") for n, lane in enumerate(lanes, 1)}
        base = _out(self.main, "rev-parse", "main")
        for n, lane in enumerate(lanes, 1):
            outcome, out = self.land(lane)
            self.assertTrue(outcome, f"lane {n} should land over the lanes before it: {out}")
        on_main = _files_on(self.main, "main")
        for n in (1, 2, 3):
            self.assertIn(f"lane{n}.txt", on_main)
        # Exactly three commits landed on top of the base — one per lane, no blend.
        landed = _out(self.main, "rev-list", "--count", f"{base}..main")
        self.assertEqual(landed, "3")
        # A lane rebased over another still carries ITS OWN change as its own commit.
        for n in (2, 3):
            subject = _out(self.main, "log", "--format=%s", "-1", "--", f"lane{n}.txt")
            self.assertEqual(subject, f"work: lane{n}.txt")
        # Lane 1 landed first onto an unmoved trunk: a pure fast-forward of its own tip.
        first_landed = _out(self.main, "rev-list", "--reverse", f"{base}..main").split()[0]
        self.assertEqual(first_landed, tips[1])

    def test_a_lane_whose_session_died_is_recovered_through_its_own_merge(self):
        lane = self.lane(1)
        self.stage(lane, "stranded.txt", "work nobody will come back for\n")
        # The liveness sidecar the heartbeat hook leaves behind: a beat, then an exit.
        sc = lane / ".session-state"
        sc.mkdir(parents=True, exist_ok=True)
        (sc / "deadbeef.live").write_text(json.dumps({"last_beat": "2026-01-01T00:00:00+00:00"}),
                                          encoding="utf-8")
        (sc / "deadbeef.ended").write_text(json.dumps({"ended": "2026-01-01T00:01:00+00:00"}),
                                           encoding="utf-8")
        # Recovery runs from the MAIN checkout, and must drive the lane's own harness.
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch", "trunk": "main"}
        session.CFG_RAW = {"trunk": "main"}
        driven = []

        def drive(path):
            driven.append(pathlib.Path(path).resolve())
            # The lane's own land, in the lane — what `session.py merge` there does.
            self.point_at(lane)
            with contextlib.redirect_stdout(io.StringIO()):
                ok = session._land_worktree_lane(None, None, "1.0.0", push=False)
            # Both halves must match what `cmd_recover_lanes` actually reads:
            # `landed = proc.returncode == 0 and "land:    BLOCKED" not in body`
            # (session.py:15612). The four-space sentinel and a non-zero code are the
            # real lane's failure shape; with a 0 code and a one-space string the
            # RECOVERED assertion below passes on both branches and pins nothing.
            return (subprocess.CompletedProcess([], 0, "land:    ok\n", "") if ok
                    else subprocess.CompletedProcess([], 1, "land:    BLOCKED\n", ""))

        buf = io.StringIO()
        with mock.patch.object(session, "_drive_lane_merge", side_effect=drive), \
             contextlib.redirect_stdout(buf):
            session.cmd_recover_lanes(argparse.Namespace(dry_run=False, lane=None))
        self.assertEqual(driven, [lane.resolve()], "recovery drives the dead lane, once")
        self.assertIn("RECOVERED", buf.getvalue())
        self.assertIn("stranded.txt", _files_on(self.main, "main"))


# =============================================================================
# 6. OBSERVABILITY — the ledger the scoreboard reads is computed, not remembered.
# =============================================================================

class FL6_Observability(unittest.TestCase):
    """Tokens, context-per-turn, and idle share come from the runtime's own transcripts,
    and the scoreboard's verdicts are functions of numbers it can show."""

    def test_the_token_ledger_reads_a_transcript_and_the_scoreboard_judges_it(self):
        import token_ledger
        import finish_line
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proj = tmp / "-Users-x-Projects-thing--claude-worktrees-poga-1"
        proj.mkdir()
        lines = []
        # Three turns: a cheap start, a mid-session turn, and one after a long idle gap.
        for i, (ts, ctx) in enumerate([("2026-09-04T10:00:00Z", 30_000),
                                       ("2026-09-04T10:05:00Z", 120_000),
                                       ("2026-09-04T11:05:00Z", 250_000)]):
            lines.append(json.dumps({
                "type": "assistant", "timestamp": ts,
                "message": {"model": "claude-opus-5",
                            "usage": {"input_tokens": 0, "cache_creation_input_tokens": 1_000,
                                      "cache_read_input_tokens": ctx - 1_000, "output_tokens": 500},
                            "content": [{"type": "tool_use", "name": "Bash", "id": f"t{i}",
                                         "input": {"command": "ls"}}]}}))
        (proj / "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        rows = token_ledger.read_sessions(tmp, days=10_000)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["project"], "thing")
        self.assertEqual(r["turns"], 3)
        self.assertEqual(r["first_ctx"], 30_000)
        self.assertEqual(r["ctx_median"], 120_000)
        self.assertEqual(r["tokens"], 30_000 + 120_000 + 250_000 + 3 * 500)
        self.assertEqual(r["tools"]["Bash"], 3)
        # One hour of wall clock, of which the 60-minute gap is idle (>10 min).
        self.assertAlmostEqual(r["wall_s"], 3900, delta=1)
        self.assertAlmostEqual(r["idle_s"], 3600, delta=1)
        s = token_ledger.summarize(rows)
        self.assertEqual(s["sessions"], 1)
        self.assertEqual(s["ctx_median"], 120_000)
        # The scoreboard's verdicts are pure functions of the numbers.
        self.assertEqual(finish_line.verdict_ctx(s["ctx_median"]), "FAIL")
        self.assertEqual(finish_line.verdict_ctx(60_000), "PASS")
        self.assertEqual(finish_line.verdict_ratio([(10, 12), (8, 9), (5, 5)]), "PASS")
        self.assertEqual(finish_line.verdict_ratio([(10, 12), (20, 9), (5, 5)]), "FAIL")
        self.assertEqual(finish_line.verdict_ratio([(10, 12)]), "UNKNOWN")
        self.assertEqual(finish_line.verdict_flat(1000, 1010), "PASS")
        self.assertEqual(finish_line.verdict_flat(1000, 1300), "FAIL")
        self.assertTrue(finish_line.is_substrate_internal("the land gate starves on CAS"))
        self.assertFalse(finish_line.is_substrate_internal("add a colour picker to the example app page"))


# =============================================================================
# 7. LANDING — a land holds the shared queue for the merge, never for the validation.
# =============================================================================

@unittest.skipUnless(GIT, "git not available")
class FL7_Landing(_LaneRepo):
    """Concurrent lanes queue behind each other's MERGE, not each other's test suite: the
    serialized section stays inside its budget, and a land that blows it says so."""

    def test_three_lanes_land_and_none_holds_the_queue_for_its_suite(self):
        """The capability, end to end and from the waiting lane's side.

        FL5 already pins that three diverged lanes all land and nothing is lost. What was
        missing is the COST of that: before ADR-0124 each of those lands held the
        one-at-a-time gate for the whole of its own suite, so the third lane waited out
        two suites it had no interest in. Five real lanes doing this on 2026-09-10 took
        about an hour to land ten minutes of work.

        The suite here is a sleep, and it is the point. It stands for the 271-second
        derive plus 58-second gate that were measured inside the lock, at a length a test
        can afford. If any of it is being paid inside the serialized section, the hold
        exceeds it and the assertion fires; if the hold stays at merge-and-publish, the
        sleep is invisible to the queue however long it is."""
        gate_seconds = 0.6
        # THE GATE LEAVES A RECEIPT, and that is not decoration (WI-0397). The previous
        # control here was `assertIn("gate:    ok", out, "control: the slow gate really
        # ran")` — a string the FAST gate prints just as happily. Combined with `land()`
        # resetting `CFG["gate"]` to `PASS_GATE` on its first line, the slow gate had never
        # executed in this test and the control could not tell. A control has to assert
        # something only its subject can produce.
        ran = self.tmp / "slow-gate-ran"
        slow_gate = [["python3", "-c",
                      f"import time, pathlib; "
                      f"pathlib.Path({str(ran)!r}).write_text('ran'); "
                      f"time.sleep({gate_seconds})"]]

        # THE IDLE BASELINE, TAKEN IN THIS RUN (WI-0397 / A15). One land through a FAST
        # gate, on this machine, under whatever load this machine is under right now.
        #
        # Everything below is measured against THIS number rather than against a constant,
        # because the old assertion -- `hold < 0.6` -- compared a duration measured on a
        # loaded machine to a threshold chosen on an idle one. That once cost a lane an
        # hour: repeated land attempts refused under the load of several concurrent
        # suites, and the check passed in isolation every single time. The property under
        # test still held; the number had drifted out from under it.
        baseline_lane = self.lane(0)
        self.stage(baseline_lane, "mod0.py", "# baseline lane touched the code\n")
        outcome, out = self.land(baseline_lane, gate=PASS_GATE)
        self.assertTrue(outcome, out)
        self.assertIn("gate:    ok", out, "control: the baseline land really landed")
        self.assertFalse(ran.exists(),
                         "control: the baseline must NOT have run the slow gate")
        baseline = session.land_receipts()[-1]["lock_seconds"]

        lanes = [self.lane(n) for n in (1, 2, 3)]
        # A file each: three lanes editing one file is a rebase conflict, which is a
        # different test (FL5 owns divergence) and would never reach the queue.
        for n, lane in enumerate(lanes, start=1):
            self.stage(lane, f"mod{n}.py", f"# lane {n} touched the code\n")
        holds, gated = [], 0
        for lane in lanes:
            ran.unlink(missing_ok=True)
            outcome, out = self.land(lane, gate=slow_gate)
            self.assertTrue(outcome, out)
            self.assertIn("gate:    ok", out)
            # THE CONTROL THAT THE OLD ONE ONLY LOOKED LIKE. This file can only exist
            # because the slow gate's own process wrote it, so it distinguishes the two
            # gates where the "gate: ok" line could not.
            self.assertTrue(ran.exists(),
                            "control: the slow gate did not run, so the holds below are "
                            "measured against a gate that never executed")
            gated += 1
            holds.append(session.land_receipts()[-1]["lock_seconds"])
        self.assertEqual(gated, 3)

        # THE PREDICATE IS THE DIFFERENCE, NOT A MULTIPLE. A plain ratio against the
        # baseline would be just as brittle in the other direction: a merge-and-publish on
        # a warm repo lands near hundredths of a second, and any multiple of a number that
        # small is blown by ordinary scheduler noise. What actually discriminates is that
        # `gate_seconds` is the amount the hold would grow by if the gate moved inside the
        # lock -- so the gate's own duration is the scale to measure the difference against:
        #
        #   gate inside the lock  =>  hold - baseline  ~=  gate_seconds
        #   gate outside the lock =>  hold - baseline  ~=  0
        #
        # Under load both terms inflate together and the DIFFERENCE does not, which is
        # exactly the property the 0.6s constant lacked. The fraction leaves a wide margin
        # and still fails hard on the real regression, where the term is a full 1.0.
        allowed = baseline + gate_seconds * 0.5
        for hold in holds:
            self.assertLess(hold, allowed,
                            f"a land held the shared queue for its own validation: holds "
                            f"{holds} against a same-run idle baseline of {baseline:.3f}s "
                            f"and a {gate_seconds}s gate (allowed < {allowed:.3f}s)")
            # The absolute budget stays. It is a real product promise about what a land may
            # cost the lanes behind it, not a test-local constant, and A15 is not about it.
            self.assertLessEqual(hold, session.LAND_LOCK_HOLD_BUDGET_SECONDS)

    def test_the_scoreboard_reads_the_holds_and_can_go_red(self):
        """The detector ships with the capability. A budget nothing aggregates is a
        number in a comment, and the whole reason this is FL7 rather than a unit test is
        that the population — every land in the window — is what the rule is about."""
        import finish_line
        self.point_at(self.lane(1))
        self.assertEqual(finish_line.probe_land_lock_hold()[0], "UNKNOWN",
                         "no lands in the window is not evidence that lands are fast")
        budget = session.LAND_LOCK_HOLD_BUDGET_SECONDS
        now = time.time()
        with mock.patch.object(session, "land_receipts",
                               return_value=[{"lock_seconds": 0.2, "at_epoch": now},
                                             {"lock_seconds": 1.0, "at_epoch": now}]):
            self.assertEqual(finish_line.probe_land_lock_hold()[0], "PASS")
        with mock.patch.object(session, "land_receipts",
                               return_value=[{"lock_seconds": 0.2, "at_epoch": now},
                                             {"lock_seconds": budget + 0.1, "at_epoch": now,
                                              "lane": "poga-9", "at": "2026-09-10",
                                              "stages": {"push": budget}}]):
            verdict, evidence = finish_line.probe_land_lock_hold()
        self.assertEqual(verdict, "FAIL")
        self.assertIn("poga-9", evidence)
        self.assertIn("push", evidence, "the evidence must name which stage blew it")


# =============================================================================
# The BOARD ITSELF — what it measures, and what it refuses to score (WI-0405).
#
# FL1-FL7 above pin capabilities. These pin the instrument: a scoreboard that measures
# the wrong subject, or scores a diagnostic as a failure, is not a weaker reading of the
# harness — it is a confident reading of something else, which is how `probe_flat` came
# to report a 99% shrinkage as the news while the body it named grew by 160%.
# =============================================================================

def _commit_at(repo, when, message="work"):
    """Commit everything staged with both dates pinned. Backdating is the whole point:
    `probe_flat` resolves its own `--before=30 days ago` revision, so a fixture that
    cannot place a commit in the past cannot exercise the probe at all."""
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    subprocess.run([GIT, "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run([GIT, "-C", str(repo), "commit", "-qm", message],
                   check=True, capture_output=True, env=env)


@unittest.skipUnless(GIT, "git not available")
class TheFlatnessProbeMeasuresTheBody(unittest.TestCase):
    """Exit criterion 2 reads the harness's body — `session.py` PLUS `sessionlib/` —
    and can therefore see growth that the entry point cannot show."""

    def setUp(self):
        import finish_line
        self.fl = finish_line
        self.addCleanup(finish_line._set_root, None)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")

    def _write(self, rel, lines):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n" * lines, encoding="utf-8")

    def test_growth_in_sessionlib_is_growth_and_a_thin_entry_point_cannot_hide_it(self):
        """THE MUTATION THIS KILLS is the code that shipped until WI-0405: reading only
        `session.py`. The fixture is built so the two readings disagree in verdict, not
        merely in number — the entry point is the SAME SIZE at both ends, so a probe that
        watches it reports PASS (flat, 0%) while the body it fronts has tripled.

        That is not a contrived shape. It is the real one: measured on this checkout on
        2026-09-20, `session.py` went 15,003 → 160 lines across the window while the body
        went 15,003 → 38,937, and the board was printing the first pair."""
        old = _dt.datetime.now() - _dt.timedelta(days=60)
        self._write("session.py", 100)
        self._write("sessionlib/config.py", 1000)
        _commit_at(self.repo, old, "old")
        self._write("session.py", 100)                  # the entry point does not move
        self._write("sessionlib/config.py", 1000)
        self._write("sessionlib/lanes.py", 2000)        # the body does
        _commit_at(self.repo, _dt.datetime.now(), "new")
        self.fl._set_root(str(self.repo))

        then_body, then_files = self.fl.harness_lines_at(self.repo, "HEAD~1")
        self.assertEqual((then_body, then_files), (1100, 2))
        self.assertEqual(self.fl.harness_lines_now(self.repo), (3100, 3))
        verdict, evidence = self.fl.probe_flat()
        self.assertEqual(verdict, "FAIL",
                         "a body that tripled is not flat, whatever session.py did")
        self.assertIn("3100", evidence, "the evidence must carry the number it judged")
        self.assertIn("sessionlib", evidence, "and must name the subject it measured")
        # The probe this replaced, reconstructed: session.py alone, both ends.
        self.assertEqual(self.fl.verdict_flat(100, 100), "PASS")

    def test_the_file_list_comes_from_the_revision_not_from_todays_directory(self):
        """A revision from BEFORE the ADR-0118 split has no `sessionlib/` at all, and the
        probe has to measure it anyway — reading the package's parts off today's tree
        would score every one of them as 0 lines back then and manufacture growth out of
        the split itself."""
        old = _dt.datetime.now() - _dt.timedelta(days=60)
        self._write("session.py", 1000)                 # monolith: no sessionlib yet
        _commit_at(self.repo, old, "before the split")
        (self.repo / "session.py").write_text("x\n" * 20, encoding="utf-8")
        self._write("sessionlib/config.py", 980)        # same body, moved
        _commit_at(self.repo, _dt.datetime.now(), "after the split")
        self.fl._set_root(str(self.repo))
        self.assertEqual(self.fl.harness_lines_at(self.repo, "HEAD~1"), (1000, 1))
        self.assertEqual(self.fl.harness_lines_now(self.repo), (1000, 2))
        self.assertEqual(self.fl.probe_flat()[0], "PASS",
                         "a pure move is not growth; the split must not read as either")


class TheRetiredRowIsGone(unittest.TestCase):
    """`sessions parked on operator (target 0)` is retired, not renamed (WI-0405 / R2)."""

    def test_no_row_scores_asking_the_user_a_question_as_a_failure(self):
        """TWO ASSERTIONS BECAUSE THEY FAIL FOR DIFFERENT REASONS. The function being
        gone is the code fact; the registration table not naming it is the product fact,
        and a probe left defined but unregistered would pass the first and still be one
        import away from coming back. The board is built with every probe stubbed so this
        costs no git, no fleet and no suite."""
        import finish_line
        self.assertFalse(hasattr(finish_line, "probe_parked_questions"),
                         "the retired probe is deleted, not left for a caller to find")
        stubs = {n: mock.patch.object(finish_line, n, return_value=("PASS", "stub"))
                 for n in dir(finish_line)
                 if n.startswith("probe_") and n != "probe_tokens"}
        with contextlib.ExitStack() as stack:
            for p in stubs.values():
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(finish_line, "probe_tokens",
                                                  return_value=("PASS", "stub", {})))
            caps, exits = finish_line.board(run_tests=False)
        labels = [n for n, _, _ in caps] + [n for n, _, _ in exits]
        self.assertNotIn("sessions parked on operator", labels)
        for label in labels:
            self.assertNotIn("parked", label.lower())
            self.assertNotIn("target 0", label.lower())
        self.assertIn("avoidable interventions", labels)
        self.assertIn("time to verified done", labels)


class TheOutcomeMeasuresAreReportedNotScored(unittest.TestCase):
    """R3's two measures carry numbers and no bar, and the green tally excludes them."""

    def test_a_report_row_is_neither_green_nor_a_regression(self):
        import finish_line
        caps = [("a", "PASS", ""), ("b", "FAIL", ""), ("c", finish_line.REPORT, "")]
        exits = [("1", "PASS", ""), ("2", "FAIL", ""), ("3", "FAIL", "")]
        line = finish_line.status_line(caps, exits)
        self.assertIn("1/2 capabilities green", line,
                      "a measure with no bar is out of the denominator, not failing it")
        self.assertIn("1 measure(s) reported without a bar", line,
                      "and it is still counted, so it cannot go missing unnoticed")


class TheInterventionSplit(unittest.TestCase):
    """Avoidable and required interventions are told apart by an AUTHORED tag, counted by
    `curate/metrics.py`, and silence costs rather than scores."""

    def setUp(self):
        _spec = importlib.util.spec_from_file_location(
            "metrics", ROOT / "curate" / "metrics.py")
        self.metrics = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(self.metrics)

    def _journal(self, entries, mid=None, days_ago=1, items="WI-0001"):
        return {
            "path": pathlib.Path(f"j{days_ago}.md"),
            "started": _dt.datetime.now() - _dt.timedelta(days=days_ago),
            "escalation_entries": entries,
            "midsession_entries": mid or [],
            "escalations": len(entries),
            "midsession_escalations": len(mid or []),
            "work_items": [s for s in items.split(",") if s],
        }

    def test_a_required_decision_is_not_counted_as_avoidable(self):
        """The brief's own acceptance test, in as many words. A `[required]` tag moves the
        entry to the separately-reported count that has NO target — the whole reason the
        `target 0` row was retired was that it scored this case as a failure."""
        m = self.metrics
        self.assertEqual(m.classify_intervention("- [required] operator must pick a price"),
                         "required")
        self.assertEqual(m.classify_intervention("- [REQUIRED DECISION] his credentials"),
                         "required")
        s = m.summarize_interventions(
            [self._journal(["- [required] only operator can authorize this"])],
            _dt.date.today())
        self.assertEqual(s["required"], 1)
        self.assertEqual(s["avoidable"], 0)
        self.assertEqual(s["avoidable_headline"], 0,
                         "asking a question that was genuinely his cannot cost anything")

    def test_an_untagged_entry_counts_as_avoidable_so_silence_cannot_score(self):
        """THE ANTI-GAMING PROPERTY, and the one worth a test of its own. A tag scheme
        whose default is 'uncounted' reads perfect for everybody who has not adopted it,
        so the measure would improve the moment it was ignored. The only way down is an
        authored `[required]` claim a human can overturn at the weekly review."""
        m = self.metrics
        self.assertEqual(m.classify_intervention("- the lane could not find the store"),
                         "untagged")
        s = m.summarize_interventions(
            [self._journal(["- the lane could not find the store",
                            "- [avoidable] re-asked a settled permission"])],
            _dt.date.today())
        self.assertEqual((s["untagged"], s["avoidable"]), (1, 1))
        self.assertEqual(s["avoidable_headline"], 2)
        self.assertEqual(s["required"], 0)

    def test_a_contested_entry_resolves_toward_being_counted(self):
        self.assertEqual(
            self.metrics.classify_intervention("- [avoidable] [required] both tags"),
            "avoidable", "a disputed classification is one the review should see")

    def test_both_sections_count_and_stay_distinguishable(self):
        """A question raised mid-flight and answered interrupted him exactly as much as
        one held to the close — WI-0278 exists because counting only the authored half
        under-reported by roughly five to one."""
        s = self.metrics.summarize_interventions(
            [self._journal(["- at close"], mid=["- mid-flight on WI-0042"])],
            _dt.date.today())
        self.assertEqual(s["total"], 2)
        sections = sorted(e["section"] for e in s["events"])
        self.assertEqual(sections, ["at-close", "midsession"])
        by_item = {e["section"]: e["items"] for e in s["events"]}
        self.assertEqual(by_item["midsession"], ["WI-0042"],
                         "an entry that names an item is charged to that item")
        self.assertEqual(by_item["at-close"], ["WI-0001"],
                         "and one that names none falls back to what the session held")

    def test_a_journal_outside_the_window_is_not_counted(self):
        s = self.metrics.summarize_interventions(
            [self._journal(["- old"], days_ago=400)], _dt.date.today())
        self.assertEqual((s["total"], s["sessions"]), (0, 0))

    def test_a_silent_journal_is_unknown_and_never_zero(self):
        """`summarize_ledger`'s standing contract, kept by the new summary: a journal with
        no ledger section at all did not report zero escalations, it reported nothing."""
        j = self._journal([])
        j["escalations"] = j["midsession_escalations"] = None
        j["escalation_entries"] = j["midsession_entries"] = None
        s = self.metrics.summarize_interventions([j], _dt.date.today())
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["silent"], 1, "and the board must be able to say how many")


@unittest.skipUnless(GIT, "git not available")
class _StoreRepo(unittest.TestCase):
    """A git repo carrying nothing but a work-item store, which is all the two outcome
    probes read from the tree."""

    def setUp(self):
        import finish_line
        self.fl = finish_line
        self.addCleanup(finish_line._set_root, None)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "work-items").mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")

    def item(self, wid, title="a thing", status="open", scope="", body=""):
        p = self.repo / "work-items" / f"{wid}-{title.replace(' ', '-')}.md"
        p.write_text(f"# {wid}: {title}\n\n- status: {status}\n- section: next\n"
                     f"- blocked-by: \n- group: \n- source: \n- impact: fix\n"
                     + (f"- scope: {scope}\n" if scope else "")
                     + f"- version: \n\n{body}\n", encoding="utf-8")
        return p

    def note(self, wid, when, text):
        d = self.repo / "work-items" / "notes" / wid
        d.mkdir(parents=True, exist_ok=True)
        stamp = when.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        (d / f"{stamp}Z-devbox-abcd.md").write_text(text, encoding="utf-8")


class TheTimeToVerifiedDone(_StoreRepo):
    """An item's duration counts from durable evidence to a closure that cites its
    acceptance — and a closure that cites nothing does not end the interval."""

    def test_an_evidence_free_done_does_not_end_the_interval(self):
        """The brief's other named acceptance test. Two items, closed the same day, one
        carrying an acceptance receipt and one not: the measured population is the first
        alone, and the second is reported as unverified rather than silently dropped.

        WHY IT MATTERS THAT IT IS NOT DROPPED: if an unverified closure simply vanished,
        closing items without evidence would shrink the denominator and leave the median
        looking the same. Counted in its own column, it is visibly the larger number."""
        filed = _dt.datetime.now() - _dt.timedelta(days=10)
        self.item("WI-0001", "verified one", scope="harness")
        self.item("WI-0002", "bare one", scope="harness")
        _commit_at(self.repo, filed, "file both")
        self.item("WI-0001", "verified one", status="done", scope="harness",
                  body="ACCEPTANCE MET: the guard fires on the real defect.")
        self.item("WI-0002", "bare one", status="done", scope="harness")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=2), "close both")
        self.fl._set_root(str(self.repo))

        self.assertTrue(self.fl.wi_acceptance_verified(self.repo, "WI-0001"))
        self.assertFalse(self.fl.wi_acceptance_verified(self.repo, "WI-0002"))
        verdict, ev = self.fl.probe_claim_to_result()
        self.assertEqual(verdict, self.fl.REPORT, "a measure with no target is reported")
        self.assertIn("1 of 2 closure(s)", ev)
        self.assertIn("1 unverified", ev)
        self.assertIn("harness 8.0d/1", ev, "8 days filed → closed, charged to its scope")

    def test_a_receipt_in_a_note_record_counts_as_evidence(self):
        """`_note_append` is the only writer that does not touch the item file, so a lane
        that records its closing evidence as a note has satisfied the rule — reading the
        item body alone would call that closure unverified."""
        filed = _dt.datetime.now() - _dt.timedelta(days=6)
        self.item("WI-0003", "noted one", scope="product")
        _commit_at(self.repo, filed, "file")
        self.item("WI-0003", "noted one", status="done", scope="product")
        self.note("WI-0003", _dt.datetime.now() - _dt.timedelta(days=3),
                  "ACCEPTANCE VERIFIED against the line, negative control run.")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=3), "close")
        self.fl._set_root(str(self.repo))
        self.assertTrue(self.fl.wi_acceptance_verified(self.repo, "WI-0003"))
        self.assertIn("product 3.0d/1", self.fl.probe_claim_to_result()[1])

    def test_open_item_ages_print_beside_the_completed_durations(self):
        """Required by name in the brief, because a median over finished work alone
        improves every time something hard is abandoned."""
        filed = _dt.datetime.now() - _dt.timedelta(days=40)
        self.item("WI-0004", "still going")
        _commit_at(self.repo, filed, "file")
        self.fl._set_root(str(self.repo))
        ev = self.fl.probe_claim_to_result()[1]
        self.assertIn("still open: 1 item(s)", ev)
        self.assertIn("median age 40d", ev)

    def test_the_two_outcome_rows_divide_by_the_same_population(self):
        """One noun, one number. The first cut printed 240 completions on one row and 271
        on the next from the same window and the same store, because one deduplicated a
        re-applied `status: done` line and the other did not."""
        filed = _dt.datetime.now() - _dt.timedelta(days=9)
        self.item("WI-0005", "closed twice")
        _commit_at(self.repo, filed, "file")
        self.item("WI-0005", "closed twice", status="done", body="ACCEPTANCE MET.")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=4), "close")
        self.item("WI-0005", "closed twice", status="open", body="ACCEPTANCE MET.")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=3), "reopen on a lane")
        self.item("WI-0005", "closed twice", status="done", body="ACCEPTANCE MET.")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=2), "re-close")
        self.fl._set_root(str(self.repo))
        closures = self.fl.outcome_closures(self.repo)
        self.assertEqual([wid for wid, _ in closures], ["WI-0005"],
                         "one item closed once, however many times the line was applied")
        self.assertIn("1 of 1 closure(s)", self.fl.probe_claim_to_result()[1])

    def test_the_rate_is_avoidable_over_the_items_actually_finished(self):
        """The probe's own arithmetic, with the journal read faked and the counting left
        real: three interventions, one of them his to make, over two finished items."""
        filed = _dt.datetime.now() - _dt.timedelta(days=8)
        self.item("WI-0006", "one", scope="harness")
        self.item("WI-0007", "two", scope="harness")
        _commit_at(self.repo, filed, "file")
        self.item("WI-0006", "one", status="done", scope="harness", body="ACCEPTANCE MET.")
        self.item("WI-0007", "two", status="done", scope="harness", body="ACCEPTANCE MET.")
        _commit_at(self.repo, _dt.datetime.now() - _dt.timedelta(days=1), "close both")
        self.fl._set_root(str(self.repo))

        _spec = importlib.util.spec_from_file_location(
            "metrics", ROOT / "curate" / "metrics.py")
        metrics = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(metrics)
        journals = [{
            "path": pathlib.Path("j.md"),
            "started": _dt.datetime.now() - _dt.timedelta(days=3),
            "escalation_entries": ["- [required] his call on WI-0006",
                                   "- re-asked a settled permission on WI-0006",
                                   "- repaired a stranded lane on WI-0007"],
            "midsession_entries": [],
            "escalations": 3, "midsession_escalations": None,
            "work_items": ["WI-0006"],
        }]
        with mock.patch.object(metrics, "mine_journal_sessions", return_value=journals), \
             mock.patch.object(self.fl, "_metrics", return_value=metrics):
            verdict, ev = self.fl.probe_interventions()
        self.assertEqual(verdict, self.fl.REPORT)
        self.assertIn("1.00 avoidable/completed item", ev, "2 avoidable over 2 finished")
        self.assertIn("1 required (no target)", ev)
        self.assertIn("2 item(s) completed", ev)
        self.assertIn("harness 2/2", ev)


class TheRuntimeAcceptanceDrillIsHonestAboutItsGap(unittest.TestCase):
    """`drills/runtime-acceptance.py` records what it drove and NAMES what it did not
    (WI-0405 / R5). ADR-0041's floor is runtime-agnostic, so a per-runtime record is the
    only evidence there is for a runtime nobody has run a session in — and a record that
    read PASS across the board would be manufacturing that evidence rather than gathering
    it."""

    def setUp(self):
        _spec = importlib.util.spec_from_file_location(
            "runtime_acceptance", ROOT / "drills" / "runtime-acceptance.py")
        self.drill = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(self.drill)

    def test_the_subjects_come_from_the_runtime_registry(self):
        """Not a list respelled in the drill. A hand-maintained subject list goes blind
        the day a runtime is added, and the record for the new one simply never exists."""
        self.assertEqual([r["id"] for r in self.drill._runtimes()],
                         [r["id"] for r in session.RUNTIMES])

    def test_a_record_says_the_agent_half_was_not_driven(self):
        sub = {"steps": [("1. checkout", "OK", "x")], "ok": True, "note": ""}
        probe = {"id": "codex", "command": "codex", "alias": "cx", "guards": "undeclared",
                 "prompt_shape": "positional", "path": "/usr/bin/codex",
                 "version": "0.1", "version_note": ""}
        text = self.drill.render_record(probe, sub, _dt.date(2026, 9, 20))
        self.assertIn("not covered here", text)
        self.assertIn("exercised nowhere", text,
                      "a runtime no session has run in must be named as untested")
        self.assertIn("undeclared", text, "and its guard status must travel with it")
        self.assertNotIn("PASS", text, "the drill reports observations, not a verdict")

    def test_an_unresolvable_binary_is_a_fact_about_the_machine_not_the_runtime(self):
        """The distinction matters for the fleet: `agy` missing on one machine says nothing
        about whether Antigravity can be a member, and a record that conflated the two
        would send someone to fix the wrong thing."""
        sub = {"steps": [], "ok": True, "note": ""}
        probe = {"id": "antigravity", "command": "agy", "alias": "ag",
                 "guards": "undeclared", "prompt_shape": "-i", "path": None,
                 "version": None, "version_note": "declared binary does not resolve"}
        # Whitespace-normalised: the record is hard-wrapped for reading, so a claim that
        # spans a line break is present in the document and absent from the string.
        text = " ".join(self.drill.render_record(probe, sub, _dt.date(2026, 9, 20)).split())
        self.assertIn("does not resolve", text)
        self.assertIn("fact about this machine, not about the runtime", text)


if __name__ == "__main__":
    unittest.main()
