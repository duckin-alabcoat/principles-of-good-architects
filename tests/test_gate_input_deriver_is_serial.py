"""WI-0338 — the gate-input deriver must measure the suite IN THIS PROCESS.

THE TRAP, AND WHY PROSE WAS THE ONLY THING HOLDING IT SHUT. `curate/gate_inputs.py`
runs the suite in-process under `sys.addaudithook` precisely so the hook can see the
reads. `curate/run_suite.py` — the sharded runner the land gate itself uses, and the one
that turned 331.7s into 71.8s — runs each shard through `subprocess.run`. An audit hook
is per-interpreter and does not follow children, so pointing the deriver at the fast
runner would measure ALMOST NOTHING.

It would not fail loudly either, and that is the dangerous half. `derive()` unions
`NON_SUITE_READ_PREFIXES` into `read_prefixes`, so a near-empty measurement still
assembles a well-formed record with prefixes in it, and `_gate_input_prefixes`
(`sessionlib/land.py`) would not collapse it to None. Every path but the declared few
would classify gate-neutral (ADR-0117 D1a) and every land touching one would skip the
whole gate while reporting success.

The rule was written down in three places — ADR-0117's consequences, the role doc's
`run_suite.py` row, and the `//gate` comment in `session.config.json` — and enforced in
none of them. Three copies of a rule nobody executes is the
[`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)
shape exactly: the reasoning is already correct, and the next person to think "we have a
fast runner now" reads none of the three. So: a test.

THREE PROPERTIES, because the defect has three shapes and each guard misses the others.

  A. THE PROBE MEASURES WHAT THE SUITE READS. End-to-end, against a copy of the harness
     in a tmp git repo whose one-test suite opens a marker file: the marker must come
     back in `prefixes`. This is the only assertion here that survives a rewrite into
     some shape neither B nor C anticipated. It carries its own NEGATIVE CONTROL — the
     same hook, armed the same way, around a suite run as a CHILD process — because "an
     audit hook does not follow subprocesses" is the premise this whole item rests on
     and it, too, had only ever been prose
     ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).
  B. THE DECLARED COMMAND IS THE SERIAL ONE. `GATE_SUITE()` is what the record calls its
     own provenance, so a sharded command there makes the record lie about itself even
     if the probe stayed honest. Asserted as a SHAPE — interpreter, `-m unittest`,
     `discover`, and no script path anywhere — rather than as a blocklist of one
     filename, so `run_suite_v2.py` fails too. Then cross-checked against the `gate` list
     in `session.config.json`, which is the authority on which scripts are the fast path
     ([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority)):
     a runner added there tomorrow is forbidden here with no edit to this file.
  C. NOTHING IS SPAWNED AFTER THE HOOK IS ARMED. The inverse of B — the label stays
     serial and the measurement is quietly shelled out. A static read of `_probe`, split
     at the `sys.addaudithook` call. Every `git` call the probe needs already sits above
     that line; this asserts that ordering is the property it looks like rather than an
     accident, and that the in-process `unittest` run still sits below it.

WHAT C CANNOT SEE, stated rather than implied. It reads one function in one file, so a
spawn reached through a helper defined elsewhere passes it. A is the backstop for that:
it runs the real probe and checks the measurement is non-empty, whatever route the suite
took to get there.

OBSERVED RED, not assumed red. `GATE_SUITE()` was pointed at `curate/run_suite.py` and
`_probe` rewritten to run the suite as a child; 6 of these 8 tests failed, and A's
`prefixes` came back **`[]`** — not thinned, empty. A guard nobody has watched fail is
evidence about nothing.

stdlib unittest: python3 -m unittest discover -s tests
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from curate import gate_inputs  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
GIT = shutil.which("git")

#: The harness the copied probe needs in order to import. Deliberately NOT the whole
#: repo: the record must stay out of the copy, and `tests/` is replaced by the one-test
#: fixture whose single read is the thing being measured.
#: WI-0337 added `curate/childaudit/` — the `sitecustomize` shim the probe puts on its
#: children's `PYTHONPATH`. It is part of the harness the deriver needs, not an
#: optional extra: without it the probe cannot import `poga_child_audit` at all.
#: WI-0371 added `poga_evidence.py` — `gate_inputs.py` imports it at module scope for
#: the one rule that keeps a cut verdict from reading as a complete one. Like
#: `interpreter.py` it is a ROOT module the copied probe cannot start without.
HARNESS = ("session.py", "interpreter.py", "poga_evidence.py", "sessionlib",
           "curate/gate_inputs.py", "curate/childaudit")

#: A probe that arms the SAME hook and then runs the suite as a child — the negative
#: control for A. It is a model of the sharded runner's mechanism, not a copy of the
#: runner: what it demonstrates is `sys.addaudithook`'s per-interpreter scope, which is
#: the single fact the deriver's serial-only rule is built on.
CHILD_PROBE = '''
import json, os, pathlib, subprocess, sys
root = pathlib.Path(__file__).resolve().parent
seen = set()

def hook(event, args):
    if event not in ("open", "os.scandir", "os.listdir") or not args or not args[0]:
        return
    try:
        p = pathlib.Path(os.fsdecode(args[0]))
        if not p.is_absolute():
            p = pathlib.Path(os.getcwd()) / p
        seen.add(p.resolve().relative_to(root).as_posix())
    except Exception:
        return

sys.addaudithook(hook)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
               cwd=str(root), capture_output=True)
pathlib.Path(os.environ["CHILD_PROBE_OUT"]).write_text(json.dumps(sorted(seen)))
'''


@unittest.skipUnless(GIT, "git required")
class TheProbeMeasuresWhatTheSuiteReadsTest(unittest.TestCase):
    """Property A — the real probe, on a real repo, measuring a real read.

    The fixture is a git repo because the probe intersects its reads with `git ls-files`
    (an untracked path is a phantom, never a lane's diff). It is a COPY of the harness
    rather than this checkout because `ROOT` in `curate/gate_inputs.py` is derived from
    `__file__`: the probe always measures the repo it is sitting in, which is exactly
    what makes a copy a faithful test rather than a mock of one.
    """

    def setUp(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.tmp = tmp
        self.repo = tmp / "repo"
        (self.repo / "curate").mkdir(parents=True)
        (self.repo / "tests").mkdir(parents=True)
        for rel in HARNESS:
            src, dst = ROOT / rel, self.repo / rel
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
        (self.repo / "marker.txt").write_text("marker\n", encoding="utf-8")
        (self.repo / "tests" / "test_marker.py").write_text(
            "import pathlib, unittest\n"
            "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"
            "class MarkerTest(unittest.TestCase):\n"
            "    def test_reads_the_marker(self):\n"
            "        self.assertEqual((ROOT / 'marker.txt').read_text().strip(), "
            "'marker')\n",
            encoding="utf-8")
        # WI-0337. A second marker read ONLY by a subprocess the suite spawns — the
        # shape 67 of this repo's 112 test modules have. Under a schema-1 probe this
        # read reached the record by no route at all, and the record said so by saying
        # nothing. Kept in the same fixture as `marker.txt` deliberately: one probe run
        # now proves both halves of the measurement, and the pair fails differently —
        # `marker.txt` missing means the deriver stopped measuring its own process,
        # `child_marker.txt` missing means it stopped following its children.
        (self.repo / "child_marker.txt").write_text("child\n", encoding="utf-8")
        (self.repo / "tests" / "test_child_marker.py").write_text(
            "import pathlib, subprocess, sys, unittest\n"
            "ROOT = pathlib.Path(__file__).resolve().parent.parent\n"
            "class ChildMarkerTest(unittest.TestCase):\n"
            "    def test_a_subprocess_reads_the_child_marker(self):\n"
            "        r = subprocess.run(\n"
            "            [sys.executable, '-c',\n"
            "             \"import sys; print(open(sys.argv[1]).read().strip())\",\n"
            "             str(ROOT / 'child_marker.txt')],\n"
            "            cwd=str(ROOT), capture_output=True, text=True)\n"
            "        self.assertEqual(r.returncode, 0, r.stderr)\n"
            "        self.assertEqual(r.stdout.strip(), 'child')\n",
            encoding="utf-8")
        git = [GIT, "-C", str(self.repo)]
        subprocess.run(git + ["init", "-q", "-b", "main"], check=True, capture_output=True)
        for k, v in (("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")):
            subprocess.run(git + ["config", k, v], check=True, capture_output=True)
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-qm", "base"], check=True, capture_output=True)

    def _probe_env(self, out: pathlib.Path) -> dict:
        """An environment the OUTER probe cannot reach into.

        THIS SUITE RUNS INSIDE THE THING IT TESTS. `curate/gate_inputs.py --derive` runs
        the whole suite under its own probe, and that probe puts its `curate/childaudit/`
        on `PYTHONPATH` for everything it spawns (WI-0337) — including this test, and so
        including the fixture-repo probe this test starts. Inherit that and the fixture
        probe's children find the OUTER repo's `sitecustomize` when their own is missing,
        which makes `test_a_probe_whose_shim_cannot_load_refuses_and_writes_nothing` pass
        standalone and fail on the full run. It did, twice, in the lane that wrote it.

        So the inner probe gets a clean slate: no inherited `PYTHONPATH`, and neither
        child-audit variable. `verify-in-the-created-configuration` — the configuration
        that matters here is *inside a derive*, and it is not the one a developer runs."""
        env = dict(os.environ, POGA_GATE_INPUTS_PROBE="1",
                   POGA_GATE_INPUTS_OUT=str(out), POGA_GATE="1")
        for key in ("PYTHONPATH", "POGA_GATE_INPUTS_CHILD_DIR", "POGA_GATE_INPUTS_ROOT"):
            env.pop(key, None)
        return env

    def _run_probe(self) -> dict:
        out = self.tmp / "raw.json"
        r = subprocess.run([sys.executable, str(self.repo / "curate" / "gate_inputs.py")],
                           cwd=str(self.repo), env=self._probe_env(out),
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:] or r.stdout[-2000:])
        self.assertTrue(out.exists(), "the probe wrote no measurement")
        return json.loads(out.read_text(encoding="utf-8"))

    def test_a_file_the_suite_opens_lands_in_the_measurement(self):
        data = self._run_probe()
        self.assertEqual(data["tests"], 2, data)
        self.assertTrue(data["ok"], data["verdict"])
        self.assertIn("marker.txt", data["prefixes"],
                      "the suite opened marker.txt and the probe did not see it — the "
                      "deriver is no longer measuring the suite it runs (WI-0338)")
        self.assertIn("marker.txt", data["paths"])

    def test_a_file_a_suites_SUBPROCESS_opens_lands_in_the_measurement(self):
        """WI-0337, and the only assertion in this repo that exercises the whole route.

        `sys.addaudithook` binds one interpreter, so before this the read below reached
        the record by no route at all. It is the acceptance criterion of WI-0337 —
        *"a test using a subprocess-spawning module asserts its reads appear in the
        record"* — run against the REAL probe assembling a REAL measurement, not against
        the collection helper: the union of the children's reads into the parent's set is
        one line in `_probe` that no unit test can reach."""
        data = self._run_probe()
        self.assertIn("child_marker.txt", data["prefixes"],
                      "a file only a SUBPROCESS of the suite opened is missing from the "
                      "measurement — the child audit is not reaching the record, and "
                      "under-measurement is what licenses a wrong gate-neutral skip "
                      "(WI-0337)")
        self.assertIn("child_marker.txt", data["paths"])

    def test_a_probe_whose_shim_cannot_load_refuses_and_writes_nothing(self):
        """WI-0337 D2. The refusal, exercised where it actually lives.

        `poga_child_audit` still imports — so the probe starts normally — but the
        `sitecustomize` beside it is gone, which is what `site` looks for in a child.
        The canary therefore hears nothing back, and the probe must EXIT NONZERO AND
        WRITE NOTHING rather than record the parent's half as the whole input set. That
        distinction is the whole defect: an unarmed child-audit does not merely stop
        measuring children, it certifies a partial measurement as complete."""
        (self.repo / "curate" / "childaudit" / "sitecustomize.py").unlink()
        out = self.tmp / "refused.json"
        r = subprocess.run([sys.executable, str(self.repo / "curate" / "gate_inputs.py")],
                           cwd=str(self.repo), env=self._probe_env(out),
                           capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=300)
        self.assertNotEqual(r.returncode, 0,
                            "a probe that cannot audit its children succeeded anyway")
        self.assertIn("child-process audit", r.stderr)
        self.assertFalse(out.exists(),
                         "a refused probe still wrote a measurement a land would read")

    def test_the_probe_reports_on_the_children_it_could_not_reach(self):
        """`declare-what-a-check-assumes`. The shim cannot follow a non-Python child, so
        the record has to say that rather than let a reader infer completeness from a
        number. The counts come back from a real run, not a fixture."""
        audit = self._run_probe()["child_audit"]
        self.assertIs(audit["ok"], True, audit)
        self.assertTrue(audit["canary"]["ok"], audit["canary"])
        self.assertGreater(audit["expected_reports"], 0,
                           "the fixture suite spawns a child; the probe saw no spawn "
                           "it expected to hear back from")
        self.assertGreater(audit["reports"], 0,
                           "children started and none reported — the shim did not arm")
        self.assertEqual(audit["torn_reports"], 0, audit)
        self.assertIsInstance(audit["unaudited"], list)

    def test_the_same_hook_sees_nothing_when_the_suite_is_a_child(self):
        # The premise, measured. If this ever fails by FINDING the marker, then audit
        # hooks do follow children on this platform and the serial-only rule is obsolete
        # rather than merely unenforced — read that failure the right way round.
        script = self.repo / "child_probe.py"
        script.write_text(CHILD_PROBE, encoding="utf-8")
        out = self.tmp / "child.json"
        r = subprocess.run([sys.executable, str(script)], cwd=str(self.repo),
                           env=dict(os.environ, CHILD_PROBE_OUT=str(out)),
                           capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        seen = json.loads(out.read_text(encoding="utf-8"))
        self.assertNotIn("marker.txt", seen,
                         "a subprocess-run suite was measured after all; the reason the "
                         "deriver must stay serial no longer holds as stated")


class TheDeclaredSuiteCommandIsSerialTest(unittest.TestCase):
    """Property B — the command the record names as its own provenance."""

    def test_the_command_is_an_in_process_unittest_discovery(self):
        cmd = gate_inputs.GATE_SUITE()
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1:3], ["-m", "unittest"],
                         f"the deriver's command must invoke unittest directly: {cmd}")
        self.assertIn("discover", cmd)
        self.assertIn("tests", cmd)

    def test_the_command_names_no_script(self):
        # The shape that excludes `curate/run_suite.py` AND every future runner: a script
        # path is the only way to reach a spawning runner, and the in-process probe
        # reimplements `-m unittest discover` rather than executing one.
        scripts = [part for part in gate_inputs.GATE_SUITE()[1:] if part.endswith(".py")]
        self.assertEqual(scripts, [],
                         f"the gate-input deriver's command runs a script ({scripts}); a "
                         "runner is spawned, `sys.addaudithook` does not follow children, "
                         "so the measurement would be near-empty and every path would "
                         "classify gate-neutral (WI-0338)")

    def test_the_command_shares_no_runner_with_the_gate_list(self):
        # The gate list is the authority on what the FAST path runs. Whatever is added
        # there is forbidden here, with no edit to this test.
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        gate = cfg.get("gate") or []
        runners = {pathlib.PurePosixPath(tok).name
                   for cmd in gate for tok in cmd.split() if tok.endswith(".py")}
        self.assertIn("run_suite.py", runners,
                      "the sharded runner left the gate list — re-read this test's "
                      "premise before editing it away")
        derived = " ".join(gate_inputs.GATE_SUITE())
        for runner in sorted(runners):
            self.assertNotIn(runner, derived,
                             f"the deriver's command names {runner}, which the gate runs "
                             "as its own process; the deriver must measure in-process")


class TheProbeSpawnsNothingAfterTheHookTest(unittest.TestCase):
    """Property C — a static read of `_probe`, split at the hook.

    Over the SOURCE, because the ordering is a claim about every path through the
    function and a behavioural test can only drive the ones a fixture reaches. The
    subject file is resolved from the imported module rather than named, so moving the
    deriver does not silently retire the guard.
    """

    #: Dotted prefixes that start another process. `os.spawn*` / `os.exec*` /
    #: `os.posix_spawn*` are matched by prefix so a variant nobody listed is caught too.
    SPAWN_PREFIXES = ("subprocess.", "multiprocessing.", "os.spawn", "os.exec",
                      "os.posix_spawn", "os.system", "os.popen", "os.fork")

    @staticmethod
    def _dotted(node: ast.AST) -> str:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    def setUp(self):
        source = pathlib.Path(inspect.getsourcefile(gate_inputs)).read_text(encoding="utf-8")
        self.module = ast.parse(source)
        probe = [n for n in self.module.body
                 if isinstance(n, ast.FunctionDef) and n.name == "_probe"]
        self.assertEqual(len(probe), 1, "curate/gate_inputs.py has no _probe()")
        self.probe = probe[0]
        armed = [i for i, stmt in enumerate(self.probe.body)
                 for call in ast.walk(stmt)
                 if isinstance(call, ast.Call)
                 and self._dotted(call.func) == "sys.addaudithook"]
        self.assertEqual(len(armed), 1,
                         "_probe must arm exactly one audit hook, at one place")
        self.after = self.probe.body[armed[0] + 1:]

    def _calls_after_the_hook(self):
        return [self._dotted(n.func) for stmt in self.after for n in ast.walk(stmt)
                if isinstance(n, ast.Call)]

    def test_no_process_is_started_after_the_hook_is_armed(self):
        spawns = [name for name in self._calls_after_the_hook()
                  if name.startswith(self.SPAWN_PREFIXES)
                  or name.endswith("ProcessPoolExecutor")]
        self.assertEqual(spawns, [],
                         f"_probe starts {spawns} after `sys.addaudithook`; the hook is "
                         "per-interpreter, so everything that child reads is invisible to "
                         "the measurement (WI-0338). The probe's own `git` calls belong "
                         "ABOVE the hook, where they already are")

    def test_the_suite_still_runs_in_process_below_the_hook(self):
        # The other half of the same ordering: moving the spawn above the hook would
        # satisfy the check above while measuring nothing at all.
        self.assertIn("unittest.TextTestRunner", self._calls_after_the_hook(),
                      "_probe no longer runs the suite in-process under the hook")

    def test_the_deriver_binds_no_spawner_by_a_bare_name(self):
        # `from subprocess import run` would make every dotted check above blind.
        bound = [alias.name for node in ast.walk(self.module)
                 if isinstance(node, ast.ImportFrom)
                 and (node.module or "").split(".")[0] in ("subprocess", "multiprocessing")
                 for alias in node.names]
        self.assertEqual(bound, [],
                         f"curate/gate_inputs.py binds {bound} by a bare name, which the "
                         "dotted-call guard above cannot see")


class TheDeriverAndTheLandAgreeOnTheKeyTest(unittest.TestCase):
    """Moved from the retired tests/test_gate_classifies_each_command.py (WI-0347) when
    ADR-0148 deleted the per-command skip plan: the command KEY outlives it. The deriver
    files each entry under `session._gate_command_key(cmd)`; the land's suite keys
    (`_land_suite_keys`) and `_run_gate`'s skip set use the same key. `_run_gate`
    rewrites `python3` to `sys.executable` before spawning, so a key taken after that
    substitution would differ between machines."""

    def setUp(self):
        import session
        self.session = session

    def test_the_key_is_the_command_as_configured(self):
        s = self.session
        self.assertEqual(s._gate_command_key("python3 session.py wi-check"),
                         "python3 session.py wi-check")
        self.assertEqual(s._gate_command_key(["python3", "session.py", "wi-check"]),
                         "python3 session.py wi-check")

    def test_the_key_does_not_depend_on_the_running_interpreter(self):
        self.assertNotIn(sys.executable,
                         self.session._gate_command_key("python3 curate/run_suite.py"))

    def test_whitespace_does_not_make_two_keys_of_one_command(self):
        s = self.session
        self.assertEqual(s._gate_command_key("python3  session.py   wi-check"),
                         s._gate_command_key("python3 session.py wi-check"))

    def test_the_deriver_files_entries_under_the_lands_key(self):
        source = inspect.getsource(gate_inputs.measure_command)
        self.assertIn("session._gate_command_key(cmd)", source,
                      "the deriver derives the record's command key some other way")

    def test_the_suite_runner_has_one_definition(self):
        s = self.session
        self.assertTrue(s._gate_is_suite_command("python3 curate/run_suite.py"))
        self.assertFalse(s._gate_is_suite_command("python3 session.py wi-check"))
        self.assertIn(s.GATE_SUITE_RUNNER, "python3 curate/run_suite.py")


class TheRealGateListIsCoveredTest(unittest.TestCase):
    """A statement about THIS repo's gate (moved with the class above)."""

    def test_every_configured_gate_command_has_a_distinct_key_and_one_suite(self):
        import session
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        gate = cfg.get("gate") or []
        self.assertGreater(len(gate), 1)
        keys = [session._gate_command_key(c) for c in gate]
        self.assertEqual(len(set(keys)), len(keys), keys)
        self.assertEqual(sum(1 for c in gate if session._gate_is_suite_command(c)), 1,
                         "exactly one gate command must run the suite — the deriver "
                         "gives that one the serial probe's measurement, and the land "
                         "skips exactly it (ADR-0148 D1)")


if __name__ == "__main__":
    unittest.main()
