"""WI-0208 / ADR-0136 — the standard section splits into two delivered tiers.

The split is a DELIVERY decision, not an authority one, and the failure it can produce is
specific: an injected tier that names a reference file no member actually has. CANON.md
has been telling nearly every member repo that the registries are its source of truth while
`push-substrate` shipped neither registry, so this is not a hypothetical failure mode —
it is the one already running in the same building. The manifest tests below are the ones
that matter most.

stdlib unittest: python3 -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from curate import standardize  # noqa: E402


def _push_substrate():
    sys.path.insert(0, str(ROOT / "curate"))
    spec = importlib.util.spec_from_file_location("ps", ROOT / "curate" / "push-substrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SplitTiersTest(unittest.TestCase):
    """The parser itself. Each case is a way the marker could be misread."""

    def test_a_marked_section_goes_to_reference_and_an_unmarked_one_stays(self):
        injected, reference = standardize.split_tiers(
            "## Kept\nrule text\n\n## Moved\n<!-- tier: reference -->\n\nlookup text\n")
        self.assertIn("## Kept", injected)
        self.assertIn("rule text", injected)
        self.assertNotIn("## Moved", injected)
        self.assertIn("## Moved", reference)
        self.assertIn("lookup text", reference)
        self.assertNotIn("## Kept", reference)

    def test_the_marker_never_reaches_either_output(self):
        """It is an instruction to the generator, not content. A leaked marker would be
        the second thing every reader asked about."""
        injected, reference = standardize.split_tiers(
            "## Moved\n<!-- tier: reference -->\n\nbody\n")
        self.assertNotIn(standardize.TIER_MARKER, injected)
        self.assertNotIn(standardize.TIER_MARKER, reference)

    def test_stripping_the_marker_leaves_the_heading_a_blank_line(self):
        """The marker occupies the line under the heading. Take it away without putting a
        blank back and the first paragraph welds itself to the `##`, which renders as a
        heading swallowing its own text."""
        _, reference = standardize.split_tiers(
            "## Moved\n<!-- tier: reference -->\nbody\n")
        self.assertIn("## Moved\n\nbody", reference)

    def test_the_preamble_above_the_first_heading_is_always_injected(self):
        """It frames the file a member reads first, and it has no heading to mark."""
        injected, reference = standardize.split_tiers(
            "front matter\n\n## Moved\n<!-- tier: reference -->\n\nbody\n")
        self.assertIn("front matter", injected)
        self.assertNotIn("front matter", reference)

    def test_a_marked_section_takes_its_subsections_with_it(self):
        """A `##` section runs to the next `##`. If `###` closed it, half a moved section
        would stay behind and the reader would get an orphaned subsection in each file."""
        injected, reference = standardize.split_tiers(
            "## Moved\n<!-- tier: reference -->\n\n### Inner\ndetail\n\n## Kept\nrule\n")
        self.assertIn("### Inner", reference)
        self.assertIn("detail", reference)
        self.assertNotIn("### Inner", injected)
        self.assertIn("## Kept", injected)

    def test_a_heading_inside_a_fence_is_not_a_heading(self):
        """`standard-source.md` carries fenced markdown examples containing `## …`.
        Reading one as a section boundary would split a section mid-example."""
        injected, reference = standardize.split_tiers(
            "## Kept\n```markdown\n## Not a heading\n```\nstill kept\n")
        self.assertIn("still kept", injected)
        self.assertEqual(reference, "")

    def test_an_unmarked_source_puts_everything_in_the_injected_tier(self):
        """The pre-split behaviour, unchanged, for a member or a fixture with no markers."""
        body = "## One\na\n\n## Two\nb\n"
        injected, reference = standardize.split_tiers(body)
        self.assertEqual(injected, body)
        self.assertEqual(reference, "")


class CheckCoversTheNewTierTest(unittest.TestCase):
    def test_check_is_stale_when_only_the_reference_copy_is_wrong(self):
        """The new failure mode. Before the split there was one body to compare; a
        --check that kept comparing only STANDARD.md would certify a stale reference
        file green forever."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text("## Kept\nrule\n\n## Moved\n<!-- tier: reference -->\n\nlookup\n")
            inj, ref = root / "STANDARD.md", root / "STANDARD-REFERENCE.md"
            with mock.patch.object(standardize, "ROOT", root), \
                 mock.patch.object(standardize, "SRC", source), \
                 mock.patch.object(standardize, "TARGETS",
                                   [(inj, "federation", "injected"),
                                    (ref, "federation", "reference")]):
                inj.write_text(standardize.build("federation", "injected"))
                ref.write_text(standardize.build("federation", "reference"))
                with mock.patch("sys.argv", ["standardize", "--check"]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    standardize.main()  # in sync: returns without raising

                ref.write_text("drifted")
                out = io.StringIO()
                with mock.patch("sys.argv", ["standardize", "--check"]), \
                     contextlib.redirect_stdout(out):
                    with self.assertRaises(SystemExit) as raised:
                        standardize.main()
                self.assertNotEqual(raised.exception.code, 0)
                self.assertIn("STANDARD-REFERENCE.md", out.getvalue())


class TheReferenceTierIsDeliveredTest(unittest.TestCase):
    """The one thing the shortlist said must not be got wrong."""

    def test_the_reference_file_is_in_the_push_manifest(self):
        self.assertIn("STANDARD-REFERENCE.md", _push_substrate().BYTE_IDENTICAL)

    def test_the_reference_file_is_introduced_where_absent(self):
        """STANDARD.md is REFRESH_ONLY because a CANON-floor repo deliberately has none.
        The reference tier must NOT inherit that: a member with STANDARD.md and no
        STANDARD-REFERENCE.md is a dangling pointer, not a floor choice."""
        self.assertNotIn("STANDARD-REFERENCE.md", _push_substrate().REFRESH_ONLY)

    def test_a_fresh_member_gets_it_at_seed_not_at_the_next_push(self):
        text = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
        self.assertEqual(text.count('("STANDARD-REFERENCE.md",'), 2,
                         "both COPY_PLAN and ADOPT_COPY_PLAN must carry it")
        self.assertTrue((ROOT / "bootstrap-kit" / "STANDARD-REFERENCE.md").is_file(),
                        "the kit must hold the file the copy plans name")

    def test_a_member_missing_it_is_reportable(self):
        import standard_check
        self.assertIn("standard-reference", standard_check.CAPABILITIES)
        _, detector, scope = standard_check.CAPABILITIES["standard-reference"]
        self.assertEqual(scope, "all")
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self.assertFalse(detector(repo, {}))
            # WI-0375: this used to write "x" and assert the detector said yes, which
            # is the defect stated as an assertion — presence was the whole test, so a
            # stub at the end of the pointer was indistinguishable from the reference
            # tier. The detector reads CONTENT now, so the delivered file is what has
            # to satisfy it, and a stub is what must not.
            (repo / "STANDARD-REFERENCE.md").write_text("x")
            self.assertFalse(detector(repo, {}),
                             "a stub is a dangling pointer that happens to resolve")
            (repo / "STANDARD-REFERENCE.md").write_bytes(
                (ROOT / "STANDARD-REFERENCE.md").read_bytes())
            self.assertTrue(detector(repo, {}))

    def test_it_is_a_new_release_and_not_a_floor_addition(self):
        """required_set walks RELEASES cumulatively and 1.0.0 is what makes a member
        BELOW-FLOOR rather than merely behind. In the floor, this key would flip every
        existing member to below-floor the moment it landed, for a file none of them can
        have yet."""
        import standard_check
        floor = dict(standard_check.RELEASES)["1.0.0"]["add"]
        self.assertNotIn("standard-reference", floor)
        self.assertIn("standard-reference", standard_check.required_set(standard_check.LATEST))

    def test_the_injected_tier_names_the_reference_file(self):
        """If the pointer were dropped, the reference tier would be delivered and
        invisible — worse than not splitting at all."""
        self.assertIn("STANDARD-REFERENCE.md",
                      (ROOT / "STANDARD.md").read_text(encoding="utf-8"))


class TheSetFitsItsCeilingTest(unittest.TestCase):
    def test_the_injected_set_is_under_its_declared_ceiling(self):
        """The item's acceptance, asserted on the real files rather than on paper. This
        is what was false for 25 of the 28 days before ADR-0136."""
        sys.path.insert(0, str(ROOT / "curate"))
        from metrics import CANON_BUDGET_CHARS, CANON_FILES
        total = sum(len((ROOT / n).read_bytes()) for n in CANON_FILES)
        self.assertLessEqual(total, CANON_BUDGET_CHARS,
                             f"injected set is {total} of {CANON_BUDGET_CHARS}")

    def test_the_reference_tier_is_not_counted_against_the_ceiling(self):
        """Delivered-not-injected is the whole mechanism. If STANDARD-REFERENCE.md were
        ever added to CANON_FILES the split would save nothing and nobody would notice
        until the next consolidation pass."""
        sys.path.insert(0, str(ROOT / "curate"))
        from metrics import CANON_FILES
        self.assertNotIn("STANDARD-REFERENCE.md", CANON_FILES)
        self.assertTrue((ROOT / "STANDARD-REFERENCE.md").is_file())


class TheGateRunsTheBudgetCheckTest(unittest.TestCase):
    def test_the_configured_gate_runs_the_canon_budget_check(self):
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        self.assertTrue(any("check_canon_budget.py" in c for c in cfg["gate"]),
                        "ADR-0137: the only code that knows the ceiling must run at land time")

    def test_the_other_checks_are_still_there(self):
        """Add, never replace — the control this repo puts on every gate-list change."""
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        joined = " ".join(cfg["gate"])
        for cmd in ("wi-check", "run_suite.py", "distill.py --check",
                    "standardize.py --check", "gen_settings.py --check",
                    "check_substrate_docs.py --check", "check_citations.py --check"):
            self.assertIn(cmd, joined)


if __name__ == "__main__":
    unittest.main()
