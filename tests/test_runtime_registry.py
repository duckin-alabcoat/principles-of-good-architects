"""The runtime registry and its refusals (WI-0033 Phase 1, ADR-0082 D2/D3/D4).

`poga` owns the exec but not the table. The registry lives here in Python for one
concrete reason: `curate/push-substrate.py` refuses to push unless this suite is green,
and this suite cannot reach bash — a table in `poga` would be the one part of the launch
path nothing gates.

Two properties these tests exist to defend.

**Every resolution failure is NAMED, and named DIFFERENTLY.** Unknown alias, known
runtime with no command configured, and configured command not installed here need three
different fixes, so they get three exit codes. Folding them into one "couldn't launch" is
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes) — the
operator cannot tell whether they typed it wrong, whether we never wired it, or whether
they need to install something.

**The refusal happens BEFORE a lane exists.** The failure this replaces is a raw
`no such file or directory` thrown from inside `exec`, after a worktree and branch have
already been created — a confusing error plus litter to clean up. The known-bad case on
a machine without codex on PATH is pinned here, as is
the commandless-row refusal, which since the chatgpt removal has no shipped instance and
is therefore exercised against an injected row rather than deleted with its example.

`shutil.which` is patched in every test that touches it. A test whose result depends on
what happens to be installed on the machine running it is the WI-0054 flakiness class, and
this file would otherwise pass on one machine and fail on a box where codex IS installed.
"""

import io
import pathlib
import sys
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


class _Args:
    """argparse.Namespace stand-in. Tests call cmd_runtime_resolve DIRECTLY rather than
    through the CLI, because that is how poga's own caller and any future wrapper reach
    it — the same reason WI-0044 pinned the never-a-major rule inside the function
    instead of trusting argparse `choices`."""

    def __init__(self, runtime=None, list=False):
        self.runtime = runtime
        self.list = list


class RuntimeRowTest(unittest.TestCase):
    def test_resolves_by_canonical_id_and_by_short_alias(self):
        """Both forms are first-class (D2) — `-r c` and `-r claude-code` are one request."""
        for key in ("claude-code", "c"):
            with self.subTest(key=key):
                row = session._runtime_row(key)
                self.assertIsNotNone(row)
                self.assertEqual(row["id"], "claude-code")

    def test_the_declared_roster_is_the_three_that_can_actually_be_launched(self):
        """Roster set in session ~110 as four; `chatgpt` was removed in ~111 on the operator's
        correction — it cannot be run bare in a terminal at all, only by invoking `codex`,
        which is already its own row. It was a product name in a column of executables.
        A new runtime must be a deliberate row, not a silent arrival — this test is what
        makes adding one show up in review.

        Session ~125: `gemini` OUT, `antigravity` IN: Google's supported terminal agent
        is Antigravity, and the roster lists supported runtimes only. Note the removal is
        not the chatgpt case — `gemini` launched fine — so this roster is what SHOULD be
        started, not merely what CAN be."""
        self.assertEqual({r["alias"] for r in session.RUNTIMES}, {"c", "cx", "ag"})
        self.assertEqual({r["id"] for r in session.RUNTIMES},
                         {"claude-code", "codex", "antigravity"})

    def test_the_retired_gemini_alias_is_not_silently_inherited(self):
        """`-r g` must REFUSE, not quietly hand over a different product. Reusing a freed
        alias is how muscle memory gets a working session with something it did not ask
        for — indistinguishable from success, which is the failure mode worth a test."""
        self.assertIsNone(session._runtime_row("g"))
        self.assertIsNone(session._runtime_row("gemini"))

    def test_antigravity_declares_the_binary_not_the_product_name(self):
        """The `antigravity` package installs its executable as `agy`. Declaring the
        product name would make D4 refuse every launch as not-installed — a refusal that
        reads as 'you haven't installed it' when it is in fact installed."""
        row = session._runtime_row("antigravity")
        self.assertEqual(row["command"], "agy")

    def test_every_declared_row_can_actually_be_launched(self):
        """The rule the chatgpt removal establishes: a row exists to be launched. A
        commandless row is a promise nobody can keep — it reads as 'not wired up yet'
        when the truth may be 'this will never be a terminal command'."""
        for row in session.RUNTIMES:
            with self.subTest(runtime=row["id"]):
                self.assertTrue(row["command"],
                                f"{row['id']} declares no launch command")

    def test_unknown_key_and_empty_key_both_resolve_to_nothing(self):
        for key in ("bogus", "", None, "   "):
            with self.subTest(key=key):
                self.assertIsNone(session._runtime_row(key))

    def test_aliases_are_unique(self):
        """An alias collision would make one runtime unreachable by its short form, and
        which one you got would depend on registry order."""
        aliases = [r["alias"] for r in session.RUNTIMES]
        self.assertEqual(len(aliases), len(set(aliases)))

    def test_a_returned_row_cannot_mutate_the_registry(self):
        row = session._runtime_row("c")
        row["command"] = "tampered"
        self.assertEqual(session._runtime_row("c")["command"], "claude")

    def test_the_model_slot_is_reserved_on_every_row(self):
        """D2's SECOND axis — which model inside the runtime. Unused in Phase 1, present
        on every row so filling it later is data rather than a schema change. Pinned so
        removing it as 'dead' is a deliberate act."""
        for row in session.RUNTIMES:
            with self.subTest(runtime=row["id"]):
                self.assertIn("model_flag", row)

    def test_the_argv_shape_column_is_declared_on_every_row(self):
        """ADR-0082 D3 argv-shape column (WI-0096): how an opening prompt is passed.
        antigravity uses -i; claude-code and codex are positional."""
        for row in session.RUNTIMES:
            with self.subTest(runtime=row["id"]):
                self.assertIn("argv_shape", row)
                if row["id"] == "antigravity":
                    self.assertEqual(row["argv_shape"], "-i")
                else:
                    self.assertEqual(row["argv_shape"], "positional")

    def test_only_claude_code_claims_native_guards(self):
        """D6: a runtime's guard posture is DECLARED, and everything that is not Claude's
        PreToolUse surface must not silently claim to have it."""
        for row in session.RUNTIMES:
            with self.subTest(runtime=row["id"]):
                if row["id"] == "claude-code":
                    self.assertEqual(row["guards"], "native")
                else:
                    self.assertNotEqual(row["guards"], "native")


class ResolveRefusalTest(unittest.TestCase):
    """Three failures, three exit codes, three different fixes."""

    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as cm:
                session.cmd_runtime_resolve(args)
        return cm.exception.code, out.getvalue(), err.getvalue()

    def test_unknown_runtime_exits_2_and_shows_the_real_roster(self):
        """An operator who mistypes an alias needs to see the actual ones immediately,
        not go reading source."""
        code, _, err = self._run(_Args(runtime="bogus"))
        self.assertEqual(code, 2)
        self.assertIn("not a known runtime", err)
        # Derived from the registry, not hardcoded: this test asks "does the refusal show
        # the REAL roster", and a literal list here would fail whenever a row changes for
        # a reason this test has no opinion about. Pinning WHICH rows exist is
        # `test_the_declared_roster_is_the_three_that_can_actually_be_launched`'s job.
        for row in session.RUNTIMES:
            with self.subTest(alias=row["alias"]):
                self.assertRegex(err, rf"\b{row['alias']}\b")
                self.assertIn(row["id"], err)

    def test_runtime_with_no_command_configured_exits_3(self):
        """No SHIPPED row is commandless since chatgpt was removed, so this injects one.
        The guard stays tested on its own terms rather than being deleted along with its
        only instance — a refusal path that exists but is unexercised is how the next
        commandless row would ship broken. Distinct from 'you need to install something':
        nothing the operator installs fixes this one."""
        row = {"id": "notional", "alias": "nx", "command": None,
               "worktree_flag": None, "model_flag": None, "guards": "undeclared"}
        with patch.object(session, "RUNTIMES", session.RUNTIMES + (row,)):
            code, _, err = self._run(_Args(runtime="nx"))
        self.assertEqual(code, 3)
        self.assertIn("no launch command configured", err)

    def test_configured_command_missing_from_path_exits_4_and_names_the_fix(self):
        """codex on a machine that lacks it. The fix IS an operator action, so the message has to
        carry it — this is the case that would otherwise surface as a raw exec error after
        a lane had already been created."""
        with patch("session.shutil.which", return_value=None):
            code, _, err = self._run(_Args(runtime="cx"))
        self.assertEqual(code, 4)
        self.assertIn("not on PATH", err)
        self.assertIn("ln -s", err)

    def test_the_three_refusals_are_distinguishable_from_each_other(self):
        """The whole point of separate codes: a caller must be able to branch on them."""
        row = {"id": "notional", "alias": "nx", "command": None,
               "worktree_flag": None, "model_flag": None, "guards": "undeclared"}
        with patch.object(session, "RUNTIMES", session.RUNTIMES + (row,)):
            with patch("session.shutil.which", return_value=None):
                codes = {
                    self._run(_Args(runtime="bogus"))[0],
                    self._run(_Args(runtime="nx"))[0],
                    self._run(_Args(runtime="cx"))[0],
                }
        self.assertEqual(codes, {2, 3, 4})


class ResolveSuccessTest(unittest.TestCase):
    def _run(self, args):
        out = io.StringIO()
        with redirect_stdout(out):
            session.cmd_runtime_resolve(args)
        return out.getvalue()

    def _kv(self, text):
        return dict(line.split("=", 1) for line in text.strip().splitlines() if "=" in line)

    def test_resolving_emits_the_row_poga_needs(self):
        with patch("session.shutil.which", return_value="/fake/bin/claude"):
            kv = self._kv(self._run(_Args(runtime="c")))
        self.assertEqual(kv["POGA_RUNTIME_ID"], "claude-code")
        self.assertEqual(kv["POGA_RUNTIME_COMMAND"], "/fake/bin/claude")
        self.assertEqual(kv["POGA_RUNTIME_WORKTREE_FLAG"], "--worktree")
        self.assertEqual(kv["POGA_RUNTIME_GUARDS"], "native")
        self.assertEqual(kv["POGA_RUNTIME_ARGV_SHAPE"], "positional")

    def test_resolving_antigravity_emits_interactive_prompt_shape(self):
        with patch("session.shutil.which", return_value="/fake/bin/agy"):
            kv = self._kv(self._run(_Args(runtime="ag")))
        self.assertEqual(kv["POGA_RUNTIME_ARGV_SHAPE"], "-i")

    def test_the_command_emitted_is_the_RESOLVED_absolute_path(self):
        """poga execs what this prints. Emitting the bare name would re-resolve against
        whatever PATH the exec happens to see, which is not necessarily the PATH we just
        verified against."""
        with patch("session.shutil.which", return_value="/somewhere/else/claude"):
            kv = self._kv(self._run(_Args(runtime="claude-code")))
        self.assertEqual(kv["POGA_RUNTIME_COMMAND"], "/somewhere/else/claude")

    def test_no_argument_resolves_the_default_runtime(self):
        """Phase 1 changes no behaviour: a bare `poga` still gets Claude."""
        with patch("session.shutil.which", return_value="/fake/bin/claude"):
            kv = self._kv(self._run(_Args(runtime=None)))
        self.assertEqual(kv["POGA_RUNTIME_ID"], session.RUNTIME_DEFAULT)
        self.assertEqual(kv["POGA_RUNTIME_ID"], "claude-code")

    def test_a_runtime_without_a_native_worktree_flag_emits_an_empty_one(self):
        """Not an error — it selects the launch STYLE. An empty flag means poga creates
        the lane itself and launches with it as cwd, the off-repo pad path."""
        with patch("session.shutil.which", return_value="/fake/bin/codex"):
            kv = self._kv(self._run(_Args(runtime="cx")))
        self.assertEqual(kv["POGA_RUNTIME_WORKTREE_FLAG"], "")

    def test_list_prints_every_runtime_and_does_not_exit(self):
        """`--list` is the discoverability half of declaring aliases — an abbreviation you
        can only learn by reading source is worse than no abbreviation."""
        text = self._run(_Args(list=True))
        for row in session.RUNTIMES:
            self.assertIn(row["id"], text)
            self.assertIn(row["alias"], text)

    def test_list_names_a_runtime_that_has_no_command_rather_than_hiding_it(self):
        """No shipped row is commandless since chatgpt was removed, so this injects one.
        The property still matters: a roster that silently omitted an unlaunchable row
        would let an operator pick it and only find out at exec."""
        row = {"id": "notional", "alias": "nx", "command": None,
               "worktree_flag": None, "model_flag": None, "guards": "undeclared"}
        with patch.object(session, "RUNTIMES", session.RUNTIMES + (row,)):
            text = self._run(_Args(list=True))
        self.assertIn("no launch command configured", text)

    def test_list_shows_every_shipped_runtime(self):
        text = self._run(_Args(list=True))
        for row in session.RUNTIMES:
            with self.subTest(runtime=row["id"]):
                self.assertIn(row["id"], text)


if __name__ == "__main__":
    unittest.main()
