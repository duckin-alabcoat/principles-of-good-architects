"""WI-0337 — the gate-input measurement follows its own subprocesses, or refuses.

`curate/gate_inputs.py --derive` decides which repo paths the land gate's suite reads,
and `sessionlib/land.py` skips the whole gate for any diff that touches none of them.
The measurement was taken with `sys.addaudithook`, which binds ONE interpreter: 67 of
the federation's 112 test modules spawn subprocesses, and every repo file those
children opened was read by the suite and recorded by nobody.

WHY THAT IS A DEFECT AND NOT AN INACCURACY, which is the thing these tests are really
about: the classifier skips when NO changed path is in the measured set, so a read that
goes missing does not make the gate careful — it makes it skip. Under-measurement fails
PERMISSIVE. The end state of the bug is a suite that should have run and did not, on a
record that looks well-formed and confident either way.

WHAT EACH GROUP PINS, and each fails differently:

  A. `ChildChannelTest` — a child's repo read, and a GRANDCHILD's, reach the
     measurement, including the relative-path-after-`chdir` case that destroyed the
     original in-process measurement once already. `test_a_real_test_module...` is the
     acceptance criterion verbatim: a real subprocess-spawning module from this suite,
     run through the real shim, must contribute reads that the in-process half never
     sees.
  B. `ChainTest` — the shim must not swallow a `sitecustomize` that was already there.
     A measurement that changes what it measures is not a measurement, and a packaged
     Python can ship one that rewrites `sys.path` and `sys.executable`.
  C. `CanaryTest` — the detector, not the capability. A child-audit that silently fails
     to arm does not merely stop measuring children, it certifies the parent's half as
     the complete input set. The canary is why a broken channel refuses instead.
  D. `RefusalTest` — a near-empty measurement must fail LOUDLY rather than produce a
     well-formed record. Every guard in this module used to sit on the reading side and
     take the record's own word for it; a probe that ran no tests passed all of them.
  E. `SpawnAccountingTest` — what the record says it could NOT reach. These are derived
     from the spawn event (a non-Python executable, `-I`, an `env=` without the shim),
     never guessed, so the gap is written down beside the number rather than inferred
     from its absence (`declare-what-a-check-assumes`).
  F. `ReaderRefusesAnUnreceiptedRecordTest` — both readers, `curate` and `sessionlib`,
     reject a record with no child-audit receipt. Structural, not a threshold: such a
     record was derived by a probe blind to every subprocess, so its input set is
     known-incomplete, and an incomplete set is exactly what licenses a skip.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "curate" / "childaudit"))

import poga_child_audit as pca  # noqa: E402
from curate import gate_inputs  # noqa: E402

#: A measurement that `refusal()` must accept, so every negative case below differs from
#: a passing one by exactly the field it is testing.
GOOD = {
    "tests": 3000,
    "prefixes": ["tests/", "curate/", "session.py", "sessionlib/", "adr/",
                 "bootstrap.py", "poga", "CANON.md", "STANDARD.md"],
    "child_audit": {"ok": True},
}


class _Box(unittest.TestCase):
    """A collection directory outside the repo, torn down with the test."""

    def setUp(self):
        self.box = pathlib.Path(tempfile.mkdtemp(prefix="poga-childaudit-test-"))
        self.addCleanup(shutil.rmtree, self.box, ignore_errors=True)

    def run_child(self, argv, cwd=None, env=None, timeout=600):
        env = pca.child_env(env if env is not None else os.environ, self.box, ROOT)
        return subprocess.run(argv, cwd=str(cwd or self.box), env=env,
                              capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)

    def collected(self):
        paths, records, torn = pca.collect(self.box)
        self.assertEqual(torn, 0, "a child's record was torn")
        return paths, records


# ── A. the channel ──────────────────────────────────────────────────────────────

class ChildChannelTest(_Box):

    def test_a_childs_repo_read_reaches_the_measurement(self):
        """The bug in one line: this read is invisible to the parent's audit hook."""
        r = self.run_child([sys.executable, "-c",
                            "import sys; open(sys.argv[1]).close()",
                            str(ROOT / "CANON.md")])
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, records = self.collected()
        self.assertIn("CANON.md", paths)
        self.assertEqual(len(records), 1, "one child, one record")

    def test_a_grandchilds_read_reaches_it_too(self):
        """`PYTHONPATH` is inherited across `exec`, so depth costs nothing.

        It matters because the suite's spawns are not flat: a test runs `session.py`,
        which runs `git`, which runs a hook. Anything that only followed direct
        children would measure the first layer and miss the rest."""
        src = ("import subprocess, sys\n"
               "subprocess.run([sys.executable, '-c',"
               " \"open('STANDARD.md').close()\"], cwd=sys.argv[1], check=True)\n")
        r = self.run_child([sys.executable, "-c", src, str(ROOT)])
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, records = self.collected()
        self.assertIn("STANDARD.md", paths)
        self.assertEqual(len(records), 2, "the child and the grandchild both report")

    def test_a_relative_read_after_a_chdir_resolves_against_the_childs_own_cwd(self):
        """The failure that destroyed the first in-process measurement, one process out.

        Resolving a relative path against a cwd captured at start — rather than the live
        one — produced 503 "read prefixes" that were other people's tmpdir names wearing
        repo-relative paths. A child is where this is most likely to recur, because a
        child's cwd is almost never the repo."""
        src = ("import os, sys\n"
               "os.chdir(sys.argv[1])\n"
               "open('portfolio.md').close()\n")
        r = self.run_child([sys.executable, "-c", src, str(ROOT)])
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, _ = self.collected()
        self.assertIn("portfolio.md", paths)

    def test_a_relative_read_in_a_tmpdir_does_not_become_the_repo_file_of_that_name(self):
        """The 503-prefix episode, reproduced in the only place it can still happen.

        The positive test above cannot tell the two rules apart: its child `chdir`s INTO
        the repo, where joining to the live cwd and joining to the root give the same
        answer. This is the case that distinguishes them — a child sitting in a tmpdir
        opening a bare `CANON.md` that exists THERE. Resolved against the live cwd it is
        outside the repo and not an input; resolved against the root it silently becomes
        the repo's own `CANON.md`, which is tracked, so nothing downstream would drop
        it. That is how the first measurement came back with 503 prefixes named `00`,
        `a1`, `wt` and `repo` and measured nothing at all."""
        (self.box / "CANON.md").write_text("a decoy, not the federation's", encoding="utf-8")
        r = self.run_child([sys.executable, "-c", "open('CANON.md').close()"],
                           cwd=self.box)
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, _ = self.collected()
        self.assertNotIn("CANON.md", paths,
                         "a tmpdir file was recorded as the repo file of the same name")

    def test_a_read_outside_the_repo_is_not_in_the_measurement(self):
        """Tests run overwhelmingly in tmpdirs and none of it is a gate input."""
        scratch = self.box / "elsewhere.txt"
        scratch.write_text("x", encoding="utf-8")
        r = self.run_child([sys.executable, "-c",
                            "import sys; open(sys.argv[1]).close()", str(scratch)])
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, _ = self.collected()
        self.assertEqual(paths, set(), "a tmpdir read entered the input set: %r" % paths)

    def test_the_shim_it_displaced_is_not_charged_to_the_child(self):
        """WI-0484, found running the suite on Linux. The sharded runner puts the store
        guard's shim (curate/storeguard_child) on every worker's PYTHONPATH, so this
        audit's shim chains to it. Armed BEFORE that chain, the guard shim's own .py and
        .pyc opens were recorded as the child's reads. macOS hid it only because Xcode's
        Python caches bytecode outside the repo and the cache was warm, so this test
        forces a cold cache: a fresh PYTHONPYCACHEPREFIX makes the leak show everywhere."""
        env = dict(os.environ)
        guard = str(ROOT / "curate" / "storeguard_child")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (guard, env.get("PYTHONPATH")) if p)
        env["PYTHONPYCACHEPREFIX"] = str(self.box / "cold-pycache")
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        r = self.run_child([sys.executable, "-c", "pass"], env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        paths, _ = self.collected()
        self.assertEqual({p for p in paths if "storeguard_child" in p}, set(),
                         "the displaced shim's own reads were charged to the child")

    def test_a_real_test_module_that_spawns_subprocesses_contributes_its_reads(self):
        """THE ACCEPTANCE CRITERION. A real module from this suite, the real shim.

        `tests/test_interpreter.py` spawns interpreters the way 67 modules here do. Run
        as a child of a measuring parent, its own subprocess reads must land in the
        record — and the assertion is on `interpreter.py`, which is the specimen: it is
        in `NON_SUITE_READ_PREFIXES` today precisely because nothing measured it, and
        without it a diff touching only the module that decides WHICH INTERPRETER THE
        GATE RUNS ON would classify gate-neutral."""
        r = self.run_child([sys.executable, "-m", "unittest", "discover",
                            "-s", "tests", "-p", "test_interpreter.py"], cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        paths, records = self.collected()
        self.assertGreater(len(records), 1,
                           "the module spawns subprocesses; only one process reported")
        tracked = {gate_inputs.top_prefix(p)
                   for p in subprocess.run(
                       ["git", "ls-files"], cwd=str(ROOT), capture_output=True,
                       text=True).stdout.split()}
        prefixes = set(gate_inputs.measured_prefixes(paths, tracked))
        for expected in ("interpreter.py", "session.py", "sessionlib/", "tests/"):
            self.assertIn(expected, prefixes,
                          "a child's read of %s never reached the record" % expected)

    def test_the_shim_is_inert_without_the_probes_environment(self):
        """Every Python on this machine imports `sitecustomize`; only a probe descendant
        may be measured by it, and none of them may be slowed or altered otherwise."""
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "curate" / "childaudit")
        env.pop(pca.ENV_DIR, None)
        env.pop(pca.ENV_ROOT, None)
        r = subprocess.run(
            [sys.executable, "-c",
             "import poga_child_audit as p; print(p.arm()); open('CANON.md').close()"],
            cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False")
        self.assertEqual(list(self.box.glob("*")), [],
                         "an unarmed child wrote a record anyway")


# ── B. the chain ────────────────────────────────────────────────────────────────

class ChainTest(_Box):
    """The shim goes FIRST on `PYTHONPATH` to be sure it loads, which is also what makes
    it capable of hiding somebody else's `sitecustomize`. Somebody else's can be
    real — a packaged interpreter may carry one in its stdlib that reshuffles
    `sys.path`, rewrites `sys.executable` to an install prefix and fixes up
    `site.PREFIXES`. A measurement that silently changes its subject's interpreter
    configuration is not measuring the thing that will run at land time."""

    def test_a_displaced_sitecustomize_still_runs(self):
        other = pathlib.Path(tempfile.mkdtemp(prefix="poga-other-site-"))
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        marker = other / "ran.txt"
        (other / "sitecustomize.py").write_text(
            "import pathlib\n"
            "pathlib.Path(%r).write_text('yes')\n" % str(marker), encoding="utf-8")
        env = pca.child_env(os.environ, self.box, ROOT)
        env["PYTHONPATH"] = env["PYTHONPATH"] + os.pathsep + str(other)
        r = subprocess.run([sys.executable, "-c", "open('CANON.md').close()"],
                           cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(marker.exists(),
                        "the shim swallowed the sitecustomize it displaced")
        paths, _ = self.collected()
        self.assertIn("CANON.md", paths, "chaining cost us the measurement")

    def test_the_shims_directory_is_still_on_the_path_after_chaining(self):
        """The entry is removed and re-added, never snapshot-and-restored: a displaced
        `sitecustomize` mutates `sys.path` on purpose, and restoring a saved copy of the
        list would undo the work that was chained to."""
        env = pca.child_env(os.environ, self.box, ROOT)
        here = str(pathlib.Path(pca.__file__).resolve().parent)
        r = subprocess.run(
            [sys.executable, "-c",
             "import os, sys; print(any(p and os.path.abspath(p) == %r "
             "for p in sys.path))" % here],
            cwd=str(ROOT), env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "True")


# ── C. the detector ─────────────────────────────────────────────────────────────

class CanaryTest(unittest.TestCase):

    def test_the_canary_sees_exactly_the_file_it_read(self):
        ok, detail = gate_inputs._canary(ROOT)
        self.assertTrue(ok, json.dumps(detail, indent=2))
        self.assertEqual(detail["saw"], [gate_inputs.CANARY_REL])
        self.assertEqual(detail["reports"], 1)
        self.assertEqual(detail["torn_reports"], 0)

    def test_a_channel_that_cannot_arm_fails_the_canary(self):
        """`ship-the-detector-with-the-capability`, read the way that habit asks: a
        child-audit that fails silently does not merely stop measuring children, it
        certifies the parent's half as the whole input set. So the failure has to be
        loud at derive time, and this is the thing that makes it loud."""
        here = str(pathlib.Path(pca.__file__).resolve().parent)

        def without_the_shim(base, out_dir, root):
            # STRIPS `PYTHONPATH`, rather than merely declining to add to it. The first
            # version built the env from `base` and only left the entry off — which is
            # the same thing everywhere except inside a real derive, where the probe has
            # already put the shim into `os.environ` for the whole suite. There the
            # child armed anyway and this test failed, on the full run, after passing
            # standalone every time: `verify-in-the-created-configuration`, caught by
            # the configuration rather than by review.
            env = dict(base)
            env["PYTHONPATH"] = os.pathsep.join(
                p for p in (env.get("PYTHONPATH") or "").split(os.pathsep)
                if p and os.path.abspath(p) != here)
            env[pca.ENV_DIR] = str(out_dir)
            env[pca.ENV_ROOT] = str(root)
            return env

        real = pca.child_env
        try:
            pca.child_env = without_the_shim
            ok, detail = gate_inputs._canary(ROOT)
        finally:
            pca.child_env = real
        self.assertFalse(ok, "a child with no shim on its path reported anyway")
        self.assertEqual(detail["reports"], 0)
        self.assertEqual(detail["saw"], [])


# ── D. the write-side floor ─────────────────────────────────────────────────────

class RefusalTest(unittest.TestCase):
    """The guard that was missing entirely: every previous check sat on the READING side
    and took the record's own word for it. A probe that discovered no tests, or whose
    hook never armed, wrote a schema, a timestamp, a commit and a handful of prefixes —
    and passed every "is this usable?" test in the repo, while meaning that nearly every
    diff is gate-neutral."""

    def test_a_complete_measurement_is_accepted(self):
        self.assertIsNone(gate_inputs.refusal(dict(GOOD)))

    def test_a_measurement_with_no_child_audit_is_refused(self):
        bad = dict(GOOD)
        bad.pop("child_audit")
        self.assertIn("child-process audit", gate_inputs.refusal(bad) or "")

    def test_a_failed_child_audit_is_refused(self):
        self.assertIn("child-process audit",
                      gate_inputs.refusal(dict(GOOD, child_audit={"ok": False})) or "")

    def test_a_measurement_over_almost_no_tests_is_refused(self):
        self.assertIn("below the floor",
                      gate_inputs.refusal(dict(GOOD, tests=3)) or "")

    def test_a_measurement_over_no_tests_at_all_is_refused(self):
        self.assertIsNotNone(gate_inputs.refusal(dict(GOOD, tests=0)))
        self.assertIsNotNone(gate_inputs.refusal(dict(GOOD, tests=None)))

    def test_a_measurement_that_never_saw_the_suites_own_directory_is_refused(self):
        """The non-arbitrary half of the floor. `unittest discover -s tests` cannot
        complete without reading `tests/`, so a measurement without it did not observe
        the run it claims to have measured, whatever its counts say."""
        bad = dict(GOOD, prefixes=[p for p in GOOD["prefixes"] if p != "tests/"])
        self.assertIn("suite's own directory", gate_inputs.refusal(bad) or "")

    def test_a_near_empty_measurement_is_refused(self):
        bad = dict(GOOD, prefixes=["tests/", "curate/"])
        self.assertIn("near-empty", gate_inputs.refusal(bad) or "")

    def test_the_sentinel_is_checked_against_measured_prefixes_not_declared_ones(self):
        """A declaration cannot vouch for a hook that never fired. `refusal` reads
        `prefixes` — the probe's raw measurement — and never `read_prefixes`, which is
        the measured set unioned with the hand-declared one."""
        bad = dict(GOOD, prefixes=[], read_prefixes=list(GOOD["prefixes"]),
                   declared_prefixes=list(GOOD["prefixes"]))
        self.assertIsNotNone(gate_inputs.refusal(bad))


# ── E. what it says it could not reach ──────────────────────────────────────────

class SpawnAccountingTest(unittest.TestCase):

    def note(self, exe, argv, cwd=None, env=None):
        return gate_inputs._spawn_note((exe, argv, cwd, env), ROOT)

    def test_an_ordinary_python_child_is_expected_to_report(self):
        n = self.note(sys.executable, [sys.executable, "-m", "unittest"])
        self.assertIsNone(n["why"])

    def test_a_non_python_child_is_named_as_unreachable(self):
        self.assertEqual(self.note("/usr/bin/git", ["git", "status"])["why"],
                         "non-python")

    def test_an_isolated_interpreter_is_named_as_unreachable(self):
        """`-I` implies `-E`, and `-S` skips `site` — which is the only place the shim
        can be imported from. Neither is a suspicion about the call; both are facts
        readable off its argv."""
        for flag in ("-I", "-E", "-S", "-ES"):
            self.assertEqual(
                self.note(sys.executable, [sys.executable, flag, "-c", "1"])["why"],
                "isolated-interpreter", flag)

    def test_a_flag_after_the_program_is_not_an_interpreter_flag(self):
        """`session.py --explain -E` is the child's own argument, not an interpreter
        one. Misreading it would name a perfectly measured child as unreachable."""
        n = self.note(sys.executable, [sys.executable, "run.py", "-E"])
        self.assertIsNone(n["why"])

    def test_a_spawn_that_replaces_the_environment_is_named_as_unreachable(self):
        self.assertEqual(
            self.note(sys.executable, [sys.executable, "-c", "1"],
                      env={"PATH": "/bin"})["why"], "env-without-shim")

    def test_a_spawn_that_carries_the_shim_forward_is_expected_to_report(self):
        env = pca.child_env({}, "/tmp", ROOT)
        self.assertIsNone(self.note(sys.executable, [sys.executable, "-c", "1"],
                                    env=env)["why"])

    def test_a_repo_script_exec_is_recorded_even_though_the_child_is_not_python(self):
        """The exec IS a read, and for `./poga`, `drills/*.sh` or a deploy installer it
        is the only read we will ever get."""
        n = self.note("./poga", ["./poga", "work", "list"], cwd=str(ROOT))
        self.assertEqual(n["read"], "poga")

    def test_watching_children_start_and_hearing_from_none_refuses_the_derive(self):
        """The defect itself, as a condition the deriver can be in. From outside, a
        probe whose shim never armed is indistinguishable from a healthy one — this is
        the thing that tells them apart."""
        spawns = [{"why": None, "cmd": "python -m unittest", "read": None},
                  {"why": "non-python", "cmd": "git status", "read": None}]
        self.assertIn("measured by nothing",
                      gate_inputs.accounting_failure(spawns, []) or "")

    def test_one_report_is_enough_to_show_the_shim_armed(self):
        spawns = [{"why": None, "cmd": "python -m unittest", "read": None}]
        self.assertIsNone(gate_inputs.accounting_failure(spawns, [{"paths": []}]))

    def test_more_reports_than_spawns_is_not_a_failure(self):
        """Grandchildren report without the parent ever seeing them start, so reports
        legitimately exceed expectations. A check that fired on the difference would be
        noise on every run."""
        spawns = [{"why": None, "cmd": "python x.py", "read": None}]
        self.assertIsNone(gate_inputs.accounting_failure(
            spawns, [{"paths": []}, {"paths": []}, {"paths": []}]))

    def test_a_suite_that_spawned_only_unreachable_children_is_not_a_failure(self):
        """No expectation, no silence to explain. A run whose only spawns were `git`
        has nothing to hear back from, and refusing there would wedge the derive over a
        gap that is declared rather than broken."""
        spawns = [{"why": "non-python", "cmd": "git status", "read": None}]
        self.assertIsNone(gate_inputs.accounting_failure(spawns, []))

    def test_a_relative_exec_resolves_against_the_spawns_cwd_not_the_live_one(self):
        """A test running `./poga` with `cwd=ROOT` from inside a tmpdir is the normal
        case here, and joining that path to the live cwd is the phantom-path mistake
        `poga_child_audit.rel` carries a paragraph about."""
        elsewhere = pathlib.Path(tempfile.mkdtemp(prefix="poga-cwd-"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)
        n = self.note("./poga", ["./poga"], cwd=str(elsewhere))
        self.assertIsNone(n["read"],
                          "an exec outside the repo was recorded as a repo read")


# ── E2. placing what was read ───────────────────────────────────────────────────

class MeasuredPrefixesTest(unittest.TestCase):
    """`measured_prefixes` exists because the acceptance test above failed on a warm
    bytecode cache, and the failure was the real thing rather than the test's fault.

    A child's `import sessionlib.land` reads `sessionlib/__pycache__/land.cpython-39.pyc`
    — dropped as a bytecode cache — so on a warm cache the only surviving evidence that
    the child read the harness is the `os.scandir` of `sessionlib`. That renders as a
    bare name, and the land only ever classifies FILE paths out of a diff, so a bare name
    can never match anything: a real read became no read, in the permissive direction,
    depending on whether `__pycache__` happened to be populated."""

    TRACKED = {"tests/", "sessionlib/", "curate/", "session.py", "poga"}

    def test_a_file_read_places_at_its_directory(self):
        self.assertEqual(
            gate_inputs.measured_prefixes(["tests/test_x.py"], self.TRACKED), ["tests/"])

    def test_a_root_file_read_places_at_itself(self):
        self.assertEqual(
            gate_inputs.measured_prefixes(["session.py", "poga"], self.TRACKED),
            ["poga", "session.py"])

    def test_a_bare_directory_read_is_promoted_to_its_prefix(self):
        """The bytecode case. Without this the scandir is worth nothing at all."""
        self.assertEqual(
            gate_inputs.measured_prefixes(["sessionlib"], self.TRACKED), ["sessionlib/"])

    def test_a_bare_name_git_does_not_track_is_still_dropped(self):
        """Promotion may only ever add a prefix git already tracks as a directory. The
        phantom names a child produces — `member`, `pin`, `hostile-bin` from fixture
        tmpdirs resolved against a repo cwd — must not survive it."""
        self.assertEqual(
            gate_inputs.measured_prefixes(["member", "pin", "hostile-bin"], self.TRACKED),
            [])

    def test_an_untracked_path_inside_a_tracked_directory_still_places(self):
        """Membership is decided at the directory (ADR-0117 D2), so a file nobody
        tracks inside a read directory is still that directory's read."""
        self.assertEqual(
            gate_inputs.measured_prefixes(["tests/scratch.tmp"], self.TRACKED), ["tests/"])


# ── F. no receipt, no classification ────────────────────────────────────────────

class ReaderRefusesAnUnreceiptedRecordTest(unittest.TestCase):
    """Both readers, because there are two and they cannot share code: `land.py` ships
    to every member and cannot import `curate/`, which is federation-only. The pair is
    the known duplication ([P16](../principles/master.md#p16--avoid-duplication)) and
    the thing that keeps it honest is a test that asserts the same property of both."""

    RECORD = {
        "schema": gate_inputs.SCHEMA,
        "tests": 3000,
        "suite_ok": True,
        "read_prefixes": ["tests/", "curate/"],
        "child_audit": {"ok": True},
    }

    def _load(self, record):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="poga-record-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "gate-inputs.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        real = gate_inputs.RECORD
        try:
            gate_inputs.RECORD = path
            return gate_inputs.load()
        finally:
            gate_inputs.RECORD = real

    def test_a_complete_record_loads(self):
        self.assertIsNotNone(self._load(dict(self.RECORD)))

    def test_a_record_with_no_child_audit_receipt_does_not_load(self):
        bad = dict(self.RECORD)
        bad.pop("child_audit")
        self.assertIsNone(self._load(bad))

    def test_a_record_whose_child_audit_failed_does_not_load(self):
        self.assertIsNone(self._load(dict(self.RECORD, child_audit={"ok": False})))

    def test_the_two_readers_agree_on_the_schema_number(self):
        """A land reads `sessionlib`'s constant and the deriver writes `curate`'s. If
        they ever diverge, every record written is unreadable by the thing that reads
        them — silently, and in the safe direction, which is how it would survive."""
        import session
        self.assertEqual(gate_inputs.SCHEMA, session.GATE_INPUTS_SCHEMA)


if __name__ == "__main__":
    unittest.main()
