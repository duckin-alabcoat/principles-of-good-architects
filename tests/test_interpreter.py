"""The interpreter is resolved, not inherited from PATH (WI-0328).

On 2026-09-10 (dispatch D-069e3d) five lanes built green and every one of them was
refused at the land gate. Nothing was wrong with the work: `python3` on one machine's
PATH was 3.14 while `/usr/bin/python3` was 3.9.6, the suite's verdict differs between
them, and every entry point took whichever one the operator's shell happened to hand
it. Two lanes then patched the same file to satisfy 3.14 — the collision an undeclared
dependency always eventually produces — and the wave was landed by hand from a shell
with `/usr/bin` first.

WI-0320 had already normalised every SUBPROCESS the gate spawns to `sys.executable`.
What it could not reach is the process that CHOOSES `sys.executable`. So the tests here
are about the entry point, and the one that carries the item's acceptance is
`ThePathDoesNotDecideTest` — put a hostile `python3` first on PATH and the answer must
not move.

WHY SO MUCH OF THIS IS UNIT-LEVEL. The mechanism is a re-exec, and a re-exec can only
be observed end-to-end on a machine that has two genuinely different interpreters —
which the fleet does not uniformly have. So the end-to-end case is here and states its
skip reason out loud (`declare-what-a-check-assumes`), and the DECISION it rests on —
exec or don't, loop or don't, fail open or brick — is pinned directly, on every machine.

stdlib unittest: python3 -m unittest discover -s tests
"""

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
import interpreter  # noqa: E402
import session as m  # noqa: E402

import harness_fixture  # noqa: E402

BASE_CFG = {
    "architect_name": "Architect", "architect_id": "member-arch", "user_name": "operator",
    "role_doc": "role.md", "handoff": "handoff.md", "timezone": "UTC",
    "machine_map": {"anything": "TestBox"},
}

HOSTILE = """#!/bin/sh
# A `python3` that is not one. If anything resolves the interpreter from PATH after
# this lands in front of it, the marker appears and the test says so by name.
echo "invoked" >> "%s"
exit 42
"""


def _second_interpreter():
    """A real Python on this machine whose `sys.executable` differs from ours, or "".

    The re-exec is only observable when there IS somewhere else to go. Rather than
    assume any particular pair of interpreters exists everywhere, ask."""
    seen = {os.path.realpath(sys.executable)}
    for cand in (shutil.which("python3"), "/usr/bin/python3",
                 "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if not cand or not os.access(cand, os.X_OK):
            continue
        try:
            got = subprocess.run([cand, "-c", "import sys; print(sys.executable)"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=30).stdout.decode().strip()
        except Exception:
            continue
        if got and os.path.realpath(got) not in seen:
            return cand
    return ""


SECOND = _second_interpreter()


class DeclarationTest(unittest.TestCase):
    """`interpreter.declared` is a second reader of a config key, and a second reader
    is only allowed to exist if it cannot disagree with the first.

    It exists because `ensure` runs BEFORE the heavy imports and inside processes
    (`curate/run_suite.py`) that never load `sessionlib` at all — paying a 7000-line
    module import to read one string would cost more than the drift it prevents. That
    argument is only good while the two agree, so the agreement is checked here rather
    than asserted in a comment."""

    MATRIX = (
        {},
        {"interpreter": "/a/top-level"},
        {"layout": {"interpreter": "/a/block"}},
        # The block wins over the top-level key — the tier order `layout_of` defines.
        {"interpreter": "/a/top-level", "layout": {"interpreter": "/a/block"}},
        # Wrong shapes must fall through, not raise and not be believed.
        {"interpreter": ""},
        {"interpreter": 17},
        {"layout": "not-a-dict", "interpreter": "/a/top-level"},
        {"layout": {"interpreter": ""}, "interpreter": "/a/top-level"},
        {"layout": {}},
    )

    def test_it_agrees_with_layout_of_on_every_shape(self):
        for cfg in self.MATRIX:
            with self.subTest(cfg=cfg):
                self.assertEqual(interpreter.declared(cfg),
                                 m.layout_of(cfg, "interpreter"),
                                 "the pre-import reader and layout_of disagree, which "
                                 "is the only thing that made a second reader allowed")

    def test_the_key_is_registered_in_the_layout_defaults(self):
        """A key a member cannot DECLARE is a key that only ever has one value. Every
        previous instance of this shape was closed by adding a private top-level key
        that reached exactly one call site (WI-0029); this one is registered where the
        resolver looks."""
        self.assertIn("interpreter", m.LAYOUT_DEFAULTS)
        self.assertEqual(m.LAYOUT_DEFAULTS["interpreter"], "",
                         "empty means 'not declared', which must stay distinguishable "
                         "from 'declared as the default'")

    def test_a_non_dict_config_does_not_raise(self):
        for cfg in (None, [], "", 3):
            with self.subTest(cfg=cfg):
                self.assertEqual(interpreter.declared(cfg), "")


class ResolutionOrderTest(unittest.TestCase):
    """$POGA_PYTHON, then the config, then the per-OS default — and a declared value
    that cannot be used falls to NO PIN, never quietly to a different interpreter."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.real = os.path.realpath(sys.executable)
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        os.environ.pop(interpreter.ENV_OVERRIDE, None)

    def write_cfg(self, **extra):
        cfg = dict(BASE_CFG)
        cfg.update(extra)
        (self.root / "session.config.json").write_text(json.dumps(cfg), encoding="utf-8")

    def test_the_env_override_outranks_the_config(self):
        self.write_cfg(interpreter=self.real)
        os.environ[interpreter.ENV_OVERRIDE] = "/usr/bin/env"
        path, source, problem = interpreter.resolve(self.root)
        self.assertEqual(path, "/usr/bin/env")
        self.assertEqual(source, "$" + interpreter.ENV_OVERRIDE)
        self.assertEqual(problem, "")

    def test_the_config_outranks_the_platform_default(self):
        self.write_cfg(interpreter=self.real)
        path, source, _ = interpreter.resolve(self.root)
        self.assertEqual(path, self.real)
        self.assertEqual(source, interpreter.CONFIG_NAME)

    def test_a_relative_declaration_resolves_against_the_repo(self):
        """A member declares its layout in repo-relative terms everywhere else; an
        absolute-only interpreter key would be the one arrangement key that could not
        be written the way all the others are."""
        binp = self.root / "pin"
        binp.mkdir()
        wrapper = binp / "python3"
        wrapper.write_text("#!/bin/sh\nexec %s \"$@\"\n" % self.real, encoding="utf-8")
        wrapper.chmod(0o755)
        self.write_cfg(interpreter="pin/python3")
        path, source, problem = interpreter.resolve(self.root)
        self.assertEqual(path, str(wrapper))
        self.assertEqual(source, interpreter.CONFIG_NAME)
        self.assertEqual(problem, "")

    def test_the_block_tier_is_honoured(self):
        self.write_cfg(layout={"interpreter": self.real})
        self.assertEqual(interpreter.resolve(self.root)[0], self.real)

    def test_a_broken_declaration_is_no_pin_and_says_so(self):
        """NOT a silent fall-through to the platform default. Substituting a different
        interpreter for the one a member asked for would hide the config error behind a
        machine that still worked — and the machine that still worked is exactly how
        this defect survived as long as it did."""
        self.write_cfg(interpreter="/no/such/python3")
        path, source, problem = interpreter.resolve(self.root)
        self.assertEqual(path, "", "a broken declaration must not resolve to anything")
        self.assertEqual(source, interpreter.CONFIG_NAME)
        self.assertIn("/no/such/python3", problem)
        self.assertNotEqual(problem, "")

    def test_a_broken_env_override_is_no_pin_and_says_so(self):
        self.write_cfg(interpreter=self.real)
        os.environ[interpreter.ENV_OVERRIDE] = "/no/such/python3"
        path, _source, problem = interpreter.resolve(self.root)
        self.assertEqual(path, "")
        self.assertIn("/no/such/python3", problem)

    def test_an_unreadable_config_falls_open_to_the_platform_default(self):
        """Malformed JSON must not be able to stop the harness from starting."""
        (self.root / "session.config.json").write_text("{ not json", encoding="utf-8")
        path, _source, problem = interpreter.resolve(self.root)
        self.assertEqual(problem, "", "an unparseable config is not a DECLARATION that "
                                      "failed — there is nothing to report against")
        self.assertEqual(path, interpreter.PLATFORM_DEFAULT
                         if interpreter.usable(interpreter.PLATFORM_DEFAULT) else "")

    def test_no_config_at_all_falls_open(self):
        path, _source, problem = interpreter.resolve(self.root)
        self.assertEqual(problem, "")
        self.assertEqual(path, interpreter.PLATFORM_DEFAULT
                         if interpreter.usable(interpreter.PLATFORM_DEFAULT) else "")


class TheShimIsNotASymlinkTest(unittest.TestCase):
    """`/usr/bin/python3` on macOS is Apple's xcrun shim, and a process started through
    it reports `sys.executable` as the CommandLineTools binary — a completely different
    path that `realpath` does not reconcile.

    Left unhandled, the "am I already running on the pin?" test is false forever and
    EVERY entry point pays one extra interpreter start. That is 0.109 s, which is
    precisely the per-verb tax WI-0222 measured and refused to pay — so this is a
    correctness test for the resolution and a performance test for the fleet at once."""

    @unittest.skipUnless(sys.platform == "darwin" and os.access("/usr/bin/python3", os.X_OK),
                         "no /usr/bin/python3 shim on this platform")
    def test_the_shim_is_recognised_as_the_interpreter_it_starts(self):
        with tempfile.TemporaryDirectory() as td:
            got = interpreter.resolved_executable("/usr/bin/python3", td)
            self.assertTrue(got, "the shim did not report an executable")
            self.assertNotEqual(os.path.realpath(got),
                                os.path.realpath("/usr/bin/python3"),
                                "if these were realpath-equal this test would be "
                                "asserting nothing — the shim's whole point is that "
                                "they are not")
            self.assertTrue(interpreter.same("/usr/bin/python3", got, td))

    def test_the_answer_is_cached_against_the_file(self):
        """A miss costs the same interpreter start the redundant exec would have cost;
        the point is that it happens once, not once per verb."""
        with tempfile.TemporaryDirectory() as td:
            first = interpreter.resolved_executable(sys.executable, td)
            self.assertTrue(first)
            cache = pathlib.Path(td) / interpreter.CACHE_REL
            self.assertTrue(cache.is_file(), "nothing was cached")
            # Poison the cache: a second call that re-probed would overwrite it.
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["executable"] = "/a/cached/answer"
            cache.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(interpreter.resolved_executable(sys.executable, td),
                             "/a/cached/answer")

    def test_a_stale_cache_is_not_believed(self):
        with tempfile.TemporaryDirectory() as td:
            interpreter.resolved_executable(sys.executable, td)
            cache = pathlib.Path(td) / interpreter.CACHE_REL
            payload = json.loads(cache.read_text(encoding="utf-8"))
            payload["stamp"] = "0:0:0"
            payload["executable"] = "/a/stale/answer"
            cache.write_text(json.dumps(payload), encoding="utf-8")
            self.assertNotEqual(interpreter.resolved_executable(sys.executable, td),
                                "/a/stale/answer")

    def test_an_unwritable_cache_dir_still_answers(self):
        """Fail open: the gate builds a fresh scratch worktree per land, and a cache
        that could not be written there must degrade to "slower", never to "wrong"."""
        with tempfile.TemporaryDirectory() as td:
            blocked = pathlib.Path(td) / interpreter.CACHE_REL
            blocked.parent.mkdir(parents=True)
            blocked.mkdir()  # a DIRECTORY where the cache file goes — writes will fail
            self.assertTrue(interpreter.resolved_executable(sys.executable, td))


class EnsureDecidesTest(unittest.TestCase):
    """The re-exec decision itself, with `os.execv` stubbed — so the branch that only
    fires on a machine with two interpreters is pinned on every machine."""

    def setUp(self):
        self.calls = []
        self._execv = os.execv
        os.execv = lambda path, argv: self.calls.append((path, list(argv)))
        self.addCleanup(lambda: setattr(os, "execv", self._execv))
        self._env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(self._env)))
        self._argv = list(sys.argv)
        self.addCleanup(lambda: setattr(sys, "argv", self._argv))
        self._resolve = interpreter.resolve
        self.addCleanup(lambda: setattr(interpreter, "resolve", self._resolve))
        os.environ.pop(interpreter.ENV_GUARD, None)
        sys.argv = ["session.py", "start"]

    def pin(self, path):
        interpreter.resolve = lambda root: (path, "test", "")

    def test_it_execs_into_a_pin_it_is_not_already_running(self):
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)
        self.assertEqual(len(self.calls), 1)
        path, argv = self.calls[0]
        self.assertEqual(path, "/a/different/python3")
        self.assertEqual(argv[0], "/a/different/python3")
        self.assertEqual(argv[-2:], ["session.py", "start"],
                         "the verb and its script must survive the exec")

    def test_it_does_not_exec_when_it_is_already_the_pin(self):
        self.pin(sys.executable)
        interpreter.ensure(ROOT)
        self.assertEqual(self.calls, [])

    def test_it_does_not_exec_when_nothing_is_pinned(self):
        self.pin("")
        interpreter.ensure(ROOT)
        self.assertEqual(self.calls, [])

    def test_it_never_loops(self):
        """The guard is the whole reason a wrong `same()` costs one exec instead of a
        fork bomb."""
        os.environ[interpreter.ENV_GUARD] = "/a/different/python3"
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)
        self.assertEqual(self.calls, [])

    def test_the_guard_is_set_before_the_exec(self):
        recorded = {}
        os.execv = lambda p, a: recorded.setdefault(
            "guard", os.environ.get(interpreter.ENV_GUARD))
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)
        self.assertEqual(recorded.get("guard"), "/a/different/python3",
                         "a child that inherits no guard re-execs again forever")

    def test_a_dash_c_invocation_is_left_alone(self):
        """`python3 -c '...'` keeps its source out of argv, so an exec would run a
        DIFFERENT program rather than the same one elsewhere."""
        sys.argv = ["-c"]
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)
        self.assertEqual(self.calls, [])

    def test_a_non_file_argv0_is_left_alone(self):
        """`python3 -m unittest` REWRITES `sys.argv[0]` to the descriptive string
        "python3.14 -m unittest". Forwarding that to `execv` killed the process with
        `can't open file '<cwd>/python3.14 -m unittest'` — dying on the line whose whole
        job is to make the process deterministic. Found by running the suite on the
        other interpreter, not by reading the code: under the pin nothing re-execed, so
        the branch never ran."""
        sys.argv = ["python3.14 -m unittest", "discover", "-s", "tests"]
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)
        self.assertEqual(self.calls, [])

    def test_importing_an_entry_point_does_not_re_exec_the_importer(self):
        """`ensure` belongs to the ENTRY POINT. A module that merely imports `session`
        — every test in this suite does — must not have its process replaced; that is
        somebody else's program. Pinned at the source, because the symptom is a test
        runner that restarts itself and reports something unrelated."""
        for rel in ("session.py", "curate/run_suite.py", "curate/gate_inputs.py",
                    "poga_cli.py"):
            with self.subTest(rel):
                src = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("ensure(", src, rel + " no longer pins its interpreter")
                head = src.split("ensure(")[0]
                self.assertIn('__name__ == "__main__"', head.rsplit("\n\n", 3)[-1],
                              rel + " calls ensure() unguarded — importing it would "
                                    "re-exec the importer's process")

    def test_a_raising_execv_leaves_the_process_running_and_unmarked(self):
        """Fail open. A guard that cannot run the tool it guards is worse than the
        drift it prevents."""
        def boom(path, argv):
            raise OSError("nope")
        os.execv = boom
        self.pin("/a/different/python3")
        interpreter.ensure(ROOT)  # must not raise
        self.assertIsNone(os.environ.get(interpreter.ENV_GUARD),
                          "a failed exec must not leave the guard set — the next "
                          "process would skip its own pin on the strength of it")

    def test_bytecode_writing_off_survives_the_exec(self):
        """`-B` is not cosmetic here: the mutation harness rewrites one file per
        mutation, and a child that dropped `-B` reads the stale `.pyc` — a mutation
        that reports "not caught" when it never ran."""
        self.assertIn("-B", interpreter._flags() if sys.dont_write_bytecode else ["-B"])
        got = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sys, os; sys.path.insert(0, %r); import interpreter; "
             "print(' '.join(interpreter._flags()))" % str(ROOT)],
            stdout=subprocess.PIPE, timeout=60).stdout.decode()
        self.assertIn("-B", got)


class ThePathDoesNotDecideTest(unittest.TestCase):
    """THE ACCEPTANCE. A foreign `python3` first on PATH does not change the verdict.

    Built as a real member repo running a real subprocess, because the claim is about
    what happens when the harness is STARTED, and that is not observable from inside
    the process that is already running."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = pathlib.Path(self.tmp.name) / "member"
        harness_fixture.install_harness(self.repo)
        shutil.copyfile(ROOT / "interpreter.py", self.repo / "interpreter.py")
        cfg = dict(BASE_CFG)
        cfg["interpreter"] = os.path.realpath(sys.executable)
        (self.repo / "session.config.json").write_text(json.dumps(cfg), encoding="utf-8")

        self.marker = pathlib.Path(self.tmp.name) / "hostile-was-run"
        self.binroot = pathlib.Path(self.tmp.name) / "hostile-bin"
        self.binroot.mkdir()
        shim = self.binroot / "python3"
        shim.write_text(HOSTILE % self.marker, encoding="utf-8")
        shim.chmod(0o755)

    def run_verb(self, path, exe=None):
        env = dict(os.environ)
        env["PATH"] = path
        env.pop(interpreter.ENV_OVERRIDE, None)
        env.pop(interpreter.ENV_GUARD, None)
        return subprocess.run(
            [exe or sys.executable, str(self.repo / "session.py"), "interpreter"],
            cwd=str(self.repo), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=180)

    def test_a_hostile_python3_first_on_path_changes_nothing(self):
        clean = self.run_verb("/usr/bin:/bin")
        poisoned = self.run_verb("%s:/usr/bin:/bin" % self.binroot)

        self.assertEqual(clean.returncode, 0, clean.stderr.decode())
        self.assertEqual(poisoned.returncode, 0, poisoned.stderr.decode())
        self.assertEqual(clean.stdout, poisoned.stdout,
                         "the answer moved when PATH moved, which is the whole defect")
        self.assertIn(b"in force: yes", poisoned.stdout)
        self.assertFalse(
            self.marker.exists(),
            "something still resolved `python3` from PATH: " + (
                self.marker.read_text(encoding="utf-8") if self.marker.exists() else ""))

    def test_the_report_names_the_interpreter_and_where_the_pin_came_from(self):
        """"Print which one they chose" is the half of the acceptance that makes the
        other half auditable: a pin nobody can see is a pin nobody can trust."""
        got = self.run_verb("/usr/bin:/bin").stdout.decode()
        self.assertIn(os.path.realpath(sys.executable), got)
        self.assertIn("session.config.json", got)
        self.assertIn("in force: yes", got)

    @unittest.skipUnless(SECOND, "this machine has only one Python interpreter, so a "
                                 "re-exec has nowhere to go — EnsureDecidesTest pins "
                                 "the decision instead")
    def test_started_on_the_wrong_interpreter_it_re_execs_into_the_pin(self):
        """The end-to-end case, and the one the 09-10 wave actually hit: the harness is
        started on an interpreter that is not the pinned one."""
        got = self.run_verb("%s:/usr/bin:/bin" % self.binroot, exe=SECOND)
        self.assertEqual(got.returncode, 0, got.stderr.decode())
        out = got.stdout.decode()
        self.assertIn("in force: yes", out)
        self.assertIn(os.path.realpath(sys.executable), out)
        self.assertFalse(self.marker.exists())

    def test_a_broken_declaration_makes_the_verb_fail_rather_than_go_quiet(self):
        cfg = json.loads((self.repo / "session.config.json").read_text(encoding="utf-8"))
        cfg["interpreter"] = "/no/such/python3"
        (self.repo / "session.config.json").write_text(json.dumps(cfg), encoding="utf-8")
        got = self.run_verb("/usr/bin:/bin")
        self.assertEqual(got.returncode, 1,
                         "a declaration that could not be honoured must be a thing that "
                         "FAILS, not a thing that quietly does nothing")
        self.assertIn(b"/no/such/python3", got.stdout)


class TheVerbsUseTheResolvedInterpreterTest(unittest.TestCase):
    """The two call sites the item names by title — the test verb and the gate — must
    not re-resolve `python3` from PATH inside an already-running interpreter."""

    def test_the_test_verb_spells_sys_executable(self):
        """`poga test` ran a LITERAL "python3", so it could report a verdict from a
        different Python than the one that would gate the land — the exact split that
        parked the wave."""
        src = harness_fixture.harness_source()
        self.assertNotIn('argv = ["python3", "-m", "unittest"', src,
                         "the test verb still re-resolves python3 from PATH")
        self.assertIn('argv = [sys.executable, "-m", "unittest"', src)

    def test_the_pin_ships_with_the_harness_everywhere_the_harness_ships(self):
        """`interpreter.py` is imported BY `session.py`, so anywhere `session.py` goes
        without it is a member whose harness cannot honour its own declaration.

        Four lists say what the substrate is, and each was written by someone thinking
        about a different question — which is exactly how `sessionlib/` came to be in
        three of them and missing from the fourth."""
        push = (ROOT / "curate" / "push-substrate.py").read_text(encoding="utf-8")
        self.assertIn('"interpreter.py"', push,
                      "it would never reach the fleet")
        boot = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
        self.assertIn('SHARED_SUBSTRATE = ["session.py", "interpreter.py"', boot,
                      "a freshly bootstrapped member would get no pin")
        self.assertIn("interpreter.py", harness_fixture.harness_files(ROOT))

    def test_the_shipped_detector_and_the_fixture_agree_on_what_the_harness_is(self):
        """`harness_fixture`'s docstring says these two "must not be able to disagree
        about what the harness is". Nothing enforced that, so it held by coincidence —
        and a coincidence is what `interpreter.py` would have broken silently."""
        sys.path.insert(0, str(ROOT))
        import standard_check  # noqa: E402
        # Package 3: the detector no longer reads the harness as one string; it LOADS
        # it, and `_harness_paths` is the file set it fingerprints and parses. That set
        # is what must agree with the fixture's.
        detector = [p.relative_to(ROOT).as_posix()
                    for p in standard_check._harness_paths(ROOT)]
        fixture = harness_fixture.harness_files(ROOT)
        self.assertEqual(sorted(detector), sorted(fixture),
                         "the shipped detector and the test fixture are reading "
                         "different sets of files as 'the harness'")

    def test_an_interpreter_only_diff_is_not_gate_neutral(self):
        """THE ONE THAT BITES. A root module is read at `import session`, before the
        deriver installs its audit hook, so it never appears in the MEASURED read set —
        the identical blind window that let a `sessionlib/`-only diff skip the entire
        gate. Undeclared, a change to the module that decides which interpreter the gate
        runs on would be gated by nothing at all."""
        sys.path.insert(0, str(ROOT / "curate"))
        import gate_inputs  # noqa: E402
        self.assertIn("interpreter.py", gate_inputs.NON_SUITE_READ_PREFIXES)

    def test_the_config_declares_the_pin_rather_than_relying_on_the_default(self):
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        self.assertEqual(m.layout_of(cfg, "interpreter"), "/usr/bin/python3",
                         "this repo's pin is a thing it SAYS, not a default it inherits")


if __name__ == "__main__":
    unittest.main()
