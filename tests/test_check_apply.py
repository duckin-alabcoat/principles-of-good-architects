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

import importlib.util
import pathlib
import sys
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
