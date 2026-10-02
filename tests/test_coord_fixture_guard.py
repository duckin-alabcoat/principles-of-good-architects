"""The detector for `coord_fixture` (WI-0126) — a guard nobody can forget.

The defect this closes was not that the fix was hard; it was that the fix was per-module.
Two modules carried it, six did not, and nothing anywhere could tell you that. A new test
that writes a coordination record inherits the same silent journal-sharing bug, passes on
the author's machine, and fails on someone else's — the exact shape
[`ship-the-detector-with-the-capability`](../habits/master.md#ship-the-detector-with-the-capability)
names: a capability with no marker reads as absent to the surface that should show it.

The rule is mechanical and needs no exemption list, which is why it was chosen: a module
that CALLS a coordination function writes `session._coord_`; a module that merely mocks
one out (`mock.patch.object(session, "_coord_reap", ...)`) or names one in prose does not.
That distinction picks out exactly the modules that build fictional holders.
"""

import pathlib
import re
import unittest

TESTS = pathlib.Path(__file__).resolve().parent

#: A real call into the coordination layer, as opposed to a mock target or a prose mention.
CALLS_COORD = re.compile(r"session\._coord_")
#: The guard, however the module reaches it.
APPLIES_GUARD = re.compile(r"neutralize_coord_journal\s*\(")


def _modules():
    for p in sorted(TESTS.glob("test_*.py")):
        yield p, p.read_text(encoding="utf-8")


class CoordWritersCarryTheGuardTest(unittest.TestCase):
    def test_every_module_that_writes_coord_records_neutralizes_the_journal(self):
        missing = [p.name for p, src in _modules()
                   if CALLS_COORD.search(src) and not APPLIES_GUARD.search(src)]
        self.assertEqual(missing, [], (
            "these modules write coordination records but never call "
            "`neutralize_coord_journal(self)`, so their fictional holders inherit the "
            "ambient session's journal and read as one holder (WI-0126). Import it from "
            "`coord_fixture` and call it first in the fixture's setUp; if the module is "
            "genuinely ABOUT holder resolution, call it and then "
            "`exercise_real_coord_holder(self)` in that class, which says so by name."))

    def test_the_guard_is_reached_through_the_shared_module(self):
        """One implementation, not N copies — the copies are how six modules drifted."""
        for p, src in _modules():
            if APPLIES_GUARD.search(src):
                self.assertIn("coord_fixture", src,
                              f"{p.name} calls the guard without importing the one that "
                              "is under test; a local re-definition is the duplication "
                              "this module exists to remove")

    def test_the_detector_would_actually_fire(self):
        """A check that cannot fail is not a check
        ([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration))."""
        unguarded = "x = session._coord_try_acquire('claims', 'WI-0001', 'lane', 60)\n"
        self.assertTrue(CALLS_COORD.search(unguarded))
        self.assertFalse(APPLIES_GUARD.search(unguarded))
        mocked_only = 'p = mock.patch.object(session, "_coord_reap", side_effect=OSError)\n'
        self.assertIsNone(CALLS_COORD.search(mocked_only),
                          "mocking a coord function out is not writing a record")

    def test_at_least_one_module_is_actually_covered(self):
        """Guards against the rule silently matching nothing (an empty sweep reads green)."""
        covered = [p.name for p, src in _modules() if CALLS_COORD.search(src)]
        self.assertGreaterEqual(len(covered), 8, covered)


if __name__ == "__main__":
    unittest.main()
