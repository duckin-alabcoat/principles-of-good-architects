"""Tests for the session janitor — the retention half of the session lifecycle.

The reaper closes a session it can PROVE is dead from the liveness sidecar. Two classes
of litter are invisible to it by construction, and both accumulate forever:

  - a journal whose evidence NO LONGER EXISTS. The record is durable (tracked in git);
    the evidence is a gitignored scratch file, per-tree and per-machine, deleted with the
    lane worktree that held it. "No evidence -> leave open" then means "never closable by
    anything" — federation carried three such journals for 14-17 days;
  - sidecar files for sessions long over. Nothing had ever deleted one: one member's
    `.session-state/` held about a hundred files, a third belonging to sessions that
    closed perfectly.

What must hold, and why each one bit:
  - closing uses GIT history, because that is the evidence that outlives the scratch file;
  - the duration is `unknown`, never computed. Arithmetic between `started` and a bulk
    commit would manufacture an 8-day session and trip our own insane-duration miner;
  - the close is LABELLED `closed-by: janitor`, so machine-closed history can never be
    mistaken for an Architect's sign-off;
  - a journal is never DELETED here. The reaper can prove a stub lived seconds; with no
    evidence at all, "this did nothing" is an inference, and deleting a narrative to tidy
    a list is not a trade worth making;
  - anything with evidence, anything young, and the current session are left alone;
  - `.jsonl` logs are never swept — they are the audit trail, not per-session residue.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import inspect
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class JanitorBase(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        (self.repo / "sessions" / "journal").mkdir(parents=True)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")

        self._save = {k: getattr(session, k) for k in
                      ("ROOT", "JOURNAL_DIR", "SESSION_STATE_DIR", "CFG")}
        session.ROOT = self.repo
        session.JOURNAL_DIR = self.repo / "sessions" / "journal"
        session.SESSION_STATE_DIR = self.repo / ".session-state"
        session.SESSION_STATE_DIR.mkdir()
        session.CFG = {"tz": ZoneInfo("UTC"), "architect_name": "T",
                       "architect_id": "t-arch", "machine_map": {}}
        self.tz = session.CFG["tz"]

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # --- helpers -------------------------------------------------------------
    def _journal(self, sid, csid, started, body="### What happened\n\n- real work\n",
                 ended=""):
        p = session.journal_path(sid)
        p.write_text(session.render_journal({
            "session-id": sid, "ordinal": 1, "title": "(in progress)", "machine": "Runner",
            "runtime": "claude-code", "role-doc-version": "v1", "base-commit": "abc",
            "started": started, "ended": ended, "claude-session-id": csid}, body),
            encoding="utf-8")
        return p

    def _commit(self, p, when):
        """Commit `p` with an authored/committed date, so the git probe has a real
        timestamp to find — the durable evidence the whole design rests on."""
        _git(self.repo, "add", "--", str(p))
        subprocess.run([GIT, "-C", str(self.repo), "commit", "-qm", "j"], check=True,
                       capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(self.tmp),
                            "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})

    def _fm(self, p):
        fm, _ = session.parse_journal(p.read_text(encoding="utf-8"))
        return fm

    def _ago(self, days):
        return (datetime.now(self.tz) - timedelta(days=days)).isoformat()


class AgedJournalTest(JanitorBase):
    def test_an_evidence_less_journal_is_closed_at_its_last_commit(self):
        """The core case. No sidecar exists anywhere — it died with the lane worktree —
        so the only durable evidence left is the journal's own commit history."""
        when = self._ago(20)
        p = self._journal("j-aged", "csid-aged", self._ago(21))
        self._commit(p, when)
        res = session.janitor_sweep(None)
        self.assertEqual(["j-aged"], [s for s, _ in res["closed"]])
        fm = self._fm(p)
        self.assertTrue(fm["ended"], "must be closed, not left flown-not-landed")
        self.assertEqual(fm["ended"][:10], when[:10], "closed at the last provable activity")

    def test_the_duration_is_unknown_never_computed(self):
        """With no evidence the end time genuinely is not known. Subtracting `started`
        from a bulk-commit timestamp would invent an 8-day session out of arithmetic and
        trip our own insane-duration miner with our own fiction."""
        p = self._journal("j-dur", "csid-dur", self._ago(30))
        self._commit(p, self._ago(20))
        session.janitor_sweep(None)
        self.assertEqual("unknown", self._fm(p)["duration"])

    def test_the_close_is_labelled_as_a_machine_close(self):
        """An `ended` stamp with no label is indistinguishable from an Architect's own
        sign-off — that would make the janitor a quiet manufacturer of clean history."""
        p = self._journal("j-label", "csid-label", self._ago(30))
        self._commit(p, self._ago(20))
        session.janitor_sweep(None)
        self.assertIn("janitor", self._fm(p)["closed-by"])

    def test_a_young_evidence_less_journal_is_left_alone(self):
        """A session open for two days is very plausibly still running."""
        p = self._journal("j-young", "csid-young", self._ago(2))
        self._commit(p, self._ago(2))
        res = session.janitor_sweep(None)
        self.assertEqual([], res["closed"])
        self.assertEqual("", self._fm(p)["ended"])

    def test_a_journal_with_evidence_is_the_reapers_business_not_ours(self):
        """Overlapping writers on one journal is exactly the defect this whole area
        exists to prevent. Evidence present -> hands off, whatever its age."""
        p = self._journal("j-eviden", "csid-ev", self._ago(30))
        self._commit(p, self._ago(20))
        (session.SESSION_STATE_DIR / "csid-ev.live").write_text(
            json.dumps({"last_beat": self._ago(19)}), encoding="utf-8")
        res = session.janitor_sweep(None)
        self.assertEqual([], res["closed"])
        self.assertEqual("", self._fm(p)["ended"])

    def test_the_current_session_is_never_closed(self):
        p = self._journal("j-me", "csid-me", self._ago(30))
        self._commit(p, self._ago(20))
        res = session.janitor_sweep(current_csid="csid-me")
        self.assertEqual([], res["closed"])
        self.assertEqual("", self._fm(p)["ended"])

    def test_a_stub_bodied_journal_is_closed_not_deleted(self):
        """The reaper deletes a phantom because it can PROVE the session lived seconds.
        Here there is no evidence, so 'it did nothing' is an inference — and deleting a
        record on an inference is not reversible."""
        p = self._journal("j-stub", "csid-stub", self._ago(30), body="\n")
        self._commit(p, self._ago(20))
        session.janitor_sweep(None)
        self.assertTrue(p.exists(), "a narrative is never destroyed to tidy a list")
        self.assertTrue(self._fm(p)["ended"])

    def test_an_undatable_journal_is_left_alone_rather_than_guessed(self):
        """Uncommitted and no `started` — nothing to close it AT. Leaving it open is the
        honest answer; stamping `now` would record a time nothing supports."""
        p = self._journal("j-nodate", "csid-nodate", "")
        res = session.janitor_sweep(None)
        self.assertEqual([], res["closed"])
        self.assertEqual("", self._fm(p)["ended"])

    def test_uncommitted_journal_falls_back_to_its_own_start_time(self):
        """A journal never committed has no git history, but `started` is a floor — the
        session cannot have ended before it began."""
        p = self._journal("j-uncommitted", "csid-unc", self._ago(30))
        res = session.janitor_sweep(None)
        self.assertEqual(["j-uncommitted"], [s for s, _ in res["closed"]])
        self.assertTrue(self._fm(p)["ended"])

    def test_dry_run_writes_nothing(self):
        p = self._journal("j-dry", "csid-dry", self._ago(30))
        self._commit(p, self._ago(20))
        res = session.janitor_sweep(None, dry_run=True)
        self.assertEqual(["j-dry"], [s for s, _ in res["closed"]])
        self.assertEqual("", self._fm(p)["ended"], "reported, not written")
        self.assertFalse((session.SESSION_STATE_DIR / "janitor.jsonl").exists())

    def test_the_sweep_is_logged_so_a_rising_rate_is_visible(self):
        """A janitor that quietly closes a fifth of a member's sessions every week is
        hiding a defect rather than fixing one. The log is what makes that a signal."""
        p = self._journal("j-logged", "csid-log", self._ago(30))
        self._commit(p, self._ago(20))
        session.janitor_sweep(None)
        line = (session.SESSION_STATE_DIR / "janitor.jsonl").read_text(encoding="utf-8")
        self.assertIn("j-logged", json.loads(line.strip())["closed"])

    def test_sweeping_twice_is_idempotent(self):
        p = self._journal("j-twice", "csid-twice", self._ago(30))
        self._commit(p, self._ago(20))
        session.janitor_sweep(None)
        first = p.read_text(encoding="utf-8")
        self.assertEqual([], session.janitor_sweep(None)["closed"])
        self.assertEqual(first, p.read_text(encoding="utf-8"))


class SidecarRetentionTest(JanitorBase):
    """WI-0270 moved these fixtures onto REAL-shaped stems, and the reason is worth
    keeping. The sidecar stem is a session identity — a UUID, or the durable id
    `durable_session_id` mints — and the janitor now prunes a stem that is neither on
    sight, at any age, because it names no session and so is not evidence of one.

    These tests used `csid-old` / `csid-recent`, which no session has ever produced. That
    made them pass for the wrong reason the moment the shape rule landed: the aged-residue
    test still went green, but on the malformed branch, so it had stopped being able to
    fail for the age property it is named after. A fixture whose stand-in cannot occur is
    the same defect this item is about, one layer in — the tests using an identity no
    session would have is exactly how `testsession` got into the live store."""

    #: A real claude-session-id, and a real durable id: the two shapes that actually occur.
    OLD_CSID = "9aca62e1-392f-4f5b-ae9c-4447003267f7"
    RECENT_CSID = "b0a1d556-2da6-49af-9d63-c77e7fa8727e"

    def _residue(self, name, days_old):
        p = session.SESSION_STATE_DIR / name
        p.write_text("{}", encoding="utf-8")
        old = (datetime.now(self.tz) - timedelta(days=days_old)).timestamp()
        import os
        os.utime(p, (old, old))
        return p

    def test_aged_residue_for_a_finished_session_is_dropped(self):
        """Nothing had ever deleted one of these — including for the sessions that closed
        perfectly, which is why the directory only ever grew."""
        files = [self._residue(f"{self.OLD_CSID}{suf}", 30)
                 for suf in (".live", ".ended", ".gone", ".touched", ".end-ran")]
        res = session.janitor_sweep(None)
        self.assertEqual(5, res["pruned"])
        for f in files:
            self.assertFalse(f.exists())

    def test_a_per_session_work_item_snapshot_is_residue_too(self):
        f = self._residue(f"wi-snapshot-{self.OLD_CSID}.json", 30)
        session.janitor_sweep(None)
        self.assertFalse(f.exists())

    def test_young_residue_is_kept(self):
        f = self._residue(f"{self.RECENT_CSID}.live", 1)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())

    def test_a_stem_that_names_no_session_is_pruned_however_young(self):
        """WI-0270 ask (d). The write-side refusal stops NEW forgeries; it can do nothing
        about the ones already on disk, and the age rule above was structurally incapable
        of reaching them: a `testsession.live` was re-beaten by every suite run, so it was
        always younger than the cutoff. Three survived every sweep for eight days and held
        `poga-7` unrecoverable the whole time.

        A stem that is neither a UUID nor a durable id belongs to no session, so there is
        no session whose work a grace could be protecting. Nothing to wait for."""
        f = self._residue("testsession.live", 0)
        self.assertEqual(1, session.janitor_sweep(None)["pruned"])
        self.assertFalse(f.exists())

    def test_the_app_snapshot_is_live_state_and_survives_the_shape_rule(self):
        """`wi-snapshot-app.json` is NOT residue and NOT a fixture's leavings. `"app"` is
        the documented fallback key for a runtime with no `CLAUDE_CODE_SESSION_ID` — a
        plain terminal `poga` — and the file is read back to resolve an item RANGE
        ("dispatch items 1-10"). Deleting it does not just lose a record: the next range
        resolves against nothing, and `_wi_snapshot` returns `(None, {})`.

        Caught during WI-0270 with the deletion already written. The shape survey behind
        `SIDECAR_STEM_RE` covered the liveness suffixes; the first cut of the rule applied
        it to every suffix the janitor recognises, including this one. A probe's answer is
        only about what the probe asked."""
        f = self._residue("wi-snapshot-app.json", 0)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())

    def test_attribution_residue_is_not_judged_on_shape(self):
        """`.touched` and `.end-ran` feed no liveness verdict, so a stem that names no
        session costs nothing there and the age rule is the whole policy."""
        f = self._residue("some-odd-key.touched", 1)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())

    def test_a_forged_ended_marker_is_pruned_too(self):
        """The opposite direction, and the reason the rule covers three suffixes rather
        than one: a `.live` that names no session holds a DEAD lane open, and an `.ended`
        that names no session would retire a LIVE one."""
        f = self._residue("testsession.ended", 0)
        self.assertEqual(1, session.janitor_sweep(None)["pruned"])
        self.assertFalse(f.exists())

    def test_a_malformed_stem_for_an_OPEN_session_is_still_kept(self):
        """The one place the shape rule must yield. If a journal is open under that id,
        something is genuinely keyed on it, and deleting its evidence would destroy the
        only thing that could ever close it — the janitor manufacturing its own work.
        Shape is a reason to distrust a stem, never a reason to outrank live evidence."""
        self._journal("j-odd", "odd-shaped-id", self._ago(2))
        f = self._residue("odd-shaped-id.live", 90)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())

    def test_residue_for_a_still_open_session_is_kept_however_old(self):
        """This is the reaper's evidence. Sweeping it would destroy the only thing that
        could ever close that journal — the janitor would be manufacturing its own work."""
        self._journal("j-open", "csid-open", self._ago(2))
        f = self._residue("csid-open.live", 60)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())

    def test_the_current_sessions_residue_is_kept(self):
        f = self._residue("csid-now.live", 60)
        self.assertEqual(0, session.janitor_sweep(current_csid="csid-now")["pruned"])
        self.assertTrue(f.exists())

    def test_logs_are_never_swept(self):
        """`.jsonl` files are the audit trail — guard firings, adopt-runner history, the
        janitor's own log. They have their own lifecycle and are not per-session residue."""
        keep = [self._residue(n, 90) for n in
                ("guard-firings.jsonl", "adopt-runner.jsonl", "janitor.jsonl", "prep.json")]
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        for f in keep:
            self.assertTrue(f.exists(), f"{f.name} must survive")

    def test_the_session_context_payload_is_not_residue(self):
        """ADR-0082 D5's canon payload is named by an opening prompt, not keyed by a
        session id — sweeping it would strip a hookless runtime of its principles."""
        f = self._residue(session.SESSION_CONTEXT_NAME, 90)
        self.assertEqual(0, session.janitor_sweep(None)["pruned"])
        self.assertTrue(f.exists())


class JanitorCliTest(JanitorBase):
    def test_the_verb_reports_and_exits_zero(self):
        p = self._journal("j-cli", "csid-cli", self._ago(30))
        self._commit(p, self._ago(20))
        with self.assertRaises(SystemExit) as cm:
            session.cmd_janitor(argparse.Namespace(dry_run=True))
        self.assertEqual(0, cm.exception.code)
        self.assertEqual("", self._fm(p)["ended"])


class StandaloneReapTest(JanitorBase):
    """The verb runs the REAPER too, not just the janitor's own sweep.

    The hole this closes: the reaper's only other caller is `start`, so a repo nobody
    opens never reaps — and litter accumulates precisely in the repos nobody is working
    in. One member carried an open journal for a week with its own clean-exit marker sitting
    beside it the whole time. The nightly fleet pass swept that repo every night and the
    janitor correctly stood down each time ("evidence exists — the REAPER's call, not
    ours"), which left the case owned by nobody: each half individually right, the pair
    with a hole in it.

    The safety argument is an EQUIVALENCE, not a new policy — same predicates, same
    graces, so the verb produces exactly what that repo's next session start would have.
    These tests are therefore mostly about what it must still REFUSE.
    """

    def _exit_marker(self, csid, when, beat=None):
        (session.SESSION_STATE_DIR / f"{csid}.ended").write_text(
            json.dumps({"ended": when, "reason": "prompt_input_exit"}), encoding="utf-8")
        (session.SESSION_STATE_DIR / f"{csid}.live").write_text(
            json.dumps({"last_beat": beat or when}), encoding="utf-8")

    def _run(self, dry_run=False):
        with self.assertRaises(SystemExit) as cm:
            session.cmd_janitor(argparse.Namespace(dry_run=dry_run))
        self.assertEqual(0, cm.exception.code)

    def test_a_cleanly_exited_session_is_closed_without_a_session_start(self):
        """The measured case exactly: a clean exit marker, no session since, and the janitor
        alone would refuse it forever. Closed at the time the EVIDENCE names — not now,
        and not computed from anything."""
        exit_at = self._ago(7)
        p = self._journal("j-exited", "csid-exited", self._ago(8))
        self._exit_marker("csid-exited", exit_at)
        self._run()
        fm = self._fm(p)
        self.assertTrue(fm["ended"], "provably dead and reachable — must be closed")
        self.assertEqual(exit_at, fm["ended"], "closed at the moment the evidence names")

    def test_that_case_is_untouchable_without_this_change(self):
        """Pins the hole itself: the janitor's own sweep must still stand down, so if the
        reaper call is ever removed from the verb the case silently returns to nobody."""
        p = self._journal("j-hole", "csid-hole", self._ago(8))
        self._exit_marker("csid-hole", self._ago(7))
        res = session.janitor_sweep(None)
        self.assertEqual([], res["closed"], "janitor must defer — evidence is the reaper's")
        self.assertEqual("", self._fm(p)["ended"])

    def test_a_live_session_is_never_closed(self):
        """A fresh heartbeat and no exit marker is the definition of live. Closing one
        would stamp `ended` on a session still writing to it."""
        p = self._journal("j-live", "csid-live", self._ago(1))
        (session.SESSION_STATE_DIR / "csid-live.live").write_text(
            json.dumps({"last_beat": datetime.now(self.tz).isoformat()}), encoding="utf-8")
        self._run()
        self.assertEqual("", self._fm(p)["ended"])

    def test_silence_inside_the_grace_is_left_alone(self):
        """Silence is ambiguous — a quiet session may merely be idle-open. The grace is
        the whole reason the reaper is trusted; running it more often must not shorten it."""
        p = self._journal("j-quiet", "csid-quiet", self._ago(1))
        recent = (datetime.now(self.tz) - timedelta(hours=2)).isoformat()
        (session.SESSION_STATE_DIR / "csid-quiet.live").write_text(
            json.dumps({"last_beat": recent}), encoding="utf-8")
        self._run()
        self.assertEqual("", self._fm(p)["ended"], "inside the 48h grace — flown-not-landed")

    def test_the_current_session_is_never_reaped(self):
        """Run from inside a live session, the verb can reach that session's own journal.
        Identity is the guarantee here, not just freshness."""
        p = self._journal("j-self", "csid-self", self._ago(8))
        self._exit_marker("csid-self", self._ago(7))
        import os
        os.environ["CLAUDE_CODE_SESSION_ID"] = "csid-self"
        try:
            self._run()
        finally:
            os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        self.assertEqual("", self._fm(p)["ended"])

    def test_dry_run_closes_nothing(self):
        """`--dry-run` predates the verb's ability to reach the reaper at all. A preview
        that quietly closed journals would be the worst defect available here — the one
        that looks like a preview."""
        p = self._journal("j-dry-reap", "csid-dry-reap", self._ago(8))
        self._exit_marker("csid-dry-reap", self._ago(7))
        self._run(dry_run=True)
        self.assertEqual("", self._fm(p)["ended"], "reported, not written")

    def test_dry_run_does_not_delete_a_phantom(self):
        """The reaper DELETES a journal it can prove lived seconds. Under a preview flag
        that deletion is unrecoverable, and a report is not worth a lost record."""
        started = self._ago(8)
        ended = (datetime.now(self.tz) - timedelta(days=8) + timedelta(seconds=30)).isoformat()
        # The REAL stub body — a phantom is recognised by exact match against the opening
        # template, so an approximation here would test nothing (it did: `"\n"` reads as
        # authored, and the case passed for the wrong reason until the delete assertion
        # disagreed).
        p = self._journal("j-phantom", "csid-phantom", started, body=session._STUB_BODY)
        self._exit_marker("csid-phantom", ended)
        self._run(dry_run=True)
        self.assertTrue(p.exists(), "a preview must never destroy a record")
        self._run()
        self.assertFalse(p.exists(), "and the real run still deletes it")

    def test_both_halves_run_in_one_pass(self):
        """Reaper first, janitor second — the same order as session start. The precise,
        evidence-based close gets first refusal; the aged-out fallback sees what's left."""
        reapable = self._journal("j-both-reap", "csid-both-reap", self._ago(8))
        self._exit_marker("csid-both-reap", self._ago(7))
        aged = self._journal("j-both-aged", "csid-both-aged", self._ago(30))
        self._commit(aged, self._ago(20))
        self._run()
        self.assertTrue(self._fm(reapable)["ended"])
        self.assertTrue(self._fm(aged)["ended"])
        self.assertNotIn("janitor", self._fm(reapable).get("closed-by", ""),
                         "an evidence-based close is not a janitor close and must not "
                         "be labelled as one")


class MachineCloseNeverTouchesTheRealRepoTest(JanitorBase):
    """WI-0146 — the suite must not close a real, tracked journal in its own checkout.

    THE FAILURE: a suite run could stamp `closed-by: janitor` into a tracked journal
    in its own checkout. A fresh clone then passed and the SECOND run in that clone failed, all in `test_claims`,
    which read the anchored checkout and found the journal the first run had mutated. It
    hid because every run on the runner host was from a lane, where the ambient and anchored
    checkouts are different files; in a main checkout they are the same file. The land
    gate runs the raw suite, so a second land in a row failed naming neither cause.

    The guard is at the write, not in each fixture, because a fixture that forgets is
    invisible — the WI-0126 lesson. These cases cover both directions: a rebound
    JOURNAL_DIR (every janitor test above) still sweeps, and the launch checkout does not.
    """

    def _aged_journal(self):
        p = self._journal("j-guard", "csid-guard", self._ago(30))
        self._commit(p, self._ago(20))
        return p

    def _exit_marker(self, csid, when):
        (session.SESSION_STATE_DIR / f"{csid}.ended").write_text(
            json.dumps({"ended": when, "reason": "prompt_input_exit"}), encoding="utf-8")
        (session.SESSION_STATE_DIR / f"{csid}.live").write_text(
            json.dumps({"last_beat": when}), encoding="utf-8")

    def test_a_fixture_bound_journal_dir_is_swept_normally(self):
        """The guard must not disarm the suite it protects: JOURNAL_DIR is the temp repo
        here, so the two directories differ and the sweep proceeds."""
        p = self._aged_journal()
        self.assertTrue(session._test_suite_is_running(),
                        "this assertion IS the detector — if it goes false the guard "
                        "below can never fire and both cases pass vacuously")
        res = session.janitor_sweep(None)
        self.assertEqual(["j-guard"], [s for s, _ in res["closed"]])
        self.assertNotIn("refused", res)

    def test_the_launch_checkouts_journals_are_refused_not_swept(self):
        p = self._aged_journal()
        with unittest.mock.patch.object(session, "_LAUNCH_JOURNAL_DIR",
                                        session.JOURNAL_DIR):
            res = session.janitor_sweep(None)
        self.assertEqual([], res["closed"])
        self.assertIn("refused", res)
        self.assertEqual("", self._fm(p)["ended"], "the journal was written anyway")

    def test_the_reaper_is_refused_on_the_launch_checkout_too(self):
        """Both machine-closers, not just the janitor — the reaper writes `ended` from
        sidecar evidence and would leave the same residue."""
        p = self._journal("j-reap-guard", "csid-reap-guard", self._ago(8))
        self._exit_marker("csid-reap-guard", self._ago(7))
        with unittest.mock.patch.object(session, "_LAUNCH_JOURNAL_DIR",
                                        session.JOURNAL_DIR):
            self.assertEqual([], session._reap_dead_journals(None))
        self.assertEqual("", self._fm(p)["ended"])
        self.assertTrue([s for s, _ in session._reap_dead_journals(None)],
                        "and with the guard off it still reaps — the refusal is scoped "
                        "to the launch checkout, not a blanket disablement")

    def test_the_detector_keys_on_path_not_module_name(self):
        """`unittest discover -s tests` names modules `test_x`; `-m unittest tests.test_x`
        names them `tests.test_x`. A name-based rule would miss one invocation, so the
        rule is 'a loaded module whose file lives under this checkout's tests/'."""
        src = inspect.getsource(session._test_suite_is_running)
        self.assertIn("__file__", src)
        self.assertNotIn('startswith("tests.")', src)


#: A real call into a machine-closer, as opposed to a mock target or a prose mention.
CALLS_MACHINE_CLOSER = re.compile(
    r"session\.(janitor_sweep|_reap_dead_journals|cmd_janitor)\s*\(")
#: Rebinding `JOURNAL_DIR`, in either idiom the suite uses.
REBINDS_JOURNAL_DIR = re.compile(
    r"session\.JOURNAL_DIR\s*=|patch\.object\(\s*session\s*,\s*[\"']JOURNAL_DIR[\"']")


class MachineCloserCallersRebindTheJournalDirTest(unittest.TestCase):
    """The detector for the WI-0146 guard — a leak nobody can re-introduce quietly.

    The runtime guard refuses the write, which makes the damage impossible; it does not
    make the MISTAKE visible. A fixture that sweeps with `JOURNAL_DIR` still pointing at
    the launch checkout is simply refused, and if it had no aged journal to close it
    passes anyway — green, and testing nothing it thinks it is testing. That is how the
    original leak survived: `test_lane_litter` rebound `ROOT` and `SESSION_STATE_DIR` and
    not `JOURNAL_DIR`, so its sidecar half used the fixture and its journal half used the
    real repo, and only the sidecar half was ever asserted on.
    """

    def _modules(self):
        d = pathlib.Path(__file__).resolve().parent
        for p in sorted(d.glob("test_*.py")):
            yield p, p.read_text(encoding="utf-8")

    def test_every_caller_rebinds_the_journal_dir(self):
        missing = [p.name for p, src in self._modules()
                   if CALLS_MACHINE_CLOSER.search(src) and not REBINDS_JOURNAL_DIR.search(src)]
        self.assertEqual(missing, [], (
            "these modules call a machine-closer (janitor_sweep / _reap_dead_journals / "
            "cmd_janitor) without ever rebinding `session.JOURNAL_DIR` to their fixture, "
            "so the journal half of the sweep aims at the real checkout's tracked "
            "journals (WI-0146). Rebind it in setUp beside ROOT and SESSION_STATE_DIR."))

    def test_at_least_one_module_is_actually_covered(self):
        """A rule that matches nothing reads green forever."""
        covered = [p.name for p, src in self._modules() if CALLS_MACHINE_CLOSER.search(src)]
        self.assertGreaterEqual(len(covered), 3, covered)

    def test_the_detector_would_actually_fire(self):
        unguarded = "res = session.janitor_sweep(None)\n"
        self.assertTrue(CALLS_MACHINE_CLOSER.search(unguarded))
        self.assertFalse(REBINDS_JOURNAL_DIR.search(unguarded))
        for idiom in ('session.JOURNAL_DIR = self.repo / "sessions"\n',
                      'mock.patch.object(session, "JOURNAL_DIR", self.jdir)\n'):
            self.assertTrue(REBINDS_JOURNAL_DIR.search(idiom), idiom)
        mention = "# the janitor_sweep story is in ADR-0051\n"
        self.assertIsNone(CALLS_MACHINE_CLOSER.search(mention),
                          "naming a closer in prose is not calling one")


class ParseIsoZuluTest(unittest.TestCase):
    """Under a UTC config git prints the commit time with a trailing `Z`, which
    fromisoformat rejects before 3.11 -- so the janitor silently fell back to `started`
    (found running the suite inside the public cut, WI-0449)."""

    def test_a_trailing_z_parses_as_utc(self):
        from sessionlib import config
        got = config._parse_iso("2026-09-08T03:00:00Z")
        self.assertEqual(got, datetime(2026, 9, 8, 3, 0, tzinfo=ZoneInfo("UTC")))


if __name__ == "__main__":
    unittest.main()
