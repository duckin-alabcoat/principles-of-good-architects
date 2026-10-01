"""Tests for the two member-variable paths the harness used to hardcode.

Both reported by a member (session 10, 2026-07-27) as instances of one shape: `session.py`
is shipped byte-identical to every member, and in these two places it encoded the
FEDERATION's own repo layout as universal. A member on a different layout is
where it breaks first — and in both cases the surface reported success:

  adr_dir       `adr-next` returned 0001 on a repo holding 42 ADRs, and the land gate
                built specifically to prevent that collision filtered its diff to
                `adr/` and certified clean. Two layers, one assumption, so neither
                could catch the other.
  user_profile  the profile glob found nothing, the injected block silently omitted
                it, and the banner asserted `prior-entry + user-profile` regardless
                while STANDARD.md told the Architect not to bother reading it.

The regression that matters most here is the DEFAULT: every member that declares
neither key must behave exactly as before.

stdlib unittest: python3 -m unittest discover -s tests
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import harness_fixture
from ambient_fixture import neutralize_ambient_env

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
    # cwd is the member: `_find_holder_journal` asks the checkout the process stands in,
    # and the suite's own cwd is the operator's checkout (ADR-0148 D3 store guard).
    return subprocess.run([sys.executable, str(repo / "session.py"), *args],
                          capture_output=True, text=True, check=False, cwd=str(repo))


def make_member(tmp, cfg_extra=None, adr_rel="adr"):
    repo = pathlib.Path(tmp) / "member"
    (repo / adr_rel).mkdir(parents=True)
    harness_fixture.install_harness(repo)
    (repo / "role.md").write_text("role\n")
    (repo / "session.config.json").write_text(
        json.dumps({**BASE_CFG, **(cfg_extra or {})}, indent=2))
    for n in ("0001-first.md", "0002-second.md", "0042-latest.md"):
        (repo / adr_rel / n).write_text("# adr\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


class AdrDirTest(unittest.TestCase):
    def setUp(self):
        # The member child inherits this process's environment. `POGA_INVOKED_FROM` and
        # `CLAUDE_CODE_SESSION_ID` send its `_coord_holder` into the RUNNER'S
        # `sessions/journal` when `adr-next` draws a number (ADR-0148 D3 store guard).
        neutralize_ambient_env(self)

    def test_default_layout_is_unchanged(self):
        """Every current member declares no `adr_dir`; none may shift by a digit."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d)
            r = harness(repo, "adr-next")
            self.assertEqual(r.stdout.strip(), "0043", r.stderr)

    def test_declared_directory_is_honoured(self):
        """A non-default shape: ADRs under docs/adr/, which used to yield 0001."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d, {"adr_dir": "docs/adr"}, adr_rel="docs/adr")
            r = harness(repo, "adr-next")
            self.assertEqual(r.stdout.strip(), "0043", r.stderr)

    def test_the_pa_regression_reproduces_without_the_declaration(self):
        """The bug itself, pinned: ADRs at docs/adr/ and NO declaration still collides.
        Kept as a test so the fix cannot be mistaken for 'it works anyway'."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d, {}, adr_rel="docs/adr")
            self.assertEqual(harness(repo, "adr-next").stdout.strip(), "0001")

    def test_basis_is_reported_on_stderr_not_stdout(self):
        """`N=$(session.py adr-next)` must still capture only the number, while a wrong
        maximum becomes visible instead of silent (capture-the-probe)."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d, {"adr_dir": "docs/adr"}, adr_rel="docs/adr")
            r = harness(repo, "adr-next")
            self.assertEqual(r.stdout.strip(), "0043")
            self.assertIn("after 0042 in docs/adr/", r.stderr)

    def test_land_gate_sees_a_collision_in_the_declared_directory(self):
        """The half that made this worse than a wrong number: the gate filtered its
        diff to `adr/`, so on a member with docs/adr/ it inspected an empty file list
        and returned clean every time — blind at exactly the same place as the
        allocator, so the two could not catch each other."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d, {"adr_dir": "docs/adr"}, adr_rel="docs/adr")
            parent = git(repo, "rev-parse", "HEAD").stdout.strip()
            (repo / "docs" / "adr" / "0042-duplicate.md").write_text("# dup\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "collide")
            tip = git(repo, "rev-parse", "HEAD").stdout.strip()
            out = subprocess.run(
                [sys.executable, "-c",
                 "import importlib.util,sys;"
                 f"s=importlib.util.spec_from_file_location('m',r'{repo}/session.py');"
                 "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
                 # signature is (tip, parent) — passing them the other way round diffs
                 # backwards, finds no ADDED files, and passes for the wrong reason.
                 f"ok,msg=m._adr_land_gate(r'{tip}',r'{parent}');print(ok);print(msg)"],
                capture_output=True, text=True, cwd=str(repo))
            self.assertIn("False", out.stdout, out.stderr)
            self.assertIn("0042", out.stdout)


class UserProfileTest(unittest.TestCase):
    def test_in_repo_profile_still_found_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d)
            (repo / "users" / "operator").mkdir(parents=True)
            (repo / "users" / "operator" / "profile.md").write_text("PROFILE BODY\n")
            self.assertIn("PROFILE BODY", self._profile(repo))

    def test_declared_path_may_escape_the_repo(self):
        """The real case: for every member but the federation, the profile lives in the
        federation's data root, not the member's own tree."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d)
            outside = pathlib.Path(d) / "federation" / "users" / "operator"
            outside.mkdir(parents=True)
            (outside / "profile.md").write_text("FEDERATION PROFILE\n")
            cfg = json.loads((repo / "session.config.json").read_text())
            cfg["user_profile"] = "../federation/users/operator/profile.md"
            (repo / "session.config.json").write_text(json.dumps(cfg))
            self.assertIn("FEDERATION PROFILE", self._profile(repo))

    def test_a_declared_but_unreadable_path_does_not_fall_back(self):
        """Falling through to the glob would restore the silent omission this exists
        to end — the same no-silent-downgrade rule the settings declaration has."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d)
            (repo / "users" / "operator").mkdir(parents=True)
            (repo / "users" / "operator" / "profile.md").write_text("SHOULD NOT BE USED\n")
            cfg = json.loads((repo / "session.config.json").read_text())
            cfg["user_profile"] = "nope/profile.md"
            (repo / "session.config.json").write_text(json.dumps(cfg))
            self.assertEqual(self._profile(repo), "")

    def test_banner_labels_report_what_was_actually_assembled(self):
        """The defect proper: the banner printed `prior-entry + user-profile` whenever
        ANYTHING was injected, so it could not express a missing profile — a status
        line unable to describe its own failure, in the substrate that ships
        no-fabricated-data to the fleet."""
        with tempfile.TemporaryDirectory() as d:
            repo = make_member(d)
            _, labels = self._inject(repo, "PRIOR ENTRY")
            self.assertEqual(labels, ["prior-entry"])          # no profile present
            (repo / "users" / "operator").mkdir(parents=True)
            (repo / "users" / "operator" / "profile.md").write_text("P\n")
            _, labels = self._inject(repo, "PRIOR ENTRY")
            self.assertEqual(labels, ["prior-entry", "user-profile"])
            _, labels = self._inject(repo, "")
            self.assertEqual(labels, ["user-profile"])

    # -- helpers ---------------------------------------------------------------

    def _call(self, repo, expr):
        out = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util;"
             f"s=importlib.util.spec_from_file_location('m',r'{repo}/session.py');"
             "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
             f"print(repr({expr}))"],
            capture_output=True, text=True, cwd=str(repo))
        self.assertEqual(out.returncode, 0, out.stderr)
        return eval(out.stdout.strip())

    def _profile(self, repo):
        """Text only — these cases are about RESOLUTION. The cap/truncation half of the
        signature is covered by ProfileInjectionCapTest."""
        return self._call(repo, "m._user_profile_text()")[0]

    def _inject(self, repo, prior):
        return self._call(repo, f"m._inject_block({prior!r})")


class ProfileInjectionCapTest(unittest.TestCase):
    """WI-0066 — the injection cap cut the profile and said nothing.

    The operator's profile had outgrown the cap, so part of it never reached ANY session
    on ANY member. Because a profile grows at the tail, the cap always ate the NEWEST
    entries — the ones an Architect has had least time to learn were the ones guaranteed
    not to arrive. The banner meanwhile printed a flat `user-profile`, so a partial
    delivery and a complete one were indistinguishable.

    Both halves are load-bearing. Raising the cap alone re-breaks the next time the
    profile gains an entry, which is the same defect with a later date."""

    def _call(self, repo, expr):
        out = subprocess.run(
            [sys.executable, "-c",
             "import importlib.util;"
             f"s=importlib.util.spec_from_file_location('m',r'{repo}/session.py');"
             "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
             f"print(repr({expr}))"],
            capture_output=True, text=True, cwd=str(repo))
        self.assertEqual(out.returncode, 0, out.stderr)
        return eval(out.stdout.strip())

    def _member_with_profile(self, tmp, body):
        repo = make_member(tmp)
        (repo / "users" / "operator").mkdir(parents=True)
        (repo / "users" / "operator" / "profile.md").write_text(body)
        return repo

    def test_a_realistic_profile_arrives_whole(self):
        """The regression that matters: today's profile must not be cut at all."""
        with tempfile.TemporaryDirectory() as d:
            body = "H\n" + ("x" * 9000) + "\nTAIL-ENTRY\n"
            repo = self._member_with_profile(d, body)
            text, dropped = self._call(repo, "m._user_profile_text()")
            self.assertEqual(dropped, 0)
            self.assertIn("TAIL-ENTRY", text,
                          "the newest entry lives at the tail — it must survive")

    def test_an_oversized_profile_reports_exactly_what_it_dropped(self):
        """When the cap does bite, the loss is a number, not a silence."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member_with_profile(d, "y" * 40)
            text, dropped = self._call(repo, "m._user_profile_text(cap=25)")
            self.assertEqual(len(text), 25)
            self.assertEqual(dropped, 15)

    def test_the_banner_label_carries_the_truncation(self):
        """The loss must reach the line a PERSON reads, not stop at a return value."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member_with_profile(d, "z" * 40)
            _, labels = self._call(repo, "m._inject_block('')")
            self.assertEqual(labels, ["user-profile"], "sanity: uncut at the real cap")

            self.assertEqual(harness_fixture.patch_harness(
                repo, "PROFILE_INJECT_CAP = 20000", "PROFILE_INJECT_CAP = 25"), 1,
                "the injection cap's spelling moved — fix this fixture")
            _, labels = self._call(repo, "m._inject_block('')")

            self.assertEqual(len(labels), 1)
            self.assertTrue(labels[0].startswith("user-profile"),
                            "prefix must hold — the banner matches on it")
            self.assertIn("TRUNCATED", labels[0])
            self.assertIn("15", labels[0], "say how much was lost, not just that it was")

    def test_a_truncated_profile_is_never_reported_as_no_profile(self):
        """The prefix-match guard: an equality test would read a delivered-but-truncated
        profile as absent and tell the Architect to declare a key it already declared."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._member_with_profile(d, "q" * 40)
            self.assertEqual(harness_fixture.patch_harness(
                repo, "PROFILE_INJECT_CAP = 20000", "PROFILE_INJECT_CAP = 25"), 1,
                "the injection cap's spelling moved — fix this fixture")
            text, _ = self._call(repo, "m._inject_block('')")
            self.assertIn("USER PROFILE / OVERRIDES", text)
            self.assertIn("TRUNCATED", text, "the injected block says so too")


if __name__ == "__main__":
    unittest.main()
