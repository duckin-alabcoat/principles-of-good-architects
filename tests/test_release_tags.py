"""WI-0226: real release publication against a bare origin."""
import argparse
import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling fixtures
import session
import harness_fixture  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import point_store_at  # noqa: E402


class ReleaseTagsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "repo"
        self.remote = Path(self.tmp.name) / "origin.git"
        self.root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        subprocess.run(["git", "init", "-b", "main", "--bare", str(self.remote)], check=True,
                       capture_output=True)
        self.git("remote", "add", "origin", str(self.remote))
        for name, value in (("ROOT", self.root), ("CFG", dict(session.CFG, architect_id="fixture-arch"))):
            patch = mock.patch.object(session, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        # ADR-0148 D3 / WI-0427: the cut's commit resolves the holder journal through
        # `JOURNAL_DIR`, `_shared_work_root()` and `POGA_INVOKED_FROM`, none of which
        # follow `ROOT` — so `cut` globbed the real checkout's `sessions/journal`.
        point_store_at(self, self.root)
        self.item = {"id": "WI-0001", "title": "Feature", "status": "done",
                     "section": "backlog", "impact": "feature", "version": ""}
        session._wi_write_item(self.item)
        self.records = session._rel_dir()
        self.records.mkdir(parents=True)
        (self.records / "1.0.0.md").write_text("- version: 1.0.0\n")
        self.git("add", ".")
        self.git("commit", "-m", "seed")
        self.git("push", "-u", "origin", "main")
        # Exercise real commit behaviour; all paths and origin belong to this fixture.
        patch = mock.patch.object(session, "_under_test", return_value=False)
        patch.start()
        self.addCleanup(patch.stop)

    def git(self, *args, check=True):
        p = subprocess.run(["git", "-C", str(self.root), *args], text=True,
                           capture_output=True, check=check)
        return p.stdout.strip()

    def cut(self, **kw):
        args = argparse.Namespace(dry_run=False, override_bump=None, reason="", no_tag=False)
        args.__dict__.update(kw)
        session.cmd_release_cut(args)

    def refusal(self, reason):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            with self.assertRaises(SystemExit) as raised:
                self.cut()
        self.assertNotEqual(raised.exception.code, 0)
        self.assertIn(reason, out.getvalue())
        self.assertEqual(session._rel_harvest()[0].get("version", ""), "")

    def test_publishes_annotated_tag_on_record_commit(self):
        self.cut()
        tag = self.git("rev-parse", "v1.1.0^{}")
        self.assertEqual(self.git("cat-file", "-t", "v1.1.0"), "tag")
        self.assertIn("1.1.0", self.git("show", tag + ":releases/fixture/1.1.0.md"))
        self.assertIn(tag, self.git("ls-remote", "origin", "refs/tags/v1.1.0^{}"))
        self.assertIn(tag, self.git("ls-remote", "origin", "refs/heads/main"))

    def test_unpushed_main_refuses_before_writing_record(self):
        (self.root / "new").write_text("unpublished")
        self.git("add", "new")
        self.git("commit", "-m", "unpublished")
        self.refusal("main is not pushed")
        self.assertFalse((self.records / "1.1.0.md").exists())

    def test_existing_local_tag_refuses(self):
        self.git("tag", "v1.1.0")
        self.refusal("tag v1.1.0 already exists")
        self.assertFalse((self.records / "1.1.0.md").exists())

    def test_existing_remote_tag_refuses_even_without_local_tag(self):
        self.git("push", "origin", "HEAD:refs/tags/v1.1.0")
        self.refusal("tag v1.1.0 already exists")

    def test_remote_rejection_does_not_publish_record_or_stamp_items(self):
        old = self.git("rev-parse", "HEAD")
        hook = self.remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.refusal("origin rejected")
        self.assertIn(old, self.git("ls-remote", "origin", "refs/heads/main"))
        self.assertEqual(self.git("ls-remote", "origin", "refs/tags/v1.1.0"), "")

    def test_no_tag_preserves_record_only_without_pushed_main(self):
        self.git("remote", "remove", "origin")
        self.cut(no_tag=True)
        self.assertTrue((self.records / "1.1.0.md").exists())
        self.assertEqual(self.git("tag", "--list"), "")

    def test_lane_cannot_tag_unlanded_work(self):
        self.git("checkout", "-b", "lane")
        self.refusal("run on landed main")

    def test_dry_run_does_not_write_or_tag(self):
        self.cut(dry_run=True)
        self.assertFalse((self.records / "1.1.0.md").exists())
        self.assertEqual(self.git("tag", "--list"), "")


# ── WI-0402: the cut has a front door that works from a lane ──────────────────────

REPO = Path(__file__).resolve().parent.parent
LANE_CFG = {
    "architect_name": "Architect", "architect_id": "member-arch", "user_name": "operator",
    "role_doc": "role.md", "handoff": "handoff.md", "timezone": "UTC",
    "machine_map": {"anything": "TestBox"},
}


class ACutFromALaneReachesTheMainCheckoutTest(unittest.TestCase):
    """WI-0402 — the acceptance, run end to end against a REAL linked worktree.

    Nothing here simulates a lane. `test_lane_cannot_tag_unlanded_work` above checks out
    a branch in the one tree, which is the *other* failure this verb has to refuse; the
    subject here is a second working tree, and the whole class of defect is a property of
    how git populates and isolates one. A mock of that would be a mock of the subject.

    THE TWO HALVES, and the second is what makes the first mean something:

      * the FRONT DOOR — `poga release cut`, typed from inside the lane — reaches the
        main checkout, tags MAIN's HEAD, writes the record into MAIN and stamps MAIN's
        store. The lane is left untouched.
      * the BACK DOOR — the lane's own `session.py release-cut`, the invocation `poga`
        never sees — refuses, names the front door, and writes nothing anywhere.

    And the half that keeps the door honest: an anchored door is not a looser gate. The
    same cut, through the same door, still refuses a dirty trunk and an unpushed one. The
    item's own warning is the reason that assertion is here rather than assumed — a door
    that is easier to reach is also easier to reach by accident, and the cut pushes a tag.
    """

    def setUp(self):
        neutralize_ambient_env(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.main = base / "member"
        self.remote = base / "origin.git"
        harness_fixture.install_harness(self.main)
        # The REAL launcher, copied rather than invoked in place: `poga` resolves the repo
        # it drives from git and its own federation-only verbs from `$0`, so a copy inside
        # the fixture is the member's own door — which is what a member has.
        shutil.copyfile(REPO / "poga", self.main / "poga")
        (self.main / "role.md").write_text("role\n", encoding="utf-8")
        (self.main / "session.config.json").write_text(json.dumps(LANE_CFG, indent=2),
                                                       encoding="utf-8")
        (self.main / "work-items").mkdir()
        self.item = {"id": "WI-0001", "title": "Feature", "status": "done",
                     "section": "backlog", "impact": "feature", "version": ""}
        # BOTH globals, and the second is not decoration: `_rel_dir()` is
        # `ROOT/releases/<project>` and the project is DERIVED from `architect_id`
        # (ADR-0006). Patching ROOT alone seeds the record under this repo's own project
        # name while the subprocess — which reads the fixture's `session.config.json` —
        # looks under the fixture's, and the cut then refuses for having no prior record.
        with mock.patch.object(session, "ROOT", self.main), \
                mock.patch.object(session, "CFG",
                                  dict(session.CFG, architect_id=LANE_CFG["architect_id"])):
            session._wi_write_item(self.item, commit=False)
            self.records = session._rel_dir()
        self.records.mkdir(parents=True)
        (self.records / "1.0.0.md").write_text("- version: 1.0.0\n", encoding="utf-8")
        self.git(self.main, "init", "-q", "-b", "main")
        for k, v in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                     ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            self.git(self.main, "config", k, v)
        subprocess.run(["git", "init", "-q", "-b", "main", "--bare", str(self.remote)],
                       check=True, capture_output=True)
        self.git(self.main, "remote", "add", "origin", str(self.remote))
        self.git(self.main, "add", "-A")
        self.git(self.main, "commit", "-qm", "seed")
        self.git(self.main, "push", "-q", "-u", "origin", "main")
        # A REAL linked worktree, populated from the index — which is why the harness had
        # to be committed above: a lane with no `session.py` cannot run the back door.
        self.lane = base / "lane"
        self.git(self.main, "worktree", "add", "-q", "-b", "worktree-lane", str(self.lane))
        self.assertTrue((self.lane / "session.py").is_file(), "the lane has no harness")

    # ── plumbing ──────────────────────────────────────────────────────────────────

    def git(self, tree, *args, check=True):
        p = subprocess.run(["git", "-C", str(tree), *args], text=True,
                           capture_output=True, check=check)
        return p.stdout.strip()

    def front_door(self, *args):
        """`poga release …` typed FROM THE LANE — the invocation the item is about."""
        return subprocess.run(["bash", str(self.main / "poga"), "release", *args],
                              text=True, capture_output=True, cwd=str(self.lane))

    def back_door(self, *args):
        """The lane's OWN harness, run from the lane. Both halves matter: the harness
        resolves the tree it drives from its own file location, so running the main
        checkout's `session.py` with `cwd` set to the lane would be a different
        experiment entirely."""
        return subprocess.run([sys.executable, str(self.lane / "session.py"), *args],
                              text=True, capture_output=True, cwd=str(self.lane))

    def lane_records(self):
        return self.lane / "releases" / "member"

    def stamped(self, tree):
        """The `version:` field of the one item, as THAT tree's store has it.

        The field is rendered as a `- version: …` bullet. Matching on `version:` alone
        finds nothing, ever — which does not fail, it passes every "nothing was stamped"
        assertion for free and takes the negative controls down with it."""
        f = next((tree / "work-items").glob("WI-0001-*.md"))
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("- version:"):
                return line.split(":", 1)[1].strip()
        self.fail(f"{f.name} has no version field at all — the store's shape changed")

    def assertNothingShipped(self, why):
        self.assertFalse((self.records / "1.1.0.md").exists(), f"{why}: record in main")
        self.assertFalse((self.lane_records() / "1.1.0.md").exists(),
                         f"{why}: record in the lane")
        self.assertEqual(self.git(self.main, "tag", "--list"), "", f"{why}: a tag exists")
        self.assertEqual(self.git(self.main, "ls-remote", "origin", "refs/tags/v1.1.0"),
                         "", f"{why}: origin carries a tag")
        self.assertEqual(self.stamped(self.main), "", f"{why}: main's item was stamped")
        self.assertEqual(self.stamped(self.lane), "", f"{why}: the lane's item was stamped")

    # ── the front door ────────────────────────────────────────────────────────────

    def test_the_front_door_from_a_lane_tags_the_main_checkout(self):
        """The acceptance in one test: standing in the lane, cut a release, and the tag
        is on MAIN."""
        r = self.front_door("cut")
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertEqual(self.git(self.main, "cat-file", "-t", "v1.1.0"), "tag",
                         "the cut did not create an annotated tag")
        tagged = self.git(self.main, "rev-parse", "v1.1.0^{}")
        # WHICH BRANCH CARRIES THE TAGGED COMMIT is the whole question, and it is asked
        # this way rather than against `HEAD` deliberately: the cut stamps the shipped
        # items AFTER publishing, and every stamp auto-commits, so main's HEAD has moved
        # on by the time this runs. `--contains` is the assertion that does not go stale
        # between the tag and the next commit.
        carriers = self.git(self.main, "branch", "--format=%(refname:short)",
                            "--contains", tagged).split()
        self.assertIn("main", carriers, "the tagged commit is not on the trunk")
        self.assertNotIn("worktree-lane", carriers,
                         "the tagged commit is on the LANE's branch — the cut tagged the "
                         "tree it was typed in rather than the tree it released")
        self.assertIn(tagged, self.git(self.main, "ls-remote", "origin",
                                       "refs/tags/v1.1.0^{}"),
                      "the tag was not published to origin")

    def test_the_front_door_writes_the_record_and_the_stamps_into_main(self):
        """Tagging the right tree is not enough on its own. The record, the commit that
        carries it and the `version:` stamps all have to land in the checkout everything
        else reads — a cut that tagged main while writing its record into the lane would
        pass the test above and still have shipped nothing anybody can find."""
        r = self.front_door("cut")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.records / "1.1.0.md").exists(), "no record in main")
        self.assertFalse((self.lane_records() / "1.1.0.md").exists(),
                         "the record was written into the lane")
        self.assertEqual(self.stamped(self.main), "1.1.0")
        self.assertIn("1.1.0", self.git(self.main, "show",
                                        "v1.1.0^{}:releases/member/1.1.0.md"),
                      "the tagged commit does not contain the record it releases")

    def test_the_readers_are_reachable_through_the_same_door(self):
        """`list` is why the door takes a verb rather than being `poga release-cut`."""
        r = self.front_door("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1.0.0", r.stdout)

    def test_an_unknown_sub_verb_refuses_rather_than_launching_a_lane(self):
        """The WI-0112 shape, which `poga ops` had to be guarded against for the same
        reason: an unrouted token falls through to the launcher and opens a LANE with
        'release' as its prompt — which exits 0 and looks like success."""
        r = self.front_door("frobnicate")
        self.assertEqual(r.returncode, 2, f"stdout={r.stdout!r}")
        self.assertIn("unknown verb", r.stderr)

    # ── the anchored door is not a looser gate ────────────────────────────────────

    def test_a_dirty_trunk_still_refuses_through_the_front_door(self):
        (self.main / "role.md").write_text("edited\n", encoding="utf-8")
        r = self.front_door("cut")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("tracked changes must be landed", r.stdout + r.stderr)
        self.assertNothingShipped("a dirty trunk")

    def test_an_unpushed_trunk_still_refuses_through_the_front_door(self):
        (self.main / "new").write_text("unpublished", encoding="utf-8")
        self.git(self.main, "add", "new")
        self.git(self.main, "commit", "-qm", "unpublished")
        r = self.front_door("cut")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("main is not pushed", r.stdout + r.stderr)
        self.assertNothingShipped("an unpushed trunk")

    def test_an_existing_tag_still_refuses_through_the_front_door(self):
        self.git(self.main, "tag", "v1.1.0")
        r = self.front_door("cut")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exists", r.stdout + r.stderr)
        self.assertFalse((self.records / "1.1.0.md").exists())
        self.assertEqual(self.stamped(self.main), "")

    def test_the_dry_run_through_the_door_writes_nothing(self):
        r = self.front_door("cut", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("would cut 1.1.0", r.stdout)
        self.assertNothingShipped("a dry run")

    # ── the back door ─────────────────────────────────────────────────────────────

    def test_the_lanes_own_harness_refuses_and_writes_nothing(self):
        """A front door only helps if the back door is shut. `session.py` is on disk in
        every lane and its `--help` advertises `release-cut`."""
        r = self.back_door("release-cut")
        self.assertEqual(r.returncode, session.LANE_STORE_REFUSAL_EXIT,
                         f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("poga release cut", r.stderr, "the refusal does not name the door")
        self.assertNothingShipped("the back door")

    def test_the_refusal_carries_the_arguments_it_was_given(self):
        """The line in the refusal is meant to be RUN, not read — a door named without
        the flags the operator typed is a door they have to reconstruct."""
        r = self.back_door("release-cut", "--override-bump", "patch", "--reason", "x y")
        self.assertEqual(r.returncode, session.LANE_STORE_REFUSAL_EXIT, r.stdout)
        self.assertIn("poga release cut --override-bump patch --reason 'x y'", r.stderr)

    def test_the_readers_still_run_in_the_lane(self):
        """The deliberate carve-out. `release-list` writes nothing and strands nothing;
        refusing it would leave a lane unable to so much as look at the release history,
        which is the shape of a guard that fires on correct code and gets deleted."""
        r = self.back_door("release-list")
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("1.0.0", r.stdout)

    def test_the_main_checkouts_own_harness_is_not_refused(self):
        """The negative control that makes every refusal above mean something: without
        it this class would pass just as happily against a harness that had stopped
        cutting releases at all."""
        r = subprocess.run([sys.executable, str(self.main / "session.py"),
                            "release-cut", "--dry-run"],
                           text=True, capture_output=True, cwd=str(self.main))
        self.assertEqual(r.returncode, 0, f"stdout={r.stdout!r} stderr={r.stderr!r}")
        self.assertIn("would cut 1.1.0", r.stdout)


class TheAnchoredReleaseVerbsAreTheWritingOnesTest(unittest.TestCase):
    """WI-0402 — which verbs the guard covers, and that the door it names exists.

    A refusal that names a command the launcher does not serve is worse than no refusal:
    it reads as a working instruction and fails at the moment someone is already blocked.
    `poga`'s own `_rule_verb_exists` lint checks the first token of a `poga <verb>` string
    emitted by the substrate; nothing checks the SUB-verb, so it is checked here.
    """

    def _cmd_release(self):
        src = (REPO / "poga").read_text(encoding="utf-8")
        m = re.search(r"^cmd_release\(\) \{\n.*?^\}", src, re.M | re.S)
        self.assertIsNotNone(m, "poga has no cmd_release front door")
        return m.group(0)

    def test_every_anchored_verb_is_served_by_the_door_the_refusal_names(self):
        arms = re.search(r"^    ([a-z|]+)\)\s*\n\s*harness_exec", self._cmd_release(),
                         re.M)
        self.assertIsNotNone(arms, "cmd_release routes nothing to the harness")
        served = arms.group(1).split("|")
        for verb in sorted(session.LANE_ANCHORED_RELEASE_VERBS):
            leaf = verb[len("release-"):]
            self.assertIn(leaf, served,
                          f"{verb} is refused from a lane and `poga release {leaf}` is "
                          f"not a door poga serves — the refusal names a dead end")

    def test_the_writing_verbs_are_anchored(self):
        """Both write into `releases/`, which in a lane is a tracked copy frozen at the
        lane's base commit. Named outright as well as covered by the set, because the set
        is a literal and a literal can be edited without anything noticing."""
        self.assertEqual(sorted(session.LANE_ANCHORED_RELEASE_VERBS),
                         ["release-cut", "release-declare"])

    def test_the_readers_are_deliberately_not_anchored(self):
        """Pinned so a later consistency pass turns this red instead of quietly folding
        them in — the same asymmetry `wi-stale` and `wi-renumber` carry in the store."""
        for verb in ("release-list", "release-candidates"):
            self.assertNotIn(verb, session.LANE_ANCHORED_RELEASE_VERBS, verb)

    def test_the_door_is_a_poga_verb(self):
        src = (REPO / "poga").read_text(encoding="utf-8")
        self.assertRegex(src, r"(?m)^    release\)\s+shift; cmd_release ",
                         "the dispatch arm is missing or not at the 4-space indent "
                         "poga's own verb-table reader requires")
        self.assertIn("poga release [list|candidates|declare|cut]", src,
                      "the verb is not in poga's --help")
