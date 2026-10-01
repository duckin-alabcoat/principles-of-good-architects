"""The floor that stops the suite writing into a REAL session's coordination state.

WI-0286 / ADR-0133. Six instances of "our evidence is not separated from our state" were
collected in one session, and the answer shipped for the worst of them (WI-0270) was a
write-side refusal keyed on the *shape of the session id*. That rule is sound and it is
not enough: it refuses a FORGED stem at the real store and cannot see a test that
inherited the operator's real `CLAUDE_CODE_SESSION_ID`, whose stem is well-formed by
construction.

MEASURED 2026-09-13 (session ~314), audit hook on `open` across a full 4,234-test run:
**52 writes landed in a real `.session-state/` and the shipped floor passed every one**.
35 were `announce.txt`, from a module whose `setUp` pins `SESSION_STATE_DIR` correctly and
which is green under `test_live_store_fixture_guard` — because the banner path was a
module constant computed FROM that directory at import, so redirecting the directory did
not move it. The other anchor with the same shape, `GUARD_FIRINGS_FILE`, had already been
converted to call-time resolution on 2026-08-07 after 432 of 532 records in the real
denial log turned out to be fixtures. The banner was the half nobody went back for.

So there are two halves here and both are tested below: the anchor family collapses to
ONE movable directory, and `atomic_write` refuses anything still aimed at a real store.
"""

import pathlib
import sys
import tempfile
import unittest

TESTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))
import session  # noqa: E402


class RealStoreSetTest(unittest.TestCase):
    """`_real_live_stores` must answer from handles a fixture cannot move."""

    def test_the_launch_store_is_listed(self):
        self.assertIn(session._LAUNCH_SESSION_STATE_DIR.resolve(),
                      session._real_live_stores())

    def test_rebinding_the_movable_anchors_does_not_change_the_answer(self):
        """THE NEGATIVE CONTROL FOR THE BUG THIS GUARD SHIPPED WITH.

        The first cut asked `_shared_work_root()`, which fixtures patch — so under a
        fixture it returned the tmpdir and the floor refused correct writes into it. A
        question about what is REAL cannot be put to a handle a test may move."""
        before = list(session._real_live_stores())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        fake = pathlib.Path(tmp.name)
        saved_root, saved_dir = session.ROOT, session.SESSION_STATE_DIR
        saved_swr = session._shared_work_root
        try:
            session.ROOT = fake
            session.SESSION_STATE_DIR = fake / ".session-state"
            session._shared_work_root = lambda: fake
            self.assertEqual(before, session._real_live_stores())
        finally:
            session.ROOT, session.SESSION_STATE_DIR = saved_root, saved_dir
            session._shared_work_root = saved_swr


class WriteFloorTest(unittest.TestCase):
    """`atomic_write` refuses a real-store target while the suite is running."""

    def _sentinel(self):
        """A path in the REAL store that must never come to exist.

        UNIQUE PER CALL, AND CLEANED UP, and that is not fussiness — the first version
        used one fixed name and went red in the serial suite over a file it had not
        written. The MUTATION CHECK for this very guard had left it there: disabling the
        floor and re-running is how you prove a guard works, and with the floor disabled
        the write lands in real state for real. A test that asserts a real path is absent
        inherits every earlier run's litter, so it owns the name and removes it."""
        import uuid
        p = session._LAUNCH_SESSION_STATE_DIR / f"floor-probe-{uuid.uuid4().hex}.json"
        self.addCleanup(p.unlink, missing_ok=True)
        return p

    def test_a_write_into_the_real_launch_store_is_refused(self):
        target = self._sentinel()
        self.assertFalse(target.exists(), "precondition: the sentinel starts absent")
        with self.assertRaises(RuntimeError) as caught:
            session.atomic_write(target, "{}\n")
        self.assertIn("REAL session's coordination state", str(caught.exception))
        self.assertFalse(target.exists(), "the refusal must happen BEFORE the temp write")
        self.assertFalse(target.with_name(target.name + ".tmp").exists(),
                         "no temp file either — the refusal precedes both writes")

    def test_the_refusal_names_the_way_out(self):
        """A denial nobody can act on is the silent skip one layer up."""
        with self.assertRaises(RuntimeError) as caught:
            session.atomic_write(self._sentinel(), "{}\n")
        self.assertIn("neutralize_live_store", str(caught.exception))

    def test_a_write_into_a_fixture_tmpdir_is_allowed(self):
        """THE OTHER HALF. A guard that fires on correct code gets weakened, and the
        overwhelming majority of `.session-state/` writes in a suite run are a fixture's
        own and entirely right."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = pathlib.Path(tmp.name) / ".session-state"
        d.mkdir(parents=True)
        p = d / "beat.json"
        session.atomic_write(p, "{}\n")
        self.assertEqual(p.read_text(encoding="utf-8"), "{}\n")


class BannerFollowsTheAnchorTest(unittest.TestCase):
    """The banner path is joined to `SESSION_STATE_DIR` at CALL time.

    FAILS AGAINST THE OLD CODE, which is the point of it: with `SESSION_BANNER_FILE` bound
    at import, `_banner_file(None)` returned the real store's path however the directory
    had been redirected — the exact 35-writes-a-run escape."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.saved = session.SESSION_STATE_DIR
        self.addCleanup(setattr, session, "SESSION_STATE_DIR", self.saved)
        session.SESSION_STATE_DIR = pathlib.Path(self.tmp.name) / ".session-state"

    def test_the_unkeyed_banner_follows_a_redirected_anchor(self):
        got = session._banner_file(None)
        self.assertEqual(got.parent, session.SESSION_STATE_DIR)
        self.assertNotEqual(got.parent.resolve(),
                            session._LAUNCH_SESSION_STATE_DIR.resolve())

    def test_the_keyed_banner_follows_it_too(self):
        got = session._banner_file("abc-123")
        self.assertEqual(got.parent, session.SESSION_STATE_DIR)

    def test_the_derived_path_constants_are_gone(self):
        """They were the whole mechanism: a THIRD such file would have had to be
        remembered in every fixture. Names joined at call time cannot drift."""
        for dead in ("SESSION_BANNER_FILE", "GUARD_FIRINGS_FILE"):
            self.assertFalse(hasattr(session, dead),
                             f"{dead} is back — a path bound at import does not follow a "
                             f"fixture that redirects the directory (WI-0286)")
        self.assertEqual(session.BANNER_NAME, "announce.txt")
        self.assertEqual(session.GUARD_FIRINGS_NAME, "guard-firings.jsonl")


if __name__ == "__main__":
    unittest.main()
