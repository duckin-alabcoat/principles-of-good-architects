"""The public cut's ADR index, checked on trunk (WI-0485).

The cut ships `public-overlay/adr/README.md` as its `adr/README.md`, beside only the ADRs
the boundary publishes, and its CI runs `check_substrate_docs.py` there ("Substrate docs
match the code"). Until this test, nothing on trunk did. On 2026-10-02 the 2026.10.3 cut
failed that step in both editions: ADR-0150 had landed with no overlay row, and withheld
ADR-0149 left a hole the overlay did not declare. Trunk's suite was green, because trunk's
own index was right; the cut lane caught it only because it ran the CI step by hand
before publishing.

So this lays out `adr/` as the cut would publish it -- `public_cut.adr_tree`, the cut's
own plan, overlays and renames -- and runs `check_substrate_docs.adr_index_findings` over
it, the same D3-D8 rules the CI step runs. Nothing about which ADRs ship or what the index
must say is restated here.

The live tree is checked in every edition, and a fixture copy of `adr/` and the overlay
is corrupted the two ways the cut actually failed, plus the withhold it failed on, to show
each one is caught and named.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import shutil
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "curate"))

import public_cut  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "check_substrate_docs", REPO / "curate" / "check_substrate_docs.py")
csd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(csd)

INDEX = "public-overlay/adr/README.md"
LABEL = f"{INDEX} (the public cut's adr/README.md)"
FIX = (f"Fix {INDEX}: give every ADR the cut publishes exactly one row, matching the "
       f"record's Status and Date; and for every ADR the manifest withholds "
       f"(curate/public_cut_manifest.json `exclude`), add `| NNNN | withheld | **Withheld.** "
       f"Spent on an ADR that is not published in this copy. |` under '## Unused numbers' "
       f"and add the number to the list in that section's lead sentence.")


def public_index_problems(root=REPO, manifest=None, reserved=None):
    """(problems, unchecked) for the overlay index against the ADRs the cut publishes."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "adr").mkdir()
        for rel, text in public_cut.adr_tree(root, manifest):
            (tmp / rel).write_text(text, encoding="utf-8")
        return csd.adr_index_findings(tmp, label=LABEL, reserved=reserved)


def _report(problems, unchecked):
    return "\n".join(["The public cut's ADR index does not match what the cut publishes; "
                      "its CI step 'Substrate docs match the code' would fail:"]
                     + [f"  - {p}" for p in problems]
                     + [f"  - COULD NOT CHECK: {u}" for u in unchecked] + [FIX])


class TheLiveOverlayIndexMatchesTheCutTest(unittest.TestCase):
    """A claim about this repo, so asserted against it: what fails here fails the cut's CI."""

    def test_every_edition(self):
        base = public_cut.load_manifest()
        for name in [public_cut.DEFAULT_EDITION] + sorted(base.get("editions", {})):
            with self.subTest(edition=name):
                problems, unchecked = public_index_problems(
                    manifest=public_cut.apply_edition(base, name))
                self.assertEqual((problems, unchecked), ([], []), _report(problems, unchecked))


@unittest.skipIf((REPO / "PUBLIC-CUT-RECEIPT.md").is_file(),
                 "public cut: public-overlay/ is not shipped (its index ships as "
                 "adr/README.md), so there is no overlay copy to break; this runs on trunk")
class MutationTest(unittest.TestCase):
    """A copy of `adr/` and the overlay, broken the ways the 2026.10.3 cut was."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        shutil.copytree(REPO / "adr", self.root / "adr")
        shutil.copytree(REPO / "public-overlay" / "adr", self.root / "public-overlay" / "adr")
        self.index = self.root / INDEX
        self.manifest = public_cut.load_manifest()

    def problems(self):
        # No reservations: a live `adr-alloc` hold must not be able to excuse a hole here.
        problems, unchecked = public_index_problems(self.root, self.manifest, reserved=set())
        self.assertEqual(unchecked, [])
        return problems

    def drop_lines(self, pred):
        lines = self.index.read_text(encoding="utf-8").splitlines(keepends=True)
        kept = [l for l in lines if not pred(l)]
        self.assertEqual(len(lines) - len(kept), 1, "the mutation must remove exactly one line")
        self.index.write_text("".join(kept), encoding="utf-8")

    def test_the_unbroken_copy_is_clean(self):
        # Without this, every failure below could be the fixture's and not the mutation's.
        self.assertEqual(self.problems(), [])

    def test_a_published_adr_with_no_overlay_row_fails_naming_it(self):
        self.drop_lines(lambda l: l.startswith("| [0150]("))
        problems = self.problems()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ADR-0150", problems[0])
        self.assertIn("no index row", problems[0])
        self.assertIn(INDEX, problems[0])
        self.assertIn("one row", _report(problems, []))

    def test_a_withheld_number_left_undeclared_fails_naming_it(self):
        self.drop_lines(lambda l: l.startswith("| 0149 |"))
        problems = self.problems()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("0149 is a hole", problems[0])
        self.assertIn("Unused numbers", problems[0])
        self.assertIn(INDEX, problems[0])
        self.assertIn("| NNNN | withheld |", _report(problems, []))

    def test_withholding_one_more_adr_is_read_from_the_manifest(self):
        # The withheld set is the manifest's, not a list in this test: withhold 0148 in the
        # boundary alone and the overlay, still indexing it, is now wrong twice over.
        self.manifest["exclude"].append(
            {"path": "adr/0148-a-land-is-a-merge.md", "why": "test: withheld"})
        problems = self.problems()
        self.assertTrue(any("a row claims ADR-0148" in p for p in problems), problems)
        self.assertTrue(any("0148 is a hole" in p for p in problems), problems)

    def test_a_renamed_adr_is_checked_under_its_public_name(self):
        # 0038 ships renamed (the manifest's `rename`), and the overlay links the new name.
        # Checked against trunk's filename, the row would read as linking the wrong file.
        names = [r for r, _t in public_cut.adr_tree(self.root, self.manifest)]
        self.assertIn("adr/0038-a-shared-resource-is-scoped-by-who-can-reach-it.md", names)
        self.assertNotIn("adr/0038-a-shared-resource-is-scoped-by-who-can-reach-it.md", names)


if __name__ == "__main__":
    unittest.main()
