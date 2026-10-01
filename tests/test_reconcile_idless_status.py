"""WI-0373 — an id-less STATUS.md is reported as id-less, at its real path.

ONE INPUT, TWO ENTRY POINTS, TWO OPPOSITE WRONG ANSWERS. A `STATUS.md` carrying no
`id:` in its frontmatter used to be:

  * dropped by `find_status_files()` (`sid = fm.get("id"); if not sid: continue`), after
    which the member fell out of `found`, into `classify_absent()`'s `absent`, and out
    under **UNLOCATED** — whose prose reads *"not reachable from here … add the repo's
    path to repo-paths.local"*. So the one surface holding the evidence — a broken file
    at a real path on this disk — emitted a confident instruction to go look on another
    machine. Over-reported as a missing repo.
  * waved through `resolve_mapped()` by a truthy guard (`if live_id and live_id != sid`)
    and filed into `found` as a healthy member. Under-reported as converged.

The drop was always SURFACED. The diagnosis was wrong, which is worse than silence in
the way an in-range line citation is worse than an out-of-range one — it is actionable,
and following it costs a trip to the wrong machine.

Every test below runs against a REAL id-less file on disk rather than a stubbed locator
return, because the defect lived in the read itself: a fake that hands back `{}` proves
only that the report renders an empty dict the way the test author expected
([`verify-in-the-created-configuration`]).

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import io
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("reconcile", ROOT / "curate" / "reconcile.py")
reconcile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reconcile)

IDLESS = "---\nversion: 3.5.1\nlast_active: 2026-09-17\n---\n\n# STATUS\n"
WITH_ID = "---\nid: sample-svc\nversion: 1.0.0\nlast_active: 2026-09-17\n---\n"


class _Fixture(unittest.TestCase):
    """A machine with one member repo whose STATUS.md lost its `id:` line."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # `.resolve()`: on macOS `tempfile` hands back `/var/...`, which is a symlink to
        # `/private/var/...`, and `find_status_files` resolves each root before walking.
        # Without this the fixture's own idea of "its real path" differs from the one the
        # report prints, and an `assertIn` would have passed on the substring anyway —
        # i.e. the loose assertion hid the mismatch rather than the code being right.
        self.base = pathlib.Path(self._tmp.name).resolve()
        self.root = self.base / "Projects"
        self.root.mkdir()
        self.member_repo = self.root / "sample-member"
        self.member_repo.mkdir()
        self.status = self.member_repo / "STATUS.md"
        self.status.write_text(IDLESS, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _repo(self, name, body):
        r = self.root / name
        r.mkdir(exist_ok=True)
        (r / "STATUS.md").write_text(body, encoding="utf-8")
        return r


class BothEntryPointsTest(_Fixture):
    """The same file, read the two ways the locator can reach it."""

    def test_the_walk_reports_it_rather_than_dropping_it(self):
        seen, idless = reconcile.find_status_files([str(self.root)])
        self.assertEqual(seen, {}, "an id-less file must not be located under any id")
        self.assertEqual([p for p, _r in idless], [self.status],
                         "the file must be reported AT ITS REAL PATH")

    def test_the_map_calls_it_broken_rather_than_trusting_the_key(self):
        found, broken = reconcile.resolve_mapped({"orbit": str(self.member_repo)})
        self.assertEqual(found, {}, "the map key is not a substitute for a self-report")
        self.assertIn("orbit", broken)
        raw, reason = broken["orbit"]
        self.assertEqual(raw, str(self.member_repo))
        self.assertIn("no id", reason)

    def test_the_two_entry_points_do_not_disagree_about_the_same_file(self):
        """The regression this item exists to prevent, stated as one assertion.

        Before the fix these two calls returned OPPOSITE verdicts on this exact file:
        the walk said 'no such member here', the map said 'member present and fine'.
        Neither is 'this file is broken', which is the only true answer.
        """
        seen, idless = reconcile.find_status_files([str(self.root)])
        found, broken = reconcile.resolve_mapped({"orbit": str(self.member_repo)})
        self.assertNotIn("orbit", seen)
        self.assertNotIn("orbit", found)
        self.assertTrue(idless and broken, "neither surface may stay silent")

    def test_a_sibling_with_an_id_still_locates_normally(self):
        """The negative control. A fix that reported EVERY file as id-less would pass
        every assertion above, so the walk must still find a well-formed member."""
        self._repo("sample-svc", WITH_ID)
        seen, idless = reconcile.find_status_files([str(self.root)])
        self.assertEqual(list(seen), ["sample-svc"])
        self.assertEqual([p for p, _r in idless], [self.status])


class SelfEntryTest(_Fixture):
    """The third locator, same shape — `self_entry()` read the hub's own STATUS.md and
    returned `{}` when it had no id, so the federation reported ITSELF as unlocated:
    'the live repo isn't reachable from this machine', about the tree the script is
    executing in."""

    def test_our_own_id_less_status_is_reported_not_dropped(self):
        with mock.patch.object(reconcile, "_MAIN_CHECKOUT", self.member_repo):
            entry, idless = reconcile.self_entry()
        self.assertEqual(entry, {})
        self.assertEqual([p for p, _r in idless], [self.status])

    def test_a_well_formed_self_status_still_resolves(self):
        r = self._repo("hub", "---\nid: federation\nversion: 2.73.0\n---\n")
        with mock.patch.object(reconcile, "_MAIN_CHECKOUT", r):
            entry, idless = reconcile.self_entry()
        self.assertEqual(list(entry), ["federation"])
        self.assertEqual(idless, [])


class ReasonsAreDistinctTest(_Fixture):
    """`parse_frontmatter` returns `{}` for a file with no header AND for one whose
    header parsed to nothing, so 'frontmatter carries no `id:`' would point a reader at
    a line that does not exist ([`declare-what-a-check-assumes`])."""

    def test_a_missing_header_and_a_header_without_id_read_differently(self):
        self.status.write_text("# STATUS\n\nplain prose, no frontmatter\n",
                               encoding="utf-8")
        headerless = reconcile.find_status_files([str(self.root)])[1][0][1]
        self.status.write_text(IDLESS, encoding="utf-8")
        no_id = reconcile.find_status_files([str(self.root)])[1][0][1]
        self.assertNotEqual(headerless, no_id)
        self.assertIn("no frontmatter block", headerless)
        self.assertIn("no `id:`", no_id)


class BannerTest(_Fixture):
    """`--status`, the line a session actually sees at startup."""

    def _line(self, roster, roots=(), repo_paths=None):
        with mock.patch.object(reconcile, "read_roots_config",
                               lambda warn=True: [str(r) for r in roots]), \
                mock.patch.object(reconcile, "read_repo_paths",
                                  lambda: repo_paths or {}), \
                mock.patch.object(reconcile, "self_entry", lambda: ({}, [])), \
                mock.patch.object(reconcile, "roster_ids", lambda: set(roster)), \
                mock.patch.object(reconcile, "roster_residency", lambda: {}), \
                mock.patch.object(reconcile, "this_machine", lambda: "DevBox"):
            return reconcile.status_line()

    def test_the_banner_counts_id_less_separately_from_unlocated(self):
        line = self._line({"orbit"}, roots=[self.root])
        self.assertIn("1 id-less STATUS.md", line)

    def test_a_broken_map_member_is_no_longer_also_counted_unlocated(self):
        """The double count WI-0373 exposed: a member whose path IS mapped has been
        located — what failed is the file at the end of it, already reported as
        broken-map with its real reason. Counting it as unlocated as well printed one
        member twice under two contradictory diagnoses."""
        line = self._line({"orbit"}, repo_paths={"orbit": str(self.member_repo)})
        self.assertIn("1 broken-map (orbit)", line)
        self.assertNotIn("unlocated", line)


class FullReportTest(_Fixture):
    """The human report — the surface whose prose sent the reader to the wrong machine."""

    def _report(self, roster, roots=(), repo_paths=None):
        buf = io.StringIO()
        argv = ["reconcile.py", *[str(r) for r in roots]]
        with mock.patch.object(sys, "argv", argv), \
                mock.patch.object(reconcile, "read_repo_paths",
                                  lambda: repo_paths or {}), \
                mock.patch.object(reconcile, "read_roots_config",
                                  lambda warn=True: []), \
                mock.patch.object(reconcile, "self_entry", lambda: ({}, [])), \
                mock.patch.object(reconcile, "roster_ids", lambda: set(roster)), \
                mock.patch.object(reconcile, "roster_residency", lambda: {}), \
                mock.patch.object(reconcile, "git_staleness", lambda d: None), \
                mock.patch.object(reconcile, "this_machine", lambda: "DevBox"), \
                mock.patch.object(sys, "stdout", buf):
            reconcile.main()
        return buf.getvalue()

    def test_the_walked_file_gets_its_own_section_naming_the_real_path(self):
        out = self._report({"orbit"}, roots=[self.root])
        self.assertIn("ID-LESS STATUS.md", out)
        self.assertIn(str(self.status), out)

    def test_the_section_says_fix_the_file_not_the_locator(self):
        out = self._report({"orbit"}, roots=[self.root])
        head = out.split("ID-LESS STATUS.md", 1)[1].split("\n\n", 1)[0]
        self.assertIn("id: <system-id>", head)
        self.assertIn("nothing in repo-paths.local", head)

    def test_the_unlocated_advice_no_longer_stands_alone_when_a_file_is_id_less(self):
        """The core of the finding. `orbit` is still unlocated — an id-less file names
        no member, so it CANNOT be attributed — but the report may no longer leave the
        'look on another machine' advice as the reader's only lead when a broken file
        is sitting right here.
        """
        out = self._report({"orbit"}, roots=[self.root])
        self.assertIn("UNLOCATED", out)
        block = out.split("UNLOCATED", 1)[1]
        self.assertIn("BEFORE looking on another machine", block)
        self.assertIn("broken rather than absent", block)

    def test_that_note_is_absent_when_there_is_nothing_to_point_at(self):
        """The negative control for the note: an ordinary unlocated member must keep
        the shorter, true text rather than a permanent hedge."""
        out = self._report({"orbit"}, roots=[self.base / "empty"])
        self.assertIn("UNLOCATED", out)
        self.assertNotIn("BEFORE looking on another machine", out)

    def test_a_mapped_id_less_member_is_broken_map_and_not_unlocated(self):
        """The map half, end to end. Naming it broken is only half the fix: while it
        also printed under UNLOCATED, the prose forbidden by this item still applied to
        it — telling the reader to add a path that was already in the map."""
        out = self._report({"orbit"}, repo_paths={"orbit": str(self.member_repo)})
        self.assertIn("BROKEN MAP", out)
        self.assertIn(str(self.member_repo), out)
        self.assertNotIn("UNLOCATED", out)

    def test_a_genuinely_absent_member_still_reports_unlocated(self):
        """The load-bearing property this module has had since sessions 35/37/47 must
        survive the change: absence is still stated, never softened into silence."""
        out = self._report({"orbit", "gone"}, repo_paths={"orbit": str(self.member_repo)})
        self.assertIn("UNLOCATED", out)
        self.assertIn("- gone", out)


if __name__ == "__main__":
    unittest.main()
