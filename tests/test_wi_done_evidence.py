"""WI-0385: a done claim needs substantive evidence on the trunk."""

import argparse
import io
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

from test_wi_done_is_terminal import TerminalBase, _git

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import session  # noqa: E402


class DoneEvidenceTest(TerminalBase):
    def test_no_citation_refuses_without_editing_item(self):
        # Terminal reclassification is intentionally outside this gate; use a
        # fresh open item to exercise an actual transition into done.
        self._reset("WI-0003", "open")
        before = self._item("WI-0003")
        code, out = self._status("WI-0003", status="done", section="backlog")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0003"), before)
        self.assertIn("refs/heads/main", out)
        self.assertIn("work-items:", out)
        self.assertIn("no substantive citing commit", out)

    def test_citing_commit_still_in_lane_has_distinct_remedy(self):
        self._reset("WI-0003", "open")
        _git(self.main, "branch", "lane", "main")
        _git(self.main, "checkout", "-q", "lane")
        (self.main / "lane.txt").write_text("implemented")
        _git(self.main, "add", "lane.txt")
        _git(self.main, "commit", "-qm", "feat: implement WI-0003")
        code, out = self._status("WI-0003", status="done")
        self.assertEqual(code, 2)
        self.assertIn("has not landed", out)
        self.assertEqual(self._item("WI-0003")["status"], "open")

    def test_direct_citation_on_trunk_allows_done(self):
        self._reset("WI-0003", "open")
        (self.main / "feature.txt").write_text("implemented")
        _git(self.main, "add", "feature.txt")
        _git(self.main, "commit", "-qm", "feat: implement WI-0003")
        code, out = self._status("WI-0003", status="done")
        self.assertEqual(code, 0, out)

    def test_unlanded_close_journal_is_in_flight(self):
        self._reset("WI-0003", "open")
        _git(self.main, "branch", "lane", "main")
        _git(self.main, "checkout", "-q", "lane")
        journal = self.main / "sessions/journal/20260918T0000Z-test-abc.md"
        journal.parent.mkdir(parents=True)
        journal.write_text("---\nwork-items: WI-0003\n---\n\nWork shipped.\n")
        (self.main / "feature.txt").write_text("implemented")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "docs(handoff): close 20260918T0000Z-test-abc")
        code, out = self._status("WI-0003", status="done")
        self.assertEqual(code, 2)
        self.assertIn("has not landed", out)

    def test_store_note_commit_is_not_delivery(self):
        self._reset("WI-0003", "open")
        path = next((self.main / "work-items").glob("WI-0003-*.md"))
        path.write_text(path.read_text() + "\nnoted\n")
        _git(self.main, "add", str(path))
        _git(self.main, "commit", "-qm", "chore(work-items): note on WI-0003")
        code, out = self._status("WI-0003", status="done")
        self.assertEqual(code, 2)
        self.assertIn("no substantive citing commit", out)

    def test_landed_close_journal_citation_allows_done(self):
        self._reset("WI-0003", "open")
        journal = self.main / "sessions/journal/20260918T0000Z-test-abc.md"
        journal.parent.mkdir(parents=True)
        journal.write_text("---\nwork-items: WI-0003\n---\n\nWork shipped.\n")
        (self.main / "feature.txt").write_text("implemented")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "docs(handoff): close 20260918T0000Z-test-abc")
        code, out = self._status("WI-0003", status="done")
        self.assertEqual(code, 0, out)


class BulkMoveIsGuardedTest(TerminalBase):
    """The class, not the instance — `wi-move` sets `status` too.

    Both status refusals were built against `cmd_wi_status`, the verb where the
    defect was observed. `cmd_wi_move` writes the same field and had neither, so
    `poga work move <id> --status done` minted an unshipped `done` and
    `--status open` reopened a closed item — the whole of WI-0380 and WI-0385,
    reachable by typing `move` instead of `status`
    (`retire-the-class-not-the-instance`).

    The all-or-nothing case is the one worth writing carefully: this verb already
    promises the batch never half-applies, and a guard called from inside the write
    loop would have broken that promise silently.
    """

    def _move(self, *ids, **kw):
        kw = {"ids": list(ids), "section": None, "status": None, **kw}
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_move(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def test_move_to_done_without_evidence_is_refused(self):
        self._reset("WI-0003", "open")
        code, out = self._move("WI-0003", status="done")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0003")["status"], "open")
        self.assertIn("no substantive citing commit", out)

    def test_move_cannot_reopen_a_closed_item_either(self):
        code, out = self._move("WI-0002", status="open")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0002")["status"], "done")
        self.assertIn("ADR-0139", out)

    def test_move_to_done_WITH_evidence_is_allowed(self):
        """The positive control. WI-0001 has `feat: deliver WI-0001` on the trunk
        from the fixture, so a guard that simply refused every bulk done would
        pass the two tests above and still be broken."""
        code, out = self._move("WI-0001", status="done")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._item("WI-0001")["status"], "done")

    def test_a_refused_id_leaves_the_WHOLE_batch_unwritten(self):
        """WI-0001 has evidence and WI-0003 does not. The verb's contract is that
        an unacceptable batch changes nothing — so WI-0001 must still be open even
        though, taken alone, its transition was fine."""
        self._reset("WI-0003", "open")
        code, _out = self._move("WI-0001", "WI-0003", status="done")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0001")["status"], "open",
                         "the acceptable id was written before the refusal — the "
                         "batch half-applied")
        self.assertEqual(self._item("WI-0003")["status"], "open")

    def test_a_section_only_move_is_untouched_by_the_guards(self):
        """The control that keeps the guard narrow. Re-sectioning a DONE item is
        ordinary curation and must not start refusing."""
        code, out = self._move("WI-0002", section="backlog")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._item("WI-0002")["section"], "backlog")
        self.assertEqual(self._item("WI-0002")["status"], "done")


class RealHistoryTest(unittest.TestCase):
    """Acceptance clause 5 — the detector is proven against the REAL case.

    Every test above builds its own repo, and a hand-written fixture can only ever
    confirm that the resolver agrees with the shape its author already had in mind.
    The case that nearly killed this guard was found in the actual history: **an item
    shipped, and no commit on the trunk names it.** Its implementation rode in under a
    squashed `docs(handoff): close <session-id>` subject that names the JOURNAL and
    nothing else, which is what the merge path (ADR-0143 D1) makes the default for a
    lane that closed honestly. So a subject-only rule would have refused exactly the
    well-run lanes, and the journal join is the whole reason this guard is shippable.

    THE CASE IS DERIVED FROM THE TRUNK, NOT PINNED — and the reason is that the pin
    invalidated itself within a day. It named WI-0065 and the commit that closed it;
    then this guard's own delivery commit landed, citing WI-0065 in its body as the
    motivating example, and the item stopped being journal-only. Two tests here went
    red on a trunk nobody had broken, and every lane queued behind them was blocked by
    a fixture rather than by a defect
    ([`derive-a-checks-subjects-from-the-authority`](../habits/master.md)). The
    authority for "which real close is journal-only" is the trunk itself, so this class
    now asks it: newest close first, first qualifying item wins. Deterministic given a
    history, and it cannot be invalidated by a later commit mentioning an id.

    DERIVED WITHOUT THE FUNCTION UNDER TEST, which is the part that keeps the clause
    honest. The search below reimplements the file-based substantive test against
    `git diff-tree` rather than calling `_wi_landed_evidence`; asking the resolver to
    pick its own fixture and then asserting it agrees would be a tautology.

    READ-ONLY BY CONSTRUCTION. Everything here shells `git log`, `git grep`, `git show`
    and `git diff-tree`; nothing writes, commits or touches the store, so pointing it at
    the real repo cannot act *as* the system
    (`evidence-is-separated-from-state-by-construction`).

    IT SKIPS RATHER THAN LYING when the history it needs is not present — a shallow
    clone, the public mirror, or a checkout whose trunk is not `main` has no opinion to
    offer here, and a green from a run that could not look is the false all-clear this
    whole neighbourhood exists to prevent. The skip says how many closes it read, so
    "found none" is never mistaken for "did not look".
    """

    #: The guard's own delivery — cited directly in a commit subject on the trunk.
    DIRECT_ITEM = "WI-0385"
    #: How far back to read. 60 closes is several weeks of this repo and keeps the
    #: search under a second; the qualifying case has been in the newest handful every
    #: time it has been run.
    SCAN_LIMIT = 60
    #: Paths that cannot prove delivery — the resolver's own list, restated here rather
    #: than imported, so this search is an independent opinion and not a mirror.
    BOOKKEEPING_DIRS = ("work-items/", "sessions/journal/")
    BOOKKEEPING_FILES = frozenset({"session-handoff.md", "STATUS.md", "ROADMAP.md"})

    @classmethod
    def _git(cls, *args):
        return subprocess.run(["git", "-C", str(cls.root), *args],
                              capture_output=True, text=True)

    @classmethod
    def _files(cls, sha):
        r = cls._git("diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha)
        return r.stdout.splitlines() if r.returncode == 0 else []

    @classmethod
    def _substantive(cls, sha):
        return any(not (p.startswith(cls.BOOKKEEPING_DIRS)
                        or p in cls.BOOKKEEPING_FILES)
                   for p in cls._files(sha))

    @classmethod
    def _citing_commits(cls, wid):
        """(sha, subject, body) for every trunk commit whose message cites `wid`."""
        r = cls._git("log", "refs/heads/main", "--fixed-strings", f"--grep={wid}",
                     "--format=%H%x1f%s%x1f%B%x1e")
        out = []
        for record in r.stdout.split("\x1e"):
            parts = record.strip().split("\x1f", 2)
            if len(parts) != 3:
                continue
            sha, subject, body = parts
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(wid)}(?!\d)", body):
                out.append((sha, subject, body))
        return out

    @classmethod
    def _derive_journal_only_case(cls):
        """(item, sha, scanned): the newest close whose item is reachable ONLY through
        the journal join. `scanned` is what the skip message reports."""
        log = cls._git("log", "refs/heads/main", "--format=%H%x1f%s", "--",
                       "sessions/journal/")
        scanned = 0
        for line in log.stdout.splitlines():
            if "\x1f" not in line:
                continue
            sha, subject = line.split("\x1f", 1)
            if not subject.startswith("docs(handoff): close "):
                continue
            scanned += 1
            if scanned > cls.SCAN_LIMIT:
                break
            if not cls._substantive(sha):
                continue
            for path in cls._files(sha):
                if not path.startswith("sessions/journal/"):
                    continue
                if pathlib.Path(path).stem not in subject:
                    continue
                blob = cls._git("show", f"{sha}:{path}")
                if blob.returncode != 0:
                    continue
                front = blob.stdout.split("\n---", 1)[0]
                ids = [wid for fl in front.splitlines()
                       if fl.startswith("work-items:")
                       for wid in re.findall(r"WI-\d+", fl)]
                for wid in ids:
                    citing = cls._citing_commits(wid)
                    if any(cls._substantive(c) for c, _s, _b in citing):
                        continue
                    return wid, sha, scanned
        return None, None, scanned

    @classmethod
    def setUpClass(cls):
        cls.root = pathlib.Path(__file__).resolve().parent.parent
        if not (cls.root / ".git").exists():
            raise unittest.SkipTest("not a git checkout")
        if cls._git("rev-parse", "--verify", "--quiet",
                    "refs/heads/main").returncode != 0:
            raise unittest.SkipTest("no refs/heads/main here")
        cls.item, cls.sha, scanned = cls._derive_journal_only_case()
        if not cls.item:
            raise unittest.SkipTest(
                f"read {scanned} close commit(s) on refs/heads/main and none carried a "
                f"journal-only item — the join is NOT exercised by this run")

    def setUp(self):
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self.addCleanup(self._restore)
        session.ROOT = self.root
        session.CFG = {"trunk": "main"}

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def test_the_journal_join_finds_the_real_uncited_close(self):
        """The case a subject-only rule gets wrong, on the real commit."""
        state, sha = session._wi_landed_evidence(self.item)
        self.assertEqual(state, "landed")
        self.assertEqual(sha, self.sha,
                         f"{self.item} resolves to {sha[:12]}, not the close "
                         f"{self.sha[:12]} the trunk says delivered it")

    def test_the_real_case_is_genuinely_invisible_to_a_subject_rule(self):
        """The negative control the clause above is worthless without.

        If the derived item were reachable directly, the test above would pass through
        the direct path and silently stop exercising the journal join at all — green,
        and measuring nothing. Two independent halves, because the search only checked
        the first: no substantive commit cites it in a SUBJECT either, and the close's
        journal really does carry the id in its `work-items:` frontmatter, which is the
        only thing the join has to bite on.
        """
        citing = self._citing_commits(self.item)
        self.assertTrue(citing, "expected the store's own note commits at least")
        loud = [s for _sha, s, _b in citing
                if not (s.startswith("chore(work-items):")
                        or s.startswith("docs(roadmap):"))]
        self.assertFalse(
            loud, f"{self.item} is cited by a substantive-looking subject ({loud}) "
                  f"while carrying no substantive files — the file rule and the subject "
                  f"rule disagree about this commit, and that is worth reading")
        journals = self._git("grep", "-l", "-F", self.item, "refs/heads/main", "--",
                             "sessions/journal/")
        self.assertIn("sessions/journal/", journals.stdout,
                      f"{self.item} is in no trunk journal, so nothing could join")

    def test_a_directly_cited_item_lands_on_the_real_trunk(self):
        state, sha = session._wi_landed_evidence(self.DIRECT_ITEM)
        self.assertEqual(state, "landed")
        self.assertTrue(sha)

    def test_an_id_the_store_never_issued_is_absent_not_landed(self):
        """The positive/negative pair on the same real history. Without this, a
        resolver that returned `landed` unconditionally would pass every clause
        above."""
        self.assertEqual(session._wi_landed_evidence("WI-9999"), ("absent", ""))


class CannotTellFailsClosedTest(unittest.TestCase):
    """The fourth state, and the direction it fails in.

    `_wi_landed_evidence` answers `landed`, `in-flight`, `absent` or **`unknown`** —
    the last meaning the trunk could not be read at all. Nothing asserted that one,
    which is the gap `declare-what-a-check-assumes` names exactly: "couldn't tell"
    folded into the same output as "checked and it's fine".

    IT FAILS CLOSED, DELIBERATELY. The general preference is that a guard fails open
    rather than bricking the verb it guards — but the direction is set by what is on
    the other side, and on the other side of this one is a `done` that ADR-0139 D1
    makes TERMINAL. `_refuse_reopen` will not let it back out. So a wrong `done`
    written while git was unreadable is not a mistake anyone can undo, and open is
    the unsafe direction here.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # TWO shapes, because they fail at different depths and only one of them
        # is a git error. `no_trunk` is a real repo on an unborn `main` — git
        # answers fine and the ref simply is not there (`rev-parse` exits 1).
        # `not_a_repo` is an ordinary directory — git itself errors (exit 128).
        # A resolver that only handled the first would report the second as a
        # searched negative.
        self.no_trunk = self.tmp / "repo"
        self.no_trunk.mkdir()
        subprocess.run(["git", "-C", str(self.no_trunk), "init", "-q", "-b", "main"],
                       check=True, capture_output=True)
        # Never committed to here, but the module-wide rule is that a fixture repo
        # states its own signing default rather than inheriting the runner's.
        subprocess.run(["git", "-C", str(self.no_trunk),
                        "config", "commit.gpgsign", "false"],
                       check=True, capture_output=True)
        self.not_a_repo = self.tmp / "bare-directory"
        self.not_a_repo.mkdir()
        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self.addCleanup(self._restore)
        session.CFG = {"trunk": "main"}
        session.ROOT = self.no_trunk

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)

    def test_a_trunk_that_is_not_there_is_unknown_not_absent(self):
        """`absent` claims a search happened. This one never got to look."""
        self.assertEqual(session._wi_landed_evidence("WI-0001"), ("unknown", ""))

    def test_git_failing_outright_is_unknown_too(self):
        """The deeper shape: not a missing ref, a missing repository."""
        session.ROOT = self.not_a_repo
        self.assertEqual(session._wi_landed_evidence("WI-0001"), ("unknown", ""))

    def test_it_refuses_and_says_it_could_not_check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as e:
                session._require_wi_landed_evidence("WI-0001")
        self.assertEqual(e.exception.code, 2)
        out = buf.getvalue()
        self.assertIn("could not be checked", out)
        self.assertNotIn("no substantive citing commit", out,
                         "an unreadable trunk must not report a searched negative")
