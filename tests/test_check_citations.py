"""Tests for curate/check_citations.py — the WI-0342 structural guard.

The property that matters is not "the check passes today". It passes today because this
session had just re-pointed 141 citations by hand, and a check that has only ever been
seen to pass is indistinguishable from a check that cannot fail
([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)).
So each test below breaks exactly one thing in a synthetic tree and demands the single
matching finding — and, as often, demands SILENCE for the neighbouring shape that must
not fire.

The false-positive cases carry as much weight as the findings, because this check reads
prose. `csid[:8]` is a slice, `test_session.py 353` is a test count, and a bare `:349`
after `tests/test_attention.py:301` continues that file and not the one three clauses
earlier. Every one of those was a real near-miss while WI-0342 was being repaired; each
has a test here for that reason.

Two assertions are about this repo rather than a fixture, and neither reads the live
record (ADR-0148 D3: a bookkeeping-only land skips the suite, so no test may depend on
the store as it sits in the checkout):

  * `TheLiveTreeIsCleanTest` — the gate command that sweeps the live record is present
    in its blocking spelling; the sweep itself is the gate's, not the suite's.
  * `TheCheckIsInTheGateTest` — the guard is wired where something will run it. A guard
    nothing invokes is a guard that does not exist.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_citations", REPO / "curate" / "check_citations.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

#: A cited module with two functions, so symbol containment has something to be wrong about.
MODULE = '''\
"""A module."""


def alpha():
    x = 1
    return x


def beta():
    y = 2
    return y
'''
MODULE_LINES = len(MODULE.splitlines())          # 11


class Tree:
    """A synthetic repo: the three record directories plus one cited module."""

    def __init__(self, stack):
        self.root = pathlib.Path(stack.enter_context(tempfile.TemporaryDirectory()))
        for d in ("work-items", "ops-items", "adr"):
            (self.root / d).mkdir()
        (self.root / "sessionlib").mkdir()
        (self.root / "sessionlib" / "thing.py").write_text(MODULE, encoding="utf-8")
        (self.root / "session.py").write_text("import sessionlib\n", encoding="utf-8")

    def record(self, body, name="WI-0001-x.md", where="work-items"):
        (self.root / where / name).write_text(body, encoding="utf-8")
        return self

    def run(self, symbols=False):
        old, cc.ROOT = cc.ROOT, self.root
        try:
            return cc.run(symbols=symbols)
        finally:
            cc.ROOT = old
            cc.reset_caches()


class _Base(unittest.TestCase):
    def setUp(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.tree = Tree(self.stack)


class ACitationPastTheEndOfItsFileFailsTest(_Base):
    """D1. The defect WI-0342 was filed for, in one line."""

    def test_a_line_past_eof_is_a_finding(self):
        self.tree.record(f"see sessionlib/thing.py:{MODULE_LINES + 900} for the cause\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f"sessionlib/thing.py:{MODULE_LINES + 900}", problems[0])
        self.assertIn(f"{MODULE_LINES} lines long", problems[0])
        self.assertEqual(checked, 1)

    def test_the_last_real_line_is_not_a_finding(self):
        """The boundary itself: off-by-one here would fail every honest citation."""
        self.tree.record(f"see sessionlib/thing.py:{MODULE_LINES} for the cause\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual(problems, [])
        self.assertEqual(checked, 1)

    def test_a_range_is_judged_on_its_END(self):
        self.tree.record(f"see sessionlib/thing.py:1-{MODULE_LINES + 5}\n")
        problems, _adv, _unres, _c = self.tree.run()
        self.assertEqual(len(problems), 1, problems)


class AnUnresolvablePathIsNotAFindingTest(_Base):
    """A citation this repo cannot check is reported as unchecked — never as fine."""

    def test_a_file_the_repo_lacks_is_counted_separately(self):
        self.tree.record("see other/repo/thing.py:99999 over there\n")
        problems, _adv, unresolvable, checked = self.tree.run()
        self.assertEqual(problems, [])
        self.assertEqual(len(unresolvable), 1, unresolvable)
        self.assertIn("other/repo/thing.py", unresolvable[0])
        self.assertEqual(checked, 0, "an unresolvable citation must not count as checked")

    def test_a_bare_module_name_resolves_through_sessionlib(self):
        """`thing.py:3` in prose means sessionlib/thing.py — that is how the corpus writes it."""
        self.tree.record("see thing.py:3 for the cause\n")
        problems, _adv, unresolvable, checked = self.tree.run()
        self.assertEqual((problems, unresolvable), ([], []))
        self.assertEqual(checked, 1)


class AMissingCorpusDirectoryCannotBeCheckedTest(_Base):
    """Exit 2, not 0. A sweep that never ran must not report a clean sweep."""

    def test_a_missing_record_directory_raises(self):
        (self.tree.root / "adr").rmdir()
        with self.assertRaises(cc.CannotCheck) as caught:
            self.tree.run()
        self.assertIn("adr/", str(caught.exception))


class ABareContinuationInheritsTheNearestPathTest(_Base):
    """The form that bites: re-point one half of a line and it names two files at once."""

    def test_a_bare_reference_is_checked_against_the_preceding_path(self):
        self.tree.record(f"`alpha` (sessionlib/thing.py:4), `beta` (:{MODULE_LINES + 40})\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual(checked, 2)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn(f":{MODULE_LINES + 40}", problems[0])

    def test_a_bare_reference_inherits_the_NEAREST_path_not_the_first(self):
        """A real near-miss: `:349` after tests/test_attention.py:301 is not session.py's."""
        self.tree.record(
            f"sessionlib/thing.py:4 and later other/far.py:301 and :{MODULE_LINES + 70}\n")
        problems, _adv, unresolvable, _c = self.tree.run()
        self.assertEqual(problems, [], "the bare ref belongs to other/far.py, which we lack")
        self.assertEqual(len(unresolvable), 2, unresolvable)

    def test_a_leading_bare_reference_with_nothing_to_inherit_is_ignored(self):
        self.tree.record(":4321 is not a citation, it has no file\n")
        problems, _adv, unresolvable, checked = self.tree.run()
        self.assertEqual((problems, unresolvable, checked), ([], [], 0))


class ProseThatOnlyLooksLikeACitationIsIgnoredTest(_Base):
    """Every one of these was a live false positive during the WI-0342 repair."""

    def test_a_python_slice_is_not_a_citation(self):
        self.tree.record("sessionlib/thing.py:4 renders it as `csid[:8]` — eight chars\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual(problems, [])
        self.assertEqual(checked, 1, "only the real citation counts; the slice is not one")

    def test_a_count_after_a_filename_is_not_a_citation(self):
        """`session.py 12` in 'tests/ 14 of 80, session.py 12' is a count of blockers."""
        self.tree.record("blockers: tests/ 14 of 80, sessionlib/thing.py 12, curate/ 2\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual((problems, checked), ([], 0))

    def test_a_time_is_not_a_citation(self):
        self.tree.record("sessionlib/thing.py:4 was read at 14:32 today\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual((problems, checked), ([], 1))


class TheApproximateFormIsStillAnAddressTest(_Base):
    """`thing.py ~4863` hedges the number; it does not stop being checkable."""

    def test_a_tilde_address_past_eof_is_a_finding(self):
        self.tree.record(f"`alpha` (sessionlib/thing.py ~{MODULE_LINES + 12}) does it\n")
        problems, _adv, _unres, _c = self.tree.run()
        self.assertEqual(len(problems), 1, problems)

    def test_a_tilde_address_in_range_is_not(self):
        self.tree.record("`alpha` (sessionlib/thing.py ~4) does it\n")
        problems, _adv, _unres, checked = self.tree.run()
        self.assertEqual((problems, checked), ([], 1))


class TheSymbolProbeIsAdvisoryAndOffByDefaultTest(_Base):
    """D2 reports when asked and never gates — measured wrong often enough to matter."""

    def test_a_citation_inside_the_wrong_function_is_silent_by_default(self):
        self.tree.record("`alpha` (sessionlib/thing.py:10) does it\n")   # 10 is in beta
        problems, advisories, _unres, _c = self.tree.run()
        self.assertEqual(problems, [])
        self.assertEqual(advisories, [], "D2 must not fire unless it was asked for")

    def test_the_same_citation_is_reported_when_asked(self):
        self.tree.record("`alpha` (sessionlib/thing.py:10) does it\n")
        problems, advisories, _unres, _c = self.tree.run(symbols=True)
        self.assertEqual(problems, [], "an advisory is never a problem")
        self.assertEqual(len(advisories), 1, advisories)
        self.assertIn("alpha", advisories[0])

    def test_a_citation_inside_the_named_function_is_silent(self):
        self.tree.record("`alpha` (sessionlib/thing.py:5) does it\n")
        _p, advisories, _unres, _c = self.tree.run(symbols=True)
        self.assertEqual(advisories, [])

    def test_a_symbol_that_is_merely_nearby_does_not_anchor(self):
        """The 29-of-50 failure: the sentence's SUBJECT is not the cited code."""
        self.tree.record(
            "`alpha` carries an age guard: sessionlib/thing.py:10 filters the status\n")
        _p, advisories, _unres, _c = self.tree.run(symbols=True)
        self.assertEqual(advisories, [], "only an IMMEDIATELY adjacent symbol may anchor")


class SymbolSpansTest(_Base):
    """D2's spans must end where the function does, or containment means nothing."""

    def test_each_function_gets_its_own_span(self):
        old, cc.ROOT = cc.ROOT, self.tree.root
        try:
            cc.reset_caches()
            spans = cc.symbol_spans("sessionlib/thing.py")
        finally:
            cc.ROOT = old
            cc.reset_caches()
        self.assertIn("alpha", spans)
        self.assertIn("beta", spans)
        self.assertLess(spans["alpha"][1], spans["beta"][0])

    def test_a_name_defined_twice_anchors_nothing(self):
        p = self.tree.root / "sessionlib" / "twice.py"
        p.write_text("def dup():\n    pass\n\n\ndef dup():\n    pass\n", encoding="utf-8")
        old, cc.ROOT = cc.ROOT, self.tree.root
        try:
            cc.reset_caches()
            spans = cc.symbol_spans("sessionlib/twice.py")
        finally:
            cc.ROOT = old
            cc.reset_caches()
        self.assertNotIn("dup", spans, "an ambiguous symbol must abstain, not pick one")


class EveryRecordDirectoryIsSweptTest(_Base):
    """ops-items/ and adr/ are records too — WI-0342's own count came from all three."""

    def test_a_finding_in_adr_is_found(self):
        self.tree.record(f"see sessionlib/thing.py:{MODULE_LINES + 3}\n",
                         name="0001-x.md", where="adr")
        problems, _adv, _unres, _c = self.tree.run()
        self.assertEqual(len(problems), 1, problems)

    def test_a_finding_in_ops_items_is_found(self):
        self.tree.record(f"see sessionlib/thing.py:{MODULE_LINES + 3}\n",
                         name="OPS-0001-x.md", where="ops-items")
        problems, _adv, _unres, _c = self.tree.run()
        self.assertEqual(len(problems), 1, problems)


class TheLiveTreeIsCleanTest(unittest.TestCase):
    """The repair itself, pinned — by the GATE, not by this suite.

    This test used to sweep the live record here (`cc.run()` against the real ROOT).
    ADR-0148 / WI-0427 retired that: a bookkeeping-only land skips the suite, so a
    suite test whose verdict depends on `work-items/`, `ops-items/` or `comms/` as they
    sit in the checkout could go red with no code land to blame, and the store guard
    fails it by name. The claim "this repo's records resolve" is still made on every
    land — by the gate command `check_citations.py --check`, which reads the live tree
    because that is its job. What the suite can hold is that the command is there, in
    its blocking spelling; the finding logic is held by the synthetic trees above."""

    def test_the_gate_sweeps_the_live_record_in_its_blocking_spelling(self):
        cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
        cmds = [" ".join(c) if isinstance(c, list) else c for c in (cfg.get("gate") or [])]
        sweeping = [c for c in cmds if "check_citations.py" in c]
        self.assertTrue(sweeping, f"nothing in the gate sweeps the live record: {cmds}")
        for c in sweeping:
            self.assertNotIn("--status", c, "--status always exits 0; it cannot block a land")
            self.assertNotIn("--symbols", c, "the symbol probe is advisory (D2)")


class TheCheckIsInTheGateTest(unittest.TestCase):
    """Wired, or it does not exist."""

    def test_the_configured_gate_runs_the_citation_check(self):
        cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
        cmds = [" ".join(c) if isinstance(c, list) else c for c in (cfg.get("gate") or [])]
        self.assertTrue(
            any("check_citations.py" in c for c in cmds),
            f"nothing in the gate runs the citation check: {cmds}")

    def test_the_record_directories_are_gate_inputs(self):
        """ADR-0117: a land whose diff touches only unmeasured paths SKIPS the gate.

        The check reads adr/, so an ADR-only land would otherwise skip the very gate
        that would have caught it.
        """
        src = (REPO / "curate" / "gate_inputs.py").read_text(encoding="utf-8")
        for d in cc.CORPUS_DIRS:
            self.assertIn(f'"{d}/"', src,
                          f"{d}/ is read by the citation check but is not a declared "
                          f"gate input, so a {d}-only land would skip the gate")


if __name__ == "__main__":
    unittest.main()
