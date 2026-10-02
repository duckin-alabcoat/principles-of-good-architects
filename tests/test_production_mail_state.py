"""WI-0361: explicit service roots, immutable durable messages, no release writes.

All service state/recipients/remotes live in TemporaryDirectory. No harness session,
launchctl, network, or production state is accessed by these fixtures.
"""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "curate"))
import production
import mailqueue


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class ExternalState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.code = self.base / "release"
        self.code.mkdir()
        git(self.code, "init", "-q", "-b", "main")
        git(self.code, "config", "user.name", "Fixture")
        git(self.code, "config", "user.email", "fixture@example.invalid")
        git(self.code, "config", "commit.gpgsign", "false")
        (self.code / "program.txt").write_text("release one\n")
        git(self.code, "add", "program.txt")
        git(self.code, "commit", "-qm", "release one")
        git(self.code, "tag", "v1")
        git(self.code, "checkout", "--detach", "-q")
        self.state = self.base / "state"
        self.cfg = self.state / "config"
        self.cfg.mkdir(parents=True)
        (self.cfg / "mailboxes.json").write_text('{"members": {}}\n')
        (self.cfg / "repo-paths.local").write_text("# no local recipients\n")
        (self.cfg / "reconcile-roots.local").write_text("# fixture only\n")
        self.config = self.base / "production.json"
        self.data = {"schema_version": 1, "code_root": str(self.code),
                     "state_root": str(self.state), "config_root": str(self.cfg),
                     "transport_root": str(self.base / "transport.git"),
                     "transport": {"remote": str(self.base / "remote.git"),
                                   "owned_ref": "refs/heads/runner/mail",
                                   "input_refs": ["refs/heads/main"]}}
        self.save()
        self.env = {production.CONFIG_ENV: str(self.config)}
        self.roots = production.resolve(self.code, self.env)

    def save(self):
        self.config.write_text(json.dumps(self.data))

    def test_detached_does_not_select_production(self):
        self.assertIsNone(production.resolve(self.code, {}))
        import channel
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(channel.data_root(self.code), self.code)

    def test_attached_branch_cannot_be_selected_as_production(self):
        git(self.code, "checkout", "-q", "-B", "mutable-fixture")
        with self.assertRaisesRegex(production.ConfigurationError, "writable branch"):
            production.resolve(self.code, self.env)

    def test_missing_declared_configuration_never_falls_back(self):
        with self.assertRaises(production.ConfigurationError):
            production.resolve(self.code, {production.CONFIG_ENV: str(self.base / "missing.json")})
        (self.cfg / "repo-paths.local").unlink()
        with self.assertRaisesRegex(production.ConfigurationError, "repo-paths"):
            production.resolve(self.code, self.env)

    def test_config_and_state_inside_any_checkout_refuse(self):
        for key in ("state_root", "config_root", "transport_root"):
            with self.subTest(key=key):
                previous = self.data[key]
                self.data[key] = str(self.code / "unsafe")
                self.save()
                # NAME THE REFUSAL, not just its class. `code_root` is itself a git
                # checkout, so the broader "inside a Git checkout" rule also matches
                # every path under it — and while both raise ConfigurationError, an
                # untyped assertion here passes with the release-tree rule deleted.
                with self.assertRaisesRegex(production.ConfigurationError,
                                            "inside the program checkout"):
                    production.resolve(self.code, self.env)
                self.data[key] = previous
        other = self.base / "development"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        self.data["state_root"] = str(other / "state")
        self.save()
        with self.assertRaisesRegex(production.ConfigurationError, "Git checkout"):
            production.resolve(self.code, self.env)

    def test_transport_state_alias_and_changed_symlink_refuse(self):
        self.data["transport_root"] = str(self.state / "transport")
        self.save()
        with self.assertRaisesRegex(production.ConfigurationError, "separate"):
            production.resolve(self.code, self.env)
        outside = self.base / "other"
        outside.mkdir()
        (self.state / "queue").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(production.ConfigurationError):
            mailqueue.enqueue(self.roots, "test-arch", "brief.md", "test")
        self.assertEqual(list(outside.iterdir()), [])

    def test_inbox_authority_is_optional_and_existing_not_created(self):
        self.assertIsNone(self.roots.inbox_root)
        self.assertFalse((self.state / "inbox").exists())
        development = self.base / "development"
        development.mkdir()
        git(development, "init", "-q", "-b", "main")
        inbox = development / "proposed-edits/federation-arch/pending"
        inbox.mkdir(parents=True)
        self.data["inbox_root"] = str(inbox)
        self.save()
        self.assertEqual(production.resolve(self.code, self.env).inbox_root, inbox)
        self.data["inbox_root"] = str(self.state / "pending")
        (self.state / "pending").mkdir()
        self.save()
        with self.assertRaisesRegex(production.ConfigurationError, "mailbox tree must be separate"):
            production.resolve(self.code, self.env)
        self.data["inbox_root"] = str(self.code / "inbox")
        self.save()
        with self.assertRaisesRegex(production.ConfigurationError, "release tree"):
            production.resolve(self.code, self.env)

    def test_scrub_refusal_and_identity_collision_leave_original_intact(self):
        with self.assertRaisesRegex(ValueError, "scrub"):
            mailqueue.enqueue(self.roots, "test-arch", "network.md", "local address 10.0.0.5")
        self.assertFalse(self.roots.queue_root.exists())
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "original\n", message_id="stable")
        original = {p.name: p.read_bytes() for p in path.parent.iterdir()}
        self.assertEqual(mailqueue.enqueue(self.roots, "test-arch", "brief.md", "original\n", message_id="stable"), path)
        for text, aid in (("changed\n", "test-arch"), ("original\n", "other-arch")):
            with self.assertRaisesRegex(ValueError, "different"):
                mailqueue.enqueue(self.roots, aid, "brief.md", text, message_id="stable")
        self.assertEqual({p.name: p.read_bytes() for p in path.parent.iterdir()}, original)

    def test_interrupt_before_commit_exposes_no_partial_message(self):
        with mock.patch.object(mailqueue.os, "rename", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                mailqueue.enqueue(self.roots, "test-arch", "brief.md", "text", message_id="retry")
        self.assertEqual(mailqueue.pending(self.roots), [])
        # A process killed before cleanup can leave private staging data: readers ignore it.
        staging = self.roots.queue_root / ".enqueue-killed"
        staging.mkdir()
        (staging / "half.md").write_text("partial")
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "text", message_id="retry")
        self.assertEqual([e.payload_path for e in mailqueue.pending(self.roots)], [path])

    def test_concurrent_producers_cannot_overwrite(self):
        def same(_):
            return mailqueue.enqueue(self.roots, "test-arch", "brief.md", "same", message_id="one")
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(same, range(24)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(len(mailqueue.pending(self.roots)), 1)
        def distinct(n):
            return mailqueue.enqueue(self.roots, "test-arch", "same-name.md", str(n), message_id=f"id-{n}")
        with ThreadPoolExecutor(max_workers=8) as workers:
            paths = list(workers.map(distinct, range(20)))
        self.assertEqual(len(set(paths)), 20)
        self.assertEqual({p.read_text() for p in paths}, {str(n) for n in range(20)})

    def test_ack_state_separate_and_payload_survives_upgrade_rollback(self):
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "unchanged\n", message_id="event")
        before = {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob("*") if p.is_file()}
        config = self.config.read_bytes()
        (self.code / "program.txt").write_text("release two\n")
        git(self.code, "add", "program.txt")
        git(self.code, "commit", "-qm", "release two")
        git(self.code, "tag", "v2")
        for tag in ("v1", "v2", "v1"):
            git(self.code, "checkout", "--detach", "-q", tag)
            roots = production.resolve(self.code, self.env)
            self.assertEqual(mailqueue.pending(roots)[0].payload_path.read_bytes(), b"unchanged\n")
            self.assertEqual(git(self.code, "status", "--porcelain"), "")
        self.assertEqual(before, {str(p.relative_to(self.state)): p.read_bytes() for p in self.state.rglob("*") if p.is_file()})
        self.assertEqual(config, self.config.read_bytes())
        self.assertEqual(mailqueue.lifecycle(self.roots, "event"), {"state": "queued"})
        mailqueue.mark(self.roots, "event", "published", remote_commit="abc")
        self.assertEqual(len(mailqueue.pending(self.roots)), 1)
        mailqueue.mark(self.roots, "event", "delivered", acknowledgment="ack")
        self.assertEqual(mailqueue.pending(self.roots), [])
        self.assertEqual(path.read_bytes(), b"unchanged\n")
        self.assertEqual(mailqueue.mark(self.roots, "event", "published", remote_commit="late")["state"], "delivered")

    def test_tamper_is_a_fault_not_empty_queue(self):
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "original", message_id="event")
        path.write_text("changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            mailqueue.pending(self.roots)

    def test_corrupt_message_can_be_reported_without_hiding_healthy_mail(self):
        bad = mailqueue.enqueue(self.roots, "test-arch", "bad.md", "original", message_id="bad")
        good = mailqueue.enqueue(self.roots, "test-arch", "good.md", "healthy", message_id="good")
        bad.write_text("damaged")
        faults = []
        messages = mailqueue.pending(self.roots, on_error=lambda path, error: faults.append((path, error)))
        self.assertEqual([m.payload_path for m in messages], [good])
        self.assertEqual(len(faults), 1)
        self.assertEqual(faults[0][0], bad.parent)
        self.assertTrue(bad.exists())
        with mock.patch.object(Path, "iterdir", side_effect=PermissionError("unreadable queue")):
            with self.assertRaises(PermissionError):
                mailqueue.pending(self.roots, on_error=lambda *_: None)

    def test_a_declared_code_root_that_is_not_the_running_program_refuses(self):
        """A service config belongs to ONE release. Pointed at another checkout it would
        hand this program another release's queue, config and transport."""
        other = self.base / "other-release"
        other.mkdir()
        git(other, "init", "-q", "-b", "main")
        git(other, "config", "user.name", "Fixture")
        git(other, "config", "user.email", "fixture@example.invalid")
        git(other, "config", "commit.gpgsign", "false")
        (other / "program.txt").write_text("another release\n")
        git(other, "add", "program.txt")
        git(other, "commit", "-qm", "another release")
        git(other, "checkout", "--detach", "-q")
        self.data["code_root"] = str(other)
        self.save()
        with self.assertRaisesRegex(production.ConfigurationError,
                                    "does not match running program"):
            production.resolve(self.code, self.env)

    def test_a_relative_root_refuses_rather_than_resolving_against_the_cwd(self):
        """A relative path resolves against whatever directory the service happened to
        start in, which on a launchd unit is not a thing anyone chose."""
        for key in ("state_root", "config_root", "transport_root"):
            with self.subTest(key=key):
                previous = self.data[key]
                self.data[key] = "state/queue"
                self.save()
                with self.assertRaisesRegex(production.ConfigurationError,
                                            "must be an absolute path"):
                    production.resolve(self.code, self.env)
                self.data[key] = previous
                self.save()

    def test_an_envelope_from_a_newer_release_refuses(self):
        """Version 1 is asserted in the envelope so a later format cannot be read as this
        one. Nothing exercised the check, so the version field was decoration."""
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body",
                                 message_id="versioned")
        envelope = path.parent / "envelope.json"
        data = json.loads(envelope.read_text())
        data["schema_version"] = 2
        envelope.write_text(json.dumps(data, sort_keys=True) + "\n")
        with self.assertRaisesRegex(ValueError, "unsupported envelope version"):
            mailqueue.pending(self.roots)

    def test_an_envelope_directory_that_is_a_symlink_refuses(self):
        """The queue is addressed by digest, so a symlink is how an entry gets served
        from somewhere the state-root validation never saw."""
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body",
                                 message_id="real")
        link = path.parent.parent / mailqueue.identity_key("linked")
        link.symlink_to(path.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            mailqueue.pending(self.roots)

    def test_an_envelope_with_unusable_provenance_refuses(self):
        """Provenance travels with the message and is scrubbed with it; a reader that
        accepts any shape here hands the transport something it cannot attribute."""
        path = mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body",
                                 message_id="provenanced")
        envelope = path.parent / "envelope.json"
        data = json.loads(envelope.read_text())
        data["provenance"] = "deploy.escalate"
        envelope.write_text(json.dumps(data, sort_keys=True) + "\n")
        with self.assertRaisesRegex(ValueError, "invalid queue provenance"):
            mailqueue.pending(self.roots)

    def test_advancing_a_message_without_evidence_refuses(self):
        """`published` and `delivered` are claims about something that happened off this
        machine. Recording either without the evidence makes the lifecycle a place to
        write wishes, and the queue drains on it."""
        mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body", message_id="bare")
        for state in ("published", "delivered"):
            with self.subTest(state=state):
                with self.assertRaisesRegex(ValueError, "requires evidence"):
                    mailqueue.mark(self.roots, "bare", state)
        self.assertEqual(mailqueue.lifecycle(self.roots, "bare")["state"], "queued")

    def test_evidence_cannot_smuggle_in_its_own_state_or_identity(self):
        """`mark(**evidence)` merges into the record, so an evidence key named `state`
        would set the lifecycle directly, bypassing the monotonic rank check above it.

        THE REFUSAL IS THE SIGNATURE, NOT THE CHECK INSIDE IT. `mark(roots, message_id,
        state, **evidence)` binds both names positionally, so Python raises TypeError
        before the body runs and `mark`'s own `cannot override` guard is unreachable —
        deleting that line changes nothing observable, which is why a mutation sweep
        reports it as untested. The property is what matters and it holds, so this test
        asserts the property and names the mechanism; the assertion is deliberately not
        tied to `ValueError`, because the day someone renames a parameter is the day the
        body's guard becomes the thing doing the work."""
        mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body", message_id="smug")
        for key in ("state", "message_id"):
            with self.subTest(key=key):
                with self.assertRaises((TypeError, ValueError)):
                    mailqueue.mark(self.roots, "smug", "published", **{key: "delivered"})
        self.assertEqual(mailqueue.lifecycle(self.roots, "smug")["state"], "queued")

    def test_a_corrupt_lifecycle_record_is_a_fault_not_a_queued_message(self):
        """`lifecycle` answers `queued` for a MISSING record, which is correct. A record
        that exists but is unreadable must not take that same answer: it would make a
        delivered message look unsent and re-send it."""
        mailqueue.enqueue(self.roots, "test-arch", "brief.md", "body", message_id="rotten")
        record = self.roots.state_path("message-state",
                                       mailqueue.identity_key("rotten") + ".json")
        record.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        record.write_text(json.dumps({"message_id": "rotten", "state": "invented"}))
        with self.assertRaisesRegex(ValueError, "invalid message lifecycle"):
            mailqueue.lifecycle(self.roots, "rotten")
        record.write_text(json.dumps({"message_id": "someone-else", "state": "delivered"}))
        with self.assertRaisesRegex(ValueError, "invalid message lifecycle"):
            mailqueue.lifecycle(self.roots, "rotten")

    def test_a_queue_entry_filed_under_the_wrong_identity_is_a_fault(self):
        """The check at `mailqueue.pending` had no test that could fail.

        Every corruption fixture here damages the PAYLOAD, which `read()` catches on the
        hash before the directory name is ever compared — so deleting the identity check
        left nine test files green. The entry below is intact in every other respect: the
        envelope parses, the payload hash verifies, and only the stem is wrong.

        It is reachable. `mailqueue` addresses an entry by `sha256(message_id)`, so a
        restored backup, a copied queue, or the migration WI-0365 has still to write can
        land a good message under a stem that is not its own. Unread, `pending()` hands
        it to the worker, which publishes it and then marks `sha256(message_id)` — a
        DIFFERENT directory. The entry is never acknowledged and ships on every cycle.
        """
        good = mailqueue.enqueue(self.roots, "test-arch", "good.md", "healthy",
                                 message_id="good")
        misfiled = good.parent.parent / mailqueue.identity_key("never-enqueued")
        shutil.copytree(good.parent, misfiled)
        self.assertEqual(mailqueue.read(misfiled).sha256, mailqueue.read(good.parent).sha256,
                         "the entry must be intact, or the hash check catches it first")
        with self.assertRaisesRegex(ValueError, "identity differs from directory"):
            mailqueue.pending(self.roots)

    def test_a_misfiled_entry_is_reported_without_hiding_healthy_mail(self):
        good = mailqueue.enqueue(self.roots, "test-arch", "good.md", "healthy",
                                 message_id="good")
        other = mailqueue.enqueue(self.roots, "test-arch", "other.md", "second",
                                  message_id="other")
        misfiled = good.parent.parent / mailqueue.identity_key("never-enqueued")
        shutil.copytree(good.parent, misfiled)
        faults = []
        messages = mailqueue.pending(self.roots, on_error=lambda p, e: faults.append(p))
        self.assertEqual(sorted(m.payload_path for m in messages), sorted([good, other]))
        self.assertEqual(faults, [misfiled])
        # The healthy original keeps its own lifecycle; the copy never touched it.
        self.assertEqual(mailqueue.lifecycle(self.roots, "good")["state"], "queued")

    def test_actual_service_call_sites_leave_detached_release_unchanged(self):
        # Run imports FROM a detached release, not patched path helpers in the source tree.
        for name in ("curate", "deploy", "sessionlib"):
            shutil.copytree(REPO / name, self.code / name, ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(REPO / "session.py", self.code / "session.py")
        # `deploy/runner.py` and `curate/adopt-runner.py` both import it (WI-0371).
        shutil.copy2(REPO / "poga_evidence.py", self.code / "poga_evidence.py")
        # `curate/standard_version.py` imports it from the federation root.
        shutil.copy2(REPO / "standard_check.py", self.code / "standard_check.py")
        shutil.copy2(REPO / "session.config.json", self.code / "session.config.json")
        git(self.code, "add", ".")
        git(self.code, "commit", "-qm", "fixture services")
        head = git(self.code, "rev-parse", "HEAD")
        script = r'''
import importlib.util, json, pathlib, sys
from unittest.mock import patch
code = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(code / 'curate'))
sys.path.insert(0, str(code / 'deploy'))
import production, outbox, runner, deliver, reconcile, mailqueue
roots = production.resolve(code)
def load(name, file):
    spec = importlib.util.spec_from_file_location(name, code / 'curate' / file)
    mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
    spec.loader.exec_module(mod); return mod
adopt = load('adopt_fixture', 'adopt-runner.py')
poller = load('poller_fixture', 'mail-poller.py')
assert reconcile.ROOTS_CONFIG == roots.config_root / 'reconcile-roots.local'
# Every reader of the machine-local locator, not just the two that were converted
# first. A release clone carries neither file (both are gitignored), so a reader left
# on the checkout locates ZERO members after cutover and reports a clean run.
import metrics, standard_version, gen_registry
push = load('push_fixture', 'push-substrate.py')
for module in (reconcile, metrics, standard_version, push):
    assert module.ROOTS_CONFIG == roots.config_root / 'reconcile-roots.local', module.__name__
    assert module.REPO_PATHS_CONFIG == roots.config_root / 'repo-paths.local', module.__name__
assert gen_registry.config_root() == roots.config_root
assert deliver.MAILBOXES == roots.mailboxes_path
assert adopt.BRIEF_STATE_PATH == roots.session_state_root / 'adopt-runner.briefs.json'
assert poller.STATE_DIR == roots.session_state_root
assert runner.self_cutover_log('federation').parent == roots.session_state_root
assert runner.ledger_dir() == pathlib.Path(sys.argv[2])
adopt.write_brief_state({'permanent-quarantine': {'failures': 3}})
adopt.write_status({'last_run': 'fixture'})
adopt.log_outcome({'event': 'fixture'})
adopt._queue_condition(adopt.STALE_NOTE, 'login unavailable')
assert outbox.post('test-arch', 'outbox.md', 'hello').is_file()
assert outbox.publish('subject', 'body', log=lambda _: None) is False
# WI-0316 gave `refresh` a Refresh record instead of a bare bool, because `ok` and
# `advanced` are different facts and the status file was reporting one as the other.
# The assertion this replaces read `is False`; what it was testing is that production
# REFUSES here, and `.ok` is where that fact lives now. Asserting the type too, so a
# later regression to a bare bool is caught by the test that cares rather than by the
# AttributeError in `main()` that a bare bool would cause.
_refreshed = poller.refresh(lambda _: None)
assert isinstance(_refreshed, poller.Refresh) and _refreshed.ok is False
rec = {'status': 'deployed', 'current': 'v1', 'deployed_at': '2026-09-14T12:00:00Z'}
with patch.object(runner, 'read_ledger', return_value=rec):
    result = runner.post_result('test', {}, False, lambda _: None)
assert result['queued'] and not result['published'] and not result['delivered']
assert runner.escalate('test', 'Failure', 'fixture failure', lambda _: None).is_file()
assert len(mailqueue.pending(roots)) == 4
# WI-0366. The one call site of this shape the suite had never run: every test in
# `tests/test_diagnose.py` inherits a fixture that clears POGA_FEDERATION_CONFIG, so
# `post_if_changed`'s production fork had never executed, and that file's only
# does-not-write test fingerprints the ledger, comms, outbox and LaunchAgents dirs —
# not HEAD and not the code tree.
#
# WHAT THIS DOES AND DOES NOT PROTECT, because the first version of this comment got it
# wrong and the mutation said so. It is NOT the guard against a writer naming the tree's
# outbox: `outbox.post` refuses a root that is not the configured `queue_root`, and
# `outbox.publish` refuses to commit at all under production, so forcing the development
# branch here is behaviour-preserving — measured, not assumed. What it pins is one fault
# those two cannot catch: diagnose ceasing to RECOGNISE production. With
# `production.resolve` mutated to answer None the result came back
# `{'posted': True, ..., 'committed': False}` and the `committed` assertion below fired.
# Rendering and digest behaviour stay covered in `tests/test_diagnose.py`.
import diagnose
with patch.object(diagnose, 'snapshot', return_value={'machine': 'fixture',
                                                      'collected_at': '2026-09-14T12:00:00Z'}), \
     patch.object(diagnose, 'digest', return_value={'stable': 'moved'}), \
     patch.object(diagnose, '_brief', return_value=('diagnosis.md', 'a fixture bundle\n')):
    posted = diagnose.post_if_changed('test', log=lambda _: None, run_verify=False)
assert posted.get('queued') is True, posted
assert 'committed' not in posted, posted
assert len(mailqueue.pending(roots)) == 5
rows = {'federation': {'architect_id': 'federation-arch', 'mailbox': 'inbox', 'tracked': False}}
try: deliver.resolve('federation-arch', rows, {'federation': code})
except ValueError as exc: assert 'not authoritative' in str(exc)
else: raise AssertionError('a second federation inbox was permitted')
'''
        env = {**os.environ, production.CONFIG_ENV: str(self.config),
               "HOME": str(self.base / "home"),
               "POGA_DATA_ROOT": str(self.base / "data"),
               "POGA_DEPLOY_ROOT": str(self.base / "deploy"),
               "POGA_CHANNEL_ROOT": str(self.base / "legacy-channel"),
               "POGA_DEPLOY_LAUNCH_AGENTS": str(self.base / "agents"),
               "POGA_DEPLOY_LEDGER_DIR": str(self.base / "ledger"),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(self.base / "pycache")}
        run = subprocess.run([sys.executable, "-c", script, str(self.code), str(self.base / "ledger")],
                             capture_output=True, text=True, env=env, cwd=self.code)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(git(self.code, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.code, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()


class TheDeclaredSchemaVersionIsReadStrictly(ExternalState):
    """WI-0418 bumped the service configuration to version 2, and this is the READER.

    The document shape did not change -- version 2 marks a config examined for the
    channel-branch collision -- so both versions resolve. That is a deliberate choice with a
    failure mode of its own: refusing version 1 here would make production dormant on every
    host between the release landing and that host's own migration running, which is a wider
    outage than the defect being fixed. Pinned so nobody tightens it back by tidiness."""

    def resolve_at(self, value):
        self.data["schema_version"] = value
        self.save()
        return production.resolve(self.code, self.env)

    def test_a_version_one_configuration_still_resolves(self):
        self.assertIsNotNone(self.resolve_at(1))

    def test_a_version_two_configuration_resolves(self):
        roots = self.resolve_at(2)
        self.assertIsNotNone(roots)
        self.assertEqual(roots.transport["input_refs"], ["refs/heads/main"])

    def test_a_version_nobody_published_refuses(self):
        for value in (0, 3, 99):
            with self.subTest(value=value):
                with self.assertRaisesRegex(production.ConfigurationError, "schema_version"):
                    self.resolve_at(value)

    def test_a_boolean_is_not_a_version_number(self):
        """`True == 1`, so `schema_version != 1` read `true` as version 1 for as long as that
        check existed. Matching the message rather than the class: `resolve` refuses a dozen
        different ways and a bare assertRaises cannot tell them apart."""
        with self.assertRaisesRegex(production.ConfigurationError, "schema_version"):
            self.resolve_at(True)

    def test_a_string_is_not_a_version_number(self):
        with self.assertRaisesRegex(production.ConfigurationError, "schema_version"):
            self.resolve_at("2")
