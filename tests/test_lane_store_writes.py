"""WI-0193 — a shared-store write cannot be BORN stranded inside a lane.

THE DEFECT, and it is not hypothetical. OPS-0006 (a monthly Auditor-class spot audit,
already 43 days overdue) was created with `python3 session.py ops-new` from inside lane
poga-2. A lane's checkout carries its own TRACKED copy of `ops-items/`, frozen at the
lane's base commit, which no other checkout ever reads. The obligation existed, rendered
in that lane's own startup banner as a genuine overdue item, and was absent from
`poga work list`, from a status board, and from every other session on the machine. It
reached main only because a human noticed two banners disagreeing. OPS-0004 was not that
lucky: drawn in a lane, never landed, deleted, its number permanently burned because
main's allocator had already moved past it.

WHY THE FRONT DOOR DID NOT ALREADY COVER THIS. WI-0125 built `poga ops` for exactly this
case and `require_repo` anchors it on the MAIN checkout, so the front door has been
correct all along. What it could not do is shut the other door: `session.py` is on disk in
every lane, its `--help` advertises `ops-new`, and nothing between the verb and the file
asked which checkout was about to be written. Two surfaces of one fact, fixed on one of
them — the shape `add-structural-guard-on-recurrence` exists to stop.

WHAT IS PINNED HERE, in the order the acceptance asks for it:

  * the REAL invocation, from a REAL linked worktree — `git worktree add`, the lane's own
    checked-out `session.py`, the argv that filed OPS-0006 — refuses and writes nothing;
  * the NEGATIVE CONTROL, which is the half that makes the first one mean something: the
    identical command from the main checkout still creates the file. Without it this suite
    would pass just as happily against a harness that had stopped writing the store at all;
  * the refused set is DERIVED from the harness AST, not curated here, so a store verb
    added tomorrow cannot quietly arrive with no rule;
  * the two deliberate lane-local carve-outs (`renumber`, `wi-commit`) still run in the
    lane, so a later "consistency" pass cannot fold them in without turning this red.

stdlib unittest: python3 -m unittest discover -s tests
"""

import ast
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
import harness_fixture  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

BASE_CFG = {
    "architect_name": "Architect", "architect_id": "member-arch", "user_name": "operator",
    "role_doc": "role.md", "handoff": "handoff.md", "timezone": "UTC",
    "machine_map": {"anything": "TestBox"},
}


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def harness(repo, *args):
    """Run the harness THAT LIVES IN `repo`, from `repo`.

    Both halves matter. The harness resolves the tree it drives from its own file
    location, and a lane's copy is the one checked out into the lane — so invoking the
    main checkout's `session.py` with `cwd` set to the lane would be a different
    experiment entirely, and the one this guard is not about."""
    return subprocess.run([sys.executable, str(repo / "session.py"), *args],
                          capture_output=True, text=True, cwd=str(repo), check=False)


def make_repo(tmp):
    """A member checkout with a committed harness — committed because a linked worktree
    is populated from the INDEX, so an uncommitted `session.py` would leave the lane with
    no harness to run at all."""
    repo = pathlib.Path(tmp) / "member"
    repo.mkdir(parents=True)
    harness_fixture.install_harness(repo)
    (repo / "role.md").write_text("role\n", encoding="utf-8")
    (repo / "session.config.json").write_text(json.dumps(BASE_CFG, indent=2),
                                              encoding="utf-8")
    for d in ("work-items", "ops-items", "adr"):
        (repo / d).mkdir()
        (repo / d / ".keep").write_text("", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def add_lane(repo):
    """A REAL linked worktree — the thing under test. Nothing here simulates one: the
    whole class of defect is a property of how git populates and isolates a second
    working tree, and a mock of that would be a mock of the subject."""
    lane = repo.parent / "lane"
    r = git(repo, "worktree", "add", "-q", "-b", "worktree-lane", str(lane))
    assert lane.is_dir(), r.stderr
    return lane


# ── The refused set is derived from the harness, never curated in this file ────────

STORE_WRITERS = {"_wi_write_item", "_ops_write_item"}


def _call_graph():
    """`{function name: set of names it calls}` over the whole harness.

    Read from the AST rather than from a list in this test, for the reason
    `derive-a-checks-subjects-from-the-authority` gives: a hand-kept roster of store verbs
    here would go stale in exactly the same silence as the one this guard replaces."""
    tree = ast.parse(harness_fixture.harness_source())
    graph = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            graph[node.name] = {c.func.id for c in ast.walk(node)
                                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    return graph


def derived_store_writing_verbs(depth=2):
    """Every `wi-*` / `ops-*` verb whose command function reaches a store writer.

    BOUNDED AT TWO HOPS, deliberately, and the bound is the honest part. Unbounded
    reachability through a namespace this size answers "almost everything" — `wi-next`,
    which creates nothing at all, reaches `_wi_write_item` down a five-call chain through
    shared number-release machinery. Two hops is "this command writes a record, or calls
    one helper that does", which is the question the guard is actually asking. A verb that
    buries its write deeper is not silently exempt: it is simply not DERIVED here, and the
    carve-out list it must then appear in is asserted below to be short and reasoned."""
    graph = _call_graph()
    out = set()
    for name, calls in graph.items():
        if not (name.startswith("cmd_wi_") or name.startswith("cmd_ops_")):
            continue
        reached = set(calls)
        for callee in calls:
            reached |= graph.get(callee, set())
        if reached & STORE_WRITERS:
            out.add(name[len("cmd_"):].replace("_", "-"))
    return out


class TheRefusedSetIsDerivedFromTheWritersTest(unittest.TestCase):
    """The forgetting direction is the one that has to fail loudly.

    A guard whose subject list is typed by hand covers what its author remembered on the
    day. `_store_autocommit` lives at one hook point for precisely this reason — *a verb
    added tomorrow cannot forget a step that is not its to remember* — and a list here
    would put the forgetting back one level up."""

    def test_every_store_writing_verb_carries_a_rule(self):
        derived = derived_store_writing_verbs()
        self.assertTrue(derived, "the AST walk found no store-writing verbs at all — the "
                                 "derivation stopped describing the harness")
        known = session.LANE_STRANDED_STORE_VERBS | session.LANE_LOCAL_STORE_VERBS
        missing = sorted(derived - known)
        self.assertEqual(missing, [],
                         f"{missing} write the store and appear in neither "
                         f"LANE_STRANDED_STORE_VERBS nor LANE_LOCAL_STORE_VERBS — a new "
                         f"store verb arrived with no rule about which checkout it may "
                         f"write, which is the whole defect WI-0193 closes")

    def test_a_verb_cannot_be_both_refused_and_lane_local(self):
        self.assertEqual(
            session.LANE_STRANDED_STORE_VERBS & session.LANE_LOCAL_STORE_VERBS, set())

    def test_the_deliberate_carve_outs_stay_out_of_the_refused_set(self):
        """`renumber`'s subject is a record that exists ONLY in the lane — that is what
        makes its number collide — and `wi-commit` is the retry `_store_autocommit` prints
        when its own commit fails. Both would be actively broken by a refusal, and both
        are the kind of asymmetry a later tidying pass deletes without reading. Pinned so
        that pass turns this red instead."""
        for verb in ("wi-renumber", "ops-renumber", "wi-commit"):
            self.assertIn(verb, session.LANE_LOCAL_STORE_VERBS, verb)
            self.assertNotIn(verb, session.LANE_STRANDED_STORE_VERBS, verb)

    def test_the_creating_verbs_the_item_names_are_refused(self):
        """The two the acceptance names outright, asserted by name as well as by
        derivation: the derivation could go blind, and these two are the reason the item
        exists."""
        self.assertIn("ops-new", session.LANE_STRANDED_STORE_VERBS)
        self.assertIn("wi-new", session.LANE_STRANDED_STORE_VERBS)


class TheGuardRunsFromARealLinkedWorktreeTest(unittest.TestCase):
    """The acceptance, run end to end: a real repo, a real `git worktree add`, the lane's
    own checked-out harness, and the argv that filed OPS-0006."""

    def setUp(self):
        neutralize_ambient_env(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = make_repo(self.tmp.name)
        self.lane = add_lane(self.repo)

    def _ops_files(self, tree):
        return sorted(p.name for p in (tree / "ops-items").glob("OPS-*.md"))

    def _wi_files(self, tree):
        return sorted(p.name for p in (tree / "work-items").glob("WI-*.md"))

    def test_ops_new_from_a_lane_refuses_and_writes_nothing(self):
        """The exact shape that filed OPS-0006."""
        r = harness(self.lane, "ops-new", "Monthly spot audit", "--cadence", "monthly")
        self.assertNotEqual(r.returncode, 0,
                            f"the lane's ops-new succeeded — stdout={r.stdout!r}")
        self.assertEqual(self._ops_files(self.lane), [],
                         "an obligation file was created in the lane's frozen store")
        self.assertEqual(self._ops_files(self.repo), [],
                         "the refusal wrote into the main checkout instead of refusing")

    def test_wi_new_from_a_lane_refuses_and_writes_nothing(self):
        r = harness(self.lane, "wi-new", "Some item")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self._wi_files(self.lane), [])
        self.assertEqual(self._wi_files(self.repo), [])

    def test_the_refusal_is_on_stderr_so_a_captured_value_is_never_corrupted(self):
        """Same argument `_report_checkout_staleness` makes: `N=$(session.py …)` captures
        stdout, and a refusal that landed there would hand the caller a number-shaped
        blob of prose."""
        r = harness(self.lane, "ops-new", "x")
        self.assertEqual(r.stdout.strip(), "")
        self.assertIn("REFUSING", r.stderr)

    def test_the_refusal_names_the_front_door_for_that_namespace(self):
        self.assertIn("poga ops new", harness(self.lane, "ops-new", "x").stderr)
        self.assertIn("poga work new", harness(self.lane, "wi-new", "x").stderr)

    def test_the_remedy_it_prints_is_runnable_verbatim(self):
        """A remedy has to be runnable by the reader it is printed to (WI-0158) — and the
        reader here has just been stopped mid-command, so retyping is exactly what they
        will not do carefully. A title holding a space and a backtick must come back
        quoted, or the pasted line is a different command."""
        r = harness(self.lane, "ops-new", "audit `poga` monthly")
        self.assertIn("poga ops new 'audit `poga` monthly'", r.stderr)

    def test_the_same_command_from_the_main_checkout_still_creates_the_file(self):
        """THE NEGATIVE CONTROL, and the half without which none of the above means
        anything: every assertion above would pass just as well against a harness that had
        stopped writing the store entirely."""
        r = harness(self.repo, "ops-new", "Monthly spot audit", "--cadence", "monthly")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self._ops_files(self.repo)), 1,
                         f"the main checkout's ops-new created nothing — {r.stderr}")

    def test_a_read_verb_from_a_lane_is_untouched(self):
        """The bound on the blast radius. Reading the store from a lane is normal and
        constant — the startup banner does it — and a guard that stopped it would be
        removed within a day, taking the write refusal with it."""
        r = harness(self.lane, "wi-list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("REFUSING", r.stderr)

    def test_a_lane_local_verb_still_runs_in_the_lane(self):
        """`wi-commit` sweeps the tree it is standing in; a refusal here would name a
        front door that cannot reach the lane's own uncommitted files."""
        r = harness(self.lane, "wi-commit")
        self.assertNotIn("REFUSING to write the store from a lane", r.stderr)


class TheCheckHasThreeOutcomesTest(unittest.TestCase):
    """`declare-what-a-check-assumes`: main checkout, lane, and *cannot tell*. The third
    must borrow neither of the first two's sentences — a guard that cannot answer and says
    nothing renders byte-identical to one that checked and approved, which is the exact
    collapse this whole class of defect is made of."""

    def _run(self, main_answer, verb="ops-new"):
        err = io.StringIO()
        with mock.patch.object(session, "_main_checkout", return_value=main_answer), \
             mock.patch.object(session, "ROOT",
                               pathlib.Path("/repo/.claude/worktrees/p1")), \
             mock.patch.object(session.sys, "stderr", err):
            try:
                session._refuse_lane_store_write(verb, ["title"])
            except SystemExit as e:
                return "refused", int(e.code or 0), err.getvalue()
        return "proceeded", 0, err.getvalue()

    def test_the_main_checkout_proceeds_in_silence(self):
        """The common case must cost nothing to read, or the line stops being read."""
        outcome, _code, err = self._run(pathlib.Path("/repo/.claude/worktrees/p1"))
        self.assertEqual(outcome, "proceeded")
        self.assertEqual(err.strip(), "")

    def test_a_lane_is_refused(self):
        outcome, code, err = self._run(pathlib.Path("/repo"))
        self.assertEqual(outcome, "refused")
        self.assertEqual(code, session.LANE_STORE_REFUSAL_EXIT)
        self.assertIn("REFUSING", err)

    def test_an_unanswerable_git_says_so_and_does_not_refuse(self):
        """It must NOT refuse: `_main_checkout` returns None when git cannot resolve a
        common dir, and turning that into a hard stop would brick the store in every
        not-quite-a-repo case the harness is otherwise fine in. It must also not be
        silent, which is the whole point."""
        outcome, _code, err = self._run(None)
        self.assertEqual(outcome, "proceeded")
        self.assertIn("CANNOT TELL", err)
        self.assertIn("poga ops new", err)

    def test_the_refusal_exit_is_distinct_from_the_two_it_could_be_confused_with(self):
        """2 is argparse's *you used it wrong*; 3 is `_refuse_undrawn`'s *could not draw*.
        This is neither: the request is well-formed and answerable, and addressed to the
        wrong checkout."""
        self.assertNotIn(session.LANE_STORE_REFUSAL_EXIT, (0, 1, 2, 3))


class TheFrontDoorIsDerivedNotTabulatedTest(unittest.TestCase):
    """One spelling of `poga`'s own routing rule. A second table here would drift from
    `cmd_work` / `cmd_ops` the first time a verb was renamed, and it would drift in the
    direction that prints a remedy which does not exist."""

    def test_the_namespaces_map_the_way_poga_routes_them(self):
        self.assertEqual(session._store_front_door("wi-new"), "poga work new")
        self.assertEqual(session._store_front_door("ops-ran"), "poga ops ran")
        self.assertEqual(session._store_front_door("wi-status"), "poga work status")

    def test_every_refused_verb_maps_to_a_front_door_poga_actually_serves(self):
        """The remedy is only worth printing if `poga` really routes it. Read out of
        `poga`'s own case arms rather than asserted from memory."""
        src = (ROOT / "poga").read_text(encoding="utf-8")
        for verb in sorted(session.LANE_STRANDED_STORE_VERBS):
            door = session._store_front_door(verb)
            leaf = door.rsplit(" ", 1)[1]
            self.assertIn(leaf, src, f"`{door}` is printed as the remedy for {verb} but "
                                     f"`poga` has no such verb")


class TheChokePointRunsItTest(unittest.TestCase):
    """WI-0222's placement argument, reused: one hook point every verb passes through, so
    the next store verb is covered by arriving rather than by being remembered."""

    def test_main_runs_the_guard_before_the_staleness_report(self):
        """ORDER IS LOAD-BEARING, not tidiness. `_report_checkout_staleness` can pay a real
        `git fetch` — 0.81 s measured, up to the full timeout with the network down — to
        warn about a store this invocation is about to be refused from touching."""
        import inspect
        src = inspect.getsource(session.main)
        guard = src.find("_refuse_lane_store_write(")
        stale = src.find("_report_checkout_staleness(")
        self.assertNotEqual(guard, -1, "main() no longer runs the WI-0193 guard at all")
        self.assertNotEqual(stale, -1)
        self.assertLess(guard, stale,
                        "the refusal now runs after the staleness probe, so a refused "
                        "call can buy a network fetch it never needed")


if __name__ == "__main__":
    unittest.main()
