"""Tests for the resident-runtime lane path.

The shape under test: a repo whose ROOT belongs to another long-lived agent,
whose Architect launches from a pad OUTSIDE the repo, and which therefore cannot use
the native `claude --worktree` lane (it lands at the worktree root, i.e. on the
runtime). `session.py lane-pad` generates a per-lane pad instead.

What must hold, and why each one bit:
  - the pad is OUTSIDE the repo (a CLAUDE.md inside the runtime's
    tree gets auto-loaded and the runtime absorbs the Architect doc);
  - the pad's hooks reach THAT LANE's harness, not the main checkout's (session.py
    resolves its repo from its own file location, so the main checkout's copy would
    write every lane's journal and commits into the main checkout);
  - settings.local.json is a symlink to the operator's real file (D10 — approve once,
    everywhere), never a copy;
  - teardown removes the pad, and refuses to remove the operator's own;
  - an ordinary member is entirely unaffected.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import harness_fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def run_harness(repo, *args):
    """Invoke the COPY of session.py inside `repo` — the path that matters, since the
    harness resolves the repo it drives from its own file location."""
    return subprocess.run([sys.executable, str(repo / "session.py"), *args],
                          capture_output=True, text=True, check=False)


HOOK_CMD = 'python3 "$CLAUDE_PROJECT_DIR/../runtime/session.py" start'


class ResidentRuntimeLaneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self.tmp.name)
        # <system>/runtime  = the repo (root belongs to the runtime persona)
        # <system>/pad      = the Architect's launch pad, OUTSIDE the repo
        self.system = base / "system"
        self.repo = self.system / "runtime"
        (self.repo / "architect" / ".claude").mkdir(parents=True)
        self.pad = self.system / "pad"
        (self.pad / ".claude").mkdir(parents=True)

        (self.repo / "CLAUDE.md").write_text("I am the runtime persona.\n")
        (self.repo / "architect" / "role.md").write_text("I am the Architect.\n")
        (self.repo / "architect" / ".claude" / "settings.json").write_text(
            json.dumps({"hooks": {"SessionStart": [
                {"hooks": [{"type": "command", "command": HOOK_CMD}]}]}}, indent=2))
        harness_fixture.install_harness(self.repo)
        (self.repo / "session.config.json").write_text(json.dumps({
            "architect_name": "Architect", "architect_id": "resident-arch",
            "user_name": "operator", "role_doc": "architect/role.md",
            "handoff": "handoff.md", "timezone": "UTC",
            "machine_map": {"anything": "TestBox"},
            "pad_dir": "../pad", "harness_dir": "../runtime",
            "settings_path": "architect/.claude/settings.json",
        }, indent=2))

        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "t@t")
        git(self.repo, "config", "user.name", "t")
        git(self.repo, "config", "commit.gpgsign", "false")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "init")

    def tearDown(self):
        self.tmp.cleanup()

    def _make_lane(self, lane="poga-1"):
        git(self.repo, "worktree", "add", "-b", f"worktree-{lane}",
            f".claude/worktrees/{lane}")
        return self.repo / ".claude" / "worktrees" / lane

    # ---------------------------------------------------------------- build

    def test_pad_is_built_outside_the_repo(self):
        """The ADR-0027 §1 invariant. A pad inside the repo would put a CLAUDE.md in
        the runtime's load path — the leak the whole layout exists to prevent."""
        self._make_lane()
        r = run_harness(self.repo, "lane-pad", "poga-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        pad = pathlib.Path(r.stdout.strip())
        self.assertTrue(pad.is_dir())
        # .resolve() both sides: macOS reports /var as a symlink to /private/var.
        self.assertEqual(pad.resolve(), (self.system / "pad-poga-1").resolve())
        with self.assertRaises(ValueError):
            pad.resolve().relative_to(self.repo.resolve())

    def test_claude_md_points_at_the_lanes_role_doc(self):
        lane = self._make_lane()
        pad = pathlib.Path(run_harness(self.repo, "lane-pad", "poga-1").stdout.strip())
        link = pad / "CLAUDE.md"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), (lane / "architect" / "role.md").resolve())
        self.assertIn("I am the Architect", link.read_text())

    def test_hooks_are_retargeted_at_this_lanes_harness(self):
        """The load-bearing one. session.py resolves its repo from its own location, so
        a pad still pointing at the main checkout would make every lane write its
        journal, handoff and commits into the MAIN checkout — the collision lanes exist
        to prevent."""
        lane = self._make_lane()
        pad = pathlib.Path(run_harness(self.repo, "lane-pad", "poga-1").stdout.strip())
        cmd = json.loads((pad / ".claude" / "settings.json").read_text()
                         )["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        self.assertNotIn('"$CLAUDE_PROJECT_DIR/../runtime/session.py"', cmd)
        target = cmd.split('"')[1].replace("$CLAUDE_PROJECT_DIR", str(pad))
        self.assertEqual(pathlib.Path(target).resolve(), (lane / "session.py").resolve())

    def test_settings_local_is_symlinked_to_the_operators_real_file(self):
        """D10 — approve once, everywhere. A copy would satisfy 'carry my permissions'
        only for approvals that existed when the lane was cut."""
        self._make_lane()
        real = self.pad / ".claude" / "settings.local.json"
        real.write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n')
        pad = pathlib.Path(run_harness(self.repo, "lane-pad", "poga-1").stdout.strip())
        link = pad / ".claude" / "settings.local.json"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), real.resolve())
        # an approval granted "in the lane" is visible to the operator's own pad
        link.write_text('{"permissions": {"allow": ["Bash(ls)", "Bash(pwd)"]}}\n')
        self.assertIn("Bash(pwd)", real.read_text())

    def test_missing_operator_local_file_is_created_not_dangling(self):
        self._make_lane()
        pad = pathlib.Path(run_harness(self.repo, "lane-pad", "poga-1").stdout.strip())
        link = pad / ".claude" / "settings.local.json"
        self.assertTrue(link.is_symlink())
        self.assertTrue(link.resolve().is_file())

    def test_two_lanes_get_independent_pads(self):
        for lane in ("poga-1", "poga-2"):
            self._make_lane(lane)
            run_harness(self.repo, "lane-pad", lane)
        for lane in ("poga-1", "poga-2"):
            cmd = json.loads((self.system / f"pad-{lane}" / ".claude" / "settings.json")
                             .read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"]
            self.assertIn(f"worktrees/{lane}/session.py", cmd)

    # ---------------------------------------------------------------- refuse

    def test_missing_lane_worktree_is_refused_by_name(self):
        r = run_harness(self.repo, "lane-pad", "poga-9")
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not exist", r.stderr)

    def test_pad_relative_settings_declaration_is_refused(self):
        """A `../pad/...` settings path names the MAIN pad. A lane cannot share it —
        its hooks must reach its own harness — so this must fail loudly, not silently
        produce a lane wired to the main checkout."""
        self._make_lane()
        cfg = json.loads((self.repo / "session.config.json").read_text())
        cfg.pop("settings_path")
        cfg["architect_settings_path"] = "../pad/.claude/settings.json"
        (self.repo / "session.config.json").write_text(json.dumps(cfg))
        r = run_harness(self.repo, "lane-pad", "poga-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("IN-REPO", r.stderr)

    def test_unseeded_settings_file_is_refused_by_name(self):
        lane = self._make_lane()
        (lane / "architect" / ".claude" / "settings.json").unlink()
        r = run_harness(self.repo, "lane-pad", "poga-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("seed", r.stderr)

    def test_ordinary_member_is_refused_and_unaffected(self):
        cfg = json.loads((self.repo / "session.config.json").read_text())
        cfg.pop("pad_dir")
        (self.repo / "session.config.json").write_text(json.dumps(cfg))
        r = run_harness(self.repo, "lane-pad", "poga-1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not a resident-runtime member", r.stderr)

    # ---------------------------------------------------------------- teardown

    def test_remove_deletes_the_lane_pad(self):
        self._make_lane()
        run_harness(self.repo, "lane-pad", "poga-1")
        self.assertTrue((self.system / "pad-poga-1").is_dir())
        r = run_harness(self.repo, "lane-pad", "poga-1", "--remove")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.system / "pad-poga-1").exists())

    def test_remove_never_touches_the_operators_own_pad(self):
        """This runs from teardown paths, where a wrong answer deletes the Architect's
        real launch surface (P9)."""
        self._make_lane()
        run_harness(self.repo, "lane-pad", "poga-1")
        run_harness(self.repo, "lane-pad", "poga-1", "--remove")
        self.assertTrue(self.pad.is_dir())
        self.assertTrue((self.pad / ".claude").is_dir())

    def test_remove_is_idempotent(self):
        r = run_harness(self.repo, "lane-pad", "poga-3", "--remove")
        self.assertEqual(r.returncode, 0)
        self.assertIn("nothing to remove", r.stdout)

    def test_reaper_sweeps_a_pad_whose_lane_vanished(self):
        """A pad left behind is not inert — it is a fully-formed Architect launch
        surface aimed at a directory that no longer exists. A worktree can vanish by
        routes neither teardown path sees (a bare `git worktree remove`, `poga lanes
        --clean`, an `rm -rf`), so the sweep keys on ABSENCE OF THE LANE, not on a
        teardown event."""
        self._make_lane()
        run_harness(self.repo, "lane-pad", "poga-1")
        pad = self.system / "pad-poga-1"
        self.assertTrue(pad.is_dir())
        git(self.repo, "worktree", "remove", "--force", ".claude/worktrees/poga-1")
        r = run_harness(self.repo, "reap-lanes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(pad.exists(), r.stdout)

    def test_sweep_leaves_an_unmerged_lanes_pad_alone(self):
        """The sweep must not eat the pad of a lane with work in flight. An UNMERGED
        lane is the one the reaper deliberately keeps (P9 — unlanded work is never
        auto-discarded), so its launch surface has to survive with it."""
        lane = self._make_lane()
        run_harness(self.repo, "lane-pad", "poga-1")
        (lane / "work.txt").write_text("unlanded work\n")
        git(lane, "add", "-A")
        git(lane, "commit", "-qm", "work in flight")
        r = run_harness(self.repo, "reap-lanes")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((lane).is_dir(), "unmerged lane must survive")
        self.assertTrue((self.system / "pad-poga-1").is_dir(), r.stdout)

    def test_sweep_never_eats_the_operators_own_pad(self):
        self._make_lane()
        run_harness(self.repo, "lane-pad", "poga-1")
        git(self.repo, "worktree", "remove", "--force", ".claude/worktrees/poga-1")
        run_harness(self.repo, "reap-lanes")
        self.assertTrue(self.pad.is_dir())
        self.assertTrue((self.pad / ".claude").is_dir())


class PogaPadLaunchTest(unittest.TestCase):
    """`poga` must take the pad path for a resident-runtime member and the native
    `claude --worktree` path for everyone else."""

    def test_poga_carries_the_pad_launch_path(self):
        text = (ROOT / "poga").read_text()
        self.assertIn("is_resident_runtime", text)
        self.assertIn("lane-pad", text)
        # The native worktree path survives for ordinary members. Since ADR-0082 the
        # command and its worktree flag come from the runtime registry rather than being
        # written into the exec, so the literal `exec claude --worktree "$lane"` is gone
        # by design. Two things must hold in its place, and the second is the load-bearing
        # one: the exec still passes a worktree flag and the lane, AND the pre-ADR-0082
        # values are still the FALLBACK defaults — that fallback is precisely what keeps a
        # bare `poga` behaving as it did before, including in a repo whose session.py
        # cannot answer (or is not there at all).
        # WI-0122 moved the fallback defaults out of `cmd_session` and into the shared
        # `resolve_runtime`, so that `session` and `resume` cannot drift apart on which
        # agent a lane runs. The assertion follows them: what is being pinned is that the
        # pre-ADR-0082 values are still the fallback, not where the assignment sits.
        # WI-0105 wrapped the handoff in `exec_or_rollback` so a failed exec can undo
        # the lane it created. The argv being pinned here is unchanged — the worktree
        # flag and the lane still reach the runtime, which is what this test is about.
        self.assertIn('exec_or_rollback "$lane" "$rt_cmd" "$rt_wt" "$lane"', text)
        self.assertIn('POGA_RT_CMD="claude"', text)
        self.assertIn('POGA_RT_WT="--worktree"', text)

    def test_resident_detection_reads_pad_dir(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = pathlib.Path(d) / "session.config.json"
            for value, expected in ((None, 1), ("", 1), ("../pad", 0)):
                body = {} if value is None else {"pad_dir": value}
                cfg.write_text(json.dumps(body))
                rc = subprocess.run(
                    [sys.executable, "-c",
                     "import json,sys;v=json.load(open(sys.argv[1])).get('pad_dir');"
                     "sys.exit(0 if isinstance(v,str) and v.strip() else 1)", str(cfg)]
                ).returncode
                self.assertEqual(rc, expected, f"pad_dir={value!r}")


class LanesDetectorTest(unittest.TestCase):
    """D8 — the lanes capability must witness a REACHABLE launch, not two files.

    Measured 2026-07-27: a resident-runtime member reported `worktree-lanes: present` and status `clean`
    while it could not open a lane at all, and an attempt would have booted the
    runtime persona. Both files were there; neither says where the launch lands."""

    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import standard_check
        self.sc = standard_check

    def _repo(self, tmp, poga_text, harness_text):
        repo = pathlib.Path(tmp)
        (repo / "poga").write_text(poga_text)
        (repo / "session.py").write_text(harness_text)
        (repo / ".claude").mkdir()
        (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {
            "WorktreeRemove": [{"hooks": [{"type": "command",
                                           "command": "python3 x.py worktree-remove"}]}]}}))
        return repo

    def test_ordinary_member_needs_only_launcher_and_teardown(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nclaude --worktree\n", "harness\n")
            self.assertTrue(self.sc.d_worktree_lanes(repo, {}))

    def test_resident_member_without_pad_launch_reads_absent(self):
        """The live resident-runtime case before this build: everything installed, nothing reachable."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nclaude --worktree\n", "harness\n")
            self.assertFalse(self.sc.d_worktree_lanes(repo, {"pad_dir": "../architect"}))

    def test_resident_member_with_pad_launch_reads_present(self):
        # The harness half is the `lane-pad` VERB, registered on the CLI the harness's
        # own `main()` builds (Package 3) — so the subject is the executable synthetic
        # harness, not a line that merely mentions the word.
        from member_fixture import SESSION_PY
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nis_resident_runtime && lane-pad\n",
                              SESSION_PY)
            self.assertTrue(self.sc.d_worktree_lanes(repo, {"pad_dir": "../architect"}))

    def test_a_harness_that_only_mentions_lane_pad_reads_absent(self):
        """The text-marker detector read this present: the word was there, the verb was
        not. A member whose `poga` cds into a pad its harness cannot build is the
        half-state this capability exists to make visible."""
        from member_fixture import SESSION_PY
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nis_resident_runtime && lane-pad\n",
                              "def cmd_lane_pad(): pass  # lane-pad\n")
            self.assertFalse(self.sc.d_worktree_lanes(repo, {"pad_dir": "../architect"}))
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nclaude --worktree\n", SESSION_PY)
            self.assertFalse(self.sc.d_worktree_lanes(repo, {"pad_dir": "../architect"}),
                             "the harness builds pads that this poga never launches into")

    def test_half_installed_resident_member_still_reads_absent(self):
        """poga updated but the harness not (or vice versa) is the half-state the
        capability exists to make visible — it must not round up to present."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nlane-pad\n", "harness with no builder\n")
            self.assertFalse(self.sc.d_worktree_lanes(repo, {"pad_dir": "../architect"}))

    def test_off_repo_settings_path_alone_is_enough_to_detect_the_shape(self):
        """The catch that made this fix real. Keying only on the explicit `pad_dir`
        left the detector INERT for the exact member that motivated it: it declares
        `architect_settings_path: "../architect/..."` and no `pad_dir`, so it kept
        reading `present` while it could not launch a lane. A settings path that
        escapes the repo means the launch cwd is outside the repo, by definition —
        an inference from what the path MEANS, not a guess."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nclaude --worktree\n", "harness\n")
            cfg = {"architect_settings_path": "../architect/.claude/settings.json"}
            self.assertFalse(self.sc.d_worktree_lanes(repo, cfg))

    def test_in_repo_settings_path_is_not_the_resident_shape(self):
        """An in-repo settings path is just a member that keeps settings somewhere
        tidy — it must not be swept up as resident-runtime."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, "#!/bin/bash\nclaude --worktree\n", "harness\n")
            cfg = {"architect_settings_path": ".claude/settings.json"}
            self.assertTrue(self.sc.d_worktree_lanes(repo, cfg))

    def test_real_federation_repo_still_reads_present(self):
        """Regression guard: the federation is an ordinary member and must be
        unaffected by the resident-runtime branch."""
        cfg = json.loads((ROOT / "session.config.json").read_text())
        self.assertTrue(self.sc.d_worktree_lanes(ROOT, cfg))


class PushSubstrateResidentGuardTest(unittest.TestCase):
    """D9 — a resident runtime's ROOT settings are never a push target, whatever the
    config says. The declared non-root path already keeps us away; this is the
    correct-by-construction backstop, because the file is the runtime's injection
    lockdown and a mistyped config would swap it for an Architect baseline silently."""

    def setUp(self):
        # push-substrate.py is hyphenated and imports siblings from curate/, so put
        # that dir on the path before loading it by location (same as
        # tests/test_push_substrate.py).
        sys.path.insert(0, str(ROOT / "curate"))
        spec = importlib.util.spec_from_file_location(
            "push_substrate", str(ROOT / "curate" / "push-substrate.py"))
        self.ps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ps)

    def _repo(self, tmp, cfg):
        repo = pathlib.Path(tmp)
        (repo / "session.config.json").write_text(json.dumps(cfg))
        return repo

    def test_resident_member_declaring_the_root_path_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, {"pad_dir": "../architect",
                                  "settings_path": ".claude/settings.json"})
            self.assertIsNone(self.ps.settings_target(repo))

    def test_resident_member_with_a_proper_non_root_path_is_pushed(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, {"pad_dir": "../architect",
                                  "settings_path": "architect/.claude/settings.json"})
            self.assertEqual(self.ps.settings_target(repo),
                             "architect/.claude/settings.json")

    def test_ordinary_member_still_targets_the_root(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, {"architect_id": "x"})
            self.assertEqual(self.ps.settings_target(repo), ".claude/settings.json")

    def test_explicit_opt_out_still_skips(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, {"pad_dir": "../architect", "settings_path": False})
            self.assertIsNone(self.ps.settings_target(repo))


if __name__ == "__main__":
    unittest.main()
