"""ADR-0095 D2/D3/D6 — the declared data root, and the three states it must keep apart.

ADR-0009 decided a data root on 2026-05-23 and the *rejected* option is what shipped, so
the thing under test here is the indirection itself: a member's data is reached through a
declared root, never through a repo-relative literal and never through an absolute path
baked into a shared file.

The `$HOME` branch is load-bearing rather than cosmetic. The same home directory can
sit behind a different path prefix on each machine that reaches it, so any fix that writes an absolute data-root literal into a
tracked or fleet-shared file is wrong on one of the two machines by construction.

Every case drives `env` explicitly. A test that read the real environment would pass on
the machine that wrote it and prove nothing about the other one.
"""

import importlib.util
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_spec = importlib.util.spec_from_file_location(
    "session_dr", pathlib.Path(__file__).resolve().parent.parent / "session.py")
session = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(session)


FED = {"architect_id": "federation-arch",
       "data": ["proposed-edits", "users", "architect-learnings.md"]}


class SystemIdTest(unittest.TestCase):
    def test_derived_from_architect_id_per_adr_0006(self):
        self.assertEqual(session.system_id_of({"architect_id": "federation-arch"}),
                         "federation")

    def test_an_explicit_system_id_wins(self):
        """The convention is a derivation rule, not a law. A member that renames one half of its
        identity before the other and needs somewhere to say so."""
        self.assertEqual(
            session.system_id_of({"architect_id": "new-name-arch", "system_id": "old-name"}),
            "old-name")

    def test_an_id_without_the_suffix_is_left_alone(self):
        self.assertEqual(session.system_id_of({"architect_id": "example"}), "example")

    def test_no_config_is_empty_not_an_exception(self):
        self.assertEqual(session.system_id_of(None), "")


class ResolutionOrderTest(unittest.TestCase):
    def test_the_env_var_wins(self):
        root, state, why = session.data_root_of(FED, {"POGA_DATA_ROOT": "/srv/data",
                                                      "HOME": "/Users/operator"})
        self.assertEqual(root, pathlib.Path("/srv/data"))
        self.assertEqual(state, session.DATA_ROOT_RESOLVED)
        self.assertIn("POGA_DATA_ROOT", why)

    def test_xdg_beats_home(self):
        root, _s, _w = session.data_root_of(FED, {"XDG_DATA_HOME": "/x",
                                                  "HOME": "/Users/operator"})
        self.assertEqual(root, pathlib.Path("/x/poga/federation"))

    def test_home_is_the_default_and_is_per_system(self):
        root, _s, _w = session.data_root_of(FED, {"HOME": "/Users/operator"})
        self.assertEqual(root, pathlib.Path("/Users/operator/.local/share/poga/federation"))

    def test_the_same_config_resolves_differently_on_the_two_machines(self):
        """The whole reason `$HOME` is the mechanism. Neither machine stores the other's
        prefix anywhere, and the config is byte-identical across both."""
        machine_a, _s, _w = session.data_root_of(FED, {"HOME": "/Users/operator"})
        machine_b, _s2, _w2 = session.data_root_of(FED, {"HOME": "/home/operator"})
        self.assertEqual(machine_a, pathlib.Path(
            "/Users/operator/.local/share/poga/federation"))
        self.assertEqual(machine_b, pathlib.Path(
            "/home/operator/.local/share/poga/federation"))

    def test_two_members_never_share_a_root(self):
        a, _s, _w = session.data_root_of({"architect_id": "federation-arch"},
                                         {"HOME": "/h"})
        b, _s2, _w2 = session.data_root_of({"architect_id": "example-app-arch"}, {"HOME": "/h"})
        self.assertNotEqual(a, b)

    def test_an_empty_env_var_does_not_count_as_set(self):
        """An exported-but-blank variable is the classic launchd/CI shape. Treating it as
        set resolves the root to the filesystem root."""
        root, _s, _w = session.data_root_of(FED, {"POGA_DATA_ROOT": "   ",
                                                  "HOME": "/Users/operator"})
        self.assertEqual(root, pathlib.Path("/Users/operator/.local/share/poga/federation"))


class RefusesToGuessTest(unittest.TestCase):
    """The failure this ADR exists to end is a confident wrong answer, so every branch that
    cannot resolve honestly must say so rather than produce a plausible path."""

    def test_no_home_at_all_is_undeclared_not_a_guess(self):
        """`pathlib.Path.home()` would fall back to the password database and hand back a
        REAL directory that is not where anybody's data is. That is the silent class."""
        root, state, why = session.data_root_of(FED, {})
        self.assertIsNone(root)
        self.assertEqual(state, session.DATA_ROOT_UNDECLARED)
        self.assertIn("refusing to guess", why.lower())

    def test_no_architect_id_is_undeclared(self):
        root, state, _w = session.data_root_of({"data": ["users"]}, {"HOME": "/h"})
        self.assertIsNone(root)
        self.assertEqual(state, session.DATA_ROOT_UNDECLARED)

    def test_why_is_populated_even_on_success(self):
        """A detector whose good path says nothing cannot be told from one that did not
        run."""
        _r, _s, why = session.data_root_of(FED, {"HOME": "/h"})
        self.assertTrue(why.strip())


class DeclaredPathsTest(unittest.TestCase):
    def test_reads_the_members_own_declaration(self):
        self.assertEqual(session.data_paths_of(FED),
                         ["proposed-edits", "users", "architect-learnings.md"])

    def test_no_declaration_is_empty_never_the_federations_set(self):
        """`SHARED_LANE_PATHS` enumerated the FEDERATION's data paths inside byte-identical
        fleet substrate, so every member inherited our idea of which data exists. An empty
        declaration must mean 'declares no shared data', which is correct for a fresh
        Architect — never 'fall back to ours'."""
        self.assertEqual(session.data_paths_of({"architect_id": "fresh-arch"}), [])

    def test_absolute_paths_are_dropped(self):
        """A data path is relative to the root by definition; an absolute entry would
        escape it and reintroduce the two-machine problem inside the declaration."""
        self.assertEqual(session.data_paths_of({"data": ["users", "/etc/passwd"]}),
                         ["users"])

    def test_junk_entries_do_not_break_the_read(self):
        self.assertEqual(session.data_paths_of({"data": ["ok", 3, "", None]}), ["ok"])

    def test_a_non_list_declaration_is_empty(self):
        self.assertEqual(session.data_paths_of({"data": "users"}), [])


class ThreeStatesTest(unittest.TestCase):
    """D6. 'Cannot see it' and 'nothing there' must never share an output — that collapse
    is the originating defect of this neighbourhood (session 90 printed `inbox: (empty)`
    over two live briefs, one of them time-boxed)."""

    def setUp(self):
        import shutil
        import tempfile
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_declared_and_present_is_resolved(self):
        (self.tmp / "root").mkdir()
        _r, state, _w = session.data_root_state(
            FED, {"POGA_DATA_ROOT": str(self.tmp / "root")})
        self.assertEqual(state, session.DATA_ROOT_RESOLVED)

    def test_declared_and_absent_is_UNREADABLE_not_undeclared(self):
        root, state, why = session.data_root_state(
            FED, {"POGA_DATA_ROOT": str(self.tmp / "missing")})
        self.assertEqual(state, session.DATA_ROOT_UNREADABLE)
        self.assertIsNotNone(root, "name the path that is missing, not just the verdict")
        self.assertIn("not the same as having none", why)

    def test_declaring_no_data_is_not_an_unreadable_root(self):
        """A member with no shared data must not be reported as broken. The state depends
        on whether data was DECLARED, not on whether a directory happens to exist."""
        _r, state, why = session.data_root_state(
            {"architect_id": "fresh-arch"}, {"POGA_DATA_ROOT": str(self.tmp / "missing")})
        self.assertEqual(state, session.DATA_ROOT_RESOLVED)
        self.assertIn("no `data` paths", why)

    def test_unresolvable_stays_undeclared_through_the_readability_check(self):
        _r, state, _w = session.data_root_state(FED, {})
        self.assertEqual(state, session.DATA_ROOT_UNDECLARED)

    def test_the_three_states_are_distinct_values(self):
        self.assertEqual(len({session.DATA_ROOT_RESOLVED, session.DATA_ROOT_UNREADABLE,
                              session.DATA_ROOT_UNDECLARED}), 3)


if __name__ == "__main__":
    unittest.main()
