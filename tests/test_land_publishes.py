"""WI-0355 / ADR-0134 — a land publishes, or says out loud that it did not.

`session.py merge` pushed only with `--push`, a `store_true` that defaulted off, while every
dispatch brief lands with `merge --continue` or `merge --commit "<msg>"` and no flag. So
every dispatched lane landed locally and left the trunk owed to whichever interactive
session next noticed — measured in session ~302 at 6 unpushed commits on local main at one
point and 9 at another, all from lanes that had landed cleanly hours before.

WHAT THESE PIN, and each one is a property the fix could plausibly lose:

  1. THE HEADLINE — a land with no flags at all puts the work on origin. `merge`, `end`,
     and the `_merge_close_args` fallback all default to publishing.
  2. `--no-push` still lands locally AND reports the push as owed, so the escape hatch does
     not restore the invisible state the item is about.
  3. A FAILED PUSH DOES NOT FAIL THE LAND. The commits are on the local trunk; what is
     owed is one retry, not a rollback (ADR-0134 D2).
  4. AND IT IS NOT SILENT (D3). This is the one that matters most: an auto-push that
     swallows its own failure reproduces WI-0355's bug with extra steps. The banner must
     distinguish "landed and pushed" from "landed, push owed".
  5. THE PUSH IS BOUNDED (D4). `sh` passes no timeout, so a black-holed push hangs inside
     the serialized section — the worst failure available here, because the lane goes on
     reporting live while holding the land gate against every queued peer.
  6. An UNREACHABLE remote does not buy a second trip down the same dead route (D5), and
     a REJECTED push still escalates to the WI-0143 integrate that repairs it.
  7. No remote configured is not an owed push (D7).
  8. The structural guard: no lander may go back to pushing on its own.
"""

import ast
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import harness_fixture  # noqa: E402
from coord_fixture import neutralize_coord_journal, neutralize_dispatch_env  # noqa: E402

GIT = shutil.which("git")
PASS_GATE = [["python3", "-c", "import sys; sys.exit(0)"]]


def _git(repo, *args, check=True):
    return subprocess.run([GIT, "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


# ───────────────────────────── 1. the default itself ─────────────────────────────

class TheDefaultIsToPublishTest(unittest.TestCase):
    """The CLI seam WI-0355 is actually about.

    The landers already defaulted `push: bool = True` — and it bought nothing, because every
    CLI call site passed `push=args.push` and argparse's `store_true` made that `False`. A
    test that reads the function signature would have called this fixed for months. So these
    parse real argv."""

    def _parsed(self, argv):
        seen = {}

        def capture(args):
            seen["ns"] = args
            raise SystemExit(0)

        with mock.patch.object(session, "cmd_merge", capture), \
             mock.patch.object(session, "cmd_end", capture), \
             mock.patch.object(sys, "argv", ["session.py", *argv]), \
             contextlib_suppress_systemexit():
            session.main()
        return seen.get("ns")

    def test_a_bare_merge_publishes(self):
        """The command the dispatch brief actually contains."""
        self.assertTrue(self._parsed(["merge"]).push)

    def test_merge_continue_publishes(self):
        """`merge --continue` is what `_drive_lane_merge` runs and what every lane-facing
        brief in lanes.py prints. It carried no `--push` and therefore never pushed."""
        self.assertTrue(self._parsed(["merge", "--continue"]).push)

    def test_end_publishes_too(self):
        """`end` reaches the same three landers. Flipping only `merge` would have left the
        defect alive behind a second door (ADR-0134, rejected alternative 2)."""
        self.assertTrue(self._parsed(["end"]).push)

    def test_no_push_is_the_opt_out(self):
        self.assertFalse(self._parsed(["merge", "--no-push"]).push)
        self.assertFalse(self._parsed(["end", "--no-push"]).push)

    def test_the_old_flag_still_parses(self):
        """STANDARD.md ships `merge --commit "<message>" --push` to every member and they do
        not all re-render on the same day. Removing the flag would turn a stale doc into a
        hard argparse error on the one verb a member cannot work without."""
        self.assertTrue(self._parsed(["merge", "--push"]).push)
        self.assertTrue(self._parsed(["end", "--push"]).push)

    def test_the_close_path_fallback_is_the_default_not_the_old_value(self):
        """`_merge_close_args` read `getattr(args, "push", False)`. Left alone, an in-process
        caller building a bare namespace would keep landing local-only while the CLI
        published — a half-applied default flip, which is worse than none."""
        import argparse
        ns = session._merge_close_args(argparse.Namespace(commit=None))
        self.assertTrue(ns.push)


import contextlib  # noqa: E402


@contextlib.contextmanager
def contextlib_suppress_systemexit():
    try:
        yield
    except SystemExit:
        pass


# ───────────────────────── 2. the pusher's three outcomes ─────────────────────────

class PushOutcomeClassificationTest(unittest.TestCase):
    """THREE OUTCOMES, NEVER TWO. Before this change "we did not push" and "we tried and
    could not" were the same silence."""

    def test_the_measured_devbox_fault_reads_as_unreachable(self):
        """Not invented wording — this is verbatim what a broken route to
        github.com:22 emitted, intermittently, while this item was being worked."""
        real = ("Read from remote host github.com: Operation timed out\\n"
                "client_loop: send disconnect: Broken pipe\\n"
                "fatal: Could not read from remote repository.")
        self.assertTrue(session._push_is_unreachable(real))

    def test_a_non_fast_forward_is_not_unreachable(self):
        """The distinction the whole of D5 rests on. A rejection means origin ANSWERED, and
        the WI-0143 integrate repairs it; misreading one as a network fault would silently
        delete a working self-heal."""
        rejection = ("! [rejected]        main -> main (fetch first)\\n"
                     "error: failed to push some refs to 'github.com:x/y.git'\\n"
                     "hint: Updates were rejected because the remote contains work that you do\\n"
                     "hint: not have locally.")
        self.assertFalse(session._push_is_unreachable(rejection))

    def test_an_unrecognised_failure_is_treated_as_a_rejection(self):
        """The classifier is allowed to be INCOMPLETE. It is not allowed to be wrong in the
        direction that removes the repair — so anything it cannot place keeps the old
        escalating behaviour."""
        self.assertFalse(session._push_is_unreachable("error: something nobody has seen yet"))

    def test_a_hung_push_is_cut_off_rather_than_waited_out(self):
        """D4. `sh` passes no timeout, so a black-holed push hangs for as long as the kernel
        allows — inside the serialized section, holding the land gate, while the lane goes on
        reporting live. That is the failure mode a detached lane cannot survive."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.email", "t@t")
            _git(repo, "config", "user.name", "T")
            _git(repo, "config", "commit.gpgsign", "false")
            # A COMMIT IS LOAD-BEARING HERE. Without one git refuses at `src refspec main
            # does not match any` in 13ms, having never opened a socket — so the first cut
            # of this test passed a timeout it never exercised and asserted on a refusal it
            # had mistaken for a network fault.
            (repo / "a.txt").write_text("x\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "first")
            _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
            with mock.patch.object(session, "ROOT", repo):
                out = session._push_trunk("main", timeout=1.0)
        self.assertFalse(out.ok)
        self.assertTrue(out.unreachable, out)
        self.assertIn("PUSH OWED", session._push_owed_banner(out.owed, "main"))

    def test_no_remote_is_not_an_owed_push(self):
        """D7. A fixture repo is the common case in the suite; if every one of them reported
        a stalled delivery, `push owed` would mean nothing inside a week."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = pathlib.Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            with mock.patch.object(session, "ROOT", repo):
                out = session._push_trunk("main")
        self.assertTrue(out.ok)
        self.assertEqual(out.owed, "")
        self.assertEqual(out.line, "")

    def test_a_real_push_reports_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            origin = tmp / "origin.git"
            subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(origin)],
                           check=True, capture_output=True)
            repo = tmp / "repo"
            repo.mkdir()
            _git(repo, "init", "-q", "-b", "main")
            _git(repo, "config", "user.email", "t@t")
            _git(repo, "config", "user.name", "T")
            _git(repo, "config", "commit.gpgsign", "false")
            (repo / "a.txt").write_text("x\\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-qm", "first")
            _git(repo, "remote", "add", "origin", str(origin))
            with mock.patch.object(session, "ROOT", repo):
                out = session._push_trunk("main")
            self.assertTrue(out.ok, out)
            self.assertEqual(out.owed, "")
            local = _git(repo, "rev-parse", "main").stdout.strip()
            remote = subprocess.run([GIT, "-C", str(origin), "rev-parse", "main"],
                                    capture_output=True, text=True).stdout.strip()
            self.assertEqual(local, remote,
                             "the acceptance clause: origin equals the local trunk")


# ───────────────────────── 3. what the banner says ─────────────────────────

class TheBannerDistinguishesTheTwoStatesTest(unittest.TestCase):
    """WI-0355's own warning: "an auto-push that silently swallows its own failure would
    reproduce this item's bug with extra steps." The banner is where that is prevented."""

    def test_a_successful_publish_is_silent(self):
        """The push is a step of the land now, so its happening is not news."""
        self.assertEqual(session._land_publish_suffix(True, {}, "main"), "")

    def test_an_owed_push_is_loud_and_names_the_verb_that_fixes_it(self):
        s = session._land_publish_suffix(True, {"push_owed": "origin unreachable (x)"}, "main")
        self.assertIn("PUSH OWED", s)
        self.assertIn("origin unreachable (x)", s)
        self.assertIn("session.py integrate", s,
                      "a report with no remedy is a report the reader cannot act on")
        self.assertIn("nothing is lost", s,
                      "the lane must not read this as a reason to roll anything back")

    def test_the_deliberate_opt_out_says_so(self):
        """`--no-push` and 'the network ate it' are different states of the world and the
        banner must not render them the same way."""
        s = session._land_publish_suffix(False, {}, "main")
        self.assertIn("--no-push", s)
        self.assertNotIn("PUSH OWED", s)

    def test_the_receipt_carries_the_owed_reason_without_breaking_the_timing_line(self):
        """`_land_hold_line` formats every numeric stage as seconds. A string stage must ride
        along without becoming `push_owed 0.000s` — `publish_error` already relies on that
        and this is the second key to do so."""
        rec = {"stages": {"push": 1.44, "push_owed": "origin unreachable (x)"},
               "lock_seconds": 2.0, "queue_seconds": 0.0, "validate_seconds": 0.0}
        line = session._land_hold_line(rec)
        self.assertIn("push 1.440s", line)
        self.assertNotIn("push_owed", line)


# ───────────────────────── 4. end to end, through a real land ─────────────────────────

class ALandPublishesEndToEndTest(unittest.TestCase):
    """The acceptance clause, exercised rather than argued: a land with no human action puts
    the work on origin, and `--no-push` lands locally and reports the push owed."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "T")
        # Or the fixture inherits `commit.gpgsign` from whoever runs the suite and breaks
        # on a machine configured to sign (tests/test_fixture_git_defaults.py).
        _git(self.repo, "config", "commit.gpgsign", "false")
        (self.repo / "seed.txt").write_text("seed\\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "seed")
        _git(self.repo, "remote", "add", "origin", str(self.origin))
        _git(self.repo, "push", "-q", "origin", "main")

    def _one_commit_ahead(self):
        (self.repo / "work.txt").write_text("work\\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "work")
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def _origin_main(self):
        return subprocess.run([GIT, "-C", str(self.origin), "rev-parse", "main"],
                              capture_output=True, text=True).stdout.strip()

    def test_the_push_reaches_origin_with_no_flag_and_no_human(self):
        tip = self._one_commit_ahead()
        self.assertNotEqual(self._origin_main(), tip, "precondition: origin is behind")
        with mock.patch.object(session, "ROOT", self.repo):
            out = session._push_trunk("main")
        self.assertTrue(out.ok, out)
        self.assertEqual(self._origin_main(), tip,
                         "ACCEPTANCE: origin/main equals the local trunk after the land")

    def test_a_dead_origin_leaves_the_work_on_the_local_trunk(self):
        """D2. The commits are already on the trunk and are not lost; what is owed is one
        retry, not a rollback. The land must not undo anything."""
        tip = self._one_commit_ahead()
        _git(self.repo, "remote", "set-url", "origin", "https://example.invalid/x.git")
        with mock.patch.object(session, "ROOT", self.repo):
            out = session._push_trunk("main", timeout=1.0)
        self.assertFalse(out.ok)
        self.assertEqual(_git(self.repo, "rev-parse", "main").stdout.strip(), tip,
                         "the local trunk still carries the work")
        self.assertTrue(out.owed, "and the land knows it owes a push")


# ───────────────────────── 5. the structural guard ─────────────────────────

class NoLanderPushesOnItsOwnTest(unittest.TestCase):
    """`add-structural-guard-on-recurrence`. Three near-identical push blocks is how this
    neighbourhood got here: a fix applied to one of them and not the others, and a `push
    failed:` line that nobody could tell from a successful land. One pusher, or the
    divergence comes straight back (ADR-0134 D6)."""

    #: The functions allowed to publish a ref directly. `_push_trunk` IS the pusher;
    #: `_integrate_trunk_with_remote` is the out-of-band retry, which runs without the land
    #: gate and answers a different question; `_park_unpushed_trunk` writes a `parked/` ref,
    #: not the trunk; `_push_if_ahead` is the ADR-0055 session-start deferred push;
    #: `git_sync` is the SessionStart sync; `_rel_tag_publish` pushes a tag atomically.
    #: A nested def INHERITS its enclosing function's permission — `_integrate_trunk_with_
    #: remote`'s inner `_push()` is that function's own pusher, not a fourth copy.
    ALLOWED = {"_push_trunk", "_integrate_trunk_with_remote", "_park_unpushed_trunk",
               "_push_if_ahead", "git_sync", "_rel_tag_publish"}

    @staticmethod
    def _pushes_in(fn):
        """Every `["git", "push", ...]` argv literal lexically inside `fn`, skipping any
        nested def (which is walked on its own, with its own permission)."""
        found = []

        def walk(node, top=True):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not top:
                    continue
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and top:
                    continue
                if isinstance(child, ast.List):
                    vals = [e.value for e in child.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                    # `--delete` REMOVES a ref; it never publishes the trunk, so it is not
                    # this guard's business (`cmd_retire_parked` retires `parked/` branches).
                    if vals[:2] == ["git", "push"] and "--delete" not in vals:
                        found.append(child.lineno)
                walk(child, top=False)

        for stmt in fn.body:
            walk(stmt, top=False)
        return found

    def test_the_landers_do_not_call_git_push_themselves(self):
        offenders = []
        for rel in harness_fixture.harness_files():
            tree = ast.parse((harness_fixture.ROOT / rel).read_text(encoding="utf-8"))
            allowed_lines = set()
            for fn in ast.walk(tree):
                if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and fn.name in self.ALLOWED):
                    for node in ast.walk(fn):
                        if isinstance(node, ast.List):
                            allowed_lines.add(node.lineno)
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name in self.ALLOWED:
                    continue
                for lineno in self._pushes_in(fn):
                    if lineno in allowed_lines:
                        continue        # nested inside an allowed function
                    offenders.append(f"{rel}:{lineno} in {fn.name}()")
        self.assertEqual(sorted(set(offenders)), [],
                         "these push the trunk without going through `_push_trunk`, so they "
                         "get no timeout, no unreachable/rejected split, and no owed "
                         "reporting: " + ", ".join(sorted(set(offenders))))

    def test_the_guard_would_actually_catch_a_new_lander(self):
        """`a-detector-proves-itself-on-the-real-defect`. A guard that finds zero offenders
        proves nothing until it is shown the shape it exists to refuse — the block that was
        in all three landers before this change."""
        tree = ast.parse(
            "def _land_something(trunk):\n"
            "    pr = sh([\"git\", \"push\", \"origin\", f\"{trunk}:{trunk}\"], check=False)\n")
        fn = tree.body[0]
        self.assertTrue(self._pushes_in(fn),
                        "the guard must fire on the exact block it replaced")

    def test_the_guard_ignores_a_ref_deletion(self):
        """The negative control. `cmd_retire_parked` deletes `parked/` branches on origin;
        a guard that cannot tell publishing from deleting gets loosened by whoever trips
        it next (`a-guard-that-fires-on-correct-code-gets-deleted`)."""
        tree = ast.parse(
            "def cmd_x(b):\n"
            "    r = sh([\"git\", \"push\", \"origin\", \"--delete\", b], check=False)\n")
        self.assertEqual(self._pushes_in(tree.body[0]), [])

    def test_the_push_timeout_is_derived_from_the_hold_budget(self):
        """A second literal is a second thing to forget. A push still running when the time
        for the whole serialized section is spent is not going to finish."""
        self.assertEqual(session.LAND_PUSH_TIMEOUT_SECONDS,
                         session.LAND_LOCK_HOLD_BUDGET_SECONDS)

    def test_the_standard_no_longer_teaches_the_flag(self):
        """STANDARD.md is what every member reads. While it said `--push`, the default flip
        would have looked optional to every Architect in the fleet."""
        txt = (harness_fixture.ROOT / "STANDARD.md").read_text(encoding="utf-8")
        self.assertNotIn('merge --commit "<message>" --push', txt)
        self.assertIn("The land publishes", txt)

    def test_the_headless_runner_declares_its_opt_out(self):
        """ADR-0050's rule — the one outward act stays off the unattended path — used to be
        inherited from a default. A rule that rests on a default is a rule that silently
        inverts when the default moves, which is exactly what happened here."""
        txt = (harness_fixture.ROOT / "curate" / "adopt-runner.py").read_text(encoding="utf-8")
        self.assertIn("--no-push", txt)


if __name__ == "__main__":
    unittest.main()
