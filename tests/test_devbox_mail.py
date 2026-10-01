"""The devbox side of the mail transport (WI-0419), against isolated fixture repositories.

Nothing here installs a daemon or touches launchd: `prepare` stops at a rendered plist, and
the one root step lives in `deploy/install-mail-worker-daemon.sh`. Every path is under a
temporary directory standing in for HOME.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
sys.path.insert(0, str(REPO / "curate"))
import devbox_mail                                                  # noqa: E402
import migrate                                                       # noqa: E402
import production                                                    # noqa: E402


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", "-c", "gc.auto=0",
         "-c", "maintenance.auto=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True).stdout.strip()


class TheRefPairIsTheRunnersMirrored(unittest.TestCase):
    """The item's acceptance names this test: the devbox config is written with the
    mirrored ref pair. Mirrored by DERIVATION, so a rename on the Runner side moves both."""

    def test_devbox_owns_what_the_runner_reads_and_reads_what_it_owns(self):
        document = devbox_mail.config_document(home=Path("/h"), checkout=Path("/c"),
                                                remote="git@example.com:r.git")
        self.assertEqual(document["transport"]["owned_ref"], "refs/heads/dev/messages")
        self.assertEqual(document["transport"]["input_refs"], ["refs/heads/runner/messages"])
        self.assertEqual(document["transport"]["owned_ref"], migrate.TRANSPORT_INPUT_REFS[0])
        self.assertEqual(document["transport"]["input_refs"], [migrate.TRANSPORT_OWNED_REF])

    def test_neither_ref_is_the_channel_branch(self):
        """WI-0418 again, from the other side: the transport refuses a ref holding the
        channel's full tree, so neither half of the pair may be that ref."""
        document = devbox_mail.config_document(home=Path("/h"), checkout=Path("/c"),
                                                remote="r")
        self.assertFalse(migrate.names_channel_ref(document["transport"]))

    def test_the_federation_inbox_is_authoritative_here(self):
        """Devbox owns the federation inbox, so its installation names it; the Runner's
        answers "elsewhere" for this recipient so that this one delivers."""
        document = devbox_mail.config_document(home=Path("/h"), checkout=Path("/c"),
                                                remote="r")
        self.assertEqual(document["inbox_root"], "/c/proposed-edits/federation-arch/pending")


class DevboxCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="devbox-mail-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.remote = self.root / "origin.git"
        _git(["init", "--bare", "-q", "-b", "main", str(self.remote)], self.root)
        self.checkout = self.root / "checkout"
        _git(["clone", "-q", str(self.remote), str(self.checkout)], self.root)
        for name in migrate.CONFIG_FILES:
            (self.checkout / name).write_text(f"{name} from the checkout\n")
        (self.checkout / devbox_mail.INBOX).mkdir(parents=True)
        (self.checkout / "curate").mkdir()
        (self.checkout / "curate" / "mailworker.py").write_text("# fixture\n")
        _git(["add", "-A"], self.checkout)
        _git(["commit", "-qm", "release"], self.checkout)
        _git(["push", "-q", "origin", "main"], self.checkout)
        self.cut("v7.9.0")
        self.home = self.root / "home"
        self.home.mkdir()

    def cut(self, tag):
        (self.checkout / "VERSION").write_text(tag + "\n")
        _git(["add", "VERSION"], self.checkout)
        _git(["commit", "-qm", tag], self.checkout)
        _git(["tag", "-a", tag, "-m", tag], self.checkout)
        _git(["push", "-q", "--tags", "origin", "main"], self.checkout)

    def prepare(self, **kw):
        out = self.root / "rendered.plist"
        said = []
        result = devbox_mail.prepare(checkout=self.checkout, home=self.home,
                                      python=Path(sys.executable), user="operator",
                                      out=out, log=said.append, **kw)
        return result, plistlib.loads(out.read_bytes()), said


class ThePreparationProducesAWorkerTheResolverAccepts(DevboxCase):

    def test_the_rendered_daemon_runs_the_pinned_release_under_the_written_config(self):
        result, plist, _ = self.prepare()
        config = self.home / devbox_mail.CONFIG_FILE
        release = self.home / devbox_mail.RELEASE_DIR
        self.assertEqual(plist["Label"], "com.federation.mail-worker")
        self.assertEqual(plist["UserName"], "operator")
        self.assertEqual(plist["ProgramArguments"][1:],
                         [str(release / "curate/mailworker.py"), "--config", str(config),
                          "--quiet"])
        self.assertEqual(plist["WorkingDirectory"], str(release))
        self.assertEqual(plist["EnvironmentVariables"][production.CONFIG_ENV], str(config))
        self.assertEqual(plist["EnvironmentVariables"]["HOME"], str(self.home))
        self.assertNotIn("LimitLoadToSessionType", plist,
                         "a session-type limit is an agent's, and would keep a daemon unloaded")
        self.assertEqual(result["owned_ref"], "refs/heads/dev/messages")

    def test_the_written_config_is_one_the_worker_itself_would_accept(self):
        """Asked the way `mailworker.main` asks: its own code root, the config by key."""
        self.prepare()
        roots = production.resolve(self.home / devbox_mail.RELEASE_DIR,
                                   {production.CONFIG_ENV: str(self.home / devbox_mail.CONFIG_FILE),
                                    "HOME": str(self.home)})
        self.assertEqual(roots.transport["owned_ref"], "refs/heads/dev/messages")
        self.assertEqual(roots.transport["remote"], str(self.remote))
        self.assertEqual((roots.config_root / "repo-paths.local").read_text(),
                         "repo-paths.local from the checkout\n")

    def test_the_release_is_detached_at_the_highest_version_not_the_latest_name(self):
        """`v7.10.0` sorts before `v7.9.0` as text. A pin that took the textual maximum
        would run an older release than the one just cut."""
        self.cut("v7.10.0")
        result, _, _ = self.prepare()
        self.assertEqual(result["tag"], "v7.10.0")
        release = self.home / devbox_mail.RELEASE_DIR
        self.assertEqual(_git(["describe", "--tags", "--exact-match"], release), "v7.10.0")
        head = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=release)
        self.assertEqual(head.returncode, 1, "the release clone is on a branch")

    def test_re_running_advances_the_pin_and_keeps_the_authored_config(self):
        self.prepare()
        config = self.home / devbox_mail.CONFIG_FILE
        document = json.loads(config.read_text())
        document["transport"]["poll_interval_seconds"] = 45
        config.write_text(json.dumps(document))
        self.cut("v7.9.1")
        result, plist, _ = self.prepare()
        self.assertEqual(result["tag"], "v7.9.1")
        self.assertEqual(result["config_outcome"], "authored")
        self.assertEqual(plist["StartInterval"], 45)

    def test_a_seeded_file_is_never_overwritten(self):
        self.prepare()
        seeded = self.home / devbox_mail.STATE_DIR / "config" / "mailboxes.json"
        seeded.write_text("edited on this host\n")
        self.prepare()
        self.assertEqual(seeded.read_text(), "edited on this host\n")


class ItRefusesRatherThanGuesses(DevboxCase):

    def test_a_clone_of_some_other_repository_is_not_adopted(self):
        release = self.home / devbox_mail.RELEASE_DIR
        release.parent.mkdir(parents=True)
        _git(["clone", "-q", str(self.remote), str(release)], self.root)
        _git(["remote", "set-url", "origin", str(self.root / "elsewhere.git")], release)
        with self.assertRaisesRegex(devbox_mail.InstallError, "refusing to adopt it"):
            self.prepare()

    def test_a_root_user_is_refused(self):
        with self.assertRaisesRegex(devbox_mail.InstallError, "never as root"):
            devbox_mail.render_plist(None, config=Path("/c"), python=Path(sys.executable),
                                      user="root", home=Path("/h"))

    def test_a_missing_config_source_is_refused_not_skipped(self):
        (self.checkout / "reconcile-roots.local").unlink()
        with self.assertRaisesRegex(devbox_mail.InstallError, "reconcile-roots.local"):
            self.prepare()

    def test_the_plist_is_not_written_when_the_resolver_refuses(self):
        """The refusal comes while somebody is watching, never at every StartInterval."""
        config = self.home / devbox_mail.CONFIG_FILE
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"schema_version": 2}))
        with self.assertRaises(devbox_mail.InstallError):
            self.prepare()
        self.assertFalse((self.root / "rendered.plist").exists())


    def test_a_config_only_the_resolver_can_refuse_is_refused_before_rendering(self):
        """Shape-valid, so `raw_config` passes it, but its state lives inside a Git
        checkout -- which only `production.resolve` checks, and which the worker would
        refuse at every StartInterval."""
        document = devbox_mail.config_document(home=self.home, checkout=self.checkout,
                                                remote=str(self.remote))
        document["state_root"] = str(self.checkout / "state")
        document["config_root"] = str(self.checkout / "state" / "config")
        config = self.home / devbox_mail.CONFIG_FILE
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(document))
        with self.assertRaisesRegex(devbox_mail.InstallError, "inside a Git checkout"):
            self.prepare()
        self.assertFalse((self.root / "rendered.plist").exists())

class TheInstallerIsWhatTheItemSaysItIs(unittest.TestCase):
    """Read, not run: running it would ask for root and touch launchd."""

    SCRIPT = REPO / "deploy" / "install-mail-worker-daemon.sh"

    def test_it_refuses_to_run_as_root_and_asks_for_sudo_itself(self):
        text = self.SCRIPT.read_text()
        self.assertIn('if [ "$(id -u)" = "0" ]', text)
        self.assertIn("sudo install -o root -g wheel", text)
        self.assertIn("sudo launchctl bootstrap system", text)

    def test_it_is_executable_and_parses(self):
        self.assertTrue(os.access(self.SCRIPT, os.X_OK))
        subprocess.run(["bash", "-n", str(self.SCRIPT)], check=True)

    def test_it_is_not_a_deploy_template(self):
        """A `deploy/*.plist.template` is a unit of the deploy contract and the Runner's
        cutover renders it. This daemon belongs to a machine no deploy runs on."""
        self.assertFalse(list((REPO / "deploy").glob("com.federation.mail-worker*.plist.template")))
        contract = json.loads((REPO / "deploy" / "deploy.json").read_text())
        self.assertNotIn(devbox_mail.LABEL, contract["units"])


if __name__ == "__main__":
    unittest.main()
