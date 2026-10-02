"""Content and crash-boundary checks for remote mail delivery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "curate"))
import maildelivery as delivery
import mailqueue
import mailtransport
import reconcile
from production import Roots


class _Fixture(unittest.TestCase):
    """Shared roots/envelope only. Carries no test, so subclasses do not re-run a suite."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        for name in ("code", "state", "config", "inbox"):
            (self.base / name).mkdir()
        self.roots = Roots(self.base / "code", self.base / "state",
                           self.base / "transport", self.base / "config",
                           self.base / "inbox",
                           {"owned_ref": "refs/heads/receiver/mail",
                            "input_refs": ["refs/heads/sender/mail"],
                            "recipient_refs": {"test-arch": "refs/heads/sender/mail"}})
        self.payload = b"# Harmless fixture brief\n\nApply: manual\n"
        self.envelope = {"schema_version": 1, "message_id": "fixture-1",
                         "destination": "test-arch", "filename": "fixture.md",
                         "sha256": hashlib.sha256(self.payload).hexdigest(),
                         "provenance": {}, "created_at": "2026-09-14T00:00:00+00:00"}
        self.source = "refs/heads/sender/mail"
        # `locate_repos` runs reconcile's `compute_drift`, whose `self_entry` reads the
        # MAIN checkout's `STATUS.md` even when explicit roots are passed. Pointed at an
        # empty fixture directory so no test here reads the live store (ADR-0148 D3).
        main = self.base / "main-checkout"
        main.mkdir()
        patcher = mock.patch.object(reconcile, "_MAIN_CHECKOUT", main)
        patcher.start()
        self.addCleanup(patcher.stop)

    def copy(self, _destination, src, **_kwargs):
        dest = self.base / "inbox" / src.name
        shutil.copyfile(src, dest)
        return dest

    def receive(self, envelope=None, payload=None):
        return delivery.deliver_one(self.roots, self.source, "a" * 40,
                                     envelope or self.envelope,
                                     self.payload if payload is None else payload,
                                     mailboxes={}, repos={})


class MailDeliveryTests(_Fixture):
    def test_delivery_queues_ack_and_restart_does_not_copy_twice(self):
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy) as call:
            self.assertEqual(self.receive(), "delivered")
            self.assertEqual(self.receive(), "already-delivered")
        self.assertEqual(call.call_count, 1)
        pending = mailqueue.pending(self.roots)
        self.assertEqual(len(pending), 1)
        ack = json.loads(pending[0].payload_path.read_text())
        self.assertEqual(ack["sha256"], self.envelope["sha256"])
        self.assertEqual(ack["source_ref"], self.source)
        self.assertEqual((self.base / "inbox/fixture.md").read_bytes(), self.payload)

    def test_restart_after_receipt_before_ack_requeues_ack(self):
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy), \
                mock.patch.object(delivery, "_acknowledge", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                self.receive()
        with mock.patch.object(delivery.deliver, "deliver") as call:
            self.assertEqual(self.receive(), "already-delivered")
            call.assert_not_called()
        self.assertEqual(len(mailqueue.pending(self.roots)), 1)

    def test_restart_after_copy_before_ledger_accepts_exact_moved_content(self):
        destination = self.base / "inbox/accepted.md"
        destination.write_bytes(self.payload)
        held = delivery.deliver.AlreadyHeld("already moved", architect_id="test-arch",
                                            edit_id="fixture-1", where=str(destination))
        with mock.patch.object(delivery.deliver, "deliver", side_effect=held):
            self.assertEqual(self.receive(), "delivered")

    def test_same_edit_id_with_different_content_does_not_acknowledge(self):
        destination = self.base / "inbox/accepted.md"
        destination.write_bytes(b"A different brief")
        held = delivery.deliver.AlreadyHeld("same id", architect_id="test-arch",
                                            edit_id="fixture-1", where=str(destination))
        with mock.patch.object(delivery.deliver, "deliver", side_effect=held):
            with self.assertRaisesRegex(delivery.DeliveryError, "matching content"):
                self.receive()
        self.assertEqual(mailqueue.pending(self.roots), [])

    def test_same_transport_identity_cannot_inherit_old_receipt(self):
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy):
            self.receive()
        changed = b"# Changed fixture\n"
        envelope = dict(self.envelope, sha256=hashlib.sha256(changed).hexdigest())
        with self.assertRaisesRegex(delivery.DeliveryError, "identity reused"):
            self.receive(envelope, changed)

    def test_unsafe_filename_cannot_escape_state(self):
        with self.assertRaisesRegex(delivery.DeliveryError, "unsafe"):
            self.receive(dict(self.envelope, filename="../../escaped.md"))
        self.assertFalse((self.base / "escaped.md").exists())

    def test_symbolic_receive_directory_is_refused(self):
        (self.roots.state_root / "received").symlink_to(self.base / "inbox")
        with self.assertRaisesRegex(delivery.DeliveryError, "symbolic link"):
            self.receive()
        self.assertEqual(list((self.base / "inbox").iterdir()), [])

    def test_ack_requires_published_hash_recipient_and_configured_source(self):
        item_path = mailqueue.enqueue(self.roots, "test-arch", "sent.md",
                                      self.payload.decode(), message_id="sent-1")
        original = mailqueue.read(item_path)
        ack = {"schema_version": 1, "kind": "delivery-ack", "outcome": "delivered",
               "source_ref": self.roots.transport["owned_ref"],
               "message_id": original.message_id, "sha256": original.sha256,
               "destination": original.destination}
        def receive_ack(source=None, replacement=None):
            payload = (json.dumps(replacement or ack) + "\n").encode()
            envelope = dict(self.envelope, message_id="ack-1", destination="transport-ack",
                            sha256=hashlib.sha256(payload).hexdigest(),
                            provenance={"kind": "delivery-ack"})
            return delivery.deliver_one(self.roots, source or self.source, "a" * 40,
                                         envelope, payload)
        with self.assertRaisesRegex(delivery.DeliveryError, "not yet confirmed"):
            receive_ack()
        mailqueue.mark(self.roots, original.message_id, "published", commit="b" * 40)
        with self.assertRaisesRegex(delivery.DeliveryError, "not configured"):
            receive_ack("refs/heads/impostor/mail")
        with self.assertRaisesRegex(delivery.DeliveryError, "does not match"):
            receive_ack(replacement=dict(ack, sha256="0" * 64))
        self.assertEqual(receive_ack(), "acknowledged")
        self.assertEqual(mailqueue.lifecycle(self.roots, original.message_id)["state"], "delivered")

    def test_legacy_identity_includes_recipient_and_content(self):
        first = delivery._legacy("outbox/to-first-arch/note.md", self.payload, self.source)
        second = delivery._legacy("outbox/to-second-arch/note.md", self.payload, self.source)
        self.assertNotEqual(first["message_id"], second["message_id"])
        self.assertEqual(first["destination"], "first-arch")
        with self.assertRaises(delivery.DeliveryError):
            delivery._legacy("outbox/delivered/note.md", self.payload, self.source)

    def test_explicit_roots_use_external_roster_and_authoritative_inbox(self):
        from dataclasses import replace
        inbox = self.base / "authority/pending"
        inbox.mkdir(parents=True)
        roots = replace(self.roots, inbox_root=inbox)
        roster = {"federation": {"architect_id": "federation-arch", "mailbox": "wrong/pending",
                                 "tracked": False, "reachable": True}}
        (self.roots.config_root / "mailboxes.json").write_text(json.dumps({"members": roster}))
        for filename in ("repo-paths.local", "reconcile-roots.local"):
            (self.roots.config_root / filename).write_text("")
        payload = ("---\nedit-id: explicit-roots\nfrom: sender-arch\nto: federation-arch\n"
                   "apply: manual\nmanual-reason: attended\n---\n\nFixture.\n").encode()
        envelope = dict(self.envelope, destination="federation-arch",
                        sha256=hashlib.sha256(payload).hexdigest())
        with mock.patch.object(delivery.deliver.production, "resolve", side_effect=AssertionError("ambient roots")):
            outcome = delivery.deliver_one(roots, self.source, "a" * 40, envelope, payload)
        self.assertEqual(outcome, "delivered")
        self.assertEqual((inbox / envelope["filename"]).read_bytes(), payload)
        self.assertEqual(list(self.roots.code_root.iterdir()), [])

    def test_routing_and_author_errors_are_visible_outcomes_not_worker_failures(self):
        from contextlib import nullcontext
        entries = [("outbox/to-test-arch/a.md", "100644", "1"),
                   ("outbox/to-test-arch/b.md", "100644", "2")]
        with mock.patch.object(mailtransport, "transport_lock", return_value=nullcontext()), \
                mock.patch.object(mailtransport, "ensure_repository"), \
                mock.patch.object(mailtransport, "fetch_snapshot", return_value="a" * 40), \
                mock.patch.object(mailtransport, "list_blobs", side_effect=lambda _r, _c, p: entries if p == "outbox/" else []), \
                mock.patch.object(mailtransport, "read_blob", return_value=self.payload), \
                mock.patch.object(delivery, "deliver_one", side_effect=[
                    delivery.deliver.RecipientUnavailable("owning host", "elsewhere"),
                    delivery.deliver.RecipientUnavailable("no registry row", "unroutable")]):
            result = delivery.cycle(self.roots)
        self.assertEqual(result["errors"], [])
        self.assertEqual([r["outcome"] for r in result["outcomes"]], ["elsewhere", "unroutable"])

    def test_two_hosts_exchange_real_git_mail_and_ack_without_main(self):
        remote = self.base / "remote.git"
        subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", str(remote)], check=True)
        # Belt and braces: every commit that reaches this repo is made by mailtransport,
        # which already passes `-c commit.gpgsign=false`. Pinning it on the fixture itself
        # means the test does not silently start depending on that flag staying there.
        subprocess.run(["git", "-C", str(remote), "config", "commit.gpgsign", "false"],
                       check=True)
        receiver = self.roots
        receiver.transport["remote"] = str(remote)
        sender_base = self.base / "sender"
        for name in ("code", "state", "config", "inbox"):
            (sender_base / name).mkdir(parents=True)
        sender = Roots(sender_base / "code", sender_base / "state", sender_base / "transport",
                       sender_base / "config", sender_base / "inbox",
                       {"remote": str(remote), "owned_ref": self.source,
                        "input_refs": [receiver.transport["owned_ref"]],
                        "recipient_refs": {"test-arch": receiver.transport["owned_ref"]}})
        payload = ("---\nedit-id: fixture-round-trip\nfrom: sender-arch\nto: test-arch\n"
                   "apply: manual\nmanual-reason: attended\n---\n\nHarmless fixture.\n")
        item = mailqueue.read(mailqueue.enqueue(sender, "test-arch", "round-trip.md",
                                                payload, message_id="round-trip"))
        published = mailtransport.cycle(sender)
        self.assertEqual(published["status"], "ok", published)
        mailbox = self.base / "member/proposed-edits/test-arch/pending"
        mailbox.mkdir(parents=True)
        member = self.base / "member"
        (member / "session.config.json").write_text(json.dumps({"role_doc": "test-arch.md"}))
        (member / "test-arch.md").write_text("# Fixture role\n")
        roster = {"test": {"architect_id": "test-arch", "mailbox": "proposed-edits/test-arch/pending",
                            "tracked": False, "reachable": True}}
        result = delivery.cycle(receiver, mailboxes=roster, repos={"test": member})
        self.assertEqual(result["errors"], [])
        self.assertEqual((mailbox / "round-trip.md").read_text(), payload)
        self.assertEqual(mailtransport.cycle(receiver)["status"], "ok")
        result = delivery.cycle(sender, mailboxes={}, repos={})
        self.assertEqual(result["errors"], [])
        self.assertEqual(mailqueue.lifecycle(sender, item.message_id)["state"], "delivered")
        # Reverse direction uses the same publisher and receiver, rather than a second
        # implementation or an assumed symmetry of the first successful receipt.
        reverse = payload.replace("fixture-round-trip", "fixture-return")
        reverse_item = mailqueue.read(mailqueue.enqueue(receiver, "test-arch", "return.md",
                                                        reverse, message_id="return-trip"))
        self.assertEqual(mailtransport.cycle(receiver)["status"], "ok")
        result = delivery.cycle(sender, mailboxes=roster, repos={"test": member})
        self.assertEqual(result["errors"], [])
        self.assertEqual((mailbox / "return.md").read_text(), reverse)
        self.assertEqual(mailtransport.cycle(sender)["status"], "ok")
        result = delivery.cycle(receiver, mailboxes=roster, repos={"test": member})
        self.assertEqual(result["errors"], [])
        self.assertEqual(mailqueue.lifecycle(receiver, reverse_item.message_id)["state"], "delivered")
        refs = subprocess.check_output(["git", "--git-dir", str(remote), "for-each-ref", "--format=%(refname)"]).decode()
        self.assertNotIn("refs/heads/main", refs)
        self.assertIn("refs/heads/receiver/mail", refs)

    def test_receive_limit_rotates_past_prior_receipts_and_poison_messages(self):
        from contextlib import nullcontext
        self.roots.transport["max_receive_messages"] = 1
        entries = [("outbox/to-test-arch/one.md", "100644", "1"),
                   ("outbox/to-test-arch/two.md", "100644", "2")]
        seen = []
        def attempt(_roots, _ref, _commit, envelope, _payload, **_kwargs):
            seen.append(envelope["filename"])
            if envelope["filename"] == "one.md":
                raise delivery.DeliveryError("permanently malformed first message")
            return "delivered"
        with mock.patch.object(mailtransport, "transport_lock", return_value=nullcontext()), \
                mock.patch.object(mailtransport, "ensure_repository"), \
                mock.patch.object(mailtransport, "fetch_snapshot", return_value="a" * 40), \
                mock.patch.object(mailtransport, "list_blobs", side_effect=lambda _r, _c, prefix: entries if prefix == "outbox/" else []), \
                mock.patch.object(mailtransport, "read_blob", return_value=self.payload), \
                mock.patch.object(delivery, "deliver_one", side_effect=attempt):
            delivery.cycle(self.roots, mailboxes={}, repos={})
            delivery.cycle(self.roots, mailboxes={}, repos={})
        self.assertEqual(seen, ["one.md", "two.md"])


class AcknowledgmentAuthorizationTests(_Fixture):
    """Each refusal in the ack path, asserted by its own exact sentence.

    Two rules here raise the same class and one is a superset of the next, so a bare
    assertRaises or a shared substring would stay green with the narrower rule deleted.
    Every case also carries its own message identity, so one cannot drain another's queue.
    """

    def queued(self, message_id, provenance=None, text=None):
        """A message this host has published and is waiting to hear about."""
        path = mailqueue.enqueue(self.roots, "test-arch", message_id + ".md",
                                 text or self.payload.decode(), message_id=message_id,
                                 provenance=provenance)
        item = mailqueue.read(path)
        mailqueue.mark(self.roots, item.message_id, "published", commit="b" * 40)
        return item

    def ack_for(self, item):
        return {"schema_version": 1, "kind": "delivery-ack", "outcome": "delivered",
                "source_ref": self.roots.transport["owned_ref"],
                "message_id": item.message_id, "sha256": item.sha256,
                "destination": item.destination}

    def feed_ack(self, ack, *, source=None, destination="transport-ack", ident="ack-1"):
        payload = (json.dumps(ack) + "\n").encode()
        envelope = dict(self.envelope, message_id=ident, destination=destination,
                        sha256=hashlib.sha256(payload).hexdigest(),
                        provenance={"kind": "delivery-ack"})
        return delivery.deliver_one(self.roots, source or self.source, "a" * 40,
                                    envelope, payload)

    def test_each_malformed_ack_field_is_refused_on_its_own(self):
        item = self.queued("field-cases")
        good = self.ack_for(item)
        # One wrong field per case: a case that breaks several at once would stay green
        # if the four checks were joined by `and` instead of `or`.
        for label, ack in (("not an object", ["delivery-ack"]),
                           ("wrong schema", dict(good, schema_version=2)),
                           ("wrong kind", dict(good, kind="courier-note")),
                           ("wrong outcome", dict(good, outcome="failed"))):
            with self.subTest(label):
                with self.assertRaisesRegex(delivery.DeliveryError,
                                            r"^invalid acknowledgment outcome or version$"):
                    self.feed_ack(ack, ident="ack-" + label.replace(" ", "-"))
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_an_ack_addressed_to_a_member_is_not_applied_as_an_ack(self):
        item = self.queued("addressed-elsewhere")
        with self.assertRaisesRegex(delivery.DeliveryError,
                                    r"^acknowledgment must be addressed to transport-ack$"):
            self.feed_ack(self.ack_for(item), destination="test-arch")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_an_ack_about_another_hosts_ref_is_elsewhere_not_ours_to_apply(self):
        item = self.queued("someone-elses")
        ack = dict(self.ack_for(item), source_ref="refs/heads/third-party/mail")
        self.assertEqual(self.feed_ack(ack), "elsewhere")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_an_ack_with_no_usable_message_identity_is_refused(self):
        item = self.queued("identity-cases")
        good = self.ack_for(item)
        missing = {key: value for key, value in good.items() if key != "message_id"}
        # An empty string is a str and falsy; a number is neither. One case each, so the
        # two halves of the identity check are measured separately.
        for label, ack in (("absent", missing), ("empty", dict(good, message_id="")),
                           ("not a string", dict(good, message_id=42))):
            with self.subTest(label):
                with self.assertRaisesRegex(delivery.DeliveryError,
                                            r"^acknowledgment has no message identity$"):
                    self.feed_ack(ack, ident="ack-" + label.replace(" ", "-"))
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_an_ack_naming_a_message_this_host_never_queued_is_unmatched(self):
        item = self.queued("real-message")
        ack = dict(self.ack_for(item), message_id="never-queued-here")
        self.assertEqual(self.feed_ack(ack), "unmatched-ack")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_an_acknowledgment_is_never_itself_acknowledged(self):
        item = self.queued("an-ack-we-sent", provenance={"kind": "delivery-ack"})
        with self.assertRaisesRegex(delivery.DeliveryError,
                                    r"^acknowledgments are not acknowledged recursively$"):
            self.feed_ack(self.ack_for(item))
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")

    def test_a_lost_acknowledgment_leaves_the_sender_pending_until_one_arrives(self):
        """Pending state retires on a validated ack, never on a successful-looking cycle."""
        item = self.queued("awaiting-ack")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")
        self.assertIn(item.message_id, [e.message_id for e in mailqueue.pending(self.roots)])
        # A later cycle that carries no ack for it must not settle it either.
        self.assertEqual(self.feed_ack(dict(self.ack_for(item), message_id="other"),
                                       ident="ack-unrelated"), "unmatched-ack")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "published")
        self.assertEqual(self.feed_ack(self.ack_for(item), ident="ack-late"), "acknowledged")
        self.assertEqual(mailqueue.lifecycle(self.roots, item.message_id)["state"], "delivered")
        self.assertNotIn(item.message_id, [e.message_id for e in mailqueue.pending(self.roots)])


class IncomingContentTests(_Fixture):
    """The checks between a pinned remote blob and a recipient's mailbox."""

    def staged_path(self, envelope=None):
        envelope = envelope or self.envelope
        return (self.roots.state_root / "received" / delivery._digest(self.source)
                / delivery._digest(envelope["message_id"]) / "payload" / envelope["filename"])

    def test_a_payload_that_does_not_match_its_envelope_hash_is_refused(self):
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy) as call:
            with self.assertRaisesRegex(delivery.DeliveryError,
                                        r"^payload hash does not match envelope$"):
                self.receive(payload=b"# A different brief entirely\n")
        call.assert_not_called()
        self.assertEqual(list((self.base / "inbox").iterdir()), [])

    def test_a_restart_reuses_identical_staged_bytes_and_refuses_changed_ones(self):
        staged = self.staged_path()
        staged.parent.mkdir(parents=True)
        # Identical bytes are the ordinary crash-between-stage-and-deliver restart; the
        # refusal must be about the content differing, not about the file existing.
        staged.write_bytes(self.payload)
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy):
            self.assertEqual(self.receive(), "delivered")
        other = dict(self.envelope, message_id="second", filename="second.md")
        changed = self.staged_path(other)
        changed.parent.mkdir(parents=True)
        changed.write_bytes(b"# Staged under this identity by something else\n")
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy):
            with self.assertRaisesRegex(delivery.DeliveryError,
                                        r"^staged message content changed$"):
                self.receive(other)

    def test_an_incoming_payload_is_re_gated_by_scrub_before_it_reaches_a_mailbox(self):
        # Assembled at runtime so the fixture cannot itself trip a source-scanning gate.
        secret = ("# Fixture\n\nApply: manual\n\nhost at 10." + "0.0.7" + " responds\n").encode()
        self.assertTrue(delivery.scrub.findings(secret.decode()), "fixture must trip scrub")
        envelope = dict(self.envelope, sha256=hashlib.sha256(secret).hexdigest())
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.copy) as call:
            with self.assertRaisesRegex(delivery.DeliveryError,
                                        r"^incoming payload refused by scrub gate$"):
                self.receive(envelope, secret)
        call.assert_not_called()
        self.assertEqual(list((self.base / "inbox").iterdir()), [])

    def test_a_recipient_copy_that_does_not_read_back_is_refused(self):
        def wrong(_destination, src, **_kwargs):
            dest = self.base / "inbox" / src.name
            dest.write_bytes(b"# Not what we were asked to deliver\n")
            return dest
        with mock.patch.object(delivery.deliver, "deliver", side_effect=wrong):
            with self.assertRaisesRegex(delivery.DeliveryError, r"^recipient readback failed$"):
                self.receive()
        self.assertEqual(mailqueue.pending(self.roots), [])

    def test_a_legacy_path_with_no_recipient_is_refused(self):
        with self.assertRaisesRegex(delivery.DeliveryError,
                                    r"^legacy message has no recipient$"):
            delivery._legacy("outbox/to-/note.md", self.payload, self.source)


class ReceiveCycleTests(_Fixture):
    """cycle()'s own refusals, against blobs a hostile or broken sender could publish."""

    def snapshot(self, entries, decode=None, payload=None):
        from contextlib import nullcontext
        patches = [
            mock.patch.object(mailtransport, "transport_lock", return_value=nullcontext()),
            mock.patch.object(mailtransport, "ensure_repository"),
            mock.patch.object(mailtransport, "fetch_snapshot", return_value="a" * 40),
            mock.patch.object(mailtransport, "list_blobs",
                              side_effect=lambda _r, _c, prefix: [
                                  e for e in entries if e[0].startswith(prefix)]),
            mock.patch.object(mailtransport, "read_blob",
                              return_value=self.payload if payload is None else payload),
        ]
        if decode is not None:
            patches.append(mock.patch.object(mailtransport, "decode_message", side_effect=decode))
        return patches

    def run_cycle(self, entries, decode=None, payload=None, deliver_side=None):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patch in self.snapshot(entries, decode, payload):
                stack.enter_context(patch)
            call = stack.enter_context(mock.patch.object(
                delivery.deliver, "deliver",
                side_effect=deliver_side or self.copy))
            return delivery.cycle(self.roots, mailboxes={}, repos={}), call

    def test_a_nonpositive_receive_limit_is_refused_rather_than_silently_draining(self):
        self.roots.transport["max_receive_messages"] = 0
        with self.assertRaisesRegex(delivery.DeliveryError,
                                    r"^max_receive_messages must be positive$"):
            delivery.cycle(self.roots, mailboxes={}, repos={})

    def test_a_remote_symlink_blob_is_never_delivered(self):
        result, call = self.run_cycle([("outbox/to-test-arch/link.md", "120000", "1")])
        self.assertEqual([e.get("error") for e in result["errors"]],
                         ["remote mail must be a regular blob"])
        self.assertEqual(result["outcomes"], [])
        call.assert_not_called()

    def test_a_message_filed_under_another_identity_is_refused(self):
        entries = [("messages/" + "0" * 64 + ".json", "100644", "1")]
        envelope = dict(self.envelope, message_id="claims-to-be-this")
        result, call = self.run_cycle(entries, decode=lambda blob: (envelope, self.payload))
        self.assertEqual([e.get("error") for e in result["errors"]],
                         ["wire path does not match message identity"])
        self.assertEqual(result["outcomes"], [])
        call.assert_not_called()

    def test_legacy_queued_mail_drains_once_and_a_second_pass_does_not_reapply_it(self):
        entries = [("outbox/to-test-arch/legacy.md", "100644", "1")]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patch in self.snapshot(entries):
                stack.enter_context(patch)
            call = stack.enter_context(mock.patch.object(
                delivery.deliver, "deliver", side_effect=self.copy))
            first = delivery.cycle(self.roots, mailboxes={}, repos={})
            second = delivery.cycle(self.roots, mailboxes={}, repos={})
        self.assertEqual(first["errors"], [])
        self.assertEqual(second["errors"], [])
        self.assertEqual([o["outcome"] for o in first["outcomes"]], ["delivered"])
        self.assertEqual([o["outcome"] for o in second["outcomes"]], ["already-delivered"])
        self.assertEqual(call.call_count, 1)
        self.assertEqual((self.base / "inbox/legacy.md").read_bytes(), self.payload)


def _diagnosis(stamp, checked="2026-09-21T09:00:00+00:00", state="smoke-failed",
               destination="test-arch", system="fixture-system"):
    """A deploy.diagnose envelope + payload with the real header and bookkeeping lines."""
    digits = "".join(c for c in stamp if c.isdigit())
    payload = (f"---\napply: manual\nmanual-reason: attended\n"
               f"edit-id: diagnose-{system}-host-{digits}\n---\n\n# {system}\n\n"
               f"- **Collected:** {stamp}\n- **checked_at:** {checked}\n"
               f"- **status:** {state}\n").encode()
    envelope = {"schema_version": 1, "message_id": f"diagnose:{system}:host:{stamp}",
                "destination": destination, "filename": f"{stamp[:10]}-diagnose-{system}.md",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "provenance": {"producer": "deploy.diagnose", "system": system},
                "created_at": stamp}
    return envelope, payload


class SameDayNameCollisionTests(_Fixture):
    """OPS-0010 soak: the second diagnosis of a day was refused on its NAME forever —
    no receipt, no ack — and the Runner's pending count only grew."""

    def taken(self):
        return delivery.deliver.NameTaken("clash", architect_id="test-arch",
                                          where="elsewhere", taken={"fixture.md"})

    def test_a_brief_with_its_own_edit_id_is_delivered_under_the_next_free_name(self):
        payload = b"---\napply: manual\nedit-id: second-of-the-day\n---\n\n# brief\n"
        envelope = dict(self.envelope, sha256=hashlib.sha256(payload).hexdigest())
        def clash_once(destination, src, **kwargs):
            if src.name == "fixture.md":
                raise self.taken()
            return self.copy(destination, src)
        with mock.patch.object(delivery.deliver, "deliver", side_effect=clash_once) as call:
            self.assertEqual(self.receive(envelope, payload), "delivered")
        self.assertEqual(call.call_args_list[1][0][1].name, "fixture.2.md")
        self.assertEqual((self.base / "inbox/fixture.2.md").read_bytes(), payload)
        ack = json.loads(mailqueue.pending(self.roots)[0].payload_path.read_text())
        self.assertEqual(ack["sha256"], envelope["sha256"],
                         "the renamed delivery was not acknowledged, so the sender stays pending")

    def test_a_CONTROL_brief_without_an_edit_id_keeps_the_refusal(self):
        """Nothing then proves the two briefs differ; the old one may be this one, annotated."""
        with mock.patch.object(delivery.deliver, "deliver", side_effect=self.taken()) as call:
            with self.assertRaises(delivery.deliver.NameTaken):
                self.receive()
        self.assertEqual(call.call_count, 1)
        self.assertEqual(mailqueue.pending(self.roots), [])


class DuplicateDiagnosisTests(ReceiveCycleTests):
    """Receiver and sender pick the same representative of an exact-duplicate group."""

    def run_wire(self, mail):
        table = {f"messages/{delivery._digest(e['message_id'])}.json": (e, p) for e, p in mail}
        entries = [(path, "100644", "1") for path in sorted(table)]
        from contextlib import ExitStack
        with ExitStack() as stack:
            for patch in self.snapshot(entries):
                stack.enter_context(patch)
            stack.enter_context(mock.patch.object(
                mailtransport, "read_blob", side_effect=lambda _r, _c, path: path.encode()))
            stack.enter_context(mock.patch.object(
                mailtransport, "decode_message", side_effect=lambda blob: table[blob.decode()]))
            call = stack.enter_context(mock.patch.object(
                delivery.deliver, "deliver", side_effect=self.copy))
            result = delivery.cycle(self.roots, mailboxes={}, repos={})
        return {o["message_id"]: o["outcome"] for o in result["outcomes"]}, call, result

    def test_only_the_oldest_of_a_duplicate_group_is_delivered_and_acked(self):
        old = _diagnosis("2026-09-21T10:02:55+00:00", checked="2026-09-21T09:51:33+00:00")
        dup = _diagnosis("2026-09-21T10:54:11+00:00", checked="2026-09-21T10:54:04+00:00")
        new_state = _diagnosis("2026-09-21T11:04:00+00:00", state="deployed")
        outcomes, call, result = self.run_wire([dup, old, new_state])
        self.assertEqual(result["errors"], [])
        self.assertEqual(outcomes, {old[0]["message_id"]: "delivered",
                                    dup[0]["message_id"]: "superseded",
                                    new_state[0]["message_id"]: "delivered"})
        self.assertEqual(call.call_count, 2)
        acked = {json.loads(e.payload_path.read_text())["message_id"]
                 for e in mailqueue.pending(self.roots)}
        self.assertNotIn(dup[0]["message_id"], acked, "a skipped duplicate was acknowledged")

    def test_a_member_already_received_becomes_the_representative(self):
        """The first soak shape: the first bundle of the day was delivered BEFORE the
        rule existed, and it is not the oldest in its group. Picking the oldest anyway
        would deliver a second copy of content the recipient already has."""
        old = _diagnosis("2026-09-21T10:02:55+00:00")
        received = _diagnosis("2026-09-21T10:54:11+00:00")
        self.run_wire([received])
        outcomes, call, _result = self.run_wire([old, received])
        self.assertEqual(outcomes, {old[0]["message_id"]: "superseded",
                                    received[0]["message_id"]: "already-delivered"})
        call.assert_not_called()

    def test_every_member_already_received_still_re_acks(self):
        """Two same-content bundles delivered before the rule existed: both hold
        receipts. Skipping the younger one would stop re-queueing its ack, and a lost ack
        would then leave the sender pending on mail the recipient has."""
        one = _diagnosis("2026-09-21T10:02:55+00:00")
        two = _diagnosis("2026-09-21T10:54:11+00:00")
        self.run_wire([one])
        self.run_wire([two])
        outcomes, call, _result = self.run_wire([one, two])
        self.assertEqual(set(outcomes.values()), {"already-delivered"})
        call.assert_not_called()

    def test_a_CONTROL_mail_from_any_other_producer_never_collapses(self):
        one = _diagnosis("2026-09-21T10:02:55+00:00")
        two = _diagnosis("2026-09-21T10:54:11+00:00")
        for envelope, _payload in (one, two):
            envelope["provenance"] = {"producer": "deploy.runner", "system": "fixture-system"}
        outcomes, _call, _result = self.run_wire([one, two])
        self.assertEqual(set(outcomes.values()), {"delivered"})


class SenderCollapseTests(_Fixture):
    """The Runner side: `mailqueue.collapse` over the queue it owns."""

    def queue(self, stamp, **kw):
        envelope, payload = _diagnosis(stamp, **kw)
        mailqueue.enqueue(self.roots, envelope["destination"], envelope["filename"],
                          payload.decode(), message_id=envelope["message_id"],
                          provenance=envelope["provenance"])
        mailqueue.mark(self.roots, envelope["message_id"], "published", remote_ref="r")
        return envelope["message_id"]

    def test_duplicates_defer_to_the_delivered_member_and_keep_their_payloads(self):
        first = self.queue("2026-09-21T10:02:55+00:00", checked="2026-09-21T09:51:33+00:00")
        acked = self.queue("2026-09-21T10:54:11+00:00", checked="2026-09-21T10:54:04+00:00")
        third = self.queue("2026-09-22T08:00:00+00:00", checked="2026-09-22T07:59:00+00:00")
        distinct = self.queue("2026-09-22T09:00:00+00:00", state="deployed")
        mailqueue.mark(self.roots, acked, "delivered", acknowledgment={"outcome": "delivered"})
        self.assertEqual(sorted(mailqueue.collapse(self.roots)), sorted([first, third]))
        self.assertEqual([e.message_id for e in mailqueue.pending(self.roots)], [distinct])
        for message_id in (first, third):
            state = mailqueue.lifecycle(self.roots, message_id)
            self.assertEqual((state["state"], state["superseded_by"]), ("superseded", acked))
            path = self.roots.state_path("queue", mailqueue.identity_key(message_id))
            self.assertTrue(mailqueue.read(path).payload_path.is_file(), "a payload was deleted")
        self.assertEqual(mailqueue.collapse(self.roots), [], "a second pass is not a no-op")

    def test_an_ack_arriving_for_a_superseded_message_still_records_delivered(self):
        """The recipient's word outranks our bookkeeping: `superseded` is not terminal."""
        first = self.queue("2026-09-21T10:02:55+00:00")
        later = self.queue("2026-09-21T10:54:11+00:00")
        mailqueue.collapse(self.roots)
        self.assertEqual(mailqueue.lifecycle(self.roots, later)["state"], "superseded")
        mailqueue.mark(self.roots, later, "delivered", acknowledgment={"outcome": "delivered"})
        self.assertEqual(mailqueue.lifecycle(self.roots, later)["state"], "delivered")
        self.assertEqual(mailqueue.lifecycle(self.roots, first)["state"], "published")

    def test_a_CONTROL_distinct_undelivered_mail_is_never_superseded(self):
        self.queue("2026-09-21T10:02:55+00:00", state="smoke-failed")
        self.queue("2026-09-21T10:54:11+00:00", state="deployed")
        self.assertEqual(mailqueue.collapse(self.roots), [])
        self.assertEqual(len(mailqueue.pending(self.roots)), 2)


class OutcomeVisibilityTests(_Fixture):
    """Every outcome a receive cycle can report must land in a distinct observation.

    The subjects come from the delivery module's own vocabulary rather than from a list
    written here, so an outcome added later cannot default to invisible: an unclassified
    value is reported as healthy by the surface an operator actually reads.
    """

    # Values deliver_one and _apply_ack can return, plus the RecipientUnavailable
    # outcomes cycle() copies through. Derived by reading every return in the module.
    ROUTINE = ("delivered", "already-delivered", "acknowledged", "elsewhere", "superseded",
               "already-undeliverable", "acknowledged-undeliverable")
    ATTENTION = ("unroutable", "unreachable", "malformed", "unmatched-ack", "undeliverable")

    def worker_record(self, outcomes):
        import mailworker
        (self.roots.state_root / "runtime").mkdir(parents=True, exist_ok=True)
        consume = lambda _roots: {"snapshots": [{"ref": self.source, "commit": "a" * 40}],
                                  "outcomes": [{"ref": self.source, "outcome": value}
                                               for value in outcomes],
                                  "errors": [], "busy": False}
        publish = lambda _roots: {"status": "ok", "published": 0, "last_publication": None}
        return mailworker.run_cycle(self.roots, publish=publish, consume=consume)

    def test_the_delivery_vocabulary_and_the_attention_set_do_not_overlap(self):
        self.assertEqual(set(self.ROUTINE) & set(self.ATTENTION), set())

    def test_an_outcome_needing_a_human_reaches_the_attention_list(self):
        for outcome in self.ATTENTION:
            with self.subTest(outcome):
                record = self.worker_record([outcome])
                self.assertEqual([item["outcome"] for item in record["attention"]], [outcome],
                                 f"{outcome!r} is invisible to the operator-facing surface")

    def test_a_routine_outcome_does_not_cry_wolf(self):
        for outcome in self.ROUTINE:
            with self.subTest(outcome):
                record = self.worker_record([outcome])
                self.assertEqual(record["attention"], [])


class UndeliverableTests(_Fixture):
    """A message no retry can deliver gets a TERMINAL state with a reason — a negative
    ack — instead of sitting in the sender's pending set forever while health says `ok`.
    Leaving pending must not hide it: the sender counts it and its health is not `ok`."""

    MESSAGE_ID = "doomed-1"

    def setUp(self):
        super().setUp()
        self.envelope = dict(self.envelope, message_id=self.MESSAGE_ID)
        self.entries = [("messages/" + hashlib.sha256(self.MESSAGE_ID.encode()).hexdigest()
                         + ".json", "100644", "1")]
        # The SENDER's side of the same exchange: it owns the ref the receiver reads.
        sender = self.base / "sender"
        for name in ("code", "state", "config"):
            (sender / name).mkdir(parents=True)
        self.sender = Roots(sender / "code", sender / "state", sender / "transport",
                            sender / "config", None,
                            {"owned_ref": self.source,
                             "input_refs": ["refs/heads/receiver/mail"],
                             "recipient_refs": {"test-arch": "refs/heads/receiver/mail"}})

    snapshot = ReceiveCycleTests.snapshot       # the helpers, not the suite

    def cycle(self, deliver_side):
        return ReceiveCycleTests.run_cycle(
            self, self.entries, decode=lambda _blob: (self.envelope, self.payload),
            deliver_side=deliver_side)

    def acks(self):
        return [json.loads(e.payload_path.read_text()) for e in mailqueue.pending(self.roots)
                if e.provenance.get("kind") == "delivery-ack"]

    def sender_item(self):
        path = mailqueue.enqueue(self.sender, "test-arch", "fixture.md",
                                 self.payload.decode(), message_id=self.MESSAGE_ID)
        mailqueue.mark(self.sender, self.MESSAGE_ID, "published", commit="b" * 40)
        return mailqueue.read(path)

    def feed(self, ack, ident):
        payload = (json.dumps(ack) + "\n").encode()
        envelope = dict(self.envelope, message_id=ident, destination="transport-ack",
                        sha256=hashlib.sha256(payload).hexdigest(),
                        provenance={"kind": "delivery-ack"})
        return delivery.deliver_one(self.sender, "refs/heads/receiver/mail", "c" * 40,
                                    envelope, payload)

    def malformed(self):
        return delivery.deliver.MalformedBrief("routes to nobody at /Users/x/secret",
                                               brief="b", detail="d")

    # ── receiver ──────────────────────────────────────────────────────────────────
    def test_a_malformed_brief_yields_a_negative_ack_with_a_reason(self):
        result, call = self.cycle(self.malformed())
        self.assertEqual(result["errors"], [])
        self.assertEqual([(o["outcome"], o["reason"]) for o in result["outcomes"]],
                         [("undeliverable", "malformed")])
        [ack] = self.acks()
        self.assertEqual((ack["outcome"], ack["message_id"], ack["reason"]["code"]),
                         ("undeliverable", self.MESSAGE_ID, "malformed"))
        self.assertNotIn("/Users/", ack["reason"]["detail"],
                         "exception text (local paths) must not be published to a peer")
        self.assertEqual(call.call_count, 1)

    def test_a_declared_message_is_not_retried_and_its_ack_is_stable(self):
        self.cycle(self.malformed())
        result, call = self.cycle(self.malformed())
        call.assert_not_called()
        self.assertEqual([o["outcome"] for o in result["outcomes"]], ["already-undeliverable"])
        self.assertEqual(len(self.acks()), 1, "a re-ack must be the same queue identity")

    def test_a_brief_with_no_edit_id_whose_name_is_taken_is_undeliverable(self):
        taken = delivery.deliver.NameTaken("clash", architect_id="test-arch",
                                           where="x", taken={"fixture.md"})
        result, _call = self.cycle(taken)
        self.assertEqual([(o["outcome"], o["reason"]) for o in result["outcomes"]],
                         [("undeliverable", "name-taken")])

    def test_a_held_id_with_different_content_is_undeliverable(self):
        other = self.base / "inbox/held.md"
        other.write_bytes(b"a different brief")
        held = delivery.deliver.AlreadyHeld("held", architect_id="test-arch",
                                            edit_id="x", where=str(other))
        result, _call = self.cycle(held)
        self.assertEqual([(o["outcome"], o["reason"]) for o in result["outcomes"]],
                         [("undeliverable", "held-mismatch")])

    def test_an_unroutable_recipient_is_declared_only_past_its_threshold(self):
        self.roots.transport.update(undeliverable_after_attempts=3,
                                    undeliverable_after_seconds=0)
        unroutable = delivery.deliver.RecipientUnavailable("no row", "unroutable")
        outcomes = [self.cycle(unroutable)[0]["outcomes"][0]["outcome"] for _ in range(3)]
        self.assertEqual(outcomes, ["unroutable", "unroutable", "undeliverable"])
        self.assertEqual([a["reason"]["code"] for a in self.acks()], ["unroutable"])

    def test_an_unroutable_recipient_below_the_age_threshold_stays_pending(self):
        self.roots.transport.update(undeliverable_after_attempts=1,
                                    undeliverable_after_seconds=3600)
        unroutable = delivery.deliver.RecipientUnavailable("no row", "unroutable")
        for _ in range(3):
            self.assertEqual(self.cycle(unroutable)[0]["outcomes"][0]["outcome"], "unroutable")
        self.assertEqual(self.acks(), [])

    def test_a_transient_failure_is_never_declared(self):
        self.roots.transport.update(undeliverable_after_attempts=1,
                                    undeliverable_after_seconds=0)
        for failure in (delivery.deliver.RecipientUnavailable("repo-paths", "unreachable"),
                        delivery.deliver.RecipientUnavailable("other host", "elsewhere"),
                        OSError("disk busy"), ValueError("mailbox does not exist on disk")):
            with self.subTest(failure=failure):
                for _ in range(3):
                    result, _call = self.cycle(failure)
                    self.assertNotIn("undeliverable",
                                     [o["outcome"] for o in result["outcomes"]])
                self.assertEqual(self.acks(), [])

    # ── sender ────────────────────────────────────────────────────────────────────
    def sender_health(self):
        import mailworker
        mailworker.run_cycle(
            self.sender, publish=lambda _r: {"status": "ok", "last_publication": None},
            consume=lambda _r: {"snapshots": [], "outcomes": [], "errors": []})
        return mailworker.read_status(self.sender)

    def test_the_sender_marks_it_undeliverable_counts_it_and_is_not_ok(self):
        self.sender_item()
        self.cycle(self.malformed())
        [ack] = self.acks()
        self.assertEqual(self.feed(ack, "ack-neg"), "acknowledged-undeliverable")
        state = mailqueue.lifecycle(self.sender, self.MESSAGE_ID)
        self.assertEqual((state["state"], state["acknowledgment"]["reason"]["code"]),
                         ("undeliverable", "malformed"))
        self.assertEqual(mailqueue.pending(self.sender), [])
        health = self.sender_health()
        self.assertEqual((health["undeliverable_count"], health["undeliverable_reasons"]),
                         (1, {"malformed": 1}))
        self.assertEqual(health["pending_count"], 0)
        self.assertEqual(health["status"], "undeliverable")
        clause, fault = delivery.deliver.production_mail_clause(self.sender)
        self.assertIn("1 message(s) UNDELIVERABLE (malformed 1)", clause)
        self.assertTrue(fault)

    def test_a_later_delivered_ack_overrides_undeliverable(self):
        item = self.sender_item()
        self.cycle(self.malformed())
        [ack] = self.acks()
        self.feed(ack, "ack-neg")
        positive = {"schema_version": 1, "kind": "delivery-ack", "outcome": "delivered",
                    "source_ref": self.source, "message_id": item.message_id,
                    "sha256": item.sha256, "destination": item.destination}
        self.assertEqual(self.feed(positive, "ack-pos"), "acknowledged")
        self.assertEqual(mailqueue.lifecycle(self.sender, self.MESSAGE_ID)["state"], "delivered")
        # ...and the stale negative ack, re-read on a later cycle, cannot demote it.
        self.feed(ack, "ack-neg")
        self.assertEqual(mailqueue.lifecycle(self.sender, self.MESSAGE_ID)["state"], "delivered")
        health = self.sender_health()
        self.assertEqual((health["undeliverable_count"], health["status"]), (0, "ok"))

    def test_a_negative_ack_without_a_valid_reason_is_refused(self):
        item = self.sender_item()
        base = {"schema_version": 1, "kind": "delivery-ack", "outcome": "undeliverable",
                "source_ref": self.source, "message_id": item.message_id,
                "sha256": item.sha256, "destination": item.destination}
        for bad in (None, {"code": "malformed"}, {"code": "Not A Code", "detail": "x"}):
            with self.subTest(reason=bad):
                with self.assertRaisesRegex(delivery.DeliveryError,
                                            r"^undeliverable acknowledgment has no valid reason$"):
                    self.feed(dict(base, reason=bad), "ack-bad")
        self.assertEqual(mailqueue.lifecycle(self.sender, self.MESSAGE_ID)["state"], "published")

    def test_an_unrouted_recipient_accepts_only_a_negative_ack_from_a_peer_it_reads(self):
        self.sender.transport["recipient_refs"] = {}
        item = self.sender_item()
        base = {"schema_version": 1, "kind": "delivery-ack", "source_ref": self.source,
                "message_id": item.message_id, "sha256": item.sha256,
                "destination": item.destination}
        with self.assertRaisesRegex(delivery.DeliveryError, "not configured for this recipient"):
            self.feed(dict(base, outcome="delivered"), "ack-pos")
        negative = dict(base, outcome="undeliverable",
                        reason={"code": "unroutable", "detail": "no row"})
        self.assertEqual(self.feed(negative, "ack-neg"), "acknowledged-undeliverable")


if __name__ == "__main__":
    unittest.main()
