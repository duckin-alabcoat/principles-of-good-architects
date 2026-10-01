"""WI-0333 — a lane is told the trunk's suite is RED before it gates and queues.

THE DEFECT, stated once. `gate-inputs.json` has carried `suite_ok` since schema 1 and
OPS-0009 refreshes it nightly, but both readers of it — `_gate_input_prefixes` here and
`load()` in `curate/gate_inputs.py` — fold a RED into "no usable record, gate
everything". That collapse is correct for their question (may this diff skip the suite?)
and it destroys the answer to a different one (is the trunk broken before I build on
it?). So a red trunk was discoverable only by running the gate and reading a failure:
on 2026-09-10 two lanes an hour apart each spent the same diagnosis on the same
trunk-wide breakage, neither knowing the other was on it
(`sessions/journal/20260910T1201Z-devbox-66f9.md`, corroborated by `…-7e58.md`).

WHAT EACH TEST PINS, and each fails differently:

  A. (ADR-0148 D5: at land this is now the trunk check's RED, not the record's; the
     record's `suite_ok` still reaches the session-start banner.)
     `suite_ok: false` on the trunk reaches the LANE, in its own land output, BEFORE
     the gate runs. The end-to-end one, and the item's acceptance in as many words.
  B. A record the trunk does not carry is its OWN state — never green. The
     `declare-what-a-check-assumes` floor: "I could not tell" and "I checked and it's
     fine" must not share an output.
  C. A green too old to speak for today's trunk is also UNKNOWN. The stale-green case
     is the one a two-state design gets wrong silently, because it looks like evidence.
  D. The three states never share a rendering, so no consumer can read one as another.
  E. Advisory, never a refusal — a red trunk is exactly when every lane must still be
     able to land, because one of them is landing the fix.
  F. The state is read from the TRUNK REF, not from the lane's own tree, which is at the
     lane's base commit and answers an older question.
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_land_is_a_merge import GateNeutralBase, FIXTURE_RECORD, _git  # noqa: E402
import session  # noqa: E402


def _fresh(**over) -> dict:
    """The fixture record, dated now, so a GREEN assertion is about the verdict rather
    than about how long ago the file was written. `FIXTURE_RECORD`'s own `derived_at` is
    a fixed 2026-09-05 literal, which ages past the TTL as the calendar moves — a test
    that relied on it would go green today and start reporting UNKNOWN later, for a
    reason having nothing to do with the code."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"
    return dict(FIXTURE_RECORD, derived_at=stamp, **over)


class TrunkSuiteVerdictBase(GateNeutralBase):
    def _put_on_trunk(self, record: dict | None, msg: str = "test: trunk record") -> None:
        """Write (or delete) `gate-inputs.json` ON THE TRUNK and commit it there.

        The trunk is the subject, so the fixture has to move the trunk's copy — writing
        it into the lane would pin the lane's answer to its own working tree, which is
        precisely the read this function exists not to make."""
        target = self.main / "gate-inputs.json"
        if record is None:
            target.unlink(missing_ok=True)
        else:
            target.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", msg)
        # The lane must carry the trunk's commit or its land is a rebase rather than the
        # fast-forward this fixture's other assertions assume.
        _git(self.lane, "rebase", "-q", "main")


class ALaneSeesARedTrunkTest(TrunkSuiteVerdictBase):
    """Property A — the red one, and the item's acceptance."""

    def test_a_red_trunk_reaches_the_lane_before_the_gate_runs(self):
        """ADR-0148 D5 moved the land's red-trunk news from the nightly record's
        `suite_ok` to the per-land trunk check: the land prints `trunk_check_line()`
        before the gate report. The record's verdict still reaches the session-start
        banner (`test_the_session_start_orientation_carries_the_line`)."""
        self._put_on_trunk(_fresh(suite_ok=False))
        self._journal_only_lane()
        red_tip = self._trunk_tip()
        session._trunk_check_write({"state": "red", "trunk": "main", "tip": red_tip,
                                    "failing": ["FAIL: test_x (t.T)"],
                                    "checked_at": time.time()})
        outcome, gate_calls, out = self._land_watching_the_gate()
        self.assertIn("trunk check: RED", out,
                      "the lane was never told the trunk's own suite had failed:\n" + out)
        # BEFORE, not merely somewhere. The whole cost this item exists to stop is a
        # gate and a queue wait spent before the news arrives.
        self.assertLess(out.index("trunk check: RED"), out.index("gate:"),
                        "the warning arrived after the gate report:\n" + out)
        self.assertEqual(gate_calls, 1)
        self.assertTrue(outcome, "a bookkeeping land must go through a red trunk:\n" + out)

    def test_a_red_trunk_does_not_refuse_the_land(self):
        """Property E. A refusal here would wedge every lane at the moment one of them
        is landing the repair. The nightly record's RED never refuses; the per-land
        trunk check (ADR-0148 D5) refuses CODE lands only, pinned in
        tests/test_land_is_a_merge.py."""
        self._put_on_trunk(_fresh(suite_ok=False))
        self._journal_only_lane()
        before = self._trunk_tip()
        outcome, _, out = self._land_watching_the_gate()
        self.assertTrue(outcome, "a red trunk blocked a land it must only warn:\n" + out)
        self.assertNotEqual(before, self._trunk_tip(), "the trunk must have advanced")

    def test_the_session_start_orientation_carries_the_line(self):
        """The other half of "before it queues": before it BUILDS. A lane that learns at
        start costs nothing to tell and may never reach a land at all."""
        self._put_on_trunk(_fresh(suite_ok=False))
        self.assertIn("trunk RED", session.trunk_suite_line())


class CannotTellIsItsOwnStateTest(TrunkSuiteVerdictBase):
    """Properties B and C — the collapse this whole neighbourhood keeps producing."""

    def test_an_absent_record_is_unknown_and_never_green(self):
        self._put_on_trunk(None, msg="test: remove the record")
        state, detail = session.trunk_suite_verdict()
        self.assertEqual(state, session.TRUNK_SUITE_UNKNOWN)
        self.assertNotIn(session.TRUNK_SUITE_GREEN, session.trunk_suite_line())
        self.assertIn("nothing has measured", detail)
        self.assertIn("curate/gate_inputs.py --derive", detail,
                      "an UNKNOWN with no remedy is a complaint, not a check")
        self.assertIn("OPS-0009", detail,
                      "name the obligation, or a reader cannot tell a lapsed nightly "
                      "from a member that never had one")

    def test_an_unparseable_record_is_unknown(self):
        (self.main / "gate-inputs.json").write_text("{not json", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "test: corrupt record")
        self.assertEqual(session.trunk_suite_verdict()[0], session.TRUNK_SUITE_UNKNOWN)

    def test_a_record_with_no_measured_verdict_is_unknown(self):
        """`suite_ok` absent, or present as something other than a measured bool. The
        shape check is what stands in for the schema pin this reader deliberately does
        not make."""
        absent = _fresh()
        absent.pop("suite_ok")
        cases = {
            "suite_ok absent": absent,
            "suite_ok not a bool": _fresh(suite_ok="true"),
            "suite_ok null": _fresh(suite_ok=None),
            "tests zero": _fresh(tests=0),
            "tests not an int": _fresh(tests="3742"),
        }
        for name, record in cases.items():
            with self.subTest(case=name):
                self._put_on_trunk(record, msg=f"test: {name}")
                self.assertEqual(session.trunk_suite_verdict()[0],
                                 session.TRUNK_SUITE_UNKNOWN)

    def test_a_green_older_than_the_ttl_is_unknown_not_green(self):
        """Property C, and the case a two-state reader gets wrong while looking right.

        A green measured at a commit nobody has re-measured in days is not a statement
        about today's trunk. Reporting it as GREEN would rebuild the false all-clear one
        layer above the one this reader exists to expose."""
        self._put_on_trunk(_fresh())
        self.assertEqual(session.trunk_suite_verdict()[0], session.TRUNK_SUITE_GREEN)
        later = time.time() + session.TRUNK_SUITE_TTL_SECONDS + 3600
        state, detail = session.trunk_suite_verdict(now=later)
        self.assertEqual(state, session.TRUNK_SUITE_UNKNOWN)
        self.assertIn("not evidence about today's trunk", detail)

    def test_a_red_older_than_the_ttl_is_still_red(self):
        """The asymmetry, stated so it cannot be "tidied" into symmetry later.

        A stale green stops being evidence; a stale red is the newest thing anyone
        knows, and it says nobody has re-measured a trunk that was broken. Telling a
        lane to go and check costs a minute; a false all-clear costs the diagnosis."""
        self._put_on_trunk(_fresh(suite_ok=False))
        later = time.time() + session.TRUNK_SUITE_TTL_SECONDS + 3600
        self.assertEqual(session.trunk_suite_verdict(now=later)[0],
                         session.TRUNK_SUITE_RED)


class TheStatesAreDistinctTest(TrunkSuiteVerdictBase):
    """Property D — three renderings, no two of which a reader could confuse."""

    def test_each_state_renders_differently(self):
        self._put_on_trunk(_fresh())
        green = session.trunk_suite_line()
        self._put_on_trunk(_fresh(suite_ok=False), msg="test: red")
        red = session.trunk_suite_line()
        self._put_on_trunk(None, msg="test: gone")
        unknown = session.trunk_suite_line()
        self.assertEqual(len({green, red, unknown}), 3)
        for line in (green, red, unknown):
            self.assertTrue(line.startswith("suite:   trunk "), line)
        self.assertIn("GREEN", green)
        self.assertIn("RED", red)
        self.assertIn("UNKNOWN", unknown)
        self.assertNotIn("GREEN", unknown)
        self.assertNotIn("GREEN", red)


class ReadFromTheTrunkTest(TrunkSuiteVerdictBase):
    """Property F — the subject is the trunk, not the tree the lane is standing in."""

    def test_the_lanes_own_stale_copy_does_not_answer(self):
        """A lane branched before a break carries the pre-break record on disk. Reading
        the file rather than the ref would report that lane's base commit's answer as
        the trunk's — a wrong answer shaped like a right one, and older every hour the
        lane stays open."""
        self._put_on_trunk(_fresh(suite_ok=False))
        # The lane keeps a GREEN copy in its own working tree; the trunk says RED.
        (self.lane / "gate-inputs.json").write_text(
            json.dumps(_fresh(), indent=2) + "\n", encoding="utf-8")
        self.assertEqual(session.trunk_suite_verdict()[0], session.TRUNK_SUITE_RED,
                         "the reader answered from the lane's working tree")

    def test_an_unresolvable_trunk_is_unknown(self):
        self.assertEqual(session.trunk_suite_verdict(trunk="no-such-branch")[0],
                         session.TRUNK_SUITE_UNKNOWN)


def _night(outcome: str = "red", hours_ago: float = 1.0, **over) -> dict:
    """A nightly runner status, shaped like `.session-state/gate-inputs-runner.status.json`."""
    finished = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - hours_ago * 3600))
    return dict({"finished": finished + "+00:00", "outcome": outcome,
                 "reason": "suite RED — 2 failure(s), 1 error(s) over 6288 tests",
                 "failing_total": 3,
                 "failing": ["FAIL test_a.A.test_one", "FAIL test_a.A.test_two",
                             "ERROR test_b.B.test_three"]}, **over)


class ARedNightOutranksAnOlderGreenRecordTest(TrunkSuiteVerdictBase):
    """WI-0431. A red night lands no record, so the trunk's record keeps the previous
    green. On 2026-09-25 and 09-26 the start line said "trunk GREEN" over two red nights."""

    def setUp(self):
        super().setUp()
        # Dated well before any night below, but inside the TTL, so GREEN is the answer
        # the record alone would give.
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S",
                              time.gmtime(time.time() - 20 * 3600)) + "+00:00"
        self._put_on_trunk(dict(FIXTURE_RECORD, derived_at=stamp))
        self.assertEqual(session.trunk_suite_verdict(nightly=None)[0],
                         session.TRUNK_SUITE_GREEN)

    def test_a_red_night_newer_than_the_record_is_red_and_names_the_tests(self):
        state, detail = session.trunk_suite_verdict(nightly=_night())
        self.assertEqual(state, session.TRUNK_SUITE_RED)
        self.assertIn("test_a.A.test_one", detail)
        self.assertIn("ERROR test_b.B.test_three", detail)
        self.assertIn(session.NIGHTLY_STATUS_REL, detail)

    def test_the_names_are_capped_and_the_rest_counted(self):
        names = [f"FAIL t.T.test_{i}" for i in range(12)]
        detail = session.trunk_suite_verdict(
            nightly=_night(failing=names, failing_total=114))[1]
        self.assertIn("test_4", detail)
        self.assertNotIn("test_5", detail)
        self.assertIn("+109 more", detail, "the count is the verdict's, not the list's")

    def test_a_red_night_with_no_names_is_still_red(self):
        state, detail = session.trunk_suite_verdict(
            nightly=_night(failing=[], failing_total=None))
        self.assertEqual(state, session.TRUNK_SUITE_RED)
        self.assertIn("did not record which tests", detail)

    def test_a_red_night_older_than_the_record_was_answered_by_it(self):
        self.assertEqual(session.trunk_suite_verdict(nightly=_night(hours_ago=30))[0],
                         session.TRUNK_SUITE_GREEN)

    def test_a_night_with_no_verdict_does_not_turn_the_record_red(self):
        for outcome in ("refused", "error", "green"):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    session.trunk_suite_verdict(nightly=_night(outcome=outcome))[0],
                    session.TRUNK_SUITE_GREEN)

    def test_the_default_reads_the_main_checkouts_status_file(self):
        """The wiring: with no `nightly` passed, the reader finds the file the runner
        writes, in the MAIN checkout, from inside a lane."""
        status = self.main / session.NIGHTLY_STATUS_REL
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(json.dumps(_night()), encoding="utf-8")
        self.assertIn("trunk RED", session.trunk_suite_line())


class StartWiringTest(unittest.TestCase):
    """The call site. Every test above stays green if the line is never wired into the
    banner — the regression shape that left `_auto_reap_lanes` dead for a day
    (ADR-0089), and the shape this whole item is about: a correct measurement with no
    consumer. `cmd_start` runs only under a real hook payload, so the one thing a unit
    test can pin here is that the banner's own function still calls it."""

    def test_cmd_start_emits_the_trunk_suite_line(self):
        self.assertIn("trunk_suite_line()", inspect.getsource(session.cmd_start))

    def test_every_lander_gets_it_before_the_gate(self):
        """In `_land_gate` (ADR-0148; it replaced the deleted `_gate_unless_neutral`),
        which is the one function all three landers pass through on the way to the gate
        and the queue — so no lander can be given the warning and another left without
        it (the ADR-0117 D4 known-gap shape). The line is the trunk check's now (D5)."""
        src = inspect.getsource(session._land_gate)
        self.assertIn("trunk_check_line()", src)
        for call in ("run(frozenset())", "run(suite_keys)"):
            self.assertLess(src.index("trunk_check_line()"), src.index(call),
                            f"the warning must be emitted before {call}, not after")


class TheStatusVerbNamesTheRedTest(unittest.TestCase):
    """The sibling site ([`retire-the-class-not-the-instance`]).

    `curate/gate_inputs.py`'s `load()` folds a RED into None for the same good reason
    the land's reader does — for *its* question, a red measurement is as unusable as
    none. But `--status` is the verb a person types to ask whether the trunk is healthy,
    and answering "NO USABLE RECORD" to that throws away the one measurement that
    answers it. Fixing the land and leaving this is the instance-not-class trap: the
    symptom that would have led anyone here is exactly the one the land's fix removes."""

    def setUp(self):
        import contextlib
        import io
        import tempfile
        from curate import gate_inputs

        self.gate_inputs = gate_inputs
        self.contextlib, self.io = contextlib, io
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self._record = gate_inputs.RECORD
        gate_inputs.RECORD = self.tmp / session.GATE_INPUTS_RECORD
        self.addCleanup(lambda: setattr(gate_inputs, "RECORD", self._record))

    def _status(self) -> tuple[int, str]:
        buf = self.io.StringIO()
        argv = sys.argv
        sys.argv = ["gate_inputs.py", "--status"]
        try:
            with self.contextlib.redirect_stdout(buf):
                code = self.gate_inputs.main()
        finally:
            sys.argv = argv
        return code, buf.getvalue()

    def test_a_red_record_says_red_and_not_no_usable_record(self):
        self.gate_inputs.RECORD.write_text(
            json.dumps(dict(FIXTURE_RECORD, suite_ok=False)), encoding="utf-8")
        code, out = self._status()
        self.assertEqual(code, 1)
        self.assertIn("RED", out)
        self.assertNotIn("NO USABLE RECORD", out,
                         "the one measurement that answers the question was discarded "
                         "as unusable:\n" + out)

    def test_an_absent_record_still_says_no_usable_record(self):
        """The control. The red branch must not swallow the case it sits in front of."""
        code, out = self._status()
        self.assertEqual(code, 1)
        self.assertIn("NO USABLE RECORD", out)
        self.assertNotIn("RED", out)


if __name__ == "__main__":
    unittest.main()
