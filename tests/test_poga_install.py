"""`poga install` — the one-time per-machine installation verb (2026-07-23 brief).

Design constraints under test: symlink never copy (a foreign plain file is REFUSED,
never overwritten); idempotent and converging (correct -> no-op, wrong target ->
repair with old->new notice); user-scope with no hardcoded home (everything derives
from $HOME/$ROOT at run time); honest exit (a run that edited ~/.zshrc must not
report plain success). The verb is driven through the real script with HOME pointed
at a scratch dir, so nothing touches the operator's real ~/.local/bin.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
POGA = ROOT / "poga"


def _anchored_poga() -> str:
    """The poga the symlink must point at: the MAIN checkout's copy. The script anchors
    on the git common dir's parent (session-90 fix), so when the suite runs in a linked
    worktree — the merge gate's scratch checkout — `install` correctly links to the main
    checkout's poga, not the scratch copy invoking it. The test must expect the same."""
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute",
                        "--git-common-dir"], capture_output=True, text=True)
    common = pathlib.Path(r.stdout.strip()) if r.returncode == 0 else ROOT / ".git"
    return os.path.realpath(common.parent / "poga")


@unittest.skipUnless(shutil.which("bash"), "bash not available")
class PogaInstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self._tmp.name)
        self.bin = self.home / ".local" / "bin"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, path_extra="", shell="/bin/zsh", os_name=None):
        """`shell` is the user's $SHELL (None unsets it); `os_name`, when given, is what a
        stubbed `uname -s` reports, so the bash-host cases run on any machine."""
        if os_name == "Darwin" and hasattr(os, "geteuid") and os.geteuid() == 0:
            # WI-0484: as root, only a Linux cloud container passes poga's root check,
            # and a stubbed Darwin is (correctly) not one.
            self.skipTest("a simulated macOS refuses root; the suite is running as root")
        env = {**os.environ, "HOME": str(self.home)}
        env.pop("SHELL", None)
        if shell is not None:
            env["SHELL"] = shell
        # A minimal controlled PATH; append ~/.local/bin only when the case wants it.
        env["PATH"] = "/usr/bin:/bin" + (f":{path_extra}" if path_extra else "")
        if os_name is not None:
            stub = self.home / "stub-bin"
            stub.mkdir(exist_ok=True)
            (stub / "uname").write_text(f"#!/bin/sh\necho {os_name}\n", encoding="utf-8")
            (stub / "uname").chmod(0o755)
            env["PATH"] = f"{stub}:{env['PATH']}"
        return subprocess.run(["bash", str(POGA), "install"],
                              capture_output=True, text=True, env=env)

    def test_fresh_install_links_and_reports_the_new_terminal_step(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        link = self.bin / "poga"
        self.assertTrue(link.is_symlink(), "install must symlink, never copy")
        self.assertEqual(os.path.realpath(link), _anchored_poga())
        # PATH lacked ~/.local/bin -> ~/.zshrc gained the export, and the exit is
        # qualified (honest exit), not plain success.
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"',
                      (self.home / ".zshrc").read_text(encoding="utf-8"))
        self.assertIn("open a new terminal", proc.stdout)

    def test_the_advice_names_the_export_line_and_not_rehash(self):
        """External review 2026-09-30: the message said `rehash`, which rebuilds zsh's
        command table and never reads ~/.zshrc, so the new PATH export does not reach the
        shell it is typed in. The advice must be a line that works in this shell."""
        for pre_exported in (False, True):
            with self.subTest(zshrc_already_exports=pre_exported):
                if pre_exported:
                    shutil.rmtree(self.bin.parent)
                    (self.home / ".zshrc").write_text(
                        'export PATH="$HOME/.local/bin:$PATH"\n', encoding="utf-8")
                proc = self._run()  # PATH lacks ~/.local/bin
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn('    export PATH="$HOME/.local/bin:$PATH"\n', proc.stdout)
                self.assertIn("open a new terminal", proc.stdout)
                self.assertNotIn("rehash", proc.stdout + proc.stderr)
                self.assertNotIn("now works", proc.stdout,
                                 "this shell cannot resolve poga yet")

    def test_rerun_is_a_noop(self):
        self._run()
        zshrc_before = (self.home / ".zshrc").read_text(encoding="utf-8")
        proc = self._run(path_extra=str(self.bin))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("nothing to do", proc.stdout)
        self.assertEqual((self.home / ".zshrc").read_text(encoding="utf-8"),
                         zshrc_before, "a converged re-run must not touch ~/.zshrc")

    def test_wrong_target_symlink_is_repaired_with_notice(self):
        self.bin.mkdir(parents=True)
        stale = self.home / "stale-checkout-poga"
        stale.write_text("#!/bin/bash\n", encoding="utf-8")
        (self.bin / "poga").symlink_to(stale)
        proc = self._run(path_extra=str(self.bin))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("repaired symlink", proc.stdout)
        self.assertIn(str(stale), proc.stdout, "the notice reports old -> new")
        self.assertEqual(os.path.realpath(self.bin / "poga"), _anchored_poga())

    def test_foreign_plain_file_is_refused_and_untouched(self):
        self.bin.mkdir(parents=True)
        foreign = self.bin / "poga"
        foreign.write_text("#!/bin/bash\n# someone else's tool\n", encoding="utf-8")
        proc = self._run(path_extra=str(self.bin))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("REFUSED", proc.stderr)
        self.assertFalse(foreign.is_symlink(), "the foreign file must not be replaced")
        self.assertIn("someone else's tool", foreign.read_text(encoding="utf-8"))

    def test_zshrc_already_exporting_is_never_appended_again(self):
        """The first live run on a second machine: a non-interactive shell's PATH lacks
        ~/.local/bin even though ~/.zshrc exports it — the append must gate on the
        FILE's content, not this shell's PATH, or every remote run accretes a line."""
        (self.home / ".zshrc").write_text(
            'export PATH="$HOME/.local/bin:$PATH"\n# rest of rc\n', encoding="utf-8")
        proc = self._run()  # PATH deliberately does NOT include self.bin
        self.assertEqual(proc.returncode, 0, proc.stderr)
        content = (self.home / ".zshrc").read_text(encoding="utf-8")
        self.assertEqual(content.count('export PATH="$HOME/.local/bin:$PATH"'), 1,
                         "no duplicate export may be appended")
        self.assertIn("already exports", proc.stdout)

    def test_path_already_covered_means_unqualified_success_and_no_zshrc_edit(self):
        self.bin.mkdir(parents=True)
        proc = self._run(path_extra=str(self.bin))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse((self.home / ".zshrc").exists(),
                         "PATH already covers ~/.local/bin — no ~/.zshrc edit")
        self.assertIn("bare `poga` now works", proc.stdout)

    # WI-0467: the export goes to the rc file of the user's own shell, not always ~/.zshrc.

    EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'

    def test_a_bash_user_off_macos_gets_bashrc(self):
        proc = self._run(shell="/bin/bash", os_name="Linux")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(self.EXPORT, (self.home / ".bashrc").read_text(encoding="utf-8"))
        self.assertFalse((self.home / ".zshrc").exists(), "a bash host's line went to ~/.zshrc")
        self.assertIn("added an export line to ~/.bashrc", proc.stdout)
        self.assertIn("open a new terminal", proc.stdout)

    def test_a_bash_user_on_macos_gets_bash_profile(self):
        """macOS terminals open login shells, which read ~/.bash_profile, not ~/.bashrc."""
        proc = self._run(shell="/bin/bash", os_name="Darwin")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(self.EXPORT, (self.home / ".bash_profile").read_text(encoding="utf-8"))
        self.assertFalse((self.home / ".bashrc").exists())
        self.assertFalse((self.home / ".zshrc").exists())

    def test_a_bashrc_already_exporting_is_not_appended_again(self):
        (self.home / ".bashrc").write_text(self.EXPORT + "\n", encoding="utf-8")
        proc = self._run(shell="/usr/bin/bash", os_name="Linux")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            (self.home / ".bashrc").read_text(encoding="utf-8").count(self.EXPORT), 1)
        self.assertIn("~/.bashrc already exports", proc.stdout)

    def test_zsh_is_unchanged_on_either_os(self):
        for os_name in ("Darwin", "Linux"):
            with self.subTest(os=os_name):
                rc = self.home / ".zshrc"
                if rc.exists():
                    rc.unlink()
                proc = self._run(shell="/usr/local/bin/zsh", os_name=os_name)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(self.EXPORT, rc.read_text(encoding="utf-8"))
                self.assertIn("added an export line to ~/.zshrc", proc.stdout)

    # No unset-$SHELL case: bash fills an unset SHELL from the passwd entry before the
    # script runs, so the case cannot be staged through the real script.

    def test_another_shell_is_told_the_exact_line_and_nothing_is_written(self):
        proc = self._run(shell="/usr/bin/fish", os_name="Linux")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("    " + self.EXPORT + "\n", proc.stdout)
        self.assertIn("Add this line", proc.stdout)
        for rc in (".zshrc", ".bashrc", ".bash_profile"):
            self.assertFalse((self.home / rc).exists(), rc)
        self.assertNotIn("now works", proc.stdout)


if __name__ == "__main__":
    unittest.main()
