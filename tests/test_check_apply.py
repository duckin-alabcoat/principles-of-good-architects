"""Tests for curate/check-apply.py — the ADR-0049 apply-mode delivery guard.

The load-bearing property: a redistribution brief must declare a valid apply mode
before delivery — `apply: auto` with a complete strict-schema header, or
`apply: manual` with a non-empty `manual-reason:`. A reason-less manual brief (the
habit-manual default the policy removes) and a brief with no declared mode both fail.
Per ADR-0050 the manual partition is total: a non-attended manual brief routes to
the R3 runner and must carry a `verify:` command; `manual-reason: attended` is the
reserved escape hatch that surfaces to operator with no verify required.

stdlib unittest: python3 -m unittest discover -s tests
"""

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_apply", ROOT / "curate" / "check-apply.py")
check_apply = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_apply)


def _check(text):
    """Write a brief to a temp file and return its offence list."""
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / "brief.md"
        p.write_text(text, encoding="utf-8")
        return check_apply.check_file(str(p))


AUTO_OK = (
    "---\n"
    "edit-id: 2026-07-15-x\n"
    "target-file: architect.md\n"
    "expected-base-version: v1.0.0\n"
    "proposed-new-version: v1.1.0\n"
    "apply: auto\n"
    "---\n\n## op: version-bump\n"
)


class ApplyModeTest(unittest.TestCase):

    def test_auto_full_header_is_clean(self):
        self.assertEqual(_check(AUTO_OK), [])

    def test_auto_missing_required_field_fails(self):
        text = AUTO_OK.replace("target-file: architect.md\n", "")
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("target-file", offences[0])

    def test_manual_agent_path_with_verify_is_clean(self):
        # ADR-0050: a non-attended manual brief routes to the R3 runner and must
        # carry a verify command.
        text = ("---\n"
                "edit-id: 2026-07-15-y\n"
                "apply: manual\n"
                "manual-reason: multi-file migration\n"
                "verify: python3 -m pytest tests/\n"
                "---\n\nbody\n")
        self.assertEqual(_check(text), [])

    def test_manual_agent_path_without_verify_fails(self):
        # ADR-0050: non-attended manual with no verify is an unowned-gap brief.
        text = ("---\n"
                "edit-id: 2026-07-15-y\n"
                "apply: manual\n"
                "manual-reason: multi-file migration\n"
                "---\n\nbody\n")
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("verify", offences[0])

    def test_manual_attended_without_verify_is_clean(self):
        # ADR-0050: `attended` is the reserved escape hatch → surfaces to operator, no
        # verify required.
        text = ("---\n"
                "edit-id: 2026-07-15-y\n"
                "apply: manual\n"
                "manual-reason: attended\n"
                "---\n\nbody\n")
        self.assertEqual(_check(text), [])

    def test_manual_attended_is_case_insensitive(self):
        text = ("---\n"
                "apply: manual\n"
                "manual-reason: Attended\n"
                "---\n\nbody\n")
        self.assertEqual(_check(text), [])

    def test_manual_reason_routing_contract(self):
        cases = (("attended", "attended"), ("Attended", "attended"),
                 ("attended — target-side judgment", "refuse"),
                 ("Attended because another checkout is involved", "refuse"),
                 ("attended\tfor review", "refuse"),
                 ("multi-file migration", "agent"))
        for reason, route in cases:
            for verify in ("", "verify: /usr/bin/true\n"):
                with self.subTest(reason=reason, verify=bool(verify)):
                    text = ("---\napply: manual\nmanual-reason: " + reason +
                            "\nattended-because: target-side judgment\n" +
                            verify + "---\n\nbody\n")
                    offences = _check(text)
                    if route == "attended":
                        self.assertEqual(offences, [])
                    elif route == "refuse":
                        self.assertEqual(len(offences), 1)
                        self.assertIn("headless adopt-runner", offences[0])
                        self.assertIn("would route", offences[0])
                        self.assertIn("manual-reason: attended", offences[0])
                    elif verify:
                        self.assertEqual(offences, [])
                    else:
                        self.assertTrue(offences)
                        self.assertIn("verify", offences[0])

    def test_manual_with_blank_verify_fails(self):
        text = ("---\n"
                "apply: manual\n"
                "manual-reason: substrate install\n"
                "verify:   \n"
                "---\n\nbody\n")
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("verify", offences[0])

    def test_manual_without_reason_fails(self):
        text = ("---\n"
                "edit-id: 2026-07-15-y\n"
                "apply: manual\n"
                "---\n\nbody\n")
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("manual-reason", offences[0])

    def test_manual_with_blank_reason_fails(self):
        text = ("---\n"
                "apply: manual\n"
                "manual-reason:   \n"
                "---\n\nbody\n")
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("manual-reason", offences[0])

    def test_no_frontmatter_fails(self):
        text = "# A prose brief\n\n**Apply:** manual\n\nbody\n"
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("frontmatter", offences[0])

    def test_frontmatter_without_apply_field_fails(self):
        text = "---\nedit-id: z\n---\n\nbody\n"
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("apply", offences[0])

    def test_unrecognized_apply_mode_fails(self):
        text = "---\napply: sometimes\n---\n\nbody\n"
        offences = _check(text)
        self.assertTrue(offences)
        self.assertIn("sometimes", offences[0])

    def test_apply_value_case_insensitive(self):
        text = AUTO_OK.replace("apply: auto", "apply: AUTO")
        self.assertEqual(_check(text), [])


class VerifyContractTest(unittest.TestCase):
    """WI-0304 B1, delivery half — a `verify:` that cannot report a verdict is refused
    before it is delivered, not discovered after sweep upon sweep of failures.

    The rule is deliberately narrow: a bare `assert` in a `python -c` one-liner can only
    ever emit `AssertionError`, naming neither what was checked nor what was absent, and
    that is provable from the text without running anything. Everything wider than
    provable is the RUNNER's job, at runtime, where the answer is real."""

    # A synthetic setup brief's `verify:` line (the tool `gizmo` and its verbs are
    # invented), in the shape whose failures were undiagnosable night after night.
    ENROLLMENT_VERIFY = (
        'python3 -c "import json;c=json.load(open(\'session.config.json\'));'
        "m=json.load(open('.mcp.json'));h=c['settings_extras']['hooks'];"
        "assert m['mcpServers']['gizmo']['command']=='gizmo-server';"
        "assert any('gizmo release' in x['command'] for e in h['SessionEnd'] for x in e['hooks']);"
        "assert any('gizmo prune' in x['command'] for e in h['SessionStart'] for x in e['hooks']);"
        'print(\'gizmo setup wired\')"'
    )

    def _manual(self, verify):
        return _check("---\nedit-id: x\napply: manual\nmanual-reason: substrate install\n"
                      f"verify: {verify}\n---\n\nbody\n")

    def test_the_setup_brief_shape_would_now_be_refused_at_delivery(self):
        offences = self._manual(self.ENROLLMENT_VERIFY)
        self.assertEqual(len(offences), 1, offences)
        self.assertIn("3 bare `assert`", offences[0])
        self.assertIn("WI-0304", offences[0])

    def test_the_same_asserts_with_messages_pass(self):
        self.assertEqual(self._manual(
            'python3 -c "assert a, \'.mcp.json has no example-tool server\'; '
            "assert b, 'SessionEnd hook missing example-tool down'\""), [])

    def test_legitimate_silent_verifies_are_not_touched(self):
        """These print nothing on failure and are the GOOD commands — self-describing,
        one word, no interpreter. A rule that caught them would be worse than the defect."""
        for cmd in ("test -f adopted.txt",
                    "grep -q example-tool .mcp.json",
                    "python3 session.py apply-briefs --dry-run",
                    "python3 -m unittest discover -s tests",
                    "python3 tests/check_enrollment.py --strict",
                    "make verify && test -f out"):
            self.assertEqual(self._manual(cmd), [], f"false positive on: {cmd}")

    def test_a_bare_assert_is_caught_even_chained_behind_another_command(self):
        self.assertTrue(self._manual("grep -q example-tool .mcp.json && python3 -c 'assert x'"))

    def test_an_unparseable_command_line_is_refused_not_silently_cleared(self):
        """`declare-what-a-check-assumes`: a command that does not tokenize has NOT been
        checked and must never report the same 'clean' as one that has."""
        offences = self._manual("python3 -c \"assert 1, 'oops")
        self.assertEqual(len(offences), 1, offences)
        self.assertIn("does not tokenize", offences[0])

    def test_a_one_liner_that_is_not_valid_python_is_refused(self):
        offences = self._manual("python3 -c 'this is ('")
        self.assertEqual(len(offences), 1, offences)
        self.assertIn("not valid Python", offences[0])

    def test_an_attended_brief_is_not_held_to_the_contract(self):
        """`attended` surfaces to operator and needs no verify at all, so there is nothing
        here to hold to a contract about failure output."""
        self.assertEqual(_check("---\nedit-id: x\napply: manual\nmanual-reason: attended\n"
                                "---\n\nbody\n"), [])

    def test_an_auto_brief_is_not_held_to_the_contract(self):
        self.assertEqual(_check(AUTO_OK), [])


class ManualAdoptionLedgerTest(unittest.TestCase):
    """Each `apply: manual` brief the lint checks is recorded in the manual-adoption
    ledger (curate/adoption_metrics.py), pass or refuse; an auto brief is not. The
    ledger is repointed at a temp file, so nothing here touches the checkout's state."""

    MANUAL_OK = ("---\nedit-id: 2026-10-02-m\napply: manual\n"
                 "manual-reason: substrate install\nverify: test -f x\n---\n\n"
                 "## Why\n\ntext\n\n## op: replace\n")
    MANUAL_BAD = "---\nedit-id: 2026-10-02-bad\napply: manual\n---\n\nbody\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        self.log = self.dir / "manual-adoptions.jsonl"
        self.assertIsNotNone(check_apply.adoption_metrics, "the ledger module did not load")
        patcher = unittest.mock.patch.object(check_apply.adoption_metrics, "log_path",
                                             lambda: self.log)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _brief(self, name, text):
        p = self.dir / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def _main(self, *paths):
        with unittest.mock.patch.object(sys, "argv", ["check-apply.py", *paths]), \
                contextlib.redirect_stdout(io.StringIO()):
            return check_apply.main()

    def _ledger(self):
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]

    def test_a_manual_brief_is_recorded_with_category_shape_time_and_retries(self):
        self.assertEqual(self._main(self._brief("m", self.MANUAL_OK)), 0)
        [rec] = self._ledger()
        self.assertEqual(rec["source"], "check-apply")
        self.assertEqual(rec["brief"], "2026-10-02-m")
        self.assertEqual(rec["result"], "ok")
        self.assertEqual(rec["reason_category"], "substrate-install")
        self.assertEqual(rec["op_shape"]["kind"], "mixed")
        self.assertEqual(rec["op_shape"]["ops"], {"replace": 1})
        self.assertGreaterEqual(rec["elapsed_s"], 0)
        self.assertEqual(rec["retries"], 0)

    def test_a_refused_manual_brief_is_recorded_and_rechecks_count_as_retries(self):
        path = self._brief("bad", self.MANUAL_BAD)
        self.assertEqual(self._main(path), 1)
        self.assertEqual(self._main(path), 1, "recording must not change the verdict")
        recs = self._ledger()
        self.assertEqual([r["result"] for r in recs], ["refused", "refused"])
        self.assertEqual([r["retries"] for r in recs], [0, 1])
        self.assertEqual(recs[0]["reason_category"], "unset")

    def test_an_auto_brief_is_not_a_manual_adoption(self):
        self.assertEqual(self._main(self._brief("a", AUTO_OK)), 0)
        self.assertEqual(self._ledger(), [])

    def test_an_unwritable_ledger_leaves_the_verdict_alone(self):
        self.log.mkdir()
        self.assertEqual(self._main(self._brief("m", self.MANUAL_OK)), 0)
        self.assertEqual(self._main(self._brief("bad", self.MANUAL_BAD)), 1)


if __name__ == "__main__":
    unittest.main()
