"""Tests for poga_evidence.py — the one rule behind WI-0371.

Six sites cut a captured verdict to a window and handed the window on as if it were
the whole thing. A tail keeps the END of the output, and when a run fails the part
that says WHY is frequently at the HEAD — so the surviving window is a biased sample
that reads as "no errors."

The contract these tests hold the module to:

  1. Text that fits comes back BYTE-IDENTICAL, so the absence of a marker is itself a
     claim a reader may rely on. Everything else is downstream of this one.
  2. A cut always announces itself, on the side the text was lost from.
  3. `limit` buys evidence, never marker overhead.
  4. Cuts COMPOSE — the far end of a three-hop chain reports the loss against the
     ORIGINAL size, not against whatever the previous hop handed it.

stdlib unittest: python3 -m unittest discover -s tests
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import poga_evidence as evidence  # noqa: E402


def _body(lines=200):
    return "".join("line %d of the captured output\n" % i for i in range(1, lines + 1))


class FitsUnchangedTest(unittest.TestCase):
    """The load-bearing half. If a complete text could come back marked, a reader who
    sees no marker has learned nothing, and every other guarantee here is decoration."""

    def test_short_text_is_returned_identically(self):
        self.assertEqual(evidence.clip("short", 100), "short")

    def test_text_exactly_at_the_limit_is_not_marked(self):
        text = "x" * 50
        self.assertEqual(evidence.clip(text, 50), text)
        self.assertFalse(evidence.was_cut(evidence.clip(text, 50)))

    def test_empty_and_none_are_empty(self):
        self.assertEqual(evidence.clip("", 10), "")
        self.assertEqual(evidence.clip(None, 10), "")

    def test_a_complete_text_reports_no_cut(self):
        self.assertFalse(evidence.was_cut(_body()))
        self.assertIsNone(evidence.describe_cut(_body()))


class MarkerSideTest(unittest.TestCase):
    """The marker goes where the loss is, so a reader meets it BEFORE the sentence
    that starts mid-token rather than inferring the cut from the wreckage."""

    def test_a_tail_keep_announces_the_loss_above_the_window(self):
        out = evidence.clip(_body(), 40, keep="tail")
        self.assertTrue(out.startswith("[EVIDENCE CUT:"))
        self.assertEqual(evidence.describe_cut(out)["side"], "above")

    def test_a_head_keep_announces_the_loss_below_the_window(self):
        out = evidence.clip(_body(), 40, keep="head")
        self.assertTrue(out.rstrip().endswith("]"))
        self.assertEqual(evidence.describe_cut(out)["side"], "below")

    def test_the_marker_names_both_the_loss_and_the_original_size(self):
        full = _body()
        out = evidence.clip(full, 100, keep="tail")
        note = evidence.describe_cut(out)
        self.assertEqual(note["total"], len(full))
        self.assertEqual(note["dropped"], len(full) - 100)
        self.assertEqual(note["unit"], "chars")


class BudgetBuysEvidenceTest(unittest.TestCase):
    """`limit` counts the kept window only. A budget the marker ate into would quietly
    shrink the evidence every time this module got more honest."""

    def test_the_kept_window_is_the_full_limit(self):
        full = _body()
        out = evidence.clip(full, 100, keep="tail")
        window = out.split("\n", 1)[1]
        self.assertEqual(window, full[-100:])

    def test_line_budgets_keep_exactly_that_many_lines(self):
        out = evidence.clip(_body(), 3, keep="tail", unit="lines")
        self.assertEqual(out.splitlines()[1:], _body().splitlines()[-3:])


class ComposesAcrossHopsTest(unittest.TestCase):
    """The failure this module exists for. `curate/adopt-runner.py` cut the same text
    at three separate hops, so the record at the far end was a tail of a tail of a
    tail and nothing anywhere said so. A tail-cut applied to a HEAD-marked string
    would drop the marker itself — restoring the exact silence."""

    def test_three_hops_report_the_original_total_not_the_previous_hop(self):
        full = _body()
        hop1 = evidence.clip(full, 400, keep="tail")
        hop2 = evidence.clip(hop1, 200, keep="tail")
        hop3 = evidence.clip(hop2, 80, keep="tail")
        note = evidence.describe_cut(hop3)
        self.assertEqual(note["total"], len(full))
        self.assertEqual(note["dropped"], len(full) - 80)
        # The number a naive re-cut would have reported instead.
        self.assertNotEqual(note["total"], 400)

    def test_a_hop_that_takes_nothing_more_still_carries_the_marker(self):
        """The loss is real; it just did not happen at this hop. Dropping the marker
        here is how `last_detail = detail[:400]` used to launder a 300-char cut into
        something that looked complete."""
        full = _body()
        hop1 = evidence.clip(full, 400, keep="tail")
        hop2 = evidence.clip(hop1, 99999, keep="tail")
        self.assertTrue(evidence.was_cut(hop2))
        self.assertEqual(evidence.describe_cut(hop2)["total"], len(full))

    def test_a_marker_shaped_string_in_the_middle_is_content_not_ours(self):
        """Some other tool's evidence quoted inside this one. Re-basing our arithmetic
        on somebody else's number would fabricate a total."""
        inner = "before\n[EVIDENCE CUT: 9 of 99 chars above]\nafter"
        self.assertIsNone(evidence.describe_cut(inner))
        out = evidence.clip(inner, 10, keep="tail")
        self.assertEqual(evidence.describe_cut(out)["total"], len(inner))


class OneLineTest(unittest.TestCase):
    """For a field whose contract is that a reader gets exactly one line — a verdict,
    a `detail` rendered inside backticks in a comms note."""

    def test_the_result_has_no_newline(self):
        out = evidence.clip(_body(), 60, keep="tail", one_line=True)
        self.assertNotIn("\n", out)
        self.assertTrue(evidence.was_cut(out))

    def test_a_line_budget_survives_collapsing(self):
        out = evidence.clip(_body(), 1, keep="tail", unit="lines", one_line=True)
        self.assertNotIn("\n", out)
        self.assertEqual(evidence.describe_cut(out)["total"], 200)
        self.assertEqual(evidence.describe_cut(out)["dropped"], 199)

    def test_a_single_line_that_fits_is_untouched(self):
        out = evidence.clip("only one line", 1, keep="tail", unit="lines", one_line=True)
        self.assertEqual(out, "only one line")
        self.assertFalse(evidence.was_cut(out))

    def test_a_one_line_hop_keeps_an_earlier_hops_true_total(self):
        full = _body()
        hop1 = evidence.clip(full, 400, keep="tail")
        hop2 = evidence.clip(hop1, 50, keep="tail", one_line=True)
        self.assertNotIn("\n", hop2)
        self.assertEqual(evidence.describe_cut(hop2)["total"], len(full))


class RouteATest(unittest.TestCase):
    """Acceptance route (a): the full text lives beside the record and the record names
    the file. One argument, not a second mechanism."""

    def test_the_marker_names_the_file_holding_the_whole_text(self):
        out = evidence.clip(_body(), 20, full_text_at="/var/log/x.log")
        self.assertEqual(evidence.describe_cut(out)["full_text_at"], "/var/log/x.log")
        self.assertIn("full text in /var/log/x.log", out)


class RejectsNonsenseTest(unittest.TestCase):
    def test_bad_keep(self):
        with self.assertRaises(ValueError):
            evidence.clip("x", 1, keep="middle")

    def test_bad_unit(self):
        with self.assertRaises(ValueError):
            evidence.clip("x", 1, unit="words")

    def test_negative_limit(self):
        with self.assertRaises(ValueError):
            evidence.clip("x", -1)


if __name__ == "__main__":
    unittest.main()
