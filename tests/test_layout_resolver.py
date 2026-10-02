"""Tests for the ONE layout resolver (WI-0029).

The item's open half was never "fix another path" — it was *how does a member declare
its layout without the substrate growing a key per assumption?* Every instance before
this was closed by adding one top-level config key, and each key only ever reached the
one call site that read it. `curate/metrics.py` is the proof: `_handoff_name` derived
the handoff filename from the member's own config while `_briefs_root`, twelve lines
below, read the federation's `proposed-edits` literal — so a member on another layout
mined zero briefs and reported `0 briefs waiting`, a wrong answer shaped like a clean
one.

So the tests that matter here are not "does the default still work" (it must, and that
is asserted below) but:

  * the `layout` block is actually consulted — a member that declares ONLY in the block
    is honoured. This is the case that FAILS against the pre-fix code, where each reader
    looked at its own top-level key and nothing else.
  * every consumer resolves through the same door, so a declaration reaches all of them
    at once rather than whichever one was patched most recently.
  * "not declared" stays distinguishable from "declared as the default"
    (`declare-what-a-check-assumes`) — the empty-default keys return "" rather than a
    guess, and `read_member_config` returns None rather than {} for an absent config.

stdlib unittest: python3 -m unittest discover -s tests
"""

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import session as m  # noqa: E402

BASE_CFG = {
    "architect_name": "Architect", "architect_id": "member-arch", "user_name": "operator",
    "role_doc": "role.md", "handoff": "handoff.md", "timezone": "UTC",
    "machine_map": {"anything": "TestBox"},
}


class LayoutResolverTest(unittest.TestCase):
    """`layout_of` — pure, so it can be exercised without building a repo."""

    def test_default_when_nothing_is_declared(self):
        """The regression that protects every member: an untouched config keeps the
        federation shape, so this change is inert until someone opts in."""
        self.assertEqual(m.layout_of({}, "handoff"), "session-handoff.md")
        self.assertEqual(m.layout_of({}, "adr_dir"), "adr")
        self.assertEqual(m.layout_of({}, "work_items"), "work-items")
        self.assertEqual(m.layout_of({}, "journal_dir"), "sessions/journal")
        self.assertEqual(m.layout_of({}, "status_file"), "STATUS.md")

    def test_legacy_top_level_key_still_wins_over_the_default(self):
        """Every member ships these today; none may break."""
        self.assertEqual(m.layout_of({"handoff": "hand.md"}, "handoff"), "hand.md")
        self.assertEqual(m.layout_of({"adr_dir": "docs/adr"}, "adr_dir"), "docs/adr")

    def test_the_layout_block_is_consulted(self):
        """THE case that fails against the pre-fix code — there, each reader consulted
        its own top-level key and a `layout` block reached nothing."""
        cfg = {"layout": {"adr_dir": "docs/adr", "inbox": "mail/pending"}}
        self.assertEqual(m.layout_of(cfg, "adr_dir"), "docs/adr")
        self.assertEqual(m.layout_of(cfg, "inbox"), "mail/pending")

    def test_the_block_beats_the_legacy_key(self):
        """Most-specific-wins. A member mid-migration declares both; the block is the
        deliberate statement, so it is the one that counts."""
        cfg = {"adr_dir": "adr", "layout": {"adr_dir": "docs/adr"}}
        self.assertEqual(m.layout_of(cfg, "adr_dir"), "docs/adr")

    def test_no_honest_default_returns_empty_not_a_guess(self):
        """`inbox` absent means NO mailbox — materially different from a mailbox at a
        default path, and folding them together is how a member with no inbox gets
        reported as having an empty one."""
        self.assertEqual(m.layout_of({}, "inbox"), "")
        self.assertEqual(m.layout_of({}, "user_profile"), "")
        self.assertEqual(m.layout_of({}, "role_doc"), "")
        # ADR-0123: undeclared `changelog_doc` means INLINE — the changelog is a section
        # of the role doc — which is a different fact from "declared, and it is this
        # file". `curate/deliver.py` reads the two as different resolution paths, so a
        # default filename here would silently send it looking for a file no member has.
        self.assertEqual(m.layout_of({}, "changelog_doc"), "")

    def test_a_declared_changelog_doc_is_honoured_in_both_tiers(self):
        """ADR-0123 splits the federation's CHANGELOG into a tracked sibling. The key is
        resolved through this ONE door — never by a second copy of the precedence rules in
        `curate/deliver.py` — so a member declaring it in the `layout` block and a member
        declaring it top-level both reach every reader."""
        self.assertEqual(m.layout_of({"changelog_doc": "arch-CHANGELOG.md"},
                                     "changelog_doc"), "arch-CHANGELOG.md")
        self.assertEqual(m.layout_of({"layout": {"changelog_doc": "history/log.md"}},
                                     "changelog_doc"), "history/log.md")
        self.assertEqual(m.layout_of({"changelog_doc": "legacy.md",
                                      "layout": {"changelog_doc": "block.md"}},
                                     "changelog_doc"), "block.md")

    def test_unknown_key_is_no_opinion_not_an_exception(self):
        """A lookup must never be able to break a status probe."""
        self.assertEqual(m.layout_of({}, "not_a_layout_key"), "")

    def test_junk_config_degrades_to_the_default(self):
        """None, a non-dict, a non-dict `layout`, and a non-string declaration are all
        'nothing declared' — never a crash, never a partially-applied value."""
        for cfg in (None, [], "nope", {"layout": "nope"}, {"layout": {"adr_dir": 7}},
                    {"adr_dir": None}, {"adr_dir": ""}):
            self.assertEqual(m.layout_of(cfg, "adr_dir"), "adr", f"cfg={cfg!r}")


class ReadMemberConfigTest(unittest.TestCase):

    def test_absent_config_is_none_not_empty(self):
        """None and {} must stay distinguishable: 'there is no config here' is a
        different fact from 'a config that declares nothing', and a caller reporting on
        a member needs to be able to say which."""
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(m.read_member_config(pathlib.Path(d)))

    def test_malformed_config_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            (repo / "session.config.json").write_text("{ not json")
            self.assertIsNone(m.read_member_config(repo))

    def test_member_layout_reads_and_resolves(self):
        with tempfile.TemporaryDirectory() as d:
            repo = pathlib.Path(d)
            cfg = dict(BASE_CFG, layout={"adr_dir": "docs/adr"})
            (repo / "session.config.json").write_text(json.dumps(cfg))
            self.assertEqual(m.member_layout(repo, "adr_dir"), "docs/adr")
            self.assertEqual(m.member_layout(repo, "work_items"), "work-items")

    def test_member_layout_on_a_repo_with_no_config_gives_the_default(self):
        """The miner walks ten checkouts; one without a config must not except."""
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(m.member_layout(pathlib.Path(d), "handoff"),
                             "session-handoff.md")


class OneDoorForEveryConsumerTest(unittest.TestCase):
    """The duplication half: the federation-side miner must resolve a member's layout
    through the SAME function the member's own harness uses, not a private copy with its
    own fallbacks. These pin that the two readers which had diverged now agree."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "curate"))
        import metrics  # noqa: PLC0415
        self.metrics = metrics

    def _repo(self, d, cfg):
        repo = pathlib.Path(d)
        (repo / "session.config.json").write_text(json.dumps(cfg))
        return repo

    def test_handoff_reader_honours_the_block(self):
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, dict(BASE_CFG, layout={"handoff": "log/hand.md"}))
            self.assertEqual(self.metrics._handoff_name(repo), "log/hand.md")

    def test_brief_root_honours_the_block(self):
        """The one that was broken for weeks. Declared only in the block, so under the
        pre-fix reader this fell through to the federation's `proposed-edits` literal."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, dict(BASE_CFG,
                                      layout={"inbox": "mail/member-arch/pending"}))
            root, declared = self.metrics._briefs_root(repo)
            self.assertEqual(root, repo / "mail")
            self.assertTrue(declared, "a declaration must read as checked, not assumed")

    def test_undeclared_inbox_still_reports_assumed(self):
        """`declared=False` is the honest half — the caller can tell 'checked' from
        'fell back', which is what stopped `0 briefs waiting` being a silent lie."""
        with tempfile.TemporaryDirectory() as d:
            repo = self._repo(d, dict(BASE_CFG))
            root, declared = self.metrics._briefs_root(repo)
            self.assertEqual(root, repo / "proposed-edits")
            self.assertFalse(declared)


if __name__ == "__main__":
    unittest.main()
