"""A test may not permanently replace a FUNCTION on the shared `session` module.

THE DEFECT THIS EXISTS FOR, measured rather than imagined. `test_notes_file.py` shipped
09-12 (WI-0289) with two bare assignments and no restore:

    session._wi_reserve_next = lambda *a, **k: 7
    session._ops_reserve_next = lambda *a, **k: 7

Stubbing the draw was RIGHT -- the coord store is not that file's subject. The bare
assignment was the defect. `sessionlib` is exec'd into one shared `session` namespace, so
those names are process-global; nothing re-assigns a function, and the stub survived to
the end of the run. Four allocator tests in two other files went red -- two drawing the
lambda's hardcoded 7 where 6 and 10 were asserted, two looking for a reservation record
the stub never wrote. All four passed in isolation. The trunk was red for two days.

WHY DATA GLOBALS ARE NOT THE SUBJECT, which is the whole reason this guard is narrow
enough to exist. Every fixture here assigns `session.ROOT` / `CFG` / `JOURNAL_DIR` in its
own `setUp`, so a leaked DATA global is overwritten by the next test that runs and cannot
reach across files. Measured before choosing the scope, because a guard that fires on
correct code gets deleted:

    any unrestored `session.<attr> = ...`              350 sites -- unusable
    unrestored replacement of BEHAVIOUR (lambda/Mock)    5 sites
    ...the same, counting `try/finally` as a restore     0 sites

All five in the middle row restore via `try/finally` (`test_releases`,
`test_remote_provenance`, `test_residency`) and are correct. So this guard fires on
NOTHING that exists today and catches exactly the shape that cost the two days.

WHY IT IS A GUARD AND NOT A NOTE. The land gate runs the suite SHARDED, and sharding
changes the order, so the gate is structurally incapable of seeing order-dependent
contamination -- three lands succeeded on the red trunk while it was being diagnosed. The
only serial full-suite run is the nightly OPS-0009 derive. A defect class invisible to the
thing that gates every land is exactly the case for a structural guard
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).

THE REMEDY the failure prints is the shape `test_notes_file.py` now uses: save the
original, hand it to `addCleanup`, then patch. `try/finally` is equally accepted.
"""

import ast
import pathlib
import sys
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
# The module walker is the sibling guards', IMPORTED rather than restated -- two copies of
# "which files are test modules" is the duplication the fixture guards exist to prevent
# ([P16](../principles/master.md#p16--avoid-duplication)).
from test_dispatch_fixture_guard import _modules  # noqa: E402

#: Call names that produce a stand-in callable. `patch`/`patch.object` are included for
#: completeness even though nothing uses them yet: the point of a guard is to hold for the
#: shape, not for today's instances.
MOCKISH = ("Mock", "MagicMock", "AsyncMock", "patch", "create_autospec")

REMEDY = (
    "save the original and restore it, e.g.\n"
    "        original = session.<name>\n"
    "        self.addCleanup(setattr, session, \"<name>\", original)\n"
    "        session.<name> = <stub>\n"
    "    or wrap the call in try/finally. Both are accepted. `tests/test_notes_file.py`'s\n"
    "    `stub_draw` is the worked example."
)


def _is_behaviour(value, local_defs) -> bool:
    """Does this value REPLACE BEHAVIOUR, as opposed to setting a data global?"""
    if isinstance(value, (ast.Lambda,)):
        return True
    if isinstance(value, ast.Call):
        f = value.func
        name = getattr(f, "attr", None) or getattr(f, "id", None)
        if name in MOCKISH:
            return True
    # `def _fake_draw(...): ...` then `session.x = _fake_draw` -- a named local function is
    # the same replacement written in two statements.
    if isinstance(value, ast.Name) and value.id in local_defs:
        return True
    return False


def _local_defs(fn) -> set:
    return {n.name for n in ast.walk(fn) if isinstance(n, (ast.FunctionDef,
                                                           ast.AsyncFunctionDef))}


def _session_attr_targets(node):
    """`session.<attr> = ...` and `setattr(session, "<attr>", ...)`, with their values."""
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "session"):
                    yield t.attr, n.value, n.lineno
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "setattr" and len(n.args) == 3
                and isinstance(n.args[0], ast.Name) and n.args[0].id == "session"):
            key = n.args[1]
            attr = key.value if isinstance(key, ast.Constant) else "<computed>"
            yield attr, n.args[2], n.lineno


def _written_session_attrs(node) -> set:
    """Every `session.<attr>` this node WRITES, including the tuple form
    `session.A, session.B = old_a, old_b` that the real restores use."""
    out = set()
    for attr, _value, _lineno in _session_attr_targets(node):
        out.add(attr)
    for n in ast.walk(node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                for el in getattr(t, "elts", []):
                    if (isinstance(el, ast.Attribute) and isinstance(el.value, ast.Name)
                            and el.value.id == "session"):
                        out.add(el.attr)
    return out


def _restores(fn, attr) -> bool:
    """Does this function put back THIS name?

    THE ATTRIBUTE IS MATCHED, NOT JUST THE MODULE, and that is the whole correctness of
    this guard. The first draft counted any `session` write in a `tearDown` as a restore.
    `NotesFileBase.tearDown` restores `session.ROOT` -- so that draft reported ZERO
    offenders on the very file whose unrestored `_wi_reserve_next` caused the outage.
    Restoring one name says nothing about another, and a guard generous enough to blur
    them is a guard that passes on its own founding defect. Caught by running the draft
    against the real pre-fix file instead of only against a hand-written sample.

    `addCleanup` stays name-blind on purpose: the sanctioned helper passes the name as a
    VARIABLE (`self.addCleanup(setattr, session, name, original)`), so there is no literal
    to match, and demanding one would push fixtures back toward the bare assignment.
    """
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "addCleanup"):
            return True
        if isinstance(n, ast.Try) and n.finalbody:
            for f in n.finalbody:
                if attr in _written_session_attrs(f):
                    return True
    return False


def _teardown_restores(cls) -> set:
    """The names a class's own teardown puts back -- a SET, so the caller can ask about
    the one it cares about rather than about the module as a whole."""
    out = set()
    for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
        if fn.name in ("tearDown", "tearDownClass"):
            out |= _written_session_attrs(fn)
    return out


def offenders(src: str, filename: str = "<src>") -> list:
    """Every unrestored behaviour-replacement in `src`, as (line, class, func, attr)."""
    tree = ast.parse(src, filename=filename)
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    by_name = {c.name: c for c in classes}
    out = []
    for cls in classes:
        safe_attrs = _teardown_restores(cls)
        for base in cls.bases:
            if isinstance(base, ast.Name) and base.id in by_name:
                safe_attrs |= _teardown_restores(by_name[base.id])
        for fn in [n for n in cls.body if isinstance(n, ast.FunctionDef)]:
            defs = _local_defs(fn)
            for attr, value, lineno in _session_attr_targets(fn):
                if not _is_behaviour(value, defs):
                    continue
                if attr in safe_attrs or _restores(fn, attr):
                    continue
                out.append((lineno, cls.name, fn.name, attr))
    return out


class NoUnrestoredStubTest(unittest.TestCase):
    """The sweep itself."""

    def test_no_test_module_leaves_a_stub_on_the_session_module(self):
        found = []
        for path, src in _modules():
            for lineno, cls, fn, attr in offenders(src, path.name):
                found.append("%s:%d  %s.%s  ->  session.%s" % (path.name, lineno, cls,
                                                               fn, attr))
        self.assertEqual(found, [], "\n\nA test replaces a function on the shared "
                         "`session` module and never puts it back. Nothing re-assigns a "
                         "function, so the stub outlives this test and every later caller "
                         "in the process gets it -- which is how four allocator tests in "
                         "other files went red for two days (WI-0289).\n\n    " + REMEDY
                         + "\n\nOffenders:\n  " + "\n  ".join(found))


class TheDetectorFiresTest(unittest.TestCase):
    """A detector that cannot be shown to fire is indistinguishable from a broken one, and
    an `assertEqual(found, [])` over a predicate that can never match passes for free."""

    def test_it_fires_on_the_exact_defect_it_was_written_for(self):
        """The real pre-fix source from `test_notes_file.py`, verbatim in shape."""
        src = (
            "class T(Base):\n"
            "    def test_wi_new_writes_the_file_body_verbatim(self):\n"
            "        session._wi_reserve_next = lambda *a, **k: 7\n"
            "        self.assertEqual(1, 1)\n"
        )
        got = offenders(src)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][3], "_wi_reserve_next")

    def test_it_fires_on_the_setattr_spelling(self):
        src = ("class T(Base):\n"
               "    def test_x(self):\n"
               "        setattr(session, '_ops_reserve_next', lambda *a, **k: 7)\n")
        self.assertEqual(len(offenders(src)), 1)

    def test_it_fires_on_a_named_local_function(self):
        src = ("class T(Base):\n"
               "    def test_x(self):\n"
               "        def _fake(*a, **k):\n"
               "            return 7\n"
               "        session._wi_reserve_next = _fake\n")
        self.assertEqual(len(offenders(src)), 1)

    def test_it_fires_on_a_mock(self):
        src = ("class T(Base):\n"
               "    def test_x(self):\n"
               "        session._rel_commit_record = Mock()\n")
        self.assertEqual(len(offenders(src)), 1)


class TheDetectorIsQuietOnCorrectCodeTest(unittest.TestCase):
    """The other half. Each of these is a shape that EXISTS in the suite today; flagging
    any of them is what would get this guard deleted rather than fixed."""

    def test_try_finally_is_a_restore(self):
        """`test_releases`, `test_remote_provenance`, `test_residency` all use this."""
        src = ("class T(Base):\n"
               "    def _calls(self, args):\n"
               "        real = session._wi_write_item\n"
               "        session._wi_write_item = lambda it: None\n"
               "        try:\n"
               "            self.run_cmd()\n"
               "        finally:\n"
               "            session._wi_write_item = real\n")
        self.assertEqual(offenders(src), [])

    def test_the_tuple_finally_is_a_restore(self):
        src = ("class T(Base):\n"
               "    def _verdict(self, cfg):\n"
               "        old_cfg, old_detect = session.CFG, session.detect_machine\n"
               "        try:\n"
               "            session.detect_machine = lambda: 'x'\n"
               "        finally:\n"
               "            session.CFG, session.detect_machine = old_cfg, old_detect\n")
        self.assertEqual(offenders(src), [])

    def test_addcleanup_is_a_restore(self):
        """The shape `test_notes_file.stub_draw` now uses."""
        src = ("class T(Base):\n"
               "    def stub_draw(self, name, n=7):\n"
               "        original = getattr(session, name)\n"
               "        self.addCleanup(setattr, session, name, original)\n"
               "        setattr(session, name, lambda *a, **k: n)\n")
        self.assertEqual(offenders(src), [])

    def test_a_data_global_is_not_the_subject(self):
        """245 assignments in the suite look like this. Flagging them is the 350-site
        version of this guard that would have been unusable."""
        src = ("class T(Base):\n"
               "    def test_x(self):\n"
               "        session.ROOT = self.tmp\n"
               "        session.CFG = {'trunk': 'main'}\n")
        self.assertEqual(offenders(src), [])

    def test_a_teardown_in_the_base_class_covers_its_subclasses(self):
        src = ("class Base(unittest.TestCase):\n"
               "    def tearDown(self):\n"
               "        session._wi_reserve_next = self._orig\n"
               "class T(Base):\n"
               "    def test_x(self):\n"
               "        session._wi_reserve_next = lambda *a, **k: 7\n")
        self.assertEqual(offenders(src), [])

    def test_restoring_a_DIFFERENT_name_does_not_excuse_this_one(self):
        """The regression that made the first draft useless. `NotesFileBase.tearDown`
        restores `session.ROOT`; the leak was `_wi_reserve_next`. A guard that treats the
        first as covering the second reports zero offenders on its own founding defect."""
        src = ("class Base(unittest.TestCase):\n"
               "    def tearDown(self):\n"
               "        session.ROOT = self._root\n"
               "class T(Base):\n"
               "    def test_x(self):\n"
               "        session._wi_reserve_next = lambda *a, **k: 7\n")
        got = offenders(src)
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0][3], "_wi_reserve_next")


if __name__ == "__main__":
    unittest.main()
