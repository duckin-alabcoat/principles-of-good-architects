"""Tests for curate/check-brief.py — the pre-delivery self-containment guard.

This guard gates every brief the federation delivers into another Architect's
inbox (ADR-0031), and it had **no test file** until 2026-07-30 — which is how a
false positive survived: the bare-§ rule fired on a brief quoting *the target member's own*
spec sections, a document the target resolves better than we do. An untested
guard's failure mode is not "it misses things", it is "it blocks correct work and
someone rewrites correct content to appease it."

Both directions are asserted here, because a guard that only ever passes is
indistinguishable from a guard that is switched off:

  - it CATCHES a federation-internal path and a bare federation §-section;
  - it PERMITS a §-citation qualified by a document the target can resolve — an
    ADR, or a named spec (including linked-ADR-then-§ and sub-section forms).

stdlib unittest: python3 -m unittest tests.test_check_brief
"""

import importlib.util
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_brief", ROOT / "curate" / "check-brief.py")
check_brief = importlib.util.module_from_spec(_spec)
sys.modules["check_brief"] = check_brief
_spec.loader.exec_module(check_brief)


class CheckBriefTests(unittest.TestCase):
    def offences(self, body):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "brief.md"
            path.write_text(body, encoding="utf-8")
            return check_brief.check_file(path)

    def hits(self, body):
        return [hit for _lineno, _line, hit in self.offences(body)]

    # --- the guard must catch these -------------------------------------------------

    def test_catches_a_federation_internal_path(self):
        self.assertIn("bootstrap-kit/", self.hits("Copy the template from bootstrap-kit/foo.md.\n"))

    def test_catches_federation_repo_root_prose(self):
        self.assertTrue(self.hits("Run it from the federation repo root.\n"))

    def test_catches_a_bare_section_reference(self):
        """The original point of the rule: §11 alone maps to nothing on the target."""
        self.assertIn("role-doc §-section ref", self.hits("Update §11 of your role doc.\n"))

    def test_reports_the_offending_line_number(self):
        offences = self.offences("clean line\nanother clean line\nsee §4 please\n")
        self.assertEqual([lineno for lineno, _l, _h in offences], [3])

    # --- the guard must permit these ------------------------------------------------

    def test_permits_an_adr_section_citation(self):
        self.assertEqual(self.hits("Per ADR-0024 §4 the section is generated.\n"), [])

    def test_permits_a_linked_adr_then_section(self):
        """A markdown link collapses to its text, so the § stays ADR-attached."""
        self.assertEqual(self.hits("Per [ADR-0024](../adr/0024-x.md) §4, it is generated.\n"), [])

    def test_permits_a_named_spec_section(self):
        """The 2026-07-30 false positive: a member's own spec, quoted back at that member."""
        self.assertEqual(self.hits("Block reserved per example-tool spec §5.1.\n"), [])

    def test_permits_a_second_person_spec_section(self):
        self.assertEqual(self.hits("Your spec §11 pilot gate reads five sessions.\n"), [])

    def test_permits_a_clean_brief(self):
        self.assertEqual(self.hits("# Notification\n\nNothing forbidden here.\n"), [])

    # --- the exemption must not swallow the rule ------------------------------------

    def test_spec_exemption_does_not_permit_an_unrelated_bare_section(self):
        """A qualified citation on the same line must not launder a bare one."""
        body = "Per example-tool spec §5.1, then update §11 of your role doc.\n"
        self.assertIn("role-doc §-section ref", self.hits(body))

    def test_unreadable_file_is_reported_not_silently_clean(self):
        """"Couldn't read it" must never present as "checked and fine"."""
        offences = check_brief.check_file(pathlib.Path("/nonexistent-brief-probe-9f2a.md"))
        self.assertTrue(offences)


if __name__ == "__main__":
    unittest.main()
