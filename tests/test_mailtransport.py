"""WI-0362: publication recovery against real repositories and process boundaries.

All roots, recipients, remotes and hooks are temporary. No network remote is used.
Signing and background Git maintenance are disabled in every fixture repository.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

CURATE = Path(__file__).resolve().parents[1] / "curate"
sys.path.insert(0, str(CURATE))
import mailqueue
import mailtransport as transport
import production
import scrub


def git(repo, *args, payload=None, env=None):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "-c", "gc.auto=0",
         "-c", "maintenance.auto=false", "-c", "user.name=Fixture",
         "-c", "user.email=fixture@example.com", *args], input=payload,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, env=env).stdout


class MailTransportTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mailtransport-test-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        # Stop fixture git from reading unrelated repository-selection environment.
        self.environment = mock.patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
            "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.remote = self.base / "origin.git"
        self.remote.mkdir()
        git(self.remote, "init", "--bare", "-q", "-b", "main")
        git(self.base, "clone", "-q", str(self.remote), "code")
        self.code = self.base / "code"
        (self.code / "program.py").write_text("print('fixture release')\n")
        git(self.code, "add", "program.py")
        git(self.code, "commit", "-qm", "fixture release")
        git(self.code, "push", "-q", "origin", "main")
        git(self.code, "checkout", "-q", "--detach")
        self.original_main = git(self.remote, "rev-parse", "main")
        self.original_code = (git(self.code, "rev-parse", "HEAD"),
                              (self.code / "program.py").read_bytes())
        self.state = self.base / "state"
        self.config_root = self.state / "config"
        self.config_root.mkdir(parents=True)
        for name, content in (("mailboxes.json", "{}\n"), ("repo-paths.local", ""),
                              ("reconcile-roots.local", "")):
            (self.config_root / name).write_text(content)
        self.roots = production.Roots(self.code, self.state, self.base / "transport.git",
                                     self.config_root, None,
                                     {"remote": str(self.remote),
                                      "owned_ref": "refs/heads/runner/mail-test",
                                      "input_refs": ["refs/heads/main"]})
        self.config = self.base / "production.json"
        self.write_config()

    def write_config(self):
        self.config.write_text(json.dumps({
            "schema_version": 1, "code_root": str(self.code), "state_root": str(self.state),
            "config_root": str(self.config_root), "transport_root": str(self.roots.transport_root),
            "transport": self.roots.transport,
        }))

    def enqueue(self, name="one", text="A fixture message.\n\n", destination="fixture-arch",
                provenance=None, roots=None):
        return mailqueue.enqueue(roots or self.roots, destination, name + ".md", text,
                                 message_id=name, provenance=provenance or {"sender": "fixture"})

    def envelope(self, name):
        return mailqueue.read(self.roots.queue_root / mailqueue.identity_key(name))

    def remote_wire(self, name):
        return git(self.remote, "show", self.roots.transport["owned_ref"] + ":" +
                   transport.message_path(name))

    def assert_code_unchanged(self):
        self.assertEqual(git(self.remote, "rev-parse", "main"), self.original_main)
        self.assertEqual((git(self.code, "rev-parse", "HEAD"),
                          (self.code / "program.py").read_bytes()), self.original_code)
        self.assertEqual(git(self.code, "status", "--porcelain"), b"")

    def child(self, code, *args, wait=True):
        script = ("import sys,os,json\n"
                  "sys.path.insert(0,sys.argv[1])\n"
                  "import production,mailqueue,mailtransport as transport\n"
                  "roots=production.resolve(sys.argv[3],"
                  "env={'POGA_FEDERATION_CONFIG':sys.argv[2]})\n" + code)
        proc = subprocess.Popen([sys.executable, "-c", script, str(CURATE), str(self.config),
                                 str(self.code), *args], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        if not wait:
            self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
            return proc
        stdout, stderr = proc.communicate(timeout=30)
        return proc.returncode, stdout, stderr

    def reject_pushes(self):
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        return hook

    def test_round_trip_preserves_bytes_and_only_confirms_publication(self):
        payload = "  Exact Unicode: café ☕\n\n\n"
        path = self.enqueue(text=payload)
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "ok", result)
        meta, carried = transport.decode_message(self.remote_wire("one"))
        self.assertEqual(carried, payload.encode())
        self.assertEqual(meta, self.envelope("one").as_dict())
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "published")
        self.assertEqual([p.message_id for p in mailqueue.pending(self.roots)], ["one"])
        self.assertEqual(path.read_bytes(), carried)
        self.assert_code_unchanged()

    def test_rejected_push_retries_same_commit_without_new_mail(self):
        path = self.enqueue()
        hook = self.reject_pushes()
        refused = transport.cycle(self.roots)
        self.assertEqual(refused["status"], "failed")
        commit = git(self.roots.transport_root, "rev-parse", self.roots.transport["owned_ref"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        hook.unlink()
        retried = transport.cycle(self.roots)
        self.assertEqual(retried["status"], "ok", retried)
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), commit)
        self.assertEqual(path.read_bytes(), transport.decode_message(self.remote_wire("one"))[1])
        self.assert_code_unchanged()

    def test_unavailable_remote_preserves_payload_and_recovers_without_new_mail(self):
        path = self.enqueue()
        hidden = self.base / "offline.git"
        self.remote.rename(hidden)
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "failed")
        self.assertTrue(path.is_file())
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        hidden.rename(self.remote)
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.assertEqual(transport.decode_message(self.remote_wire("one"))[1], path.read_bytes())

    def crash_cycle(self, stage):
        rc, out, err = self.child(
            "transport._checkpoint=lambda stage: os._exit(75) if stage==sys.argv[4] else None\n"
            "transport.cycle(roots)\n", stage)
        self.assertEqual(rc, 75, (out, err))

    def test_process_death_after_commit_recovers_same_commit(self):
        self.enqueue()
        self.crash_cycle("committed")
        local = git(self.roots.transport_root, "rev-parse", self.roots.transport["owned_ref"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), local)

    def test_process_death_after_remote_success_reconciles_confirmation(self):
        self.enqueue()
        self.crash_cycle("pushed")
        remote = git(self.remote, "rev-parse", self.roots.transport["owned_ref"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), remote)
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["remote_commit"], remote.decode().strip())

    def test_live_process_lock_excludes_worker_and_kernel_releases_after_death(self):
        self.enqueue()
        proc = self.child("import time\nwith transport.transport_lock(roots):\n"
                          " print('locked',flush=True)\n time.sleep(25)\n", wait=False)
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "busy", result)
        self.assertFalse(self.roots.transport_root.exists())
        proc.kill()
        proc.communicate(timeout=10)
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")

    def test_overlapping_producers_and_workers_publish_every_identity_once(self):
        producer = ("for i in range(5):\n"
                    " name=sys.argv[4]+'-'+str(i)\n"
                    " mailqueue.enqueue(roots,'fixture-arch',name+'.md','Fixture '+name+'\\n',"
                    "message_id=name,provenance={})\n")
        processes = [self.child(producer, str(i), wait=False) for i in range(3)]
        workers = [self.child("print(json.dumps(transport.cycle(roots)))\n", wait=False)
                   for _ in range(2)]
        for proc in processes + workers:
            out, err = proc.communicate(timeout=30)
            self.assertEqual(proc.returncode, 0, (out, err))
        final = transport.cycle(self.roots)
        self.assertEqual(final["status"], "ok", final)
        names = git(self.remote, "ls-tree", "-r", "--name-only", self.roots.transport["owned_ref"]).splitlines()
        self.assertEqual(len(names), 15)
        self.assertEqual(len(set(names)), 15)
        self.assertTrue(all(mailqueue.lifecycle(self.roots, p.message_id)["state"] == "published"
                            for p in mailqueue.pending(self.roots)))
        self.assert_code_unchanged()

    def test_unrelated_default_index_is_unchanged_and_never_committed(self):
        with transport.transport_lock(self.roots):
            transport.ensure_repository(self.roots)
        root = self.roots.transport_root
        git(root, "read-tree", "--empty")
        oid = git(root, "hash-object", "-w", "--stdin", payload=b"unrelated program\n").decode().strip()
        git(root, "update-index", "--add", "--cacheinfo", "100644," + oid + ",unrelated.py")
        index = (root / "index").read_bytes()
        self.enqueue()
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.assertEqual((root / "index").read_bytes(), index)
        self.assertEqual(git(self.remote, "ls-tree", "-r", "--name-only",
                             self.roots.transport["owned_ref"]).decode().splitlines(),
                         [transport.message_path("one")])

    def test_remote_divergence_refuses_without_force_or_payload_loss(self):
        self.enqueue("first")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.enqueue("second")
        hook = self.reject_pushes()
        self.assertEqual(transport.cycle(self.roots)["status"], "failed")
        hook.unlink()
        other = replace(self.roots, state_root=self.base / "other-state",
                        transport_root=self.base / "other-transport.git")
        other.state_root.mkdir()
        self.enqueue("competitor", roots=other)
        self.assertEqual(transport.cycle(other)["status"], "ok")
        remote = git(self.remote, "rev-parse", self.roots.transport["owned_ref"])
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("diverged", result["failure"])
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), remote)
        self.assertEqual(mailqueue.lifecycle(self.roots, "second")["state"], "queued")
        self.assertEqual(len(mailqueue.pending(self.roots)), 2)

    def test_input_snapshot_is_pinned_and_explicit_and_never_checked_out(self):
        with transport.transport_lock(self.roots):
            transport.ensure_repository(self.roots)
            snapshot = transport.fetch_snapshot(self.roots, "refs/heads/main")
            rows = transport.list_blobs(self.roots, snapshot, "program.py")
            self.assertEqual([(p, mode) for p, mode, _oid in rows], [("program.py", "100644")])
            self.assertEqual(transport.read_blob(self.roots, snapshot, "program.py"), self.original_code[1])
            with self.assertRaises(transport.TransportError):
                transport.fetch_snapshot(self.roots, "refs/heads/unconfigured")
        self.assertEqual(git(self.roots.transport_root, "for-each-ref", "--format=%(refname)", "refs/heads"), b"")
        self.assert_code_unchanged()

    def test_ack_uses_same_publisher_and_does_not_mark_original_delivered(self):
        self.enqueue()
        self.enqueue("ack", json.dumps({"message_id": "example", "sha256": "a" * 64}),
                     destination="transport-ack", provenance={"kind": "delivery-ack"})
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        envelope, _ = transport.decode_message(self.remote_wire("ack"))
        self.assertEqual(envelope["provenance"]["kind"], "delivery-ack")
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "published")

    def test_rejects_trunk_owned_ref_before_making_repository(self):
        self.roots.transport["owned_ref"] = "refs/heads/main"
        self.enqueue()
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.roots.transport_root.exists())
        self.assert_code_unchanged()

    def test_existing_unowned_repository_is_never_adopted(self):
        self.roots.transport_root.mkdir()
        authored = self.roots.transport_root / "authored.txt"
        authored.write_text("preserve me")
        self.enqueue()
        self.assertEqual(transport.cycle(self.roots)["status"], "failed")
        self.assertEqual(authored.read_text(), "preserve me")
        self.assertFalse((self.roots.transport_root / "HEAD").exists())

    def test_corrupt_queued_payload_is_not_published(self):
        payload = self.enqueue()
        payload.write_text("Changed after enqueue")
        self.assertEqual(transport.cycle(self.roots)["status"], "failed")
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")

    def test_malformed_queue_entry_preserved_while_healthy_mail_publishes(self):
        bad = self.enqueue("broken")
        bad.write_text("corrupted after enqueue")
        good = self.enqueue("healthy")
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(len(result["queue_errors"]), 1)
        self.assertEqual(result["published"], 1)
        self.assertEqual(bad.read_text(), "corrupted after enqueue")
        self.assertEqual(mailqueue.lifecycle(self.roots, "broken")["state"], "queued")
        self.assertEqual(transport.decode_message(self.remote_wire("healthy"))[1], good.read_bytes())

    def test_published_control_message_is_retained_without_awaiting_recursive_ack(self):
        self.enqueue("control", "Fixture acknowledgment", destination="transport-ack",
                     provenance={"kind": "delivery-ack"})
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["pending_control"], 0)
        self.assertEqual(result["retained_control"], 1)
        self.assertEqual(result["pending"], 0)
        self.assertTrue(self.envelope("control").payload_path.exists())
        self.assertEqual(mailqueue.lifecycle(self.roots, "control")["state"], "published")

    def test_bounded_batch_eventually_publishes_without_dropping_old_work(self):
        self.roots.transport["max_batch_messages"] = 1
        self.roots.transport["max_pending_messages"] = 2
        for name in ("one", "two", "three"):
            self.enqueue(name)
        for expected in (1, 2, 3):
            result = transport.cycle(self.roots)
            self.assertEqual(result["status"], "ok", result)
            self.assertTrue(result["backpressure"])
            self.assertEqual(len(git(self.remote, "ls-tree", "-r", "--name-only",
                                      self.roots.transport["owned_ref"]).splitlines()), expected)
        self.assertEqual(len(mailqueue.pending(self.roots)), 3)

    def test_head_size_backpressure_preserves_undelivered_queue(self):
        self.roots.transport["max_head_bytes"] = 1
        self.enqueue()
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "ok", result)
        self.assertTrue(result["backpressure"])
        self.assertEqual(result["published"], 0)
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertTrue(self.envelope("one").payload_path.exists())

    def test_network_timeout_preserves_pending_commit_and_bounds_hook(self):
        self.roots.transport["network_timeout_seconds"] = 1
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nsleep 20\nexit 0\n")
        hook.chmod(0o755)
        self.enqueue()
        start = time.monotonic()
        result = transport.cycle(self.roots)
        self.assertLess(time.monotonic() - start, 6)
        self.assertEqual(result["status"], "failed")
        self.assertIn("timed out", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        hook.unlink()
        # The recovery is not what this test bounds. Left at 1s, a loaded machine's local
        # push outran it and the recovery itself "timed out" (2026-09-29 background run).
        self.roots.transport.pop("network_timeout_seconds")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")

    def test_wire_refuses_malformed_hash_traversal_and_schema(self):
        self.enqueue()
        blob = transport.encode_message(self.envelope("one"), self.envelope("one").payload_path.read_bytes())
        for field, value in (("schema_version", True), ("sha256", "0" * 64),
                             ("filename", "../escape.md"), ("destination", "../../other")):
            with self.subTest(field=field):
                doc = json.loads(blob)
                doc[field] = value
                with self.assertRaises(transport.TransportError):
                    transport.decode_message(json.dumps(doc).encode())

    def test_cycle_cancellation_terminates_git_helpers_before_releasing_lock(self):
        class Deadline(BaseException):
            pass

        pid_file = self.base / "hook.pid"
        survived = self.base / "hook-survived"
        hook_sleep = 2
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho $$ > " + str(pid_file) + "\nsleep "
                        + str(hook_sleep) + "\necho survived > " + str(survived) + "\n")
        hook.chmod(0o755)
        self.enqueue()
        # The deadline fires once the hook is RUNNING, not at a fixed wall-clock time.
        # A fixed 0.7s missed the hook on the 2026-09-29 nightly: under launchd's
        # background throttling, with gate shards in parallel, the push had not reached
        # the hook yet, so the test never exercised what it guards.
        hook_seen = []

        def fire_when_the_hook_runs():
            give_up = time.monotonic() + 60
            while not pid_file.exists() and time.monotonic() < give_up:
                time.sleep(0.01)
            hook_seen.append(time.monotonic())
            signal.setitimer(signal.ITIMER_REAL, 0.001)

        old_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(Deadline()))
        watcher = threading.Thread(target=fire_when_the_hook_runs, daemon=True)
        try:
            watcher.start()
            with self.assertRaises(Deadline):
                transport.cycle(self.roots)
        finally:
            watcher.join()
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        self.assertTrue(pid_file.exists(), "deadline did not exercise the waiting Git hook")
        self.assertLess(time.monotonic() - hook_seen[0], 3)
        # A surviving helper would perform this write after the worker returned.
        # This observes the protected behavior without probing unrelated OS processes.
        time.sleep(max(0.0, hook_seen[0] + hook_sleep + 0.5 - time.monotonic()))
        self.assertFalse(survived.exists(), "Git helper survived worker cancellation")
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        hook.unlink()
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")

    def test_remote_owned_ref_rejects_unauthorized_files(self):
        self.enqueue()
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        ref = self.roots.transport["owned_ref"]
        parent = git(self.remote, "rev-parse", ref).decode().strip()
        git(self.remote, "read-tree", parent)
        oid = git(self.remote, "hash-object", "-w", "--stdin", payload=b"unapproved code\n").decode().strip()
        git(self.remote, "update-index", "--add", "--cacheinfo", "100644," + oid + ",program.py")
        tree = git(self.remote, "write-tree").decode().strip()
        commit = git(self.remote, "commit-tree", tree, "-p", parent, payload=b"unauthorized remote update\n").decode().strip()
        git(self.remote, "update-ref", ref, commit, parent)
        self.enqueue("next")
        result = transport.cycle(self.roots)
        self.assertEqual(result["status"], "failed", result)
        self.assertIn("unauthorized", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "next")["state"], "queued")
        self.assert_code_unchanged()

    def test_stale_private_index_is_discarded_without_touching_default_index(self):
        with transport.transport_lock(self.roots):
            transport.ensure_repository(self.roots)
        abandoned = self.roots.transport_root / "poga-mail-index-abandoned"
        abandoned.write_bytes(b"interrupted private index")
        self.enqueue()
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        self.assertFalse(abandoned.exists())

    # ------------------------------------------------------------------
    # The cases below were written from a mutation sweep of this module: each
    # one is a guard whose deletion left the whole mail suite green. A guard no
    # fixture reaches is a sentence of the item nothing holds.
    # ------------------------------------------------------------------

    def peer(self, name="peer"):
        """A second worker, its own state and transport, publishing to our remote."""
        other = replace(self.roots, state_root=self.base / (name + "-state"),
                        transport_root=self.base / (name + "-transport.git"))
        other.state_root.mkdir()
        return other

    def snapshot(self):
        with transport.transport_lock(self.roots):
            transport.ensure_repository(self.roots)
            return transport.fetch_snapshot(self.roots, "refs/heads/main")

    def test_retry_cycle_confirms_the_remote_ref_and_not_its_own_push_status(self):
        """A retry carrying no new mail must still read the remote ref back.

        Exit zero from `push` is not publication: a remote-side hook, or a competing
        writer between the push and the read, can leave the ref where it was. The cycle
        that exposes this is the one with NOTHING to add — it has only its own local
        commit to go on — which is exactly the restart the acceptance names.
        """
        self.enqueue()
        hook = self.reject_pushes()
        self.assertEqual(transport.cycle(self.roots)["status"], "failed")
        ref = self.roots.transport["owned_ref"]
        # Committed locally, absent from the remote: the retry below has no additions
        # of its own and can only re-push the commit it already holds.
        self.assertTrue(git(self.roots.transport_root, "rev-parse", ref))
        hook.unlink()
        discard = self.remote / "hooks" / "post-receive"
        discard.write_text("#!/bin/sh\ngit update-ref -d " + ref + "\n")
        discard.chmod(0o755)

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "failed", result)
        self.assertIn("could not be confirmed", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        discard.unlink()
        retried = transport.cycle(self.roots)
        self.assertEqual(retried["status"], "ok", retried)
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "published")
        self.assertEqual(transport.decode_message(self.remote_wire("one"))[1],
                         self.envelope("one").payload_path.read_bytes())

    def test_publication_never_forces_a_ref_and_always_names_its_expected_parent(self):
        """The two prohibitions that an earlier guard keeps out of reach behaviourally.

        `cycle` refuses a diverged remote before it can reach a push, so a forcing push
        and a compare-and-set-free `update-ref` produce the same observable outcome as
        the correct code on every reachable path. What distinguishes them is the command
        issued, so that is what this asserts — every push and every owned-ref update in a
        real publishing cycle, not a hand-built argv.
        """
        recorded = []
        real = transport._run

        def record(roots, args, **kwargs):
            recorded.append(list(args))
            return real(roots, args, **kwargs)

        self.enqueue("first")
        with mock.patch.object(transport, "_run", record):
            self.assertEqual(transport.cycle(self.roots)["status"], "ok")
            self.enqueue("second")
            self.assertEqual(transport.cycle(self.roots)["status"], "ok")

        ref = self.roots.transport["owned_ref"]
        pushes = [args for args in recorded if args and args[0] == "push"]
        updates = [args for args in recorded if args and args[0] == "update-ref"
                   and len(args) > 1 and args[1] == ref]
        self.assertTrue(pushes, "no push was recorded; the cycle never published")
        self.assertTrue(updates, "no owned-ref update was recorded")
        for args in pushes:
            self.assertNotIn("origin", args[:1])
            for forcing in ("-f", "--force", "--force-with-lease", "--mirror", "--all"):
                self.assertNotIn(forcing, args, f"publication used {forcing}: {args}")
            self.assertEqual(args[-1].split(":")[-1], ref, args)
            self.assertIn("origin", args)
        for args in updates:
            # `update-ref <ref> <new> <old>` — the third operand is the compare-and-set.
            self.assertEqual(len(args), 4, f"owned-ref update has no expected parent: {args}")

    def test_delivered_mail_is_retained_as_provenance_and_never_republished(self):
        """Acknowledgment-aware retention: delivery ends republication, not the archive."""
        payload = self.enqueue()
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        published = mailqueue.lifecycle(self.roots, "one")
        mailqueue.mark(self.roots, "one", "delivered", ack_commit="a" * 40)
        commit = git(self.remote, "rev-parse", self.roots.transport["owned_ref"])

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["published"], 0)
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), commit)
        self.assertTrue(payload.is_file())
        self.assertEqual(payload.read_bytes(), transport.decode_message(self.remote_wire("one"))[1])
        retained = mailqueue.lifecycle(self.roots, "one")
        self.assertEqual(retained["state"], "delivered")
        self.assertEqual(retained["remote_commit"], published["remote_commit"])
        self.assertEqual(retained["published_at"], published["published_at"])

    def test_worker_restart_without_its_transport_repository_republishes_nothing(self):
        """Restart on a reprovisioned host: the remote already holds the mail."""
        self.enqueue("first")
        self.enqueue("second")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        published = git(self.remote, "rev-parse", self.roots.transport["owned_ref"])
        shutil.rmtree(self.roots.transport_root)

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["published"], 2)
        self.assertEqual(git(self.remote, "rev-parse", self.roots.transport["owned_ref"]), published)
        names = git(self.remote, "ls-tree", "-r", "--name-only",
                    self.roots.transport["owned_ref"]).decode().splitlines()
        self.assertEqual(sorted(names), sorted([transport.message_path("first"),
                                                transport.message_path("second")]))
        self.assert_code_unchanged()

    def test_same_identity_with_different_content_refuses_instead_of_overwriting(self):
        """"Duplicate attempts preserve message identity" — including across workers."""
        peer = self.peer()
        mailqueue.enqueue(peer, "fixture-arch", "one.md", "The peer's text.\n",
                          message_id="one", provenance={"sender": "peer"})
        self.assertEqual(transport.cycle(peer)["status"], "ok")
        self.enqueue(text="A different body under the same identity.\n")

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "failed", result)
        self.assertIn("identity changed content", result["failure"])
        self.assertEqual(transport.decode_message(self.remote_wire("one"))[1],
                         b"The peer's text.\n")
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertTrue(self.envelope("one").payload_path.is_file())

    def test_transport_repository_must_be_dedicated_owned_and_explicitly_remoted(self):
        """Each perturbation names the rule that fired, not just the exception class."""
        self.enqueue()
        payload = self.envelope("one").payload_path
        original = dict(self.roots.transport)

        def prepare_marker_mismatch(roots):
            with transport.transport_lock(roots):
                transport.ensure_repository(roots)
            marker = roots.transport_root / "poga-mail-transport.json"
            data = json.loads(marker.read_bytes())
            data["remote"] = str(self.base / "somewhere-else.git")
            marker.write_bytes(json.dumps(data).encode())
            return roots

        def prepare_origin_mismatch(roots):
            with transport.transport_lock(roots):
                transport.ensure_repository(roots)
            git(roots.transport_root, "remote", "set-url", "origin",
                str(self.base / "somewhere-else.git"))
            return roots

        def prepare_non_bare(roots):
            roots.transport_root.mkdir(parents=True)
            # The branch name is irrelevant to this case; `main` is what the fixture
            # guard requires so the repo never inherits the machine's default.
            git(roots.transport_root, "init", "-q", "-b", "main")
            (roots.transport_root / "poga-mail-transport.json").write_bytes(
                json.dumps({"schema_version": 1, "remote": roots.transport["remote"],
                            "owned_ref": roots.transport["owned_ref"]},
                           ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode() + b"\n")
            return roots

        def prepare_no_remote(roots):
            roots.transport.pop("remote")
            return roots

        def prepare_inside_code(roots):
            return replace(roots, transport_root=self.code / "mail-transport.git")

        def prepare_symlinked_marker(roots):
            with transport.transport_lock(roots):
                transport.ensure_repository(roots)
            marker = roots.transport_root / "poga-mail-transport.json"
            elsewhere = self.base / (roots.transport_root.name + "-marker.json")
            elsewhere.write_bytes(marker.read_bytes())
            marker.unlink()
            marker.symlink_to(elsewhere)
            return roots

        cases = (
            ("ownership differs from configuration", prepare_marker_mismatch),
            ("ownership marker may not be a symlink", prepare_symlinked_marker),
            ("origin differs from its configured remote", prepare_origin_mismatch),
            ("dedicated bare repository", prepare_non_bare),
            ("explicit transport remote is required", prepare_no_remote),
            ("cannot be inside released code", prepare_inside_code),
        )
        for index, (expected, prepare) in enumerate(cases):
            with self.subTest(rule=expected):
                # A distinct directory per case; naming it after the rule once collided
                # with the fixture remote and reported the wrong refusal.
                roots = replace(self.roots, transport=dict(original),
                                transport_root=self.base / f"case-{index}-transport.git")
                roots = prepare(roots)
                result = transport.cycle(roots)
                self.assertEqual(result["status"], "failed", result)
                self.assertIn(expected, result["failure"])
                self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
                self.assertTrue(payload.is_file())
                self.assert_code_unchanged()

    def test_remote_ref_listing_is_parsed_as_untrusted_output(self):
        """`ls-remote` output is data from the other end, never a value to believe."""
        ref = self.roots.transport["owned_ref"]
        sha = "b" * 40
        cases = (
            ("ambiguous", f"{sha}\t{ref}\n{'c' * 40}\t{ref}\n"),
            ("invalid", f"{sha}\t{'refs/heads/other'}\n"),
            ("invalid", f"{'not-a-hash'}\t{ref}\n"),
            ("invalid", f"{sha}\t{ref}\textra\n"),
        )
        for expected, output in cases:
            with self.subTest(output=output):
                with mock.patch.object(transport, "_run",
                                       lambda *a, **k: (0, output.encode("ascii"))):
                    with self.assertRaises(transport.TransportError) as caught:
                        transport._remote_head(self.roots, ref)
                self.assertIn(f"remote ref has an {expected} result", str(caught.exception))

    def test_snapshot_listing_refuses_an_object_kind_it_does_not_expect(self):
        """`ls-tree -r` yields blobs and gitlinks today; the parser must not assume it.

        Reached only by supplying the subprocess output, because no tree this module
        can build produces another kind — that is what makes the guard invisible to
        every other test rather than what makes it unnecessary.
        """
        record = b"040000 tree " + b"d" * 40 + b"\tmessages\x00"
        with mock.patch.object(transport, "_run", lambda *a, **k: (0, record)):
            with self.assertRaises(transport.TransportError) as caught:
                transport.list_blobs(self.roots, "e" * 40)
        self.assertIn("unexpected snapshot object type", str(caught.exception))

    def test_an_owned_ref_of_the_wrong_shape_is_refused_before_any_repository_work(self):
        """The ref is a configured string that reaches a Git command line."""
        self.enqueue()
        shapes = ("refs/heads/../escape", "refs/heads/x..y", "refs/heads/.hidden",
                  "refs/heads/branch.lock", "refs/heads/has space", "refs/heads/x@{0}",
                  "refs/heads/", "refs/tags/v1", "refs/heads/a//b", "refs/heads/tilde~1",
                  "refs/heads/star*", "refs/heads/colon:ref", "refs/heads/trailing.")
        cases = [(value, "invalid transport branch ref") for value in shapes]
        cases += [(value, "transport ref must be a string") for value in (7, None, [])]
        for value, expected in cases:
            with self.subTest(owned_ref=value):
                roots = replace(self.roots, transport={**self.roots.transport,
                                                       "owned_ref": value},
                                transport_root=self.base / "shape-transport.git")
                result = transport.cycle(roots)
                self.assertEqual(result["status"], "failed", result)
                self.assertIn(expected, result["failure"])
                self.assertFalse(roots.transport_root.exists())
                self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")

    def test_retained_head_is_bounded_by_count_and_by_bytes_without_dropping_mail(self):
        self.enqueue("first")
        self.enqueue("second")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        commit = git(self.remote, "rev-parse", self.roots.transport["owned_ref"])
        self.enqueue("third")
        for key, value, expected in (("max_head_messages", 1, "retained message count"),
                                     ("max_head_bytes", 1, "retained message bytes")):
            with self.subTest(limit=key):
                roots = replace(self.roots, transport={**self.roots.transport, key: value})
                result = transport.cycle(roots)
                self.assertEqual(result["status"], "failed", result)
                self.assertIn(expected, result["failure"])
                self.assertEqual(git(self.remote, "rev-parse",
                                     self.roots.transport["owned_ref"]), commit)
                self.assertEqual(len(mailqueue.pending(self.roots)), 3)
                self.assertEqual(mailqueue.lifecycle(self.roots, "third")["state"], "queued")

    def test_retained_message_filed_under_a_foreign_identity_is_refused(self):
        """A restored or copied history can carry an intact message at the wrong path."""
        self.enqueue("first")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        ref = self.roots.transport["owned_ref"]
        wire = self.remote_wire("first")
        parent = git(self.remote, "rev-parse", ref).decode().strip()
        git(self.remote, "read-tree", parent)
        oid = git(self.remote, "hash-object", "-w", "--stdin", payload=wire).decode().strip()
        # The blob is valid and its hash matches; only the path is another identity's.
        git(self.remote, "update-index", "--add", "--cacheinfo",
            "100644," + oid + "," + transport.message_path("misfiled"))
        tree = git(self.remote, "write-tree").decode().strip()
        commit = git(self.remote, "commit-tree", tree, "-p", parent,
                     payload=b"misfiled message\n").decode().strip()
        git(self.remote, "update-ref", ref, commit, parent)
        self.enqueue("second")

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "failed", result)
        self.assertIn("does not match its identity", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "second")["state"], "queued")
        self.assertEqual(git(self.remote, "rev-parse", ref).decode().strip(), commit)

    def test_retained_message_readback_is_verified_against_what_was_requested(self):
        """The batched `cat-file` stream is parsed by offset, so both of its guards
        matter and they are separate rules.

        The header check proves the record answers the object that was asked for; the
        trailing-newline check proves the record was not cut short. They raise different
        sentences on purpose: a fixture that only truncates the stream trips the second
        one, and an assertion loose enough to accept either message lets the first be
        deleted without a single test turning red.
        """
        self.enqueue("first")
        self.assertEqual(transport.cycle(self.roots)["status"], "ok")
        real = transport._run

        def corrupting(mangle):
            def run(roots, args, **kwargs):
                code, out = real(roots, args, **kwargs)
                if args and args[0] == "cat-file" and "--batch" in args:
                    return code, mangle(out)
                return code, out
            return run

        def wrong_header(out):
            head, _, rest = out.partition(b"\n")
            oid, kind, size = head.split(b" ")
            flipped = (b"a" if oid[:1] != b"a" else b"b") + oid[1:]
            return b" ".join((flipped, kind, size)) + b"\n" + rest

        cases = (("invalid retained message readback", wrong_header),
                 ("truncated retained message readback", lambda out: out[:-3]))
        for index, (expected, mangle) in enumerate(cases):
            with self.subTest(rule=expected):
                # A fresh identity per case: a subtest that published its predecessor's
                # message would report "queued" from a queue that had already drained.
                name = f"held-{index}"
                self.enqueue(name)
                with mock.patch.object(transport, "_run", corrupting(mangle)):
                    result = transport.cycle(self.roots)
                self.assertEqual(result["status"], "failed", result)
                self.assertEqual(result["failure"], expected)
                self.assertEqual(mailqueue.lifecycle(self.roots, name)["state"], "queued")

    def test_publication_scrubs_a_queue_entry_that_was_written_without_the_gate(self):
        """The enqueue gate is not the only gate: migrated or restored spools skip it."""
        with mock.patch.object(scrub, "findings", lambda *a, **k: []):
            payload = self.enqueue(text="Host sits at 192.168.44.7 on the lab bench.\n")
        self.assertTrue(payload.is_file())

        result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "failed", result)
        self.assertIn("publication scrub check", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertTrue(payload.is_file())
        self.assertEqual(git(self.remote, "for-each-ref", "--format=%(refname)",
                             self.roots.transport["owned_ref"]), b"")

    def test_wire_refuses_missing_provenance_schema_and_timestamp(self):
        self.enqueue()
        envelope = self.envelope("one")
        blob = transport.encode_message(envelope, envelope.payload_path.read_bytes())
        cases = (("provenance", "not-an-object"), ("provenance", None),
                 ("created_at", ""), ("created_at", 17),
                 ("schema_version", 2), ("schema_version", "1"),
                 ("message_id", ""), ("message_id", None))
        for field, value in cases:
            with self.subTest(field=field, value=value):
                doc = json.loads(blob)
                doc[field] = value
                with self.assertRaises(transport.TransportError):
                    transport.decode_message(json.dumps(doc).encode())

    def test_wire_refuses_an_oversized_blob_before_parsing_it(self):
        oversized = b"[" + b"0," * (12 * 1024 * 1024) + b"0]"
        self.assertGreater(len(oversized), 24 * 1024 * 1024)
        with self.assertRaises(transport.TransportError) as caught:
            transport.decode_message(oversized)
        self.assertIn("oversized wire message", str(caught.exception))

    def test_encoding_refuses_an_envelope_its_own_decoder_would_reject(self):
        """encode_message decodes what it just built; without that, a producer with a
        malformed envelope publishes a message every reader will refuse forever."""
        self.enqueue()
        envelope = self.envelope("one")
        payload = envelope.payload_path.read_bytes()
        for field, value in (("created_at", ""), ("provenance", "none"),
                             ("destination", "../escape"), ("filename", "a/b.md")):
            with self.subTest(field=field):
                broken = {**envelope.as_dict(), field: value}
                with self.assertRaises(transport.TransportError):
                    transport.encode_message(broken, payload)

    def test_pinned_reads_refuse_unpinned_commits_and_escaping_paths(self):
        """Each case names the rule it expects: `cat-file` fails on these too, and a
        generic TransportError would let the address check be deleted unnoticed."""
        snapshot = self.snapshot()
        with transport.transport_lock(self.roots):
            for commit, path in ((snapshot, "../../etc/passwd"), (snapshot, "/etc/passwd"),
                                 (snapshot, "a/../../b"), ("HEAD", "program.py"),
                                 ("refs/heads/main", "program.py"), (snapshot[:8], "program.py")):
                with self.subTest(commit=commit, path=path):
                    with self.assertRaises(transport.TransportError) as caught:
                        transport.read_blob(self.roots, commit, path)
                    self.assertIn("invalid pinned blob address", str(caught.exception))
            with self.assertRaises(transport.TransportError) as caught:
                transport.list_blobs(self.roots, "refs/heads/main")
            self.assertIn("pinned commit", str(caught.exception))
            bounded = replace(self.roots,
                              transport={**self.roots.transport, "max_wire_bytes": 4})
            with self.assertRaises(transport.TransportError) as caught:
                transport.read_blob(bounded, snapshot, "program.py")
            self.assertIn("wire size limit", str(caught.exception))

    def test_a_queued_message_over_the_wire_limit_refuses_and_is_kept(self):
        self.enqueue(text="A fixture message that is longer than the configured ceiling.\n")
        roots = replace(self.roots, transport={**self.roots.transport, "max_wire_bytes": 32})

        result = transport.cycle(roots)

        self.assertEqual(result["status"], "failed", result)
        self.assertIn("wire size limit", result["failure"])
        self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")
        self.assertTrue(self.envelope("one").payload_path.is_file())

    def test_a_limit_outside_its_range_refuses_the_cycle_rather_than_being_coerced(self):
        self.enqueue()
        for key, value in (("max_batch_messages", 0), ("max_head_messages", -1),
                           ("network_timeout_seconds", 10 ** 6),
                           ("max_wire_bytes", "2mb"), ("max_batch_messages", True)):
            with self.subTest(limit=key, value=value):
                roots = replace(self.roots, transport={**self.roots.transport, key: value})
                result = transport.cycle(roots)
                self.assertEqual(result["status"], "failed", result)
                self.assertIn("invalid transport limit", result["failure"])
                self.assertEqual(mailqueue.lifecycle(self.roots, "one")["state"], "queued")

    def test_a_failing_git_command_is_a_transport_fault_not_a_silent_empty_result(self):
        with transport.transport_lock(self.roots):
            transport.ensure_repository(self.roots)
            with self.assertRaises(transport.TransportError) as caught:
                transport._run(self.roots, ["rev-parse", "--verify", "refs/heads/absent"])
            self.assertIn("transport Git operation failed", str(caught.exception))
            code, _out = transport._run(self.roots, ["rev-parse", "--verify", "refs/heads/absent"],
                                        check=False)
            self.assertNotEqual(code, 0)

    def test_ambient_git_repository_selection_never_reaches_the_transport(self):
        """A worker started from a Git hook inherits GIT_DIR and GIT_INDEX_FILE.

        Honouring either would operate on whatever repository invoked us — here, the
        release checkout — instead of the dedicated transport.
        """
        self.enqueue()
        stray = self.base / "stray-index"
        with mock.patch.dict(os.environ, {
                "GIT_DIR": str(self.code / ".git"), "GIT_WORK_TREE": str(self.code),
                "GIT_INDEX_FILE": str(stray)}):
            result = transport.cycle(self.roots)

        self.assertEqual(result["status"], "ok", result)
        self.assertFalse(stray.exists())
        self.assertEqual(transport.decode_message(self.remote_wire("one"))[1],
                         self.envelope("one").payload_path.read_bytes())
        self.assert_code_unchanged()


if __name__ == "__main__":
    unittest.main()


class TheOwnershipMarkerFollowsADeliberateRefChange(unittest.TestCase):
    """WI-0418. The marker is a refusal, and moving the transport off the channel branch
    turns that refusal against the migration performing the move.

    `ensure_repository` writes the owned ref into `poga-mail-transport.json` and refuses any
    repository whose marker disagrees with the configuration. That is exactly right when the
    disagreement means another owner, and exactly wrong when it means "this host is being
    migrated off `refs/heads/runner/mail`" -- which is the Runner's state since the v7.5.2
    apply wrote the marker at 11:24:53 on 2026-09-21.
    """

    CHANNEL = "refs/heads/runner/mail"
    TARGET = "refs/heads/runner/messages"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mailtransport-marker-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.environment = mock.patch.dict(os.environ, {
            "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "gc.auto", "GIT_CONFIG_VALUE_0": "0",
            "GIT_CONFIG_KEY_1": "maintenance.auto", "GIT_CONFIG_VALUE_1": "false",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.remote = self.base / "origin.git"
        self.remote.mkdir()
        git(self.remote, "init", "--bare", "-q", "-b", "main")
        git(self.base, "clone", "-q", str(self.remote), "code")
        self.code = self.base / "code"
        (self.code / "program.py").write_text("print('fixture release')\n")
        git(self.code, "add", "program.py")
        git(self.code, "commit", "-qm", "fixture release")
        git(self.code, "checkout", "-q", "--detach")
        self.state = self.base / "state"
        self.config_root = self.state / "config"
        self.config_root.mkdir(parents=True)

    def roots_for(self, owned_ref, remote=None):
        return production.Roots(
            self.code, self.state, self.base / "transport.git", self.config_root, None,
            {"remote": remote or str(self.remote), "owned_ref": owned_ref,
             "input_refs": []})

    def marker_of(self, roots):
        return json.loads(transport.ownership_marker(roots).read_bytes())

    def provision_on(self, owned_ref):
        """Build the repository the way the v7.5.2 apply built the Runner's: through
        `ensure_repository`, so the marker is the real one rather than a hand-written
        imitation of it."""
        roots = self.roots_for(owned_ref)
        transport.ensure_repository(roots)
        return roots

    # -- the real defect ----------------------------------------------------------

    def test_without_the_repoint_a_deliberate_ref_change_refuses_forever(self):
        """The negative control, and it is the state the Runner is actually in. Run first so
        the test below is proved against a failure that really happens rather than one
        asserted to."""
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        with self.assertRaisesRegex(transport.TransportError,
                                    "transport ownership differs from configuration"):
            transport.ensure_repository(moved)

    def test_the_repoint_clears_exactly_that_refusal(self):
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        self.assertTrue(transport.repoint_ownership(moved, from_ref=self.CHANNEL))
        self.assertEqual(self.marker_of(moved)["owned_ref"], self.TARGET)
        transport.ensure_repository(moved)          # no longer raises

    def test_the_rewritten_marker_is_byte_identical_to_a_freshly_written_one(self):
        """The marker is compared with `!=` against a dict built by `ensure_repository`, so a
        repoint that wrote the same fields a different way would pass here and refuse there.
        Comparing the BYTES is what makes the two writers one format."""
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        transport.repoint_ownership(moved, from_ref=self.CHANNEL)
        repointed = transport.ownership_marker(moved).read_bytes()
        transport.ownership_marker(moved).unlink()
        shutil.rmtree(moved.transport_root)
        transport.ensure_repository(self.roots_for(self.TARGET))
        self.assertEqual(repointed, transport.ownership_marker(moved).read_bytes())

    # -- everything it must NOT do ------------------------------------------------

    def test_another_owners_repository_is_still_refused(self):
        """The case the marker exists for. A repoint that ignored the remote would be an
        adoption with a migration's paperwork."""
        self.provision_on(self.CHANNEL)
        elsewhere = self.roots_for(self.TARGET, remote=str(self.base / "somewhere-else.git"))
        self.assertFalse(transport.repoint_ownership(elsewhere, from_ref=self.CHANNEL))
        self.assertEqual(self.marker_of(elsewhere)["owned_ref"], self.CHANNEL)
        with self.assertRaisesRegex(transport.TransportError,
                                    "transport ownership differs from configuration"):
            transport.ensure_repository(elsewhere)

    def test_a_marker_on_some_third_ref_is_not_collected(self):
        """`from_ref` names the one ref being left. A repoint that moved any mismatched
        marker would quietly relabel a repository owned by a host with its own ref names."""
        self.provision_on("refs/heads/host/outbound")
        moved = self.roots_for(self.TARGET)
        self.assertFalse(transport.repoint_ownership(moved, from_ref=self.CHANNEL))
        self.assertEqual(self.marker_of(moved)["owned_ref"], "refs/heads/host/outbound")

    def test_a_marker_already_on_the_target_is_not_rewritten(self):
        roots = self.provision_on(self.TARGET)
        before = transport.ownership_marker(roots).read_bytes()
        self.assertFalse(transport.repoint_ownership(roots, from_ref=self.CHANNEL))
        self.assertEqual(transport.ownership_marker(roots).read_bytes(), before)

    def test_a_repoint_onto_the_ref_being_left_is_refused_as_a_no_op(self):
        """Configured ref == `from_ref` means nothing is being migrated. Rewriting the marker
        with its own contents would report True and invite a log line saying a move happened."""
        roots = self.provision_on(self.CHANNEL)
        self.assertFalse(transport.repoint_ownership(roots, from_ref=self.CHANNEL))

    def test_no_marker_at_all_creates_nothing(self):
        """`ensure_repository` writes the marker BEFORE `git init` so an interrupted
        initialisation restarts. A repoint that created one would hand that restart a
        repository it never made."""
        moved = self.roots_for(self.TARGET)
        self.assertFalse(transport.repoint_ownership(moved, from_ref=self.CHANNEL))
        self.assertFalse(transport.ownership_marker(moved).exists())

    def test_an_unreadable_marker_is_left_for_ensure_repository_to_report(self):
        """Two refusals, two messages. "cannot validate transport ownership" says the file is
        broken; "differs from configuration" says somebody else owns it. A repoint that
        overwrote the broken one would convert the first into silence."""
        roots = self.provision_on(self.CHANNEL)
        transport.ownership_marker(roots).write_bytes(b"{ not json")
        moved = self.roots_for(self.TARGET)
        self.assertFalse(transport.repoint_ownership(moved, from_ref=self.CHANNEL))
        self.assertEqual(transport.ownership_marker(roots).read_bytes(), b"{ not json")
        with self.assertRaisesRegex(transport.TransportError,
                                    "cannot validate transport ownership"):
            transport.ensure_repository(moved)

    def test_a_symlinked_marker_is_never_followed(self):
        roots = self.provision_on(self.CHANNEL)
        marker = transport.ownership_marker(roots)
        real = self.base / "elsewhere.json"
        real.write_bytes(marker.read_bytes())
        marker.unlink()
        marker.symlink_to(real)
        moved = self.roots_for(self.TARGET)
        self.assertFalse(transport.repoint_ownership(moved, from_ref=self.CHANNEL))
        self.assertEqual(json.loads(real.read_bytes())["owned_ref"], self.CHANNEL)

    # -- WI-0425: every opener repoints, not only the migration --------------------
    #
    # The repoint first shipped inside the migration's one-time `provision` phase, and the
    # Runner had already recorded that phase, so its marker never moved and its worker kept
    # refusing. Every opener of the repository now goes through `claim_repository`, which
    # applies the same narrow repoint under the lock it already holds.

    def test_claim_repository_moves_a_channel_marker_and_then_validates(self):
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        self.assertTrue(transport.claim_repository(moved))
        self.assertEqual(self.marker_of(moved)["owned_ref"], self.TARGET)

    def test_the_negative_control_a_third_ref_marker_still_refuses(self):
        """The case the marker exists for. `claim_repository` must not turn every opener
        into an adopter of whatever repository it is pointed at."""
        self.provision_on("refs/heads/host/outbound")
        moved = self.roots_for(self.TARGET)
        with self.assertRaisesRegex(transport.TransportError,
                                    "transport ownership differs from configuration"):
            transport.claim_repository(moved)
        self.assertEqual(self.marker_of(moved)["owned_ref"], "refs/heads/host/outbound")

    def test_the_ref_it_leaves_is_read_off_the_channel(self):
        """The ref being left is `channel.BRANCH`, not a second literal: renaming the
        channel branch has to carry this with it. Under a renamed channel the old marker
        is no longer the one being left, so it is refused like any other owner."""
        import channel
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        with mock.patch.object(channel, "BRANCH", "host/renamed-channel"):
            with self.assertRaisesRegex(transport.TransportError,
                                        "transport ownership differs from configuration"):
                transport.claim_repository(moved)
        self.assertEqual(self.marker_of(moved)["owned_ref"], self.CHANNEL)

    def test_the_worker_cycle_itself_repoints_the_runner_marker(self):
        """The production path, end to end: a repository marked on the channel branch, a
        configuration already moved off it, and nothing but the worker's own cycle run.
        Before WI-0425 this cycle failed with "ownership differs from configuration"."""
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        result = transport.cycle(moved)
        self.assertNotIn("ownership", str(result.get("failure")), result)
        self.assertEqual(self.marker_of(moved)["owned_ref"], self.TARGET)

    def test_delivery_on_its_own_repoints_too(self):
        """Delivery opens the same repository under the same lock, and it can run without a
        publication before it. An opener that still called `ensure_repository` directly
        would refuse on the Runner's marker exactly as the worker used to."""
        import maildelivery
        self.provision_on(self.CHANNEL)
        moved = self.roots_for(self.TARGET)
        maildelivery.cycle(moved)                   # raised "ownership differs" before
        self.assertEqual(self.marker_of(moved)["owned_ref"], self.TARGET)
