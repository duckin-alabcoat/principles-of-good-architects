"""Generated mail never shares a name, and every generated brief carries an edit-id.

The receiver (`maildelivery.deliver_one`) delivers a brief whose name is taken ONLY when
the brief carries an edit-id; anything else is retried forever and never acknowledged.
On the Runner that grew pending mail 49 -> 154: day-named deploy escalations, repeated
adoption failures and standing adopt-runner condition notes all collided by name with no
edit-id, and deploy receipts for one tag reused one edit-id across statuses. Each test
below is a producer site from that evidence, driven through the real code with only the
production roots and the enqueue replaced.

All state lives in TemporaryDirectory; nothing touches a real queue or mailbox.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "curate"))
sys.path.insert(0, str(ROOT / "deploy"))
import mailnames  # noqa: E402
import mailqueue  # noqa: E402
import deliver  # noqa: E402
import outbox  # noqa: E402
import runner  # noqa: E402
import migrate  # noqa: E402

_spec = importlib.util.spec_from_file_location("adopt_runner_mailnames",
                                               ROOT / "curate" / "adopt-runner.py")
adopt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(adopt)


class Captured:
    """Stands in for `mailqueue.enqueue`; records what each producer would queue."""

    def __init__(self):
        self.calls = []

    def __call__(self, roots, destination, filename, text, message_id=None, provenance=None):
        self.calls.append({"destination": destination, "filename": filename, "text": text,
                           "message_id": message_id, "provenance": provenance})
        return Path("/nonexistent") / filename

    def holds(self, roots, message_id):
        return any(c["message_id"] == message_id for c in self.calls)


def _roots(tmp):
    return types.SimpleNamespace(comms_root=Path(tmp) / "comms",
                                 queue_root=Path(tmp) / "queue")


class HelperContract(unittest.TestCase):
    def test_edit_id_is_read_exactly_as_the_receiver_reads_it(self):
        for text in ("---\nedit-id: abc-1\n---\n", "- **Edit ID:** xyz.\n", "no id here\n",
                     "---\napply: manual\n---\n\nbody edit-id: not-a-header\n"):
            self.assertEqual(mailnames.brief_edit_id(text), deliver.brief_edit_id(text), text)

    def test_ensure_edit_id_is_deterministic_and_lands_in_the_frontmatter(self):
        text = "---\napply: manual\nmanual-reason: attended\n---\n\n# body\n"
        one = mailnames.ensure_edit_id(text, "mid-1", "brief.md")
        self.assertEqual(one, mailnames.ensure_edit_id(text, "mid-1", "brief.md"))
        self.assertNotEqual(one, mailnames.ensure_edit_id(text, "mid-2", "brief.md"))
        header, has_fm = adopt.parse_frontmatter(one)
        self.assertTrue(has_fm)
        self.assertEqual(header["edit-id"], mailnames.edit_id("mid-1", "brief.md"))
        self.assertEqual(header["apply"], "manual")
        self.assertEqual(deliver.brief_edit_id(one), mailnames.edit_id("mid-1", "brief.md"))

    def test_an_authors_own_edit_id_is_kept(self):
        text = "---\nedit-id: authored-1\n---\n\nbody\n"
        self.assertEqual(mailnames.ensure_edit_id(text, "mid", "b.md"), text)

    def test_a_brief_with_no_frontmatter_gains_one(self):
        out = mailnames.ensure_edit_id("# plain\n", "mid", "plain.md")
        self.assertTrue(out.startswith("---\nedit-id: plain-"))
        self.assertTrue(out.endswith("# plain\n"))

    def test_mail_names_differ_for_different_messages_at_the_same_second(self):
        when = "2026-09-29T01:12:03+00:00"
        a = mailnames.mail_name("2026-09-29-deploy-orbit-failed.md", "m1", when)
        b = mailnames.mail_name("2026-09-29-deploy-orbit-failed.md", "m2", when)
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("2026-09-29-deploy-orbit-failed-20260929T011203Z-"))
        self.assertTrue(a.endswith(".md"))


class EscalationMail(unittest.TestCase):
    def test_two_escalations_same_system_same_day_are_two_named_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "2026-09-29-deploy-orbit-failed.md"
            cap = Captured()
            with patch.object(runner.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(mailqueue, "enqueue", cap), \
                 patch.object(mailqueue, "holds", cap.holds):
                for attempt in ("first", "second"):
                    note.write_text(f"# orbit failed ({attempt})\n")
                    self.assertTrue(runner.publish_note(note, "orbit", lambda _: None))
        self.assertEqual(len(cap.calls), 2)
        names = [c["filename"] for c in cap.calls]
        self.assertNotEqual(names[0], names[1], "same-day escalations collide by name")
        ids = [deliver.brief_edit_id(c["text"]) for c in cap.calls]
        self.assertTrue(all(ids), "an escalation without an edit-id is retried forever")
        self.assertNotEqual(ids[0], ids[1])
        for c in cap.calls:
            self.assertEqual(deliver.brief_edit_id(c["text"]),
                             mailnames.edit_id(c["message_id"], note.name))
            self.assertIn("apply: manual", c["text"])


    def test_the_same_failure_retried_is_one_message_not_one_per_attempt(self):
        """A failing system is retried ~every 20 minutes and each attempt rewrites the
        note with a new `Raised` time. Unique names would deliver every one of them."""
        with tempfile.TemporaryDirectory() as tmp:
            note = Path(tmp) / "2026-09-29-deploy-orbit-failed.md"
            cap = Captured()
            with patch.object(runner.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(mailqueue, "enqueue", cap), \
                 patch.object(mailqueue, "holds", cap.holds):
                for raised in ("01:00", "01:20", "01:40"):
                    note.write_text(f"# orbit failed\n\n- **Raised:** {raised}\n\nsame\n")
                    self.assertTrue(runner.publish_note(note, "orbit", lambda _: None))
        self.assertEqual(len(cap.calls), 1)

    def test_holds_reads_the_real_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            roots = types.SimpleNamespace(
                state_root=Path(tmp),
                state_path=lambda *parts: Path(tmp).joinpath(*parts))
            self.assertFalse(mailqueue.holds(roots, "m-1"))
            with patch.object(mailqueue.scrub, "findings", return_value=[]):
                mailqueue.enqueue(roots, "federation-arch", "a.md", "body\n", message_id="m-1")
            self.assertTrue(mailqueue.holds(roots, "m-1"))
            self.assertFalse(mailqueue.holds(roots, "m-2"))


class ReceiptEditIds(unittest.TestCase):
    def test_one_tag_two_statuses_are_two_edit_ids(self):
        base = {"current": "v1.2.0", "attempted": "v1.2.0",
                "deployed_at": "2026-09-29T01:00:00Z"}
        ids = []
        for status in ("smoke-failed", "awaiting-cutover"):
            _, text = runner.result_brief("orbit", {**base, "status": status}, "fixture")
            ids.append(deliver.brief_edit_id(text))
        self.assertTrue(all(ids))
        self.assertNotEqual(ids[0], ids[1], "status must be part of a receipt's identity")


class AdoptRunnerMail(unittest.TestCase):
    def test_blocked_note_repeats_are_distinct_mail_with_edit_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            cap = Captured()
            with patch.object(adopt.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(adopt.mailqueue, "enqueue", cap):
                for _ in range(2):
                    note = adopt.write_comms_blocked(Path(tmp), {"architect_id": "orbit"},
                                                     "2026-09-29-some-brief", "verify failed",
                                                     "2026-09-29T01:00:00Z")
                    self.assertIsNotNone(note)
            self.assertEqual(len(list((Path(tmp) / "comms").iterdir())), 1,
                             "the LOCAL note keeps its one day name")
        self.assertEqual(len(cap.calls), 2)
        self.assertNotEqual(cap.calls[0]["filename"], cap.calls[1]["filename"])
        for c in cap.calls:
            self.assertTrue(deliver.brief_edit_id(c["text"]))

    def test_condition_note_refreshes_are_distinct_mail_with_edit_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "adopt-runner-stale.md"
            cap = Captured()
            with patch.object(adopt.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(adopt.mailqueue, "enqueue", cap):
                adopt._queue_condition(path, "condition one\n")
                path.write_text("condition one\n")
                adopt._queue_condition(path, "condition two\n")
                path.write_text("condition two\n")
                adopt._queue_condition(path, "", cleared=True)
        self.assertEqual(len(cap.calls), 3)
        names = {c["filename"] for c in cap.calls}
        self.assertEqual(len(names), 3, "a fixed mail name collides at the receiver")
        for c in cap.calls:
            self.assertTrue(c["filename"].startswith("adopt-runner-stale-"))
            self.assertTrue(deliver.brief_edit_id(c["text"]))
            self.assertTrue(adopt.parse_frontmatter(c["text"])[1])


class PassThroughProducers(unittest.TestCase):
    def test_a_staged_brief_without_an_edit_id_gains_one_from_its_message_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "2026-09-29-hand-written.md"
            src.write_text("---\napply: manual\nmanual-reason: attended\n---\n\n# hi\n")
            cap = Captured()
            with patch.object(outbox.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(outbox.mailqueue, "enqueue", cap):
                outbox.stage("orbit", src)
        (c,) = cap.calls
        self.assertTrue(c["message_id"])
        self.assertEqual(deliver.brief_edit_id(c["text"]),
                         mailnames.edit_id(c["message_id"], src.name))

    def test_a_staged_brief_keeps_its_authored_edit_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "b.md"
            text = "---\nedit-id: authored-7\n---\n\n# hi\n"
            src.write_text(text)
            cap = Captured()
            with patch.object(outbox.production, "resolve", return_value=_roots(tmp)), \
                 patch.object(outbox.mailqueue, "enqueue", cap):
                outbox.stage("orbit", src)
        self.assertEqual(cap.calls[0]["text"], text)

    def test_imported_mail_without_an_edit_id_gains_a_deterministic_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "old.md"
            src.write_text("# queued before the migration\n")
            recorded = {"mail": [{"source": str(src), "recipient": "orbit",
                                  "filename": "old.md", "sha256": "abc"}]}
            runs = []
            for _ in range(2):   # a resumed import must offer identical content
                cap = Captured()
                with patch.object(migrate.mailqueue, "enqueue", cap):
                    migrate.import_mail(_roots(tmp), recorded, lambda _: None)
                runs.append(cap.calls[0])
        self.assertEqual(runs[0]["text"], runs[1]["text"])
        self.assertEqual(deliver.brief_edit_id(runs[0]["text"]),
                         mailnames.edit_id(runs[0]["message_id"], "old.md"))
        self.assertTrue(runs[0]["text"].endswith("# queued before the migration\n"))


if __name__ == "__main__":
    unittest.main()
