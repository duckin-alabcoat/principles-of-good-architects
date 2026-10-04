"""sessionlib/brief.py — the brief engine as a real module, and one frontmatter reader.

Refactor-cleanup package 4. What is pinned:

  A. The module imports WITHOUT running the harness: no `session` module, no other
     sessionlib part, no harness globals, nothing written.
  B. The compatibility facade: every old `session.<name>` IS the module's object (so a
     SurfaceBrief raised by lanes.py's `_brief_dest` is the class the engine catches), and
     `session.evaluate_brief` passes the harness's own path guard.
  C. The engine runs on explicit arguments alone: a temp tree, a caller-supplied
     `resolve_dest`, no ROOT. A guard that refuses surfaces the brief.
  D. DIALECT PARITY. The five frontmatter readers were three dialects, not one. Each
     shared-reader wrapper is checked against a verbatim copy of the implementation it
     replaced, over hand-made edge cases AND every Markdown file in the repo, so a
     difference in any real file fails here rather than in a member's inbox.

stdlib unittest: python3 -m unittest tests.test_brief_module
"""

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import session  # noqa: E402
from sessionlib import brief  # noqa: E402


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- Verbatim copies of the replaced implementations (the parity reference) ---------

def _old_session_parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            header[k.strip().lower()] = v.strip()
    nl = text.find("\n", end + 1)
    return header, (text[nl + 1:] if nl != -1 else "")


def _old_brief_header(text):  # adopt-runner.py and check-apply.py, identical
    if not text.startswith("---"):
        return {}, False
    end = text.find("\n---", 3)
    if end == -1:
        return {}, False
    header = {}
    for line in text[3:end].splitlines():
        if ":" in line and not line.strip().startswith("#"):
            k, _, v = line.partition(":")
            header[k.strip().lower()] = v.strip()
    return header, True


def _old_reconcile_parse_frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _old_deliver_frontmatter(text):
    t = (text or "").lstrip()
    if not t.startswith("---"):
        return ""
    end = t.find("\n---", 3)
    return t[3:end] if end != -1 else ""


def _old_stamp(text, stamp, proposed):
    stamp_lines = (f"applied: {stamp}\napplied-at-version: "
                   f"{session._norm_version(proposed)}\nstate: applied\n")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[: end + 1] + stamp_lines + text[end + 1:]
    return text


EDGE_CASES = [
    "",
    "no frontmatter\n",
    "---\n",
    "---\nunterminated: yes\nbody\n",
    "---\n---\n",
    "---\n---",
    "---\nA: 1\n---",
    "---\nEdit-ID: X-1\n# comment: skipped\napply: auto\n---\nbody\n## op: replace\n",
    "---\nkey: a: b: c\nnocolon\n  spaced :  v  \n---\nbody",
    "---\nid: foo\nId: bar\n---\n",
    "---\r\nk: v\r\n---\r\nbody\r\n",
    "  \n\n---\ndelivered: 2026-08-13\n---\nbody delivered: no\n",
    "---\nk: v\n----\nbody\n",
    "---\nk: v\n--- trailing\nafter\n",
    "----\nk: v\n---\n",
    "---k: v\n---\n",
    "﻿---\nk: v\n---\n",
]


def _repo_markdown():
    """Every tracked-tree Markdown file EXCEPT the live store. Bookkeeping paths
    (ADR-0148 D3) are pruned before they are listed, using the store guard's own list,
    so the corpus never reads a file a bookkeeping land can change under the suite."""
    sys.path.insert(0, str(ROOT / "curate"))
    import store_guard  # noqa: PLC0415
    book = store_guard.bookkeeping_paths(ROOT)
    skip_dirs = {".git", ".claude", "node_modules"}

    def _is_book(rel):
        return any(rel.startswith(b) if b.endswith("/") else rel == b for b in book)

    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = os.path.relpath(dirpath, ROOT)
        rel_dir = "" if rel_dir == "." else rel_dir + "/"
        dirnames[:] = [n for n in dirnames
                       if n not in skip_dirs and not _is_book(f"{rel_dir}{n}/")]
        for name in filenames:
            rel = f"{rel_dir}{name}"
            if not name.endswith(".md") or _is_book(rel):
                continue
            try:
                out.append((rel, (ROOT / rel).read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


class A_ImportsWithoutTheHarness(unittest.TestCase):
    def test_import_runs_nothing(self):
        probe = (
            "import sys, os\n"
            f"sys.path.insert(0, {str(ROOT)!r})\n"
            "before = set(os.listdir('.'))\n"
            "import sessionlib.brief as b\n"
            "parts = sorted(k for k in sys.modules if k.startswith('sessionlib.') and k != 'sessionlib.brief')\n"
            "print('session' in sys.modules, parts, hasattr(b, 'ROOT'), hasattr(b, 'CFG'),\n"
            "      set(os.listdir('.')) == before)\n")
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ, HOME=d)
            r = subprocess.run([sys.executable, "-B", "-c", probe], cwd=d, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "False [] False False True")
            self.assertEqual(os.listdir(d), [])


class B_Facade(unittest.TestCase):
    FACADE = {
        "REQUIRED_BRIEF_HEADER": "REQUIRED_BRIEF_HEADER", "OP_HEADING": "OP_HEADING",
        "FENCE_OPEN": "FENCE_OPEN", "FENCE_CLOSE": "FENCE_CLOSE",
        "BRIEF_FIELD": "BRIEF_FIELD", "VERSION_LINE": "VERSION_LINE",
        "SurfaceBrief": "SurfaceBrief", "Plan": "Plan",
        "parse_frontmatter": "parse_frontmatter", "split_op_sections": "split_op_sections",
        "extract_fields_and_blocks": "extract_fields_and_blocks",
        "_norm_version": "norm_version", "current_version": "current_version",
        "apply_replace": "apply_replace", "apply_version_bump": "apply_version_bump",
        "apply_changelog_prepend": "apply_changelog_prepend",
    }

    def test_facade_names_are_the_module_objects(self):
        for old, new in self.FACADE.items():
            with self.subTest(name=old):
                self.assertIs(getattr(session, old), getattr(brief, new))

    def test_path_guard_refusal_is_the_engines_surface_class(self):
        with self.assertRaises(brief.SurfaceBrief):
            session._brief_dest("../escape.md", "create-file")

    def test_session_evaluate_brief_passes_the_harness_guard(self):
        # A create-file aimed at `.claude/settings.json` must surface through the facade
        # exactly as before the move (WI-0348).
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            (d / "role.md").write_text("**Version:** 1.0.0\n", encoding="utf-8")
            b = d / "b.md"
            b.write_text("---\nedit-id: E\ntarget-file: role.md\nexpected-base-version: 1.0.0\n"
                         "proposed-new-version: 1.0.1\napply: auto\n---\n## op: version-bump\n"
                         "## op: create-file\nPath: .claude/settings.json\n~~~content\n{}\n~~~\n",
                         encoding="utf-8")
            old_root = session.ROOT
            session.ROOT = d
            try:
                plan = session.evaluate_brief(b)
            finally:
                session.ROOT = old_root
        self.assertEqual(plan.action, "surface")
        self.assertIn("decides what code runs", plan.reason)


class C_ExplicitArguments(unittest.TestCase):
    def setUp(self):
        self.d = pathlib.Path(tempfile.mkdtemp())
        (self.d / "role.md").write_text("# Role\n**Version:** 1.2.3\n## Changelog\n",
                                        encoding="utf-8")
        self.b = self.d / "b.md"
        self.b.write_text("---\nedit-id: E-1\ntarget-file: role.md\n"
                          "expected-base-version: 1.2.3\nproposed-new-version: 1.2.4\n"
                          "apply: auto\n---\n## op: version-bump\n## op: changelog-prepend\n"
                          "After: ## Changelog\n~~~content\n- 1.2.4 thing\n~~~\n",
                          encoding="utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def test_plans_with_a_caller_supplied_resolver(self):
        plan = brief.evaluate_brief(self.b, resolve_dest=lambda rel, op: self.d / rel)
        self.assertEqual(plan.action, "apply", plan.reason)
        new = plan.touched[self.d / "role.md"]
        self.assertIn("**Version:** 1.2.4", new)
        self.assertIn("## Changelog\n- 1.2.4 thing\n", new)
        self.assertEqual((self.d / "role.md").read_text(encoding="utf-8").count("1.2.3"), 1,
                         "evaluate_brief must never write")

    def test_a_refusing_resolver_surfaces_the_brief(self):
        def refuse(rel, op):
            raise brief.SurfaceBrief(f"{op}: refused {rel}")
        plan = brief.evaluate_brief(self.b, resolve_dest=refuse)
        self.assertEqual(plan.action, "surface")
        self.assertEqual(plan.reason, "target-file: refused role.md")

    def test_stamp_applied_matches_the_old_filing_text(self):
        text = self.b.read_text(encoding="utf-8")
        for t in (text, "no frontmatter\n", "---\nunterminated\n"):
            self.assertEqual(brief.stamp_applied(t, "2026-10-02", "v1.2.4"),
                             _old_stamp(t, "2026-10-02", "v1.2.4"))


class D_DialectParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = [(f"edge[{i}]", t) for i, t in enumerate(EDGE_CASES)] + _repo_markdown()
        cls.adopt = _load("adopt_runner_parity", "curate/adopt-runner.py")
        cls.check_apply = _load("check_apply_parity", "curate/check-apply.py")
        cls.reconcile = _load("reconcile_parity", "curate/reconcile.py")
        cls.deliver = _load("deliver_parity", "curate/deliver.py")

    def test_corpus_is_real(self):
        self.assertGreater(len(self.corpus), 100)
        self.assertTrue(any(t.startswith("---") for _, t in self.corpus[len(EDGE_CASES):]),
                        "the repo corpus should contain real frontmatter files")

    def _parity(self, new, old):
        for name, text in self.corpus:
            self.assertEqual(new(text), old(text), name)

    def test_session_brief_dialect(self):
        self._parity(session.parse_frontmatter, _old_session_parse_frontmatter)

    def test_adopt_runner_dialect(self):
        self._parity(self.adopt.parse_frontmatter, _old_brief_header)

    def test_check_apply_dialect(self):
        self._parity(self.check_apply.parse_frontmatter, _old_brief_header)

    def test_reconcile_status_dialect(self):
        self._parity(self.reconcile.parse_frontmatter, _old_reconcile_parse_frontmatter)

    def test_deliver_raw_dialect(self):
        self._parity(self.deliver._frontmatter, _old_deliver_frontmatter)
        self.assertEqual(self.deliver._frontmatter(None), "")

    def test_the_dialects_really_differ(self):
        # Why they share a reader but not a function: these inputs read differently.
        text = "---\nId: x\n# c: y\n---\n"
        self.assertEqual(brief.parse_frontmatter(text)[0], {"id": "x"})
        self.assertEqual(brief.status_frontmatter(text), {"Id": "x", "# c": "y"})
        self.assertEqual(brief.raw_frontmatter("\n" + text), "\nId: x\n# c: y")
        self.assertEqual(brief.parse_frontmatter("\n" + text)[0], {})


if __name__ == "__main__":
    unittest.main()
