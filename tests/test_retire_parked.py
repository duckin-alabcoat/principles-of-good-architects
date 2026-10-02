"""Retiring a park ref — and refusing every ref that is not provably retirable.

WI-0141. A lane that cannot push parks its work additively (`landed/<id>`); `integrate`
that cannot reconcile parks the trunk tip the same way (`parked/<trunk>-<sha>`). Both are
right to exist and both outlive their reason, and the item was filed because deleting one
afterwards is exactly what a session cannot do: `confirm-destructive-ops` denies the
force-delete structurally, correctly, because the guard cannot tell a ref whose content
has landed from one holding the only copy of somebody's work.

So what has to be pinned here is not that the verb deletes. It is:

  - that a branch the trunk absorbed BY REBASE — every SHA rewritten, ancestry gone — is
    recognised as landed and retired. This is the case the item names twice as the one a
    naive `merge-base --is-ancestor` verb would refuse forever and a "looks merged" verb
    would delete on a guess;
  - that a branch carrying ONE genuinely unlanded commit is refused, and the refusal
    names that commit rather than a count;
  - that the delete reaches BOTH sides, because a park ref usually lives only on origin
    and a verb that cleaned the local side would leave the thing it was asked to retire;
  - that a ref whose content is on the LOCAL trunk but not on origin's is refused — the
    park ref exists because the work was not safe on one disk, and retiring it then would
    delete the only published copy;
  - that everything outside the two park namespaces is refused by name, whatever its
    history says.

The last two are the ones that would go quietly wrong. `DetectorFiresTest` at the bottom
is the falsifier: it forces the predicate to answer yes and proves the suite goes red, so
a green run here is evidence the tests are looking rather than that nothing is broken.

stdlib unittest: python3 -m unittest tests.test_retire_parked
"""

import argparse
import io
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args, check=True):
    return subprocess.run([GIT, "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


@unittest.skipUnless(GIT, "git not available")
class RetireParkedBase(unittest.TestCase):
    """A bare origin plus a clone, because this verb's whole subject is a ref that lives
    on the remote. A fixture with no remote could exercise the predicate and would still
    prove nothing about the verb — the live federation instances are all origin-only."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.origin = self.tmp / "origin.git"
        subprocess.run([GIT, "init", "-q", "--bare", "-b", "main", str(self.origin)],
                       check=True, capture_output=True)
        self.repo = self.tmp / "repo"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(self.repo)],
                       check=True, capture_output=True)
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        _git(self.repo, "config", "commit.gpgsign", "false")
        self.write("base.txt", "base\n", "init")
        _git(self.repo, "push", "-q", "-u", "origin", "main")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG", "CFG_RAW")}
        session.ROOT = self.repo
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch",
                       "trunk": "main"}
        session.CFG_RAW = {"trunk": "main"}

    def tearDown(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── fixture helpers ────────────────────────────────────────────────────────
    def write(self, rel, text, msg):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", msg)
        return _git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def park(self, ref, commits, push=True, keep_local=False):
        """Cut `ref` off the trunk, put `commits` on it, publish it, and (by default)
        drop the local copy — which is the shape every live instance actually has."""
        _git(self.repo, "checkout", "-q", "-b", ref, "main")
        for rel, text, msg in commits:
            self.write(rel, text, msg)
        tip = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "-q", "main")
        if push:
            _git(self.repo, "push", "-q", "origin", f"{ref}:refs/heads/{ref}")
        if not keep_local:
            _git(self.repo, "branch", "-qD", ref)
        _git(self.repo, "fetch", "-q", "origin")
        return tip

    def land_by_rebase(self, ref):
        """Absorb `ref`'s commits into the trunk the way an ordinary `pull --rebase`
        does: a peer's commit lands first, then the parked commits are replayed on top
        and every one of them gets a NEW SHA. After this the content is wholly on the
        trunk and `merge-base --is-ancestor` still answers no — the exact trap the item
        names, reproduced rather than asserted."""
        self.write("peer.txt", "peer landed first\n", "peer: unrelated work")
        src = (f"refs/remotes/origin/{ref}" if _git(
            self.repo, "rev-parse", "--verify", "-q", f"refs/heads/{ref}",
            check=False).returncode != 0 else ref)
        base = _git(self.repo, "merge-base", "main", src).stdout.strip()
        shas = _git(self.repo, "rev-list", "--reverse",
                    f"{base}..{src}").stdout.split()
        for sha in shas:
            _git(self.repo, "cherry-pick", sha)
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")

    def run_verb(self, branch=None, **kw):
        ns = argparse.Namespace(branch=branch, all=False, dry_run=False, verbose=False)
        for k, v in kw.items():
            setattr(ns, k, v)
        buf, err = io.StringIO(), io.StringIO()
        code = None
        with redirect_stdout(buf), redirect_stderr(err):
            try:
                session.cmd_retire_parked(ns)
            except SystemExit as e:
                code = e.code
        return buf.getvalue() + err.getvalue(), code

    def ref_on_origin(self, ref):
        return _git(self.origin, "rev-parse", "--verify", "-q", f"refs/heads/{ref}",
                    check=False).returncode == 0

    def ref_local(self, ref):
        return _git(self.repo, "rev-parse", "--verify", "-q", f"refs/heads/{ref}",
                    check=False).returncode == 0


class PredicateTest(RetireParkedBase):
    """`_park_ref_verdicts` on its own — the judgement, before any deleting."""

    def test_a_rebased_branch_is_not_an_ancestor_but_is_content_equivalent(self):
        """The fixture itself is the claim: if this ever starts passing ancestry, every
        other test in this file is testing a different situation than the live one."""
        self.park("landed/rebased", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/rebased")
        ref = "refs/remotes/origin/landed/rebased"
        self.assertNotEqual(0, _git(self.repo, "merge-base", "--is-ancestor", ref,
                                    "refs/remotes/origin/main",
                                    check=False).returncode,
                            "the fixture no longer reproduces the rewritten-SHA case")
        rows, landed = session._park_ref_verdicts("landed/rebased",
                                                  "refs/remotes/origin/main")
        self.assertTrue(landed)
        self.assertEqual({"content-equivalent"}, {r["verdict"] for r in rows})

    def test_one_genuinely_unlanded_commit_falsifies_the_whole_ref(self):
        self.park("landed/mixed", [("a.txt", "a\n", "feat: a"),
                                   ("b.txt", "b\n", "feat: b — never landed")])
        # Land only the FIRST commit, by rebase. The second stays real work.
        self.write("peer.txt", "peer\n", "peer: unrelated work")
        base = _git(self.repo, "merge-base", "main",
                    "refs/remotes/origin/landed/mixed").stdout.strip()
        first = _git(self.repo, "rev-list", "--reverse",
                     f"{base}..refs/remotes/origin/landed/mixed").stdout.split()[0]
        _git(self.repo, "cherry-pick", first)
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")
        rows, landed = session._park_ref_verdicts("landed/mixed",
                                                  "refs/remotes/origin/main")
        self.assertFalse(landed)
        self.assertEqual(1, sum(1 for r in rows if r["verdict"] == "UNLANDED"))
        self.assertTrue(any(r["verdict"] == "content-equivalent" for r in rows))

    def test_an_ancestor_ref_reads_as_ancestor(self):
        """`parked/<trunk>-<sha>` is the trunk tip itself; once the trunk moves past it
        the ref is a plain ancestor and there is nothing to compare patch-wise."""
        tip = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "push", "-q", "origin", f"{tip}:refs/heads/parked/main-{tip[:12]}")
        self.write("more.txt", "more\n", "trunk moves on")
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")
        rows, landed = session._park_ref_verdicts(f"parked/main-{tip[:12]}",
                                                  "refs/remotes/origin/main")
        self.assertTrue(landed)
        self.assertEqual(["ancestor"], [r["verdict"] for r in rows])

    def test_a_ref_that_does_not_exist_is_never_landed(self):
        """Fail-safe: absent is not the same as clean, and must not read as deletable."""
        self.assertEqual(([], False),
                         session._park_ref_verdicts("landed/nope",
                                                    "refs/remotes/origin/main"))

    def test_an_unreadable_history_is_never_landed(self):
        self.park("landed/x", [("a.txt", "a\n", "feat: a")])
        with mock.patch.object(
                session, "sh",
                side_effect=lambda a, **k: subprocess.CompletedProcess(a, 1, "", "boom")):
            rows, landed = session._park_ref_verdicts("landed/x",
                                                      "refs/remotes/origin/main")
        self.assertFalse(landed)


class RetireTest(RetireParkedBase):
    """The acceptance pins: the rebased ref retires, the mixed one refuses."""

    def test_a_rebased_ref_is_retired_on_both_sides(self):
        self.park("landed/rebased", [("a.txt", "a\n", "feat: a")], keep_local=True)
        self.land_by_rebase("landed/rebased")
        out, code = self.run_verb("landed/rebased")
        self.assertIsNone(code, out)
        self.assertIn("retired: landed/rebased", out)
        self.assertFalse(self.ref_on_origin("landed/rebased"), out)
        self.assertFalse(self.ref_local("landed/rebased"), out)

    def test_the_receipt_names_every_commit_it_acted_on(self):
        """`capture-the-probe`: a delete whose receipt says "done" is not a receipt. The
        acting path never collapses, so all three commits are on the page."""
        self.park("landed/three", [("a.txt", "a\n", "feat: a"),
                                   ("b.txt", "b\n", "feat: b"),
                                   ("c.txt", "c\n", "feat: c")])
        self.land_by_rebase("landed/three")
        out, code = self.run_verb("landed/three")
        self.assertIsNone(code, out)
        for msg in ("feat: a", "feat: b", "feat: c"):
            self.assertIn(msg, out)
        self.assertEqual(3, out.count("content-equivalent"), out)

    def test_a_single_unlanded_commit_refuses_and_keeps_the_ref(self):
        self.park("landed/mixed", [("a.txt", "a\n", "feat: a"),
                                   ("b.txt", "b\n", "feat: b — never landed")])
        self.write("peer.txt", "peer\n", "peer: unrelated work")
        base = _git(self.repo, "merge-base", "main",
                    "refs/remotes/origin/landed/mixed").stdout.strip()
        first = _git(self.repo, "rev-list", "--reverse",
                     f"{base}..refs/remotes/origin/landed/mixed").stdout.split()[0]
        _git(self.repo, "cherry-pick", first)
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")
        out, code = self.run_verb("landed/mixed")
        self.assertEqual(2, code, out)
        self.assertIn("UNLANDED", out)
        self.assertIn("feat: b — never landed", out)
        self.assertIn("REFUSED", out)
        self.assertTrue(self.ref_on_origin("landed/mixed"),
                        "the verb deleted a ref carrying real work")

    def test_an_origin_only_ref_is_retired_on_origin(self):
        """The live shape: pushed from a lane that kept no local copy."""
        self.park("landed/remote-only", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/remote-only")
        self.assertFalse(self.ref_local("landed/remote-only"))
        out, code = self.run_verb("landed/remote-only")
        self.assertIsNone(code, out)
        self.assertFalse(self.ref_on_origin("landed/remote-only"), out)

    def test_dry_run_writes_nothing(self):
        self.park("landed/dry", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/dry")
        out, code = self.run_verb("landed/dry", dry_run=True)
        self.assertIn("would retire 1 ref", out)
        self.assertTrue(self.ref_on_origin("landed/dry"))

    def test_the_bare_survey_deletes_nothing(self):
        """No branch and no `--all` is a question, not an instruction. The seven live
        federation refs were all retirable when this verb landed; a survey that acted on
        them would have been an unasked bulk delete of the remote."""
        self.park("landed/one", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/one")
        out, code = self.run_verb(None)
        self.assertIn("survey only", out)
        self.assertTrue(self.ref_on_origin("landed/one"), out)

    def test_all_retires_the_clean_and_refuses_the_rest_by_name(self):
        """A sweep that silently skipped what it could not retire would leave the
        operator believing the namespace is empty — the stale-signal failure one level
        up from the one this item is about."""
        self.park("landed/clean", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/clean")
        self.park("landed/dirty", [("z.txt", "z\n", "feat: z — real work")])
        out, code = self.run_verb(None, all=True)
        self.assertIn("retired: landed/clean", out)
        self.assertIn("landed/dirty", out)
        self.assertIn("REFUSED", out)
        self.assertFalse(self.ref_on_origin("landed/clean"), out)
        self.assertTrue(self.ref_on_origin("landed/dirty"), out)


class FailedDeleteTest(RetireParkedBase):
    """Found by running the verb for real, not by reading it
    ([`exercise-delegated-work-end-to-end`](habits/master.md#exercise-delegated-work-end-to-end)).

    The first live `--all` over the federation's own seven park refs hit an SSH timeout
    part-way through: four refs were deleted on origin, three were not, every failure went
    to stderr — and the verb exited 0. A caller that checks status, which is the only
    reason an exit code exists, would read that as a clear namespace with three refs still
    standing in it. The fixture's local origin never fails, so nothing in the suite could
    have produced this; the network did."""

    def _fail_the_delete(self):
        real = session.sh

        def fake(a, **k):
            if a[:2] == ["git", "push"] and "--delete" in a:
                return subprocess.CompletedProcess(a, 128, "", "ssh: connect to host "
                                                               "github.com port 22: "
                                                               "Operation timed out")
            return real(a, **k)
        return mock.patch.object(session, "sh", side_effect=fake)

    def test_a_delete_that_did_not_happen_does_not_exit_zero(self):
        self.park("landed/clean", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/clean")
        with self._fail_the_delete():
            out, code = self.run_verb("landed/clean")
        self.assertEqual(1, code, out)
        self.assertIn("DID NOT HAPPEN", out)
        self.assertNotIn("retired: landed/clean", out)
        self.assertTrue(self.ref_on_origin("landed/clean"), out)

    def test_the_failure_code_is_distinct_from_the_refusal_code(self):
        """"the predicate said no" and "the predicate said yes and the network dropped"
        are different facts, and the retry for them is different."""
        self.park("landed/dirty", [("z.txt", "z\n", "feat: z — real work")])
        _out, refused = self.run_verb("landed/dirty")
        self.park("landed/clean", [("a.txt", "a\n", "feat: a")])
        self.land_by_rebase("landed/clean")
        with self._fail_the_delete():
            _out2, broke = self.run_verb("landed/clean")
        self.assertEqual(2, refused)
        self.assertEqual(1, broke)


class TrunkChoiceTest(RetireParkedBase):
    """Which trunk the verb measures against — the decision with the quietest failure."""

    def test_content_on_the_local_trunk_only_is_refused(self):
        """The park ref exists BECAUSE the work was not safe on one disk. Retiring it
        while the trunk carrying its content is unpushed would delete the only published
        copy and leave the work exactly where parking it was meant to prevent."""
        self.park("landed/unpushed", [("a.txt", "a\n", "feat: a")])
        self.write("peer.txt", "peer\n", "peer: unrelated")
        base = _git(self.repo, "merge-base", "main",
                    "refs/remotes/origin/landed/unpushed").stdout.strip()
        for sha in _git(self.repo, "rev-list", "--reverse",
                        f"{base}..refs/remotes/origin/landed/unpushed").stdout.split():
            _git(self.repo, "cherry-pick", sha)
        # Deliberately NOT pushed. The content is on a trunk; it is not on THE trunk.
        out, code = self.run_verb("landed/unpushed")
        self.assertEqual(2, code, out)
        self.assertTrue(self.ref_on_origin("landed/unpushed"), out)

    def test_the_banner_line_says_which_trunk_it_used(self):
        self.park("landed/x", [("a.txt", "a\n", "feat: a")])
        out, _ = self.run_verb(None)
        self.assertIn("measuring against origin/main at", out)

    def test_with_no_remote_it_says_the_answer_is_disk_local(self):
        """`declare-what-a-check-assumes`: a member with no remote still gets an answer,
        and is told the answer is about this disk only rather than given the clean one."""
        real = session.sh

        def fake(a, **k):
            if a[:2] == ["git", "remote"]:
                return subprocess.CompletedProcess(a, 0, "", "")   # no remotes at all
            return real(a, **k)

        with mock.patch.object(session, "sh", side_effect=fake):
            ref, why = session._retire_trunk_ref()
        self.assertEqual("refs/heads/main", ref)
        self.assertIn("LOCAL main", why)


class StructuralRefusalTest(RetireParkedBase):
    """Refusals that hold whatever the history says."""

    def test_a_lane_branch_is_refused_and_pointed_at_its_own_verb(self):
        _git(self.repo, "branch", "worktree-poga-9", "main")
        out, code = self.run_verb("worktree-poga-9")
        self.assertEqual(2, code, out)
        self.assertIn("not a park ref", out)
        self.assertIn("discard-phantom-lanes", out)
        self.assertTrue(self.ref_local("worktree-poga-9"))

    def test_the_trunk_is_refused(self):
        out, code = self.run_verb("main")
        self.assertEqual(2, code, out)
        self.assertIn("that is the trunk", out)
        self.assertTrue(self.ref_local("main"))

    def test_a_ref_that_does_not_exist_is_refused(self):
        out, code = self.run_verb("landed/ghost")
        self.assertEqual(2, code, out)
        self.assertIn("no such ref", out)

    def test_a_checked_out_ref_is_refused_by_name(self):
        """A ref someone is standing on is never retired, however clean its history."""
        self.park("parked/main-abc", [("a.txt", "a\n", "feat: a")], keep_local=True)
        self.land_by_rebase("parked/main-abc")
        wt = self.tmp / "wt"
        _git(self.repo, "worktree", "add", "-q", str(wt), "parked/main-abc")
        out, code = self.run_verb("parked/main-abc")
        self.assertEqual(2, code, out)
        self.assertIn("checked out at", out)
        self.assertTrue(self.ref_on_origin("parked/main-abc"), out)

    def test_a_ref_outside_the_namespaces_is_refused_even_when_fully_landed(self):
        """The namespace is a scope, not a heuristic — `retire-parked` is not a
        delete-any-ref verb wearing a sanctioned name."""
        _git(self.repo, "branch", "some/other", "main")
        out, code = self.run_verb("some/other")
        self.assertEqual(2, code, out)
        self.assertIn("not a park ref", out)
        self.assertTrue(self.ref_local("some/other"))


class BannerTest(RetireParkedBase):
    """The start-banner line. A verb nobody is told to run is the same stale signal the
    park ref already was — but the line runs at every session open, so what it is allowed
    to SPEND is as much a part of its contract as what it says."""

    def test_it_is_silent_only_when_there_are_no_park_refs(self):
        self.assertEqual([], session._retirable_park_lines())

    def test_a_ref_it_could_not_price_is_still_announced(self):
        """The cheap ancestry test cannot settle a rebased or unlanded ref, and going
        quiet about one would rebuild the stale signal this item exists to remove. It is
        named as UNCHECKED rather than folded into either answer."""
        self.park("landed/dirty", [("z.txt", "z\n", "feat: z — real work")])
        lines = session._retirable_park_lines()
        self.assertEqual(1, len(lines))
        self.assertIn("1 park ref(s) left over", lines[0])
        self.assertIn("NOT checked here", lines[0])
        self.assertIn("retire-parked", lines[0])

    def test_it_names_the_cheaply_retirable_refs(self):
        tip = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "push", "-q", "origin",
             f"{tip}:refs/heads/parked/main-{tip[:12]}")
        self.write("more.txt", "more\n", "trunk moves on")
        _git(self.repo, "push", "-q", "origin", "main")
        _git(self.repo, "fetch", "-q", "origin")
        lines = session._retirable_park_lines()
        self.assertIn("provably retirable", lines[0])
        self.assertIn(f"parked/main-{tip[:12]}", lines[0])
        self.assertNotIn("NOT checked here", lines[0])

    def test_it_never_runs_the_expensive_content_test(self):
        """THE COST IS THE CONTRACT. `cherry` computes patch-ids — ~327 ms/ref measured
        against ~35 ms for the ancestry walk — and the first draft of this line ran it
        behind a count cap, which measured ~4.7s of session-open time. A cap that bounds
        the count and not the time is not a cap on the thing that hurts, so the banner is
        pinned to the cheap half here rather than left to a comment nobody re-measures."""
        self.park("landed/a", [("a.txt", "a\n", "feat: a")])
        self.park("landed/b", [("b.txt", "b\n", "feat: b")])
        seen = []
        real = session.sh

        def spy(a, **k):
            seen.append(a)
            return real(a, **k)

        with mock.patch.object(session, "sh", side_effect=spy):
            session._retirable_park_lines()
        self.assertTrue(seen, "the banner ran no git at all")
        self.assertFalse([a for a in seen if "cherry" in a],
                         "the banner is paying for the content test again")
        self.assertTrue([a for a in seen if "merge-base" in a],
                        "the banner stopped checking anything")

    def test_it_fails_open(self):
        with mock.patch.object(session, "_park_refs", side_effect=RuntimeError("boom")):
            self.assertEqual([], session._retirable_park_lines())


class DetectorFiresTest(RetireParkedBase):
    """The falsifier. Force the predicate to answer yes and the suite must go red — a
    green run above is only evidence if these tests can actually fail."""

    def test_a_predicate_hardcoded_to_yes_is_caught(self):
        self.park("landed/dirty", [("z.txt", "z\n", "feat: z — real work")])
        with mock.patch.object(session, "_park_ref_verdicts",
                               return_value=([], True)):
            out, code = self.run_verb("landed/dirty")
        self.assertIsNone(code, "the mutated predicate still refused — the verb is not "
                                "reading the predicate this suite tests")
        self.assertFalse(self.ref_on_origin("landed/dirty"),
                         "the mutated predicate did not reach the delete, so the "
                         "passing tests above prove less than they look like they do")

    def test_the_namespace_scope_is_load_bearing(self):
        with mock.patch.object(session, "PARK_NAMESPACES", ("parked/", "landed/", "")):
            _git(self.repo, "branch", "worktree-poga-9", "main")
            out, code = self.run_verb("worktree-poga-9")
        self.assertNotIn("not a park ref", out,
                         "widening the namespaces changed nothing, so the refusal above "
                         "is not coming from the scope it claims to")


if __name__ == "__main__":
    unittest.main()
