"""The retired read-federation-direct inbox refuses by name (WI-0012, ADR-0028).

Under ADR-0027 §2 a member read its briefs straight out of the federation's own
`proposed-edits/<member>-arch/pending/`, so writing there WAS delivery. ADR-0028
moved every member onto its own repo-local inbox, and the poller drains one queue,
`outbox/to-<id>/`. Two ways back to the old model are left, and both used to lose mail
quietly:

  send side   a registry row that resolves into our tree. `deliver` copied the brief,
              read it back and reported DELIVERED, into a directory no session reads.
  queue side  a brief written into our `proposed-edits/<member>/pending/` the old way.
              The poller never looked there, so no run mentioned it.

Each now refuses with a reason that names ADR-0028 and the fix. Every refusal below is
pinned next to its control, so a guard that fired on everything, or on nothing, fails
here.
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

_CURATE = pathlib.Path(__file__).resolve().parent.parent / "curate"
sys.path.insert(0, str(_CURATE))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _CURATE / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


deliver_mod = _load("deliver_retired_under_test", "deliver.py")

_ROUTABLE_BRIEF = (
    "---\n"
    "edit-id: fixture-retired\n"
    "from: federation-arch\n"
    "to: alpha-arch\n"
    "apply: manual\n"
    "manual-reason: attended\n"
    "expected-base: n/a\n"
    "---\n\n# fixture\n"
)


def _rows(**over):
    base = {
        "alpha": {"architect_id": "alpha-arch",
                  "mailbox": "proposed-edits/alpha-arch/pending",
                  "tracked": False, "reachable": True},
        "federation": {"architect_id": "federation-arch",
                       "mailbox": "proposed-edits/federation-arch/pending",
                       "tracked": False, "reachable": True},
        "consultant": {"architect_id": "consultant", "hosted_by": "federation",
                       "mailbox": "proposed-edits/consultant/pending",
                       "tracked": False, "reachable": True},
    }
    base.update(over)
    return base


class _Tree(unittest.TestCase):
    """A federation tree and a member repo, both on disk, with the module's view of
    "the federation's own proposed-edits/" pointed at the fixture's."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fed = self.tmp / "fed"
        self.fed_proposed = self.fed / "proposed-edits"
        for aid in ("federation-arch", "consultant", "alpha-arch"):
            (self.fed_proposed / aid / "pending").mkdir(parents=True)
        self.alpha = self.tmp / "alpha"
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)

    def _brief(self, where, name="b.md"):
        where.mkdir(parents=True, exist_ok=True)
        p = where / name
        p.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        return p


class SendSideTest(_Tree):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(deliver_mod, "FED_PROPOSED", self.fed_proposed)
        p.start()
        self.addCleanup(p.stop)

    def test_a_member_row_that_resolves_into_our_tree_is_refused_by_name(self):
        # The locator maps alpha onto the FEDERATION's tree: alpha's mailbox path then
        # lands in our proposed-edits/, the read-direct location.
        with self.assertRaises(deliver_mod.RetiredInbox) as e:
            deliver_mod.resolve("alpha-arch", _rows(), {"alpha": self.fed,
                                                        "federation": self.fed})
        msg = str(e.exception)
        self.assertIn("ADR-0028", msg)
        self.assertIn("read-federation-direct", msg)
        self.assertIn("Nothing was written", msg)
        self.assertEqual(e.exception.outcome, "retired")

    def test_deliver_writes_nothing_into_the_retired_inbox(self):
        src = self._brief(self.tmp / "src")
        box = self.fed_proposed / "alpha-arch" / "pending"
        with self.assertRaises(deliver_mod.RetiredInbox):
            deliver_mod.deliver("alpha-arch", src, _rows(), {"alpha": self.fed,
                                                            "federation": self.fed})
        self.assertEqual(list(box.iterdir()), [], "the refusal must leave no copy behind")

    def test_the_same_member_in_its_own_repo_still_resolves(self):
        # Control: the guard is about WHERE the mailbox resolves, not about the id.
        _repo, box, _row = deliver_mod.resolve("alpha-arch", _rows(),
                                               {"alpha": self.alpha, "federation": self.fed})
        self.assertEqual(box, self.alpha / "proposed-edits" / "alpha-arch" / "pending")

    def test_our_own_inbox_is_not_retired(self):
        _repo, box, _row = deliver_mod.resolve("federation-arch", _rows(),
                                               {"federation": self.fed},
                                               production_roots=None)
        self.assertEqual(box, self.fed_proposed / "federation-arch" / "pending")

    def test_a_row_that_declares_it_is_hosted_here_is_not_retired(self):
        # The consultant holds no repo; its mailbox lives in our tree BY DECLARATION.
        _repo, box, _row = deliver_mod.resolve("consultant", _rows(),
                                               {"federation": self.fed})
        self.assertEqual(box, self.fed_proposed / "consultant" / "pending")

    def test_the_check_compares_resolved_paths(self):
        # A lane reaches our proposed-edits/ through a symlink into the main checkout.
        link = self.tmp / "lane"
        link.symlink_to(self.fed, target_is_directory=True)
        with self.assertRaises(deliver_mod.RetiredInbox):
            deliver_mod.resolve("alpha-arch", _rows(), {"alpha": link})


class PollerTest(_Tree):
    def setUp(self):
        super().setUp()
        self.poller = _load("mail_poller_retired_under_test", "mail-poller.py")
        self.outbox_dir = self.tmp / "outbox"
        for p in (
            mock.patch.object(self.poller.deliver, "FED_PROPOSED", self.fed_proposed),
            mock.patch.object(self.poller.outbox, "FED_PROPOSED", self.fed_proposed),
            mock.patch.object(self.poller.outbox, "OUTBOX", self.outbox_dir),
            mock.patch.object(self.poller.outbox, "DELIVERED", self.outbox_dir / "delivered"),
            mock.patch.object(self.poller.production, "resolve", return_value=None),
        ):
            p.start()
            self.addCleanup(p.stop)

    def _run(self, repos, rows=None, **kw):
        with mock.patch.object(self.poller.deliver, "load_mailboxes",
                               return_value=rows or _rows()), \
             mock.patch.object(self.poller.deliver, "locate_repos", return_value=repos):
            return self.poller.run_once(log=lambda *_: None, **kw)

    def test_a_brief_left_in_the_read_direct_location_is_named_not_ignored(self):
        left = self._brief(self.fed_proposed / "alpha-arch" / "pending")
        with mock.patch.object(self.poller.deliver, "deliver") as d:
            r = self._run({})
        d.assert_not_called()
        self.assertEqual(r["queued"], 0, "the old location is not a queue the poller drains")
        self.assertEqual(len(r["retired"]), 1)
        item = r["retired"][0]
        self.assertEqual((item["architect_id"], item["brief"]), ("alpha-arch", "b.md"))
        self.assertIn("ADR-0028", item["why"])
        self.assertIn("outbox.py stage alpha-arch", item["why"])
        self.assertTrue(left.is_file(), "naming it must not move or delete it")

    def test_a_dry_run_names_it_too(self):
        self._brief(self.fed_proposed / "alpha-arch" / "pending")
        r = self._run({}, dry_run=True)
        self.assertEqual(len(r["retired"]), 1)

    def test_our_inbox_and_a_hosted_inbox_are_mail_that_arrived(self):
        # Controls for the queue side: both directories hold briefs, neither is retired.
        self._brief(self.fed_proposed / "federation-arch" / "pending")
        self._brief(self.fed_proposed / "consultant" / "pending")
        r = self._run({})
        self.assertEqual(r["retired"], [])

    def test_a_hosted_row_is_exempt_only_where_it_declares(self):
        # `hosted_by` exempts the directory the row NAMES. The same id with no hosted
        # declaration is the old model and is named.
        self._brief(self.fed_proposed / "consultant" / "pending")
        rows = _rows(consultant={"architect_id": "consultant",
                                 "mailbox": "proposed-edits/consultant/pending",
                                 "tracked": False, "reachable": True})
        r = self._run({}, rows=rows)
        self.assertEqual([i["architect_id"] for i in r["retired"]], ["consultant"])

    def test_a_queued_brief_whose_row_points_at_our_tree_is_retired_not_elsewhere(self):
        self._brief(self.outbox_dir / "to-alpha-arch")
        r = self._run({"alpha": self.fed, "federation": self.fed})
        self.assertEqual(len(r["retired"]), 1)
        self.assertIn("ADR-0028", r["retired"][0]["why"])
        self.assertEqual(r["elsewhere"], [], "no other machine will carry this either")
        self.assertEqual(r["failed"], [])
        self.assertEqual(r["delivered"], [])
        self.assertEqual(list((self.fed_proposed / "alpha-arch" / "pending").iterdir()), [])
        self.assertTrue((self.outbox_dir / "to-alpha-arch" / "b.md").is_file(),
                        "it stays queued until someone fixes the row")

    def test_the_converged_path_still_delivers(self):
        # Control for the one above: the same queued brief, the member in its own repo.
        # A dry run: it goes through the real `resolve` and `precheck`, the same refusals
        # the live write applies, and stops only before the copy.
        self._brief(self.outbox_dir / "to-alpha-arch")
        r = self._run({"alpha": self.alpha, "federation": self.fed}, dry_run=True)
        self.assertEqual(r["retired"], [])
        self.assertEqual(r["failed"], [])
        self.assertEqual([i["architect_id"] for i in r["delivered"]], ["alpha-arch"])


class PollerReportTest(unittest.TestCase):
    """A retired path prints past --quiet and does not set the exit code: the fix is a
    person staging a brief or correcting a row, and a launchd job failing until then is
    the alarm nobody reads again (WI-0235)."""

    def test_printed_under_quiet_and_exit_zero(self):
        poller = _load("mail_poller_retired_report", "mail-poller.py")
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = {"queued": 0, "delivered": [], "elsewhere": [], "already_held": [],
                  "couriered": [], "sender_files": [], "unroutable": [], "malformed": [],
                  "failed": [],
                  "retired": [{"architect_id": "alpha-arch", "brief": "b.md",
                               "holder": "", "why": "names ADR-0028"}]}
        out = io.StringIO()
        with mock.patch.object(poller.production, "resolve", return_value=None), \
             mock.patch.object(poller, "STATE_DIR", tmp), \
             mock.patch.object(poller, "STATUS_FILE", tmp / "s.json"), \
             mock.patch.object(poller, "LOCK_FILE", tmp / "l.lock"), \
             mock.patch.object(poller, "run_once", return_value=result), \
             mock.patch.object(poller, "persist"), \
             contextlib.redirect_stdout(out):
            rc = poller.main(["--quiet", "--no-refresh", "--no-deploy-sweep"])
        self.assertEqual(rc, 0)
        self.assertIn("RETIRED PATH: b.md -> alpha-arch", out.getvalue())
        self.assertEqual(json.loads((tmp / "s.json").read_text())["retired"], 1)


if __name__ == "__main__":
    unittest.main()
