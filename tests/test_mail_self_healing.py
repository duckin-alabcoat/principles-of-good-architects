"""Federation mail heals itself on a deploy, on both machines (WI-0428, WI-0429).

WI-0428: the roster the worker routes by (`<config_root>/mailboxes.json`) was seeded once
and never refreshed, so one machine could route by a stale roster while the other's
messages sat unroutable. WI-0429: `recipient_refs` was never derived, so a host could
accept no acknowledgment until someone hand-wrote the map, and its pending count grew
for recipients the hand map missed.

Every configuration here starts with NO hand-written map; that is the point.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
sys.path.insert(0, str(REPO / "curate"))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import devbox_mail                                                  # noqa: E402
import mailworker                                                    # noqa: E402
import migrate                                                       # noqa: E402
import production                                                    # noqa: E402
from test_devbox_mail import DevboxCase, _git                     # noqa: E402
from test_federation_migration import MigrationCase                 # noqa: E402

DEV = "refs/heads/dev/messages"
RUNNER = "refs/heads/runner/messages"

#: A roster shape from before WI-0422 re-keyed a row: the orchestrator's row keyed
#: `example-app`, so `runner.recipient_architect("agent-one")` derived `agent-one-arch`.
#: The roster and the deploy list are invented; the shape that matters is a deployed
#: system (`gamma`) the roster does not name, alongside rows it does.
STALE_ROSTER = {"members": {
    key: {"architect_id": aid} for key, aid in (
        ("federation", "federation-arch"), ("example-app", "example-app-arch"),
        ("alpha", "alpha-arch"), ("beta", "beta-arch"),
        ("consultant", "consultant"))}}
DEPLOY_SYSTEMS = ["alpha", "gamma", "agent-one", "federation"]


class TheDerivedMapIsTheOneTheConsultantWroteByHand(unittest.TestCase):
    """The hand map, as recorded (WI-0415 note): "every <member>-arch + the
    orchestrator's -arch id + consultant -> refs/heads/dev/messages". Its bytes were never
    read from devbox, so this pins the paraphrase as a LITERAL list; the byte proof is
    `migrate.py recipient-refs` run against the Runner's own file, which exits 0 only on an
    exact match."""

    HAND = {rid: DEV for rid in (
        "federation-arch", "example-app-arch", "alpha-arch", "beta-arch",
        "gamma-arch", "agent-one-arch", "consultant")}

    RUNNER_CONFIG = {"transport": {"owned_ref": RUNNER, "input_refs": [DEV]}}

    def test_a_runner_config_derives_exactly_the_hand_map(self):
        self.assertEqual(migrate.derive_recipient_refs(self.RUNNER_CONFIG, STALE_ROSTER,
                                                       DEPLOY_SYSTEMS), self.HAND)

    def test_the_rekeyed_roster_derives_the_same_map(self):
        """After WI-0422 the row is keyed `agent-one`; the registry still supplies
        `agent-one-arch`, so messages already queued for that id keep an ack source."""
        roster = json.loads(json.dumps(STALE_ROSTER))
        roster["members"]["agent-one"] = roster["members"].pop("example-app")
        self.assertEqual(migrate.derive_recipient_refs(self.RUNNER_CONFIG, roster,
                                                       DEPLOY_SYSTEMS), self.HAND)

    def test_devbox_derives_the_reverse_map(self):
        devbox = {"transport": {"owned_ref": DEV, "input_refs": [RUNNER]}}
        derived = migrate.derive_recipient_refs(devbox, STALE_ROSTER, DEPLOY_SYSTEMS)
        self.assertEqual(set(derived), set(self.HAND))
        self.assertEqual(set(derived.values()), {RUNNER})

    def test_an_ambiguous_ack_source_derives_nothing(self):
        for refs in ([], [DEV, "refs/heads/elsewhere"], None):
            config = {"transport": {"owned_ref": RUNNER, "input_refs": refs}}
            self.assertEqual(migrate.derive_recipient_refs(config, STALE_ROSTER,
                                                           DEPLOY_SYSTEMS), {})

    def test_the_check_verb_says_exact_only_on_an_exact_match(self):
        holder = tempfile.TemporaryDirectory(prefix="mail-self-healing-")
        self.addCleanup(holder.cleanup)
        tmp = Path(holder.name)
        roster = tmp / "mailboxes.json"
        roster.write_text(json.dumps(STALE_ROSTER))
        config = tmp / "service.json"
        body = {"schema_version": 2, "code_root": "/c", "state_root": "/s",
                "transport_root": "/t",
                "transport": {"owned_ref": RUNNER, "input_refs": [DEV],
                              "recipient_refs": dict(self.HAND)}}
        config.write_text(json.dumps(body))
        self.assertTrue(migrate.check_recipient_refs(config, roster, DEPLOY_SYSTEMS)["exact"])
        del body["transport"]["recipient_refs"]["consultant"]
        config.write_text(json.dumps(body))
        result = migrate.check_recipient_refs(config, roster, DEPLOY_SYSTEMS)
        self.assertFalse(result["exact"])
        self.assertEqual(result["only_derived"], ["consultant"])


class AFreshRunnerDeployNeedsNoHandConfig(MigrationCase):
    """`prepare` is the contract's `apply`, so it runs on every federation deploy."""

    def _refs(self):
        return self._roots().transport.get("recipient_refs")

    def test_an_authored_config_with_no_map_gets_one(self):
        self._prepare()
        # The fixture roster names runner-test-arch; the fixture registry names federation.
        self.assertEqual(self._refs(), {"runner-test-arch": DEV, "federation-arch": DEV})

    def test_a_derived_config_gets_one_too(self):
        self.service.unlink()
        self._prepare()
        document = json.loads(self.service.read_text())
        self.assertEqual(document["transport"]["recipient_refs"],
                         {"runner-test-arch": DEV, "federation-arch": DEV})

    def test_every_derived_entry_is_the_source_the_ack_check_demands(self):
        """`maildelivery._apply_ack` accepts an ack only when recipient_refs[destination]
        equals the ref the ack arrived on — which, for the Runner, is its one input ref."""
        self._prepare()
        roots = self._roots()
        for destination, ref in roots.transport["recipient_refs"].items():
            self.assertEqual(ref, roots.transport["input_refs"][0], destination)

    def test_an_authored_entry_is_kept_and_only_the_missing_ones_added(self):
        document = json.loads(self.service.read_text())
        document["transport"]["recipient_refs"] = {"runner-test-arch": "refs/heads/by/hand"}
        self.service.write_text(json.dumps(document))
        self._prepare()
        self.assertEqual(self._refs(), {"runner-test-arch": "refs/heads/by/hand",
                                        "federation-arch": DEV})


class TheRosterFollowsTheRelease(MigrationCase):

    def test_a_redeploy_replaces_a_stale_seeded_roster(self):
        self._prepare()
        seeded = self.state / "config" / "mailboxes.json"
        (self.release / "mailboxes.json").write_text(json.dumps({"members": {
            "runner-test": {"architect_id": "runner-test-arch"},
            "agent-one": {"architect_id": "example-app-arch"}}}))
        self._prepare()
        self.assertEqual(seeded.read_bytes(), (self.release / "mailboxes.json").read_bytes())
        self.assertEqual(self._roots().transport["recipient_refs"]["example-app-arch"],
                         DEV)

    def test_the_machine_local_files_stay_seed_once(self):
        self._prepare()
        seeded = self.state / "config" / "repo-paths.local"
        before = seeded.read_bytes()
        (self.release / "repo-paths.local").write_text("runner-test = /moved\n")
        self._prepare()
        self.assertEqual(seeded.read_bytes(), before)

    def test_a_release_without_a_roster_keeps_the_seeded_one(self):
        said = []
        config = self.root / "cfg"
        config.mkdir()
        (config / "mailboxes.json").write_text("{}")
        empty = self.root / "empty-release"
        empty.mkdir()
        self.assertEqual(migrate.refresh_release_config(config, empty, said.append), [])
        self.assertEqual((config / "mailboxes.json").read_text(), "{}")


class DevboxAdvancesOnEveryRelease(DevboxCase):
    """Devbox has no deploy runner; `advance` is its deploy, and the cut calls it."""

    def setUp(self):
        super().setUp()
        self.prepare()                             # installed at v7.9.0, a non-JSON roster
        (self.checkout / "mailboxes.json").write_text(json.dumps(STALE_ROSTER))
        (self.checkout / "deploy").mkdir()
        (self.checkout / "deploy" / "registry.json").write_text(
            json.dumps({"systems": {s: {} for s in DEPLOY_SYSTEMS}}))
        (self.checkout / "repo-paths.local").write_text("moved in the release\n")
        _git(["add", "-A"], self.checkout)
        _git(["commit", "-qm", "roster"], self.checkout)
        self.cut("v7.9.2")
        self.config_root = self.home / devbox_mail.STATE_DIR / "config"
        self.service = self.home / devbox_mail.CONFIG_FILE

    def advance(self, **kw):
        return devbox_mail.advance(home=self.home, log=lambda m: None, **kw)

    def test_advance_moves_the_pin_and_the_roster_with_it(self):
        result = self.advance()
        release = self.home / devbox_mail.RELEASE_DIR
        self.assertEqual((result["previous"], result["tag"]), ("v7.9.0", "v7.9.2"))
        self.assertEqual(_git(["describe", "--tags", "--exact-match"], release), "v7.9.2")
        self.assertEqual((self.config_root / "mailboxes.json").read_bytes(),
                         (release / "mailboxes.json").read_bytes())
        self.assertEqual(result["refreshed"], ["mailboxes.json"])

    def test_the_machine_local_files_are_not_touched(self):
        self.advance()
        self.assertEqual((self.config_root / "repo-paths.local").read_text(),
                         "repo-paths.local from the checkout\n")

    def test_the_reverse_ack_map_is_filled_with_no_hand_edit(self):
        self.advance()
        refs = json.loads(self.service.read_text())["transport"]["recipient_refs"]
        self.assertEqual(set(refs), set(TheDerivedMapIsTheOneTheConsultantWroteByHand.HAND))
        self.assertEqual(set(refs.values()), {RUNNER})
        production.resolve(self.home / devbox_mail.RELEASE_DIR,
                           {production.CONFIG_ENV: str(self.service), "HOME": str(self.home)})

    def test_a_running_cycle_is_waited_out_not_raced(self):
        roots = production.resolve(self.home / devbox_mail.RELEASE_DIR,
                                   {production.CONFIG_ENV: str(self.service),
                                    "HOME": str(self.home)})
        with mailworker.cycle_lock(roots) as held:
            self.assertTrue(held)
            with self.assertRaisesRegex(devbox_mail.InstallError, "held the lock"):
                self.advance(wait_seconds=0)
        release = self.home / devbox_mail.RELEASE_DIR
        self.assertEqual(_git(["describe", "--tags", "--exact-match"], release), "v7.9.0")

    def test_a_tag_the_resolver_refuses_is_put_back(self):
        real = production.resolve
        calls = []

        def resolve(code_root, env=None):
            calls.append(code_root)
            if len(calls) > 1:
                raise production.ConfigurationError("refused for the test")
            return real(code_root, env)

        with mock.patch.object(devbox_mail.production, "resolve", resolve):
            with self.assertRaisesRegex(devbox_mail.InstallError, "back at v7.9.0"):
                self.advance()
        release = self.home / devbox_mail.RELEASE_DIR
        self.assertEqual(_git(["describe", "--tags", "--exact-match"], release), "v7.9.0")

    def test_no_installed_worker_is_a_refusal_not_a_crash(self):
        self.service.unlink()
        with self.assertRaisesRegex(devbox_mail.InstallError, "no devbox mail worker"):
            self.advance()


class TheCutRunsDevboxsAdvance(unittest.TestCase):

    def setUp(self):
        import session
        self.session = session

    def test_a_cut_of_the_federation_calls_advance_with_the_new_tag(self):
        ran = mock.Mock(returncode=0, stdout='{"tag": "v7.8.0"}', stderr="")
        out = io.StringIO()
        with mock.patch.object(self.session, "ROOT", REPO), \
                mock.patch.object(self.session.subprocess, "run", return_value=ran) as run, \
                contextlib.redirect_stdout(out):
            self.session._rel_advance_devbox_mail("7.8.0")
        command = run.call_args[0][0]
        self.assertEqual(command[1:], [str(REPO / "deploy" / "devbox_mail.py"), "advance",
                                       "--tag", "v7.8.0"])
        self.assertIn("devbox mail worker advanced", out.getvalue())

    def test_a_refusal_is_printed_and_is_not_fatal(self):
        ran = mock.Mock(returncode=1, stdout="", stderr="mail worker advance: no config")
        out = io.StringIO()
        with mock.patch.object(self.session, "ROOT", REPO), \
                mock.patch.object(self.session.subprocess, "run", return_value=ran), \
                contextlib.redirect_stdout(out):
            self.session._rel_advance_devbox_mail("7.8.0")
        self.assertIn("NOT advanced: mail worker advance: no config", out.getvalue())

    def test_a_tree_without_the_script_runs_nothing(self):
        """Every release-cut fixture's ROOT is a temporary repo with no deploy/ — so no test
        of the cut can advance the real worker under the real HOME."""
        with tempfile.TemporaryDirectory() as empty, \
                mock.patch.object(self.session, "ROOT", Path(empty)), \
                mock.patch.object(self.session.subprocess, "run") as run:
            self.session._rel_advance_devbox_mail("7.8.0")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
