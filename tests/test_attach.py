"""ADR-0100 / WI-0160 / WI-0161 — `poga attach`: the operator's machine opens a window
per dispatched lane.

Why this half exists at all, restated because it is the thing most likely to be
"simplified" away by someone who has not hit it: a process on the WORK machine cannot open
a window on the OPERATOR's machine. Under mosh/ssh the launchd manager is `Background`,
Apple Events cannot leave that session, and no permission grant changes it. So spawning and
window-opening are two halves on two machines, and the half that opens windows must be
initiated locally.

The properties pinned here:

  1. THE TRANSPORT WRAPPER IS RIGHT. Local is a bare `tmux attach`; ssh needs `-t` or tmux
     refuses with "open terminal failed"; mosh takes the command after `--`.
  2. AN UNREADABLE RECORD IS NOT AN EMPTY ONE. "I could not reach the work machine" must
     exit non-zero and say so, never render as "no lanes to attach"
     (`declare-what-a-check-assumes`) — that collapse is how a fan-out reads as finished.
  3. NO DOUBLE-ATTACH, and the state is per-MACHINE — attaching the same dispatch from a
     second desk must open windows there too.
  4. IT OPENS VIEWS AND NOTHING ELSE. No spawn, no slot, no claim, no queue advance —
     every one of those rebuilds the coordinator ADR-0051 retired.
  5. A TERMINAL IT CANNOT DRIVE IS REFUSED BY NAME. A silent no-op here is a fan-out the
     operator believes is watched and is not.

The GUI window itself is NOT exercised here and cannot be: opening one needs an `Aqua`
login and every Architect session runs `Background`. The launch path up to and including
the spawned process is covered by a template that records its arguments.
"""

import argparse
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


class AttachBase(unittest.TestCase):
    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.main = self.tmp / "repo"
        self.main.mkdir()
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        (self.main / "poga").write_text("#!/bin/sh\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "testsession"
        # The dispatch handles are cleared by `neutralize_ambient_env` above — all of
        # them, not the two this list used to name. These two are the terminal the
        # runner is sitting in, which `_attach_resolve_terminal` reads and no shared
        # guard owns.
        for k in ("TERM_PROGRAM", "TMUX"):
            os.environ.pop(k, None)
        self.evidence = self.tmp / "opened.txt"
        rec = self.tmp / "record.sh"
        rec.write_text(f'#!/bin/sh\necho "$1|$2" >> {self.evidence}\n', encoding="utf-8")
        rec.chmod(0o755)
        self.template = f"{rec} {{name}} {{cmd}}"

        # WI-0186: attach now cross-checks the record against what tmux is actually
        # running. `self.live` is the work machine's answer — a list of session names, or
        # None for could-not-look. Default: every lane the test logs is alive, which is
        # what these cases were implicitly assuming before the check existed. A test about
        # dead lanes sets it explicitly.
        self.live = None
        self._live_stub = mock.patch.object(
            session, "_attach_live_sessions",
            side_effect=lambda host: (self.live, "") if self.live is not None
            else (self._all_logged(), ""))
        self._live_stub.start()
        self.addCleanup(self._live_stub.stop)

    def _all_logged(self):
        """Every session name named by every dispatch record this test has written."""
        d = session._dispatch_dir()
        if d is None:
            return []
        names = []
        for f in sorted(d.glob("D-*.json")):
            try:
                r = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            for e in (r.get("log") or []):
                if e.get("item"):
                    names.append(session._tmux_session_name(r["dispatch_id"],
                                                            str(e["item"])))
        return names

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, did="D-test01", queue=(), log=("WI-0001",), spawned=None, **extra):
        rec = {"dispatch_id": did, "snapshot_id": "S-test01",
               "created_at": session._coord_iso(session.time.time()),
               "created_by": "probe", "surface": "tmux", "cap": 5,
               "budget": 5, "spawned": len(log) if spawned is None else spawned,
               "queue": list(queue), "held": {}, "subagents": [],
               "log": [{"item": w, "at": "2026-08-22T23:00:00+00:00", "by": "probe"}
                       for w in log]}
        rec.update(extra)
        session._dispatch_write(rec)
        return rec

    def _run(self, did="D-test01", **kw):
        args = argparse.Namespace(dispatch_id=did, host="", transport="", terminal="",
                                  once=True, interval=1, max_polls=1, forget=False,
                                  dry_run=False, no_fetch=True, raise_mode="off",
                                  raise_after=0)
        args.terminal = kw.pop("terminal", self.template)
        for k, v in kw.items():
            setattr(args, k, v)
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_attach(args)
            except SystemExit as e:
                return buf.getvalue(), e.code
        return buf.getvalue(), 0

    def _lines(self):
        if not self.evidence.exists():
            return []
        return [l for l in self.evidence.read_text().splitlines() if l.strip()]


# WI-0187: every binary the TAB runs is resolved on the operator's machine, because the
# tab's shell is `--noprofile --norc` and a package manager's bin is not on its PATH. Tests stub `which`
# rather than reading this machine's, so they pin the contract and not the install.
_BINS = {"tmux": "/opt/tools/bin/tmux", "mosh": "/opt/tools/bin/mosh",
         "ssh": "/usr/bin/ssh", "ghostty": "/opt/bin/ghostty"}


def _which(name, path=None):
    # `path=` asks a SPECIFIC directory (the Ghostty app bundle, a package manager's bin) rather
    # than PATH. Nothing lives in those on the test machine by default; a test about the
    # bundle says so itself.
    return None if path is not None else _BINS.get(name)


class TransportWrapperTest(AttachBase):
    """Property 1."""

    def setUp(self):
        super().setUp()
        w = mock.patch.object(session.shutil, "which", side_effect=_which)
        w.start()
        self.addCleanup(w.stop)

    def test_local_is_a_bare_attach_with_no_wrapper(self):
        # No transport wrapper — but tmux is still absolute (WI-0187): the local tab's
        # shell has the same stripped PATH as a remote one.
        self.assertEqual(session._attach_command("poga-x", "", "mosh"),
                         "/opt/tools/bin/tmux attach -t poga-x")

    def test_mosh_passes_the_command_after_the_double_dash(self):
        # NEAR side absolute, FAR side bare: the remote login shell reads a profile, so
        # resolving the remote tmux against THIS machine's paths would be wrong.
        self.assertEqual(session._attach_command("poga-x", "devbox", "mosh"),
                         "/opt/tools/bin/mosh devbox -- tmux attach -t poga-x")

    def test_a_missing_transport_is_refused_by_name_before_any_tab_opens(self):
        # WI-0187: three tabs once opened and all three printed `exec: mosh: not found`.
        # One named refusal replaces N identical failures read one tab at a time.
        with mock.patch.object(session.shutil, "which",
                               side_effect=lambda n: None if n == "mosh" else _which(n)):
            why = session._attach_transport_check("devbox", "mosh")
        self.assertIn("mosh is not on this machine", why)
        self.assertIn("--transport ssh", why)

    def test_a_present_transport_passes_the_check(self):
        self.assertEqual(session._attach_transport_check("devbox", "ssh"), "")
        self.assertEqual(session._attach_transport_check("", "mosh"), "")

    def test_missing_tmux_is_refused_even_locally(self):
        with mock.patch.object(session.shutil, "which",
                               side_effect=lambda n: None if n == "tmux" else _which(n)):
            why = session._attach_transport_check("", "ssh")
        self.assertIn("tmux is not on this machine", why)

    def test_ssh_forces_a_pty_or_tmux_refuses(self):
        cmd = session._attach_command("poga-x", "devbox", "ssh")
        self.assertIn("ssh -t devbox", cmd)
        self.assertIn("tmux attach -t poga-x", cmd)

    def test_a_hostile_session_name_is_quoted_not_interpolated(self):
        cmd = session._attach_command("poga-;rm -rf /", "devbox", "ssh")
        self.assertNotIn("; rm", cmd)
        self.assertIn("'poga-;rm -rf /'", cmd)


class TerminalResolutionTest(AttachBase):
    """Property 5."""

    def test_an_explicit_choice_is_never_overridden_by_detection(self):
        self.assertEqual(session._attach_resolve_terminal("iTerm2"), "iTerm2")

    def test_ghostty_is_detected_from_the_terminal_it_is_running_in(self):
        os.environ["TERM_PROGRAM"] = "ghostty"
        self.assertEqual(session._attach_resolve_terminal(), "ghostty")

    def test_inside_tmux_with_no_gui_the_overflow_path_is_chosen(self):
        os.environ["TMUX"] = "/tmp/tmux-1000/default,1,0"
        with mock.patch.object(session, "shutil") as sh, \
             mock.patch.object(session, "_dispatch_apple_events_available",
                               return_value=False), \
             mock.patch.object(session, "_tmux_bin", return_value="/opt/tools/bin/tmux"):
            sh.which.return_value = None
            self.assertEqual(session._attach_resolve_terminal(), "tmux-window")

    def test_no_way_to_open_a_window_is_refused_by_name_not_silently_skipped(self):
        with mock.patch.object(session, "shutil") as sh, \
             mock.patch.object(session, "_dispatch_apple_events_available",
                               return_value=False), \
             mock.patch.object(session, "_tmux_bin", return_value=None), \
             mock.patch.object(session, "_dispatch_session_manager",
                               return_value="Background"):
            sh.which.return_value = None
            with self.assertRaises(ValueError) as cm:
                session._attach_resolve_terminal()
        msg = str(cm.exception)
        self.assertIn("Background", msg)
        self.assertIn("machine you are SITTING AT", msg)

    def test_a_template_without_a_cmd_placeholder_is_refused_rather_than_run(self):
        ok, detail = session._attach_open("tmux attach -t x", "notaterminal", "x")
        self.assertFalse(ok)
        self.assertIn("{cmd}", detail)
        self.assertIn("{argv}", detail)


class CommandConventionTest(AttachBase):
    """Property 7 — THE TERMINAL GETS THE COMMAND IN THE SHAPE IT ACTUALLY WANTS.

    Found live on an operator machine, session ~172, by running the built-in template by hand for
    the first time: `ghostty -e "sleep 60"` opens a window containing
    `login: sleep 60: No such file or directory`, because Ghostty hands `-e`'s single
    argument straight to `login` as one argv element. `ghostty -e sleep 60` is silent.

    The template had shipped with `{cmd}` and had never been run, so the first window of
    the first drill would have been an error message. That is the whole argument for
    `{argv}` existing, and for pinning both conventions here rather than trusting the one
    that happened to be written first."""

    def _line(self, cmd, terminal):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0)) as run, \
             mock.patch.object(session.shutil, "which", return_value="/opt/bin/ghostty"), \
             mock.patch.object(session, "_tmux_bin", return_value="/opt/bin/tmux"):
            session._attach_open(cmd, terminal, "poga-D-x-WI-1")
        return run.call_args[0][0]

    def test_ghostty_receives_the_command_as_separate_arguments(self):
        # `ghostty-window` is the CLI form — `ghostty` itself now goes through the
        # AppleScript dictionary to get TABS, which the CLI cannot do at all.
        line = self._line("mosh devbox -- tmux attach -t poga-x", "ghostty-window")
        self.assertIn("-e mosh devbox -- tmux attach -t poga-x", line)
        # The failure this pins: the whole line collapsed into ONE quoted argument.
        self.assertNotIn("-e 'mosh devbox -- tmux attach -t poga-x'", line)

    def test_tmux_window_still_receives_one_quoted_argument(self):
        # The opposite convention, and it must not be broken by fixing Ghostty:
        # `tmux new-window` takes a command STRING.
        line = self._line("mosh devbox -- tmux attach -t poga-x", "tmux-window")
        self.assertIn("'mosh devbox -- tmux attach -t poga-x'", line)

    def test_a_custom_template_may_use_either_convention(self):
        self.assertIn("-x mosh devbox", self._line("mosh devbox", "myterm -x {argv}"))
        self.assertIn("-x 'mosh devbox'", self._line("mosh devbox", "myterm -x {cmd}"))

    def test_the_ssh_remote_command_survives_argv_splitting_as_one_word(self):
        # `_attach_command`'s ssh form quotes the remote command; word-splitting must
        # leave it as a SINGLE argv element or ssh runs `tmux` with stray arguments.
        with mock.patch.object(session.shutil, "which", side_effect=_which):
            cmd = session._attach_command("poga-D-x-WI-1", "devbox", "ssh")
        line = self._line(cmd, "ghostty-window")
        self.assertIn("'tmux attach -t poga-D-x-WI-1'", line)
        # WI-0187: absolute on the near side. The remote command stays one argv element.
        self.assertIn("-e /usr/bin/ssh -t devbox", line)


class GhosttyBinaryIsResolvedNotGuessedTest(AttachBase):
    """Property 5, second half — THE BINARY THAT OPENS THE WINDOW IS RESOLVED, NOT GUESSED.

    WI-0167, found on one of the machines (session ~170) while preparing WI-0160's acceptance.
    `_attach_open` did `shutil.which("ghostty") or "ghostty"`. A default Ghostty install
    puts the binary at `/Applications/Ghostty.app/Contents/MacOS/ghostty` and nothing
    symlinks it onto PATH — the DEFAULT install, not an unusual one — so `which` answered None,
    the bare name was handed to `shell=True`, and the window died `command not found`
    (exit 127). Property 5 promises that a terminal this machine cannot drive is refused BY
    NAME; the one binary nobody bothered to resolve is what turned that promise into a
    shell error, in the subsystem built to keep it.

    The `or <bare name>` is the defect and not a detail: there is no configuration in which
    running a name that is not there helps. It is a fallback to a guaranteed failure, and
    the failure it produces names nothing the operator can act on.
    """

    @staticmethod
    def _no_ghostty(name, path=None):
        """This machine has every attach binary EXCEPT Ghostty — not on PATH, not in the
        bundle."""
        return None if name == "ghostty" else _which(name, path)

    def test_the_app_bundle_is_found_when_nothing_is_on_path(self):
        # The default install's configuration, which `which("ghostty")` alone reads as
        # "Ghostty is not installed" while the app sits plainly in /Applications.
        def which(name, path=None):
            if name != "ghostty":
                return _which(name, path)
            return session.GHOSTTY_APP_BIN if path == session.GHOSTTY_APP_DIR else None

        with mock.patch.object(session.shutil, "which", side_effect=which):
            self.assertEqual(session._ghostty_bin(), session.GHOSTTY_APP_BIN)

    def test_nothing_anywhere_resolves_to_none_rather_than_to_the_bare_name(self):
        with mock.patch.object(session.shutil, "which", side_effect=self._no_ghostty):
            self.assertIsNone(session._ghostty_bin())

    def test_a_missing_ghostty_refuses_by_name_and_shells_nothing(self):
        with mock.patch.object(session.shutil, "which", side_effect=self._no_ghostty), \
             mock.patch.object(session.subprocess, "run") as run:
            ok, detail = session._attach_open("tmux attach -t poga-x", "ghostty-window",
                                              "poga-x")
        self.assertFalse(ok)
        self.assertIn("not on PATH", detail)
        # Actionable, not merely accurate: "not on PATH" is only useful next to where the
        # binary actually is.
        self.assertIn(session.GHOSTTY_APP_BIN, detail)
        # The whole point. Exit 127 was never reachable because nothing ran.
        run.assert_not_called()

    def test_a_missing_ghostty_does_not_refuse_a_template_that_never_names_it(self):
        # Per PLACEHOLDER, not per machine. Scoping the refusal to "this machine has no
        # Ghostty" would trade one wrong answer for another: `tmux-window` interpolates no
        # ghostty and must still open.
        with mock.patch.object(session.shutil, "which", side_effect=self._no_ghostty), \
             mock.patch.object(session, "_tmux_bin", return_value="/opt/bin/tmux"), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0)) as run:
            ok, detail = session._attach_open("tmux attach -t poga-x", "tmux-window",
                                              "poga-x")
        self.assertTrue(ok, detail)
        self.assertIn("/opt/bin/tmux new-window", run.call_args[0][0])

    def test_the_applescript_openers_need_no_binary_at_all(self):
        # `ghostty` (tabs, through the dictionary) and `ghostty-window` (the CLI) are two
        # different paths. Only the second interpolates a binary, so a machine with none
        # must still pass the check for the first.
        with mock.patch.object(session.shutil, "which", side_effect=self._no_ghostty):
            self.assertEqual(session._attach_terminal_check("ghostty"), "")
            self.assertEqual(session._attach_terminal_check("Terminal"), "")
            self.assertIn("not on PATH",
                          session._attach_terminal_check("ghostty-window"))

    def test_attach_refuses_once_up_front_rather_than_once_per_lane(self):
        # `_attach_transport_check`'s argument, applied to the other half of the launch:
        # one named refusal beats N identical failures read one window at a time.
        self._write(log=["WI-0001", "WI-0002"])
        with mock.patch.object(session.shutil, "which", side_effect=self._no_ghostty):
            out, code = self._run(terminal="ghostty-window")
        self.assertEqual(code, 2)
        self.assertIn(session.GHOSTTY_APP_BIN, out)
        self.assertEqual(out.count("Nothing opened"), 1)
        self.assertNotIn("WINDOW FAILED", out)


class DrillTouchesNothingRealTest(AttachBase):
    """Property 9 — A REHEARSAL COSTS NOTHING.

    operator ruled in session ~172, after the first live drill claimed two backlog items and
    spawned two real Claude sessions: a test run must not use real work items, record
    permanent data, consume real session numbers, or touch any journal or history.

    WI-0160's acceptance criteria had said "dispatch two items", so the drill as specified
    could only be run by putting live agents on the real board — a test more dangerous than
    the thing it tested. A drill lane runs a trivial script instead of the agent, because
    everything the ATTACH path exercises (session name, record, discovery, terminal,
    transport, detach) is upstream of what runs inside the lane."""

    def _run_drill(self, lanes=2, clean=False):
        opened = []

        def fake_spawn(cmd, name):
            opened.append((cmd, name))
            return True, ""

        args = argparse.Namespace(lanes=lanes, clean=clean)
        buf = io.StringIO()
        # `_tmux_bin` -> None so the clean path skips its kill loop without a blanket
        # `subprocess.run` mock, which would also answer cmd_drill's `git` call for the
        # coord store and leave the record silently unwritten.
        with mock.patch.object(session, "_dispatch_open_tmux", side_effect=fake_spawn), \
             mock.patch.object(session, "_tmux_bin", return_value=None), \
             redirect_stdout(buf):
            session.cmd_drill(args)
        return buf.getvalue(), opened

    def test_the_record_is_marked_as_a_drill(self):
        self._run_drill()
        recs = session._drill_records()
        self.assertEqual(len(recs), 1)
        self.assertTrue(recs[0]["drill"])

    def test_the_items_are_synthetic_and_never_work_item_ids(self):
        _, opened = self._run_drill(lanes=3)
        self.assertEqual(len(opened), 3)
        items = [e["item"] for e in session._drill_records()[0]["log"]]
        self.assertEqual(items, ["DRILL-1", "DRILL-2", "DRILL-3"])
        for i in items:
            self.assertFalse(i.startswith("WI-"))

    def test_no_claim_is_taken(self):
        self._run_drill()
        self.assertEqual(session._coord_list("claims"), {})

    def test_no_journal_is_written(self):
        before = set((self.main / "sessions" / "journal").glob("*")) \
            if (self.main / "sessions" / "journal").is_dir() else set()
        self._run_drill()
        after = set((self.main / "sessions" / "journal").glob("*")) \
            if (self.main / "sessions" / "journal").is_dir() else set()
        self.assertEqual(before, after)

    def test_no_branch_or_worktree_is_created(self):
        self._run_drill()
        r = subprocess.run(["git", "branch", "--list"], cwd=self.main,
                           capture_output=True, text=True)
        self.assertNotIn("worktree-", r.stdout)

    def test_the_lane_command_runs_the_drill_script_not_the_agent(self):
        _, opened = self._run_drill(lanes=1)
        cmd, name = opened[0]
        parts = cmd.split()
        # The LAUNCHED command is the drill script. `poga` now rides along as one of its
        # arguments, because the lane claims and releases its synthetic item for real
        # (session ~173) — but it is never what the lane is launched AS, which is the
        # property that keeps an agent out of a drill.
        self.assertIn("drill-lane.sh", parts[0])
        self.assertNotIn("claude", cmd.lower())
        self.assertNotIn("POGA_DISPATCH", cmd)
        self.assertRegex(name, r"^poga-D-[0-9a-f]+-DRILL-1$")

    def test_the_lane_is_handed_a_poga_to_claim_with(self):
        _, opened = self._run_drill(lanes=1)
        cmd, _ = opened[0]
        parts = cmd.split()
        self.assertEqual(parts[1], "DRILL-1")
        self.assertTrue(parts[2].endswith("/poga"), parts[2])

    def test_the_session_name_is_shaped_so_discovery_finds_it(self):
        # The drill is worthless if `attach` cannot see it the same way it sees a real one.
        _, opened = self._run_drill(lanes=1)
        did = session._drill_records()[0]["dispatch_id"]
        self._live_stub.stop()                      # the real read is the subject here
        self.addCleanup(lambda: None)
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out=opened[0][1])), \
             mock.patch.object(session, "_tmux_bin", return_value="tmux"):
            ids, why = session._attach_live_dispatch_ids("")
        self.assertEqual(ids, [did])

    def test_clean_removes_the_record_entirely(self):
        self._run_drill()
        self.assertEqual(len(session._drill_records()), 1)
        out, _ = self._run_drill(clean=True)
        self.assertEqual(session._drill_records(), [])
        self.assertIn("cleaned", out)

    def test_clean_with_nothing_to_clean_says_so_rather_than_pretending(self):
        out, _ = self._run_drill(clean=True)
        self.assertIn("nothing to clean", out)

    def test_clean_leaves_a_real_dispatch_alone(self):
        # A drill cleanup that reaped real dispatches would be catastrophic in exactly the
        # situation it is used: a drill run alongside live fan-outs.
        self._write(did="D-real01", queue=(), log=("WI-0001",))
        self._run_drill()
        self._run_drill(clean=True)
        self.assertIsNotNone(session._dispatch_read("D-real01"))


def _has_osacompile():
    return bool(shutil.which("osacompile")) and pathlib.Path(
        "/Applications/Ghostty.app/Contents/Resources/Ghostty.sdef").exists()


class GhosttyTabsTest(AttachBase):
    """Property 8 — ONE WINDOW, ONE TAB PER LANE.

    The ask, session ~172: one window, one tab per lane. Ghostty's CLI
    cannot do it — `+new-window` is a GTK action that refuses on macOS, and native macOS
    window tabbing does not apply either (setting "Prefer tabs when opening documents" to
    Always still produced a window, verified by hand). Its AppleScript dictionary
    can, and it is a real API rather than synthesized keystrokes.

    The window handle is the load-bearing part: `attach` is a WATCHER, so tab four is
    opened twenty minutes after the window, from a later `osascript` process. Ghostty's
    `window` class carries a stable `id`, which is what makes that possible at all."""

    def _script(self, cmd="mosh devbox -- tmux attach -t poga-x", window=None):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out="W-99")) as run:
            ok, detail = session._attach_open(cmd, "ghostty", "poga-x", window)
        self.assertTrue(ok, detail)
        # Select the osascript call rather than taking the LAST one. `_attach_open` also
        # resolves the shared work root (twice — once to read the window handle, once to
        # write it), and `call_args` is the last call, not the interesting one. That the
        # osascript happened to be last was never guaranteed: it depended on
        # `_shared_work_root`'s cache being warm, which in this fixture it is not, because
        # the blanket `subprocess.run` stub answers `--git-common-dir` with "W-99" and a
        # non-absolute answer is no answer (WI-0267). Asserting on the call it means makes
        # the test independent of how many times the substrate resolves a path.
        osa = [c for c in run.call_args_list if c[0][0] and c[0][0][0] == "osascript"]
        self.assertEqual(len(osa), 1, f"expected one osascript call, got {len(osa)}")
        argv = osa[0][0][0]
        return argv[2]

    def test_the_first_lane_opens_a_window(self):
        s = self._script(window={})
        self.assertIn("new window with configuration cfg", s)

    def test_a_later_lane_opens_a_tab_in_the_window_already_made(self):
        holder = {}
        self._script(window=holder)
        self.assertEqual(holder["id"], "W-99")     # captured from the first call
        s = self._script(window=holder)
        self.assertIn("new tab in target with configuration cfg", s)
        self.assertIn('is "W-99"', s)

    def test_the_command_rides_on_the_surface_configuration(self):
        s = self._script(cmd="mosh devbox -- tmux attach -t poga-D-x-WI-1", window={})
        self.assertIn('command:"mosh devbox -- tmux attach -t poga-D-x-WI-1"', s)

    def test_a_dead_transport_leaves_its_tab_up_carrying_the_error(self):
        # Without `wait after command` the tab closes on exit, so a lane that failed to
        # attach is a window that flickered — the silent strand this command exists for.
        self.assertIn("wait after command:true", self._script(window={}))

    def test_the_window_id_survives_this_process_so_a_rerun_rejoins_it(self):
        self._script(window={})
        self.assertEqual(session._attach_window_id(), "W-99")

    def test_a_closed_window_is_remade_rather_than_failing(self):
        # The script decides in ONE round trip: if no window carries the id, make one.
        # Looking first and acting second would race the operator's own hands.
        s = session._attach_ghostty_script("cmd", "W-GONE")
        self.assertIn("if target is missing value then", s)
        self.assertIn("new window with configuration cfg", s)

    def test_a_failed_open_is_named_not_swallowed(self):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=1, err="Ghostty got an error")):
            ok, detail = session._attach_open("cmd", "ghostty", "poga-x", {})
        self.assertFalse(ok)
        self.assertIn("Ghostty got an error", detail)

    def test_a_blocked_apple_event_names_the_permission_rather_than_hanging(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("osascript", 20)):
            ok, detail = session._attach_open("cmd", "ghostty", "poga-x", {})
        self.assertFalse(ok)
        self.assertIn("osascript blocked", detail)

    @unittest.skipIf(session._under_gate(),
                     "needs the macOS GUI service; unavailable in sandbox gates")
    @unittest.skipUnless(_has_osacompile(), "needs osacompile + Ghostty installed")
    def test_the_script_compiles_against_ghosttys_real_dictionary(self):
        """The only verification available from an Architect session, and it is worth
        having: `osacompile` RESOLVES the application's terminology, so this fails if a
        property or command name drifts. It does NOT prove a tab opens — that needs an Aqua
        login and every Architect session runs Background (ADR-0077). Controls were run by
        hand at authoring time: `new tabzzz` and `wait after commandzzz` both fail to
        compile, so a green here is not vacuous."""
        for wid in ("", "W-12345", 'x"; osascript -e "beep'):
            script = session._attach_ghostty_script("mosh h -- tmux attach -t poga-x", wid)
            r = subprocess.run(["osacompile", "-o", os.devnull, "-e", script],
                               text=True, capture_output=True)
            self.assertEqual(r.returncode, 0, f"wid={wid!r}: {r.stderr}")


class OpensAWindowPerLaneTest(AttachBase):
    """Properties 3 and 4."""

    def test_one_window_per_spawned_lane_with_the_right_attach_command(self):
        self._write(log=("WI-0001", "WI-0002"))
        out, code = self._run()
        self.assertEqual(code, 0)
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        want = session._attach_command("poga-D-test01-WI-0001", "", "mosh")
        self.assertIn(f"poga-D-test01-WI-0001|{want}", lines[0])
        self.assertIn("window open", out)

    def test_a_second_run_on_the_same_machine_opens_nothing_new(self):
        self._write(log=("WI-0001",))
        self._run()
        out, _ = self._run()
        self.assertEqual(len(self._lines()), 1)
        self.assertIn("0 opened by this run", out)

    def test_forget_reopens_because_a_second_desk_needs_its_own_windows(self):
        self._write(log=("WI-0001",))
        self._run()
        self._run(forget=True)
        self.assertEqual(len(self._lines()), 2)

    def test_a_failed_window_is_named_rather_than_swallowed(self):
        self._write(log=("WI-0001",))
        with mock.patch.object(session, "_attach_open",
                               return_value=(False, "ghostty not found")):
            out, _ = self._run()
        self.assertIn("WINDOW FAILED", out)
        self.assertIn("ghostty not found", out)

    def test_attach_never_spawns_a_lane(self):
        """It is a viewer. The moment it spawns, it is the master session again."""
        self._write(queue=("WI-0009",), log=("WI-0001",))
        with mock.patch.object(session, "_dispatch_try_spawn") as spawn:
            self._run()
        spawn.assert_not_called()
        self.assertEqual(session._dispatch_read("D-test01")["queue"], ["WI-0009"])

    def test_dry_run_opens_nothing_but_shows_the_command(self):
        self._write(log=("WI-0001",))
        out, _ = self._run(dry_run=True)
        self.assertEqual(self._lines(), [])
        self.assertIn("would open", out)
        self.assertIn("tmux attach -t poga-D-test01-WI-0001", out)


class UnreadableIsNotEmptyTest(AttachBase):
    """Property 2 — the collapse that keeps recurring."""

    def test_a_missing_record_exits_2_and_says_it_could_not_read(self):
        out, code = self._run(did="D-nope99")
        self.assertEqual(code, 2)
        self.assertIn("CANNOT READ", out)

    def test_an_unreachable_host_is_reported_with_its_real_reason(self):
        self._write(log=("WI-0001",))
        fake = subprocess.CompletedProcess([], 255, "", "Host key verification failed.")
        # The transport is not under test. Without this stub a machine with no mosh (a
        # CI runner) refuses on the missing transport first and never tries the read.
        with mock.patch.object(session, "_attach_transport_check", return_value=""), \
                mock.patch.object(session.subprocess, "run", return_value=fake):
            out, code = self._run(host="runner")
        self.assertEqual(code, 2)
        self.assertIn("CANNOT READ", out)
        self.assertIn("Host key verification failed", out)
        self.assertEqual(self._lines(), [])


class StopsWatchingWhenThereIsNothingToWaitForTest(AttachBase):
    def test_a_stalled_dispatch_stops_the_watcher_and_says_why(self):
        self._write(queue=("WI-0009",), log=(), spawned=0,
                    stall={"kind": "cap", "detail": "cap reached (2/2 lanes live)",
                           "at": "2026-08-22T16:06:23+00:00"})
        out, _ = self._run(once=False, max_polls=0)
        self.assertIn("STALLED", out)
        self.assertIn("cap reached", out)

    def test_a_cancelled_dispatch_stops_the_watcher(self):
        self._write(queue=(), log=("WI-0001",),
                    cancelled={"at": "2026-08-22T23:00:00+00:00", "by": "t",
                               "dropped": [], "reason": ""})
        out, _ = self._run(once=False, max_polls=0)
        self.assertIn("cancelled", out)

    def test_a_complete_dispatch_reports_the_total_it_attached(self):
        self._write(queue=(), log=("WI-0001", "WI-0002"))
        out, _ = self._run(once=False, max_polls=0)
        self.assertIn("dispatch complete", out)
        self.assertIn("2 lane(s) attached", out)


def _proc(rc=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=out, stderr=err)


class DiscoversTheDispatchTest(AttachBase):
    """Property 6 — THE OPERATOR DOES NOT CARRY THE ID.

    The `D-xxxxxx` is minted on the machine that dispatched, in a session the operator was
    never watching. Requiring them to read it off one machine and type it into another
    makes the human the transport for a token — the ADR-0099 class arriving through the
    fix for it, which is why this is pinned rather than left to the CLI's default.

    The answer comes from tmux, not from the record store and not from "the newest": a
    dispatch whose lanes have all exited has nothing to attach to however recently it ran,
    and `_tmux_session_name` already stamps the dispatch id into the session name for
    exactly this "what is running and why" question."""

    def _found(self, ids, why=""):
        """Patch the DISCOVERY, not subprocess — `cmd_attach` shells out to git to find the
        record store, and a blanket `subprocess.run` mock silently answers that call too."""
        return mock.patch.object(session, "_attach_live_dispatch_ids",
                                 return_value=(ids, why))

    def test_one_running_dispatch_is_resolved_without_the_operator_naming_it(self):
        self._write(did="D-abc123", queue=(), log=("WI-0001",))
        with self._found(["D-abc123"]):
            out, code = self._run(did=None)
        self.assertIn("one dispatch running", out)
        self.assertIn("D-abc123", out)
        self.assertEqual(code, 0)
        self.assertEqual(len(self._lines()), 1)

    def test_several_running_dispatches_are_all_attached(self):
        # the operator's ruling, session ~172: several running dispatches are all attached. The first cut refused
        # and made the operator name one, borrowing `dispatch`'s never-guess rule — but
        # that rule is about SPAWNING. This opens views, so an unwanted window costs a
        # close, while refusing left the second fan-out running headless.
        self._write(did="D-aaa111", queue=(), log=("WI-0001",))
        self._write(did="D-bbb222", queue=(), log=("WI-0002",))
        with self._found(["D-aaa111", "D-bbb222"]):
            out, code = self._run(did=None)
        self.assertEqual(code, 0)
        self.assertIn("attaching all of them", out)
        self.assertEqual(len(self._lines()), 2)
        opened = "\n".join(self._lines())
        self.assertIn("poga-D-aaa111-WI-0001", opened)
        self.assertIn("poga-D-bbb222-WI-0002", opened)

    def test_several_dispatches_are_watched_together_not_drained_in_sequence(self):
        # Sequential draining would leave the second fan-out headless for as long as the
        # first took — the strand this command exists to prevent, moved rather than fixed.
        # One still-queued dispatch must not stop the other from getting its windows.
        self._write(did="D-aaa111", queue=("WI-0009",), log=("WI-0001",))
        self._write(did="D-bbb222", queue=(), log=("WI-0002",))
        with self._found(["D-aaa111", "D-bbb222"]):
            out, code = self._run(did=None, once=False, max_polls=1)
        opened = "\n".join(self._lines())
        self.assertIn("poga-D-aaa111-WI-0001", opened)
        self.assertIn("poga-D-bbb222-WI-0002", opened)
        self.assertIn("dispatch complete", out)

    def test_one_unreadable_record_does_not_strand_the_readable_one(self):
        self._write(did="D-bbb222", queue=(), log=("WI-0002",))
        with self._found(["D-aaa111", "D-bbb222"]):
            out, code = self._run(did=None)
        self.assertIn("CANNOT READ", out)
        self.assertIn("D-aaa111", out)
        self.assertEqual(len(self._lines()), 1)
        self.assertIn("poga-D-bbb222-WI-0002", self._lines()[0])
        self.assertEqual(code, 0)

    def test_every_record_unreadable_still_exits_non_zero(self):
        # Nothing was watched. A quiet success here is the fan-out-reads-as-finished
        # collapse this file's property 2 exists for.
        with self._found(["D-aaa111", "D-bbb222"]):
            out, code = self._run(did=None)
        self.assertEqual(code, 2)
        self.assertEqual(self._lines(), [])

    def test_two_ids_can_also_be_named_explicitly(self):
        self._write(did="D-aaa111", queue=(), log=("WI-0001",))
        self._write(did="D-bbb222", queue=(), log=("WI-0002",))
        out, code = self._run(did=["D-aaa111", "D-bbb222"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self._lines()), 2)

    def test_nothing_running_is_a_definite_answer_not_a_failure_to_look(self):
        with self._found([]):
            out, code = self._run(did=None)
        self.assertEqual(code, 2)
        self.assertIn("no dispatched lanes are running", out)
        self.assertNotIn("CANNOT TELL", out)

    def test_a_work_machine_it_could_not_ask_is_cannot_tell_not_nothing_running(self):
        # The property the whole file exists for, applied to discovery:
        # 'I could not look' must never render as 'there is nothing there'.
        # The transport is not under test; see the stub in UnreadableIsNotEmptyTest.
        with self._found(None, "ssh: Could not resolve hostname devbox"), \
                mock.patch.object(session, "_attach_transport_check", return_value=""):
            out, code = self._run(did=None, host="devbox")
        self.assertEqual(code, 2)
        self.assertIn("CANNOT TELL", out)
        self.assertIn("Could not resolve hostname", out)
        self.assertNotIn("no dispatched lanes are running", out)

    def test_an_explicit_id_is_never_overridden_by_discovery(self):
        self._write(did="D-test01", queue=(), log=("WI-0001",))
        with mock.patch.object(session, "_attach_live_dispatch_ids",
                               side_effect=AssertionError("discovery must not run")):
            out, code = self._run(did="D-test01")
        self.assertEqual(code, 0)
        self.assertEqual(len(self._lines()), 1)


class ReadsWhatIsRunningFromTmuxTest(AttachBase):
    """Property 6's other half — the tmux read itself.

    Separated from the selection tests above because this is the only place a blanket
    `subprocess.run` mock is safe: this function makes exactly one call and never touches
    git. Mocking it around `cmd_attach` instead answers that command's `git rev-parse` too,
    which is how the first cut of these tests 'failed' for a reason that was not the code."""

    def setUp(self):
        super().setUp()
        # This class's SUBJECT is the discovery call itself, so the base class's stub of
        # `_attach_live_sessions` (which exists so the open-loop tests have a work machine
        # to answer them) has to stand down here.
        self._live_stub.stop()
        self.addCleanup(lambda: None)

    def _ask(self, host="", rc=0, out="", err=""):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=rc, out=out, err=err)) as run, \
             mock.patch.object(session, "_tmux_bin", return_value="tmux"):
            return session._attach_live_dispatch_ids(host), run

    def test_the_id_is_read_out_of_the_session_name(self):
        # `_tmux_session_name` stamps it there precisely so `tmux ls` answers "what is
        # running and why" without the record store.
        (ids, why), _ = self._ask(out="poga-D-abc123-WI-0001")
        self.assertEqual(ids, ["D-abc123"])
        self.assertEqual(why, "")

    def test_many_lanes_of_one_dispatch_collapse_to_one_id(self):
        (ids, _), _ = self._ask(
            out="poga-D-abc123-WI-0001\npoga-D-abc123-WI-0002\npoga-D-abc123-WI-0003")
        self.assertEqual(ids, ["D-abc123"])

    def test_two_dispatches_are_both_reported_in_a_stable_order(self):
        (ids, _), _ = self._ask(out="poga-D-aaa111-WI-0001\npoga-D-bbb222-WI-0002")
        self.assertEqual(ids, ["D-aaa111", "D-bbb222"])

    def test_unrelated_tmux_sessions_are_not_mistaken_for_dispatches(self):
        # `poga-3` is a LANE worktree name, not a dispatch — the near-miss that matters.
        (ids, _), _ = self._ask(out="my-editor\n0\npoga-3\nlogs\npoga-D")
        self.assertEqual(ids, [])

    def test_no_tmux_server_at_all_is_empty_rather_than_unreachable(self):
        # tmux exits 1 on an idle machine. Reading that as "could not look" would make the
        # COMMON case render as a fault (`declare-what-a-check-assumes`).
        (ids, why), _ = self._ask(rc=1, err="no server running on /tmp/tmux-1000/default")
        self.assertEqual(ids, [])
        self.assertEqual(why, "")

    def test_an_unreachable_host_is_none_not_an_empty_list(self):
        (ids, why), _ = self._ask(host="devbox", rc=255,
                                  err="ssh: Could not resolve hostname devbox")
        self.assertIsNone(ids)
        self.assertIn("Could not resolve hostname", why)

    def test_a_timeout_is_also_cannot_tell(self):
        with mock.patch.object(session.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("ssh", 20)):
            ids, why = session._attach_live_dispatch_ids("devbox")
        self.assertIsNone(ids)
        self.assertIn("could not ask devbox", why)

    def test_the_remote_case_asks_the_work_machine_over_ssh(self):
        _, run = self._ask(host="devbox")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[1], "devbox")
        self.assertIn("tmux ls", argv[2])

    def test_the_local_case_runs_tmux_directly_with_no_transport(self):
        _, run = self._ask()
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "tmux")
        self.assertNotIn("ssh", argv)

    def test_no_tmux_on_this_machine_is_cannot_tell(self):
        with mock.patch.object(session, "_tmux_bin", return_value=None):
            ids, why = session._attach_live_dispatch_ids("")
        self.assertIsNone(ids)
        self.assertIn("no tmux", why)


class NeverOpensATabOnACorpseTest(AttachBase):
    """Property 10 (WI-0186) — THE OBSERVATION OUTRANKS THE RECORD.

    Seen live at the first drill: three tabs for two live lanes, the third running
    `tmux attach -t poga-D-1d23bc-WI-0146` against a lane that had died at spawn and
    printing `no such session`. The galling part was that attach ALREADY KNEW — discovery
    asks the work machine what tmux is running and holds the live set in its hand, then the
    open loop iterated the record's log instead and never cross-checked. Two sources of
    truth about the same question, one observed and one asserted, and it used the asserted
    one.
    """

    def test_a_logged_lane_with_no_tmux_session_gets_no_tab(self):
        self._write(log=("WI-0001", "WI-0002"))
        self.live = ["poga-D-test01-WI-0001"]          # WI-0002 died at spawn
        out, code = self._run()
        self.assertEqual(code, 0)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("WI-0001", lines[0])

    def test_the_dead_lane_is_named_not_silently_skipped(self):
        self._write(log=("WI-0001", "WI-0002"))
        self.live = ["poga-D-test01-WI-0001"]
        out, _ = self._run()
        self.assertIn("WI-0002: NOT RUNNING", out)
        self.assertIn("poga-D-test01-WI-0002", out)

    def test_cannot_tell_does_not_filter(self):
        # `None` is could-not-look. Refusing to open on an unanswered question would trade
        # a cosmetic bug for the silent-strand bug this command exists to prevent.
        self._write(log=("WI-0001", "WI-0002"))
        self._live_stub.stop()
        self.addCleanup(lambda: None)
        with mock.patch.object(session, "_attach_live_sessions",
                               return_value=(None, "no route")):
            out, code = self._run()
        self.assertEqual(len(self._lines()), 2)
        self.assertIn("CANNOT TELL what is running", out)

    def test_a_later_wave_still_opens_because_the_set_is_re_read_each_poll(self):
        # THE REGRESSION THIS GUARDS: filtering forever against a first-pass snapshot means
        # never opening a tab for wave two — trading a cosmetic bug for the silent-strand
        # bug this command exists to prevent. attach is a WATCHER.
        #
        # Poll 1: WI-0002 is logged but not yet in tmux (mid-start), so no tab.
        # Poll 2: it has come up, and the re-read finds it.
        # The queue stays non-empty so the dispatch is `draining` and the watcher polls
        # twice rather than declaring itself complete after the first pass.
        self._write(log=("WI-0001", "WI-0002"), queue=("WI-0003",))
        polls = {"n": 0}

        def later(host):
            polls["n"] += 1
            if polls["n"] == 1:
                return ["poga-D-test01-WI-0001"], ""
            return ["poga-D-test01-WI-0001", "poga-D-test01-WI-0002"], ""

        self._live_stub.stop()
        self.addCleanup(lambda: None)
        with mock.patch.object(session, "_attach_live_sessions", side_effect=later):
            out, code = self._run(once=False, max_polls=2, interval=1)
        lines = self._lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("WI-0002", lines[1])


class SaysWhenItsOwnCheckoutIsOldTest(AttachBase):
    """Property 11 (WI-0188) — THE OPERATOR'S COPY GOES STALE SILENTLY.

    In one hour of the first drill the same fact arrived three times wearing three masks —
    `not a poga verb`, then twice `is not a git repository` — each because a fix had landed
    minutes earlier and the operator's checkout predated it. ADR-0100 makes that machine a
    required participant and nothing kept it current or warned when it was not.

    It REPORTS and never pulls: pulling under an operator mid-fan-out is a surprise, and the
    whole point of the split is that this machine is where the human is.
    """

    def test_behind_is_reported_with_the_count(self):
        with mock.patch.object(session, "sh", side_effect=[
                _proc(rc=0, out="origin/main\n"), _proc(rc=0, out="4\n")]), \
             mock.patch.object(session.subprocess, "run", return_value=_proc(rc=0)):
            line = session._attach_checkout_staleness()
        self.assertIn("4 COMMIT(S) BEHIND origin/main", line)
        self.assertIn("git pull", line)

    def test_up_to_date_says_nothing_at_all(self):
        with mock.patch.object(session, "sh", side_effect=[
                _proc(rc=0, out="origin/main\n"), _proc(rc=0, out="0\n")]), \
             mock.patch.object(session.subprocess, "run", return_value=_proc(rc=0)):
            self.assertEqual(session._attach_checkout_staleness(), "")

    def test_no_upstream_is_cannot_tell_never_up_to_date(self):
        with mock.patch.object(session, "sh", return_value=_proc(rc=128, out="")):
            line = session._attach_checkout_staleness()
        self.assertIn("CANNOT TELL", line)

    def test_a_failed_fetch_is_cannot_tell_never_up_to_date(self):
        with mock.patch.object(session, "sh", return_value=_proc(rc=0, out="origin/main")), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=1, err="could not resolve host")):
            line = session._attach_checkout_staleness()
        self.assertIn("CANNOT TELL", line)
        self.assertIn("could not resolve host", line)

    def test_it_never_pulls(self):
        # The one thing this check must not do. `git fetch` is read-only; `git pull` is not.
        with mock.patch.object(session, "sh", side_effect=[
                _proc(rc=0, out="origin/main\n"), _proc(rc=0, out="2\n")]), \
             mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0)) as run:
            session._attach_checkout_staleness()
        for call in run.call_args_list:
            self.assertNotIn("pull", call[0][0])

    def test_no_fetch_skips_the_check_entirely(self):
        self._write(log=("WI-0001",))
        with mock.patch.object(session, "_attach_checkout_staleness") as chk:
            self._run(no_fetch=True)
        chk.assert_not_called()


class TheDrillClaimsAndReleasesForRealTest(AttachBase):
    """Property 12 (session ~173) — AS CLOSE TO THE REAL THING AS POSSIBLE.

    The ruling: a rehearsal needs test work items the system can really claim and release,
    so tests get as close to the real thing as possible. None existed. `poga
    drill` fabricated a `DRILL-N` id the store had never heard of and never claimed
    anything — so claim, the coordination record, and release, which are the first and last
    things a dispatched lane does AND the only parts that leave residue, were exactly the
    parts the rehearsal skipped. The one way to exercise them was on a real backlog item,
    which is what went wrong at the first drill.

    The split that makes both true at once: the ITEM is synthetic and untracked, the CLAIM
    is the production code path.
    """

    def _items_dir(self):
        return session._drill_items_dir()

    def test_a_drill_item_is_claimable_by_the_real_claim_path(self):
        session._drill_item_write("DRILL-1", "synthetic")
        self.assertIn("DRILL-1", dict(session._wi_claimable_items()))

    def test_it_parses_its_title_through_the_same_parser(self):
        session._drill_item_write("DRILL-1", "Drill lane 1 — rehearsal")
        self.assertEqual(dict(session._wi_claimable_items())["DRILL-1"],
                         "Drill lane 1 — rehearsal")

    def test_the_backlog_never_sees_it(self):
        # THE WHOLE POINT. `_wi_parse` is what the list, the chart, ROADMAP.md, the feed,
        # `wi-check` and the number allocator read. A drill that could reach any of them
        # would be recording permanent data, which is the rule this exists under.
        session._drill_item_write("DRILL-1", "synthetic")
        self.assertNotIn("DRILL-1", [i["id"] for i in session._wi_parse()])

    def test_it_lives_outside_the_repo_so_it_cannot_be_committed(self):
        f = session._drill_item_write("DRILL-1", "synthetic")
        self.assertIn(".session-state", str(f))
        self.assertNotIn("work-items", str(f))

    def test_clean_deletes_the_items_and_releases_their_claims(self):
        session._drill_item_write("DRILL-1", "synthetic")
        session._coord_try_acquire("claims", "DRILL-1", "lane", 3600)
        self.assertIn("DRILL-1", session._coord_list("claims"))
        items, released = session._drill_items_clear()
        self.assertEqual(items, 1)
        self.assertEqual(released, 1)
        self.assertNotIn("DRILL-1", session._coord_list("claims"))
        self.assertEqual(session._drill_items(), [])

    def test_clean_leaves_a_real_claim_alone(self):
        # A drill cleaning up must never touch the backlog's coordination records.
        session._coord_try_acquire("claims", "WI-0001", "lane", 3600)
        session._drill_items_clear()
        self.assertIn("WI-0001", session._coord_list("claims"))

    def test_the_lane_script_claims_and_releases(self):
        self.assertIn("work claim", session.DRILL_SCRIPT)
        self.assertIn("work release", session.DRILL_SCRIPT)
        # A refusal is a FINDING, not a silent pass — that is the whole reason to run it.
        self.assertIn("claim REFUSED", session.DRILL_SCRIPT)

    def test_the_lane_script_is_valid_shell(self):
        r = subprocess.run(["sh", "-n"], input=session.DRILL_SCRIPT,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_no_items_on_disk_means_nothing_claimable_beyond_the_store(self):
        # Normally absent: the directory exists only while a drill is running.
        self.assertEqual(session._drill_items(), [])


class AWaitingLaneRaisesItsOwnTabTest(AttachBase):
    """Property 13 (WI-0183) — THE PULL SURFACE BECOMES A PUSH ONE.

    ADR-0101's attention records are countable but passive: a lane that blocks on a
    question is a number on a board someone has to go and look at. attach is already
    polling and the record is already the truth, so the raise needs no new channel, no
    daemon and no poll of its own — it is best-effort decoration on top of a record that
    stands whether the raise works or not, which is exactly the split ADR-0101's D-record
    insists on.

    ALL FOUR MODES EXIST BECAUSE operator RULED THE RAISE CONFIGURABLE (session ~173: every
    mode available, the operator picks one). Every one is a real command in
    Ghostty's dictionary, verified by compiling each generated script against the installed
    Ghostty before any of it was trusted.
    """

    def _rec(self, item="WI-0001", created="2026-08-25T10:00:00+00:00", msg="a question"):
        return {"name": "worktree-poga-2", "item": item, "message": msg,
                "created_at": created,
                "expires_at": session.time.time() + 3600}

    def test_every_mode_generates_a_distinct_script(self):
        seen = {m: session._attach_raise_script("T1", m)
                for m in session.ATTACH_RAISE_MODES}
        self.assertEqual(seen["off"], "")
        self.assertIn("select tab", seen["select"])
        self.assertNotIn("activate window", seen["select"])
        self.assertIn("activate window", seen["activate"])
        self.assertIn("focus (focused terminal", seen["focus"])
        # Escalating: every louder mode still selects the tab.
        for m in ("select", "activate", "focus"):
            self.assertIn("select tab", seen[m])

    def test_an_unknown_mode_generates_nothing_rather_than_guessing(self):
        self.assertEqual(session._attach_raise_script("T1", "shout"), "")

    def test_the_default_is_activate_and_the_config_declares_it(self):
        # the operator's ruling, session ~173: `activate` is the default for now. Pinned
        # because a default that lives only in code is one nobody can find to change; the
        # config declares it so revising it is an edit rather than a release. The "for
        # now" is real — this was chosen before any tab had been watched raising.
        self.assertEqual(session.ATTACH_RAISE_DEFAULT, "activate")
        self.assertIn(session.ATTACH_RAISE_DEFAULT, session.ATTACH_RAISE_MODES)

    def test_the_config_value_beats_the_code_default(self):
        self._write(log=("WI-0001",))
        session.CFG["attach_raise"] = "shout"
        out, code = self._run(raise_mode="")
        self.assertEqual(code, 2)
        self.assertIn("is not one of", out)

    def test_activate_raises_the_window_not_just_the_tab(self):
        # The distinction the default now rests on: `select` alone leaves a buried Ghostty
        # buried, which is exactly what operator chose against.
        self.assertNotIn("activate window", session._attach_raise_script("T1", "select"))
        self.assertIn("activate window", session._attach_raise_script("T1", "activate"))

    def test_a_closed_tab_is_reported_not_crashed(self):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out="gone")):
            ok, detail = session._attach_raise("T1", "select")
        self.assertFalse(ok)
        self.assertIn("closed", detail)

    def test_the_tab_id_is_captured_when_a_tab_opens(self):
        window = {}
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out="W9\tT9")):
            ok, _ = session._attach_open_ghostty("cmd", window, "poga-D-x-WI-1")
        self.assertTrue(ok)
        self.assertEqual(window["id"], "W9")
        self.assertEqual(window["tabs"]["poga-D-x-WI-1"], "T9")

    def test_a_window_only_answer_degrades_to_no_raising(self):
        # An older Ghostty returning one field must still ATTACH. A lane that cannot be
        # raised is a missing decoration, not a broken attach.
        window = {}
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out="W9")):
            ok, _ = session._attach_open_ghostty("cmd", window, "poga-D-x-WI-1")
        self.assertTrue(ok)
        self.assertEqual(window["id"], "W9")
        self.assertEqual(window.get("tabs", {}), {})

    def test_a_waiting_lane_is_raised_once_per_question(self):
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        raised = set()
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=({"k": self._rec()}, "")), \
             mock.patch.object(session, "_attach_raise", return_value=(True, "")) as r:
            session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
            session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
        # Twice through the loop, ONE raise: a tab that jumps at you every twenty seconds
        # is one you learn to ignore.
        self.assertEqual(r.call_count, 1)

    def test_a_new_question_on_the_same_lane_raises_again(self):
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        raised = set()
        with mock.patch.object(session, "_attach_raise", return_value=(True, "")) as r:
            with mock.patch.object(session, "_attach_fetch_attention",
                                   return_value=({"k": self._rec(created="A")}, "")):
                session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
            with mock.patch.object(session, "_attach_fetch_attention",
                                   return_value=({"k": self._rec(created="B")}, "")):
                session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
        self.assertEqual(r.call_count, 2)

    def test_an_old_question_escalates_to_focus(self):
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=({"k": self._rec()}, "")), \
             mock.patch.object(session, "_iso_age_min", return_value=90), \
             mock.patch.object(session, "_attach_raise", return_value=(True, "")) as r:
            session._attach_raise_pass("D-test01", "", window, "select", 60, set())
        self.assertEqual(r.call_args[0][1], "focus")

    def test_a_fresh_question_does_not_escalate(self):
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=({"k": self._rec()}, "")), \
             mock.patch.object(session, "_iso_age_min", return_value=5), \
             mock.patch.object(session, "_attach_raise", return_value=(True, "")) as r:
            session._attach_raise_pass("D-test01", "", window, "select", 60, set())
        self.assertEqual(r.call_args[0][1], "select")

    def test_a_failed_raise_still_reports_the_question(self):
        # The record is the truth. A lane that could not raise itself must not become
        # LESS visible than one that never tried.
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        buf = io.StringIO()
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=({"k": self._rec()}, "")), \
             mock.patch.object(session, "_attach_raise", return_value=(False, "nope")):
            with redirect_stdout(buf):
                session._attach_raise_pass("D-test01", "", window, "select", 0, set())
        out = buf.getvalue()
        self.assertIn("WAITING ON YOU", out)
        self.assertIn("a question", out)

    def test_cannot_tell_is_said_once_and_is_not_fatal(self):
        window = {"tabs": {"poga-D-test01-WI-0001": "T1"}}
        raised = set()
        buf = io.StringIO()
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=(None, "no route")):
            with redirect_stdout(buf):
                session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
                session._attach_raise_pass("D-test01", "", window, "select", 0, raised)
        self.assertEqual(buf.getvalue().count("CANNOT TELL"), 1)

    def test_a_lane_with_no_tab_is_skipped_quietly(self):
        window = {"tabs": {"poga-D-test01-WI-0002": "T2"}}
        with mock.patch.object(session, "_attach_fetch_attention",
                               return_value=({"k": self._rec(item="WI-0001")}, "")), \
             mock.patch.object(session, "_attach_raise", return_value=(True, "")) as r:
            session._attach_raise_pass("D-test01", "", window, "select", 0, set())
        r.assert_not_called()

    def test_a_bad_mode_is_refused_by_name(self):
        self._write(log=("WI-0001",))
        out, code = self._run(raise_mode="shout")
        self.assertEqual(code, 2)
        self.assertIn("is not one of", out)

    def test_a_non_ghostty_terminal_says_raising_is_unavailable(self):
        # SAY IT, do not silently skip: a mode operator set and did not get is the kind of
        # quiet disagreement that only surfaces when he is waiting on a silent lane.
        self._write(log=("WI-0001",))
        out, _ = self._run(raise_mode="select")
        self.assertIn("only ghostty can raise a tab", out)


class AttentionIsReadFromTheWorkMachineTest(AttachBase):
    """The records live where the lanes are; the tabs live here."""

    def test_the_local_case_reads_the_store_directly(self):
        with mock.patch.object(session, "_coord_list", return_value={"a": {}}) as cl:
            recs, why = session._attach_fetch_attention("")
        cl.assert_called_once_with(session.ATTENTION_KIND)
        self.assertEqual(recs, {"a": {}})

    def test_the_remote_case_flattens_one_record_per_line(self):
        rec = {"name": "worktree-poga-2", "item": "WI-0001",
               "expires_at": session.time.time() + 3600}
        out = json.dumps(rec) + "\n"
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out=out)) as run:
            recs, why = session._attach_fetch_attention("devbox")
        self.assertEqual(run.call_args[0][0][0], "ssh")
        self.assertIn("worktree-poga-2", recs)

    def test_an_expired_record_is_dropped(self):
        rec = {"name": "old", "item": "WI-0001", "expires_at": 1}
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out=json.dumps(rec))):
            recs, _ = session._attach_fetch_attention("devbox")
        self.assertEqual(recs, {})

    def test_one_corrupt_record_does_not_blind_the_rest(self):
        good = json.dumps({"name": "ok", "item": "WI-0001",
                           "expires_at": session.time.time() + 3600})
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=0, out="{not json\n" + good)):
            recs, _ = session._attach_fetch_attention("devbox")
        self.assertIn("ok", recs)

    def test_an_unreachable_host_is_none_not_empty(self):
        with mock.patch.object(session.subprocess, "run",
                               return_value=_proc(rc=255, err="no route")):
            recs, why = session._attach_fetch_attention("devbox")
        self.assertIsNone(recs)
        self.assertIn("no route", why)


if __name__ == "__main__":
    unittest.main()
