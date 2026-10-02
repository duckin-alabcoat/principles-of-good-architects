"""`curate/deliver.py` — delivery is a lookup, and every failure is its own refusal.

The defect this tool exists for lost briefs in BOTH directions while both sides reported
success: a reply to one member sat in the federation's own tree for days, and a
brief addressed to federation-arch sat in another member's tree for days. The first sweep for
it checked only one direction and its count was reported as the answer, so the
both-direction property below is pinned rather than trusted.

The `tracked` refusal is the send-side guard: writing into a mailbox git tracks gets the
brief swept into the recipient's history under THEIR authorship (ADR-0088 D1). It is the
one mechanical rule the old advice ("never write inside an observed repo") could not
express — too strict for a gitignored inbox, too loose for the case that causes harm.
"""

import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_spec = importlib.util.spec_from_file_location(
    "deliver", pathlib.Path(__file__).resolve().parent.parent / "curate" / "deliver.py")
deliver_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deliver_mod)


def _mailboxes(**over):
    base = {
        "alpha": {"architect_id": "alpha-arch", "mailbox": "proposed-edits/alpha-arch/pending",
                  "tracked": False, "reachable": True},
        "beta":  {"architect_id": "beta-arch", "mailbox": "proposed-edits/beta-arch/pending",
                  "tracked": True, "reachable": True},
        "gamma": {"architect_id": "gamma-arch", "mailbox": "inbox/pending",
                  "tracked": False, "reachable": True},
    }
    base.update(over)
    return base


# Delivery now runs check-apply.py before writing, so a brief body in a delivery-path
# fixture has to declare an apply mode that routes to SOMEONE (ADR-0049/ADR-0050). The
# headerless "payload" stubs these fixtures used to carry are exactly what the guard now
# refuses — which is the point of the guard, not a fixture inconvenience.
_ROUTABLE_BRIEF = (
    "---\n"
    "edit-id: fixture-brief\n"
    "from: federation-arch\n"
    "to: alpha-arch\n"
    "apply: manual\n"
    "manual-reason: attended\n"
    "expected-base: n/a\n"
    "---\n\n# fixture\n"
)


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.alpha = self.tmp / "alpha"
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.gamma = self.tmp / "gamma"
        (self.gamma / "inbox" / "pending").mkdir(parents=True)
        self.repos = {"alpha": self.alpha, "beta": self.tmp / "beta", "gamma": self.gamma}

    def test_resolves_a_conforming_member(self):
        _repo, box, _row = deliver_mod.resolve("alpha-arch", _mailboxes(), self.repos)
        self.assertTrue(box.is_dir())

    def test_a_non_standard_path_resolves_from_the_registry_not_a_convention(self):
        """gamma's mailbox is at `inbox/pending`. One member is the real instance: a survey
        that ASSUMED the fleet-standard layout reported it as having no mailbox at all.
        Delivery must read the recorded path, never derive one."""
        _repo, box, _row = deliver_mod.resolve("gamma-arch", _mailboxes(), self.repos)
        self.assertEqual(box, self.gamma / "inbox" / "pending")

    def test_a_tracked_mailbox_is_refused(self):
        with self.assertRaises(ValueError) as e:
            deliver_mod.resolve("beta-arch", _mailboxes(), self.repos)
        self.assertIn("TRACKED", str(e.exception))

    def test_an_unknown_recipient_is_refused_and_lists_the_known(self):
        with self.assertRaises(ValueError) as e:
            deliver_mod.resolve("nobody-arch", _mailboxes(), self.repos)
        msg = str(e.exception)
        self.assertIn("unknown recipient", msg)
        self.assertIn("alpha-arch", msg, "name who IS deliverable")
        self.assertIn("UNSURVEYED", msg,
                      "absent from the registry must not read as 'has no mailbox'")

    def test_a_renamed_member_resolves_by_its_own_declared_architect_id(self):
        """The live case: a member renamed itself, so mailboxes.json keys it by the
        new id while the locator (which reads STATUS.md) still finds the old one. The two
        indexes miss each other and delivery refused a member sitting right there. Refusing
        was correct; being unable to find it was not. Ask the repo what it calls itself."""
        renamed = self.tmp / "old-name"
        (renamed / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        (renamed / "session.config.json").write_text(
            '{"architect_id": "alpha-arch"}', encoding="utf-8")
        # Located under a STALE system id — exactly what a mid-rename fleet looks like.
        _repo, box, _row = deliver_mod.resolve(
            "alpha-arch", _mailboxes(), {"stale-old-id": renamed})
        self.assertEqual(box, renamed / "proposed-edits" / "alpha-arch" / "pending")

    def test_the_fallback_does_not_rescue_a_genuinely_absent_member(self):
        """The fallback must not become a way to guess. A repo that declares a different
        id is not a match."""
        other = self.tmp / "other"
        other.mkdir()
        (other / "session.config.json").write_text(
            '{"architect_id": "someone-else-arch"}', encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            deliver_mod.resolve("alpha-arch", _mailboxes(), {"other": other})
        self.assertIn("not locatable", str(e.exception))

    def test_an_unlocatable_member_is_a_location_problem_not_a_missing_mailbox(self):
        with self.assertRaises(ValueError) as e:
            deliver_mod.resolve("alpha-arch", _mailboxes(), {})
        self.assertIn("not locatable", str(e.exception))
        self.assertIn("repo-paths.local", str(e.exception))

    def test_a_registry_row_pointing_at_nothing_is_refused(self):
        """A registry that has drifted from the filesystem is worse than none — it makes
        a guess look like a lookup."""
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").rmdir()
        with self.assertRaises(ValueError) as e:
            deliver_mod.resolve("alpha-arch", _mailboxes(), self.repos)
        self.assertIn("does not exist on disk", str(e.exception))


class DeliverTest(ResolveTest):
    def test_delivers_and_verifies_by_reading_back(self):
        src = self.tmp / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        dest = deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertEqual(dest.read_text(encoding="utf-8"), _ROUTABLE_BRIEF)

    def test_an_unroutable_brief_is_refused_before_the_write(self):
        """The live instance: `2026-08-04-your-mailbox-is-tracked-and-should-be-gitignored`
        went to a member declaring `apply: manual` with no `verify:` and a manual-reason that is
        not `attended` — so the headless runner could not verify it and operator was never
        routed it. check-apply.py had diagnosed exactly this since session 68; nothing on
        the delivery path ever ran it."""
        src = self.tmp / "unroutable.md"
        src.write_text("---\nedit-id: x\nfrom: federation-arch\nto: alpha-arch\n"
                       "apply: manual\nmanual-reason: someone should look at this\n"
                       "expected-base: n/a\n---\n\n# x\n", encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertIn("routes it to nobody", str(e.exception))
        self.assertIn("Nothing was written", str(e.exception))
        # And the refusal is BEFORE the copy, not reported after it.
        box = self.alpha / "proposed-edits" / "alpha-arch" / "pending"
        self.assertEqual(list(box.glob("*.md")), [])

    def test_a_missing_checker_refuses_rather_than_passing(self):
        """Fail-CLOSED. A guard that waves things through when it cannot run is worse
        than no guard — it certifies what it never checked."""
        src = self.tmp / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        real = deliver_mod.CHECK_APPLY
        deliver_mod.CHECK_APPLY = self.tmp / "no-such-dir" / "check-apply.py"
        self.addCleanup(setattr, deliver_mod, "CHECK_APPLY", real)
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        # The specific branch — not the missing-brief one that precedes it.
        self.assertIn("cannot verify apply mode", str(e.exception))
        box = self.alpha / "proposed-edits" / "alpha-arch" / "pending"
        self.assertEqual(list(box.glob("*.md")), [])

    def test_a_missing_brief_writes_nothing(self):
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self.tmp / "nope.md", _mailboxes(), self.repos)
        self.assertIn("Nothing was written", str(e.exception))

    def test_a_refused_delivery_leaves_the_mailbox_empty(self):
        """The refusal must happen BEFORE the write, not be reported after it."""
        src = self.tmp / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        with self.assertRaises(ValueError):
            deliver_mod.deliver("beta-arch", src, _mailboxes(), self.repos)
        self.assertEqual(list((self.tmp / "beta").rglob("*.md")), [])


class CollisionTest(ResolveTest):
    """A destination can be reachable, untracked and on the roster and STILL be the wrong
    place to write — because the recipient already holds something there.

    WI-0072's third misdelivery instance: a ruling written to the right mailbox under a
    filename that collided with a closed record. Every check `resolve()` makes would have
    passed it, which is exactly why the item flagged that the obvious send-side predicate
    ("is it reachable and untracked?") is not enough.
    """

    def _brief(self, name="brief.md", eid="fixture-brief"):
        src = self.tmp / name
        src.write_text(_ROUTABLE_BRIEF.replace("edit-id: fixture-brief", f"edit-id: {eid}"),
                       encoding="utf-8")
        return src

    def _theirs(self, state, name, eid):
        d = self.alpha / "proposed-edits" / "alpha-arch" / state
        d.mkdir(parents=True, exist_ok=True)
        f = d / name
        f.write_text(f"---\nedit-id: {eid}\n---\n\n# theirs\n", encoding="utf-8")
        return f

    def test_a_brief_they_already_closed_is_not_redelivered(self):
        """The live shape: redelivering re-opens a settled record as fresh pending mail,
        with the terminal record still on disk beside it."""
        self._theirs("applied", "already.md", "fixture-brief")
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        msg = str(e.exception)
        self.assertIn("already hold", msg)
        self.assertIn("fixture-brief", msg, "name the edit-id that collided")
        self.assertIn("applied", msg, "name WHERE it already sits — the state is the point")
        self.assertIn("Nothing was written", msg)

    def test_a_brief_still_unread_in_their_pending_is_not_overwritten(self):
        self._theirs("pending", "already.md", "fixture-brief")
        with self.assertRaises(ValueError):
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        # Their copy is untouched — the refusal is before the write, not after it.
        held = self.alpha / "proposed-edits" / "alpha-arch" / "pending" / "already.md"
        self.assertIn("# theirs", held.read_text(encoding="utf-8"))

    def test_same_filename_different_id_is_refused_by_name(self):
        """Matched on edit-id first, filename second — but a name clash still destroys
        mail, so it refuses rather than falling through to the copy."""
        self._theirs("pending", "brief.md", "something-else")
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        msg = str(e.exception)
        self.assertIn("already at", msg)
        self.assertIn("Writing would", msg)
        self.assertIn("Nothing was written", msg)

    def test_replace_is_the_deliberate_override(self):
        """The escape exists, and it is an explicit act rather than the default."""
        self._theirs("pending", "brief.md", "something-else")
        dest = deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos,
                                   replace=True)
        self.assertIn("edit-id: fixture-brief", dest.read_text(encoding="utf-8"))

    def test_an_unreadable_tree_refuses_rather_than_writing_blind(self):
        """Fail-CLOSED. Not being able to see what is in the mailbox is not evidence that
        nothing is — the same rule `_check_apply_mode` follows for a missing checker."""
        real = deliver_mod._index_mailbox
        deliver_mod._index_mailbox = lambda _tree: (None, None)
        self.addCleanup(setattr, deliver_mod, "_index_mailbox", real)
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertIn("REFUSING to write blind", str(e.exception))
        box = self.alpha / "proposed-edits" / "alpha-arch" / "pending"
        self.assertEqual(list(box.glob("*.md")), [])

    def test_an_empty_mailbox_still_delivers(self):
        """The guard must not become a refusal to deliver anything at all."""
        dest = deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertTrue(dest.is_file())

    # ── WI-0235: which refusals are evidence of delivery, and which are not ──

    def test_an_edit_id_they_hold_raises_AlreadyHeld_not_a_bare_ValueError(self):
        """Typed so an unattended caller can tell "did not need to" from "could not".
        Separating those by matching prose is one wording change away from booking a
        delivered brief as a failure again, which is the whole of WI-0235."""
        held = self._theirs("applied", "already.md", "fixture-brief")
        with self.assertRaises(deliver_mod.AlreadyHeld) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertEqual(e.exception.edit_id, "fixture-brief")
        self.assertEqual(e.exception.architect_id, "alpha-arch")
        self.assertEqual(e.exception.where, str(held),
                         "carry the recipient-side evidence, not just the verdict")

    def test_AlreadyHeld_is_still_a_ValueError_so_no_existing_caller_changed(self):
        """It WIDENS what a caller can know and narrows nothing. Every `except ValueError`
        on this module — the CLI included — behaves exactly as it did."""
        self.assertTrue(issubclass(deliver_mod.AlreadyHeld, ValueError))

    def test_a_CONTROL_name_collision_is_NOT_AlreadyHeld(self):
        """Same name, no matching edit-id: two different briefs colliding. No evidence
        anything arrived, so it must not reach the caller wearing the type that means it
        did — or the new bucket becomes a place failures go to be forgiven."""
        self._theirs("pending", "brief.md", "something-else")
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertNotIsInstance(e.exception, deliver_mod.AlreadyHeld)

    def test_a_name_collision_is_NameTaken_and_names_every_label_in_the_tree(self):
        """OPS-0010 soak: the mail worker renames on THIS refusal and no other, so it is
        typed; and `taken` spans the tree, because `applied/` still owns its names."""
        self._theirs("pending", "brief.md", "something-else")
        self._theirs("applied", "brief.2.md", "older-one")
        with self.assertRaises(deliver_mod.NameTaken) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertTrue({"brief.md", "brief.2.md"} <= e.exception.taken)
        renamed = deliver_mod.free_name("brief.md", e.exception.taken)
        self.assertEqual(renamed, "brief.3.md")
        dest = deliver_mod.deliver("alpha-arch", self._brief(name=renamed), _mailboxes(),
                                   self.repos)
        self.assertEqual(dest.name, "brief.3.md")
        self.assertIn("something-else", (dest.parent / "brief.md").read_text(),
                      "the brief already there was touched")

    def test_a_CONTROL_held_edit_id_and_an_unreadable_tree_are_NOT_NameTaken(self):
        """Only the label-only clash may be renamed past. A held edit-id renamed would
        deliver the same brief twice; an unreadable tree renamed would write blind."""
        self._theirs("applied", "already.md", "fixture-brief")
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertNotIsInstance(e.exception, deliver_mod.NameTaken)
        real = deliver_mod._index_mailbox
        deliver_mod._index_mailbox = lambda _tree: (None, None)
        self.addCleanup(setattr, deliver_mod, "_index_mailbox", real)
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(name="other.md", eid="other"),
                                _mailboxes(), self.repos)
        self.assertNotIsInstance(e.exception, deliver_mod.NameTaken)

    def test_a_CONTROL_unreadable_tree_is_NOT_AlreadyHeld(self):
        """`could not tell` is the third state and always has been. It is not evidence of
        delivery, and fail-closed means it stays a plain refusal."""
        real = deliver_mod._index_mailbox
        deliver_mod._index_mailbox = lambda _tree: (None, None)
        self.addCleanup(setattr, deliver_mod, "_index_mailbox", real)
        with self.assertRaises(ValueError) as e:
            deliver_mod.deliver("alpha-arch", self._brief(), _mailboxes(), self.repos)
        self.assertNotIsInstance(e.exception, deliver_mod.AlreadyHeld)


class RetireSourceTest(unittest.TestCase):
    """After delivery the sender's copy leaves `pending/`, or the audit reports mail that
    DID arrive as stuck — which would train us to ignore the report built to catch silent
    non-delivery. Found by using the tool: five real deliveries, five still flagged."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fed = self.tmp / "fed" / "proposed-edits"
        self.pend = self.fed / "alpha-arch" / "pending"
        self.pend.mkdir(parents=True)
        self._save = deliver_mod.FED_PROPOSED
        deliver_mod.FED_PROPOSED = self.fed
        self.addCleanup(setattr, deliver_mod, "FED_PROPOSED", self._save)
        self.member = self.tmp / "alpha"
        (self.member / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.repos = {"alpha": self.member}

    def test_delivery_moves_the_source_out_of_pending(self):
        src = self.pend / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertFalse(src.exists(), "the sender's pending copy must not linger")
        moved = self.fed / "alpha-arch" / "delivered" / "brief.md"
        self.assertTrue(moved.is_file(), "moved, not deleted — it is the record we sent it")
        self.assertEqual(moved.read_text(encoding="utf-8"), _ROUTABLE_BRIEF)

    def test_a_source_outside_our_tree_is_never_relocated(self):
        """A brief handed to us from elsewhere is not ours to move."""
        src = self.tmp / "elsewhere.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertTrue(src.is_file())

    def test_delivery_survives_a_failed_retire(self):
        """Bookkeeping must never undo a delivery that already succeeded."""
        src = self.pend / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        dest = deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        deliver_mod._retire_source(src)          # already moved; must not raise
        self.assertTrue(dest.is_file())


class OutboxSourceRetireTest(unittest.TestCase):
    """The other queue — and the one whose omission MANUFACTURED WI-0235.

    A brief authored in `outbox/to-<id>/` and delivered directly is the correct move when
    the recipient is reachable from this machine; waiting for a poll buys nothing. But the
    containment test knew only `proposed-edits/`, so the queued copy stayed, every later
    poll re-delivered it, hit the collision guard, and booked a failure for a brief that
    had arrived. Retiring here removes the class at its source rather than teaching the
    poller to forgive it afterwards."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.outbox_root = self.tmp / "fed" / "outbox"
        self.queue = self.outbox_root / "to-alpha-arch"
        self.queue.mkdir(parents=True)
        for mod, name, value in (
                (deliver_mod, "FED_PROPOSED", self.tmp / "fed" / "proposed-edits"),
                (deliver_mod.outbox, "OUTBOX", self.outbox_root),
                (deliver_mod.outbox, "DELIVERED", self.outbox_root / "delivered"),
        ):
            self.addCleanup(setattr, mod, name, getattr(mod, name))
            setattr(mod, name, value)
        self.member = self.tmp / "alpha"
        (self.member / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.repos = {"alpha": self.member}

    def test_a_brief_delivered_straight_out_of_the_QUEUE_does_not_stay_queued(self):
        src = self.queue / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertFalse(src.exists(), "the queued copy is what polled forever")
        filed = self.outbox_root / "delivered" / "brief.md"
        self.assertTrue(filed.is_file(), "filed, never deleted — it is the record we sent it")

    def test_it_reuses_the_queues_own_retire_rather_than_a_second_copy_of_it(self):
        """`outbox.retire` already keeps two briefs of one name apart. A reimplementation
        here is the drift P16 names, so the dedup must be observable from this side."""
        (self.outbox_root / "delivered").mkdir(parents=True)
        (self.outbox_root / "delivered" / "brief.md").write_text("earlier\n", encoding="utf-8")
        src = self.queue / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertEqual((self.outbox_root / "delivered" / "brief.md").read_text(),
                         "earlier\n", "the earlier receipt is not overwritten")
        self.assertTrue((self.outbox_root / "delivered" / "brief.2.md").is_file())

    def test_a_file_already_in_delivered_is_not_retired_onto_itself(self):
        """Containment alone would match `outbox/delivered/` too, and the dedup would then
        silently turn a re-send into a `.2` receipt for a brief that never moved."""
        filed_dir = self.outbox_root / "delivered"
        filed_dir.mkdir(parents=True)
        src = filed_dir / "brief.md"
        src.write_text(_ROUTABLE_BRIEF, encoding="utf-8")
        deliver_mod.deliver("alpha-arch", src, _mailboxes(), self.repos)
        self.assertTrue(src.is_file())
        self.assertFalse((filed_dir / "brief.2.md").exists())


class AuditBothDirectionsTest(unittest.TestCase):
    """Half a sweep certifying the channel clean is worse than no sweep — it converts an
    unknown into a false assurance. Both directions, pinned separately."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fed = self.tmp / "fed"
        (self.fed / "proposed-edits").mkdir(parents=True)
        self._save = deliver_mod.FED_PROPOSED
        deliver_mod.FED_PROPOSED = self.fed / "proposed-edits"
        self.addCleanup(setattr, deliver_mod, "FED_PROPOSED", self._save)
        self.member = self.tmp / "alpha"
        (self.member / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.repos = {"alpha": self.member}

    def _fed_pending(self, arch):
        d = deliver_mod.FED_PROPOSED / arch / "pending"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _brief(self, edit_id):
        return f"---\nedit-id: {edit_id}\n---\n\n# A brief\n"

    def test_clean_when_nothing_is_stuck(self):
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual((out, inb), ([], []))

    def test_our_own_inbox_is_not_reported_as_undelivered(self):
        """Mail that ARRIVED is not mail that failed to leave."""
        (self._fed_pending("federation-arch") / "incoming.md").write_text("x", encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual(out, [])

    def test_outbound_stuck_in_our_tree_is_found(self):
        (self._fed_pending("alpha-arch") / "never-sent.md").write_text(
            self._brief("never-sent"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.architect_id for v in out], ["alpha-arch"])
        self.assertEqual([v.state for v in out], [deliver_mod.UNDELIVERED])

    def test_inbound_misrouted_in_a_member_tree_is_found(self):
        """The direction the first sweep missed entirely."""
        d = self.member / "proposed-edits" / "federation-arch" / "pending"
        d.mkdir(parents=True)
        (d / "addressed-to-us.md").write_text(self._brief("to-us"), encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([(v.holder, v.architect_id) for v in inb],
                         [("alpha", "federation-arch")])

    def test_a_members_own_inbox_is_not_misrouted(self):
        d = self.member / "proposed-edits" / "alpha-arch" / "pending"
        (d / "their-mail.md").write_text("x", encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual(inb, [])

    def test_status_line_is_silent_when_clean_and_speaks_when_not(self):
        self.assertEqual(deliver_mod.audit_status_line.__doc__.split("\n")[0][:4], "One-")
        (self._fed_pending("alpha-arch") / "stuck.md").write_text(
            self._brief("stuck"), encoding="utf-8")
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertTrue(out and not inb)


class GradedAuditTest(AuditBothDirectionsTest):
    """WI-0101. The audit used to infer the verdict from OUR tree alone — presence in our
    `pending/` printed as "never sent" — so of five briefs it flagged, four had already
    been delivered and three were sitting in the recipient's `applied/`. "We still hold a
    copy" and "they never received it" are different facts.

    Each state below is pinned separately because the defect was precisely that two of
    them had nowhere to go and came out as the third.

    RENAMED FROM `ThreeStateAuditTest` BY WI-0223, which found the same crowding one level
    in: the third state was itself carrying five different facts, four fixable and one
    permanent. There are five graded states now — DELIVERED, CORROBORATED, UNDELIVERED,
    UNKNOWN, UNRESOLVABLE — and the name had to stop asserting a number that a future
    grade would falsify again ([`declare-what-a-check-assumes`])."""

    def _theirs(self, sub="pending"):
        d = self.member / "proposed-edits" / "alpha-arch" / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_a_brief_the_recipient_holds_is_DELIVERED_not_undelivered(self):
        """The four-of-five case, in miniature."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("edit-42"), encoding="utf-8")
        (self._theirs() / "b.md").write_text(self._brief("edit-42"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.DELIVERED])

    def test_delivery_is_matched_by_edit_id_across_the_recipients_apply_annotation(self):
        """The recipient rewrites `State: Pending` to `State: Applied` and adds a stamp on
        apply, so the two copies are NOT byte-identical — establishing this defect by hand
        needed an md5 and a diff. Matching on bytes would call an applied brief undelivered."""
        ours = "---\nedit-id: edit-42\n---\n\nState: Pending\n"
        theirs = "---\nedit-id: edit-42\n---\n\nState: Applied\nApplied-at: 2026-07-07\n"
        (self._fed_pending("alpha-arch") / "b.md").write_text(ours, encoding="utf-8")
        (self._theirs("applied") / "b.md").write_text(theirs, encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.DELIVERED])

    def test_a_brief_in_their_applied_dir_counts_as_delivered(self):
        """Three of the four false flags were in the recipient's `applied/`. A scan that
        looked only in `pending/` repeats the defect one directory over."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("e1"), encoding="utf-8")
        (self._theirs("applied") / "b.md").write_text(self._brief("e1"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.DELIVERED])

    def test_genuinely_absent_is_UNDELIVERED(self):
        """The one real case out of five must still be caught — the fix must not simply
        make the audit quieter."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("nowhere"), encoding="utf-8")
        self._theirs()
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.UNDELIVERED])

    def test_an_unrostered_recipient_is_UNKNOWN_never_undelivered(self):
        (self._fed_pending("stranger-arch") / "b.md").write_text(
            self._brief("e1"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.UNKNOWN])
        self.assertIn("UNSURVEYED", out[0].where)

    def test_an_unlocatable_recipient_is_UNKNOWN_never_undelivered(self):
        """We cannot see their disk, so we know nothing about their mailbox. Calling that
        'never sent' is the confident-wrong-answer this item exists for."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("e1"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), {})
        self.assertEqual([v.state for v in out], [deliver_mod.UNKNOWN])
        self.assertIn("not locatable", out[0].where)

    def test_a_tracked_mailbox_is_still_READ_by_the_audit(self):
        """`tracked` is a rule about writing. Refusing to READ a tracked mailbox would make
        the members whose mailboxes are tracked permanently unauditable — and they are
        the population most likely to be holding undelivered mail."""
        theirs = self.tmp / "beta" / "proposed-edits" / "beta-arch" / "pending"
        theirs.mkdir(parents=True)
        (theirs / "b.md").write_text(self._brief("e9"), encoding="utf-8")
        (self._fed_pending("beta-arch") / "b.md").write_text(
            self._brief("e9"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), {"beta": self.tmp / "beta"})
        self.assertEqual([v.state for v in out], [deliver_mod.DELIVERED])

    def test_a_brief_with_no_edit_id_is_UNRESOLVABLE_not_merely_undetermined(self):
        """WI-0223. Still never a guess — but no longer filed beside blockers that a
        re-run can clear. Nothing about this brief will ever change from here, and an
        audit that says "could not tell" about it sends its reader back to a check that
        has no answer to give. Two real briefs sat in that bucket for over a month."""
        (self._fed_pending("alpha-arch") / "b.md").write_text("no header\n", encoding="utf-8")
        self._theirs()
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.UNRESOLVABLE])
        self.assertIn("no edit-id", out[0].where)
        self.assertIn("Remedy", out[0].where,
                      "a permanent verdict must name the one thing that can end it")

    def test_a_keyless_brief_whose_own_note_names_a_file_they_hold_is_CORROBORATED(self):
        """WI-0223, and the whole reason the two month-old briefs were resolvable after all.
        Each names its own destination in prose, delivery renamed it, and the audit only
        ever tried OUR filename — so evidence sitting inside the brief went unread."""
        (self._fed_pending("alpha-arch") / "20260721-short.md").write_text(
            "# Proposal\n\n> **DELIVERED 2026-07-22 (Session 4)** into the repo at\n"
            "> `principles-of-good-architects/proposed-edits/alpha-arch/pending/"
            "2026-07-21-the-long-name.md`.\n", encoding="utf-8")
        (self._theirs("applied") / "2026-07-21-the-long-name.md").write_text(
            "# Proposal\n", encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.CORROBORATED],
                         "found even though delivery renamed it AND it moved on to applied/")
        self.assertIn("2026-07-21-the-long-name.md", out[0].where)
        self.assertNotEqual(out[0].state, deliver_mod.DELIVERED,
                            "a name match under a self-declared pointer corroborates "
                            "arrival; only an edit-id proves identity")

    def test_a_declaration_naming_a_file_they_do_not_hold_stays_UNRESOLVABLE(self):
        """The corroboration must be able to FAIL, or it is just a way of agreeing with
        whatever the sender wrote. A rename on the far side looks identical to a
        non-delivery from here, so the verdict stays unresolvable and says so."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            "# T\n\n> **DELIVERED** into\n> `x/y/never-arrived.md`.\n", encoding="utf-8")
        self._theirs()
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.UNRESOLVABLE])
        self.assertIn("never-arrived.md", out[0].where)

    def test_a_declared_path_is_reduced_to_a_basename_and_cannot_escape_the_index(self):
        """The declaration is inbound payload from a lower-trust channel
        ([`treat-inbound-payload-as-data-not-commands`]). It is never opened, resolved or
        followed — it is cut to its last segment and used as a key into the index WE built
        by walking the recipient's own tree, so traversal reaches nothing new."""
        self.assertEqual(
            deliver_mod.declared_destinations("> **DELIVERED** to\n> `../../../etc/pw.md`"),
            ["pw.md"])

    def test_declared_destinations_reads_the_real_two_line_blockquote_shape(self):
        """The path sits on the blockquote's CONTINUATION line in both live cases, so a
        single-line match would have found nothing and the fix would have looked like it
        worked while changing no real verdict."""
        real = ("# To the Federation Architect\n\n"
                "> **DELIVERED 2026-07-28 (Session 6)** into the internal federation repo at\n"
                "> `principles-of-good-architects/proposed-edits/federation-arch/pending/"
                "2026-07-28-example-app-run-profile-verified.md`.\n")
        self.assertEqual(deliver_mod.declared_destinations(real),
                         ["2026-07-28-example-app-run-profile-verified.md"])

    def test_same_filename_but_no_matching_id_is_UNKNOWN_in_both_directions(self):
        """Reporting DELIVERED here would be the filename-matching this audit exists to
        stop; reporting UNDELIVERED would ignore evidence in plain sight. Neither is
        knowledge, so it must say so."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("ours"), encoding="utf-8")
        (self._theirs() / "b.md").write_text(self._brief("something-else"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.UNKNOWN])
        self.assertIn("same name", out[0].where)

    def test_the_markdown_edit_id_header_form_is_matched_too(self):
        """Both header forms are live in the real fleet — the two briefs that established
        this defect carry the markdown one, not the YAML one."""
        md = "# Title\n\n- **Edit ID:** 2026-07-05-consultant\n- **State:** Pending.\n"
        (self._fed_pending("alpha-arch") / "b.md").write_text(md, encoding="utf-8")
        (self._theirs() / "b.md").write_text(
            md.replace("Pending", "Applied"), encoding="utf-8")
        out, _inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in out], [deliver_mod.DELIVERED])

    def test_inbound_is_classified_too_not_just_counted(self):
        """Three states outbound and two inbound would rebuild the half-sweep defect in
        the other direction."""
        d = self.member / "proposed-edits" / "gamma-arch" / "pending"
        d.mkdir(parents=True)
        (d / "b.md").write_text(self._brief("e5"), encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.state for v in inb], [deliver_mod.UNKNOWN],
                         "gamma is rostered but not located here — that is not 'undelivered'")

    def test_status_line_names_undetermined_separately_from_undelivered(self):
        """Folding UNKNOWN into either count is the same over-confidence one layer up."""
        (self._fed_pending("stranger-arch") / "u.md").write_text(
            self._brief("e1"), encoding="utf-8")
        (self._fed_pending("alpha-arch") / "d.md").write_text(
            self._brief("e2"), encoding="utf-8")
        self._theirs()
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        b = deliver_mod._split(out + inb)
        self.assertEqual((len(b.lost), len(b.leftover), len(b.unknown)), (1, 0, 1))

    def test_a_delivered_leftover_alone_does_not_fail_the_audit(self):
        """An audit that keeps failing on mail that DID arrive trains its reader to ignore
        it — the crying-wolf failure `_retire_source` was written for."""
        (self._fed_pending("alpha-arch") / "b.md").write_text(
            self._brief("e7"), encoding="utf-8")
        (self._theirs() / "b.md").write_text(self._brief("e7"), encoding="utf-8")
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        b = deliver_mod._split(out + inb)
        self.assertEqual((len(b.lost), len(b.leftover), len(b.unknown)), (0, 1, 0))

    def test_unresolvable_fails_the_audit_but_corroborated_does_not(self):
        """WI-0223. The asymmetry is about whether anyone can ACT, not about confidence.
        An unresolvable brief is a real hole in the channel's evidence with a real remedy
        the exit code presses for — the sender declares an `edit-id`. A corroborated one
        is evidence the thing arrived, so our copy is a leftover to file, and failing on
        bookkeeping is how an audit trains its reader to ignore it."""
        (self._fed_pending("alpha-arch") / "keyless.md").write_text(
            "no header\n", encoding="utf-8")
        self._theirs()
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        b = deliver_mod._split(out + inb)
        self.assertEqual(len(b.unresolvable), 1)
        self.assertEqual(len(b.unknown), 0,
                         "a permanent verdict must not also be counted as a re-runnable one")

    def _stub_line(self, verdicts):
        """`audit_status_line()` reads the real machine, so the wording is pinned against a
        stubbed sweep. Restored in a finally — a test that leaves a module stubbed makes
        every later test in the process lie about what it exercised.

        EVERY live read it makes has to be stubbed here, and the list is not static.
        `channel.waiting` was added to `audit_status_line` by WI-0360 without being added
        here, so three silence tests kept passing only while the Runner channel happened
        to be empty — and went red across the whole trunk the first time a brief actually
        waited on it. A partially-stubbed seam list does not fail when it is written; it
        fails later, for a reason that looks like the change that tripped it."""
        real_audit, real_reach = deliver_mod.audit, deliver_mod.reachability
        real_stale = deliver_mod._registry_staleness
        real_waiting = deliver_mod.channel.waiting
        deliver_mod.audit = lambda *a, **k: (verdicts, [])
        deliver_mod.reachability = lambda *a, **k: []
        deliver_mod._registry_staleness = lambda *a, **k: ""
        deliver_mod.channel.waiting = lambda *a, **k: []
        try:
            return deliver_mod.audit_status_line()
        finally:
            deliver_mod.audit, deliver_mod.reachability = real_audit, real_reach
            deliver_mod._registry_staleness = real_stale
            deliver_mod.channel.waiting = real_waiting

    def test_the_status_line_names_every_state_separately(self):
        """WI-0129 gave `unreachable` its own clause because "N undelivered" and "M cannot
        be written to at all" are different facts. WI-0223 applies the same rule one level
        in: "could not tell this run" and "cannot be told from here at all" are different
        facts too, and only the first is worth coming back to."""
        def v(state):
            return deliver_mod.Verdict("alpha-arch", pathlib.Path("b.md"), state, "why")
        line = self._stub_line([v(deliver_mod.UNDELIVERED), v(deliver_mod.UNKNOWN),
                                v(deliver_mod.UNRESOLVABLE), v(deliver_mod.CORROBORATED),
                                v(deliver_mod.DELIVERED)])
        for phrase in ("1 undelivered", "1 undetermined", "1 unknowable by construction",
                       "1 corroborated by the sender's own note",
                       "1 delivered leftover(s) to file"):
            self.assertIn(phrase, line)

    def test_the_status_line_is_silent_when_every_brief_is_accounted_for(self):
        """Silence has to survive the two new states, or the line that was quiet on a
        healthy channel starts speaking every startup and stops being read."""
        self.assertEqual(self._stub_line([]), "")


class OutboxConventionTest(AuditBothDirectionsTest):
    """WI-0079. The sweep knew ONE directory shape — `proposed-edits/<id>/pending/` —
    because that is the shape the federation ships. One member keeps its outbound mail at
    `outbox/to-federation-arch/` instead, several briefs deep.

    A sweep that knows only the first shape does not report the second as unexamined; it
    reports the channel CLEAN, having never looked. That is the same
    certifies-without-looking failure the three-state rebuild above was for, one
    convention over — and it is why the reusable probe in the item's notes had to match
    both paths.

    The other half is that the answer is not a FILE COUNT. Of the six real briefs on that
    surface, four were already in our mailbox and two were pre-edit-id retained copies —
    zero stranded. Counting files would have reported a backlog of six."""

    # The live surface is addressed to `federation-arch`, so the fixture rosters the
    # federation as a readable recipient — otherwise every verdict is UNKNOWN for a
    # reason that has nothing to do with what is being pinned.
    FED_ROW = {"federation": {"architect_id": "federation-arch",
                              "mailbox": "proposed-edits/federation-arch/pending",
                              "tracked": False, "reachable": True}}

    def _boxes(self):
        return _mailboxes(**self.FED_ROW)

    def _repos(self):
        return dict(self.repos, federation=self.fed)

    def _outbox(self, to="federation-arch"):
        d = self.member / "outbox" / f"to-{to}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _ours(self, sub="pending"):
        d = deliver_mod.FED_PROPOSED / "federation-arch" / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_a_stranded_brief_in_an_outbox_is_found(self):
        self._ours()                       # a real, readable, empty mailbox
        (self._outbox() / "stuck.md").write_text(self._brief("outbox-1"), encoding="utf-8")
        _out, inb = deliver_mod.audit(self._boxes(), self._repos())
        self.assertEqual([(v.holder, v.architect_id, v.state) for v in inb],
                         [("alpha", "federation-arch", deliver_mod.UNDELIVERED)])

    def test_an_outbox_brief_the_recipient_holds_is_DELIVERED_not_a_backlog(self):
        """The live case: most of that member's outbox was already in our `accepted/`."""
        self._ours()                       # the mailbox itself, alongside its siblings
        (self._outbox() / "sent.md").write_text(self._brief("outbox-2"), encoding="utf-8")
        (self._ours("accepted") / "sent.md").write_text(
            self._brief("outbox-2"), encoding="utf-8")
        _out, inb = deliver_mod.audit(self._boxes(), self._repos())
        self.assertEqual([v.state for v in inb], [deliver_mod.DELIVERED])

    def test_an_outbox_brief_with_no_edit_id_is_UNRESOLVABLE_not_undelivered(self):
        """Two of the six predate the edit-id convention and are explicitly labelled
        retained sender-side copies. There is nothing to match on, and saying so is still
        the honest answer — never by relaxing the match to filenames, which is the
        matching this audit exists to stop.

        WI-0223 sharpened the grade, not the match: a pre-convention brief carrying no
        self-declaration is unknowable from here, permanently, and it is now filed as that
        instead of beside blockers a re-run could clear."""
        self._ours()
        (self._outbox() / "old.md").write_text("# A pre-convention brief\n", encoding="utf-8")
        _out, inb = deliver_mod.audit(self._boxes(), self._repos())
        self.assertEqual([v.state for v in inb], [deliver_mod.UNRESOLVABLE])

    def test_the_addressee_comes_from_the_directory_name(self):
        """`to-<architect-id>` is the routing information, so a brief addressed to a
        THIRD party sitting in a member's outbox is classified against that third party —
        not against us. The live sweep found exactly one such pair."""
        (self._outbox("alpha-arch") / "theirs.md").write_text(
            self._brief("third-party"), encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual([v.architect_id for v in inb], ["alpha-arch"])

    def test_a_repo_with_no_outbox_is_not_an_error(self):
        out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual((out, inb), ([], []))

    def test_a_stray_file_beside_the_outbox_dirs_is_ignored(self):
        (self.member / "outbox").mkdir(parents=True, exist_ok=True)
        (self.member / "outbox" / "to-notes.txt").write_text("x", encoding="utf-8")
        (self.member / "outbox" / "README.md").write_text("x", encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual(inb, [])

    def test_our_own_tree_reached_as_a_member_is_not_double_reported(self):
        """The federation joined the locator map on 2026-08-13 so briefs could be
        delivered TO the hub — which silently enrolled our own tree in the INBOUND sweep
        too, and every outbound brief began reporting twice: once as outbound, once as
        'held in federation's tree'. Found by reading the live audit while closing this
        item. A duplicated row inflates a count that gets relayed onward as fact."""
        (self._fed_pending("alpha-arch") / "one.md").write_text(
            self._brief("dup-1"), encoding="utf-8")
        repos = dict(self.repos, federation=self.fed)
        out, inb = deliver_mod.audit(self._boxes(), repos)
        self.assertEqual(len(out) + len(inb), 1,
                         "one brief must produce exactly one verdict, not one per sweep")
        self.assertEqual([v.state for v in out], [deliver_mod.UNDELIVERED])
        self.assertEqual(inb, [])

    def test_both_conventions_are_swept_in_one_pass(self):
        """The point of the item: neither surface may be traded for the other."""
        d = self.member / "proposed-edits" / "federation-arch" / "pending"
        d.mkdir(parents=True, exist_ok=True)
        (d / "old-shape.md").write_text(self._brief("shape-a"), encoding="utf-8")
        (self._outbox() / "new-shape.md").write_text(self._brief("shape-b"), encoding="utf-8")
        _out, inb = deliver_mod.audit(_mailboxes(), self.repos)
        self.assertEqual(sorted(p.path.name for p in inb), ["new-shape.md", "old-shape.md"])


class ReachabilitySweepTest(ResolveTest):
    """WI-0129 — the audit could not see a recipient nobody could write to.

    `audit()` enumerates BRIEFS. An unreachable recipient never acquires one, because the
    send path refuses before writing and leaves nothing behind to sweep — so you could not
    learn a recipient was unreachable until a brief was stuck on them, and delivery
    declined to create that brief. Reported by a member Architect after the HUB itself became
    unwritable (the federation's repo sits outside every walk root) while `--audit`
    reported "1 undelivered" and read as otherwise healthy.

    The sweep is driven by the send path's own `resolve()` per roster row, so the reasons
    are the same five sentences delivery already refuses with rather than a second set
    free to drift.
    """

    def _sweep(self, mailboxes=None, repos=None):
        return {u.architect_id: u.reason for u in deliver_mod.reachability(
            _mailboxes() if mailboxes is None else mailboxes,
            self.repos if repos is None else repos)}

    def test_a_tracked_mailbox_is_reported_unreachable(self):
        """beta's mailbox is tracked, so a brief dropped there lands in their history
        under their authorship. Delivery refuses — and until now nothing said so."""
        got = self._sweep()
        self.assertIn("beta-arch", got)
        self.assertIn("TRACKED", got["beta-arch"])

    def test_an_unlocatable_member_is_reported_with_the_location_reason(self):
        """The hub's own case: a real, rostered member whose repo cannot be found from
        this machine. A LOCATION problem, which the reason has to say — it is fixed in
        repo-paths.local, not in the registry."""
        got = self._sweep(repos={"alpha": self.alpha, "beta": self.tmp / "beta"})
        self.assertIn("gamma-arch", got)
        self.assertIn("not locatable", got["gamma-arch"])

    def test_a_reachable_member_is_absent_from_the_sweep(self):
        """It reports what is BROKEN — a clean member producing a row would train the
        reader to skim past the ones that matter."""
        self.assertNotIn("alpha-arch", self._sweep())

    def test_a_mailbox_recorded_but_absent_on_disk_is_reported(self):
        mb = _mailboxes()
        mb["alpha"]["mailbox"] = "proposed-edits/alpha-arch/nowhere"
        self.assertIn("does not exist on disk", self._sweep(mailboxes=mb)["alpha-arch"])

    def test_a_row_with_no_architect_id_cannot_be_addressed_and_says_so(self):
        mb = _mailboxes(delta={"mailbox": "inbox/pending", "tracked": False})
        self.assertIn("no architect_id", " ".join(self._sweep(mailboxes=mb).values()))

    def test_the_status_line_keeps_the_two_numbers_apart(self):
        """The whole ask: "N undelivered" and "M unreachable" are different facts, and
        folding them is how the first became an understatement."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[
                 deliver_mod.Unreachable("beta-arch", "beta", "TRACKED in git")]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]):
            line = deliver_mod.audit_status_line()
        self.assertIn("1 recipient(s) unreachable", line)
        self.assertNotIn("undelivered", line,
                         "nothing is undelivered here; the count must not borrow that word")

    def test_the_status_line_is_silent_when_everything_is_reachable(self):
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]):
            self.assertEqual(deliver_mod.audit_status_line(), "")

    def test_a_failing_sweep_is_reported_not_swallowed(self):
        """`--audit` must never print a clean verdict on the strength of a probe that
        crashed ([`declare-what-a-check-assumes`])."""
        out = io.StringIO()
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability",
                               side_effect=RuntimeError("boom")), \
             contextlib.redirect_stdout(out):
            deliver_mod._print_audit()
        self.assertIn("NOT CHECKED", out.getvalue())

    def test_an_unreachable_recipient_makes_the_audit_exit_nonzero(self):
        """A channel nobody can write to is a channel failure, not bookkeeping."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[
                 deliver_mod.Unreachable("beta-arch", "beta", "TRACKED in git")]), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(deliver_mod._print_audit(), 1)


class RegistryProbeTest(unittest.TestCase):
    """WI-0077 / ADR-0088 D2+D5. The registry decides delivery, and it was hand-maintained:
    every row was probed once, by hand, and stamped the same date. Days later some were
    wrong — one member had gitignored its mailbox and another had replaced its symlink
    loop — so the federation held a to-do list for members that had already fixed
    themselves. Reachability is therefore probed BY USE: a self-referential symlink
    satisfied every presence check and failed every write."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _repo(self, name, ignore=None):
        repo = self.tmp / name
        (repo / "proposed-edits" / f"{name}-arch" / "pending").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        if ignore:
            (repo / ".gitignore").write_text(ignore, encoding="utf-8")
        return repo

    def _row(self, name, **over):
        row = {"architect_id": f"{name}-arch",
               "mailbox": f"proposed-edits/{name}-arch/pending",
               "tracked": False, "reachable": True, "verified": "2026-01-01"}
        row.update(over)
        return row

    def test_an_ignored_mailbox_probes_untracked(self):
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        p = deliver_mod.probe_member(repo, self._row("alpha"))
        self.assertEqual((p["reachable"], p["tracked"]), (True, False))

    def test_a_non_ignored_mailbox_probes_tracked(self):
        """The harm case: their session-end sweep commits our brief under their name."""
        repo = self._repo("alpha")
        p = deliver_mod.probe_member(repo, self._row("alpha"))
        self.assertEqual((p["reachable"], p["tracked"]), (True, True))

    def test_the_probe_leaves_nothing_behind(self):
        """It writes into somebody else's repo to test it. Anything left is litter we put
        in a tree we do not own."""
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        deliver_mod.probe_member(repo, self._row("alpha"))
        box = repo / "proposed-edits" / "alpha-arch" / "pending"
        self.assertEqual(list(box.iterdir()), [])

    def test_a_missing_mailbox_is_unreachable(self):
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        row = self._row("alpha", mailbox="proposed-edits/alpha-arch/nonexistent")
        p = deliver_mod.probe_member(repo, row)
        self.assertFalse(p["reachable"])
        self.assertTrue(p["detail"], "an unreachable row must say why")

    @unittest.skipIf(os.geteuid() == 0, "root ignores the mode bits this test relies on")
    def test_a_present_but_unwritable_mailbox_is_unreachable(self):
        """THE discriminating case, and the one that motivates probing by use: one member's
        mailbox was a self-referential symlink that satisfied `is_dir()` and failed every
        write with ELOOP. A presence check answers True here and is wrong; only an
        attempted write is evidence. Asserted alongside `is_dir()` so the test would fail
        against a presence-based implementation rather than agreeing with it."""
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        box = repo / "proposed-edits" / "alpha-arch" / "pending"
        box.chmod(0o555)
        self.addCleanup(box.chmod, 0o755)
        self.assertTrue(box.is_dir(), "premise: a presence check would say this is fine")
        p = deliver_mod.probe_member(repo, self._row("alpha"))
        self.assertFalse(p["reachable"], "probed by use, so it is not fine")
        self.assertTrue(p["detail"], "an unreachable row must say why")

    def test_a_symlinked_mailbox_is_answered_via_its_real_repo(self):
        """git refuses a path 'beyond a symbolic link'. A lane's own `proposed-edits` IS
        such a symlink into the main checkout, so before this the federation's own row —
        the one mailbox we can always see — was the only one the probe could not read."""
        real = self._repo("alpha", ignore="proposed-edits/\n")
        lane = self.tmp / "lane"
        lane.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(lane)], check=True)
        (lane / "proposed-edits").symlink_to(real / "proposed-edits")
        p = deliver_mod.probe_member(lane, self._row("alpha"))
        self.assertEqual(p["tracked"], False,
                         "the answer lives in the repo that owns the resolved path")

    def test_an_undeterminable_ignore_status_is_None_not_False(self):
        """`?` must not read as `conforming`. Folding 'could not tell' into 'fine' is the
        exemption-defaults-to-pass shape that certifies a gap instead of finding it."""
        v, detail = deliver_mod._check_ignore(self.tmp / "not-a-repo", "x/pending")
        self.assertIsNone(v)
        self.assertTrue(detail)

    def test_drift_is_reported_against_the_recorded_row(self):
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        boxes = {"alpha": self._row("alpha", tracked=True)}      # record says tracked…
        results = deliver_mod.probe_registry(boxes, {"alpha": repo})   # …probe says not
        _sid, _row, _p, drift = results[0]
        self.assertTrue(any("tracked" in d for d in drift), drift)

    def test_an_unlocatable_member_is_not_probed_and_not_certified(self):
        results = deliver_mod.probe_registry({"ghost": self._row("ghost")}, {})
        _sid, _row, p, drift = results[0]
        self.assertIsNone(p["reachable"])
        self.assertIsNone(p["tracked"])
        self.assertEqual(drift, [], "we learned nothing, so we can claim no drift")


class ProbeWritebackTest(RegistryProbeTest):
    def setUp(self):
        super().setUp()
        self.reg = self.tmp / "mailboxes.json"
        self._save = deliver_mod.MAILBOXES
        deliver_mod.MAILBOXES = self.reg
        self.addCleanup(setattr, deliver_mod, "MAILBOXES", self._save)

    def _write_registry(self, members):
        self.reg.write_text(json.dumps({"schema": 1, "members": members}), encoding="utf-8")

    def test_probed_truth_replaces_the_recorded_value_and_restamps(self):
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        row = self._row("alpha", tracked=True, note="hand-written reasoning")
        self._write_registry({"alpha": row})
        results = deliver_mod.probe_registry({"alpha": row}, {"alpha": repo})
        changes = deliver_mod.write_probe_results(results)
        after = json.loads(self.reg.read_text())["members"]["alpha"]
        self.assertFalse(after["tracked"])
        self.assertNotEqual(after["verified"], "2026-01-01")
        self.assertTrue(any("tracked" in c for c in changes), changes)

    def test_a_hand_written_note_is_never_rewritten(self):
        """Notes carry reasoning a probe cannot regenerate — a member's path can be non-standard
        ON PURPOSE (ADR-0088 D2b), and losing that turns a decision into an anomaly."""
        repo = self._repo("alpha", ignore="proposed-edits/\n")
        row = self._row("alpha", tracked=True, note="DELIBERATELY NOT MOVED")
        self._write_registry({"alpha": row})
        deliver_mod.write_probe_results(
            deliver_mod.probe_registry({"alpha": row}, {"alpha": repo}))
        self.assertEqual(json.loads(self.reg.read_text())["members"]["alpha"]["note"],
                         "DELIBERATELY NOT MOVED")

    def test_an_unprobed_row_keeps_its_old_verified_date(self):
        """Re-stamping a row we could not check would launder an unknown into a fresh
        certification — the exact move the audit half of this file exists to stop."""
        row = self._row("ghost")
        self._write_registry({"ghost": row})
        deliver_mod.write_probe_results(deliver_mod.probe_registry({"ghost": row}, {}))
        self.assertEqual(json.loads(self.reg.read_text())["members"]["ghost"]["verified"],
                         "2026-01-01")


class RegistryStalenessTest(unittest.TestCase):
    """Re-probing on someone remembering is the chore that silently lapses — the registry
    said in its own header that stale rows should be re-probed, nothing checked, and two
    of eleven were wrong three days later."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = self.tmp / "mailboxes.json"
        self._save = deliver_mod.MAILBOXES
        deliver_mod.MAILBOXES = self.reg
        self.addCleanup(setattr, deliver_mod, "MAILBOXES", self._save)

    def _reg(self, verified):
        self.reg.write_text(json.dumps({"members": {
            "a": {"architect_id": "a-arch", "mailbox": "m", "verified": verified}}}),
            encoding="utf-8")

    def test_a_fresh_registry_is_silent(self):
        self._reg(deliver_mod._today())
        self.assertEqual(deliver_mod._registry_staleness(), "")

    def test_a_stale_registry_speaks_and_names_the_verb(self):
        """Confirmed to actually FIRE, not merely to exist — a threshold nobody has seen
        trip is a claim, not a guard."""
        self._reg("2020-01-01")
        msg = deliver_mod._registry_staleness()
        self.assertIn("stale", msg)
        self.assertIn("--probe", msg, "a warning must name the verb that clears it")

    def test_the_oldest_row_sets_the_age_not_the_newest(self):
        """One freshly-probed row must not certify the ten beside it."""
        self.reg.write_text(json.dumps({"members": {
            "fresh": {"architect_id": "f-arch", "mailbox": "m",
                      "verified": deliver_mod._today()},
            "old": {"architect_id": "o-arch", "mailbox": "m", "verified": "2020-01-01"}}}),
            encoding="utf-8")
        self.assertIn("stale", deliver_mod._registry_staleness())

    def test_a_never_probed_registry_says_so_rather_than_passing(self):
        self.reg.write_text(json.dumps({"members": {
            "a": {"architect_id": "a-arch", "mailbox": "m"}}}), encoding="utf-8")
        self.assertIn("never been probed", deliver_mod._registry_staleness())

    def test_an_unreadable_registry_is_reported_not_silently_swallowed(self):
        """It must not raise (a startup line cannot brick startup) but it must not fall
        silent either: `load_mailboxes()` returns {} for an absent registry AND for a
        corrupt one, so the first cut of this reported a corrupt file as 'never probed'.
        An audit whose registry cannot be parsed resolves nothing while looking fine —
        this file's own defect class, committed inside the fix for it."""
        self.reg.write_text("{ not json", encoding="utf-8")
        msg = deliver_mod._registry_staleness()
        self.assertIn("UNREADABLE", msg)
        self.assertNotIn("never been probed", msg,
                         "unreadable and never-probed are different facts")


class EditIdExtractionTest(unittest.TestCase):
    def test_yaml_frontmatter_form(self):
        self.assertEqual(
            deliver_mod.brief_edit_id("---\nedit-id: 2026-08-07-thing\nto: x\n---\n"),
            "2026-08-07-thing")

    def test_markdown_bullet_form(self):
        self.assertEqual(
            deliver_mod.brief_edit_id("# T\n\n- **Edit ID:** 2026-08-07-thing\n"),
            "2026-08-07-thing")

    def test_a_trailing_period_is_not_part_of_the_id(self):
        self.assertEqual(
            deliver_mod.brief_edit_id("- **Edit ID:** 2026-08-07-thing.\n"),
            "2026-08-07-thing")

    def test_absent_is_none_not_a_fallback(self):
        self.assertIsNone(deliver_mod.brief_edit_id("# Just a title\n"))


if __name__ == "__main__":
    unittest.main()


class ChangelogReceiptTest(unittest.TestCase):
    """WI-0179 / brief 2026-08-24. The mailbox is gitignored data on one machine; the
    role-doc CHANGELOG is tracked history. When the two disagree about whether a brief was
    already applied, the durable one has to be able to speak."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.alpha = self.tmp / "alpha"
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        (self.alpha / "session.config.json").write_text(
            '{"architect_id": "alpha-arch", "role_doc": "alpha-arch.md"}', encoding="utf-8")
        self.repos = {"alpha": self.alpha}
        self.src = self.tmp / "2026-08-24-a-settled-thing.md"
        self.src.write_text(
            _ROUTABLE_BRIEF.replace("edit-id: fixture-brief",
                                    "edit-id: 2026-08-24-a-settled-thing"), encoding="utf-8")

    def _roledoc(self, body):
        (self.alpha / "alpha-arch.md").write_text(body, encoding="utf-8")

    def test_an_edit_id_in_the_changelog_refuses_redelivery(self):
        """The whole point: the mailbox is EMPTY — a rebuilt data root — and the delivery
        is still refused, because tracked history remembers the decision."""
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n"
                      "- v1.2.0 — adopted the thing (2026-08-24-a-settled-thing)\n")
        with self.assertRaises(ValueError) as e:
            deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)
        msg = str(e.exception)
        self.assertIn("CHANGELOG", msg)
        self.assertIn("2026-08-24-a-settled-thing", msg)
        self.assertIn("--replace", msg, "name the deliberate override")

    def test_an_unrelated_changelog_does_not_block_a_new_brief(self):
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n"
                      "- v1.1.0 — something else (2026-01-01-unrelated-brief)\n")
        deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)

    def test_a_mention_outside_the_changelog_is_not_a_receipt(self):
        """An edit-id quoted in prose is someone TALKING about a brief, not a record that
        it was applied. Treating the two alike would refuse deliveries never made."""
        self._roledoc("# Alpha\n\nWe should think about 2026-08-24-a-settled-thing.\n\n"
                      "## CHANGELOG\n\n- v1.0.0 — initial\n")
        deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)

    def test_a_missing_role_doc_is_cannot_tell_and_never_blocks(self):
        """The index WIDENS the guard and must never narrow it: unreadable is reported,
        not treated as either answer."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)
        self.assertIn("could not cross-check", err.getvalue())

    def _config(self, **extra):
        cfg = {"architect_id": "alpha-arch", "role_doc": "alpha-arch.md"}
        cfg.update(extra)
        (self.alpha / "session.config.json").write_text(json.dumps(cfg), encoding="utf-8")

    # ---- WI-0282: could-not-check is its own answer ------------------------------------

    def test_a_role_doc_with_no_changelog_heading_is_cannot_check_not_empty(self):
        """THE WI-0282 DEFECT, inverted. This assertion previously read `(set(), None)` —
        *checked and found nothing* — for a doc this function never managed to check. The
        consumer only ever branches on `why`, so the empty set printed no note and an
        unread index looked exactly like a clean one; that is how one member's applied
        history read as empty while its briefs sat in `## 10. Changelog`. A miss grants
        nothing, so the cost of the wrong answer is a silently re-enabled re-delivery."""
        self.assertEqual(deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch"),
                         (None, "no role doc located in the recipient's repo"))
        self._roledoc("# Alpha\n\nNo changelog section at all.\n")
        ids, why = deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch")
        self.assertIsNone(ids, "a role doc with no CHANGELOG heading was never CHECKED")
        self.assertIn("no CHANGELOG heading", why)

    def test_a_heading_that_was_found_and_parsed_empty_is_the_empty_answer(self):
        """The other half of the same distinction — and the reason the fix is not simply
        'return None more often'. A changelog that genuinely records no edit-ids WAS
        checked, and must keep saying so."""
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n- v1.0.0 — initial, no edit-id\n")
        self.assertEqual(deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch"),
                         (set(), None))

    def test_a_numbered_changelog_heading_is_matched(self):
        """A member may head its section `## 10. Changelog`. A member is free to number
        its sections, and the guard must not depend on it not doing so — the old two
        `rfind` patterns matched neither `## changelog` nor `changelog` here."""
        for heading in ("## 10. Changelog", "### 4) CHANGELOG", "# Changelog",
                        "## CHANGELOG"):
            with self.subTest(heading=heading):
                self._roledoc(f"# Alpha\n\n{heading}\n\n"
                              f"- v1.2.0 — adopted (2026-08-24-a-settled-thing)\n")
                ids, why = deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch")
                self.assertIsNone(why)
                self.assertEqual(ids, {"2026-08-24-a-settled-thing"})

    def test_a_numbered_heading_refuses_the_redelivery_end_to_end(self):
        """The live impact, at the consumer: before the widened match this delivery went
        through with an empty mailbox and a clean-looking cross-check."""
        self._roledoc("# Alpha\n\n## 10. Changelog\n\n"
                      "- v1.2.0 — adopted the thing (2026-08-24-a-settled-thing)\n")
        with self.assertRaises(ValueError) as e:
            deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)
        self.assertIn("2026-08-24-a-settled-thing", str(e.exception))

    # ---- ADR-0123 / WI-0284: the changelog may live in a tracked sibling ---------------

    def test_a_declared_changelog_doc_is_read_and_yields_a_non_empty_set(self):
        """WI-0284's acceptance clause. The failure mode being guarded is a SILENT empty
        set, not an error, so the assertion is on the receipts actually coming back."""
        self._config(changelog_doc="alpha-arch-CHANGELOG.md")
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n"
                      "See [alpha-arch-CHANGELOG.md](alpha-arch-CHANGELOG.md).\n")
        (self.alpha / "alpha-arch-CHANGELOG.md").write_text(
            "# Alpha — CHANGELOG\n\nHistory for the role doc.\n\n"
            "- **1.2.0** (2026-08-24) — adopted the thing "
            "(`2026-08-24-a-settled-thing`)\n", encoding="utf-8")
        ids, why = deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch")
        self.assertIsNone(why)
        self.assertEqual(ids, {"2026-08-24-a-settled-thing"})

    def test_a_receipt_only_in_the_history_file_still_refuses_redelivery(self):
        """The regression the split exists to avoid: an already-applied brief must not
        become re-deliverable just because its receipt moved to a sibling file."""
        self._config(changelog_doc="alpha-arch-CHANGELOG.md")
        self._roledoc("# Alpha\n\n## CHANGELOG — see alpha-arch-CHANGELOG.md\n")
        (self.alpha / "alpha-arch-CHANGELOG.md").write_text(
            "# Alpha — CHANGELOG\n\n- **1.2.0** (2026-08-24) — adopted "
            "(2026-08-24-a-settled-thing)\n", encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)
        self.assertIn("2026-08-24-a-settled-thing", str(e.exception))

    def test_a_declared_but_missing_changelog_doc_never_falls_back_to_the_role_doc(self):
        """The fallback that looks harmless and is the whole bug. A split member's role
        doc still carries a `## CHANGELOG` pointer, so falling back would find a heading,
        parse zero receipts, and report a CLEAN cross-check of a file never read."""
        self._config(changelog_doc="alpha-arch-CHANGELOG.md")
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n"
                      "- v1.2.0 — adopted (2026-08-24-a-settled-thing)\n")
        ids, why = deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch")
        self.assertIsNone(ids, "declared-and-missing is COULD NOT CHECK, never a fallback")
        self.assertIn("changelog_doc", why)

    def test_prose_above_the_first_entry_of_a_history_file_is_not_a_receipt(self):
        """Section-scoping did this job inside a role doc; a whole-file history has no
        section, so the first ENTRY is the boundary instead."""
        self._config(changelog_doc="alpha-arch-CHANGELOG.md")
        self._roledoc("# Alpha\n")
        (self.alpha / "alpha-arch-CHANGELOG.md").write_text(
            "# Alpha — CHANGELOG\n\nWe should think about "
            "2026-08-24-a-settled-thing one day.\n\n"
            "- **1.0.0** (2026-01-01) — initial\n", encoding="utf-8")
        deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)

    def test_a_continuation_line_inside_an_entry_still_carries_its_receipt(self):
        """Entry-scoping must never become entry-LINE matching: a wrapped entry's receipt
        is still a receipt, and dropping it would narrow the guard into a re-delivery."""
        self._config(changelog_doc="alpha-arch-CHANGELOG.md")
        self._roledoc("# Alpha\n")
        (self.alpha / "alpha-arch-CHANGELOG.md").write_text(
            "# Alpha — CHANGELOG\n\n- **1.2.0** (2026-08-24) — adopted the thing,\n"
            "  per brief 2026-08-24-a-settled-thing, at the operator's direction.\n",
            encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            deliver_mod._check_collision("alpha-arch", self.src, _mailboxes(), self.repos)
        self.assertIn("2026-08-24-a-settled-thing", str(e.exception))

    def test_an_undeclared_changelog_doc_leaves_inline_members_exactly_as_they_were(self):
        """Most members carry an inline CHANGELOG and none of them is being edited. The
        split is opt-in by declaration, and silence must keep meaning inline."""
        self._roledoc("# Alpha\n\n## CHANGELOG\n\n"
                      "- **1.2.0** (2026-08-24) — adopted (2026-08-24-a-settled-thing)\n")
        self.assertEqual(deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch"),
                         ({"2026-08-24-a-settled-thing"}, None))

    def test_a_layout_block_declaration_reaches_the_delivery_guard(self):
        """WI-0029: resolution goes through the shipped harness's one door, so a member
        that declares in the `layout` block is honoured here without a second copy of the
        precedence rules living in `curate/deliver.py`."""
        self._config(layout={"changelog_doc": "history/log.md"})
        self._roledoc("# Alpha\n")
        (self.alpha / "history").mkdir()
        (self.alpha / "history" / "log.md").write_text(
            "- **1.2.0** (2026-08-24) — adopted (2026-08-24-a-settled-thing)\n",
            encoding="utf-8")
        self.assertEqual(deliver_mod.changelog_edit_ids(self.alpha, "alpha-arch"),
                         ({"2026-08-24-a-settled-thing"}, None))


class DeclaredResidencyTest(unittest.TestCase):
    """WI-0205 / ADR-0110. a machine may deliberately carry only a subset of the fleet, and the
    reason some members are absent from it was written
    down in a PROSE COMMENT in `reconcile-roots.local` — which no check can read. So the
    sweep filed "deliberately elsewhere" with "broken" and prescribed *map it in
    repo-paths.local*, advice that could only be followed by inventing a path to a repo
    that is not on this disk.

    The exemption is therefore a DECLARATION, and every way it could quietly become a
    default-to-pass is pinned below: an exemption that grants itself does not merely fail
    to find a gap, it certifies one."""

    def test_a_row_declaring_another_machine_returns_that_machines_label(self):
        self.assertEqual(
            deliver_mod.declared_elsewhere({"resides": "Runner"}, "DevBox"), "Runner")

    def test_a_machine_that_cannot_name_itself_may_not_excuse_anyone(self):
        """The withhold rule. Not knowing where you are standing is a reason to WITHHOLD
        the by-design verdict, never to grant it — if `this_machine()` degrades to "" on a
        host it cannot map, a defaults-to-pass exemption would excuse the whole roster on
        exactly the machine we understand least."""
        self.assertEqual(deliver_mod.declared_elsewhere({"resides": "Runner"}, ""), "",
                         "an unnamed machine must not be able to excuse an absent member")
        self.assertEqual(deliver_mod.declared_elsewhere({"resides": "Runner"}, None), "")

    def test_a_row_declaring_THIS_machine_is_a_real_gap_not_a_design(self):
        """It claims to live here and it is not here. That is the ordinary unreachable
        member the sweep exists to report, and the declaration must not launder it."""
        self.assertEqual(
            deliver_mod.declared_elsewhere({"resides": "DevBox"}, "DevBox"), "",
            "claims to live here but cannot be found here — a defect, not a residency")

    def test_a_row_with_no_resides_is_UNDECLARED_not_lives_everywhere(self):
        """ADR-0095 D2's empty-value rule. Absence of the field is the symptom a genuinely
        missing repo shares with a deliberately-elsewhere one, so it must never be the
        thing that decides between them."""
        self.assertEqual(deliver_mod.declared_elsewhere({}, "DevBox"), "")
        self.assertEqual(deliver_mod.declared_elsewhere({"resides": "   "}, "DevBox"), "",
                         "a blank declaration declares nothing")
        self.assertEqual(deliver_mod.declared_elsewhere(None, "DevBox"), "")


class ReachabilityResidencyTest(unittest.TestCase):
    """The sweep's own half of WI-0205: DEFECT vs EXPECTED, not reachable vs not.

    Folding the two made one member's committed symlink loop — the one real channel
    failure — the fourth line of four identical-looking rows, which is how a report trains
    its reader to skim it. The remedy printed beside the three innocent ones was *map it in
    repo-paths.local*, which is unfollowable for a repo that is not on this disk."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.alpha = self.tmp / "alpha"
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.gamma = self.tmp / "gamma"
        (self.gamma / "inbox" / "pending").mkdir(parents=True)
        # Pinned, so the ruling does not depend on which host the suite runs on.
        self._save = deliver_mod.this_machine
        deliver_mod.this_machine = lambda: "DevBox"
        self.addCleanup(setattr, deliver_mod, "this_machine", self._save)

    def _sweep(self, mailboxes, repos):
        return {u.architect_id: u for u in deliver_mod.reachability(mailboxes, repos)}

    def test_a_declared_elsewhere_member_is_reported_BY_DESIGN_without_repair_advice(self):
        """The advice is the harm. `resolve()` can only say "not locatable", so it
        prescribed a repo-paths.local mapping — and following it would mean fabricating a
        path to a repo that lives on another machine."""
        mb = _mailboxes()
        mb["gamma"]["resides"] = "Runner"
        u = self._sweep(mb, {"alpha": self.alpha})["gamma-arch"]
        self.assertTrue(u.by_design, "the roster declares this one elsewhere on purpose")
        self.assertIn("Runner", u.reason, "name the machine it does live on")
        self.assertNotIn("repo-paths.local", u.reason,
                         "there is nothing here to map — unfollowable advice is worse "
                         "than none, because it is actionable")

    def test_an_undeclared_missing_member_stays_a_gap_and_keeps_the_location_advice(self):
        """The fix must not quieten the sweep. A member missing with NO declaration is the
        ordinary unreachable it always was, repair advice included."""
        u = self._sweep(_mailboxes(), {"alpha": self.alpha})["gamma-arch"]
        self.assertFalse(u.by_design)
        self.assertIn("not locatable", u.reason)
        self.assertIn("repo-paths.local", u.reason)

    def test_a_member_that_is_here_is_judged_by_the_send_path_despite_its_declaration(self):
        """Both conditions are required. A row declaring `resides: Runner` whose repo is
        nonetheless sitting on this disk falls through to `resolve()` — otherwise the field
        becomes a way to opt out of being checked at all."""
        mb = _mailboxes()
        mb["gamma"]["resides"] = "Runner"
        mb["gamma"]["mailbox"] = "inbox/nowhere"
        u = self._sweep(mb, {"alpha": self.alpha, "gamma": self.gamma})["gamma-arch"]
        self.assertFalse(u.by_design, "it is here, so its broken mailbox is a real defect")
        self.assertIn("does not exist on disk", u.reason)

    def test_a_declared_elsewhere_member_that_is_reachable_is_absent_from_the_sweep(self):
        mb = _mailboxes()
        mb["gamma"]["resides"] = "Runner"
        self.assertNotIn("gamma-arch",
                         self._sweep(mb, {"alpha": self.alpha, "gamma": self.gamma}))


class EvidenceScopeTest(unittest.TestCase):
    """WI-0205 / ADR-0110 — the audit stated its verdicts globally while reading ONE
    machine.

    ADR-0088 D1 gitignores every member's mailbox deliberately, so a mailbox's contents
    exist on exactly one machine and never travel. one member delivered a brief to another from
    one machine; from a second machine the audit could read the sender's tracked copy and
    could never read the recipient's mailbox, so it read the copy, read an empty mailbox, and
    reported UNDELIVERED. It had been delivered, and the obvious next action would have
    duplicated a brief the recipient already held.

    The fix names the evidence rather than weakening the verdict, so the converse is pinned
    beside it: where our copy is machine-local too, this machine is the only possible
    sender and UNDELIVERED still stands."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.alpha = self.tmp / "alpha"          # gitignored mailbox — MACHINE-LOCAL
        (self.alpha / "proposed-edits" / "alpha-arch" / "pending").mkdir(parents=True)
        self.beta = self.tmp / "beta"            # tracked mailbox — visible from anywhere
        (self.beta / "proposed-edits" / "beta-arch" / "pending").mkdir(parents=True)
        self.repos = {"alpha": self.alpha, "beta": self.beta}
        self.brief = self.tmp / "2026-08-13-a-brief.md"
        self.brief.write_text("---\nedit-id: 2026-08-13-a-brief\n---\n\n# A brief\n",
                              encoding="utf-8")

    def _classify(self, aid="alpha-arch", travels=None, brief=None):
        return deliver_mod.classify(aid, brief or self.brief, _mailboxes(), self.repos,
                                    travels=travels)

    def test_a_travelling_brief_absent_from_a_machine_local_mailbox_is_OFF_MACHINE(self):
        """The live wrong answer. Our copy is tracked and readable from every machine;
        their mailbox is gitignored and readable from one. A delivery made from another
        machine looks exactly like this from here."""
        v = self._classify(travels=True)
        self.assertEqual(v.state, deliver_mod.OFF_MACHINE,
                         "absence from a mailbox we cannot read everywhere proves nothing")
        self.assertIn("MACHINE-LOCAL", v.where)
        self.assertIn("absence is not evidence", v.where)

    def test_a_machine_local_brief_absent_from_their_mailbox_is_still_UNDELIVERED(self):
        """THE test that the fix did not simply gut the verdict. A brief sitting in our own
        gitignored `pending/` never left this machine, so this machine is the only machine
        that could have sent it and `_retire_source` would have moved it if we had. There,
        absence really is evidence."""
        v = self._classify(travels=False)
        self.assertEqual(v.state, deliver_mod.UNDELIVERED,
                         "our copy is machine-local, so we are the only possible sender — "
                         "the verdict stands")

    def test_an_unknown_travel_answer_is_OFF_MACHINE_with_its_own_reason(self):
        """`None` in, `None` out. Inventing `False` where git could not answer would
        manufacture exactly the confident verdict this change exists to remove."""
        v = self._classify(travels=None)
        self.assertEqual(v.state, deliver_mod.OFF_MACHINE)
        self.assertIn("could not tell whether our copy", v.where)
        self.assertNotIn("MACHINE-LOCAL", v.where,
                         "not the same reason as the asymmetry case — do not fold them")

    def test_a_tracked_recipient_mailbox_makes_absence_evidence_again(self):
        """beta's mailbox is IN git, so a delivery from any machine is committed and
        visible from here. (Writing there is refused for other reasons — that is a rule
        about writing, and this is a question about reading.)"""
        v = self._classify("beta-arch", travels=True)
        self.assertEqual(v.state, deliver_mod.UNDELIVERED,
                         "their mailbox travels, so we really can see it was not delivered")

    def test_a_delivered_stamp_is_quoted_as_the_senders_self_report_not_a_receipt(self):
        """The live brief carried `delivered:` in its own frontmatter. It is the
        SENDER writing about itself — the same class of evidence as our ROADMAP recording a
        brief as "delivered to their inbox" while their banner read `inbox: (empty)`. So it
        is quoted as corroboration and never relied on as the answer."""
        stamped = self.tmp / "stamped.md"
        stamped.write_text("---\nedit-id: stamped-1\n"
                           "delivered: 2026-08-13 to example-app\n---\n\n# A brief\n",
                           encoding="utf-8")
        v = self._classify(travels=True, brief=stamped)
        self.assertEqual(v.state, deliver_mod.OFF_MACHINE)
        self.assertIn("delivered: 2026-08-13 to example-app", v.where,
                      "quote the stamp so the reader can weigh it")
        self.assertIn("self-report", v.where)
        self.assertIn("not a receipt", v.where)


    def test_a_deferred_travels_probe_is_resolved_like_a_plain_answer(self):
        """`audit()` hands `classify` a PROBE rather than an answer, so the git call is
        paid for only on the branch that needs it. Both forms must reach the same verdict
        — a lazily-obtained False is still False."""
        v = self._classify(travels=lambda: False)
        self.assertEqual(v.state, deliver_mod.UNDELIVERED)

    def test_the_travels_probe_is_never_asked_about_a_brief_that_arrived(self):
        """Why it is deferred at all: `audit_status_line` runs on the SessionStart path,
        and a check that shells out to git per brief has no business there. A DELIVERED
        verdict is settled before the question of travel ever arises."""
        theirs = self.alpha / "proposed-edits" / "alpha-arch" / "pending" / "theirs.md"
        theirs.write_text("---\nedit-id: 2026-08-13-a-brief\n---\n\n# Applied\n",
                          encoding="utf-8")
        asked = []

        def probe():
            asked.append(1)
            return True

        v = self._classify(travels=probe)
        self.assertEqual(v.state, deliver_mod.DELIVERED)
        self.assertEqual(asked, [], "a settled verdict must not pay for a subprocess")

class TrackedNamesTest(unittest.TestCase):
    """Does OUR copy of a brief travel between machines? A tracked file is in the repo's
    history, so every clone has it; an ignored or untracked one exists on exactly the
    machine that wrote it. That one bit is what lets UNDELIVERED declare the scope of its
    own evidence."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _repo(self, name):
        repo = self.tmp / name
        (repo / "outbox").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        return repo

    def _commit(self, repo, rel):
        subprocess.run(["git", "-C", str(repo), "add", rel], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid",
                        "-c", "commit.gpgsign=false",
                        "commit", "-qm", "x"], check=True)

    def test_a_committed_brief_is_named_and_therefore_travels(self):
        repo = self._repo("alpha")
        (repo / "outbox" / "sent.md").write_text("x", encoding="utf-8")
        self._commit(repo, "outbox/sent.md")
        self.assertEqual(deliver_mod._tracked_names(repo / "outbox"), {"sent.md"})

    def test_an_untracked_brief_is_absent_from_the_set(self):
        """A member's `outbox/` is tracked and the federation's `proposed-edits/` is not —
        the whole asymmetry turns on telling those two apart."""
        repo = self._repo("alpha")
        (repo / "outbox" / "sent.md").write_text("x", encoding="utf-8")
        self._commit(repo, "outbox/sent.md")
        (repo / "outbox" / "local-only.md").write_text("x", encoding="utf-8")
        names = deliver_mod._tracked_names(repo / "outbox")
        self.assertNotIn("local-only.md", names)
        self.assertFalse(deliver_mod._travels(names, repo / "outbox" / "local-only.md"))

    def test_a_directory_in_no_git_repo_is_an_EMPTY_SET_not_unknown(self):
        """Established, not unknown: nothing outside git travels by git. Returning None
        here would report every such brief as UNDETERMINED and quietly retire the
        UNDELIVERED verdict for the whole class."""
        plain = self.tmp / "no-repo"
        plain.mkdir()
        names = deliver_mod._tracked_names(plain)
        self.assertIsNotNone(names, "we know the answer here; it is not 'cannot tell'")
        self.assertEqual(names, set())

    def test_git_failing_to_answer_is_None_and_never_collapses_into_either_answer(self):
        """`None` is reserved for git genuinely failing. A directory that is not there at
        all is not the same fact as a directory outside git, and folding them would hand
        `_travels` a confident False it never established."""
        self.assertIsNone(deliver_mod._tracked_names(self.tmp / "nowhere" / "at-all"))
        self.assertIsNone(deliver_mod._travels(None, pathlib.Path("b.md")),
                          "None in, None out — no fact about the files in it")


class AuditResidencyReportTest(unittest.TestCase):
    """The report layer of WI-0205: a member the roster keeps on another machine is not a
    channel failure, and must not be printed with the urgency, the advice, or the exit code
    of one — while staying visible rather than being silently dropped."""

    def _elsewhere(self, aid="far-arch"):
        return deliver_mod.Unreachable(aid, "far", "lives on Runner, by declaration.",
                                       by_design=True)

    def _broken(self, aid="beta-arch"):
        return deliver_mod.Unreachable(aid, "beta", "TRACKED in git")

    def _print(self, verdicts=([], []), unreachable=()):
        out = io.StringIO()
        with mock.patch.object(deliver_mod, "audit", return_value=verdicts), \
             mock.patch.object(deliver_mod, "reachability",
                               return_value=list(unreachable)), \
             contextlib.redirect_stdout(out):
            rc = deliver_mod._print_audit()
        return rc, out.getvalue()

    def test_a_by_design_row_prints_under_its_own_heading_not_under_UNREACHABLE(self):
        """Folding them made the one real failure — a member's committed symlink loop —
        the fourth line of four, which is how a report trains its reader to skim it."""
        _rc, text = self._print(unreachable=[self._elsewhere(), self._broken()])
        self.assertIn("ELSEWHERE BY DESIGN", text)
        head = text.index("ELSEWHERE BY DESIGN")
        broken = text.index("UNREACHABLE —")
        self.assertIn("far-arch", text[head:broken], "declared residency, its own section")
        self.assertNotIn("far-arch", text[broken:],
                         "a by-design member is not something nobody can write to")
        self.assertIn("beta-arch", text[broken:], "the real failure keeps its heading")

    def test_a_by_design_row_alone_does_not_fail_the_audit(self):
        """The fleet layout working as designed is not a channel failure, and an audit that
        exits nonzero on one trains its reader to ignore the exit code."""
        rc, text = self._print(unreachable=[self._elsewhere()])
        self.assertEqual(rc, 0, "nothing is broken — the roster says so")
        self.assertIn("ELSEWHERE BY DESIGN", text, "reported, not silently dropped")
        self.assertNotIn("UNREACHABLE", text)

    def test_the_evidence_scope_is_stated_before_the_verdicts_not_after_them(self):
        """A reader who has already read "UNDELIVERED" has formed the belief; the scope of
        the evidence has to arrive first to qualify it."""
        v = deliver_mod.Verdict("alpha-arch", pathlib.Path("/x/b.md"),
                                deliver_mod.UNDELIVERED, "/x")
        _rc, text = self._print(verdicts=([v], []))
        self.assertIn("Evidence scope:", text)
        self.assertIn("ADR-0088 D1", text, "say WHY the scope is one machine")
        self.assertLess(text.index("Evidence scope:"), text.index("UNDELIVERED —"),
                        "the scope qualifies the verdicts, so it comes before them")

    def test_the_status_line_does_not_count_by_design_rows_as_unreachable(self):
        """A count an operator reads as "recipients nobody can write to" must not be
        inflated by members that are exactly where the roster puts them."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability",
                               return_value=[self._elsewhere(), self._broken()]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]):
            line = deliver_mod.audit_status_line()
        self.assertIn("1 recipient(s) unreachable", line)
        self.assertNotIn("far-arch", line)

    def test_the_status_line_is_silent_when_the_only_row_is_by_design(self):
        """It stays visible under `--audit`; what it does not do is cry wolf every
        session."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability",
                               return_value=[self._elsewhere()]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]):
            self.assertEqual(deliver_mod.audit_status_line(), "")


class StalenessScopedToProbeableRowsTest(unittest.TestCase):
    """WI-0205 / ADR-0110 D5. `_registry_staleness` measured the oldest `verified` date
    across EVERY row and told the reader to run `--probe`. On one machine the oldest rows
    were the members that live on another machine —
    and `write_probe_results` only advances `verified` where a probe determined something.
    So the banner said "mailbox registry 19d stale, run `--probe`" every session, and
    running it would have changed nothing, ever. An alarm the named command cannot clear
    teaches its reader to skip the line, and that line is the only delivery signal most
    sessions ever see."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.reg = self.tmp / "mailboxes.json"
        self._save = deliver_mod.MAILBOXES
        deliver_mod.MAILBOXES = self.reg
        self.addCleanup(setattr, deliver_mod, "MAILBOXES", self._save)
        # Pinned so the test does not depend on which machine it runs on.
        self._machine = deliver_mod.this_machine
        deliver_mod.this_machine = lambda: "DevBox"
        self.addCleanup(setattr, deliver_mod, "this_machine", self._machine)

    def _reg(self, **rows):
        self.reg.write_text(json.dumps({"schema": 1, "members": {
            sid: dict({"architect_id": f"{sid}-arch", "mailbox": "m"}, **row)
            for sid, row in rows.items()}}), encoding="utf-8")

    def test_an_elsewhere_row_raises_no_stale_warning_this_machine_cannot_clear(self):
        self._reg(here={"verified": deliver_mod._today()},
                  away={"verified": "2020-01-01", "resides": "Runner"})
        line = deliver_mod._registry_staleness()
        self.assertNotIn("registry", line,
                         "the old code measured the Runner row and prescribed a command "
                         "that can never advance it")
        self.assertIn("only be re-probed from the machine they live on", line,
                      "the fact is not dropped, it is restated with the right remedy")

    def test_the_elsewhere_clause_names_a_machine_not_a_command(self):
        self._reg(here={"verified": deliver_mod._today()},
                  away={"verified": "2020-01-01", "resides": "Runner"})
        self.assertNotIn("--probe", deliver_mod._registry_staleness(),
                         "a remedy this machine cannot perform must not be printed as one")

    def test_a_genuinely_stale_local_row_still_warns_and_still_names_the_command(self):
        """The fix must not buy quiet by going blind — this is what D5 preserves."""
        self._reg(here={"verified": "2020-01-01"},
                  away={"verified": deliver_mod._today(), "resides": "Runner"})
        line = deliver_mod._registry_staleness()
        self.assertIn("stale", line)
        self.assertIn("--probe", line, "a row this machine CAN probe keeps the command")

    def test_a_fresh_elsewhere_row_says_nothing(self):
        self._reg(here={"verified": deliver_mod._today()},
                  away={"verified": deliver_mod._today(), "resides": "Runner"})
        self.assertEqual(deliver_mod._registry_staleness(), "")

    def test_a_row_declaring_THIS_machine_is_measured_like_any_other(self):
        """`resides` naming this machine is not an excuse: the repo is supposed to be here,
        so keeping its row fresh is this machine's job."""
        self._reg(mine={"verified": "2020-01-01", "resides": "DevBox"})
        line = deliver_mod._registry_staleness()
        self.assertIn("stale", line)
        self.assertIn("--probe", line)

    def test_an_unnamed_machine_cannot_excuse_a_single_row(self):
        """`this_machine()` returning "" withholds the exemption rather than granting it —
        the same rule that stops an unnamed machine excusing an absent member."""
        deliver_mod.this_machine = lambda: ""
        self._reg(away={"verified": "2020-01-01", "resides": "Runner"})
        self.assertIn("stale", deliver_mod._registry_staleness(),
                      "not knowing which machine we are is a reason to keep measuring")


class OffMachineIsItsOwnGradeTest(unittest.TestCase):
    """WI-0205 meeting WI-0223. That item graded "could not tell" by asking *will a re-run
    ever answer this?* — UNKNOWN for a blocker you can fix and re-run, UNRESOLVABLE for a
    brief with no content key, where no later ever comes. This is the third position on
    that same axis: a brief that HAS a content key, that no re-run on THIS machine will
    ever settle, and that another machine could settle today.

    Folding it into UNKNOWN would send the reader back to a check that cannot answer here —
    the exact failure WI-0223 removed. Folding it into UNRESOLVABLE would be worse: it IS
    resolvable, just not from here, and the remedy is a machine rather than an amended
    brief. Three different next actions, so three states."""

    def _v(self, state):
        return deliver_mod.Verdict("a-arch", pathlib.Path("b.md"), state, "why", "")

    def test_off_machine_gets_its_own_bucket_and_borrows_no_other(self):
        b = deliver_mod._split([self._v(deliver_mod.OFF_MACHINE)])
        self.assertEqual(len(b.off_machine), 1)
        for other in ("lost", "unknown", "unresolvable", "corroborated", "leftover"):
            self.assertEqual(getattr(b, other), [],
                             f"an off-machine verdict must not also land in {other}")

    def test_the_three_could_not_tell_states_are_three_different_words(self):
        """If any two of these ever collapse, the reader gets one heading for two
        different next actions — which is the whole defect both items exist to remove."""
        self.assertEqual(len({deliver_mod.UNKNOWN, deliver_mod.UNRESOLVABLE,
                              deliver_mod.OFF_MACHINE}), 3)

    def test_the_status_line_counts_it_apart_from_the_other_two(self):
        with mock.patch.object(deliver_mod, "audit",
                               return_value=([self._v(deliver_mod.OFF_MACHINE)], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]):
            line = deliver_mod.audit_status_line()
        self.assertIn("1 answerable only from another machine", line)
        self.assertNotIn("undetermined", line,
                         "nothing here could be told by re-running; the count must not "
                         "borrow that word")
        self.assertNotIn("unknowable by construction", line,
                         "it IS knowable — from the other machine")

    def test_it_prints_under_its_own_heading_and_does_not_fail_the_audit(self):
        """Nothing an operator on this machine can do changes it, and no amount of work
        here will — failing every session on a fact about a different disk is the
        crying-wolf failure in its purest form."""
        out = io.StringIO()
        with mock.patch.object(deliver_mod, "audit",
                               return_value=([self._v(deliver_mod.OFF_MACHINE)], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             contextlib.redirect_stdout(out):
            rc = deliver_mod._print_audit()
        self.assertEqual(rc, 0, "an off-machine verdict is honest, not actionable here")
        self.assertIn("OFF-MACHINE", out.getvalue())


class ProductionMailClauseTest(unittest.TestCase):
    """Under production every other clause on this line reads the wrong place (WI-0364).

    `audit()` walks the tracked `outbox/` directories and `channel.waiting()` reads the
    Runner branch. On a cut-over host neither is carrying the mail any more — it is in
    `mailqueue`'s lifecycle states and the worker's health file, and both surfaces were
    blind to both. A stopped worker and a healthy channel printed the same line, which is
    the quietest way a delivery system can fail. Recorded on WI-0363 for an owner rather
    than half-built there; these are its tests.

    THE HEALTH RECORDS HERE ARE BUILT BY RUNNING A CYCLE, not by writing a record. A
    hand-written record can carry a shape `run_cycle` never produces, and a clause
    asserted against a fiction reads exactly like a working one.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        base = self.tmp.resolve()
        self.code = base / "release"
        self.state = base / "state"
        self.config = base / "config"
        for path in (self.code / "curate", self.state, self.config):
            path.mkdir(parents=True)
        import mailqueue
        import mailworker
        from production import Roots
        self.mailqueue, self.mailworker = mailqueue, mailworker
        self.Roots = Roots
        self.roots = Roots(self.code, self.state, base / "transport.git",
                           self.config, None, {})

    # ── fixtures ──────────────────────────────────────────────────────────────────
    def _knobs(self, **over):
        self.roots = self.Roots(self.roots.code_root, self.roots.state_root,
                                self.roots.transport_root, self.roots.config_root,
                                None, over)

    def _queue(self, text="body", message_id="m1"):
        return self.mailqueue.enqueue(self.roots, "alpha-arch", "brief.md", text,
                                      message_id=message_id)

    def _cycle(self, *, publish=None, consume=None):
        publish = publish or (lambda roots: {"status": "ok", "last_publication": None})
        consume = consume or (lambda roots: {"snapshots": [], "outcomes": [], "errors": []})
        return self.mailworker.run_cycle(self.roots, publish=publish, consume=consume)

    def _clause(self):
        return deliver_mod.production_mail_clause(self.roots)

    # ── off production ────────────────────────────────────────────────────────────
    def test_off_production_the_clause_is_silent_and_costs_nothing(self):
        """Every development checkout resolves to None, which is every checkout until the
        cutover. `curate/` is federation-only, so this rides the federation's own
        SessionStart hook rather than the fleet's — but that hook is the path a person
        waits on at every session open, and a clause that speaks when there is nothing to
        say is the one that gets skipped when there is."""
        with mock.patch.object(deliver_mod.production, "resolve", return_value=None):
            self.assertEqual(deliver_mod.production_mail_clause(), ("", False))

    def test_an_unreadable_production_config_is_reported_not_swallowed(self):
        """A startup hook must never brick startup and must never fall silent either:
        'not checked' and 'nothing to report' are different facts."""
        with mock.patch.object(deliver_mod.production, "resolve",
                               side_effect=ValueError("bad config")):
            clause, fault = deliver_mod.production_mail_clause()
        self.assertEqual(clause, "production mail configuration UNREADABLE (ValueError) — "
                                 "the queue behind this line cannot be read")
        self.assertTrue(fault, "a configuration nothing can read is not a clean queue")

    # ── the states that must not read as health ───────────────────────────────────
    def test_a_worker_whose_own_status_read_raises_is_reported_not_swallowed(self):
        """The `except` around `read_status` is a different failure from the one around
        `resolve`: the config parsed, the roots are real, and reading the observation
        itself blew up — a corrupt health file, a state root that vanished, a knob out of
        range. It has to be a fault, because the alternative is a startup line that says
        nothing about a queue it could not look at."""
        with mock.patch.object(self.mailworker, "read_status",
                               side_effect=OSError("state root gone")):
            clause, fault = self._clause()
        self.assertEqual(clause, "production mail worker NOT CHECKED (OSError)",
                         "name what went wrong, not merely that it did")
        self.assertTrue(fault, "a queue nothing could read is not a clean queue")

    def test_a_worker_that_never_ran_is_unknown_rather_than_empty(self):
        """No health file at all. The failure this guards is the one that makes an
        unattended worker worth having: nothing scheduled ever ran, and the surface an
        operator reads said nothing, because nothing is exactly what an empty queue says
        too."""
        clause, fault = self._clause()
        self.assertIn("UNKNOWN", clause)
        self.assertTrue(fault)
        self.assertNotIn("queued in the production mail queue", clause,
                         "a worker with no observation has no count to report")
        self.assertIn("queue not counted", clause,
                      "and it says so, rather than letting the absence read as zero")
        self.assertIn("no readable local worker observation", clause,
                      "the worker's own stated reason is carried, not replaced by ours")

    def test_a_stale_observation_suppresses_the_count_it_would_otherwise_print(self):
        """THE DISCRIMINATING CASE. A worker that stopped an hour ago still has a
        `pending_count` in its last record, and printing it beside 'stale' presents a dead
        measurement as a current one — the reader acts on the number, not the adjective.

        `test_queued_mail_is_visible_and_is_not_a_fault` is this test's positive control:
        same fixture, fresh observation, and the count IS printed there. Without that pair
        this assertion would pass for free on any implementation that never counts."""
        self._knobs(poll_interval_seconds=30, stale_after_seconds=60)
        self._queue()
        self._cycle()
        record = json.loads(
            (self.state / "runtime" / self.mailworker.HEALTH_NAME).read_text())
        self.assertEqual(record["pending_count"], 1,
                         "the fixture must actually have a count to suppress")
        old = self.mailworker._now() - datetime.timedelta(hours=1)
        record["observed_at"] = old.isoformat()
        (self.state / "runtime" / self.mailworker.HEALTH_NAME).write_text(
            json.dumps(record))
        clause, fault = self._clause()
        self.assertIn("STALE", clause)
        self.assertTrue(fault)
        self.assertNotIn("queued in the production mail queue", clause,
                         "a stopped worker's last count is not a current measurement")
        self.assertIn("queue not counted", clause,
                      "the suppression is stated; a missing number is not an argument")
        self.assertIn("3600s ago", clause, "how stale, not merely that it is")
        self.assertIn("past its 60s threshold", clause,
                      "and against which threshold, so the reader can judge it")

    # ── the states that are ordinary ──────────────────────────────────────────────
    def test_a_healthy_empty_queue_says_nothing_at_all(self):
        """The line is already carrying eight clauses; a ninth that fires every session
        with nothing behind it is how a status line teaches its reader to skip it."""
        self._cycle()
        self.assertEqual(self._clause(), ("", False))

    def test_queued_mail_is_visible_and_is_not_a_fault(self):
        """Mail in the queue is in flight — the next cycle is what moves it. Failing an
        audit on it is the crying-wolf failure this file warns about four other times;
        omitting it is the blind spot the item exists to close. Both halves are asserted
        here because either one alone passes on a wrong implementation."""
        self._queue()
        self._cycle()
        clause, fault = self._clause()
        self.assertIn("1 queued in the production mail queue", clause)
        self.assertFalse(fault, "in-flight mail is not an actionable fault")

    # ── the states that need a person ─────────────────────────────────────────────
    def test_an_outcome_needing_attention_is_named_and_is_a_fault(self):
        """The attention set is the delivery path's own vocabulary, and `unmatched-ack`
        was in it for a day before anything read it (WI-0363). Naming the outcome rather
        than counting it is what keeps the next addition from arriving invisible."""
        def consume(roots):
            return {"snapshots": [], "errors": [],
                    "outcomes": [{"ref": "refs/x", "path": "p", "outcome": "unroutable",
                                  "detail": "no such recipient"}]}
        self._cycle(consume=consume)
        clause, fault = self._clause()
        self.assertIn("1 delivery outcome(s) needing attention (unroutable)", clause)
        self.assertTrue(fault)

    def test_several_kinds_of_attention_are_listed_rather_than_totalled(self):
        """A count alone sends its reader to another command to find out which. The set is
        deduplicated and sorted so the same two kinds always read the same way."""
        with mock.patch.object(self.mailworker, "read_status", return_value={
                "status": "ok", "pending_count": 0, "attention": [
                    {"outcome": "unroutable"}, {"outcome": "malformed"},
                    {"outcome": "unroutable"}]}):
            clause, fault = self._clause()
        self.assertIn("3 delivery outcome(s) needing attention (malformed, unroutable)",
                      clause)
        self.assertTrue(fault)

    def test_a_failed_cycle_names_its_own_failure(self):
        self._cycle(publish=lambda roots: {"status": "failed", "failure": "remote refused"})
        clause, fault = self._clause()
        self.assertIn("FAILED", clause)
        self.assertIn("remote refused", clause,
                      "a failure with no reason sends its reader back to the logs")
        self.assertTrue(fault)

    def test_a_failure_with_no_stated_reason_still_reads_as_a_sentence(self):
        """`failure` can be absent or None on a record. Interpolated bare it prints
        'None', which an operator reads as a reason rather than as its absence."""
        for state in ("failed", "unknown"):
            with self.subTest(state=state):
                with mock.patch.object(self.mailworker, "read_status",
                                       return_value={"status": state, "failure": None}):
                    clause, fault = self._clause()
                self.assertIn("no reason recorded", clause)
                self.assertNotIn("None", clause)
                self.assertTrue(fault)

    def test_two_things_worth_saying_are_joined_readably(self):
        """The clause is one line inside a line that is itself a list of clauses, so its
        own separator is the only thing keeping two facts from reading as one."""
        with mock.patch.object(self.mailworker, "read_status", return_value={
                "status": "failed", "failure": "remote refused", "pending_count": 4}):
            clause, _ = self._clause()
        self.assertIn("remote refused; 4 queued in the production mail queue", clause)

    def test_an_aged_queue_names_the_threshold_it_passed(self):
        """A worker that is running fine while the oldest message never leaves is the
        'stalled queue' the item's outcome names, and it is NOT the stale case: the
        observation is current, which is what makes the age trustworthy."""
        self._knobs(poll_interval_seconds=30, stale_after_seconds=604800,
                    aged_after_seconds=60)
        self._queue()
        self._cycle()
        later = self.mailworker._now() + datetime.timedelta(hours=2)
        with mock.patch.object(self.mailworker, "_now", return_value=later):
            clause, fault = self._clause()
        self.assertIn("oldest queued message ", clause,
                      "the subject is the MAIL, not the worker — the words carry that")
        self.assertIn("past its 60s threshold", clause)
        self.assertNotIn("STALE", clause,
                         "the observation is current; only the mail is old")
        self.assertTrue(fault)

    # ── the two surfaces ──────────────────────────────────────────────────────────
    def test_the_startup_line_carries_the_queue_when_nothing_else_speaks(self):
        """The regression this whole class exists for: with the brief channel clean, the
        startup line returned "" and said nothing about a queue that was not moving."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]), \
             mock.patch.object(deliver_mod, "production_mail_clause",
                               return_value=("mail worker STALE — 900s ago", True)):
            line = deliver_mod.audit_status_line()
        self.assertIn("mail worker STALE", line)

    def test_an_empty_queue_clause_does_not_leak_a_separator_into_the_line(self):
        """Off production the clause is "", and an empty string appended to the clause
        list still gets a '; ' from the join — so every member's startup line would open
        with a separator and nothing before it. The clause has to be absent, not empty."""
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "_registry_staleness",
                               return_value="mailbox registry 9d stale"), \
             mock.patch.object(deliver_mod.channel, "waiting", return_value=[]), \
             mock.patch.object(deliver_mod, "production_mail_clause",
                               return_value=("", False)):
            line = deliver_mod.audit_status_line()
        self.assertIn("Delivery audit: mailbox registry 9d stale", line)

    def test_a_clean_brief_channel_does_not_certify_a_faulting_queue(self):
        """`--audit` printed 'clean — every brief in both directions is accounted for'
        and exited 0 while the queue behind it was stopped. The sentence was true and the
        verdict was wrong, which is the harder of the two to notice."""
        out = io.StringIO()
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "production_mail_clause",
                               return_value=("mail worker STALE — 900s ago", True)), \
             contextlib.redirect_stdout(out):
            rc = deliver_mod._print_audit()
        self.assertEqual(rc, 1, "a stopped queue is actionable and must fail the audit")
        self.assertIn("PRODUCTION MAIL QUEUE", out.getvalue())
        self.assertIn("mail worker STALE", out.getvalue())

    def test_visible_but_faultless_queue_state_does_not_fail_the_audit(self):
        """The companion to the test above, and the reason the clause returns two values
        rather than letting the caller infer the verdict from the text."""
        out = io.StringIO()
        with mock.patch.object(deliver_mod, "audit", return_value=([], [])), \
             mock.patch.object(deliver_mod, "reachability", return_value=[]), \
             mock.patch.object(deliver_mod, "production_mail_clause",
                               return_value=("3 queued in the production mail queue", False)), \
             contextlib.redirect_stdout(out):
            rc = deliver_mod._print_audit()
        self.assertEqual(rc, 0, "mail in flight is not a channel failure")
        self.assertIn("3 queued", out.getvalue(), "and it is still shown")
