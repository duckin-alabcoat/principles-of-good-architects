"""ADR-0112 — relay-authorization grants: the record a lane cites instead of a peer.

A dispatched lane has no inbound channel (WI-0166), so an approval arrives as a peer
lane's prose. Six lanes on 2026-09-03 correctly refused to make shared-state writes on
that authority and had nothing to cite instead. A grant changes WHAT THE LANE ASSERTS —
"authorized via G-xxxx", a claim about a record it read itself — rather than proving who
minted it (D10: intent and time, never identity).

The invariants pinned here are the ones a plausible future refactor would break:

  1. a grant round-trips through the coordination store (D2);
  2/3. it is NOT a `COORD_KINDS` member and survives lane teardown (D3) — the second is
     the behavioural half, and the one that would actually catch the regression;
  4. the default scope is the routine set and stops short of canon (D6);
  5. the resolver reports all six outcomes with a sentence quotable to the operator (D8);
  6. it FAILS CLOSED, inverting this file's otherwise universal fail-open contract (D8);
  7-10. wildcards, duration parsing, the origin marker (D7), and what `list` hides;
  11. `authorize-check` exits 0 only on GRANTED.

Fixture shape is `test_claims.py`'s: a temp git repo with two linked worktree lanes, a
stubbed `session.CFG`, and the ambient session's environment variables popped so a test
never reads the developer's live session. `POGA_DISPATCH` is popped here for the same
reason and one variable further on — it is D7's input, so a suite run from inside a
dispatched lane would otherwise stamp every fixture grant `origin: dispatched-lane` and
the default-origin assertion would pass or fail on who ran it.
"""

import argparse
import ast
import inspect
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")

#: D4 — `G-` plus `secrets.token_hex(3)`. Minted, not drawn: a grant lands no tracked
#: file, so there is nothing for two lanes to collide over at land time.
GRANT_ID_SHAPE = re.compile(r"^G-[0-9a-f]{6}$")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _out(repo, *args):
    """One git read, as text. Not `check=True`: several callers ask questions whose
    honest answer is an empty string (WI-0280's trailer read on a commit that has none)."""
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(GIT, "git not available")
class GrantsBase(unittest.TestCase):
    def setUp(self):
        # The fictional holders below must not inherit the runner's journal and read as
        # one holder (WI-0126). First thing, before anything writes a record.
        # FIRST, before the fixture builds anything: this snapshots the environment and
        # restores that snapshot wholesale at cleanup, so anything set before it is baked
        # in and outlives the test (WI-0275). `POGA_INVOKED_FROM` would point the holder
        # lookup at the DEVELOPER'S checkout if left in place (WI-0061), and
        # `POGA_DISPATCH` is D7's input — it would silently retag every grant this module
        # mints. Tests that care about either set it explicitly, after this call.
        #
        # One list, not two: `AMBIENT_VARS` is `DISPATCH_ENV_VARS` plus the identity axis
        # (WI-0275), so this replaces the separate `neutralize_dispatch_env` call. The
        # hand-kept list that preceded both named four variables and omitted two, and was
        # the ONLY reason the two `origin` assertions below passed inside a dispatched
        # lane — a guard nobody could see, holding up a test nobody knew relied on it.
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "README.md").write_text("fixture\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        _git(self.main, "branch", "-M", "main")
        self.laneA = self.main / ".claude" / "worktrees" / "poga-1"
        self.laneB = self.main / ".claude" / "worktrees" / "poga-2"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.laneA), "main")
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-2",
             str(self.laneB), "main")
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        # Every grant call runs from lane 1, so `sh(cwd=ROOT)` resolves the git common
        # dir to the FIXTURE repo's `.git` and nothing touches the real store.
        session.ROOT = self.laneA

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── helpers ────────────────────────────────────────────────────────────────
    def mint(self, items=(), lanes=(), verbs=(), ttl=3600, note=""):
        rec = session._grant_mint(items=list(items), lanes=list(lanes),
                                  verbs=list(verbs), ttl=ttl, note=note)
        self.assertTrue(rec.get("written"), "the fixture's grant did not land on disk")
        return rec

    def grants_dir(self):
        return self.main / ".git" / session.COORD_DIRNAME / session.GRANTS_DIRNAME

    def rewrite(self, gid, **fields):
        """Edit a grant record in place — how the fixture fabricates an expired one."""
        rec = session._grant_read(gid)
        self.assertIsNotNone(rec, f"{gid} was not readable back")
        rec.update(fields)
        self.assertTrue(session._grant_write(rec))
        return rec

    def expire(self, gid):
        return self.rewrite(gid, expires_at=time.time() - 60,
                            expires_iso=session._coord_iso(time.time() - 60))

    def store_unreachable(self):
        """Simulate an unresolvable coordination store — a lane outside git, or the
        `.git/poga-coord/` tree gone mid-flight (ADR-0112's named failure mode)."""
        return mock.patch.object(session, "_git_common_dir", return_value=None)


class AMintedGrantRoundTripsThroughTheStoreTest(GrantsBase):
    def test_the_id_is_minted_in_the_declared_shape(self):
        rec = self.mint()
        self.assertRegex(rec["grant_id"], GRANT_ID_SHAPE)

    def test_two_grants_do_not_collide(self):
        self.assertNotEqual(self.mint()["grant_id"], self.mint()["grant_id"])

    def test_it_lands_as_json_under_the_shared_git_common_dir(self):
        rec = self.mint(note="cleared in chat")
        path = self.grants_dir() / f"{rec['grant_id']}.json"
        self.assertTrue(path.is_file(), f"no grant file at {path}")
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["grant_id"], rec["grant_id"])
        self.assertEqual(on_disk["kind"], session.GRANTS_DIRNAME)
        self.assertEqual(on_disk["note"], "cleared in chat")

    def test_the_store_is_the_one_a_sibling_lane_reads(self):
        """A grant is minted in one session to authorize a DIFFERENT one, so it has to
        live in the git COMMON dir — the same physical directory from every lane."""
        rec = self.mint()
        session.ROOT = self.laneB
        self.assertEqual(session._grant_read(rec["grant_id"])["grant_id"],
                         rec["grant_id"])

    def test_the_record_carries_its_scope_expiry_and_origin(self):
        rec = self.mint(items=["WI-0261"], lanes=["worktree-poga-1"], verbs=["land"],
                        ttl=1800)
        self.assertEqual(rec["items"], ["WI-0261"])
        self.assertEqual(rec["lanes"], ["worktree-poga-1"])
        self.assertEqual(rec["verbs"], ["land"])
        self.assertEqual(rec["ttl_seconds"], 1800)
        self.assertEqual(rec["origin"], "interactive")
        self.assertEqual(rec["revoked_at"], "")

    def test_the_default_ttl_is_four_hours(self):
        self.assertEqual(session.GRANT_TTL_SECONDS, 4 * 3600)

    def test_the_scope_line_reads_any_for_an_unrestricted_axis(self):
        line = session._grant_scope_line(self.mint(items=["WI-0261"]))
        self.assertIn("items=WI-0261", line)
        self.assertIn("lanes=any", line)


class AGrantIsNotAHoldAndOutlivesTheLaneThatMintedItTest(GrantsBase):
    """D3. Grants sit beside `dispatch`, not beside `claims`.

    A grant exists to authorize a session OTHER than the one that minted it, so outliving
    its minter is the entire point. Membership in `COORD_KINDS` would hand it to the TTL
    reaper, `_coord_release_dead_lanes` and `_lane_exit_release` at once — all three
    of which exist to let go of a dead lane's HOLDS. It is retired by `authorize-revoke`,
    not swept."""

    def test_grants_are_excluded_from_the_hold_kinds(self):
        self.assertNotIn(session.GRANTS_DIRNAME, session.COORD_KINDS,
                         "adding `grants` here silently enlists it in the TTL reaper and "
                         "both teardown sweeps; a grant must outlive its minting session "
                         "(ADR-0112 D3)")

    def test_a_grant_survives_the_teardown_that_releases_a_claim(self):
        """The behavioural half of the exclusion, and the test that would actually catch
        the regression: a constant can be edited, so pin the consequence too."""
        gid = self.mint()["grant_id"]
        session._coord_try_acquire("claims", "WI-0261", "worktree-poga-1", 3600)

        released = session._lane_exit_release("worktree-poga-1")

        self.assertGreaterEqual(len(released), 1,
                                "teardown released nothing, so this asserts nothing about "
                                "what it spares")
        self.assertIsNone(session._coord_read(
            self.main / ".git" / session.COORD_DIRNAME / "claims" / "WI-0261.json"),
            "the claim is a HOLD and teardown must take it")
        self.assertIsNotNone(session._grant_read(gid),
                             "teardown deleted the grant — the lane it authorizes is a "
                             "DIFFERENT session that has not run yet (ADR-0112 D3)")

    def test_the_grant_still_resolves_after_teardown(self):
        gid = self.mint()["grant_id"]
        session._lane_exit_release("worktree-poga-1")
        state, _ = session.resolve_grant(gid, verb="land")
        self.assertEqual(state, session.GRANT_GRANTED)


class TheDefaultScopeIsTheRoutineSetTest(GrantsBase):
    """D6 — the carve-out that stops one wide default from ever reaching canon."""

    def test_the_routine_set_is_exactly_the_four_ordinary_verbs(self):
        self.assertEqual(session.GRANT_ROUTINE_VERBS,
                         ("close", "land", "note", "write"))
        self.assertEqual(session.GRANT_NAMED_VERBS, ("canon", "standard", "fleet"))

    def test_a_bare_grant_covers_every_routine_verb(self):
        gid = self.mint()["grant_id"]
        for verb in session.GRANT_ROUTINE_VERBS:
            with self.subTest(verb=verb):
                state, sentence = session.resolve_grant(gid, verb=verb)
                self.assertEqual(state, session.GRANT_GRANTED, sentence)

    def test_a_bare_grant_covers_none_of_the_named_verbs(self):
        """The other direction, which is the one that matters: the WI-0248 lane refused a
        fleet-wide `session.py` change and under D6 it still refuses a default grant."""
        gid = self.mint()["grant_id"]
        for verb in session.GRANT_NAMED_VERBS:
            with self.subTest(verb=verb):
                state, sentence = session.resolve_grant(gid, verb=verb)
                self.assertEqual(state, session.GRANT_OUT_OF_SCOPE, sentence)
                self.assertIn(verb, sentence)

    def test_a_named_verb_is_covered_once_it_is_asked_for(self):
        gid = self.mint(verbs=["canon"])["grant_id"]
        self.assertEqual(session.resolve_grant(gid, verb="canon")[0],
                         session.GRANT_GRANTED)
        self.assertEqual(session.resolve_grant(gid, verb="land")[0],
                         session.GRANT_OUT_OF_SCOPE,
                         "naming canon narrows the grant to canon; it does not add to the "
                         "routine set")


class AGrantInScopeAuthorizesTheActTest(GrantsBase):
    def test_it_resolves_granted_and_says_which_grant_authorized_it(self):
        rec = self.mint(items=["WI-0261"], lanes=["worktree-poga-1"], verbs=["land"])
        state, sentence = session.resolve_grant(rec["grant_id"], verb="land",
                                               item="WI-0261",
                                               lane="worktree-poga-1")
        self.assertEqual(state, session.GRANT_GRANTED)
        self.assertTrue(sentence.strip())
        self.assertIn(rec["grant_id"], sentence,
                      "the sentence is what the lane records instead of 'operator approved'; "
                      "without the id it is prose again")
        self.assertIn("interactive", sentence, "D7's marker is surfaced, not just stored")

    def test_an_unnamed_axis_is_not_checked(self):
        """`resolve_grant` takes empty strings for axes the caller is not asserting — a
        `note` write that names no item must not be refused for naming no item."""
        gid = self.mint(items=["WI-0261"])["grant_id"]
        self.assertEqual(session.resolve_grant(gid, verb="note")[0],
                         session.GRANT_GRANTED)


class AnUnknownGrantIdAuthorizesNothingTest(GrantsBase):
    def test_an_id_that_was_never_minted_is_no_such_grant(self):
        state, sentence = session.resolve_grant("G-abc123", verb="land")
        self.assertEqual(state, session.GRANT_NO_SUCH)
        self.assertTrue(sentence.strip())
        self.assertIn("G-abc123", sentence)

    def test_an_empty_id_is_no_such_grant_rather_than_a_wildcard(self):
        for gid in ("", "   "):
            with self.subTest(gid=repr(gid)):
                state, sentence = session.resolve_grant(gid, verb="land")
                self.assertEqual(state, session.GRANT_NO_SUCH)
                self.assertTrue(sentence.strip())

    def test_a_deleted_store_reads_as_no_such_grant_not_as_granted(self):
        """The store is ephemeral by design; losing it mid-flight must refuse."""
        gid = self.mint()["grant_id"]
        shutil.rmtree(self.grants_dir())
        state, _ = session.resolve_grant(gid, verb="land")
        self.assertEqual(state, session.GRANT_NO_SUCH)


class AnExpiredGrantAuthorizesNothingTest(GrantsBase):
    def test_a_grant_past_its_expiry_is_refused(self):
        gid = self.mint()["grant_id"]
        self.expire(gid)
        state, sentence = session.resolve_grant(gid, verb="land")
        self.assertEqual(state, session.GRANT_EXPIRED)
        self.assertTrue(sentence.strip())
        self.assertIn(gid, sentence)

    def test_expiry_beats_a_scope_that_would_otherwise_match(self):
        gid = self.mint(items=["WI-0261"], verbs=["land"])["grant_id"]
        self.expire(gid)
        self.assertEqual(session.resolve_grant(gid, verb="land", item="WI-0261")[0],
                         session.GRANT_EXPIRED)

    def test_a_record_with_no_readable_expiry_is_treated_as_expired(self):
        """`_coord_expired` counts a malformed TTL as stale. For a hold that means
        reclaimable; for a grant it means refused, which is the right direction."""
        gid = self.mint()["grant_id"]
        self.rewrite(gid, expires_at="not-a-number")
        self.assertEqual(session.resolve_grant(gid, verb="land")[0],
                         session.GRANT_EXPIRED)


class ARevokedGrantAuthorizesNothingTest(GrantsBase):
    def test_a_revoked_grant_is_refused_even_before_it_expires(self):
        gid = self.mint(ttl=3600)["grant_id"]
        self.rewrite(gid, revoked_at="2026-09-04T10:32:00+00:00")
        state, sentence = session.resolve_grant(gid, verb="land")
        self.assertEqual(state, session.GRANT_REVOKED)
        self.assertTrue(sentence.strip())
        self.assertIn(gid, sentence)

    def test_the_revoke_verb_produces_that_state(self):
        gid = self.mint()["grant_id"]
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            session.cmd_authorize_revoke(argparse.Namespace(id=gid))
        self.assertIn("revoked", out.getvalue())
        self.assertEqual(session.resolve_grant(gid, verb="land")[0],
                         session.GRANT_REVOKED)

    def test_revoking_an_unknown_grant_is_an_error_not_a_silent_ok(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            session.cmd_authorize_revoke(argparse.Namespace(id="G-000000"))
        self.assertNotEqual(cm.exception.code, 0)

    def test_revoking_twice_is_idempotent(self):
        gid = self.mint()["grant_id"]
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            session.cmd_authorize_revoke(argparse.Namespace(id=gid))
            session.cmd_authorize_revoke(argparse.Namespace(id=gid))
        self.assertIn("already revoked", out.getvalue())


class ARefusalNamesTheAxisThatMissedTest(GrantsBase):
    """D8 — each non-granted outcome returns a sentence the lane can quote BACK to its
    operator. "Out of scope" alone is not actionable; "it covers close, not 'canon'" is."""

    def test_the_wrong_verb_class_is_named(self):
        gid = self.mint(verbs=["close"])["grant_id"]
        state, sentence = session.resolve_grant(gid, verb="canon")
        self.assertEqual(state, session.GRANT_OUT_OF_SCOPE)
        self.assertIn("canon", sentence)
        self.assertIn("close", sentence)
        self.assertNotIn("covers items", sentence,
                         "only the axis that actually missed may be reported")
        self.assertNotIn("covers lanes", sentence)

    def test_the_wrong_work_item_is_named(self):
        gid = self.mint(items=["WI-0261"])["grant_id"]
        state, sentence = session.resolve_grant(gid, item="WI-0249")
        self.assertEqual(state, session.GRANT_OUT_OF_SCOPE)
        self.assertIn("covers items WI-0261", sentence)
        self.assertIn("WI-0249", sentence)
        self.assertNotIn("covers lanes", sentence)

    def test_the_wrong_lane_is_named(self):
        gid = self.mint(lanes=["worktree-poga-1"])["grant_id"]
        state, sentence = session.resolve_grant(gid, lane="worktree-poga-2")
        self.assertEqual(state, session.GRANT_OUT_OF_SCOPE)
        self.assertIn("covers lanes worktree-poga-1", sentence)
        self.assertIn("worktree-poga-2", sentence)
        self.assertNotIn("covers items", sentence)

    def test_every_missed_axis_is_reported_together_with_the_full_scope(self):
        gid = self.mint(items=["WI-0261"], lanes=["worktree-poga-1"],
                        verbs=["close"])["grant_id"]
        state, sentence = session.resolve_grant(gid, verb="fleet", item="WI-0249",
                                                lane="worktree-poga-2")
        self.assertEqual(state, session.GRANT_OUT_OF_SCOPE)
        self.assertIn("covers items", sentence)
        self.assertIn("covers lanes", sentence)
        self.assertIn("fleet", sentence)
        self.assertIn(session._grant_scope_line(session._grant_read(gid)), sentence)


class AnUnreadableStoreIsRefusedNeverWavedThroughTest(GrantsBase):
    """D8, the sharpest invariant — and the one most likely to be "fixed" away.

    Every other `_coord_*` helper in `session.py` fails OPEN, and says so in its own
    docstring: coordination is an optimization that must never brick its caller, so an
    unresolvable store degrades to a no-op and the operation proceeds. Authorization
    inverts that. A reader who has internalized the fail-open rule will see CANNOT TELL
    as the odd one out and make it consistent, at which point an unreachable store starts
    authorizing every write in the fleet — silently, because a no-op looks like success.
    That refactor is exactly what this test exists to catch, so it asserts the negative
    (`!= GRANTED`) as well as the state."""

    def test_an_unreachable_store_reports_cannot_tell(self):
        gid = self.mint()["grant_id"]
        with self.store_unreachable():
            state, sentence = session.resolve_grant(gid, verb="land")
        self.assertEqual(state, session.GRANT_CANNOT_TELL)
        self.assertTrue(sentence.strip())

    def test_cannot_tell_is_not_granted(self):
        gid = self.mint()["grant_id"]
        with self.store_unreachable():
            state, _ = session.resolve_grant(gid, verb="land", item="WI-0261",
                                             lane="worktree-poga-1")
        self.assertNotEqual(state, session.GRANT_GRANTED,
                            "an unreadable coordination store must never read as an "
                            "authorization (ADR-0112 D8)")

    def test_the_refusal_says_it_is_treating_the_gap_as_unauthorized(self):
        with self.store_unreachable():
            _, sentence = session.resolve_grant("G-abc123", verb="land")
        self.assertIn("NOT authorized", sentence,
                      "the lane has to be able to quote why it stopped")

    def test_even_a_live_in_scope_grant_cannot_be_resolved_without_the_store(self):
        """Guards the test above from passing for the wrong reason: the grant really is
        valid, and the refusal is about reachability rather than about the record."""
        gid = self.mint(items=["WI-0261"], verbs=["land"])["grant_id"]
        self.assertEqual(session.resolve_grant(gid, verb="land", item="WI-0261")[0],
                         session.GRANT_GRANTED)
        with self.store_unreachable():
            self.assertEqual(
                session.resolve_grant(gid, verb="land", item="WI-0261")[0],
                session.GRANT_CANNOT_TELL)


class AWildcardMatchesAnythingOnItsAxisTest(GrantsBase):
    def test_a_wildcard_item_covers_an_item_nobody_named(self):
        gid = self.mint(items=[session.GRANT_WILDCARD])["grant_id"]
        self.assertEqual(session.resolve_grant(gid, item="WI-9999")[0],
                         session.GRANT_GRANTED)

    def test_a_wildcard_lane_covers_a_lane_nobody_named(self):
        gid = self.mint(lanes=[session.GRANT_WILDCARD])["grant_id"]
        self.assertEqual(session.resolve_grant(gid, lane="worktree-poga-14")[0],
                         session.GRANT_GRANTED)

    def test_a_wildcard_verb_covers_the_named_classes_too(self):
        gid = self.mint(verbs=[session.GRANT_WILDCARD])["grant_id"]
        for verb in session.GRANT_ALL_VERBS:
            with self.subTest(verb=verb):
                self.assertEqual(session.resolve_grant(gid, verb=verb)[0],
                                 session.GRANT_GRANTED)

    def test_an_empty_axis_defaults_to_the_wildcard(self):
        rec = self.mint()
        self.assertEqual(rec["items"], [session.GRANT_WILDCARD])
        self.assertEqual(rec["lanes"], [session.GRANT_WILDCARD])
        self.assertEqual(session.resolve_grant(rec["grant_id"], item="WI-9999",
                                               lane="worktree-poga-14")[0],
                         session.GRANT_GRANTED)


class DurationsParseOrRefuseTest(GrantsBase):
    def test_the_documented_units_parse(self):
        for text, seconds in (("30m", 1800), ("4h", 14400), ("2d", 172800),
                              ("45s", 45), ("3600", 3600), (" 2H ", 7200)):
            with self.subTest(text=text):
                self.assertEqual(session._grant_parse_ttl(text), seconds)

    def test_an_unusable_duration_raises_rather_than_defaulting(self):
        """A silently-defaulted TTL would hand a grant a lifetime nobody asked for."""
        for text in ("", "   ", "0", "0h", "-1", "-5m", "banana", "h", "4 hours", None):
            with self.subTest(text=repr(text)):
                with self.assertRaises(ValueError):
                    session._grant_parse_ttl(text)

    def test_the_mint_verb_refuses_a_bad_duration_without_writing_anything(self):
        ns = argparse.Namespace(item=None, lane=None, for_=None, ttl="banana", note=None)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            session.cmd_authorize(ns)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(session._grant_list(include_expired=True), {})


class AGrantRecordsWhereItWasMintedTest(GrantsBase):
    """D7 — a MARKER, not a control. A lane could unset `POGA_DISPATCH`; the point is
    that the lazy self-authorization case is visibly different from an operator's rather
    than indistinguishable. Both directions are set explicitly here: an earlier defect in
    this suite had tests reading the runner's own live environment."""

    def test_a_lane_minting_its_own_grant_is_stamped_as_such(self):
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-edbab9"}):
            self.assertEqual(session._grant_origin(), "dispatched-lane")
            self.assertEqual(self.mint()["origin"], "dispatched-lane")

    def test_an_operator_minting_by_hand_is_stamped_interactive(self):
        os.environ.pop("POGA_DISPATCH", None)
        self.assertEqual(session._grant_origin(), "interactive")
        self.assertEqual(self.mint()["origin"], "interactive")

    def test_a_blank_dispatch_variable_is_not_a_dispatch(self):
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "   "}):
            self.assertEqual(session._grant_origin(), "interactive")

    def test_the_marker_reaches_the_sentence_the_lane_quotes(self):
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-edbab9"}):
            gid = self.mint()["grant_id"]
        _, sentence = session.resolve_grant(gid, verb="land")
        self.assertIn("dispatched-lane", sentence,
                      "a lane deciding whether to accept the grant has to be able to see "
                      "the marker, not merely have it recorded")


class ListingHidesWhatNoLongerAuthorizesTest(GrantsBase):
    def setUp(self):
        super().setUp()
        self.live = self.mint(note="live")["grant_id"]
        self.dead = self.mint(note="dead")["grant_id"]
        self.gone = self.mint(note="gone")["grant_id"]
        self.expire(self.dead)
        self.rewrite(self.gone, revoked_at="2026-09-04T10:32:00+00:00")

    def test_the_default_listing_shows_only_live_grants(self):
        self.assertEqual(set(session._grant_list()), {self.live})

    def test_include_expired_shows_the_expired_and_the_revoked(self):
        self.assertEqual(set(session._grant_list(include_expired=True)),
                         {self.live, self.dead, self.gone})

    def test_an_unreachable_store_lists_nothing(self):
        with self.store_unreachable():
            self.assertEqual(session._grant_list(include_expired=True), {})

    def test_the_list_verb_marks_the_state_of_each_grant(self):
        out = io.StringIO()
        with redirect_stdout(out):
            session.cmd_authorize_list(argparse.Namespace(all=True))
        text = out.getvalue()
        self.assertIn("[EXPIRED]", text)
        self.assertIn("[REVOKED]", text)
        self.assertIn(f"{self.live}  ", text, "the live grant carries no state marker")

    def test_the_list_verb_distinguishes_an_unreadable_store_from_an_empty_one(self):
        """"No grants" and "I cannot read the grants" are different answers, and only one
        of them means it is safe to proceed.

        The refusal goes to STDERR, matching `authorize` and `authorize-revoke`: stdout is
        the listing, and a caller piping it must not get the refusal interleaved with what
        it is parsing. So this asserts on stderr AND that stdout stayed empty — a refusal
        that also printed a line to stdout would read as a (very short) listing."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.store_unreachable():
            with self.assertRaises(SystemExit) as cm:
                session.cmd_authorize_list(argparse.Namespace(all=False))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn(session.GRANT_CANNOT_TELL, err.getvalue())
        self.assertEqual(out.getvalue(), "",
                         "stdout is the listing channel; a refusal there would parse as "
                         "an empty list to anything reading it")


class TheCheckVerbExitsZeroOnlyWhenGrantedTest(GrantsBase):
    """The read side is what a lane actually calls, so the exit code is the contract: a
    non-zero code on every outcome but GRANTED is what keeps a shell wrapper from
    treating CANNOT TELL as a pass."""

    def check(self, gid, **axes):
        ns = argparse.Namespace(id=gid, for_=axes.get("verb"), item=axes.get("item"),
                                lane=axes.get("lane"))
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            session.cmd_authorize_check(ns)
        return cm.exception.code, out.getvalue()

    def test_granted_exits_zero_and_prints_the_citation(self):
        gid = self.mint()["grant_id"]
        code, text = self.check(gid, verb="land")
        self.assertEqual(code, 0)
        self.assertIn(gid, text)

    def test_an_unknown_grant_exits_non_zero(self):
        code, text = self.check("G-000000", verb="land")
        self.assertNotEqual(code, 0)
        self.assertIn(session.GRANT_NO_SUCH, text)

    def test_an_expired_grant_exits_non_zero(self):
        gid = self.mint()["grant_id"]
        self.expire(gid)
        code, text = self.check(gid, verb="land")
        self.assertNotEqual(code, 0)
        self.assertIn(session.GRANT_EXPIRED, text)

    def test_a_revoked_grant_exits_non_zero(self):
        gid = self.mint()["grant_id"]
        self.rewrite(gid, revoked_at="2026-09-04T10:32:00+00:00")
        code, text = self.check(gid, verb="land")
        self.assertNotEqual(code, 0)
        self.assertIn(session.GRANT_REVOKED, text)

    def test_an_out_of_scope_act_exits_non_zero(self):
        gid = self.mint(verbs=["close"])["grant_id"]
        code, text = self.check(gid, verb="canon")
        self.assertNotEqual(code, 0)
        self.assertIn(session.GRANT_OUT_OF_SCOPE, text)

    def test_an_unreachable_store_exits_non_zero(self):
        gid = self.mint()["grant_id"]
        out = io.StringIO()
        with redirect_stdout(out), self.store_unreachable():
            with self.assertRaises(SystemExit) as cm:
                session.cmd_authorize_check(argparse.Namespace(
                    id=gid, for_="land", item=None, lane=None))
        self.assertNotEqual(cm.exception.code, 0,
                            "CANNOT TELL must not exit 0 — a caller that only checks the "
                            "status would read an unreadable store as authorization")
        self.assertIn(session.GRANT_CANNOT_TELL, out.getvalue())


class TheMintVerbIsTheOperatorsOneCommandTest(GrantsBase):
    """D5's ergonomic requirement: a bare `poga authorize` has to clear a working session's
    routine relays, because the thing it replaces is one typed disclaimer per lane per decision."""

    def mint_cli(self, **kw):
        ns = argparse.Namespace(item=kw.get("item"), lane=kw.get("lane"),
                                for_=kw.get("for_"), ttl=kw.get("ttl"),
                                note=kw.get("note"))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            session.cmd_authorize(ns)
        return out.getvalue().strip(), err.getvalue()

    def test_a_bare_mint_prints_the_id_on_stdout_and_the_scope_on_stderr(self):
        gid, err = self.mint_cli()
        self.assertRegex(gid, GRANT_ID_SHAPE)
        self.assertIsNotNone(session._grant_read(gid))
        self.assertIn("items=any", err)
        self.assertIn("for=close, land, note, write", err)

    def test_an_unknown_verb_class_is_refused_before_anything_is_written(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            session.cmd_authorize(argparse.Namespace(
                item=None, lane=None, for_=["deploy"], ttl=None, note=None))
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(session._grant_list(include_expired=True), {})

    def test_a_named_class_is_called_out_when_it_is_granted(self):
        gid, err = self.mint_cli(for_=["canon"])
        self.assertIn("canon", err)
        self.assertEqual(session.resolve_grant(gid, verb="canon")[0],
                         session.GRANT_GRANTED)

    def test_minting_inside_a_dispatched_lane_says_so_out_loud(self):
        with mock.patch.dict(os.environ, {"POGA_DISPATCH": "D-edbab9"}):
            _, err = self.mint_cli()
        self.assertIn("dispatched-lane", err)

    def test_an_unreachable_store_mints_nothing_and_says_nothing_was_written(self):
        with redirect_stderr(io.StringIO()) as err, self.store_unreachable():
            with self.assertRaises(SystemExit) as cm:
                session.cmd_authorize(argparse.Namespace(
                    item=None, lane=None, for_=None, ttl=None, note=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("Nothing was written", err.getvalue())


class AGrantsLifetimeIsCappedTest(GrantsBase):
    """The over-broad grant is the failure mode this design has to stay ahead of, and an
    unbounded `--ttl` is its sharpest form: `authorize --ttl 3650d` with no `--item` and
    no `--lane` is a decade-long grant over everything, with only the D6 verb carve-out
    between it and a canon write. WI-0023 named this before it happened."""

    def test_a_duration_past_the_ceiling_is_refused(self):
        with redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as cm:
                session.cmd_authorize(argparse.Namespace(
                    item=None, lane=None, for_=None, ttl="3650d", note=None))
        self.assertNotEqual(cm.exception.code, 0)
        self.assertIn("ceiling", err.getvalue())

    def test_nothing_is_minted_when_the_ceiling_refuses(self):
        """The refusal has to be inert — a grant written and then complained about would
        be worse than one written silently."""
        before = set(session._grant_list(include_expired=True))
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                session.cmd_authorize(argparse.Namespace(
                    item=None, lane=None, for_=None, ttl="3650d", note=None))
        self.assertEqual(set(session._grant_list(include_expired=True)), before)

    def test_the_boundary_is_tested_from_both_sides(self):
        """A ceiling asserted only from the far side does not pin where it actually sits."""
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            session.cmd_authorize(argparse.Namespace(
                item=None, lane=None, for_=None, ttl="7d", note=None))
        self.assertTrue(out.getvalue().strip().startswith("G-"),
                        "7d sits inside the ceiling and must mint")
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                session.cmd_authorize(argparse.Namespace(
                    item=None, lane=None, for_=None, ttl="8d", note=None))


class ARecordThatIsNotAGrantIsNotAGrantTest(GrantsBase):
    """Hardening rather than a known hole — no reachable path was found that reaches
    another coordination kind through this reader. Pinned so it stays that way."""

    def test_a_record_without_the_grant_kind_does_not_resolve(self):
        rec = self.mint()
        rec["kind"] = "claims"
        session._grant_write(rec)
        state, _ = session.resolve_grant(rec["grant_id"], verb="land")
        self.assertEqual(state, session.GRANT_NO_SUCH)


class AMintedIdNeverLandsOnALiveGrantTest(GrantsBase):
    """`_grant_write` replaces by id without asking, so a duplicate id would silently
    overwrite a live grant — the record would be gone and nothing would say so."""

    def test_minting_skips_an_id_that_is_already_taken(self):
        existing = self.mint()["grant_id"]
        taken = [existing, existing, "G-aaaaaa"]
        with mock.patch.object(session.secrets, "token_hex",
                               side_effect=[g[2:] for g in taken]):
            gid = session._grant_id()
        self.assertNotEqual(gid, existing,
                            "minting returned an id that already names a live grant")
        self.assertIsNotNone(session._grant_read(existing),
                             "the pre-existing grant must still be there")


# ══ ADR-0112 D9 — the `Authorized-By:` commit trailer (WI-0280) ══════════════════
#
# D9 is the half of this ADR that outlives the coordination store: the grant id rides
# into the commit, so *what authorized this?* is answerable from tracked history after
# `.git/poga-coord/` is gone. It had ZERO test hits when WI-0280 was filed — the trailer
# builder and its input variable were referenced nowhere in `tests/`, on a mechanism whose
# whole job is to be trustworthy afterwards.
#
# THE LAND CALL SITE IS TESTED IN `test_worktree_lane.py`, not here, because the fixture
# that can actually run `_land_worktree_lane` lives there. Deliberate split, cross-
# referenced from both sides: duplicating that fixture into this module to keep ADR-0112's
# tests in one file would be the maintained-copy shape P16 forbids.


class TheTrailerTakesNoAmbientInputTest(GrantsBase):
    """WI-0280's decision, pinned. The grant id comes from the COMMAND LINE, never from
    the environment.

    `_grant_trailer_args` used to fall through to `os.environ.get("POGA_GRANT")`. Nothing
    in the repo ever set it, so the trailer was dead on both real paths; what the fallback
    DID offer was a channel in which one `export` stamps an `Authorized-By` line onto every
    note and every land made in that shell, for as long as it stays set, including commits
    the grant was never minted for. D10 already concedes a grant proves intent and time
    rather than identity — a switch that can be left on out of band gives up the intent
    half too, and intent is the half this ADR exists to keep.

    These are the tests that fail if the fallback is put back."""

    def test_an_exported_variable_does_not_reach_the_trailer(self):
        """The behavioural half. A live, in-scope grant, named ONLY in the environment."""
        gid = self.mint(verbs=["note"])["grant_id"]
        os.environ["POGA_GRANT"] = gid
        buf = io.StringIO()
        with redirect_stderr(buf):
            args = session._grant_trailer_args(verb="note")
        self.assertEqual(args, [],
                         "an exported POGA_GRANT reached the commit trailer. The grant is "
                         "live and in scope, so this is not a resolve failure — the "
                         "environment is being read again (WI-0280).")
        self.assertEqual(buf.getvalue(), "",
                         "an unread variable must also be silent: printing a refusal for "
                         "a grant nobody cited would report a failure that did not happen")

    def test_the_builder_reads_no_environment_variable_at_all(self):
        """The CLASS half, and the one that survives a rename.

        The test above pins the one variable the defect was filed against, which leaves
        `POGA_AUTH`, `GRANT_ID` and every other spelling open — `retire-the-class-not-the-
        instance`. This asks the structural question instead: the builder's own body
        contains no read of the process environment, whatever it would be called."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(session._grant_trailer_args)))
        reads = [n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name) and n.value.id == "os"]
        self.assertEqual(reads, [],
                         f"`_grant_trailer_args` reads os.{reads} — the grant id is taken "
                         f"from `--grant` on the verb being run, never from ambient "
                         f"process state (WI-0280)")
        self.assertNotIn("environ", inspect.getsource(session._grant_trailer_args)
                         .split('"""')[-1],
                         "the builder's CODE (not its docstring) names `environ`")

    def test_a_citation_cannot_outlive_the_invocation_that_made_it(self):
        """The property that makes a module global honest where a variable was not.

        `main()` sets the citation UNCONDITIONALLY — to `""` when no `--grant` was given —
        so a second verb can never inherit the first one's grant. An environment variable
        has no equivalent: nothing clears it, which is the whole complaint."""
        session.cite_grant("G-stale")
        with mock.patch.object(session, "_report_checkout_staleness"), \
             mock.patch.object(sys, "argv", ["session.py", "wi-next"]), \
             mock.patch.object(session, "cmd_wi_next") as verb:
            session.main()
        verb.assert_called_once()
        self.assertEqual(session.cited_grant(), "",
                         "a verb run without --grant inherited the previous citation")


class TheTrailerResolvesBeforeItWritesTest(GrantsBase):
    """The docstring's load-bearing claim, which was asserted in prose and tested nowhere:
    *a named-but-unresolvable grant prints its refusal and writes nothing.*

    That is the whole design. A trailer written for a grant that does not authorize the act
    would be an unbacked provenance claim in tracked history — strictly worse than no
    trailer, because it reads as a receipt. Every non-granted state must therefore produce
    the SAME argv the harness produced before ADR-0112 existed."""

    def setUp(self):
        super().setUp()
        self.addCleanup(session.cite_grant, "")

    def build(self, gid="", **scope):
        """Return `(args, stderr)` for one trailer build."""
        buf = io.StringIO()
        with redirect_stderr(buf):
            args = session._grant_trailer_args(gid, **scope)
        return args, buf.getvalue()

    # ── state 1 of 4: no grant named ────────────────────────────────────────────
    def test_no_grant_named_writes_nothing_and_says_nothing(self):
        args, err = self.build(verb="note")
        self.assertEqual(args, [], "a commit made without a grant must be argv-identical "
                                   "to what this harness produced before ADR-0112")
        self.assertEqual(err, "", "and silent — most members never dispatch a lane")

    # ── state 2 of 4: a resolving, in-scope grant ───────────────────────────────
    def test_a_resolving_grant_writes_the_trailer_as_a_second_message(self):
        gid = self.mint(verbs=["note"])["grant_id"]
        args, err = self.build(gid, verb="note")
        self.assertEqual(args, ["-m", f"{session.GRANT_TRAILER_KEY}: {gid}"])
        self.assertEqual(err, "")

    def test_the_citation_is_the_fallback_when_no_id_is_passed(self):
        """How the id actually arrives on both real paths: neither call site passes one."""
        gid = self.mint(verbs=["note"])["grant_id"]
        session.cite_grant(gid)
        args, _ = self.build(verb="note")
        self.assertEqual(args, ["-m", f"{session.GRANT_TRAILER_KEY}: {gid}"])

    def test_an_explicit_id_beats_the_citation(self):
        explicit = self.mint(verbs=["note"])["grant_id"]
        session.cite_grant(self.mint(verbs=["note"])["grant_id"])
        args, _ = self.build(explicit, verb="note")
        self.assertEqual(args, ["-m", f"{session.GRANT_TRAILER_KEY}: {explicit}"])

    # ── state 3 of 4: found and live, but out of scope ──────────────────────────
    def test_an_out_of_scope_grant_writes_no_trailer_and_names_the_axis(self):
        """D6's carve-out reaching the commit: a `--for canon` grant does not cover a
        note, and the refusal has to be quotable back to the operator (D8)."""
        gid = self.mint(verbs=["canon"])["grant_id"]
        args, err = self.build(gid, verb="note")
        self.assertEqual(args, [], "an out-of-scope grant wrote a provenance line")
        self.assertIn(session.GRANT_OUT_OF_SCOPE, err)
        self.assertIn("'note'", err, "the refusal must name the axis that missed")
        self.assertIn(gid, err, "and the grant, so the operator can widen THAT one")

    def test_a_grant_for_another_lane_does_not_authorize_this_land(self):
        """The lane axis, which the land call site did not name until WI-0280. Without it
        a grant minted `--lane worktree-poga-1` stamped a land out of any other lane with
        a line that truthfully cited a record authorizing somebody else."""
        gid = self.mint(lanes=["worktree-poga-1"], verbs=["land"])["grant_id"]
        args, err = self.build(gid, verb="land", lane="worktree-poga-5")
        self.assertEqual(args, [])
        self.assertIn(session.GRANT_OUT_OF_SCOPE, err)
        self.assertIn("worktree-poga-1", err)

    # ── state 4 of 4: an unknown id ─────────────────────────────────────────────
    def test_an_unknown_id_writes_no_trailer_and_says_why(self):
        args, err = self.build("G-000000", verb="note")
        self.assertEqual(args, [])
        self.assertIn(session.GRANT_NO_SUCH, err)
        self.assertIn("G-000000", err)

    # ── the remaining D8 outcomes, for the same argv-identical property ─────────
    def test_a_revoked_grant_writes_no_trailer(self):
        gid = self.mint(verbs=["note"])["grant_id"]
        self.rewrite(gid, revoked_at=session._coord_iso(time.time()))
        args, err = self.build(gid, verb="note")
        self.assertEqual(args, [])
        self.assertIn(session.GRANT_REVOKED, err)

    def test_an_expired_grant_writes_no_trailer(self):
        gid = self.mint(verbs=["note"])["grant_id"]
        self.expire(gid)
        args, err = self.build(gid, verb="note")
        self.assertEqual(args, [])
        self.assertIn(session.GRANT_EXPIRED, err)

    def test_an_unreadable_store_writes_no_trailer(self):
        """The fail-closed inversion, at the commit. CANNOT TELL is refused, never folded
        into "fine" — an unreadable store must not certify a write."""
        gid = self.mint(verbs=["note"])["grant_id"]
        with self.store_unreachable():
            args, err = self.build(gid, verb="note")
        self.assertEqual(args, [])
        self.assertIn(session.GRANT_CANNOT_TELL, err)


class TheNoteCommitCarriesTheTrailerTest(GrantsBase):
    """CALL SITE 1 OF 2 — `_store_autocommit`, the single hook point every `poga work` /
    `poga ops` store write passes through (`verb="note"`).

    Driven end-to-end against a real git repo rather than asserted on the argv, because the
    argv is not the artifact: D9's claim is about what a reader finds in TRACKED HISTORY
    years later, and only the commit object can answer that. `_under_test` is lifted for
    the call — the guard exists so the suite never commits the operator's real repo, and
    ROOT here is a temp fixture."""

    def setUp(self):
        super().setUp()
        self.addCleanup(session.cite_grant, "")
        session.ROOT = self.main
        self.store = self.main / session.WI_DIRNAME
        self.store.mkdir(exist_ok=True)
        self._n = 0

    def commit_a_store_file(self, gid=""):
        """Write one store file and let `_store_autocommit` commit it. Returns stderr."""
        self._n += 1
        name = f"WI-9{self._n:03d}-fixture.md"
        (self.store / name).write_text(f"# fixture {self._n}\n", encoding="utf-8")
        session.cite_grant(gid)
        buf = io.StringIO()
        with mock.patch.object(session, "_under_test", return_value=False), \
             redirect_stdout(io.StringIO()), redirect_stderr(buf):
            session._store_autocommit(session.WI_DIRNAME, [name],
                                      f"chore(work-items): fixture {self._n}",
                                      "poga work commit")
        return buf.getvalue()

    def head_message(self):
        return _out(self.main, "log", "-1", "--format=%B")

    def trailer(self):
        """The `Authorized-By` value on HEAD, or `""`. Read with `--trailers`, which is how
        an auditor would actually ask — a substring search would pass on a body mention."""
        return _out(self.main, "log", "-1",
                    f"--format=%(trailers:key={session.GRANT_TRAILER_KEY},valueonly)").strip()

    def test_a_store_commit_without_a_grant_carries_no_trailer(self):
        self.commit_a_store_file()
        self.assertIn("fixture 1", self.head_message(),
                      "precondition, not the subject: the commit was actually made")
        self.assertEqual(self.trailer(), "")

    def test_a_resolving_grant_lands_in_the_commit_object(self):
        gid = self.mint(verbs=["note"])["grant_id"]
        self.commit_a_store_file(gid)
        self.assertEqual(self.trailer(), gid,
                         "the grant id must be readable back off the commit — that is the "
                         "whole of D9: the receipt survives the coordination store")

    def test_an_out_of_scope_grant_still_commits_but_claims_nothing(self):
        """BOTH halves matter. The write must not be lost because an authorization failed
        — the file is already on disk and `_store_autocommit` fails open by design — and
        the commit must not carry a line that says it was authorized."""
        gid = self.mint(verbs=["canon"])["grant_id"]
        err = self.commit_a_store_file(gid)
        self.assertIn("fixture 1", self.head_message(), "the commit must still be made")
        self.assertEqual(self.trailer(), "")
        self.assertIn(session.GRANT_OUT_OF_SCOPE, err, "and the refusal must be reported")

    def test_an_unknown_id_still_commits_but_claims_nothing(self):
        err = self.commit_a_store_file("G-000000")
        self.assertIn("fixture 1", self.head_message())
        self.assertEqual(self.trailer(), "")
        self.assertIn(session.GRANT_NO_SUCH, err)

    def test_an_exported_variable_does_not_reach_the_commit_object(self):
        """WI-0280's decision at the artifact, not just at the helper."""
        os.environ["POGA_GRANT"] = self.mint(verbs=["note"])["grant_id"]
        self.commit_a_store_file()
        self.assertEqual(self.trailer(), "",
                         "an exported POGA_GRANT stamped a provenance line onto a real "
                         "commit — the ambient channel is back (WI-0280)")


class TheGrantFlagIsReachableFromEveryVerbTest(GrantsBase):
    """`ship-the-detector-with-the-capability`, applied to the input side: a trailer that
    can only be cited by a verb nobody wired is a capability with no door.

    `--grant` is declared in ONE loop over `sub.choices` rather than on a hand-kept list of
    the verbs that can commit, so a verb added tomorrow cannot forget it. These tests are
    what make that claim checkable — the loop is invisible from any single verb."""

    def parser_verbs(self):
        """Every subcommand `main()` builds, taken from argparse itself rather than from a
        list in this test — `derive-a-checks-subjects-from-the-authority`."""
        captured = {}

        def spy(self_ap, *a, **kw):
            captured["ap"] = self_ap
            return real(self_ap, *a, **kw)

        real = argparse.ArgumentParser.add_subparsers
        with mock.patch.object(argparse.ArgumentParser, "add_subparsers", spy), \
             mock.patch.object(sys, "argv", ["session.py", "wi-next"]), \
             mock.patch.object(session, "_report_checkout_staleness"), \
             mock.patch.object(session, "cmd_wi_next"):
            session.main()
        return captured["ap"]._subparsers._group_actions[0].choices

    def test_every_verb_accepts_a_grant(self):
        missing = [name for name, p in self.parser_verbs().items()
                   if "--grant" not in {s for act in p._actions for s in act.option_strings}]
        self.assertEqual(missing, [],
                         f"{len(missing)} verb(s) cannot cite a grant: {missing[:8]}")

    def test_the_choke_point_cites_what_the_flag_names(self):
        """The wiring itself: `--grant` on the command line becomes the citation the
        trailer builder reads, with no per-verb plumbing in between."""
        self.addCleanup(session.cite_grant, "")
        seen = {}
        with mock.patch.object(sys, "argv",
                               ["session.py", "wi-next", "--grant", "G-abc123"]), \
             mock.patch.object(session, "_report_checkout_staleness"), \
             mock.patch.object(session, "cmd_wi_next",
                               side_effect=lambda a: seen.update(
                                   cited=session.cited_grant())):
            session.main()
        self.assertEqual(seen.get("cited"), "G-abc123",
                         "the citation must be set BEFORE the verb runs — a verb that "
                         "commits on its first line would otherwise miss it")
class AGrantSaysAtIssueWhetherALaneCanVerifyItTest(GrantsBase):
    """WI-0292 — the read side has to be RUNNABLE in the population grants exist for.

    ADR-0112's design is that a lane cites a grant instead of trusting a peer's prose, so
    `authorize check` is what makes the citation worth anything. A dispatched lane is a
    detached tmux TUI with the default permission posture and nobody attached, so a command
    matching no allow entry does not fail fast — it raises a prompt no one can answer.
    MEASURED 2026-09-05: lane poga-28 held G-47b435, could not run the check, correctly
    refused to act on an unverifiable grant, and the operator who minted it to unblock
    three lanes learned an hour later that it had unblocked none.

    The operator is standing right there at MINT. The lane is not, and has no channel back.
    That asymmetry is why the question is asked at issue time, and it is the property these
    tests pin: a grant nobody can verify has to say so while someone is still reading."""

    def setUp(self):
        super().setUp()
        # The detector legitimately reads the USER-level settings file, which on a
        # developer's machine is ambient state. Point it at a path that does not exist so
        # every case below is decided only by what the test itself wrote.
        self._save_user = session.USER_SETTINGS_PATH
        session.USER_SETTINGS_PATH = self.tmp / "nohome" / ".claude" / "settings.json"
        self.addCleanup(setattr, session, "USER_SETTINGS_PATH", self._save_user)

    def write_settings(self, path, allow=(), deny=()):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"permissions": {"allow": list(allow), "deny": list(deny)}}), encoding="utf-8")
        return path

    def project_settings(self):
        return session.ROOT / ".claude" / "settings.json"

    # ── the three answers ──────────────────────────────────────────────────────
    def test_the_wrapper_spelling_being_admitted_makes_a_grant_verifiable(self):
        self.write_settings(self.project_settings(),
                            allow=["Bash(poga authorize check:*)"])
        state, detail = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFIABLE)
        self.assertIn("poga authorize check G-abc123", detail)

    def test_the_harness_spelling_alone_also_makes_it_verifiable(self):
        """The two spellings are one capability: `poga authorize check` execs
        `session.py authorize-check` as a CHILD, and a child is never permission-checked.
        A tree admitting either one can verify a grant."""
        self.write_settings(self.project_settings(),
                            allow=["Bash(python3 session.py:*)"])
        state, _ = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFIABLE)

    def test_a_tree_admitting_neither_is_unverifiable_and_names_the_command(self):
        """This is the 2026-09-05 configuration, reconstructed: a plausible allow-list
        with no entry for either spelling."""
        self.write_settings(self.project_settings(), allow=[
            "Bash(git status:*)", "Bash(ls:*)", "Bash(poga deploy:*)"])
        state, detail = session.grant_verifiability("G-47b435")
        self.assertEqual(state, session.GRANT_UNVERIFIABLE)
        self.assertIn("G-47b435", detail)
        self.assertIn("poga authorize check", detail)

    def test_an_absent_project_settings_file_is_cannot_tell_not_a_quiet_pass(self):
        """THREE answers, never two. "I could not read the allow-list" must never render
        as "the allow-list is fine" — that collapse is the shape of the original defect,
        where silence read as success."""
        self.assertFalse(self.project_settings().exists())
        state, detail = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFY_UNKNOWN)
        self.assertIn("UNKNOWN", detail)

    def test_a_malformed_project_settings_file_is_cannot_tell_not_unverifiable(self):
        p = self.project_settings()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        state, _ = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFY_UNKNOWN)

    # ── the two ways a naive version would answer wrongly ──────────────────────
    def test_the_machine_local_permission_pile_does_not_count(self):
        """THE false-positive this check exists to avoid, and the one a naive
        implementation would walk straight into.

        `.claude/settings.local.json` is where Claude Code piles up every one-off approval
        anyone has ever clicked. It is GITIGNORED, so it does not propagate into a worktree
        — and a dispatched lane runs in a worktree. Counting it would mean the operator
        mints from a checkout where the pile makes everything look allowed, gets a clean
        bill of health, and hands the lane a grant it still cannot verify. That is the
        original defect with a check bolted on top, which is worse than no check."""
        self.write_settings(self.project_settings(), allow=["Bash(git status:*)"])
        self.write_settings(session.ROOT / ".claude" / "settings.local.json",
                            allow=["Bash(poga authorize check:*)",
                                   "Bash(python3 session.py:*)"])
        state, _ = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_UNVERIFIABLE)

    def test_a_deny_rule_beats_an_allow_rule(self):
        """Claude Code resolves deny before allow, so an allow entry shadowed by a deny
        does not make the command runnable. Reporting VERIFIABLE there would be a false
        all-clear of exactly the expensive kind."""
        self.write_settings(
            self.project_settings(),
            allow=["Bash(poga authorize check:*)", "Bash(python3 session.py:*)"],
            deny=["Bash(poga authorize check:*)", "Bash(python3 session.py:*)"])
        state, _ = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_UNVERIFIABLE)

    def test_an_unreadable_user_settings_file_keeps_the_warning_and_names_it(self):
        """A file we could not read can only ADD permissions, so the honest direction is
        to keep the warning rather than assume it would have helped — and to say which
        file was not read, instead of folding it into the same output as a clean look."""
        self.write_settings(self.project_settings(), allow=["Bash(git status:*)"])
        bad = self.tmp / "userhome" / ".claude" / "settings.json"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("{nope", encoding="utf-8")
        session.USER_SETTINGS_PATH = bad
        state, detail = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_UNVERIFIABLE)
        self.assertIn(str(bad), detail)

    def test_the_user_settings_file_can_widen_the_answer(self):
        self.write_settings(self.project_settings(), allow=["Bash(git status:*)"])
        user = self.tmp / "userhome" / ".claude" / "settings.json"
        self.write_settings(user, allow=["Bash(poga authorize check:*)"])
        session.USER_SETTINGS_PATH = user
        state, _ = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFIABLE)

    # ── the rule matcher, at its edges ─────────────────────────────────────────
    def test_the_matcher_honours_the_three_shapes_the_settings_actually_use(self):
        m = session._bash_allow_matches
        cmd = "poga authorize check G-abc123"
        self.assertTrue(m("Bash(poga authorize check:*)", cmd))
        self.assertTrue(m("Bash(poga authorize check *)", cmd))
        self.assertTrue(m("Bash(poga authorize check G-abc123)", cmd))
        self.assertFalse(m("Bash(poga authorize check G-999999)", cmd))

    def test_the_matcher_does_not_admit_a_prefix_that_stops_mid_word(self):
        """`Bash(poga auth:*)` must not admit `poga authorize check` — a prefix has to end
        on a word boundary, or a narrow-looking entry silently authorizes a wider verb."""
        self.assertFalse(session._bash_allow_matches(
            "Bash(poga auth:*)", "poga authorize check G-abc123"))

    def test_the_matcher_ignores_rules_for_other_tools(self):
        for rule in ("Edit(**)", "Write(**)", "WebFetch(domain:example.com)", "", "Bash("):
            self.assertFalse(session._bash_allow_matches(
                rule, "poga authorize check G-abc123"), rule)

    # ── the mint verb reports it ───────────────────────────────────────────────
    def mint_cli(self):
        ns = argparse.Namespace(item=None, lane=None, for_=None, ttl=None, note=None)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            session.cmd_authorize(ns)
        return out.getvalue().strip(), err.getvalue()

    def test_the_mint_verb_reports_a_verifiable_grant_as_verifiable(self):
        self.write_settings(self.project_settings(),
                            allow=["Bash(poga authorize check:*)"])
        gid, err = self.mint_cli()
        self.assertIn("verifiable", err)
        self.assertIn(gid, err)

    def test_the_mint_verb_warns_at_issue_when_no_lane_could_verify_the_grant(self):
        """The acceptance sentence of WI-0292: the operator learns it HERE, not through a
        lane's refusal an hour later."""
        self.write_settings(self.project_settings(), allow=["Bash(git status:*)"])
        gid, err = self.mint_cli()
        self.assertIn(session.GRANT_UNVERIFIABLE, err)
        self.assertIn(gid, err)

    def test_an_unverifiable_grant_is_still_minted(self):
        """Verifiability is a property of the TREE, not of the record. Refusing to mint
        would punish the operator for a settings gap they can only fix from outside this
        command — and would leave them with neither a grant nor a working verifier."""
        self.write_settings(self.project_settings(), allow=["Bash(git status:*)"])
        gid, _ = self.mint_cli()
        self.assertRegex(gid, GRANT_ID_SHAPE)
        self.assertIsNotNone(session._grant_read(gid))
        self.assertEqual(session.resolve_grant(gid, verb="close")[0],
                         session.GRANT_GRANTED)

    def test_the_cannot_tell_state_reaches_the_operator_too(self):
        gid, err = self.mint_cli()          # no settings file written at all
        self.assertIn(session.GRANT_VERIFY_UNKNOWN, err)
        self.assertRegex(gid, GRANT_ID_SHAPE)


class TheShippedFloorActuallyAdmitsTheReadSideTest(GrantsBase):
    """The detector run against the REAL artifact, not a fixture.

    Everything above proves the check reasons correctly about a settings file. This proves
    the settings file THIS REPO SHIPS is one a dispatched lane can verify a grant from —
    `verify-in-the-created-configuration`. Point it at the pre-WI-0292 allow-list and it
    goes red, which is the only reason to trust the green."""

    def test_this_repos_generated_settings_admit_a_lane_verifying_a_grant(self):
        session.ROOT = pathlib.Path(__file__).resolve().parent.parent
        state, detail = session.grant_verifiability("G-abc123")
        self.assertEqual(state, session.GRANT_VERIFIABLE, detail)

    def test_the_check_would_have_caught_the_2026_09_05_allow_list(self):
        """A DETECTOR PROVES ITSELF ON THE REAL DEFECT. Take this repo's actual generated
        settings and strip exactly what WI-0292 added; what is left is the allow-list lane
        poga-28 was run under, minus the machine-local pile that never reached it. The
        check must call that UNVERIFIABLE, or it is not detecting anything."""
        real = json.loads(
            (pathlib.Path(__file__).resolve().parent.parent /
             ".claude" / "settings.json").read_text(encoding="utf-8"))
        allow = [r for r in real["permissions"]["allow"] if "authorize" not in r]
        # `Bash(python3 session.py:*)` is a federation settings_extra, not floor — it is
        # what made the harness spelling work HERE while every member had nothing. Drop it
        # too, and this is the fleet's pre-fix position.
        allow = [r for r in allow if "session.py" not in r]
        p = session.ROOT / ".claude" / "settings.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"permissions": {
            "allow": allow, "deny": real["permissions"]["deny"]}}), encoding="utf-8")
        saved, session.USER_SETTINGS_PATH = session.USER_SETTINGS_PATH, self.tmp / "nope"
        self.addCleanup(setattr, session, "USER_SETTINGS_PATH", saved)
        state, _ = session.grant_verifiability("G-47b435")
        self.assertEqual(state, session.GRANT_UNVERIFIABLE)


if __name__ == "__main__":
    unittest.main()
