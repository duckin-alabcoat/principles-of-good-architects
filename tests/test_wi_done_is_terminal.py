"""WI-0380 — a closed work item stays closed, and the refusal teaches the remedy.

[ADR-0073](../adr/0073-work-item-store.md) D3 gave an item a `status` and never said
whether `done` was FINAL. The substrate's silence read as permission:
`poga work status <id> --status open` accepted a done item with no refusal, and
`wi-check` never looked at a done->open transition.
[ADR-0139](../adr/0139-a-closed-work-item-stays-closed-and-a-residue-is-minted-fresh.md)
D1 settled it — `done` and `superseded` are terminal and a residue is MINTED FRESH — and
shipped with **no enforcement at all**, having rejected a refusal in `wi-status` for SCOPE
rather than merit. WI-0380 is that refusal.

Why it could not wait for the first violation: the store already holds one live example of
the shape (WI-0264 back to WI-0250), and a refusal that costs one print is cheaper than the
audit that has to find a silent reopen after the fact.

The properties pinned here:

  A. THE TRANSITION IS REFUSED, and refused BEFORE anything is written. A call that
     reopens *and* moves the section leaves both untouched — half-applying is the failure
     mode `cmd_wi_edit` already guards its two prose fields against (WI-0289).

  B. THE REFUSAL NAMES ADR-0139 AND PRINTS THE REMEDY. The acceptance asks for both
     separately and it is right to: a refusal that only says no moves the confusion rather
     than ending it. The remedy is the mint, and the `--source` it prints is the CLOSED
     ITEM'S REPO-RELATIVE PATH — D2's half of the ruling, and the half that is easy to
     drop. `source:` is resolved as a filesystem path by `_triage_debt`, so a remedy that
     printed a bare `WI-0264` would hand the operator a permanently dead citation and
     manufacture startup debt in the act of fixing something.

  C. IT IS NARROW, WITH ITS CONTROLS. Terminal -> non-terminal only. An idempotent re-run
     of the close, a reclassification between two closed states, and every ordinary
     transition out of a live status all still work. A guard that fires on correct use is
     the one that gets routed around and then deleted.

  D. THE OPS STORE IS DELIBERATELY NOT COVERED. `cmd_ops_status` has the identical shape
     and `OPS_TERMINAL` the identical vocabulary, so its absence has to be a decision on
     the record rather than a gap nobody noticed. D1's argument is that `done` is read by
     the release machinery and every "what shipped" view; an obligation carries no version
     axis and appears in none of them, so the argument does not transfer.

stdlib unittest: python3 -m unittest discover -s tests
"""

import argparse
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import session  # noqa: E402
from ambient_fixture import neutralize_ambient_env  # noqa: E402
from coord_fixture import neutralize_coord_journal  # noqa: E402

GIT = shutil.which("git")


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _item_text(wid, title, status="open"):
    return (f"# {wid}: {title}\n\n"
            f"- status: {status}\n- section: next\n- blocked-by: \n"
            f"- group: \n- source: \n- impact: fix\n- version: \n\n"
            f"the body of {wid}.\n")


@unittest.skipUnless(GIT, "git required")
class TerminalBase(unittest.TestCase):
    """A real store in a real repo — `cmd_wi_status` writes and commits through it."""

    def setUp(self):
        neutralize_ambient_env(self)
        neutralize_coord_journal(self)
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.main = self.tmp / "repo"
        (self.main / "work-items").mkdir(parents=True)
        _git(self.main, "init", "-q", "-b", "main")
        _git(self.main, "config", "user.email", "t@t")
        _git(self.main, "config", "user.name", "t")
        _git(self.main, "config", "commit.gpgsign", "false")
        for wid, title, status in (
            ("WI-0001", "a live item", "open"),
            ("WI-0002", "a closed item", "done"),
            ("WI-0003", "an absorbed item", "superseded"),
        ):
            (self.main / "work-items" / f"{wid}-{title.replace(' ', '-')}.md").write_text(
                _item_text(wid, title, status), encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "seed")
        # The ordinary live->done control has real delivery evidence. Without it,
        # the new completion guard rightly refuses that one transition.
        (self.main / "delivered.txt").write_text("work for WI-0001\n")
        _git(self.main, "add", "delivered.txt")
        _git(self.main, "commit", "-qm", "feat: deliver WI-0001")

        self._save = {k: getattr(session, k) for k in ("ROOT", "CFG")}
        self._env = dict(os.environ)
        self.addCleanup(self._restore)
        session.ROOT = self.main
        session.CFG = {"tz": ZoneInfo("UTC"), "machine_map": {},
                       "architect_name": "Test", "architect_id": "test-arch"}
        os.environ["CLAUDE_CODE_SESSION_ID"] = "terminaltest"

    def _restore(self):
        for k, v in self._save.items():
            setattr(session, k, v)
        os.environ.clear()
        os.environ.update(self._env)

    def _status(self, wid, **kw):
        kw = {"id": wid, "status": None, "section": None, "blocked_by": None,
              "source": None, "impact": None, "migration": None, **kw}
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                session.cmd_wi_status(argparse.Namespace(**kw))
                code = 0
            except SystemExit as e:
                code = e.code
        return code, buf.getvalue()

    def _item(self, wid):
        return {it["id"]: it for it in session._wi_parse()}[wid]

    def _reset(self, wid, status):
        """Put `wid` back to a known status by writing the file, NOT by another
        `wi-status` call — the verb under test cannot be the fixture that sets up the
        control for it."""
        item = self._item(wid)
        item["status"] = status
        session._wi_write_item(item)


class ADoneItemCannotBeReopenedTest(TerminalBase):
    """Property A."""

    def test_done_to_open_is_refused(self):
        code, out = self._status("WI-0002", status="open")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0002")["status"], "done",
                         "the refusal must leave the item as it was")
        self.assertIn("TERMINAL", out)

    def test_superseded_to_open_is_refused_too(self):
        """`done` is the one the acceptance names; `superseded` is terminal by the same
        ruling and would otherwise be the way around the refusal."""
        code, _out = self._status("WI-0003", status="open")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0003")["status"], "superseded")

    def test_done_to_in_progress_and_to_held_are_refused(self):
        for target in ("in-progress", "held"):
            with self.subTest(target=target):
                code, _ = self._status("WI-0002", status=target)
                self.assertEqual(code, 2)
                self.assertEqual(self._item("WI-0002")["status"], "done")

    def test_it_refuses_before_ANY_field_is_written(self):
        """Never half-apply. The refusal sits above every assignment in `cmd_wi_status`,
        so a call that reopens AND re-sections leaves the section alone as well — which a
        refusal placed just before the write would not."""
        before = self._item("WI-0002")
        code, _ = self._status("WI-0002", status="open", section="backlog",
                               impact="feature")
        self.assertEqual(code, 2)
        after = self._item("WI-0002")
        self.assertEqual(after["section"], before["section"])
        self.assertEqual(after["impact"], before["impact"])


class TheRefusalTeachesTheRemedyTest(TerminalBase):
    """Property B — the half that keeps the refusal from just moving the confusion."""

    def test_it_names_the_ruling(self):
        _code, out = self._status("WI-0002", status="open")
        self.assertIn("ADR-0139", out)

    def test_it_prints_the_mint_fresh_remedy(self):
        _code, out = self._status("WI-0002", status="open")
        self.assertIn("poga work new", out)
        self.assertIn("--source", out)

    def test_the_remedy_cites_the_repo_relative_PATH_not_a_bare_id(self):
        """D2, and the half that is easy to drop. `source:` is resolved as a filesystem
        path, so a remedy printing a bare `WI-0002` would hand over a permanently dead
        citation and manufacture startup debt in the act of fixing something."""
        _code, out = self._status("WI-0002", status="open")
        name = session._wi_filename_for("WI-0002")
        self.assertIsNotNone(name)
        self.assertIn(f"--source {session.WI_DIRNAME}/{name}", out)

    def test_it_says_a_FALSE_close_is_also_a_mint(self):
        """The case an operator reaches for the reopen hardest — the wrong id, or work
        that never shipped. D4 answers it, and a refusal silent on it invites the
        hand-edit that the whole store exists to remove."""
        _code, out = self._status("WI-0002", status="open")
        self.assertIn("FALSE", out)
        self.assertIn("ADR-0139 D4", out)


class TheGuardIsNarrowTest(TerminalBase):
    """Property C — every control the refusal must NOT fire on."""

    def test_re_closing_a_done_item_still_works(self):
        code, _ = self._status("WI-0002", status="done")
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0002")["status"], "done")

    def test_done_to_superseded_is_a_reclassification_not_a_reopen(self):
        code, _ = self._status("WI-0002", status="superseded")
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0002")["status"], "superseded")

    def test_superseded_to_done_works_the_same_way(self):
        code, _ = self._status("WI-0003", status="done")
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0003")["status"], "done")

    def test_every_transition_out_of_a_live_status_is_untouched(self):
        # Each case starts from a KNOWN live status, restored on disk rather than by
        # another `wi-status` call — walking the list in place left the item terminal
        # partway through and the loop then measured the refusal instead of the control,
        # which is the shape a relative predicate over shared state always takes.
        for live in ("open", "in-progress", "held"):
            for target in ("in-progress", "held", "done", "superseded", "open"):
                with self.subTest(live=live, target=target):
                    self._reset("WI-0001", live)
                    code, out = self._status("WI-0001", status=target)
                    self.assertEqual(code, 0, out)
                    self.assertEqual(self._item("WI-0001")["status"], target)

    def test_a_status_free_edit_to_a_done_item_still_works(self):
        """Closing an item must not freeze the rest of its record: a done item's section,
        `blocked-by` and release labels stay editable."""
        code, _ = self._status("WI-0002", section="backlog", impact="feature")
        self.assertEqual(code, 0)
        self.assertEqual(self._item("WI-0002")["section"], "backlog")


class ItRefusesAREALReopenFromThisRepoSHistoryTest(TerminalBase):
    """The detector proven on the defect that produced it, not on a case I invented.

    A guard that has only ever met hand-written inputs has been tested against my model of
    the bug. This one is run against the actual item file, taken verbatim from the commit
    before a reopen the trunk really made
    ([`a-detector-proves-itself-on-the-real-defect`](../habits/master.md#a-detector-proves-itself-on-the-real-defect)).

    WI-0344 went `done` -> `open` on the trunk on 2026-09-12, in `a0dd8753`, five days
    before ADR-0139 was accepted. It is one of SIX such transitions in the first-parent
    history of `work-items/` — measured, because the item's own note cites one and names a
    different pair. The other five are WI-0006, WI-0010, WI-0037, WI-0061 and WI-0097;
    ADR-0139 D4 accounts for two of them as history and stands by them, which this does not
    disturb.

    SKIPS rather than fails where the commit is unreachable — a shallow clone, or the
    public mirror, is not a broken guard."""

    REOPEN = "a0dd8753"
    PATH = "work-items/WI-0344-a-test-run-piped-to-tail-reports-a-false.md"

    def _blob(self, rev):
        r = subprocess.run(
            ["git", "-C", str(pathlib.Path(__file__).resolve().parent.parent),
             "show", f"{rev}:{self.PATH}"], capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else None

    def test_the_verb_refuses_the_transition_that_actually_happened(self):
        before = self._blob(f"{self.REOPEN}^")
        after = self._blob(self.REOPEN)
        if before is None or after is None:
            self.skipTest(f"{self.REOPEN} is not reachable from this checkout")
        # The fixture is the REAL file, and the control is the real commit: it must be a
        # done -> open transition, or this test is pointed at the wrong thing.
        self.assertIn("- status: done", before)
        self.assertIn("- status: open", after)

        (self.main / self.PATH).write_text(before, encoding="utf-8")
        _git(self.main, "add", "-A")
        _git(self.main, "commit", "-qm", "the store as it stood before the reopen")

        code, out = self._status("WI-0344", status="open")
        self.assertEqual(code, 2)
        self.assertEqual(self._item("WI-0344")["status"], "done")
        self.assertIn("ADR-0139", out)
        # The remedy names THIS item's real path, not the fixture items' — the citation is
        # only useful if it resolves to the file actually in front of the operator.
        self.assertIn(self.PATH, out)


class TheOpsStoreIsExcludedOnPurposeTest(TerminalBase):
    """Property D. Recorded as a decision, so the next reader does not have to re-derive
    whether the sibling store was considered."""

    def test_the_refusal_is_wired_only_into_the_work_item_verb(self):
        src = pathlib.Path(session.__file__).resolve().parent
        wired = [p.name for p in (src / "sessionlib").glob("*.py")
                 if "_refuse_reopen(" in p.read_text(encoding="utf-8")]
        self.assertEqual(wired, ["store.py"])
        text = (src / "sessionlib" / "store.py").read_text(encoding="utf-8")
        ops = text[text.index("def cmd_ops_status"):]
        self.assertNotIn("_refuse_reopen", ops[:ops.index("\ndef ", 1)],
                         "if the ops verb ever gains this refusal, D1's argument has to "
                         "be re-made for it — an obligation has no release axis")


if __name__ == "__main__":
    unittest.main()
