"""Tests for `curate/scrub.py`, and specifically for `redact` (WI-0229).

`scrub` had no test file of its own. `gate` and `findings` were exercised incidentally
through `tests/test_outbox.py`, which is enough while the only caller is a refusal — a
refusal that misses something fails safe, by leaving the author to notice. `redact` does
not have that property: it is the one entry point whose OUTPUT is trusted and forwarded,
so a class it handles wrongly does not stall, it travels.

THE LOAD-BEARING TEST IS `TheRedactorLeavesNothingBehind`, and it derives its subjects
from `scrub.PATTERNS` rather than from a list written here
([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)).
A pattern added to `_SPECS` next year is covered the day it lands, and a redactor that
learned to miss it fails this file without anyone remembering to come back.

THE DEFECT IT WAS WRITTEN FROM is recorded because it was found by running the code and
not by reading it. The first `redact` masked every match in place, which is right for a
pattern that matches the datum and actively harmful for one that matches a LABEL:
`api_key = hunter2` came back as `[[xpi_kxy =]] hunter2`, the secret intact one character
to the right, and `findings` then reported the result CLEAN. A redactor that converts a
catchable secret into an uncatchable one is worse than no redactor, because the gate
behind it stops firing.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "curate"))
import scrub  # noqa: E402


#: One synthetic line per class, keyed by the class name in `scrub.PATTERNS`. Every class
#: must appear here — `test_every_pattern_class_has_a_sample` is what makes that true
#: rather than hoped, so a new pattern cannot be added without a sample to prove it.
SAMPLES = {
    "private-ip": "listening on 192.168.4.22 now",
    "overlay-ip": "peer at 100.101.102.103 responded",
    "mac-address": "iface aa:bb:cc:dd:ee:ff up",
    "credential": "api_key = hunter2hunter2",
    "ssn": "subject 123-45-6789 filed",
    "phone": "call (555) 555-0123 back",
    "street-address": "at 1234 Maple Street today",
    "wifi-secret": "psk: correcthorsebattery",
    "dob": "date of birth 1970-01-01",
}


class TheRedactorLeavesNothingBehind(unittest.TestCase):

    def test_every_pattern_class_has_a_sample(self):
        """The coverage below is only as wide as this map, so the map is checked against
        the authority instead of trusted. Without this, adding a pattern silently narrows
        every other test in the class."""
        self.assertEqual(sorted(SAMPLES), sorted(c for c, _p, _w in scrub.PATTERNS))

    def test_a_redacted_sample_is_clean_to_the_gate_that_follows_it(self):
        """The property the whole design rests on: `outbox.post` scrubs AFTER `redact`
        runs, so anything `redact` fails to neutralise hits a closed door — and anything
        it neutralises must actually pass. Both halves are this one assertion."""
        for cls, sample in SAMPLES.items():
            with self.subTest(cls=cls):
                out, counts = scrub.redact(sample)
                self.assertEqual(counts.get(cls), 1, f"{cls} was not redacted at all")
                self.assertEqual(scrub.findings(out), [],
                                 f"{cls} survived redaction in a form the gate still "
                                 f"refuses: {out!r}")

    def test_a_marker_class_takes_the_whole_line(self):
        """`api_key = hunter2` must not become `[[xpi_kxy =]] hunter2`. This is the
        measured defect, pinned in the shape it actually occurred."""
        out, _ = scrub.redact("api_key = hunter2hunter2")
        self.assertNotIn("hunter2hunter2", out)
        self.assertIn("line removed", out)

    def test_a_value_class_keeps_its_line_readable(self):
        """The other half, and the reason marker-vs-value is a distinction rather than a
        blanket line-drop: an address masked in place leaves the sentence around it
        useful, and a diagnosis made of removed lines diagnoses nothing."""
        out, _ = scrub.redact("listening on 192.168.4.22 now")
        self.assertNotIn("192.168.4.22", out)
        self.assertIn("listening on", out)
        self.assertIn("now", out)

    def test_the_allowlist_is_honoured(self):
        """Loopback and documentation ranges read like PII to a regex and are not. A
        redactor that mangled them would train its callers to route around it."""
        out, counts = scrub.redact("bound to 127.0.0.1 and 0.0.0.0")
        self.assertEqual(counts, {})
        self.assertEqual(out, "bound to 127.0.0.1 and 0.0.0.0")

    def test_clean_text_is_returned_unchanged(self):
        text = "nothing here but ordinary prose, exit code 0, pid 4242"
        out, counts = scrub.redact(text)
        self.assertEqual(out, text)
        self.assertEqual(counts, {})

    def test_counts_are_per_class_and_cumulative(self):
        out, counts = scrub.redact("a 192.168.4.1 b 10.1.2.3 c aa:bb:cc:dd:ee:ff")
        self.assertEqual(counts["private-ip"], 2)
        self.assertEqual(counts["mac-address"], 1)
        self.assertEqual(scrub.findings(out), [])

    def test_multi_line_input_keeps_its_line_structure(self):
        """Callers hand this whole log tails; a redactor that reflowed them would make the
        evidence harder to read than the thing it was protecting."""
        out, _ = scrub.redact("one\ntwo\nthree")
        self.assertEqual(out.splitlines(), ["one", "two", "three"])

    def test_empty_input_is_not_an_error(self):
        self.assertEqual(scrub.redact(""), ("", {}))


class TheRefusalPathIsUnchanged(unittest.TestCase):
    """`redact` is an addition, not a loosening. The gate that guards authored content
    still refuses rather than quietly cleaning it up behind the author's back."""

    def test_findings_still_reports_a_raw_secret(self):
        hits = scrub.findings("api_key = hunter2hunter2")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].cls, "credential")

    def test_a_finding_never_reproduces_the_value_it_found(self):
        hits = scrub.findings("listening on 192.168.4.22 now")
        self.assertNotIn("192.168.4.22", hits[0].render())


if __name__ == "__main__":
    unittest.main()
