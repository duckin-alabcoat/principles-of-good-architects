"""No test may read a path the public cut withholds unless it says so (WI-0495).

Twice in a row a trunk-only test broke only in the public cut. In 2026.10.4 WI-0485's
`MutationTest` copied `public-overlay/adr`, which the cut does not ship; trunk was green,
and only the cut lane's hand-run CI caught it (comms/2026-10-02-date-release-2026.10.4-
ready.md, "Still open"). The nightly runner now runs the suite inside a scratch cut
(curate/gate-inputs-runner.py, `cut_suite`), but that is a night late. This is the cheap
check that runs on every suite: it reads every test file's AST and fails any test that
builds a path from the repo root into a root the cut withholds -- `public-overlay/`,
`work-items/`, `sessions/`, and every other row of the manifest's exclude list -- and
then USES it (opens, lists, copies, checks, passes it on), unless that test

  * skips by name when `PUBLIC-CUT-RECEIPT.md` exists (in its own body or decorators,
    its class's decorators or `setUp*`, a base class's, or `setUpModule`), or
  * is a helper whose every caller in the module does, or
  * is listed in `TRUNK_ONLY` below with the reason it is safe in the cut anyway.

WHICH PATHS ARE WITHHELD is asked of the cut itself: `public_cut.withheld_reason`, over
the same manifest the cut is built from. Nothing here restates the boundary, so a root
the manifest starts withholding tomorrow is checked tomorrow.

WHAT IT CANNOT SEE, said out loud: a path spelled as one string (`"work-items/x.md"`
with `cwd=ROOT`), an f-string, a path built in a helper module, and a read through a
module under test (`public_cut.adr_tree(ROOT)`). The nightly cut suite is the backstop
for those. This check is about the shape that broke 2026.10.4: a root-anchored path
expression in the test file.

stdlib unittest: python3 -m unittest discover -s tests
"""

import ast
import pathlib
import sys
import tempfile
import textwrap
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "curate"))

import public_cut  # noqa: E402

RECEIPT = public_cut.RECEIPT_NAME

#: Tests that use a withheld path and are still safe in the cut, each with its reason.
#: A key is `<file>::<Class>` or `<file>::<Class>.<method>` or `<file>::<function>`.
#: The bar: the test cannot fail in the cut for the path's absence. Prefer a by-name skip
#: on the receipt; list here only where the test already copes with the path missing.
TRUNK_ONLY = {
    "test_ci_linux_job.py::<module>":
        "not trunk-only: it reads the CI file from public-overlay/ when that exists and "
        "from .github/workflows/ci.yml (the manifest's `add` row) when it does not, so "
        "it runs, and passes, in both trees",
    "test_reconcile.py::RepoPathsConfigTest.test_missing_file_is_empty":
        "names a `.local` file that exists in no tree, on purpose: the test is that a "
        "missing file reads as empty, which holds in the cut too",
}

_PATH_CTORS = {"Path", "PurePath", "PosixPath", "PurePosixPath"}
_IDENTITY_ATTRS = {"resolve", "absolute", "expanduser"}
_OSPATH_IDENTITY = {"abspath", "realpath", "normpath", "fspath"}
_SETUP_NAMES = ("setUp", "setUpClass", "asyncSetUp")


def _dotted(node):
    """`os.path.join` -> "os.path.join"; None for anything that is not a plain chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Paths(object):
    """A tiny evaluator: which expressions in one test file are repo-relative paths.

    A value is ("path", parts) -- `parts` relative to the repo root -- or ("str", s), or
    None for anything it does not follow. `__file__` is the test file's own path; `.parent`,
    `.parents[n]`, `os.path.dirname` climb; `/`, `joinpath`, `os.path.join` and `Path(a, b)`
    descend. Climbing above the repo root is None: that is not a path into the cut."""

    def __init__(self, file_rel):
        self.file_parts = tuple(file_rel.split("/"))

    @staticmethod
    def _join(base, more):
        if base is None or base[0] != "path":
            return None
        parts = list(base[1])
        for m in more:
            if m is None or m[0] != "str":
                return None
            for seg in m[1].split("/"):
                if seg in ("", "."):
                    continue
                if seg == "..":
                    if not parts:
                        return None
                    parts.pop()
                else:
                    parts.append(seg)
        return ("path", tuple(parts))

    @staticmethod
    def _up(v, n=1):
        if v is None or v[0] != "path" or len(v[1]) < n:
            return None
        return ("path", v[1][:len(v[1]) - n])

    def value(self, node, env):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return ("str", node.value)
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return ("path", self.file_parts)
            return env.get(node.id)
        if isinstance(node, ast.Attribute):
            if node.attr == "parent":
                return self._up(self.value(node.value, env))
            name = _dotted(node)
            if name and name.split(".")[0] in ("self", "cls"):
                return env.get("." + node.attr)
            if name and name in env:
                return env.get(name)
            return None
        if isinstance(node, ast.Subscript):
            v = node.value
            if isinstance(v, ast.Attribute) and v.attr == "parents":
                idx = node.slice
                if isinstance(idx, ast.Index):  # pragma: no cover (Python < 3.9)
                    idx = idx.value
                if isinstance(idx, ast.Constant) and isinstance(idx.value, int):
                    return self._up(self.value(v.value, env), idx.value + 1)
            return None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return self._join(self.value(node.left, env), [self.value(node.right, env)])
        if isinstance(node, ast.Call):
            return self._call(node, env)
        return None

    def _call(self, node, env):
        f = node.func
        name = _dotted(f) or ""
        last = name.rsplit(".", 1)[-1]
        args = [self.value(a, env) for a in node.args]
        if last in _PATH_CTORS and args:
            first = args[0]
            if first is not None and first[0] == "str":
                return None  # a literal path is not anchored at the repo
            return self._join(first, args[1:])
        if name in ("str", "os.fspath") and len(args) == 1:
            return args[0] if args[0] and args[0][0] == "path" else None
        if name.startswith("os.path."):
            if last == "join" and args:
                return self._join(args[0], args[1:])
            if last == "dirname" and len(args) == 1:
                return self._up(args[0])
            if last in _OSPATH_IDENTITY and len(args) == 1:
                return args[0] if args[0] and args[0][0] == "path" else None
            return None
        if isinstance(f, ast.Attribute):
            if f.attr in _IDENTITY_ATTRS:
                return self.value(f.value, env)
            if f.attr == "joinpath":
                return self._join(self.value(f.value, env), args)
        return None


def _is_builder(parent, child):
    """Does `parent` only build a further path out of `child` (or bind it to a name)?"""
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div) and parent.left is child:
        return True
    if isinstance(parent, ast.Attribute) and parent.value is child:
        return parent.attr in {"parent", "parents", "joinpath"} | _IDENTITY_ATTRS
    if isinstance(parent, ast.Call) and child in parent.args:
        name = _dotted(parent.func) or ""
        last = name.rsplit(".", 1)[-1]
        return (last in _PATH_CTORS or name in ("os.path.join", "os.path.dirname")
                or name in ("os.path." + n for n in _OSPATH_IDENTITY))
    if isinstance(parent, ast.Call) and parent.func is child:
        return True  # `.resolve()` etc.: the Attribute above already decided
    if isinstance(parent, (ast.Assign, ast.AnnAssign)) and parent.value is child:
        return all(isinstance(t, (ast.Name, ast.Attribute))
                   for t in (parent.targets if isinstance(parent, ast.Assign)
                             else [parent.target]))
    return False


def _mentions(node, names):
    """Does `node` name the receipt: the literal, `RECEIPT_NAME`, or a module-level name
    already known to (`names`)?"""
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and RECEIPT in n.value:
            return True
        if isinstance(n, ast.Attribute) and n.attr in names | {"RECEIPT_NAME"}:
            return True
        if isinstance(n, ast.Name) and n.id in names | {"RECEIPT_NAME"}:
            return True
    return False


def _receipt_names(tree):
    """Module-level names whose definition mentions the receipt, to a fixpoint:
    `IN_CUT = (ROOT / "PUBLIC-CUT-RECEIPT.md").is_file()`, `def in_cut(): ...`."""
    names = set()
    while True:
        grew = False
        for st in tree.body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound = [st.name]
            elif isinstance(st, ast.Assign):
                bound = [t.id for t in st.targets if isinstance(t, ast.Name)]
            else:
                continue
            new = [b for b in bound if b not in names]
            if new and not isinstance(st, ast.ClassDef) and _mentions(st, names):
                names.update(new)
                grew = True
        if not grew:
            return names


class Hit(object):
    def __init__(self, qual, line, rel, why):
        self.qual, self.line, self.rel, self.why = qual, line, rel, why

    def __repr__(self):
        return "%s:%d %s" % (self.qual, self.line, self.rel)


def scan(source, file_rel, manifest):
    """Every use of a withheld path in one test file: [(Hit, guarded)]."""
    tree = ast.parse(source)
    ev = _Paths(file_rel)
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    rnames = _receipt_names(tree)
    hits, seen_hits = [], set()

    def bind(st, env):
        if isinstance(st, ast.Assign):
            val = ev.value(st.value, env)
            for t in st.targets:
                if isinstance(t, ast.Name):
                    env[t.id] = val
                elif isinstance(t, ast.Attribute) and (_dotted(t) or "").split(".")[0] in (
                        "self", "cls"):
                    env["." + t.attr] = val
        elif isinstance(st, ast.AnnAssign) and st.value is not None and isinstance(
                st.target, ast.Name):
            env[st.target.id] = ev.value(st.value, env)

    def look(nodes, env, qual):
        """Record the withheld paths `nodes` USE, binding names as statements pass."""
        for st in nodes:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            for n in ast.walk(st):
                if isinstance(getattr(n, "ctx", None), (ast.Store, ast.Del)):
                    continue
                v = ev.value(n, env) if isinstance(
                    n, (ast.BinOp, ast.Call, ast.Attribute, ast.Subscript, ast.Name)) else None
                if not v or v[0] != "path" or not v[1]:
                    continue
                rel = "/".join(v[1])
                why = public_cut.withheld_reason(rel, manifest)
                if why is None or _is_builder(parents.get(n), n):
                    continue
                key = (qual, getattr(n, "lineno", 0), rel)
                if key not in seen_hits:
                    seen_hits.add(key)
                    hits.append(Hit(qual, key[1], rel, why))
            # Bind after the statement's own uses, in order.
            if not isinstance(st, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                bind(st, env)
            else:
                for sub in ast.walk(st):
                    if isinstance(sub, (ast.Assign, ast.AnnAssign)):
                        bind(sub, env)

    def walk_defs(body, env, prefix, cls_env):
        for st in body:
            if isinstance(st, ast.ClassDef):
                cenv = dict(env)
                for s in st.body:  # class attributes, visible as self.X / cls.X
                    if isinstance(s, ast.Assign):
                        val = ev.value(s.value, cenv)
                        for t in s.targets:
                            if isinstance(t, ast.Name):
                                cenv[t.id] = val
                                cenv["." + t.id] = val
                # Instance attributes set in a setUp* are what every method of the class
                # sees as `self.X`.
                for m in st.body:
                    if isinstance(m, ast.FunctionDef) and m.name.startswith("setUp"):
                        for sub in ast.walk(m):
                            if isinstance(sub, ast.Assign):
                                bind(sub, cenv)
                q = prefix + st.name
                look(st.decorator_list, dict(env), q)
                look(st.body, dict(cenv), q)
                walk_defs(st.body, cenv, q + ".", cenv)
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fenv = dict(env)
                q = prefix + st.name
                look(st.decorator_list, fenv, q)
                look(st.body, fenv, q)
                walk_defs(st.body, fenv, q + ".", cls_env)

    env = {}
    look(tree.body, env, "<module>")
    walk_defs(tree.body, env, "", {})
    return [(h, _guarded(tree, h.qual, rnames)) for h in hits]


def _defs(tree):
    """qualname -> (node, enclosing ClassDef chain) for every def and class."""
    out = {}

    def go(body, prefix, classes):
        for st in body:
            if isinstance(st, ast.ClassDef):
                q = prefix + st.name
                out[q] = (st, classes)
                go(st.body, q + ".", classes + [st])
            elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = prefix + st.name
                out[q] = (st, classes)
                go(st.body, q + ".", classes)
    go(tree.body, "", [])
    return out


def _class_guarded(cls, tree, rnames, seen=()):
    if cls.name in seen:
        return False
    if any(_mentions(d, rnames) for d in cls.decorator_list):
        return True
    for m in cls.body:
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in _SETUP_NAMES \
                and _mentions(m, rnames):
            return True
    local = {c.name: c for c in tree.body if isinstance(c, ast.ClassDef)}
    for b in cls.bases:
        if isinstance(b, ast.Name) and b.id in local and _class_guarded(
                local[b.id], tree, rnames, tuple(seen) + (cls.name,)):
            return True
    return False


def _guarded(tree, qual, rnames, seen=None):
    """Is the code at `qual` skipped by name when the receipt exists?"""
    seen = set() if seen is None else seen
    if qual in seen:
        return False
    seen.add(qual)
    for st in tree.body:
        if isinstance(st, ast.FunctionDef) and st.name == "setUpModule" and _mentions(st, rnames):
            return True
        if isinstance(st, ast.If) and _mentions(st.test, rnames) and any(
                isinstance(n, ast.Raise) for n in ast.walk(st)):
            return True
    defs = _defs(tree)
    if qual not in defs:
        return False
    parts = qual.split(".")
    for i in range(len(parts), 0, -1):  # the def itself, then every enclosing def
        node, classes = defs[".".join(parts[:i])]
        if isinstance(node, ast.ClassDef):
            if _class_guarded(node, tree, rnames):
                return True
        elif _mentions(node, rnames):
            return True
    node, classes = defs[qual]
    if isinstance(node, ast.ClassDef) or node.name.startswith("test"):
        return False
    # A helper: guarded when every def in the module that calls it is.
    callers = []
    for q, (n, _c) in defs.items():
        if q == qual or isinstance(n, ast.ClassDef):
            continue
        for sub in ast.walk(n):
            if sub is node:
                break
            if (isinstance(sub, ast.Attribute) and sub.attr == node.name) or (
                    isinstance(sub, ast.Name) and sub.id == node.name):
                callers.append(q)
                break
    callers = [q for q in callers if not any(
        o != q and o.startswith(q + ".") for o in callers)]  # innermost caller only
    return bool(callers) and all(_guarded(tree, q, rnames, seen) for q in callers)


def _listed(file_name, qual, listing):
    parts = qual.split(".")
    return any("%s::%s" % (file_name, ".".join(parts[:i])) in listing
               for i in range(1, len(parts) + 1))


def offenders(test_dir, manifest, listing):
    """[(file, Hit)] for every unguarded, unlisted use of a withheld path."""
    out = []
    for f in sorted(pathlib.Path(test_dir).glob("test_*.py")):
        rel = "tests/" + f.name
        for hit, guarded in scan(f.read_text(encoding="utf-8"), rel, manifest):
            if not guarded and not _listed(f.name, hit.qual, listing):
                out.append((f.name, hit))
    return out


def _report(found):
    lines = ["%d test(s) use a path the public cut withholds, with no by-name skip:"
             % len(found)]
    for name, h in found:
        lines.append("  tests/%s:%d %s uses %s/ (%s)" % (name, h.line, h.qual, h.rel, h.why))
    lines.append("Fix: skip it by name when %s exists, e.g. @unittest.skipIf((REPO / %r)"
                 ".is_file(), \"public cut: <path> is not shipped\"); or, if it already "
                 "copes with the path missing, list it in TRUNK_ONLY in "
                 "tests/test_cut_withheld_reads.py with the reason." % (RECEIPT, RECEIPT))
    return "\n".join(lines)


class TheLiveSuiteTest(unittest.TestCase):

    def test_no_test_uses_a_withheld_path_without_a_by_name_skip(self):
        man = public_cut.load_manifest()
        found = offenders(REPO / "tests", man, TRUNK_ONLY)
        self.assertEqual(found, [], _report(found))

    def test_every_trunk_only_row_names_a_def_that_exists_and_says_why(self):
        # A row naming nothing reads exactly like a row that works.
        for key, why in TRUNK_ONLY.items():
            name, _sep, qual = key.partition("::")
            self.assertTrue(why.strip(), key)
            src = REPO / "tests" / name
            self.assertTrue(src.is_file(), key)
            if qual != "<module>":
                self.assertIn(qual, _defs(ast.parse(src.read_text(encoding="utf-8"))), key)


class TheCheckFiresTest(unittest.TestCase):
    """A fixture test file, read the three ways the rule allows and the one it refuses.

    The roots are real rows of the real manifest -- `public-overlay/adr` is the path that
    broke 2026.10.4 -- so the fixture is a shape that can occur, not one the predicate
    never sees."""

    HEAD = textwrap.dedent('''\
        import pathlib, shutil, unittest
        REPO = pathlib.Path(__file__).resolve().parent.parent
        ''')

    def setUp(self):
        self.man = public_cut.load_manifest()
        self.dir = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)

    def _offenders(self, body, listing=None):
        (self.dir / "test_fixture.py").write_text(self.HEAD + textwrap.dedent(body))
        return offenders(self.dir, self.man, listing or {})

    def test_the_predicate_answers_from_the_manifest(self):
        for rel in ("public-overlay/adr", "work-items", "sessions/journal"):
            self.assertIsNotNone(public_cut.withheld_reason(rel, self.man), rel)
        for rel in ("adr", "tests/test_x.py", ".git", RECEIPT, "portfolio.md"):
            self.assertIsNone(public_cut.withheld_reason(rel, self.man), rel)

    def test_an_unguarded_read_is_flagged_by_name(self):
        found = self._offenders('''
            class MutationTest(unittest.TestCase):
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            ''')
        self.assertEqual([(n, h.qual, h.rel) for n, h in found],
                         [("test_fixture.py", "MutationTest.test_copy",
                           "public-overlay/adr")])
        self.assertIn("MutationTest.test_copy", _report(found))

    def test_the_real_2026_10_4_defect_is_flagged_without_its_skip(self):
        # The test that broke the cut, as it landed: its class with the by-name skip the
        # cut lane added taken off again. Guarded as it is; flagged without the skip.
        src = (REPO / "tests" / "test_public_adr_index.py").read_text(encoding="utf-8")
        rel = "tests/test_public_adr_index.py"
        self.assertTrue(any(h.qual.startswith("MutationTest.") and g
                            for h, g in scan(src, rel, self.man)))
        tree = ast.parse(src)
        cls = [n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MutationTest"][0]
        self.assertTrue(cls.decorator_list)
        cls.decorator_list = []
        bare = [h for h, g in scan(ast.unparse(tree), rel, self.man) if not g]
        self.assertTrue(bare)
        self.assertTrue(all(h.qual.startswith("MutationTest.") for h in bare), bare)
        self.assertIn("public-overlay/adr", {h.rel for h in bare})

    def test_each_root_shape_is_flagged(self):
        found = self._offenders('''
            import os
            OVERLAY = REPO / "public-overlay"
            def helper_free(): pass
            class T(unittest.TestCase):
                def setUp(self):
                    self.store = REPO.joinpath("work-items")
                def test_a(self):
                    OVERLAY.iterdir()
                def test_b(self):
                    list(self.store.glob("*.md"))
                def test_c(self):
                    open(os.path.join(REPO, "sessions", "journal", "x.md"))
                def test_d(self):
                    (pathlib.Path(__file__).parents[1] / "comms").is_dir()
            ''')
        self.assertEqual(sorted((h.qual, h.rel) for _n, h in found),
                         [("T.test_a", "public-overlay"), ("T.test_b", "work-items"),
                          ("T.test_c", "sessions/journal/x.md"), ("T.test_d", "comms")])

    def test_a_path_that_only_builds_or_ships_is_not_flagged(self):
        found = self._offenders('''
            OVERLAY = REPO / "public-overlay"
            class T(unittest.TestCase):
                def test_sample(self):
                    (REPO / "work-items" / "WI-0290-the-adr-counter-still-has-no-renumberer.md"
                     ).read_text()
                def test_tmp(self):
                    root = pathlib.Path("/tmp/x")
                    (root / "sessions").mkdir()
                def test_shipped(self):
                    (REPO / "adr").iterdir()
            ''')
        self.assertEqual(found, [])

    def test_a_skip_on_the_method_passes(self):
        self.assertEqual(self._offenders('''
            class MutationTest(unittest.TestCase):
                @unittest.skipIf((REPO / "PUBLIC-CUT-RECEIPT.md").is_file(), "public cut")
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            '''), [])

    def test_a_skip_on_the_class_or_its_setup_or_a_named_flag_passes(self):
        self.assertEqual(self._offenders('''
            IN_CUT = (REPO / "PUBLIC-CUT-RECEIPT.md").is_file()
            @unittest.skipIf(IN_CUT, "public cut")
            class A(unittest.TestCase):
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            class B(unittest.TestCase):
                def setUp(self):
                    if (REPO / "PUBLIC-CUT-RECEIPT.md").is_file():
                        self.skipTest("public cut")
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            class C(B):
                def test_also(self):
                    (REPO / "sessions").iterdir()
            '''), [])

    def test_a_helper_is_guarded_only_by_every_caller(self):
        body = '''
            class T(unittest.TestCase):
                def _load(self):
                    return (REPO / "work-items").iterdir()
                @unittest.skipIf((REPO / "PUBLIC-CUT-RECEIPT.md").is_file(), "cut")
                def test_one(self):
                    self._load()
            '''
        self.assertEqual(self._offenders(body), [])
        found = self._offenders(body + '''
                def test_two(self):
                    self._load()
            ''')
        self.assertEqual([h.qual for _n, h in found], ["T._load"])

    def test_a_trunk_only_listing_passes_and_only_the_named_def(self):
        body = '''
            class MutationTest(unittest.TestCase):
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            class Other(unittest.TestCase):
                def test_copy(self):
                    shutil.copytree(REPO / "public-overlay" / "adr", "/tmp/x")
            '''
        found = self._offenders(body, {"test_fixture.py::MutationTest": "copes"})
        self.assertEqual([h.qual for _n, h in found], ["Other.test_copy"])
        self.assertEqual(self._offenders(body, {
            "test_fixture.py::MutationTest.test_copy": "copes",
            "test_fixture.py::Other": "copes"}), [])


if __name__ == "__main__":
    unittest.main()
