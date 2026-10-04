"""Package 3 — capability detection that survives a refactor.

`standard_check.py` used to detect every harness capability by searching one
concatenated string of harness SOURCE for a marker. It now checks the wiring each
capability declares in the VERSIONED `standard-capabilities.json` against the harness
LOADED the way `session.py` loads it, plus small pure probes. These tests pin the four
properties that change was for:

  - nothing moved: this checkout reads every capability it read before;
  - refactor-robust: a function MOVED to another sessionlib part, or a call/binding/tuple
    merely REFLOWED, changes no answer (the reflow is exactly what the old text markers
    could not survive);
  - still a gate: a callable genuinely removed, or a probe that genuinely fails, reads
    ABSENT and takes the member off clean;
  - versioned: a description of another manifest_version / another standard, or one that
    disagrees with the release table, is REPORTED and never silently accepted.

stdlib unittest: python3 -m unittest discover -s tests
"""

import ast
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("standard_check_p3", ROOT / "standard_check.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness_fixture import harness_source, install_harness, patch_harness  # noqa: E402
from member_fixture import HOOK_SETTINGS, cfg, make_repo  # noqa: E402

MANIFEST = ROOT / sc.MANIFEST_NAME


def _harness_keys():
    manifest, err = sc.load_manifest()
    assert err is None, err
    return [k for k, e in manifest["capabilities"].items() if e.get("harness")]


def _member(d):
    """The REAL harness, installed into a scratch member with hook-bound settings."""
    repo = install_harness(pathlib.Path(d))
    (repo / ".claude").mkdir(exist_ok=True)
    (repo / ".claude" / "settings.json").write_text(HOOK_SETTINGS, encoding="utf-8")
    (repo / "poga").write_text((ROOT / "poga").read_text(encoding="utf-8"), encoding="utf-8")
    return repo


def _move_functions(repo, names, to_part="relocated"):
    """Cut each top-level `def` in `names` out of whichever sessionlib part holds it and
    append it to a NEW part, registered right after `config` in PARTS — the shape of a
    real refactor that splits a concern out. Returns {name: original part}."""
    lib = repo / "sessionlib"
    moved, origin = [], {}
    for part in sorted(lib.glob("*.py")):
        if part.name == "__init__.py":
            continue
        src = part.read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.splitlines(keepends=True)
        spans = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in names:
                start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
                spans.append((start, node.end_lineno, node.name))
        for start, end, name in sorted(spans, reverse=True):
            moved.append("".join(lines[start:end]))
            del lines[start:end]
            origin[name] = part.name
        if spans:
            part.write_text("".join(lines), encoding="utf-8")
    (lib / f"{to_part}.py").write_text(
        "from __future__ import annotations\n\n" + "\n\n".join(reversed(moved)),
        encoding="utf-8")
    init = lib / "__init__.py"
    src = init.read_text(encoding="utf-8")
    assert src.count("('config', ") == 1, "PARTS moved — fix this fixture"
    init.write_text(src.replace("('config', ", f"('config', '{to_part}', "), encoding="utf-8")
    return origin


class NothingMovedTest(unittest.TestCase):
    """The same capabilities read present on this healthy checkout as before."""

    def test_the_shipped_manifest_is_one_this_checker_reads(self):
        manifest, err = sc.load_manifest()
        self.assertIsNone(err)
        self.assertEqual(manifest["standard"], sc.LATEST)
        self.assertEqual(set(manifest["capabilities"]), set(sc.CAPABILITIES))

    def test_every_harness_capability_is_wired_on_this_checkout(self):
        for key in _harness_keys():
            with self.subTest(capability=key):
                ok, why = sc.wiring(ROOT, key)
                self.assertTrue(ok, f"{key}: {why}")

    @unittest.skipIf((ROOT / "PUBLIC-CUT-RECEIPT.md").is_file(),
                     "public cut: session-handoff.md, STATUS.md and ROADMAP.md are withheld, "
                     "so handoff, status-surface and roadmap read absent; the full set is a "
                     "fact about the trunk (WI-0495, found by the nightly cut suite)")
    def test_the_full_capability_set_is_detected_on_this_checkout(self):
        """Before Package 3 this checkout read every one of the 31 capabilities present
        (or n/a where the settings binding is not the Claude hook). That is the answer
        the new detection must give — not one row fewer."""
        r = sc.evaluate(ROOT, sc._load_cfg(ROOT))
        self.assertIsNone(r["manifest_error"])
        self.assertEqual([k for k, v in r["caps"].items() if v == "absent"], [])
        self.assertEqual(set(r["caps"]), set(sc.CAPABILITIES))
        self.assertIn(r["status"], ("clean",))

    def test_the_real_harness_in_a_scratch_member_reads_clean(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            for name in ("CANON.md", "STANDARD.md", "STANDARD-REFERENCE.md",
                         "standard_check.py"):
                (repo / name).write_text((ROOT / name).read_text(encoding="utf-8"),
                                         encoding="utf-8")
            # Bookkeeping files are the live store (ADR-0148 D3): never read here. Their
            # detectors check existence only, so a stub is the whole contract.
            for name in ("session-handoff.md", "STATUS.md", "ROADMAP.md"):
                (repo / name).write_text(f"# {name}\n", encoding="utf-8")
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["status"], "clean", r)


class RefactorRobustnessTest(unittest.TestCase):
    """Moving or reflowing harness code must not make a healthy member read a capability
    missing."""

    MOVED = ("run_compile", "_complete_lazy_start", "_coord_try_acquire", "cmd_janitor",
             "_merge_onto", "_dispatched_close_authorization", "_store_renumber_batch",
             "false_green_violation", "_notes_compiled", "cmd_integrate",
             "_unattended_close_authorization", "residency_state")

    def test_functions_moved_to_a_new_sessionlib_part_are_still_detected(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            origin = _move_functions(repo, self.MOVED)
            self.assertEqual(set(origin), set(self.MOVED),
                             "a function to move was not found — fix this fixture")
            for key in _harness_keys():
                with self.subTest(capability=key):
                    ok, why = sc.wiring(repo, key)
                    self.assertTrue(ok, f"{key}: {why}")
            # ...and the moved code really lives elsewhere now: the harness that answered
            # is the refactored one, not a cached load of the original.
            h = sc._harness(repo)
            self.assertTrue(h.ns["cmd_janitor"].__code__.co_filename.endswith("relocated.py"))

    def test_a_reflowed_call_binding_and_gate_tuple_are_still_detected(self):
        """Every one of these patches leaves the code semantically identical, and every
        one removes the exact text a pre-Package-3 marker matched — so the old detector
        read each of these capabilities ABSENT on a healthy member."""
        reflows = [
            ("_merge_onto(local, remote, resolve)",
             "_merge_onto(\n            local,\n            remote, resolve)",
             "_merge_onto(local, remote"),
            ("renumberer=_store_renumber_batch", "renumberer = _store_renumber_batch",
             "renumberer=_store_renumber_batch"),
            ("_store_autocommit(OPS_DIRNAME", "_store_autocommit( OPS_DIRNAME",
             "_store_autocommit(OPS_DIRNAME"),
            ("_resolve_rebase_conflict(resolve)", "_resolve_rebase_conflict( resolve )",
             "_resolve_rebase_conflict(resolve)"),
            ("_interpreter.ensure(_ROOT)", "_interpreter.ensure( _ROOT )",
             "_interpreter.ensure(_ROOT)"),
            ("_pf_onboarding,\n                         _pf_residency, _pf_memory_pressure)",
             "_pf_onboarding,\n                         _pf_residency,\n"
             "                         _pf_memory_pressure,\n                         )",
             None),
        ]
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            for old, new, _marker in reflows:
                changed = 0
                for rel in ["session.py", *sorted(
                        p.relative_to(repo).as_posix() for p in (repo / "sessionlib").glob("*.py"))]:
                    p = repo / rel
                    src = p.read_text(encoding="utf-8")
                    if old in src:
                        p.write_text(src.replace(old, new), encoding="utf-8")
                        changed += 1
                self.assertGreaterEqual(changed, 1, f"reflow subject moved: {old!r}")
            text = harness_source(repo)
            for _old, _new, marker in reflows:
                if marker:
                    self.assertNotIn(marker, text, "the old text marker survived the reflow")
            r = sc.evaluate(repo, cfg())
            self.assertEqual([k for k in _harness_keys() if r["caps"][k] != "present"], [])

    def test_a_member_is_read_from_its_own_files_not_this_process(self):
        """The federation imports `sessionlib` for itself; evaluating a member must load
        the MEMBER's parts, or a member missing a capability reads the federation's."""
        import importlib
        sys.path.insert(0, str(ROOT))
        try:
            importlib.import_module("sessionlib")
        finally:
            sys.path.remove(str(ROOT))
        before_path = list(sys.path)
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            self.assertEqual(_move_functions(repo, ("cmd_janitor",)), {"cmd_janitor": "lanes.py"})
            (repo / "sessionlib" / "relocated.py").write_text("", encoding="utf-8")
            self.assertFalse(sc.d_janitor(repo, {}))
            self.assertTrue(sc.d_janitor(ROOT, {}))
        self.assertEqual(sys.path, before_path, "loading a harness leaked into sys.path")

    def test_a_part_importing_a_sessionlib_module_loads_the_members_copy(self):
        """Refactor-cleanup P4: config.py binds the brief engine with `from sessionlib
        import brief`. The loader must resolve that — and `import sessionlib.x` / `from
        sessionlib.x import y` — from the MEMBER's tree, never from the federation's own
        already-imported `sessionlib.brief`, and a harness that cannot resolve it must not
        silently read every capability absent on a healthy machine."""
        import importlib
        sys.path.insert(0, str(ROOT))
        try:
            importlib.import_module("sessionlib.brief")
        finally:
            sys.path.remove(str(ROOT))
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            lib = repo / "sessionlib"
            with (lib / "brief.py").open("a", encoding="utf-8") as fh:
                fh.write('\nMEMBER_MARKER = "member copy"\n')
            (lib / "extra_mod.py").write_text("VALUE = 42\n", encoding="utf-8")
            with (lib / "config.py").open("a", encoding="utf-8") as fh:
                fh.write("\nimport sessionlib.extra_mod as _x_mod\n"
                         "from sessionlib.extra_mod import VALUE as _x_value\n")
            h = sc._Harness(repo)
            self.assertIsNone(h.error)
            self.assertEqual(h.ns["_brief"].MEMBER_MARKER, "member copy")
            self.assertEqual(pathlib.Path(h.ns["_brief"].__file__), lib / "brief.py")
            self.assertEqual((h.ns["_x_mod"].VALUE, h.ns["_x_value"]), (42, 42))
            self.assertFalse(hasattr(sys.modules["sessionlib.brief"], "MEMBER_MARKER"))
            r = sc.evaluate(repo, cfg())
            self.assertEqual([k for k in _harness_keys() if r["caps"][k] != "present"], [])

    def test_detection_writes_nothing_into_the_member(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)

            def snapshot():
                return sorted((str(p.relative_to(repo)), p.stat().st_mtime_ns, p.stat().st_size)
                              for p in repo.rglob("*") if "__pycache__" not in p.parts)

            before = snapshot()
            sc.evaluate(repo, cfg())
            self.assertEqual(snapshot(), before)


class StillAGateTest(unittest.TestCase):
    """A genuinely missing capability is still reported missing."""

    def test_a_removed_callable_reads_absent_and_the_member_is_not_clean(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            _move_functions(repo, ("cmd_janitor",))
            (repo / "sessionlib" / "relocated.py").write_text("", encoding="utf-8")
            # The real registration names `cmd_janitor` directly, so the harness's own
            # `main()` can no longer build its CLI — which is the truth about that member:
            # no verb of it is reachable, the janitor included.
            ok, _why = sc.wiring(repo, "janitor")
            self.assertFalse(ok)
            r = sc.evaluate(repo, cfg())
            self.assertEqual(r["caps"]["janitor"], "absent")
            self.assertIn("janitor", r["missing"])
            self.assertNotEqual(r["status"], "clean")

    def test_a_failing_probe_reads_absent(self):
        """The names are all there; the behaviour is not. A manifest alone proves nothing."""
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            n = patch_harness(repo, "def false_green_violation(cmd: str) -> str | None:\n",
                              "def false_green_violation(cmd: str) -> str | None:\n"
                              "    return None\n")
            self.assertEqual(n, 1, "the predicate's signature moved — fix this fixture")
            ok, why = sc.wiring(repo, "false-green-guard")
            self.assertFalse(ok)
            self.assertIn("probe", why)
            self.assertFalse(sc.d_false_green_guard(repo, {}))

    def test_an_unwired_verb_reads_absent(self):
        with tempfile.TemporaryDirectory() as d:
            repo = _member(d)
            n = patch_harness(repo, "jn.set_defaults(func=cmd_janitor)",
                              "jn.set_defaults(func=None)")
            self.assertEqual(n, 1, "the janitor registration moved — fix this fixture")
            ok, why = sc.wiring(repo, "janitor")
            self.assertFalse(ok)
            self.assertIn("not handled by cmd_janitor", why)

    def test_a_harness_that_will_not_load_reads_every_harness_capability_absent(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            (repo / "session.py").write_text("raise SystemExit('boom')\n", encoding="utf-8")
            r = sc.evaluate(repo, cfg())
            for key in _harness_keys():
                with self.subTest(capability=key):
                    self.assertEqual(r["caps"][key], "absent")
            self.assertNotEqual(r["status"], "clean")


class ManifestVersionTest(unittest.TestCase):
    """An unknown, old or inconsistent description is reported, not silently accepted."""

    def _evaluate_with(self, mutate):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        mutate(data)
        with tempfile.TemporaryDirectory() as d:
            path = pathlib.Path(d) / sc.MANIFEST_NAME
            path.write_text(json.dumps(data), encoding="utf-8")
            repo = make_repo(d)
            return sc.evaluate(repo, cfg(), manifest_path=path)

    def _assert_reported(self, r, needle):
        self.assertIsNotNone(r["manifest_error"])
        self.assertIn(needle, r["manifest_error"])
        self.assertIn("capability manifest unusable", r["note"])
        self.assertNotEqual(r["status"], "clean")
        for key in _harness_keys():
            self.assertEqual(r["caps"][key], "absent", key)

    def test_the_fixture_member_is_clean_under_the_shipped_manifest(self):
        r = self._evaluate_with(lambda data: None)
        self.assertIsNone(r["manifest_error"])
        self.assertEqual(r["status"], "clean")

    def test_an_unknown_manifest_version_is_reported(self):
        self._assert_reported(
            self._evaluate_with(lambda data: data.update(manifest_version=2)),
            "manifest_version 2")

    def test_an_old_manifest_version_is_reported(self):
        self._assert_reported(
            self._evaluate_with(lambda data: data.update(manifest_version=0)),
            "manifest_version 0")

    def test_a_description_of_an_older_standard_is_reported(self):
        self._assert_reported(
            self._evaluate_with(lambda data: data.update(standard="1.19.0")),
            "v1.19.0")

    def test_a_since_that_disagrees_with_the_release_table_is_reported(self):
        def bump(data):
            data["capabilities"]["janitor"]["since"] = "1.9.0"
        self._assert_reported(self._evaluate_with(bump), "janitor")

    def test_a_missing_entry_is_reported(self):
        self._assert_reported(
            self._evaluate_with(lambda data: data["capabilities"].pop("residency")),
            "residency")

    def test_an_unknown_wiring_kind_or_probe_is_reported(self):
        def kind(data):
            data["capabilities"]["janitor"]["harness"]["greps"] = ["def cmd_janitor"]
        self._assert_reported(self._evaluate_with(kind), "greps")

        def probe(data):
            data["capabilities"]["janitor"]["harness"]["probes"] = ["no-such-probe"]
        self._assert_reported(self._evaluate_with(probe), "no-such-probe")

    def test_a_missing_manifest_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            r = sc.evaluate(repo, cfg(), manifest_path=pathlib.Path(d) / "nope.json")
            self._assert_reported(r, "missing")

    def test_the_override_does_not_outlive_the_evaluation(self):
        self._evaluate_with(lambda data: data.update(manifest_version=2))
        self.assertIsNone(sc._active_manifest()[1])


class DistributionTest(unittest.TestCase):
    """The description ships wherever the checker does — or the checker reports it
    missing on every member it reaches."""

    def _load(self, name, rel):
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_bootstrap_seeds_it_beside_the_checker(self):
        src = (ROOT / "bootstrap.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        shared = next(ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", None) == "SHARED_SUBSTRATE" for t in n.targets))
        self.assertIn("standard_check.py", shared)
        self.assertIn(sc.MANIFEST_NAME, shared)

    def test_the_fleet_push_and_the_currency_axis_carry_it(self):
        push = (ROOT / "curate" / "push-substrate.py").read_text(encoding="utf-8")
        self.assertIn(f'"{sc.MANIFEST_NAME}"', push)
        added = [str(ROOT), str(ROOT / "curate")]
        sys.path[:0] = added
        try:
            sv = self._load("standard_version_p3", "curate/standard_version.py")
        finally:
            for entry in added:
                sys.path.remove(entry)
        self.assertIn(sc.MANIFEST_NAME, sv.substrate_subjects())

    def test_the_stale_substrate_recovery_refreshes_it(self):
        line = next(l for l in (ROOT / "poga").read_text(encoding="utf-8").splitlines()
                    if l.startswith("POGA_HARNESS_ROOTS="))
        self.assertIn(sc.MANIFEST_NAME, line)

    def test_the_public_cut_exports_it(self):
        cut = json.loads((ROOT / "curate" / "public_cut_manifest.json").read_text(encoding="utf-8"))
        text = json.dumps(cut)
        self.assertIn('"standard_check.py"', text)
        self.assertIn(f'"{sc.MANIFEST_NAME}"', text)


if __name__ == "__main__":
    unittest.main()
