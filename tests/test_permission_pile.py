"""Tests for the permission-pile visibility line (WI-0023).

The pile is `.claude/settings.local.json` — where Claude Code records every "yes, allow
that" and from which nothing is ever removed. One member's had grown to tens of KB,
and nobody has ever read one, because no surface has ever mentioned it.

What these pin, in order of what would actually hurt if it broke:

  1. ABSENT, UNREADABLE and SMALL are three different answers, not two. A pile that
     cannot be parsed must never come back looking like a pile that is fine
     (`declare-what-a-check-assumes`); the whole point of the line is to be trusted when
     it is silent.
  2. The line stays SILENT below the threshold. A banner line that prints on every start
     in every member forever is one its reader learns to skip, which costs more than it
     buys — so "quiet" is a tested property, not an implementation detail.
  3. No per-entry age is ever claimed. The file is a bare list of strings carrying no
     timestamps, so an "oldest entry" figure could only be invented
     (`no-fabricated-data`). The mtime IS reported, under its own honest name.

Each threshold case is driven from BOTH sides — below stays quiet, at/above speaks — so
a comparison flipped to `<=` fails here rather than shipping.

stdlib unittest: python3 -m unittest discover -s tests
"""

import json
import pathlib
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
import session  # noqa: E402


def _pile(tmp, allow=(), deny=(), ask=(), raw=None):
    """Write a settings.local.json into `tmp` and return its path."""
    p = pathlib.Path(tmp) / "settings.local.json"
    if raw is not None:
        p.write_text(raw, encoding="utf-8")
        return p
    doc = {"permissions": {"allow": list(allow), "deny": list(deny), "ask": list(ask)}}
    p.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return p


class PermissionPileStatsTest(unittest.TestCase):
    """The measurement itself — the three outcomes and the exact-path ratio."""

    def test_absent_pile_is_none_not_an_empty_measurement(self):
        """No file at all is a real, clean answer and must be distinguishable from a
        pile measured as empty — otherwise "nothing accumulated here" and "I read it and
        found nothing" collapse into one state and the caller cannot tell them apart."""
        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "settings.local.json"
            self.assertFalse(missing.exists())
            self.assertIsNone(session._permission_pile_stats(missing))

    def test_unparseable_pile_reports_unreadable_never_clean(self):
        """A present-but-broken file must not read as a small, healthy pile."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _pile(tmp, raw="{not json at all")
            stats = session._permission_pile_stats(p)
            self.assertIsNotNone(stats)
            self.assertFalse(stats["readable"])
            self.assertNotIn("total", stats)

    def test_readable_pile_counts_every_bucket(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _pile(tmp,
                      allow=["Bash(git status)", "Bash(git log:*)"],
                      deny=["Bash(rm -rf *)"],
                      ask=["Bash(curl:*)"])
            stats = session._permission_pile_stats(p)
            self.assertTrue(stats["readable"])
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["allow"], 2)
            self.assertEqual(stats["deny"], 1)
            self.assertEqual(stats["ask"], 1)

    def test_exact_counts_only_wildcardless_rules(self):
        """`Bash(git status)` can match exactly one invocation forever; `Bash(git
        status:*)` is a standing grant. Conflating them would make the ratio — the only
        reviewable signal in the line — meaningless."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _pile(tmp, allow=["Bash(git status)",          # exact
                                  "Read(/tmp/one/file.txt)",   # exact
                                  "Bash(git log:*)",           # wildcard
                                  "Bash(python3 *)"])          # wildcard
            stats = session._permission_pile_stats(p)
            self.assertEqual(stats["total"], 4)
            self.assertEqual(stats["exact"], 2)

    def test_missing_permissions_block_measures_as_empty_not_unreadable(self):
        """Valid JSON with no permissions key is genuinely an empty pile — readable."""
        with tempfile.TemporaryDirectory() as tmp:
            p = _pile(tmp, raw=json.dumps({"hooks": {}}))
            stats = session._permission_pile_stats(p)
            self.assertTrue(stats["readable"])
            self.assertEqual(stats["total"], 0)

    def test_non_string_rules_are_skipped_without_raising(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _pile(tmp, raw=json.dumps(
                {"permissions": {"allow": ["Bash(git status)", 17, None, {"a": 1}]}}))
            stats = session._permission_pile_stats(p)
            self.assertEqual(stats["total"], 1)


class PermissionPileLineTest(unittest.TestCase):
    """The banner line — when it speaks, when it stays quiet, and what it claims."""

    def _line(self, stats, now=None):
        with mock.patch.object(session, "_permission_pile_stats", return_value=stats):
            return session._permission_pile_line(now=now)

    def test_absent_pile_is_silent(self):
        self.assertEqual(self._line(None), "")

    def test_unreadable_pile_speaks_and_says_it_is_not_small(self):
        line = self._line({"readable": False, "size": None, "mtime": None})
        self.assertIn("UNREADABLE", line)
        self.assertIn("not the same as small", line)

    def test_small_pile_is_silent_on_both_axes(self):
        rules, byte_max = session._permission_pile_thresholds()
        self.assertEqual(self._line({"readable": True, "total": rules - 1,
                                     "exact": 0, "size": byte_max - 1,
                                     "mtime": time.time()}), "")

    def test_rule_count_at_the_threshold_speaks(self):
        """Driven at the boundary from both sides, so a `<` flipped to `<=` fails."""
        rules, _ = session._permission_pile_thresholds()
        stats = {"readable": True, "total": rules, "exact": 3,
                 "size": 10, "mtime": time.time()}
        self.assertIn("perms:", self._line(stats))
        stats["total"] = rules - 1
        self.assertEqual(self._line(stats), "")

    def test_byte_size_alone_can_trip_the_line(self):
        """A pile of few but enormous rules is still a pile worth seeing."""
        _, byte_max = session._permission_pile_thresholds()
        stats = {"readable": True, "total": 1, "exact": 1,
                 "size": byte_max, "mtime": time.time()}
        self.assertIn("perms:", self._line(stats))
        stats["size"] = byte_max - 1
        self.assertEqual(self._line(stats), "")

    def test_line_reports_the_exact_path_count(self):
        rules, _ = session._permission_pile_thresholds()
        line = self._line({"readable": True, "total": rules + 40, "exact": 31,
                           "size": 40 * 1024, "mtime": time.time()})
        self.assertIn(f"{rules + 40} accumulated rule(s)", line)
        self.assertIn("31 are exact-path one-offs", line)

    def test_line_reports_mtime_age_as_when_the_pile_grew(self):
        """The mtime is a real fact about the FILE. It is reported as "last grew", never
        as the age of any entry — the entries carry no timestamps at all."""
        now = 1_000_000.0
        line = self._line({"readable": True, "total": 500, "exact": 400,
                           "size": 60 * 1024, "mtime": now - (9 * 86400)}, now=now)
        self.assertIn("last grew 9d ago", line)

    def test_line_never_claims_a_per_entry_age(self):
        """`no-fabricated-data`: the pile has no per-entry timestamps, so the line must
        say so rather than imply an oldest-entry figure it cannot possibly have."""
        line = self._line({"readable": True, "total": 500, "exact": 400,
                           "size": 60 * 1024, "mtime": time.time()})
        self.assertIn("No per-entry ages exist", line)
        self.assertNotIn("oldest", line.lower())

    def test_a_broken_mtime_degrades_to_no_age_rather_than_raising(self):
        line = self._line({"readable": True, "total": 500, "exact": 400,
                           "size": 60 * 1024, "mtime": None})
        self.assertIn("perms:", line)
        self.assertNotIn("last grew", line)


class PermissionPileThresholdConfigTest(unittest.TestCase):
    """Per-system override, so a member with a legitimately larger pile can raise its own
    bar instead of being nagged forever or patching the shared harness."""

    def test_defaults_when_unconfigured(self):
        with mock.patch.dict(session.CFG, {}, clear=False):
            session.CFG.pop("permission_pile", None)
            self.assertEqual(session._permission_pile_thresholds(),
                             (session.PERMISSION_PILE_RULES,
                              session.PERMISSION_PILE_BYTES))

    def test_config_overrides_are_honoured(self):
        with mock.patch.dict(session.CFG,
                             {"permission_pile": {"rules": 7, "bytes": 99}}):
            self.assertEqual(session._permission_pile_thresholds(), (7, 99))

    def test_garbage_config_falls_back_rather_than_raising(self):
        """A member that writes nonsense here gets the default, not a broken start —
        the banner must never be the thing that fails."""
        for bad in ({"rules": "lots"}, {"rules": -3}, {"rules": None}, "not-a-dict"):
            with mock.patch.dict(session.CFG, {"permission_pile": bad}):
                self.assertEqual(session._permission_pile_thresholds()[0],
                                 session.PERMISSION_PILE_RULES)


class PermissionPileRealRepoTest(unittest.TestCase):
    """End-to-end against a real file on disk, not a mocked stats dict — the parse, the
    byte count and the threshold have to agree with each other for the line to be true."""

    def test_a_genuinely_large_pile_on_disk_produces_the_line(self):
        """Resolved through `ROOT / PERMISSION_PILE`, the way the start path does it —
        so the default path, the parse, the byte count and the threshold all have to
        agree, not just the arithmetic on a hand-built dict."""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".claude").mkdir()
            rules, _ = session._permission_pile_thresholds()
            doc = {"permissions": {
                "allow": [f"Bash(some-command-{i})" for i in range(rules + 5)]}}
            (root / session.PERMISSION_PILE).write_text(
                json.dumps(doc, indent=2), encoding="utf-8")
            with mock.patch.object(session, "ROOT", root):
                line = session._permission_pile_line()
            self.assertIn("perms:", line)
            self.assertIn(f"{rules + 5} accumulated rule(s)", line)
            # every rule here is wildcardless, so all of them are dead one-offs
            self.assertIn(f"{rules + 5} are exact-path one-offs", line)

    def test_a_repo_with_no_pile_at_all_stays_silent_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(session, "ROOT", pathlib.Path(tmp)):
                self.assertEqual(session._permission_pile_line(), "")

    def test_this_repos_own_pile_is_measured_without_raising(self):
        """Whatever state this checkout is in — pile, no pile, unreadable pile — asking
        must produce an answer rather than an exception, because it runs on the start
        path where a raise would cost the banner."""
        line = session._permission_pile_line()
        self.assertIsInstance(line, str)


if __name__ == "__main__":
    unittest.main()
