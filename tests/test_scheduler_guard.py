"""The suite may not install a launchd job on the machine running it.

The defect this pins is not hypothetical and is not reconstructed from a report: on
2026-09-18 at 10:00 a test run left a real `com.federation.mail-poller.plist` in the
operator's `~/Library/LaunchAgents`, pointing at a `tempfile` directory that no longer
exists, with the live job booted out and never replaced. It was found eighteen hours later
by a member Architect chasing a deploy receipt that could not arrive.

So the first test here drives the guard against THAT shape -- a plist, at that exact path,
with that exact label -- rather than against an invented one. A detector that has only ever
seen hand-written inputs is a detector that has not met the bug.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))
import schedulerguard

spec = importlib.util.spec_from_file_location(
    "mail_worker_installer", ROOT / "deploy/install-mail-worker.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

LABEL = "com.federation.mail-poller"


#: A name no launchd job has. THE DIRECTORY IS THE DISCRIMINATOR, NOT THE FILENAME -- the
#: guard keys on containment in the live scheduler directory and never looks at the label --
#: so aiming these tests at the REAL `com.federation.mail-poller.plist` would add no
#: coverage whatsoever while making this file's own failure mode "clobber the operator's
#: mail poller", which is the exact defect it exists to prevent. The real directory is
#: therefore kept and the real name is not.
PROBE_PLIST = "com.federation.invalid.test-scheduler-guard-probe.plist"


class TheRealDefectIsRefused(unittest.TestCase):
    """The 2026-09-18 write, reproduced through the code path that performed it."""

    def _live_dir(self) -> Path:
        return schedulerguard.real_home() / "Library" / "LaunchAgents"

    def test_a_write_into_the_live_scheduler_directory_is_refused(self):
        target = self._live_dir() / PROBE_PLIST
        self.assertFalse(target.exists(), "precondition: the probe path is unused")
        with self.assertRaises(schedulerguard.SchedulerWriteRefused) as caught:
            installer._write(target, b"<plist/>")
        # The message has to name the file and the way out, or it is a refusal someone
        # routes around rather than complies with.
        self.assertIn(str(target), str(caught.exception))
        self.assertIn("POGA_DEPLOY_LAUNCH_AGENTS", str(caught.exception))
        # NOTHING WAS WRITTEN is the claim; assert it rather than trusting the raise. This
        # is also the assertion that goes red if the guard is ever removed, and it does so
        # having created one obviously-named stray file rather than having replaced a job.
        self.assertFalse(target.exists(), "the guard raised but the file was written anyway")

    def test_the_backup_path_is_refused_too(self):
        """`change()` writes `.pre-mail-worker` beside the destination before replacing it.

        That write lands in the same live directory and would clobber a real backup, so the
        choke point has to cover it. It does, because both go through `_write` -- this test
        is what stops a later edit moving one of them out from under the guard."""
        target = self._live_dir() / (PROBE_PLIST + ".pre-mail-worker")
        with self.assertRaises(schedulerguard.SchedulerWriteRefused):
            installer._write(target, b"<plist/>")
        self.assertFalse(target.exists())

    def test_the_live_poller_path_is_covered_without_being_written_to(self):
        """The real job's path IS inside the guarded set — asked of the predicate alone.

        `refuse_live_scheduler_write` is a pure containment check that touches no
        filesystem, so this can name the real file safely where `_write` could not."""
        real = self._live_dir() / f"{LABEL}.plist"
        with self.assertRaises(schedulerguard.SchedulerWriteRefused):
            schedulerguard.refuse_live_scheduler_write(real)


class TheGuardDoesNotFireOnCorrectFixtures(unittest.TestCase):
    """The negative control, and the reason the discriminator is not `$HOME`."""

    def test_a_redirected_home_is_not_refused(self):
        """A fixture that redirects HOME is doing the right thing and must pass.

        This is the case a `Path.home()`-based guard would have refused -- inverting the
        check, punishing the careful fixture and waving through the careless one."""
        with mock.patch.dict(os.environ, {"HOME": "/tmp/fixture-home"}):
            fixture = Path("/tmp/fixture-home/Library/LaunchAgents") / f"{LABEL}.plist"
            # No raise. Asserted by calling it directly rather than by assertLogs-style
            # indirection: the guard's whole contract here is that it returns.
            self.assertIsNone(schedulerguard.refuse_live_scheduler_write(fixture))

    def test_an_ordinary_temp_directory_is_not_refused(self):
        self.assertIsNone(schedulerguard.refuse_live_scheduler_write(
            Path("/private/var/folders/k2/whatever/T/tmpabc123/LaunchAgents/x.plist")))

    def test_redirecting_HOME_does_not_move_the_real_home(self):
        """The property the whole guard rests on, pinned on its own.

        If `real_home()` ever starts reading `$HOME`, both tests above keep passing and the
        guard silently stops working — so the invariant gets its own assertion rather than
        being implied by the others."""
        real = schedulerguard.real_home()
        with mock.patch.dict(os.environ, {"HOME": "/tmp/fixture-home"}):
            self.assertEqual(schedulerguard.real_home(), real)
            self.assertNotEqual(Path.home(), real,
                                "control: $HOME really was redirected for this check")


class TheSystemDirectoriesAreRefused(unittest.TestCase):

    def test_system_launch_agents_and_daemons_are_refused(self):
        for root in schedulerguard.SYSTEM_SCHEDULER_ROOTS:
            with self.subTest(root=root):
                with self.assertRaises(schedulerguard.SchedulerWriteRefused):
                    schedulerguard.refuse_live_scheduler_write(root / "x.plist")

    def test_a_path_merely_starting_with_the_root_name_is_not_refused(self):
        """`/Library/LaunchAgents-fixture` is not inside `/Library/LaunchAgents`.

        String-prefix containment would refuse it. The check walks parents instead, and
        this is the test that tells the two apart."""
        self.assertIsNone(schedulerguard.refuse_live_scheduler_write(
            Path("/Library/LaunchAgents-fixture/x.plist")))


class TheTestDetectorIsHonest(unittest.TestCase):

    def test_it_reports_a_test_run_from_inside_one(self):
        self.assertTrue(schedulerguard.under_test())

    def test_outside_a_test_run_the_real_home_is_writable(self):
        """The guard must be INERT for a real `--apply`, which legitimately installs there.

        A guard that also blocked the real install would make the product unusable and would
        be removed within the week."""
        with mock.patch.object(schedulerguard, "under_test", return_value=False):
            self.assertIsNone(schedulerguard.refuse_live_scheduler_write(
                schedulerguard.real_home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"))

    def test_the_production_paths_do_not_import_unittest(self):
        """`under_test()` reads `sys.modules`, so it is only honest if the production
        entrypoints do not drag a test runner in themselves. Measured in a SEPARATE
        interpreter — asking this one is meaningless, since the suite has already imported
        unittest by definition."""
        program = (
            "import sys;"
            "sys.path.insert(0, %r);"
            "sys.path.insert(0, %r);"
            "import schedulerguard, production, mailworker;"
            "print('unittest' in sys.modules or 'pytest' in sys.modules)"
            % (str(ROOT / "deploy"), str(ROOT / "curate"))
        )
        result = subprocess.run([sys.executable, "-c", program],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False",
                         "a production import pulls in a test runner, so under_test() "
                         "would refuse real installs")


if __name__ == "__main__":
    unittest.main()
