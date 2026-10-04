"""The outbox channel — `curate/scrub.py`, `curate/outbox.py`, `curate/mail-poller.py`.

WHAT THESE PIN, and why each case exists rather than being obvious:

* **The scrub gate refuses the shape that motivated it.** Not an arbitrary string —
  the shape of a brief the FEDERATION once authored and sent: a host described by its
  interfaces, each with a private address. A gate tested only against a sample nobody
  was afraid of is a gate whose author never checked it against the thing they were
  afraid of.
* **A refusal leaves NOTHING behind.** The half-written-then-rejected file is how a
  later run ends up guessing whether a brief was cleared.
* **"Not locatable from this machine" is not an error.** It is the expected answer for
  most of the fleet on any box, and folding it into the failure count would make every
  healthy run look broken — the crying-wolf failure the delivery audit was rebuilt to
  end.
* **The status file distinguishes NEVER RUN from STALE from OK.** A poller that never
  started must not read as one that ran and found nothing; that collapse is the
  `declare-what-a-check-assumes` failure on a scheduled surface.
* **One bad brief does not block the queue.** A poller that aborts on the first error
  lets a single malformed file stop all mail, and the symptom looks nothing like
  the cause.
* **"They already have it" is not a delivery failure, and reporting it once is not
  enough.** The collision guard refusing a brief the recipient holds is EVIDENCE OF
  DELIVERY; counting it as `failed` reported a delivered brief as broken on every run,
  forever (WI-0235). Both halves are pinned: the state is its own bucket, and the copy is
  filed so the count clears — a fourth state that reported honestly and then repeated
  forever would fail the same test the third one failed. The control matters as much: a
  genuine same-name collision stays `failed` and stays queued, or the new bucket becomes
  a place failures go to be forgiven.
"""

import contextlib
import datetime
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_CURATE = pathlib.Path(__file__).resolve().parent.parent / "curate"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _CURATE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


scrub = _load("scrub", "scrub.py")
outbox = _load("outbox", "outbox.py")


class ScrubTest(unittest.TestCase):
    """The gate on tracked history."""

    def test_a_multi_homed_host_description_is_REFUSED(self):
        """The motivating shape: one host, two interfaces, each named with a private
        address. The addresses and host here are invented."""
        text = ("The example host has two interfaces:\n"
                "- `if0` — `192.168.44.7`\n"
                "- `if1` — `192.168.77.20`\n")
        hits = scrub.findings(text, "brief.md")
        self.assertEqual(len(hits), 2)
        self.assertEqual({h.cls for h in hits}, {"private-ip"})
        self.assertEqual([h.line for h in hits], [2, 3])

    def test_the_matched_VALUE_is_masked_in_the_report(self):
        """P14: the gate says something is there; it does not reproduce it into a
        terminal, a log or a transcript."""
        hits = scrub.findings("host at 192.168.44.7 here", "b.md")
        rendered = hits[0].render()
        self.assertNotIn("192.168.44.7", rendered)
        self.assertIn("xxx", rendered)

    def test_debugging_vocabulary_is_NOT_a_hit(self):
        """'diagnosis', 'symptom', 'routing', 'apiKeyHelper' are this repo's ordinary
        prose. A gate that fires on them trains people to --force, and a gate everyone
        forces is not a gate."""
        text = ("The diagnosis was a symptom of routing, and apiKeyHelper is a config "
                "key. Prescription: fix the dosage of retries.\n")
        self.assertEqual(scrub.findings(text, "b.md"), [])

    def test_documentation_ranges_and_placeholders_are_allowed(self):
        for benign in ("use 127.0.0.1 for local", "the 10.0.0.0/8 range", "0.0.0.0 binds all"):
            self.assertEqual(scrub.findings(benign, "b.md"), [], benign)

    def test_a_real_credential_assignment_is_REFUSED(self):
        hits = scrub.findings("api_key = sk-abcdef0123456789abcdef\n", "b.md")
        self.assertEqual([h.cls for h in hits], ["credential"])

    def test_an_unreadable_file_REFUSES_rather_than_passing(self):
        """'could not read it' and 'read it and it was clean' must not share an exit."""
        hits = scrub.scan_file("/nonexistent/definitely/not/here.md")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].cls, "unreadable")

    def test_force_passes_and_ANNOUNCES(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "b.md"
            p.write_text("host 192.168.44.7\n", encoding="utf-8")
            buf = io.StringIO()
            self.assertTrue(scrub.gate(p, force=True, out=buf))
            self.assertIn("OVERRIDDEN", buf.getvalue())
            self.assertIn("NOT clean", buf.getvalue())


class OutboxTest(unittest.TestCase):
    """Staging: the scrub gate, and the refusals that keep the queue honest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self._tmp.name)
        self._patches = [
            mock.patch.object(outbox, "ROOT", root),
            mock.patch.object(outbox, "OUTBOX", root / "outbox"),
            mock.patch.object(outbox, "DELIVERED", root / "outbox" / "delivered"),
            mock.patch.object(outbox, "FED_PROPOSED", root / "proposed-edits"),
        ]
        for p in self._patches:
            p.start()
        self.root = root
        self.src_dir = root / "src"
        self.src_dir.mkdir()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    def _brief(self, name, body="# clean\n\nnothing sensitive\n"):
        p = self.src_dir / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_a_clean_brief_is_queued_under_its_recipient(self):
        dest = outbox.stage("orbit-arch", self._brief("b.md"), out=io.StringIO())
        self.assertTrue(dest.is_file())
        self.assertEqual(dest.parent.name, "to-orbit-arch")
        self.assertEqual([("orbit-arch", dest)], outbox.pending())

    def test_a_dirty_brief_is_REFUSED_and_leaves_NOTHING_behind(self):
        src = self._brief("dirty.md", "gateway at 192.168.44.7\n")
        with self.assertRaises(ValueError):
            outbox.stage("orbit-arch", src, out=io.StringIO())
        self.assertFalse((self.root / "outbox" / "to-orbit-arch" / "dirty.md").exists())
        self.assertEqual(outbox.pending(), [])
        self.assertTrue(src.is_file(), "a refusal must not consume the source")

    def test_staging_REFUSES_to_overwrite_queued_mail(self):
        outbox.stage("orbit-arch", self._brief("b.md"), out=io.StringIO())
        with self.assertRaises(ValueError) as cm:
            outbox.stage("orbit-arch", self._brief("b.md", "# different\n"),
                         out=io.StringIO())
        self.assertIn("already exists", str(cm.exception))

    def test_a_brief_from_our_own_pending_dir_is_MOVED_not_copied(self):
        """Staging IS the send. Leaving the original in pending/ keeps the delivery
        audit reporting it as stuck forever."""
        pend = self.root / "proposed-edits" / "orbit-arch" / "pending"
        pend.mkdir(parents=True)
        src = pend / "b.md"
        src.write_text("# clean\n", encoding="utf-8")
        outbox.stage("orbit-arch", src, out=io.StringIO())
        self.assertFalse(src.exists())

    def test_a_brief_from_elsewhere_is_COPIED(self):
        src = self._brief("b.md")
        outbox.stage("orbit-arch", src, out=io.StringIO())
        self.assertTrue(src.is_file(), "a brief that is not ours is not ours to move")

    def test_delivered_is_not_read_back_as_a_recipient_queue(self):
        outbox.retire(self._brief("b.md"))
        self.assertEqual(outbox.pending(), [])

    def test_retire_never_overwrites_an_earlier_receipt(self):
        outbox.retire(self._brief("b.md", "first\n"))
        second = outbox.retire(self._brief("b.md", "second\n"))
        self.assertEqual(second.name, "b.2.md")
        self.assertEqual((self.root / "outbox" / "delivered" / "b.md").read_text(), "first\n")


class PollerTest(unittest.TestCase):
    """The drain. Loaded late because it imports `deliver`, which reads the real repo."""

    @classmethod
    def setUpClass(cls):
        cls.poller = _load("mail_poller", "mail-poller.py")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self._patches = [
            mock.patch.object(outbox, "OUTBOX", self.root / "outbox"),
            mock.patch.object(outbox, "DELIVERED", self.root / "outbox" / "delivered"),
            mock.patch.object(outbox, "FED_PROPOSED", self.root / "proposed-edits"),
            mock.patch.object(self.poller, "STATE_DIR", self.root / ".session-state"),
            mock.patch.object(self.poller, "STATUS_FILE",
                              self.root / ".session-state" / "s.json"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()

    # Every recipient these tests name, ON THE ROSTER. An empty registry is no longer a
    # neutral stand-in: WI-0336 split "no row in mailboxes.json" (nobody can ever deliver
    # this) out of "not reachable from here" (another machine will), so a fixture with no
    # rows makes every brief unroutable and quietly stops testing what its name says.
    ROSTER = {
        "alpha": {"architect_id": "alpha-arch"},
        "beta": {"architect_id": "beta-arch"},
        "orbit": {"architect_id": "orbit-arch"},
        "gamma": {"architect_id": "gamma-arch"},
    }

    def _queue(self, architect_id, name="b.md"):
        box = outbox.box_for(architect_id)
        box.mkdir(parents=True, exist_ok=True)
        (box / name).write_text("# clean\n", encoding="utf-8")

    def _member(self, sid, architect_id, recipient, name="m.md"):
        """A located member repo with one brief queued in its OWN outbox."""
        repo = self.root / "members" / sid
        (repo / "outbox" / f"to-{recipient}").mkdir(parents=True, exist_ok=True)
        (repo / "outbox" / f"to-{recipient}" / name).write_text("# theirs\n",
                                                                encoding="utf-8")
        (repo / "session.config.json").write_text(
            json.dumps({"architect_id": architect_id}), encoding="utf-8")
        return repo

    def test_an_unreachable_recipient_is_ELSEWHERE_not_FAILED(self):
        self._queue("orbit-arch")
        with mock.patch.object(self.poller.deliver, "resolve",
                               side_effect=ValueError("not locatable from this machine")), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}):
            r = self.poller.run_once(log=lambda *_: None)
        self.assertEqual(len(r["elsewhere"]), 1)
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["delivered"], [])

    def test_an_unreachable_brief_STAYS_QUEUED_for_the_machine_that_can_reach_it(self):
        self._queue("orbit-arch")
        with mock.patch.object(self.poller.deliver, "resolve",
                               side_effect=ValueError("nope")), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}):
            self.poller.run_once(log=lambda *_: None)
        self.assertEqual(len(outbox.pending()), 1)

    def test_one_bad_brief_does_not_block_the_others(self):
        self._queue("alpha-arch", "a.md")
        self._queue("beta-arch", "b.md")
        calls = {"n": 0}

        def _deliver(aid, path, *a, **k):
            calls["n"] += 1
            if aid == "alpha-arch":
                raise ValueError("malformed brief")
            return pathlib.Path("/dev/null")

        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver", side_effect=_deliver):
            r = self.poller.run_once(log=lambda *_: None)
        self.assertEqual(calls["n"], 2, "the run continued past the failure")
        self.assertEqual(len(r["failed"]), 1)
        self.assertEqual(len(r["delivered"]), 1)

    def test_a_dry_run_writes_nothing(self):
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver") as d:
            self.poller.run_once(dry_run=True, log=lambda *_: None)
        d.assert_not_called()
        self.assertEqual(len(outbox.pending()), 1)

    def test_status_says_NEVER_RUN_rather_than_quiet(self):
        _payload, (verdict, _why) = self.poller.read_status()
        self.assertEqual(verdict, "NEVER RUN")

    def test_status_says_STALE_when_the_schedule_stopped_firing(self):
        old = (self.poller._now()
               - datetime.timedelta(seconds=self.poller.STATUS_STALE_SECONDS + 60))
        self.poller.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.poller.STATUS_FILE.write_text(
            json.dumps({"last_run": self.poller._iso(old)}), encoding="utf-8")
        _payload, (verdict, _why) = self.poller.read_status()
        self.assertEqual(verdict, "STALE")

    # ── WI-0235: "could not deliver" and "did not need to" must not share an output ──

    def _already_held(self, aid="alpha-arch", eid="2026-09-03-a-brief"):
        return self.poller.deliver.AlreadyHeld(
            f"REFUSING to deliver to '{aid}': they already hold edit-id '{eid}'",
            architect_id=aid, edit_id=eid, where="/their/repo/applied/b.md")

    def _run_with(self, side_effect):
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver", side_effect=side_effect):
            return self.poller.run_once(log=lambda *_: None)

    def test_a_brief_the_recipient_ALREADY_HOLDS_is_not_counted_as_FAILED(self):
        """The defect exactly. The guard is right to refuse; nothing failed — the brief is
        in their mailbox. Folding the two into one count is what made the failed number
        unreadable."""
        self._queue("alpha-arch")
        r = self._run_with(self._already_held())
        self.assertEqual(r["failed"], [], "a delivered brief is not a failure")
        self.assertEqual(len(r["already_held"]), 1)
        self.assertEqual(r["already_held"][0]["edit_id"], "2026-09-03-a-brief")
        self.assertEqual(r["already_held"][0]["where"], "/their/repo/applied/b.md",
                         "say what convinced us, not just the verdict")

    def test_an_already_held_brief_is_FILED_so_the_count_actually_clears(self):
        """Reporting the state honestly and then repeating it every run would fail the
        same test the `failed` count failed: a permanently stuck number nobody reads."""
        self._queue("alpha-arch")
        self._run_with(self._already_held())
        self.assertEqual(outbox.pending(), [], "the queue is shorter than it was")
        self.assertTrue((self.root / "outbox" / "delivered" / "b.md").is_file(),
                        "filed, never deleted — our copy is the record we sent it")

    def test_the_stuck_forever_loop_is_GONE_across_two_runs(self):
        """The mechanism, not an assertion about it. Run one reports it; run two has
        nothing to report, because run one ended it. Before the fix this was `1 failed`
        on every run for as long as the file existed."""
        self._queue("alpha-arch")
        first = self._run_with(self._already_held())
        second = self._run_with(self._already_held())
        self.assertEqual(len(first["already_held"]), 1)
        self.assertEqual(second["queued"], 0)
        self.assertEqual((second["already_held"], second["failed"]), ([], []))

    def test_a_CONTROL_genuine_failure_is_still_FAILED_and_stays_queued(self):
        """The bucket must not become a place failures go to be forgiven. A same-name
        collision with no matching edit-id is two different briefs colliding — no evidence
        anything arrived — so it stays loud AND stays queued for a human."""
        self._queue("alpha-arch")
        r = self._run_with(ValueError("a different file of that name is already at ..."))
        self.assertEqual(len(r["failed"]), 1)
        self.assertEqual(r["already_held"], [])
        self.assertEqual(len(outbox.pending()), 1, "nothing arrived; nothing to file")

    def test_already_held_does_not_raise_the_exit_code(self):
        """The whole point is keeping the alarm channel clean. A non-zero exit would put
        this outcome straight back into it."""
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver",
                               side_effect=self._already_held()), \
             mock.patch.object(self.poller, "_Lock", contextlib.nullcontext), \
             mock.patch.object(self.poller, "persist"), \
             mock.patch.object(self.poller, "refresh",
                               return_value=self.poller.Refresh(False)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = self.poller.main(["--quiet"])
        self.assertEqual(rc, 0)
        self.assertIn("1 already held", out.getvalue(), "and it is named, not just quiet")
        self.assertIn("0 failed", out.getvalue())

    def _repo(self):
        """Turn the fixture root into a REAL git repo, and stop mocking git here.

        These two persist tests used to drive a fake `_git` and assert on the verb list.
        That could only ever assert the assumption back, and it stopped being able to see
        the subject at all once the commit moved into `outbox.publish` — the poller's own
        `_git` is no longer the thing that commits, so a run that committed correctly and a
        run that committed nothing would both have shown an empty verb list. The invariant
        was never "which git verbs ran"; it is whether a commit exists. Ask git."""
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], check=True)
        for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                     ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(self.root), "config", k, v], check=True)
        (self.root / "outbox").mkdir(parents=True, exist_ok=True)
        (self.root / "seed").write_text("seed\n")
        subprocess.run(["git", "-C", str(self.root), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "first"], check=True)
        return self.root

    def _subjects(self):
        return subprocess.run(["git", "-C", str(self.root), "log", "--format=%s"],
                              capture_output=True, text=True, check=True).stdout.split()

    def test_a_run_that_only_FILED_still_commits_the_retire(self):
        """A retire that never lands is a retire the next clone has not got — which is how
        the brief comes back, and the stuck count with it."""
        self._repo()
        self._queue("alpha-arch")
        self.poller.persist({"delivered": [],
                             "already_held": [{"architect_id": "alpha-arch"}]},
                            log=lambda *_: None)
        self.assertIn("chore(outbox):", " ".join(self._subjects()),
                      "a filed retire is committed like a delivery")

    def test_queued_mail_this_machine_cannot_deliver_is_committed_anyway(self):
        """WI-0230 — the gap that made a machine-written result unable to leave the machine.

        `persist` used to return early unless something had MOVED. A receipt queued for a
        recipient this host cannot reach moves nothing, so it was never committed — and it
        sat in the tree as the dirt that makes `refresh` decline to fast-forward on every
        later run. One uncommitted brief, and the checkout stops advancing."""
        self._repo()
        self._queue("beta-arch", name="receipt.md")
        self.poller.persist({"delivered": [], "already_held": [], "couriered": [],
                             "sender_files": [],
                             "elsewhere": [{"architect_id": "beta-arch"}]},
                            log=lambda *_: None)
        dirt = subprocess.run(["git", "-C", str(self.root), "status", "--porcelain",
                               "--", "outbox"],
                              capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(dirt, "", f"undeliverable mail left uncommitted: {dirt!r}")

    def test_status_is_OK_right_after_a_run(self):
        self.poller.write_status({"queued": 0, "delivered": [], "elsewhere": [],
                                  "already_held": [], "failed": []},
                                 refreshed=self.poller.Refresh(True))
        _payload, (verdict, _why) = self.poller.read_status()
        self.assertEqual(verdict, "OK")

    def test_an_unreadable_status_file_is_NOT_reported_as_healthy(self):
        self.poller.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.poller.STATUS_FILE.write_text("{ truncated", encoding="utf-8")
        _payload, (verdict, _why) = self.poller.read_status()
        self.assertEqual(verdict, "UNREADABLE")

    # ── WI-0336: a member's outbox is delivered, and their copy is still theirs ──────

    def _courier_run(self, deliver_fn=None, repos=None, **kw):
        """One run with a member repo in the locator. `deliver` succeeds unless told
        otherwise, so a test that cares about the failure path says so in one line."""
        def _ok(architect_id, path, *a, **k):
            return pathlib.Path("/their/mailbox") / path.name
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos",
                               return_value=repos if repos is not None else {}), \
             mock.patch.object(self.poller.deliver, "deliver",
                               side_effect=deliver_fn or _ok):
            return self.poller.run_once(log=lambda *_: None, **kw)

    def test_a_MEMBERS_queued_brief_is_actually_DELIVERED(self):
        """The defect itself. `_outbox_dirs` has read this surface since WI-0079 and only
        `audit()` ever reached it, so a member's brief was classified every startup and
        carried by nobody."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        r = self._courier_run(repos={"gamma": repo})
        self.assertEqual(len(r["couriered"]), 1)
        self.assertEqual(r["couriered"][0]["holder"], "gamma")
        self.assertEqual(r["delivered"], [], "it is not OUR delivery to book")

    def test_the_SENDERS_queued_copy_is_left_exactly_where_it_was(self):
        """The boundary `push-substrate.py` already draws: the federation may write its own
        generated substrate into any tree it reaches, never a target's authored files.
        Moving this one would commit to their history under our authorship."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        queued = repo / "outbox" / "to-alpha-arch" / "m.md"
        self._courier_run(repos={"gamma": repo})
        self.assertTrue(queued.is_file(), "their tracked file, their write")
        self.assertFalse((repo / "outbox" / "delivered").exists())

    def test_retire_REFUSES_a_brief_queued_in_someone_elses_outbox(self):
        """The structural guard, at the one function that could do the damage — rather
        than a rule the poller is trusted to keep remembering."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        with self.assertRaises(ValueError) as cm:
            outbox.retire(repo / "outbox" / "to-alpha-arch" / "m.md")
        self.assertIn("another member's outbox", str(cm.exception))

    def test_delivers_OWN_retire_path_still_moves_nothing_of_theirs(self):
        """`deliver.deliver` calls `_retire_source`, in a module the poller does not own.
        Its containment test is load-bearing here, so it is pinned here."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        src = repo / "outbox" / "to-alpha-arch" / "m.md"
        self.poller.deliver._retire_source(src)
        self.assertTrue(src.is_file(), "a brief from somewhere else is not ours to move")

    def test_a_couriered_brief_the_recipient_HOLDS_is_not_already_held(self):
        """`already_held` files our copy to `delivered/`. There is no copy of ours to file
        here, and pretending otherwise is what would reach into their tree."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        queued = repo / "outbox" / "to-alpha-arch" / "m.md"
        r = self._courier_run(deliver_fn=self._already_held(), repos={"gamma": repo})
        self.assertEqual(len(r["sender_files"]), 1)
        self.assertEqual(r["already_held"], [])
        self.assertEqual(r["failed"], [], "it ARRIVED; nothing failed")
        self.assertTrue(queued.is_file())

    def test_OUR_OWN_outbox_still_delivers_and_retires_unchanged(self):
        """The control. Widening the sweep must not cost us the half that worked."""
        self._queue("alpha-arch")
        r = self._courier_run()
        self.assertEqual(len(r["delivered"]), 1)
        self.assertEqual(r["couriered"], [])
        self.assertEqual(outbox.pending(), [], "ours, and ours to file")

    def test_our_own_tree_reached_as_a_MEMBER_is_not_swept_twice(self):
        """The federation joined the locator map so briefs could be delivered TO the hub,
        which enrols us in this sweep as well. Identity is the repo's own declared
        architect_id, so a lane worktree and the main checkout both answer and both skip."""
        us = self.root
        (us / "session.config.json").write_text(
            json.dumps({"architect_id": self.poller.deliver.FED_ARCH_ID}),
            encoding="utf-8")
        self._queue("alpha-arch")
        r = self._courier_run(repos={"federation": us})
        self.assertEqual(r["queued"], 1, "counted once, from pending()")
        self.assertEqual(r["couriered"], [])

    def test_a_recipient_with_NO_REGISTRY_ROW_is_unroutable_not_elsewhere(self):
        """`elsewhere` promises another machine finishes the job. Nobody can resolve a
        recipient `mailboxes.json` has never heard of, so filing them there hands the brief
        to a courier who does not exist — WI-0336's own failure, one bucket over."""
        self._queue("nobody-arch")
        r = self._courier_run()
        self.assertEqual(len(r["unroutable"]), 1)
        self.assertEqual(r["elsewhere"], [])

    def test_unroutable_is_PRINTED_even_under_quiet_and_does_not_fail_the_run(self):
        """Its remedy is a registry row a person adds, so a non-zero exit would leave the
        scheduled job failing for days — but silence would lose the only report of it."""
        self._queue("nobody-arch")
        with mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller, "_Lock", contextlib.nullcontext), \
             mock.patch.object(self.poller, "persist"), \
             mock.patch.object(self.poller, "refresh",
                               return_value=self.poller.Refresh(False)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = self.poller.main(["--quiet"])
        self.assertEqual(rc, 0)
        self.assertIn("UNROUTABLE", out.getvalue())
        self.assertIn("mailboxes.json", out.getvalue(), "and it names the fix")

    def test_a_couriered_only_run_commits_NOTHING(self):
        """`persist` is pathspec'd to OUR outbox. A couriered run moved nothing there, and
        a commit naming those recipients would claim a write we did not make.

        Asserted as "no commit exists", not as "no git command ran". The run now always
        asks git what is staged — it has to, since undeliverable mail of our own must be
        committed even on a run that delivered nothing — so the old assertion would have
        been read as this one while testing something weaker: a program that committed the
        couriered names would fail this and pass that."""
        repo = self._repo()
        before = self._subjects()
        self.poller.persist({"delivered": [], "already_held": [],
                             "couriered": [{"architect_id": "alpha-arch"}],
                             "sender_files": []}, log=lambda *_: None)
        self.assertEqual(self._subjects(), before,
                         "nothing of ours changed, so nothing is committed")
        self.assertTrue((repo / ".git").is_dir())

    # ── WI-0336: a rehearsal that skips the gates rehearses nothing ─────────────────

    def _malformed(self, brief="m.md"):
        return self.poller.deliver.MalformedBrief(
            f"REFUSING to deliver {brief} — its apply mode routes it to nobody",
            brief=brief, detail="apply: manual (agent path) with no `verify:`")

    def test_a_brief_whose_HEADER_routes_it_nowhere_is_not_FAILED(self):
        """It is refused identically on every run until its author edits the header, so
        `failed` would be pinned non-zero forever — on this program's own schedule, which
        is the harm WI-0235 names."""
        self._queue("alpha-arch")
        r = self._courier_run(deliver_fn=self._malformed())
        self.assertEqual(len(r["malformed"]), 1)
        self.assertEqual(r["failed"], [])
        self.assertIn("verify", r["malformed"][0]["detail"], "and it carries the report")

    def test_a_malformed_brief_does_not_raise_the_exit_code_but_is_PRINTED(self):
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver",
                               side_effect=self._malformed()), \
             mock.patch.object(self.poller, "_Lock", contextlib.nullcontext), \
             mock.patch.object(self.poller, "persist"), \
             mock.patch.object(self.poller, "refresh",
                               return_value=self.poller.Refresh(False)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            rc = self.poller.main(["--quiet"])
        self.assertEqual(rc, 0)
        self.assertIn("MALFORMED", out.getvalue())
        self.assertIn("0 failed", out.getvalue())

    def test_a_MISSING_CHECKER_is_still_FAILED_not_malformed(self):
        """The control for the split, at the poller's routing layer."""
        self._queue("alpha-arch")
        r = self._courier_run(
            deliver_fn=ValueError("cannot verify apply mode: check-apply.py is missing"))
        self.assertEqual(len(r["failed"]), 1)
        self.assertEqual(r["malformed"], [])

    def test_a_MISSING_CHECKER_raises_a_BARE_ValueError_not_the_type(self):
        """The same control one layer down, against the real `_check_apply_mode`. The test
        above pins how the poller ROUTES the exception; this pins which exception
        `deliver.py` chooses, and only this one fails if that choice is wrong. Nothing is
        known about the brief, the fault is in OUR tooling, and a re-run clears it — so it
        must stay in the alarm channel rather than joining the standing census."""
        brief = self.root / "any.md"
        brief.write_text("# brief\n", encoding="utf-8")
        with mock.patch.object(self.poller.deliver, "CHECK_APPLY",
                               self.root / "no-such-checker.py"):
            with self.assertRaises(ValueError) as cm:
                self.poller.deliver._check_apply_mode(brief)
        self.assertNotIsInstance(cm.exception, self.poller.deliver.MalformedBrief)
        self.assertIn("cannot verify apply mode", str(cm.exception))

    def test_a_DRY_RUN_applies_the_same_refusals_the_write_path_does(self):
        """The defect this pins, exactly: `--dry-run` used to return before
        `deliver.deliver()` and therefore before both of its gates, so it reported 11
        deliveries the live run refused — and that number reached a journal before anyone
        ran the real thing."""
        self._queue("alpha-arch")
        seen = []
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "_check_apply_mode",
                               side_effect=lambda pth: seen.append("apply")), \
             mock.patch.object(self.poller.deliver, "_check_collision",
                               side_effect=lambda *a: seen.append("collision")), \
             mock.patch.object(self.poller.deliver, "deliver") as wrote:
            r = self.poller.run_once(dry_run=True, log=lambda *_: None)
        self.assertEqual(seen, ["apply", "collision"], "both gates, in the write order")
        wrote.assert_not_called()
        self.assertEqual(len(r["delivered"]), 1)

    def test_a_dry_run_REFUSES_what_the_live_run_would_refuse(self):
        """The agreement itself, not the plumbing: a brief the gate rejects must be
        rejected in rehearsal too, or the rehearsal is a false assurance."""
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "_check_apply_mode",
                               side_effect=self._malformed()):
            r = self.poller.run_once(dry_run=True, log=lambda *_: None)
        self.assertEqual(len(r["malformed"]), 1)
        self.assertEqual(r["delivered"], [], "it would NOT have been delivered")

    def test_a_dry_run_still_files_NOTHING(self):
        """A rehearsal that now runs the collision guard must not act on what it finds."""
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "_check_apply_mode"), \
             mock.patch.object(self.poller.deliver, "_check_collision",
                               side_effect=self._already_held()):
            r = self.poller.run_once(dry_run=True, log=lambda *_: None)
        self.assertEqual(len(r["already_held"]), 1)
        self.assertEqual(len(outbox.pending()), 1, "nothing was filed")

    # ─────────────────────────────────────────── WI-0251: the rehearsal and the run agree
    #
    # A brief that satisfies the REAL `check-apply.py` — `apply: auto` needs all four of
    # these keys — and whose `edit-id` line doubles as what `deliver.brief_edit_id` reads.
    # The fixtures above stub both guards out, which pins how the poller ROUTES an
    # exception and proves nothing about the VALUES a real refusal carries; the tests
    # below run the guards for real precisely to close that gap.
    _HELD_BRIEF = ("---\n"
                   "apply: auto\n"
                   "edit-id: 2026-09-12-a-brief-they-already-hold\n"
                   "target-file: ROLE.md\n"
                   "expected-base-version: 1.0.0\n"
                   "proposed-new-version: 1.1.0\n"
                   "---\n\n# fixture\n")

    # `recipient_mailbox_tree` reads `row["mailbox"]` directly, not with `.get` — the
    # ROSTER above gets away without it only because its `locate_repos` is empty and
    # `_locate` returns None first. A fixture that actually resolves needs the key.
    def _held_fixture(self):
        """Our queue holds a brief the recipient ALREADY HOLDS, under a different name.

        Different name on purpose: same-name-different-id is `_check_collision`'s third
        refusal and a plain `ValueError`, so a matching filename would let this fixture
        pass for the wrong reason."""
        repo = self.root / "alpha"
        box = repo / "proposed-edits" / "alpha-arch" / "pending"
        box.mkdir(parents=True, exist_ok=True)
        (box / "they-already-have-this.md").write_text(self._HELD_BRIEF, encoding="utf-8")
        (repo / "session.config.json").write_text(
            json.dumps({"architect_id": "alpha-arch"}), encoding="utf-8")
        ours = outbox.box_for("alpha-arch")
        ours.mkdir(parents=True, exist_ok=True)
        (ours / "b.md").write_text(self._HELD_BRIEF, encoding="utf-8")
        roster = {"alpha": {"architect_id": "alpha-arch", "tracked": False,
                            "reachable": True,
                            "mailbox": "proposed-edits/alpha-arch/pending"}}
        return roster, {"alpha": repo}

    def _held_run(self, dry_run):
        """The identical fixture, run for real — no guard is stubbed."""
        roster, repos = self._held_fixture()
        with mock.patch.object(self.poller.deliver, "load_mailboxes", return_value=roster), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value=repos):
            return self.poller.run_once(dry_run=dry_run, log=lambda *_: None)

    def test_a_dry_run_and_the_live_run_AGREE_on_a_brief_ALREADY_HELD(self):
        """WI-0251, and the only test here that can settle it: the SAME fixture through
        both paths, compared field by field.

        The run whose whole job is to say what will happen was the one run that could not
        say this. Reporting the right BUCKET is not enough — an `already_held` verdict
        that cannot name the edit-id it matched or the recipient-side path it saw is the
        WI-0101 shape, a verdict that cannot say what convinced it, and a rehearsal is
        read by someone deciding whether to run the real thing.

        The ONE field that must differ is `filed`, and it differs in the honest
        direction: the live run names where it put our copy, the rehearsal says
        `(dry-run)` because it put it nowhere. Predicting the path instead would be a
        guess — `outbox.retire` picks a non-colliding name at write time — printed in a
        receipt field."""
        dry = self._held_run(dry_run=True)
        self.assertEqual(len(outbox.pending()), 1, "the rehearsal retired nothing")

        live = self._held_run(dry_run=False)
        self.assertEqual(outbox.pending(), [], "the live run retired our copy")

        self.assertEqual(len(dry["already_held"]), 1, "the rehearsal reported it AT ALL")
        self.assertEqual(len(live["already_held"]), 1)
        d, l = dry["already_held"][0], live["already_held"][0]
        for field in ("architect_id", "brief", "edit_id", "where"):
            self.assertEqual(d[field], l[field], f"rehearsal disagrees about {field}")
        self.assertEqual(d["edit_id"], "2026-09-12-a-brief-they-already-hold")
        self.assertTrue(d["where"].endswith("they-already-have-this.md"),
                        f"the rehearsal must cite the recipient-side copy, got {d['where']}")
        self.assertEqual(d["filed"], "(dry-run)")
        self.assertNotEqual(l["filed"], "(dry-run)", "the live run filed it somewhere real")

        for bucket in ("queued", "delivered", "elsewhere", "couriered", "sender_files",
                       "unroutable", "malformed", "failed"):
            self.assertEqual(dry[bucket], live[bucket], f"buckets differ: {bucket}")

    def test_the_rehearsal_gates_through_the_WRITE_PATHS_OWN_precheck(self):
        """The class, not the instance. The first repair re-listed `deliver()`'s gates at
        this call site; two hand-maintained copies of one sequence is how the rehearsal
        drifted to begin with, so what is pinned is that there is ONE object and the dry
        run calls it."""
        self._queue("alpha-arch")
        with mock.patch.object(self.poller.deliver, "precheck") as gate, \
             mock.patch.object(self.poller.deliver, "resolve", return_value=(1, 2, 3)), \
             mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=self.ROSTER), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value={}), \
             mock.patch.object(self.poller.deliver, "deliver") as wrote:
            self.poller.run_once(dry_run=True, log=lambda *_: None)
        gate.assert_called_once()
        wrote.assert_not_called()

    def test_the_WRITE_PATH_gates_through_precheck_too(self):
        """The other half, and the half that makes the first half mean anything: if
        `deliver()` ever grew a refusal `precheck` did not carry, the rehearsal would
        silently stop rehearsing it while every test above still passed."""
        box = self.root / "their-box"
        box.mkdir()
        src = self.root / "outgoing.md"
        src.write_text(self._HELD_BRIEF, encoding="utf-8")
        with mock.patch.object(self.poller.deliver, "precheck",
                               return_value=(self.root, box, {})) as gate:
            dest = self.poller.deliver.deliver("alpha-arch", src, self.ROSTER, {})
        gate.assert_called_once()
        self.assertEqual(dest, box / "outgoing.md")
        self.assertEqual(dest.read_text(encoding="utf-8"), self._HELD_BRIEF)

    def test_a_members_brief_that_genuinely_FAILS_is_still_failed(self):
        """The control for the split. Widening the alarm's exemptions must not widen them
        past the one case that earns it."""
        repo = self._member("gamma", "gamma-arch", "alpha-arch")
        r = self._courier_run(deliver_fn=ValueError("malformed brief"),
                              repos={"gamma": repo})
        self.assertEqual(len(r["failed"]), 1)
        self.assertEqual(r["failed"][0]["holder"], "gamma")
        self.assertEqual(r["couriered"], [])


if __name__ == "__main__":
    unittest.main()
