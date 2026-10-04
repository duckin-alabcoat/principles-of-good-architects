"""Tests for curate/adoption_metrics.py — the manual-adoption ledger.

The adoption half (curate/adopt-runner.py) is driven end-to-end in
tests/test_adopt_runner_e2e.py and the delivery half in tests/test_check_apply.py; this
module pins the pure pieces both rely on and the ledger's write discipline:
append-only, machine-local (never a tracked file), and best-effort.

stdlib unittest: python3 -m unittest discover -s tests
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
import adoption_metrics as am  # noqa: E402


class ReasonCategoryTest(unittest.TestCase):

    def test_the_vocabulary_process_md_names(self):
        for reason, want in (("attended", "attended"), ("Attended", "attended"),
                             ("substrate install", "substrate-install"),
                             ("multi-file migration", "migration"),
                             ("requires target-side judgment", "judgment")):
            self.assertEqual(am.reason_category(reason), want, reason)

    def test_reasons_the_archive_actually_holds(self):
        """Taken from manual-reason lines in this repo's briefs, not invented."""
        for reason, want in (
                ("attended — a one-line repair in your own tree", "attended"),
                ("informational — a file-format change, no role-doc edit", "informational"),
                ("a routing answer plus a verified finding; nothing here edits your role doc",
                 "informational"),
                ("three of these are yours to decide and one is a value only your machine "
                 "holds", "judgment"),
                ("an ingest of 180 MB across four directories", "migration")):
            self.assertEqual(am.reason_category(reason), want, reason)

    def test_empty_is_unset_and_anything_else_is_other(self):
        self.assertEqual(am.reason_category(""), "unset")
        self.assertEqual(am.reason_category(None), "unset")
        self.assertEqual(am.reason_category("because"), "other")


class OpShapeTest(unittest.TestCase):

    HDR = "---\napply: manual\nmanual-reason: x\n---\n"

    def test_a_prose_brief(self):
        shape = am.op_shape(self.HDR + "\n## Context\n\ntext\n\n## Ask\n\ndo it\n")
        self.assertEqual(shape["kind"], "prose")
        self.assertEqual(shape["ops"], {})
        self.assertEqual(shape["prose_sections"], 2)

    def test_a_manual_brief_made_only_of_strict_ops(self):
        """The case the ledger exists to find: manual, but the engine could have taken it."""
        shape = am.op_shape(self.HDR + "## op: version-bump\n## op: replace\n~~~before\na\n"
                            "~~~\n~~~after\nb\n~~~\n## op: replace\n")
        self.assertEqual(shape["kind"], "ops")
        self.assertEqual(shape["ops"], {"replace": 2, "version-bump": 1})
        self.assertEqual(shape["op_count"], 3)

    def test_mixed(self):
        shape = am.op_shape(self.HDR + "## Why\n\n## op: create-file\nPath: x\n")
        self.assertEqual(shape["kind"], "mixed")
        self.assertEqual(shape["ops"], {"create-file": 1})

    def test_frontmatter_is_not_counted_as_body(self):
        self.assertEqual(am.op_shape(self.HDR)["body_bytes"], 0)


class LedgerTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = pathlib.Path(self._tmp.name) / "sub" / "manual-adoptions.jsonl"

    def _rec(self, brief="b1", source="check-apply"):
        return am.entry(source=source, stamp="2026-10-02T00:00:00Z", brief=brief,
                        header={"apply": "manual", "manual-reason": "attended"},
                        text="---\napply: manual\n---\n", result="ok",
                        elapsed_s=0.01234, retries=0)

    def test_append_only_and_one_json_object_per_line(self):
        self.assertTrue(am.record(self._rec("b1"), path=self.log))
        self.assertTrue(am.record(self._rec("b2"), path=self.log))
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["brief"] for x in lines], ["b1", "b2"])
        self.assertEqual(json.loads(lines[0])["elapsed_s"], 0.012)

    def test_prior_attempts_counts_by_source_and_brief(self):
        for brief, source in (("b1", "check-apply"), ("b1", "check-apply"),
                              ("b2", "check-apply"), ("b1", "adopt-runner")):
            am.record(self._rec(brief, source), path=self.log)
        with self.log.open("a", encoding="utf-8") as fh:
            fh.write("not json\n")
        self.assertEqual(am.prior_attempts("check-apply", "b1", path=self.log), 2)
        self.assertEqual(am.prior_attempts("adopt-runner", "b1", path=self.log), 1)
        self.assertEqual(am.prior_attempts("check-apply", "b9", path=self.log), 0)
        self.assertEqual(am.prior_attempts("check-apply", "b1",
                                           path=self.log.with_name("absent.jsonl")), 0)

    def test_the_fixture_switch_writes_nothing(self):
        with unittest.mock.patch.dict("os.environ", {am.OFF_ENV: "1"}):
            self.assertFalse(am.record(self._rec(), path=self.log))
        self.assertFalse(self.log.exists())

    def test_delivery_under_a_suite_runs_check_apply_with_the_switch_set(self):
        """deliver.py runs check-apply as a subprocess no test can patch; without the
        switch every delivery test would append fixture lines to the real ledger."""
        sys.path.insert(0, str(ROOT / "curate"))
        import deliver
        brief = pathlib.Path(self._tmp.name) / "b.md"
        brief.write_text("---\napply: manual\nmanual-reason: attended\n---\n\nx\n",
                         encoding="utf-8")
        seen = {}

        def fake_run(cmd, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(cmd, 0, "OK", "")
        with unittest.mock.patch.object(deliver.subprocess, "run", fake_run):
            deliver._check_apply_mode(brief)
        self.assertEqual((seen.get("env") or {}).get(am.OFF_ENV), "1")

    def test_an_unwritable_ledger_returns_false_not_an_exception(self):
        self.log.mkdir(parents=True)
        self.assertFalse(am.record(self._rec(), path=self.log))

    @unittest.skipIf("POGA_FEDERATION_CONFIG" in __import__("os").environ,
                     "production roots relocate the ledger outside the checkout")
    def test_the_default_location_is_gitignored_machine_state(self):
        """Never a tracked file: the checkout default is under `.session-state/`."""
        default = ROOT / ".session-state" / am.LOG_NAME
        self.assertEqual(am.log_path(), default)
        probe = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q",
                                str(default.relative_to(ROOT))], capture_output=True)
        self.assertEqual(probe.returncode, 0, "the ledger path is not gitignored")


if __name__ == "__main__":
    unittest.main()
