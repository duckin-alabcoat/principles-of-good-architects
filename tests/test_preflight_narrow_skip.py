"""WI-0463 — `POGA_SKIP_PREFLIGHT_CHECKS` skips onboarding and workspace-trust only.

THE DEFECT. On a fresh cloud VM the launch gate refused: onboarding read FAIL and
workspace trust UNKNOWN, while `claude auth status` was live and `claude -p` worked. The
only way through was `POGA_SKIP_PREFLIGHT=1`, which skips every launch check — terminfo,
residency and memory pressure included.

THE RULE. `POGA_SKIP_PREFLIGHT_CHECKS=onboarding,workspace-trust` (alias `trust`) drops
those two checks and nothing else, and only when a live login stands in for them: auth
must answer OK, or the skip is refused and every check runs. Every outcome is announced.
"""

import contextlib
import io
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

ENV = "POGA_SKIP_PREFLIGHT_CHECKS"


def _check(name, verdict, calls):
    def fn():
        calls.append(name)
        return session._PreflightResult(name, verdict, f"{name} detail", f"{name} remedy")
    fn.__name__ = f"_fake_{name}"
    return fn


class NarrowSkipTest(unittest.TestCase):

    def gate(self, env_value, auth=session.PREFLIGHT_OK, onboarding=session.PREFLIGHT_FAIL,
             trust=session.PREFLIGHT_UNKNOWN, terminfo=session.PREFLIGHT_OK,
             memory=session.PREFLIGHT_OK):
        """Run the launch gate with fake checks. Returns (exit code, checks run, stderr)."""
        calls = []
        term = _check("terminfo", terminfo, calls)
        tr = _check("workspace-trust", trust, calls)
        onb = _check("onboarding", onboarding, calls)
        mem = _check("memory-pressure", memory, calls)
        au = _check("auth", auth, calls)
        env = {k: v for k, v in os.environ.items()
               if k not in (ENV, session.PREFLIGHT_BYPASS_ENV)}
        if env_value is not None:
            env[ENV] = env_value
        env[session.PREFLIGHT_NO_REPAIR_ENV] = "1"
        err = io.StringIO()
        code = 0
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(session, "PREFLIGHT_GATE_CHECKS", (term, tr, onb, mem)), \
             mock.patch.object(session, "_pf_onboarding", onb), \
             mock.patch.object(session, "_pf_workspace_trust", tr), \
             mock.patch.object(session, "_pf_auth", au), \
             mock.patch.object(session, "detect_machine", return_value="TestBox"), \
             contextlib.redirect_stderr(err):
            try:
                session._preflight_gate()
            except SystemExit as e:
                code = int(e.code)
        return code, calls, err.getvalue()

    def test_control_without_the_variable_onboarding_fail_refuses(self):
        code, calls, _ = self.gate(None)
        self.assertEqual(code, 1)
        self.assertNotIn("auth", calls, "auth must not run on the default launch path")

    def test_the_skip_launches_when_auth_is_live(self):
        code, calls, err = self.gate("onboarding,workspace-trust")
        self.assertEqual(code, 0)
        self.assertNotIn("onboarding", calls)
        self.assertNotIn("workspace-trust", calls)
        self.assertIn("terminfo", calls)
        self.assertIn("memory-pressure", calls)
        self.assertIn("NOT checked", err)
        self.assertIn(ENV, err)

    def test_trust_alias_and_spacing(self):
        code, calls, _ = self.gate(" Onboarding , trust ")
        self.assertEqual(code, 0)
        self.assertNotIn("workspace-trust", calls)

    def test_the_skip_is_refused_when_auth_is_not_ok(self):
        for verdict in (session.PREFLIGHT_FAIL, session.PREFLIGHT_UNKNOWN):
            with self.subTest(auth=verdict):
                code, calls, err = self.gate("onboarding,workspace-trust", auth=verdict)
                self.assertEqual(code, 1)
                self.assertIn("onboarding", calls)
                self.assertIn("refused", err)

    def test_other_checks_cannot_be_skipped(self):
        code, calls, err = self.gate("terminfo,memory-pressure",
                                     terminfo=session.PREFLIGHT_FAIL,
                                     onboarding=session.PREFLIGHT_OK)
        self.assertEqual(code, 1)
        self.assertIn("terminfo", calls)
        self.assertIn("memory-pressure", calls)
        self.assertNotIn("auth", calls, "no skippable name, so no auth call is owed")
        self.assertIn("still checking: terminfo, memory-pressure", err)

    def test_a_skipped_onboarding_does_not_hide_a_failing_other_check(self):
        code, calls, _ = self.gate("onboarding", memory=session.PREFLIGHT_FAIL)
        self.assertEqual(code, 1)
        self.assertIn("workspace-trust", calls, "only the named check is skipped")

    def test_the_refusal_names_the_narrow_skip(self):
        _, _, err = self.gate(None)
        self.assertIn(ENV, err)


class TheRealChecksAreTheOnesSkippedTest(unittest.TestCase):
    """Against the unpatched gate subset: the names map to real gate members."""

    def test_both_skippable_checks_are_in_the_launch_gate(self):
        live = mock.MagicMock(return_value=session._PreflightResult(
            "auth", session.PREFLIGHT_OK, "live"))
        with mock.patch.dict(os.environ, {ENV: "onboarding,workspace-trust"}), \
             mock.patch.object(session, "_pf_auth", live), \
             contextlib.redirect_stderr(io.StringIO()):
            skipped = session._preflight_narrow_skips()
        self.assertEqual(len(skipped), 2)
        for fn in skipped:
            self.assertIn(fn, session.PREFLIGHT_GATE_CHECKS)
        self.assertEqual(tuple(session.PREFLIGHT_NARROW_SKIPPABLE),
                         ("onboarding", "workspace-trust"))

    def test_unset_costs_nothing(self):
        auth = mock.MagicMock()
        env = {k: v for k, v in os.environ.items() if k != ENV}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(session, "_pf_auth", auth):
            self.assertEqual(session._preflight_narrow_skips(), ())
        auth.assert_not_called()


if __name__ == "__main__":
    unittest.main()
