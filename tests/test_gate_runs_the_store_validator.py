"""WI-0307 half four — the land gate never ran the store's own validator.

THE MEASUREMENT. The session ~241 land reported success and the gate passed. The gate
runs the suite, `distill --check`, `standardize --check` and `gen_settings --check`. It
does NOT run `wi-check`. So a land that left the store in a state its own validator
rejects passed four green checks, and the corruption was found only by running the
validator by hand afterwards. That is
[`a-close-is-the-banner-not-the-sentence`](habits/master.md#a-close-is-the-banner-not-the-sentence)
one layer down: the receipt was real, and the thing it certified was never checked.

THERE WERE TWO HOLES, not one, and closing only the obvious one would have changed
nothing. The second is that `work-items/` and `ops-items/` were not in the gate's
MEASURED input set, so a land whose diff touched only the store classified as
gate-neutral (ADR-0117 D1a) and skipped the ENTIRE gate. Adding `wi-check` to the command
list while leaving the classifier alone would have produced a validator that runs on
every land except the ones that change the store — the only lands it exists for.

WHY THE PREFIXES ARE DECLARED RATHER THAN MEASURED. `curate/gate_inputs.py --derive`
measures the SUITE with an audit hook. The other gate commands are not the suite and
contribute nothing to that measurement, so a check the gate runs beside it has inputs
nothing has ever recorded. Declaring them keeps the record honest — `read_prefixes` is
what the gate reads, and the record now says which of those were measured and which were
declared, rather than quietly presenting a hand-added entry as a measurement.

  A. `wi-check` IS IN THE GATE. Asserted against the live `session.config.json`, because
     a gate that runs it only in a fixture is the defect, not the fix.
  B. THE STORE IS IN THE MEASURED INPUT SET. Asserted at the DERIVER — its declaration
     and the record it assembles — never against the live `gate-inputs.json`. See
     WI-0327 on the class below for why reading the live record was itself a defect.
  C. A STORE-TOUCHING LAND STILL RUNS `wi-check`. (ADR-0148 D3: the store is
     bookkeeping and skips the SUITE, but every cheap check runs at every land.)
  D. ONLY THE SUITE IS SKIPPED for a bookkeeping diff. The WI-0294 win is preserved; a
     fix that ran the suite on every land would be a regression wearing a correctness
     fix's clothes.
  E. A RED `wi-check` REFUSES, AND CARRIES ITS OWN OUTPUT. The validator's message is
     what makes the refusal actionable; a bare "exit 1" would send the operator to run
     it by hand, which is the loop this closes.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import session  # noqa: E402
from curate import gate_inputs  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parent.parent
GIT = shutil.which("git")

#: The two stores the validator reads. Named here so the assertions below cannot drift
#: from the declaration in `curate/gate_inputs.py`.
STORE_PREFIXES = ("work-items/", "ops-items/")

#: Declared alongside the stores for a DIFFERENT reason, found while measuring this one:
#: the harness's own source is read at `import session`, before the probe installs its
#: audit hook, so it never appeared in the measurement at all. Before this change the
#: record carried 20 prefixes and `sessionlib/` was not one of them — meaning a diff
#: touching only harness code classified as gate-neutral and skipped the whole gate.
HARNESS_PREFIX = "sessionlib/"

#: And the module the harness imports BEFORE `sessionlib`, for the same reason (WI-0328).
#: A root module is its own prefix — `_gate_top_prefix` maps "interpreter.py" to
#: "interpreter.py" — so it is declared as a filename, not a directory.
INTERPRETER_PREFIX = "interpreter.py"

#: Declared for the SAME reason as the stores, one reader later (WI-0342).
#: `curate/check_citations.py` gates on whether a `file.py:NNN` citation still resolves,
#: and it reads `adr/` as well as the two stores — but the suite does not read `adr/` at
#: all, so an ADR-only diff classified gate-neutral and skipped the gate holding the only
#: check that reads ADRs. 18 of the 119 dead citations WI-0342 was filed for were in
#: `adr/`, so this is the exact diff shape that would have escaped.
ADR_PREFIX = "adr/"


def _gate_cmds():
    cfg = json.loads((REPO / "session.config.json").read_text(encoding="utf-8"))
    return [" ".join(c) if isinstance(c, list) else c for c in (cfg.get("gate") or [])]


class WiCheckIsInTheGateTest(unittest.TestCase):
    """Property A — asserted against the live config, not a fixture."""

    def test_the_configured_gate_runs_wi_check(self):
        cmds = _gate_cmds()
        self.assertTrue(
            any("wi-check" in c for c in cmds),
            f"the land gate must run the store validator; it runs: {cmds}")

    def test_the_other_three_checks_are_still_there(self):
        # Control: this must ADD a check, never replace the ones already gating.
        cmds = " | ".join(_gate_cmds())
        for expected in ("run_suite.py", "distill.py --check",
                         "standardize.py --check", "gen_settings.py --check"):
            self.assertIn(expected, cmds)


@unittest.skipUnless(GIT, "git required")
class TheStoreIsAGateInputTest(unittest.TestCase):
    """Property B — asserted at the deriver, never against the live record.

    WI-0327. THESE THREE ASSERTIONS USED TO READ `ROOT/gate-inputs.json`, on the
    argument that the record is what the land actually reads. That argument was right
    about the consumer and wrong about the cost: reading the record here put the record
    into the suite's own MEASURED read set, and ADR-0122 D1's reuse proof refuses the
    moment `read_paths` names it —

        suite verdict not reused: record read by suite: gate-inputs.json

    — because a suite measured against bytes the record commit then overwrites has not
    been shown to describe the tree that lands. So the reuse path shipped and could
    never once fire, and every suite-changing land ran the five-minute suite twice. The
    three reads bought a check on one stale artifact and cost the whole mechanism.

    WHAT IS ASSERTED INSTEAD, AND WHY IT IS STRONGER. The record is not the property; it
    is the deriver's OUTPUT, rewritten on every land that touches `tests/` or
    `curate/gate_inputs.py` ([`land.py`](../sessionlib/land.py) `_gate_record_refresh`).
    So the durable claim is *the deriver declares the stores, and its declaration reaches
    the field the land classifies on* — which holds for every record it will ever write,
    including the one on disk right now. A regression that dropped `work-items/` from the
    declaration fails here AND forces a re-derive at its own land; the old assertion could
    only notice afterwards.

    The guard that keeps the reads from returning is
    `test_gate_record_is_not_a_suite_input.py`.
    """

    #: A measurement the probe could plausibly have returned. It deliberately does NOT
    #: contain the record — the assertion below is that nothing downstream puts it there.
    #:
    #: WIDENED FOR WI-0337, and the widening is itself the point. The old four paths are
    #: below `gate_inputs.MIN_MEASURED_PREFIXES`, so the deriver now refuses this canned
    #: measurement outright — a near-empty record is exactly what it must not write. The
    #: fixture has to model a measurement that passes the floor, or it is testing the
    #: refusal rather than the declaration.
    MEASURED_PATHS = ("CANON.md", "STANDARD.md", "STATUS.md", "portfolio.md",
                      "bootstrap.py", "poga", "session.config.json",
                      "curate/distill.py", "session.py", "tests/test_x.py")

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        (self.repo / "tests").mkdir(parents=True)
        (self.repo / "tests" / "test_x.py").write_text("# a suite\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(self.repo), "init", "-q", "-b", "main"],
                       check=True, capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run([GIT, "-C", str(self.repo), "config", k, v],
                           check=True, capture_output=True)
        subprocess.run([GIT, "-C", str(self.repo), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run([GIT, "-C", str(self.repo), "commit", "-qm", "base"],
                       check=True, capture_output=True)
        self.tree = subprocess.run(
            [GIT, "-C", str(self.repo), "rev-parse", "HEAD^{tree}"],
            check=True, capture_output=True, text=True).stdout.strip()

    def _derive(self):
        """Run the real `derive()` against the fixture repo with a canned probe.

        Only the probe subprocess is intercepted — every `git` call still runs for
        real against the fixture repo, so the record's provenance fields are assembled
        by the code under test rather than by the test. The serial probe itself takes
        minutes and measures THIS repo's suite, which is neither cheap nor the subject.
        """
        raw = {
            "verdict": {"exit_code": 0, "tests_run": 3000, "failures": 0, "errors": 0,
                        "skips": 0, "test_ids": ["test_x.X.test_ok"],
                        "test_ids_sha256": "-", "interpreter": sys.executable,
                        "version": list(sys.version_info[:3]),
                        "environment": {"POGA_GATE": "1"}, "measured_tree": self.tree},
            "tests": 3000, "gate_skips": 0, "gate_skip_ids": [], "ok": True,
            "paths": list(self.MEASURED_PATHS),
            "prefixes": sorted({gate_inputs.top_prefix(p) for p in self.MEASURED_PATHS}),
            "dropped_untracked": [],
            # WI-0337. The probe writes this; a canned raw without it is a measurement
            # taken blind to every subprocess, and `derive` refuses it — correctly, and
            # loudly enough that leaving it out here reads as a fixture bug, not a
            # product one.
            "child_audit": {"ok": True, "canary": {"ok": True}, "spawns": 0,
                            "expected_reports": 0, "reports": 0, "torn_reports": 0,
                            "child_only_prefixes": [], "unaudited": [],
                            "unaudited_total": 0},
        }
        real_run = subprocess.run

        def run(argv, **kwargs):
            if argv and argv[0] == sys.executable:          # the probe, and only it
                pathlib.Path(kwargs["env"]["POGA_GATE_INPUTS_OUT"]).write_text(
                    json.dumps(raw), encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "", "")
            return real_run(argv, **kwargs)

        with mock.patch.object(gate_inputs, "ROOT", self.repo), \
             mock.patch.object(subprocess, "run", side_effect=run):
            return gate_inputs.derive(), raw

    def test_the_deriver_declares_both_stores_and_the_harness_source(self):
        # The declaration itself. A record that omits a store lets a store-only land
        # classify as gate-neutral and skip the validator that exists for it; a record
        # that omits the harness lets a `sessionlib/`-only land skip the whole gate.
        declared = set(gate_inputs.NON_SUITE_READ_PREFIXES)
        for p in (*STORE_PREFIXES, HARNESS_PREFIX):
            self.assertIn(p, declared,
                          f"{p} must be declared, or a land touching only it skips the gate")

    def test_a_derived_record_carries_the_declared_prefixes(self):
        rec, raw = self._derive()
        self.assertEqual(
            set(rec["read_prefixes"]),
            set(raw["prefixes"]) | set(gate_inputs.NON_SUITE_READ_PREFIXES),
            "read_prefixes is what the land classifies on and must be the complete union")
        for p in (*STORE_PREFIXES, HARNESS_PREFIX):
            self.assertIn(p, rec["read_prefixes"])

    def test_the_record_says_which_prefixes_were_declared(self):
        # `declare-what-a-check-assumes`: a declared prefix presented as a measured one
        # would make the record's own provenance a lie.
        rec, _ = self._derive()
        self.assertEqual(rec["declared_prefixes"],
                         sorted(gate_inputs.NON_SUITE_READ_PREFIXES),
                         "the record must name the prefixes asserted rather than observed")
        self.assertEqual(set(rec["declared_prefixes"]),
                         set(STORE_PREFIXES) | {HARNESS_PREFIX, INTERPRETER_PREFIX,
                                                ADR_PREFIX})

    def test_declaring_a_prefix_never_adds_a_path_to_read_paths(self):
        # `read_paths` is the MEASUREMENT, and it is the evidence `_gate_validation_reuse`
        # reads to decide whether the record commit left the tested tree unchanged. A
        # declared entry leaking into it would refuse reuse for a file nobody ever read —
        # the WI-0327 defect arriving from the other direction.
        rec, raw = self._derive()
        self.assertEqual(rec["read_paths"], raw["paths"])
        self.assertNotIn(session.GATE_INPUTS_RECORD, rec["read_paths"])


@unittest.skipUnless(GIT, "git required")
class StoreDiffsAreNotNeutralTest(unittest.TestCase):
    """Properties C and D — the classifier, on a real repo."""

    #: `schema` and the WI-0337 `child_audit` receipt both come from the harness rather
    #: than being written as literals: a stale literal here does not fail, it makes every
    #: record unreadable, and an unreadable record gates — so Property D would go green
    #: for the wrong reason while asserting a skip that can no longer happen.
    RECORD = {
        "schema": session.GATE_INPUTS_SCHEMA,
        "derived_at": "2026-09-07T08:00:00+00:00",
        "derived_at_commit": "0" * 40,
        "suite": "python3 -m unittest discover -s tests",
        "tests": 3147,
        "suite_ok": True,
        "read_prefixes": ["curate/", "session.py", "tests/",
                          "work-items/", "ops-items/"],
        "declared_prefixes": list(STORE_PREFIXES),
        "read_paths_sampled": [],
        "child_audit": {"ok": True, "spawns": 0, "expected_reports": 0, "reports": 0},
    }

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run([GIT, "-C", str(self.repo), "init", "-q", "-b", "main"],
                       check=True, capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run([GIT, "-C", str(self.repo), "config", k, v],
                           check=True, capture_output=True)
        (self.repo / "gate-inputs.json").write_text(
            json.dumps(self.RECORD, indent=2), encoding="utf-8")
        (self.repo / "sessions").mkdir()
        (self.repo / "sessions" / "a.md").write_text("base\n", encoding="utf-8")
        (self.repo / "work-items").mkdir()
        (self.repo / "work-items" / "WI-0001-x.md").write_text("base\n", encoding="utf-8")
        self._commit("base")
        self.parent = self._rev()

        self._save = getattr(session, "ROOT")
        session.ROOT = self.repo
        self.addCleanup(setattr, session, "ROOT", self._save)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _commit(self, msg):
        subprocess.run([GIT, "-C", str(self.repo), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run([GIT, "-C", str(self.repo), "commit", "-qm", msg],
                       check=True, capture_output=True)

    def _rev(self):
        return subprocess.run([GIT, "-C", str(self.repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    # ADR-0148 D3 re-drew this line. A store-only diff IS bookkeeping now — the suite
    # does not run for it — but the classifier no longer decides whether the CHEAP checks
    # run: `_land_gate` runs every non-suite command at every land, neutral or not. So
    # property C survives as "a store-touching land still runs wi-check", which is what
    # WI-0307 was about, and D as "only the suite is skipped".

    SUITE = "python3 curate/run_suite.py"
    WICHECK = "python3 session.py wi-check"

    def _land_gate_skips(self):
        saved = session.CFG
        session.CFG = dict(saved or {}, gate=[self.SUITE, self.WICHECK])
        self.addCleanup(setattr, session, "CFG", saved)
        seen = []
        ok, _, info = session._land_gate(self.parent, self._rev(),
                                         lambda skip: (seen.append(skip), (True, ""))[1],
                                         {})
        self.assertTrue(ok)
        self.assertEqual(len(seen), 1, "the gate runner must have been called once")
        return seen[0], info

    def test_a_work_items_only_diff_still_runs_wi_check(self):
        (self.repo / "work-items" / "WI-0002-y.md").write_text("new\n", encoding="utf-8")
        self._commit("file an item")
        skip, info = self._land_gate_skips()
        self.assertNotIn(self.WICHECK, skip, "a store-touching land skipped the validator")
        self.assertEqual(info["diff_class"], "bookkeeping")

    def test_an_ops_items_only_diff_still_runs_wi_check(self):
        (self.repo / "ops-items").mkdir()
        (self.repo / "ops-items" / "OPS-0001-y.md").write_text("new\n", encoding="utf-8")
        self._commit("file an obligation")
        skip, info = self._land_gate_skips()
        self.assertNotIn(self.WICHECK, skip, "an ops-touching land skipped the validator")

    def test_a_sessions_only_diff_skips_only_the_suite(self):
        # Property D — the WI-0294 win, preserved and widened. Without this, "make the
        # gate run" could be implemented by running the suite everywhere.
        (self.repo / "sessions" / "b.md").write_text("a journal\n", encoding="utf-8")
        self._commit("close a session")
        skip, info = self._land_gate_skips()
        self.assertEqual(skip, frozenset({self.SUITE}))
        self.assertTrue(info["gate_neutral"])


class ARedWiCheckRefusesTest(unittest.TestCase):
    """Property E — the refusal carries the validator's own words."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_wi_check_exits_nonzero_and_names_the_missing_id(self):
        # Run the real command against a store with a hole, exactly as the gate does.
        repo = self.tmp / "repo"
        (repo / "work-items").mkdir(parents=True)
        for n in (1, 3):                      # WI-0002 is missing — the ~241 shape
            (repo / "work-items" / f"WI-{n:04d}-x.md").write_text(
                f"# WI-{n:04d}: item {n}\n\n- status: open\n- section: next\n"
                f"- blocked-by: \n- group: \n- source: \n- impact: fix\n- version: \n\n"
                f"ACCEPTANCE: body.\n", encoding="utf-8")
        shutil.copy(REPO / "session.py", repo / "session.py")
        shutil.copytree(REPO / "sessionlib", repo / "sessionlib")
        r = subprocess.run(["python3", "session.py", "wi-check"],
                           cwd=repo, capture_output=True, text=True)
        self.assertEqual(r.returncode, 1,
                         f"a store with a hole must refuse:\n{r.stdout}\n{r.stderr}")
        out = r.stdout + r.stderr
        self.assertIn("WI-0002", out, "the refusal must name the id")

    def test_the_gate_puts_a_failing_checks_output_in_the_receipt(self):
        # `_run_gate` reports stderr, falling back to stdout — and `wi-check` prints its
        # problems on STDOUT, so the fallback is the load-bearing half for this check.
        #
        # WHAT THIS TEST DOES NOT COVER, said here so it is not trusted for more than it
        # checks (WI-0334). The fixture emits ONE line, and a one-line output cannot
        # express a truncation — so this assertion passed for four years of
        # `splitlines()[-4:]`, including against the suite, whose last four lines are
        # structurally the verdict and never the failing test's name. Keeping a
        # single-line fixture is right for THIS property (the stdout fallback) and
        # useless for that one. The truncation is exercised against real multi-line
        # runner output in `tests/test_a_failing_gate_names_the_test.py`; a change to
        # the receipt's shape must be checked against that file, not this one.
        saved = getattr(session, "CFG")
        session.CFG = {"gate": [["python3", "-c",
                                 "import sys; print('WI-0002 is missing'); sys.exit(1)"]]}
        self.addCleanup(setattr, session, "CFG", saved)
        ok, report = session._run_gate()
        self.assertFalse(ok)
        self.assertIn("WI-0002 is missing", report,
                      "the validator's own output must reach the receipt")


if __name__ == "__main__":
    unittest.main()
