#!/usr/bin/env python3
"""Refuse to write a launchd job into THIS machine's real scheduler from a test run.

WHY THIS EXISTS, with the failure rather than the principle. A test run that forgets the
redirect writes a real LaunchAgent to `~/Library/LaunchAgents/com.federation.mail-poller.plist`
whose `ProgramArguments`, `WorkingDirectory`, `StandardOutPath` and `StandardErrorPath` all
point inside its own `tempfile` directory. The fixture is cleaned up; the plist is not.
It replaces the live mail poller with one that cannot start, and it says nothing --
`launchctl list | grep federation` returns empty, so the job is not merely broken but
absent, and the only symptom is a deploy receipt that never arrives. The same fixture
can leak a state directory into `~/.local/state/poga/` from the other end.

WHY A REDIRECT IS NOT ENOUGH, which is the whole argument for a refusal rather than another
environment variable. `deploy/runner.py::launch_agents_dir` is already redirectable through
`POGA_DEPLOY_LAUNCH_AGENTS`, and its docstring already gives this exact reason: *"a suite
that writes the real one would install jobs on the machine running the tests."* That
reasoning is correct and it does not hold, because **a redirect protects the tests that
remember it**. A fixture that does not remember is refused by nothing, and the suite
stays green. A redirect is a convention; this is the check that makes forgetting
one loud. Both stay -- the redirect is how a test says where it wants to write, and this is
what happens when it forgets to say.

THE DISCRIMINATOR IS THE ACCOUNT'S REAL HOME, NEVER `$HOME`, and getting that backwards
would make this guard worse than useless. A well-behaved fixture redirects `HOME`, so
`Path.home()` inside it is the fixture's own directory and a write there is exactly right.
A guard asking about `Path.home()` would therefore refuse precisely the tests that did the
right thing, while passing the one that did not -- and a guard that fires on correct code is
a guard somebody deletes. The password database answers the question `$HOME` can be made to
lie about: the account's real home, whatever the environment says.

WHAT IT DOES NOT CLAIM. This is not a sandbox and it is not a permission model. It answers
one question -- "is a test process about to write into the real scheduler?" -- and it is
silent for every real invocation, because a real install writing to `~/Library/LaunchAgents`
is the correct and intended behaviour of `install-mail-worker.py --apply`.
"""
from __future__ import annotations

import os
import pwd
import sys
from pathlib import Path

#: The machine-wide scheduler directories. Absolute, so no redirect reaches them and no
#: fixture has a legitimate reason to write one.
SYSTEM_SCHEDULER_ROOTS = (Path("/Library/LaunchAgents"), Path("/Library/LaunchDaemons"))


class SchedulerWriteRefused(RuntimeError):
    """A test process tried to write into this machine's live scheduler."""


def real_home() -> Path:
    """The account's home from the password database, ignoring `$HOME` entirely.

    `Path.home()` reads `$HOME` first, which is the variable a fixture redirects. Asking it
    would invert this guard: correct tests refused, the incorrect one waved through."""
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def live_scheduler_roots() -> tuple:
    """Every directory on this machine where a written plist is a real installed job."""
    return (*SYSTEM_SCHEDULER_ROOTS, real_home() / "Library" / "LaunchAgents")


def under_test() -> bool:
    """True when this process is a test run.

    `unittest`/`pytest` in `sys.modules` is the one signal that cannot be forgotten by the
    fixture being guarded against: importing the runner is what makes a test run a test run.
    An environment marker would have to be set by the same author who forgot the redirect,
    which is the failure this exists to catch, so it would inherit the defect it is meant to
    close. Neither module is imported by this harness's production paths -- asserted by
    `tests/test_scheduler_guard.py`, not assumed here."""
    return "unittest" in sys.modules or "pytest" in sys.modules


def _within(target: Path, root: Path) -> bool:
    """Containment on normalised absolute paths, without touching the filesystem.

    `resolve()` is deliberately avoided: the target usually does not exist yet, and on macOS
    resolving drags `/var` to `/private/var`, so one side would normalise and the other
    would not."""
    target = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(target)))))
    root = Path(os.path.normpath(os.path.abspath(str(root))))
    return target == root or root in target.parents


def refuse_live_scheduler_write(path) -> None:
    """Refuse when a TEST process is about to write `path` into the live scheduler.

    A no-op outside a test run, and a no-op for any path a fixture redirected out of the
    real home. Raises `SchedulerWriteRefused` otherwise, naming the file and the redirect
    the caller should have set -- a refusal that does not say how to comply is a refusal
    someone routes around."""
    if not under_test():
        return
    target = Path(path)
    for root in live_scheduler_roots():
        if _within(target, root):
            raise SchedulerWriteRefused(
                f"REFUSING to write {target} from a test run: {root} is this machine's "
                f"live scheduler, and a plist written there is an INSTALLED JOB, not a "
                f"fixture. On 2026-09-18 a test did exactly this and silently replaced the "
                f"operator's mail poller with one pointing into a temp directory that was "
                f"then cleaned up.\n"
                f"          Point the fixture at a directory of its own — redirect HOME, or "
                f"set POGA_DEPLOY_LAUNCH_AGENTS for the runner's path — and pass that "
                f"directory as the destination. Nothing was written.")
