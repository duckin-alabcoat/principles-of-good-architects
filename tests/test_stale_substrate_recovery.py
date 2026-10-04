"""WI-0151 — the recovery command must not be delivered by the mechanism it recovers.

FOUND session ~161 on a genuinely stuck Runner. `poga integrate` exists to publish a trunk
whose push was rejected because origin moved on. It printed `integrate is not a poga verb`,
because `~/.local/bin/poga` symlinks into the MAIN checkout and that checkout still held
pre-fix code: the fix for the blockage was sitting at origin, unreachable by exactly the
blockage it fixes. The only route out — merge origin into a LANE's branch and run that
lane's own harness — is one a stuck operator would have had to invent, and it needs a live
lane, which the machine that most needs recovering is the least likely to have.

THESE TESTS DRIVE THE REAL WRAPPER AS A SUBPROCESS, against a real bare origin holding a
real harness, because the subject is a disagreement BETWEEN two copies of the substrate.
Reading either copy's source cannot see it: each file is individually correct, and what is
wrong is which one the operator's PATH reaches. The fixture is therefore built the only way
that can fail honestly — an origin whose harness carries the verb, and a checkout cloned
before it did.

THE FIXTURE IS A FIXTURE ALL THE WAY DOWN. Every repo here is a temp clone of a temp bare
origin, so a run cannot reach the live checkout, the live origin, or the network — not
because these tests are trusted not to, but because there is nothing else in scope
([`evidence-is-separated-from-state-by-construction`]).

stdlib unittest: python3 -m unittest discover -s tests
"""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
# `push-substrate.py` imports `from common import …`, a sibling under curate/ — on the
# path before the hyphenated module is loaded, the same way `test_push_substrate` does it.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "curate"))

import importlib.util  # noqa: E402

from ambient_fixture import neutralize_ambient_env  # noqa: E402
from harness_fixture import harness_files, install_harness, patch_harness  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

_PS_SPEC = importlib.util.spec_from_file_location(
    "push_substrate", ROOT / "curate" / "push-substrate.py")
PUSH_SUBSTRATE = importlib.util.module_from_spec(_PS_SPEC)
_PS_SPEC.loader.exec_module(PUSH_SUBSTRATE)
POGA = ROOT / "poga"
GIT = shutil.which("git")
BASH = shutil.which("bash")

#: The one verb WI-0151 was found on. Used as the subject throughout because it is the
#: verb a stuck machine actually reaches for, and because its `--dry-run` reports which of
#: the five divergence cases it sees without writing anything.
VERB = "integrate"

#: The fixture's deliberate defect, in both halves of the substrate. Renaming rather than
#: deleting keeps each file syntactically whole, so what the test builds is a harness that
#: PREDATES the verb — not one that is broken.
POGA_CASE = "    integrate)\n"
POGA_CASE_OLD = "    integrate-not-yet)\n"
PARSER_ARG = '        "integrate",\n'
PARSER_ARG_OLD = '        "integrate-not-yet",\n'

#: The minimum `_load_config` accepts: it returns None on ANY missing key, and a None CFG
#: makes `integrate` refuse for a reason that has nothing to do with this item.
CONFIG = (
    '{\n'
    '  "architect_name": "Fixture Architect",\n'
    '  "architect_id": "fixture-arch",\n'
    '  "user_name": "test",\n'
    '  "role_doc": "role.md",\n'
    '  "handoff": "session-handoff.md",\n'
    '  "timezone": "UTC",\n'
    '  "machine_map": {}\n'
    '}\n'
)


def _git(repo, *args):
    return subprocess.run([GIT, "-C", str(repo), *args],
                          capture_output=True, text=True)


@unittest.skipUnless(GIT and BASH, "git+bash required")
class StaleSubstrateBase(unittest.TestCase):
    """A bare origin carrying the CURRENT harness, and a checkout cloned before it did.

    Built in that order deliberately. The obvious shortcut — clone once, then doctor the
    checkout's files in place — produces a DIRTY tree, and a dirty harness is the one case
    the recovery refuses outright; the fixture would then be testing the refusal in every
    test that meant to test the repair. Two pushes, with the clone taken between them, is
    what makes the local copy cleanly BEHIND, which is the real shape of a stuck machine.
    """

    #: Subclasses set these to say which half of the substrate predates the verb.
    stale_poga = False
    stale_harness = False

    def setUp(self):
        neutralize_ambient_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "-b", "main", "--bare", str(self.origin)],
                       check=True, capture_output=True, text=True)

        self.build = self._clone("build")
        self._lay_substrate(self.build, stale=True)
        (self.build / "session.config.json").write_text(CONFIG, encoding="utf-8")
        (self.build / "role.md").write_text("# role\n\n**Version:** 1.0.0\n", encoding="utf-8")
        self._commit(self.build, "seed: the substrate before the verb existed")
        _git(self.build, "push", "-q", "-u", "origin", "main")

        # THE CLONE HAPPENS HERE — between the two pushes. This checkout is the stuck
        # machine: complete, clean, and one commit behind the fix it needs.
        self.main = self._clone("main")

        self._lay_substrate(self.build, stale=False)
        self._commit(self.build, "substrate: the verb lands upstream")
        _git(self.build, "push", "-q", "origin", "main")

    # -- fixture construction --------------------------------------------------
    def _clone(self, name):
        path = self.tmp / name
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(path)],
                       check=True, capture_output=True, text=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "Test"),
                     ("commit.gpgsign", "false")):
            _git(path, "config", k, v)
        return path

    def _lay_substrate(self, repo, *, stale):
        """Install the real wrapper + harness, optionally rolled back to before the verb."""
        install_harness(repo, ROOT)
        shutil.copyfile(POGA, repo / "poga")
        (repo / "poga").chmod(0o755)
        if ROOT.joinpath("standard_check.py").is_file():
            shutil.copyfile(ROOT / "standard_check.py", repo / "standard_check.py")
        if not stale:
            return
        if self.stale_poga:
            self._retire_in_poga(repo)
        if self.stale_harness:
            n = patch_harness(repo, PARSER_ARG, PARSER_ARG_OLD)
            self.assertEqual(1, n, "the integrate subparser's shape moved — the fixture "
                                   "would now be describing a harness that still has it")

    def _retire_in_poga(self, repo):
        p = repo / "poga"
        src = p.read_text(encoding="utf-8")
        self.assertEqual(1, src.count(POGA_CASE),
                         "poga's integrate case label moved — fixture describes nothing")
        p.write_text(src.replace(POGA_CASE, POGA_CASE_OLD, 1), encoding="utf-8")

    def _commit(self, repo, msg):
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", msg)

    # -- running the subject ---------------------------------------------------
    def poga(self, *args, cwd=None, env=None):
        """Run the CHECKOUT'S OWN copy of the wrapper — never the repo's.

        This is the whole geometry of the defect: on a stuck machine `~/.local/bin/poga`
        is a symlink into the checkout, so the wrapper that must do the recovering is
        itself the stale one. Running the federation's live copy here would test a
        wrapper the stuck machine does not have."""
        cwd = cwd or self.main
        e = dict(os.environ)
        e.pop("POGA_SUBSTRATE_REFRESHED", None)
        e.pop("POGA_NO_SELF_REFRESH", None)
        e.update(env or {})
        return subprocess.run([BASH, str(self.main / "poga"), *args],
                              cwd=str(cwd), capture_output=True, text=True, env=e)

    def harness_carries_verb(self, repo, verb=VERB):
        """Does this checkout's harness know the verb? Asked the way the wrapper asks it."""
        r = subprocess.run([sys.executable, str(repo / "session.py"), verb, "--help"],
                           cwd=str(repo), capture_output=True, text=True)
        return r.returncode == 0

    def poga_carries_verb(self, repo, verb=VERB):
        return f"\n    {verb})\n" in (repo / "poga").read_text(encoding="utf-8")


class TheHarnessBehindTheWrapperIsStaleTest(StaleSubstrateBase):
    """The acceptance case, stated in the item: a checkout with NO OPEN LANE whose own
    `session.py` predates the verb, while origin has it.

    The wrapper knows the verb and forwards; argparse refuses the subcommand it has never
    heard of. Before WI-0151 that exit-2 was the end of the road."""

    stale_harness = True

    def test_the_fixture_really_is_stuck_before_the_recovery_runs(self):
        """A test whose subject is a REPAIR has to prove the damage first, or it passes
        for free on a fixture that was never broken."""
        self.assertFalse(self.harness_carries_verb(self.main))
        self.assertTrue(self.poga_carries_verb(self.main),
                        "this case is the wrapper KNOWING the verb and the harness not")
        r = self.poga(VERB, "--dry-run", env={"POGA_NO_SELF_REFRESH": "1"})
        self.assertEqual(2, r.returncode, r.stderr)
        self.assertIn("invalid choice", r.stderr)

    def test_it_refreshes_from_origin_and_the_verb_then_runs(self):
        r = self.poga(VERB, "--dry-run")
        self.assertIn("is not a\n      subcommand of", r.stderr, r.stderr)
        self.assertIn("refreshed", r.stderr, r.stderr)
        # The retry actually reached the verb. `--dry-run` reports the divergence case it
        # sees and writes nothing, so this asserts the verb RAN rather than merely existing.
        self.assertIn("integrate:", r.stdout + r.stderr,
                      f"the retry never reached the verb.\n{r.stdout}\n{r.stderr}")
        self.assertEqual(0, r.returncode, r.stderr)

    def test_the_checkout_is_left_able_to_run_the_verb_on_its_own(self):
        """Recovery means the MACHINE recovered, not that one invocation squeaked through.
        A repair that has to re-run on every call is a workaround wearing a fix's clothes."""
        self.poga(VERB, "--dry-run")
        self.assertTrue(self.harness_carries_verb(self.main))
        r = self.poga(VERB, "--dry-run", env={"POGA_NO_SELF_REFRESH": "1"})
        self.assertEqual(0, r.returncode, r.stderr)

    def test_no_hand_run_git_is_required_of_the_operator(self):
        """The item's acceptance, literally: the only thing typed is the verb itself.

        Pinned by asserting on what the tool EMITS, because the failure this replaces was
        not an error — it was a correct refusal that left a human holding the next step."""
        r = self.poga(VERB, "--dry-run")
        for banned in ("git pull", "git fetch", "git merge", "git reset", "git checkout"):
            self.assertNotIn(banned, r.stdout + r.stderr,
                             f"the recovery told the operator to run `{banned}` — the "
                             f"errand is the remaining defect")

    def test_a_bad_flag_on_a_verb_it_does_have_is_not_treated_as_staleness(self):
        """argparse answers 2 for a bad FLAG as well as for an unknown SUBCOMMAND, and
        collapsing the two would spend a fetch — and a substrate write — on every typo."""
        before = (self.main / "session.py").read_bytes()
        r = self.poga("main-sync", "--no-such-flag")
        self.assertEqual(2, r.returncode)
        self.assertNotIn("refreshed", r.stderr, r.stderr)
        self.assertEqual(before, (self.main / "session.py").read_bytes())


class TheWrapperItselfIsStaleTest(StaleSubstrateBase):
    """The reported case: the token is not in this wrapper's own verb table at all.

    This is what the Runner actually printed — `integrate is not a poga verb` — and the
    refusal was correct about this copy and wrong about the world."""

    stale_poga = True

    def test_the_fixture_really_is_stuck_before_the_recovery_runs(self):
        self.assertFalse(self.poga_carries_verb(self.main))
        r = self.poga(VERB, "--dry-run", env={"POGA_NO_SELF_REFRESH": "1"})
        self.assertEqual(2, r.returncode)
        self.assertIn("is not a poga verb", r.stderr)

    def test_an_unknown_verb_consults_origin_before_refusing(self):
        r = self.poga(VERB, "--dry-run")
        self.assertIn(f"exists at origin/main", r.stderr, r.stderr)
        self.assertIn("refreshed", r.stderr, r.stderr)
        self.assertTrue(self.poga_carries_verb(self.main))
        self.assertEqual(0, r.returncode, r.stderr)

    def test_a_token_origin_does_not_have_either_is_a_searched_negative(self):
        """`declare-what-a-check-assumes`. "Not a verb here" and "not a verb" are different
        answers and the refusal has to say which one it reached — otherwise the consult is
        invisible and a reader cannot tell an unexamined refusal from an examined one."""
        r = self.poga("definitely-not-a-verb")
        self.assertEqual(2, r.returncode)
        self.assertIn("consulted origin/main", r.stderr, r.stderr)
        self.assertIn("does not carry", r.stderr, r.stderr)
        self.assertIn("is not a poga verb", r.stderr)
        self.assertIn("verbs:", r.stderr, "the derived roster must still be printed")

    def test_it_says_so_rather_than_guessing_when_origin_cannot_be_read(self):
        """A checkout with no remote is not a checkout whose verb was disproved."""
        _git(self.main, "remote", "remove", "origin")
        r = self.poga(VERB, "--dry-run")
        self.assertEqual(2, r.returncode)
        self.assertIn("could not read origin/main", r.stderr, r.stderr)
        self.assertIn("not '" + VERB + " is not a verb'", r.stderr, r.stderr)
        self.assertFalse(self.poga_carries_verb(self.main), "nothing should have changed")

    def test_a_process_already_retrying_does_not_recover_again(self):
        """The loop guard, from the outside. A retry that could itself recover would fetch
        and rewrite the substrate once per hop for a verb nothing upstream has — so the
        flag has to be honoured, and honoured SILENTLY: it is an internal instruction, not
        a diagnosis the operator asked for."""
        r = self.poga(VERB, "--dry-run", env={"POGA_SUBSTRATE_REFRESHED": "1"})
        self.assertEqual(2, r.returncode)
        self.assertIn("is not a poga verb", r.stderr)
        self.assertNotIn("origin", r.stderr, r.stderr)
        self.assertNotIn("refreshed", r.stderr, r.stderr)
        self.assertFalse(self.poga_carries_verb(self.main))

    def test_the_opt_out_disables_the_consult_and_says_it_did(self):
        """The automatic path is the default and the escape is the opt-out, never the
        reverse — but an escape that operates silently is indistinguishable from the
        feature being absent."""
        r = self.poga(VERB, "--dry-run", env={"POGA_NO_SELF_REFRESH": "1"})
        self.assertIn("POGA_NO_SELF_REFRESH=1", r.stderr)
        self.assertNotIn("refreshed", r.stderr)
        self.assertFalse(self.poga_carries_verb(self.main))


class ItRefusesRatherThanOverwriteUnlandedWorkTest(StaleSubstrateBase):
    """The one thing a recovery path must never do is create the loss it was called to
    prevent. A locally MODIFIED harness file is somebody's unlanded work."""

    stale_poga = True

    def test_a_dirty_harness_file_stops_the_refresh_and_is_named(self):
        edited = self.main / "sessionlib" / "config.py"
        edited.write_text(edited.read_text(encoding="utf-8") + "\n# local work\n",
                          encoding="utf-8")
        before = edited.read_bytes()
        r = self.poga(VERB, "--dry-run")
        self.assertEqual(2, r.returncode)
        self.assertIn("REFUSED to refresh", r.stderr, r.stderr)
        self.assertIn("sessionlib/config.py", r.stderr, r.stderr)
        self.assertEqual(before, edited.read_bytes(), "the edit must survive untouched")
        self.assertFalse(self.poga_carries_verb(self.main))


class BytecodeIsNotUnlandedWorkTest(StaleSubstrateBase):
    """WI-0468: found on Linux, where python3 writes __pycache__ into the tree. Apple's
    python3 keeps bytecode outside it, so on macOS this only showed with Homebrew's. An
    untracked __pycache__ under the harness made the refresh refuse as if it were work."""

    stale_harness = True

    def test_untracked_bytecode_does_not_stop_the_refresh(self):
        cache = self.main / "sessionlib" / "__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "config.cpython-312.pyc").write_bytes(b"\x00bytecode")
        (self.main / "stray.pyc").write_bytes(b"\x00bytecode")
        r = self.poga(VERB, "--dry-run")
        self.assertNotIn("REFUSED to refresh", r.stderr, r.stderr)
        self.assertIn("refreshed", r.stderr, r.stderr)
        self.assertEqual(0, r.returncode, r.stderr)
        self.assertTrue(self.harness_carries_verb(self.main))


@unittest.skipUnless(GIT and BASH, "git+bash required")
class TheLaneUnderYourFeetIsNewerTest(unittest.TestCase):
    """WI-0151's second half, same root as the first.

    A lane runs the MAIN checkout's wrapper through the PATH symlink while running its own
    `session.py`, so a lane can hold newer code than the dispatcher fronting it — and the
    two disagree silently. The answer is nearer to hand than origin and costs no network:
    ask the tree you are standing in first."""

    def setUp(self):
        neutralize_ambient_env(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "main"
        self.main.mkdir()
        subprocess.run([GIT, "init", "-q", "-b", "main", str(self.main)],
                       check=True, capture_output=True, text=True)
        for k, v in (("user.email", "t@example.invalid"), ("user.name", "Test"),
                     ("commit.gpgsign", "false")):
            _git(self.main, "config", k, v)
        install_harness(self.main, ROOT)
        (self.main / "session.config.json").write_text(CONFIG, encoding="utf-8")
        shutil.copyfile(POGA, self.main / "poga")
        (self.main / "poga").chmod(0o755)
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed")
        # The lane: a real linked worktree, which is what `poga` allocates.
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        # NOW roll the main checkout's wrapper back. The lane keeps the current one, which
        # is the live shape: a lane cut from a newer base than the copy on PATH.
        src = (self.main / "poga").read_text(encoding="utf-8")
        self.assertEqual(1, src.count(POGA_CASE))
        (self.main / "poga").write_text(src.replace(POGA_CASE, POGA_CASE_OLD, 1),
                                        encoding="utf-8")

    def test_it_uses_the_standing_trees_wrapper_without_touching_the_network(self):
        e = dict(os.environ)
        e.pop("POGA_SUBSTRATE_REFRESHED", None)
        e.pop("POGA_NO_SELF_REFRESH", None)
        r = subprocess.run([BASH, str(self.main / "poga"), VERB, "--dry-run"],
                           cwd=str(self.lane), capture_output=True, text=True, env=e)
        self.assertIn("checkout you are standing in has it", r.stderr, r.stderr)
        # There is no remote at all here, so reaching origin was never an option: the
        # answer had to come from the tree under our feet or not at all.
        self.assertNotIn("origin", r.stderr, r.stderr)
        self.assertIn("integrate:", r.stdout + r.stderr, r.stdout + r.stderr)


class TheRefreshSetIsTheExecutableSubstrateTest(unittest.TestCase):
    """The list of files the recovery replaces is a claim about what the substrate IS, and
    a hand-kept claim drifts from the authority that ships it.

    `curate/push-substrate.py` is that authority: `BYTE_IDENTICAL` is what goes to every
    member byte-for-byte. The recovery takes the EXECUTABLE half of it and deliberately
    leaves the generated docs, because no amount of content makes a verb dispatchable —
    so this pins the relationship, not a transcription of the list
    ([`derive-a-checks-subjects-from-the-authority`])."""

    def _declared_roots(self):
        src = POGA.read_text(encoding="utf-8")
        line = next(l for l in src.splitlines() if l.startswith("POGA_HARNESS_ROOTS="))
        return set(line.split("=", 1)[1].strip().strip('"').split())

    def test_it_covers_every_executable_file_the_fleet_is_sent(self):
        roots = self._declared_roots()
        for rel in PUSH_SUBSTRATE.BYTE_IDENTICAL:
            if rel.endswith(".md"):
                continue           # content, deliberately out of scope
            top = rel.split("/", 1)[0]
            with self.subTest(substrate=rel):
                self.assertIn(top, roots,
                              f"{rel} ships fleet-wide and decides what runs, but the "
                              f"recovery would not refresh it")

    def test_it_refreshes_nothing_the_fleet_is_not_sent(self):
        """The other direction. A root that is not substrate would be this recovery
        quietly reaching past its own subject."""
        shipped = {rel.split("/", 1)[0] for rel in PUSH_SUBSTRATE.BYTE_IDENTICAL}
        self.assertLessEqual(self._declared_roots(), shipped)

    def test_the_harness_package_is_read_from_the_tree_not_listed_here(self):
        """`sessionlib/` is named once, as a directory. Naming its modules would be the
        transcribed list that goes stale the day one is added."""
        roots = self._declared_roots()
        self.assertIn("sessionlib", roots)
        for rel in harness_files(ROOT):
            if "/" in rel:
                self.assertNotIn(rel, roots)


if __name__ == "__main__":
    unittest.main()
