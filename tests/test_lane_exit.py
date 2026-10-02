"""WI-0312 — the ONE lane exit, wired and bound to a durable identity.

WI-0298 shipped `_lane_exit_release` with a docstring calling itself *the* path a lane
leaves by. It was not: exactly one caller reached it, `cmd_worktree_remove`, and land,
reap and recovery each still let go their own way. The acceptance ruled for this item is
literally the sentence *make the docstring true* — so `TheDocstringIsTrueTest` is a
structural check over the source rather than a behaviour test, because "there is no second
release implementation" is a claim about the code, not about a run.

The other half is the hazard the convergence creates. Routing every route through one
release makes that release's ownership rule load-bearing everywhere at once — and it keyed
on the LANE BRANCH, which `_coord_mine_for_refresh` had already established is a location
that every session in that lane shares. On the renewal side that confusion made a record
immortal (the 07:27 wedge). On the release side it points the other way: a cleanup that
runs late frees the records of whoever is sitting in that lane NOW.
`ReusedLaneIsNotTheSameSessionTest` is that case, once per kind that can carry it.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling fixtures
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")

OLD = "20260910T0100Z-devbox-old0"       # the session that finished in the lane
NEW = "20260910T0500Z-devbox-new0"       # the session sitting in it now


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class LaneExitBase(unittest.TestCase):
    """One repo, one lane, two sessions — the same shape `test_land_gate_liveness` uses,
    because it is the same confusion seen from the release side."""

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
        (self.main / "f.txt").write_text("x\n", encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "init")
        self.lane = self.main / ".claude" / "worktrees" / "poga-1"
        _git(self.main, "worktree", "add", "-q", "-b", "worktree-poga-1",
             str(self.lane), "main")
        self.journals = self.lane / "sessions" / "journal"
        self.journals.mkdir(parents=True)
        self._save = {k: getattr(session, k)
                      for k in ("ROOT", "CFG", "JOURNAL_DIR", "_LAND_GATE_DEPTH")}
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        session._LAND_GATE_DEPTH = 0
        session.ROOT = self.lane
        session.JOURNAL_DIR = self.journals
        self.identity = "worktree-poga-1"

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── the two sessions ─────────────────────────────────────────────────────────
    def _journal(self, sid, started, ended=""):
        """Write a journal for `sid`. `ended` blank means the session is still open."""
        (self.journals / f"{sid}.md").write_text(
            "---\n"
            f"session-id: {sid}\n"
            f"claude-session-id: csid-{sid}\n"
            f"started: {started}\n"
            f"ended: {ended}\n"
            "machine: devbox\n"
            "---\n\n### What happened\n- work\n",
            encoding="utf-8")
        return sid

    def _at(self, epoch_offset):
        """An ISO stamp `epoch_offset` seconds from now, in the configured tz."""
        return session._coord_iso(time.time() + epoch_offset)

    def _as_journal(self, journal):
        """Run the rest of this test as the session whose journal id is `journal`."""
        p = unittest.mock.patch.object(
            session, "_coord_holder", lambda: (journal, "claude-code", "journal"))
        p.start()
        self.addCleanup(p.stop)

    def _write(self, kind, name, rec):
        d = session._coord_dir(kind, create=True)
        (d / f"{name}.json").write_text(json.dumps(rec) + "\n", encoding="utf-8")
        return d / f"{name}.json"

    def _record(self, session_id, journal, created_offset, ttl=3600, **extra):
        now = time.time()
        rec = {"session_id": session_id, "journal": journal, "branch": self.identity,
               "machine": "devbox", "created_at": self._at(created_offset),
               "expires_at": now + ttl, "ttl_seconds": ttl}
        rec.update(extra)
        return rec

    def _names(self, kind):
        return set(session._coord_list(kind, include_expired=True))


class ReusedLaneIsNotTheSameSessionTest(LaneExitBase):
    """THE HAZARD THE CONVERGENCE CREATES, once per kind that can carry it.

    Every one of these is the same story: session OLD finished in poga-1 at 02:00; session
    NEW opened in poga-1 at 05:00 and is working; OLD's cleanup runs late — a retry, a
    teardown hook that fires minutes after the fact, a reaper pass. Both sessions have the
    identical `_coord_identity()`, because a lane branch is a location. Nothing OLD does
    may touch NEW's records.

    Kinds split because they are matched by three different arms of `_lane_exit_owns` — a
    claim by the ownership key, the reservation and the attention record by their NAME —
    and a guard that covered only the arm someone happened to think of is the shape of
    every leak in this file's history.
    """

    def setUp(self):
        super().setUp()
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))   # closed 3h ago
        self._journal(NEW, self._at(-3600))                            # still open
        self._as_journal(OLD)
        self.epoch = session._lane_exit_epoch((OLD,))
        self.assertIsNotNone(self.epoch, "precondition: OLD's close is datable")

    def test_a_new_sessions_claim_in_the_reused_lane_is_left_alone(self):
        self._write("claims", "WI-0001",
                    self._record(self.identity, OLD, -12000))       # OLD's own
        self._write("claims", "WI-0002",
                    self._record(self.identity, NEW, -1800))        # NEW's, same lane

        freed = session._lane_exit_release(self.identity, journals=(OLD,),
                                           epoch=self.epoch)

        self.assertEqual([n for _k, n in freed], ["WI-0001"])
        self.assertEqual(self._names("claims"), {"WI-0002"},
                         "a late cleanup from the previous session released the claim of "
                         "the session working in that lane now")

    def test_a_new_sessions_lane_reservation_is_left_alone(self):
        """The reservation is the sharpest form: it is matched by NAME — the lane — because
        the launcher writes it before any journal exists, so the name is the ONLY thing
        either session's record has to distinguish it, and they are identical."""
        self._write(session.LANE_ALLOC_KIND, "poga-1",
                    self._record("app", "", -1800, dispatch_id="D-newnew"))

        freed = session._lane_exit_release(self.identity,
                                           kinds=(session.LANE_ALLOC_KIND,),
                                           journals=(OLD,), epoch=self.epoch)

        self.assertEqual(freed, [])
        self.assertEqual(self._names(session.LANE_ALLOC_KIND), {"poga-1"},
                         "a live lane was dropped out of the dispatch cap it is occupying")

    def test_its_own_lane_reservation_is_still_released(self):
        """The other direction, in the same test class on purpose: a guard that never
        releases is not a guard, it is the leak WI-0078 filed."""
        self._write(session.LANE_ALLOC_KIND, "poga-1",
                    self._record("app", "", -14500))         # drawn just before OLD began

        freed = session._lane_exit_release(self.identity,
                                           kinds=(session.LANE_ALLOC_KIND,),
                                           journals=(OLD,), epoch=self.epoch)

        self.assertEqual([n for _k, n in freed], ["poga-1"])
        self.assertEqual(self._names(session.LANE_ALLOC_KIND), set())

    def test_a_new_sessions_attention_record_is_left_alone(self):
        """`attention` is named for the coordination identity and keyed by the CLAUDE
        session id, so it matches the ownership key only by accident of which door wrote
        it. It is released by name, and the name is the recycled address."""
        key = session._attention_key(self.identity)
        self._write(session.ATTENTION_KIND, key,
                    self._record(f"csid-{NEW}", NEW, -600, message="waiting on you"))

        freed = session._lane_exit_release(self.identity,
                                           kinds=(session.ATTENTION_KIND,),
                                           journals=(OLD,), epoch=self.epoch)

        self.assertEqual(freed, [])
        self.assertEqual(self._names(session.ATTENTION_KIND), {key},
                         "a live lane's 'waiting on you' was cleared by a dead session, so "
                         "the question it names never reaches anyone")

    def test_a_new_sessions_dispatch_slot_is_left_alone(self):
        """The slot is what the dispatch cap counts. Freeing a live lane's slot does not
        just lose a record — it tells the next wave there is room where there is not."""
        self._write(session.DISPATCH_SPAWN_KIND, "D-newnew__WI-0002",
                    self._record(self.identity, NEW, -1800))

        freed = session._lane_exit_release(self.identity, journals=(OLD,),
                                           epoch=self.epoch)

        self.assertEqual(freed, [])
        self.assertEqual(self._names(session.DISPATCH_SPAWN_KIND), {"D-newnew__WI-0002"})

    def test_the_journal_arm_is_not_epoch_guarded(self):
        """A record naming OUR journal is ours whenever it was written — a journal id is
        one session, so there is no ambiguity for the epoch to resolve. Pinned because the
        naive fix (guard everything by time) would silently strand every record a session
        wrote after its own close stamp — which is exactly when a land writes them."""
        self._write("claims", "WI-0003", self._record("main", OLD, +60))

        freed = session._lane_exit_release(self.identity, journals=(OLD,),
                                           epoch=self.epoch)

        self.assertEqual([n for _k, n in freed], ["WI-0003"])


class TheEpochIsTheEndOfOccupancyTest(LaneExitBase):
    """`_lane_exit_epoch` reads `ended`, and reads it from the right session."""

    def test_an_open_session_has_no_epoch(self):
        """None disables the guard, and that is correct rather than a gap: a session that
        has not closed has no successor in its lane to protect."""
        self._journal(OLD, self._at(-14400))
        self.assertIsNone(session._lane_exit_epoch((OLD,)))

    def test_the_epoch_comes_from_the_named_journals_not_from_this_process(self):
        """The teardown case. `cmd_worktree_remove` runs in the MAIN checkout's own
        session, so `_find_holder_journal` there answers about the wrong session entirely —
        and a teardown carrying the hook's clock would judge every record the lane wrote to
        be newer than its own occupancy and free nothing."""
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        self._journal(NEW, self._at(-3600))
        self._as_journal(NEW)                      # this process is the OTHER session
        epoch = session._lane_exit_epoch((OLD,))
        self.assertIsNotNone(epoch)
        self.assertLess(epoch, time.time() - 10000,
                        "the epoch was read from the running process rather than from the "
                        "journals the exit was asked about")

    def test_with_no_journals_it_falls_back_to_this_sessions_own(self):
        """The close's own call passes no journals — `_lane_exit_report` asks the question
        of whoever is running. `_find_holder_journal` answers it by the Claude session key,
        so that is what the fixture supplies rather than the `_coord_holder` patch the
        other tests use (which answers a different question: what a NEW record is stamped
        with)."""
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        with unittest.mock.patch.dict(os.environ,
                                      {"CLAUDE_CODE_SESSION_ID": f"csid-{OLD}"}):
            self.assertIsNotNone(session._lane_exit_epoch())


class TheExitIsIdempotentAndRetryableTest(LaneExitBase):
    """Both are acceptance terms on the item, and both are about a cleanup that runs more
    than once — which, once every route calls one exit, is the ordinary case rather than
    the exceptional one."""

    def setUp(self):
        super().setUp()
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        self._as_journal(OLD)
        for wid in ("WI-0001", "WI-0002", "WI-0003"):
            self._write("claims", wid, self._record(self.identity, OLD, -12000))

    def test_running_it_twice_frees_nothing_the_second_time(self):
        first = session._lane_exit_release(self.identity, journals=(OLD,))
        second = session._lane_exit_release(self.identity, journals=(OLD,))
        self.assertEqual(len(first), 3)
        self.assertEqual(second, [], "a repeat reported releases it did not make — the "
                                     "close, the teardown and the reaper all run this "
                                     "over the same lane")
        self.assertEqual(self._names("claims"), set())

    def test_one_records_failure_does_not_abandon_the_rest(self):
        """The try used to sit around the LOOP, so a single unreadable record left every
        record after it held and made a partial failure indistinguishable from a total
        one — with nothing for a retry to make progress on."""
        real = session._coord_release

        def boom(kind, name, *a, **k):
            if name == "WI-0002":
                raise OSError("disk gone")
            return real(kind, name, *a, **k)

        with unittest.mock.patch.object(session, "_coord_release", boom):
            freed = session._lane_exit_release(self.identity, journals=(OLD,))

        self.assertEqual({n for _k, n in freed}, {"WI-0001", "WI-0003"})
        self.assertEqual(self._names("claims"), {"WI-0002"})

    def test_the_retry_after_a_partial_failure_finishes_the_job(self):
        real = session._coord_release

        def boom(kind, name, *a, **k):
            if name == "WI-0002":
                raise OSError("disk gone")
            return real(kind, name, *a, **k)

        with unittest.mock.patch.object(session, "_coord_release", boom):
            session._lane_exit_release(self.identity, journals=(OLD,))
        ok, left = session._lane_exit_verify(self.identity, journals=(OLD,))

        self.assertTrue(ok, f"the retry left records behind: {left}")
        self.assertEqual(self._names("claims"), set())

    def test_verify_names_the_survivors_rather_than_reporting_a_clean_exit(self):
        with unittest.mock.patch.object(session, "_coord_release",
                                        lambda *a, **k: False):
            ok, left = session._lane_exit_verify(self.identity, journals=(OLD,))
        self.assertFalse(ok)
        self.assertEqual({n for _k, n in left}, {"WI-0001", "WI-0002", "WI-0003"})


class CleanupTouchesHoldsAndNothingElseTest(LaneExitBase):
    """A cleanup that could write the record of the close could fabricate one.

    The item's ruling is explicit about the two failures worth guarding: cleanup must not
    reopen the journal, and it must not manufacture a delivery result. Both reduce to one
    testable property — the exit writes coordination records and nothing else — so that is
    what is pinned, in bytes rather than in intent."""

    def test_the_closed_journal_is_untouched_by_the_exit(self):
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        self._as_journal(OLD)
        self._write("claims", "WI-0001", self._record(self.identity, OLD, -12000))
        jpath = self.journals / f"{OLD}.md"
        before = jpath.read_bytes()

        session._lane_exit_release(self.identity, journals=(OLD,))

        self.assertEqual(jpath.read_bytes(), before,
                         "the lane exit rewrote the journal — a cleanup with a path to the "
                         "close receipt can fabricate one")

    def test_a_session_closed_with_the_land_unfinished_is_not_reopened(self):
        """The interrupted case named on the item: a session may be closed with delivery
        pending. Cleanup runs over it and must leave that state exactly as it found it —
        neither reopening the journal nor stamping it as delivered."""
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        self._as_journal(OLD)
        session._lane_exit_release(self.identity, journals=(OLD,))
        fm, _body = session.parse_journal(
            (self.journals / f"{OLD}.md").read_text(encoding="utf-8"))
        self.assertTrue(fm.get("ended"), "cleanup reopened a closed journal")


class TheReaperUsesTheOneExitTest(LaneExitBase):
    """The reap route, which is where the ADDRESS arm earns its keep.

    A close knows its own journal, so arm 1 answers nearly everything it asks. The reaper
    knows only a lane branch whose worktree is already gone — there is no journal to read
    and no session to ask — so a record keyed by its address (`lane-alloc` by the lane,
    `attention` by the identity) is reachable through that arm or through nothing at all.
    Before this it was through nothing: the sweep matched on the ownership key, and neither
    of those records carries one that names the lane."""

    def setUp(self):
        super().setUp()
        # A SECOND lane, torn down: worktree removed and branch deleted, which is exactly
        # the proof `_coord_holder_is_dead` reads (`refs/heads/<identity>` gone).
        self.dead = "worktree-poga-9"
        self._write("claims", "WI-0009", self._record(self.dead, "", -12000))
        self._write(session.LANE_ALLOC_KIND, "poga-9", self._record("app", "", -14000))
        self._write(session.ATTENTION_KIND, session._attention_key(self.dead),
                    self._record("csid-gone", "", -13000, message="waiting on you"))

    def test_a_dead_lanes_records_go_together_through_the_one_exit(self):
        freed = session._coord_release_dead_lanes()
        self.assertEqual({k for k, _n in freed},
                         {"claims", session.LANE_ALLOC_KIND, session.ATTENTION_KIND},
                         f"the sweep left a kind behind: {freed}")
        self.assertEqual(self._names("claims"), set())
        self.assertEqual(self._names(session.LANE_ALLOC_KIND), set())
        self.assertEqual(self._names(session.ATTENTION_KIND), set())

    def test_a_live_lanes_records_are_left_alone(self):
        """`worktree-poga-1` exists in this fixture, so the death proof fails and the
        sweep must not touch it — including its reservation and its attention record,
        which the address arm now makes REACHABLE and therefore losable."""
        self._write("claims", "WI-0001", self._record(self.identity, "", -12000))
        self._write(session.LANE_ALLOC_KIND, "poga-1", self._record("app", "", -14000))
        self._write(session.ATTENTION_KIND, session._attention_key(self.identity),
                    self._record("csid-live", "", -600, message="waiting on you"))

        session._coord_release_dead_lanes()

        self.assertIn("WI-0001", self._names("claims"))
        self.assertIn("poga-1", self._names(session.LANE_ALLOC_KIND))
        self.assertIn(session._attention_key(self.identity),
                      self._names(session.ATTENTION_KIND))


class TheCloseCallsTheExitForItselfTest(LaneExitBase):
    """`_lane_exit_report` — the close's one call into the exit, and the only place that
    assembles the three arguments it needs from the running session."""

    def setUp(self):
        super().setUp()
        self._journal(OLD, self._at(-14400), ended=self._at(-10800))
        self._as_journal(OLD)
        self.env = unittest.mock.patch.dict(
            os.environ, {"CLAUDE_CODE_SESSION_ID": f"csid-{OLD}"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_a_clean_exit_says_nothing(self):
        """Silence is the report for the ordinary case. A line per close that only ever
        says 'it worked' is a line nobody reads by the third one."""
        self._write("claims", "WI-0001", self._record(self.identity, OLD, -12000))
        self.assertEqual(session._lane_exit_report("closed"), [])
        self.assertEqual(self._names("claims"), set())

    def test_it_finds_this_sessions_front_door_records_by_journal(self):
        """The WI-0123 case, reached through the close rather than through a hand-built
        call: a record taken through the main-anchored front door carries `branch: main`
        and main's ownership key, so the journal is the only thing that finds it."""
        self._write("claims", "WI-0002", self._record("main", OLD, -12000, branch="main"))
        self.assertEqual(session._lane_exit_report("closed"), [])
        self.assertEqual(self._names("claims"), set())

    def test_it_names_what_survived_rather_than_reporting_a_clean_close(self):
        self._write("claims", "WI-0003", self._record(self.identity, OLD, -12000))
        with unittest.mock.patch.object(session, "_coord_release",
                                        lambda *a, **k: False):
            lines = session._lane_exit_report("closed")
        self.assertTrue(lines)
        self.assertIn("claims/WI-0003", lines[0])
        self.assertIn("INCOMPLETE", lines[0])

    def test_it_does_not_free_the_slot_the_land_just_drew_for_the_next_item(self):
        """THE SEQUENCE, not a hedge. `_dispatch_after_land` runs at the end of a
        successful land and draws a spawn slot for the next queued item — keyed by THIS
        lane's identity and journal, because this lane is the one spawning. The close's
        exit runs seconds later. Releasing it would hand the spawn right for a lane that is
        starting up to whoever asks next, which is the duplicate-lane failure the slot is
        drawn rather than computed to prevent."""
        self._write(session.DISPATCH_SPAWN_KIND, "D-abcdef__WI-0777",
                    self._record(self.identity, OLD, +1, spawned_by=self.identity))
        self._write("claims", "WI-0005", self._record(self.identity, OLD, -12000))

        self.assertEqual(session._lane_exit_report("closed"), [])

        self.assertEqual(self._names("claims"), set(), "the close freed nothing at all")
        self.assertEqual(self._names(session.DISPATCH_SPAWN_KIND), {"D-abcdef__WI-0777"},
                         "the close released the spawn right for the lane it had just "
                         "opened")

    def test_teardown_still_frees_the_slot(self):
        """The exception is scoped to the CLOSE. By the time a lane's branch is gone,
        whatever it spawned has its own records and its own claim, so the full set is
        right there — and the slot going unfreed is the leak WI-0298 named."""
        self._write(session.DISPATCH_SPAWN_KIND, "D-abcdef__WI-0777",
                    self._record(self.identity, OLD, -1000))

        freed = session._lane_exit_release(self.identity, journals=(OLD,),
                                           reason="teardown")

        self.assertEqual([k for k, _n in freed], [session.DISPATCH_SPAWN_KIND])

    def test_it_refuses_to_touch_a_half_patched_fixture(self):
        """WI-0035, for the reason `_release_lane_reservation` spells out: a test that
        moves `JOURNAL_DIR` but leaves `ROOT` on the live checkout would resolve the real
        machine's lane and free its live records."""
        self._write("claims", "WI-0004", self._record(self.identity, OLD, -12000))
        with unittest.mock.patch.object(session, "_running_against_fixture",
                                        return_value=True):
            self.assertEqual(session._lane_exit_report("closed"), [])
        self.assertEqual(self._names("claims"), {"WI-0004"})


class TheDocstringIsTrueTest(unittest.TestCase):
    """THE ACCEPTANCE, as ruled: *a grep that finds a lane release outside the one function
    is the failing case*.

    A structural check rather than a behavioural one, deliberately. The defect this item
    names is not that any single route misbehaves — each of the three worked — it is that
    there were three of them, so they could drift apart with nothing failing until a lane
    happened to leave by the one nobody exercised. Only the source can say how many there
    are.

    THE LINE IT DRAWS, because "a lane release" needs a definition a test can apply: a
    release keyed on the HOLDER — *this session is going away, free what it holds* — is a
    lane exit and belongs in `_lane_exit_release`. A release keyed on the RESOURCE — this
    number is spent, this lease's block ended, this acquire failed and is being undone — is
    not, and folding those in would be worse: `_release_landed_numbers` frees exactly the
    numbers whose files reached the trunk and deliberately leaves a drawn number whose file
    did not, which an identity-keyed sweep cannot express.

    So the allowlist below is the resource-keyed set, and it is named one function at a
    time rather than by pattern: adding a function here is a decision about which kind of
    release you are writing, which is exactly the decision this item exists to make
    explicit.
    """

    #: Functions permitted to call `_coord_release` directly. Everything else must go
    #: through `_lane_exit_release`. Each entry is resource-keyed — see the class docstring.
    RESOURCE_KEYED = {
        "_lane_exit_release",          # the one exit itself
        "_lease_release",              # a named lease, released by its own block/verb
        "_release_landed_numbers",     # the numbers this land carried, by git diff
        "_release_committed_numbers",  # the numbers this store commit carried
        "_counter_land_gate",          # a dead holder's number, freed inline at the gate
        "_store_renumber_one",         # a number and claim being renumbered, not exited
        "_adr_renumber_one",           # an ADR number being renumbered, not exited
        "_dispatch_try_spawn",         # undo of an acquire this function just made
        "_dispatch_item_sweep",        # a guard whose item is now claimed
        "_drill_items_clear",          # `poga drill --clean` scaffolding
        "cmd_release",                 # the operator's own `session.py release` verb
        "land_gate_lock",              # the gate + ticket, at the end of their own block
        "trunk_lock",                  # the trunk lock, at the end of its own block
    }

    def test_no_second_lane_release_implementation(self):
        import ast
        root = pathlib.Path(__file__).resolve().parent.parent / "sessionlib"
        found: dict[str, set[str]] = {}
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            stack: list[str] = []

            def walk(node, stack=stack, mod=path.name):
                named = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                if named:
                    stack.append(node.name)
                if isinstance(node, ast.Call) and \
                        getattr(node.func, "id", "") == "_coord_release":
                    # The OUTERMOST enclosing def, so a closure is attributed to the
                    # function that owns it rather than to its own private name.
                    found.setdefault(stack[0] if stack else "<module>",
                                     set()).add(f"{mod}:{node.lineno}")
                for child in ast.iter_child_nodes(node):
                    walk(child)
                if named:
                    stack.pop()

            walk(tree)

        extra = {fn: sorted(where) for fn, where in found.items()
                 if fn not in self.RESOURCE_KEYED}
        self.assertEqual(extra, {},
                         "these functions release a coordination record without going "
                         "through `_lane_exit_release`. If the release is keyed on the "
                         "HOLDER it is a lane exit and belongs in the one exit; if it is "
                         "keyed on the RESOURCE, add it to RESOURCE_KEYED with the reason "
                         "— but say which, because a fourth quiet exit path is the defect "
                         "WI-0312 closed.")

    def test_every_route_that_ends_a_lane_calls_the_one_exit(self):
        """The other half: the allowlist proves nothing ELSE releases, and this proves the
        routes that should, do. Named by source rather than driven end-to-end because each
        route's own behaviour is covered where it lives; what has never had a test is that
        all of them arrive at the same function."""
        root = pathlib.Path(__file__).resolve().parent.parent / "sessionlib"
        src = {p.name: p.read_text(encoding="utf-8") for p in root.glob("*.py")}
        routes = {
            "close":     ("hooks.py", "_lane_exit_report("),
            # WI-0354. `merge --continue` lands without closing, so it never reached the
            # close's exit — a lane blocked at the gate lands through it and nothing else.
            "continue":  ("lanes.py", "_lane_exit_report("),
            "reservation": ("coord.py", "_lane_exit_release(identity, kinds=(LANE_ALLOC_KIND,)"),
            "attention": ("store.py", "_lane_exit_release(ident, kinds=(ATTENTION_KIND,)"),
            "reap":      ("coord.py", "_lane_exit_release(ident, kinds=kinds"),
            "teardown":  ("lanes.py", "_lane_exit_verify(branch,"),
            "recovery":  ("lanes.py", '_lane_exit_verify(l["branch"]'),
            "rollback":  ("lanes.py", '_lane_exit_release(f"worktree-{lane}"'),
        }
        for route, (mod, needle) in routes.items():
            with self.subTest(route=route):
                self.assertIn(needle, src[mod],
                              f"the {route} route no longer reaches the one lane exit")


if __name__ == "__main__":                                     # pragma: no cover
    unittest.main()
