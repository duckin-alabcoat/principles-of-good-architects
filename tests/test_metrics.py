"""Tests for curate/metrics.py — the mining layer + four renderings.

Self-contained: every fixture is built in a tempdir, nothing depends on the live
fleet. The load-bearing properties (federation metrics brief M2/M3):

  - legacy stamps (pre-S1, no runtime field) default to claude-code, but a real
    trailing runtime is honoured — the default only touches lines that STRUCTURALLY
    lack the field;
  - guard-firing parse counts by guard and SKIPS malformed lines without crashing;
  - staleness respects the cadence map and the parked (staleness-exempt) flag;
  - a pending brief past the dwell threshold surfaces as a stranded-brief item;
  - an empty fleet renders exactly "All clear — no attention items.";
  - money-slide runtime counts come from the stamp runtime fields;
  - a signal whose SOURCE does not exist is emitted as `n/a (source pending: …)`,
    never a fabricated number (P15).

stdlib unittest: python3 -m unittest tests.test_metrics
"""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))
_spec = importlib.util.spec_from_file_location("metrics", ROOT / "curate" / "metrics.py")
metrics = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(metrics)


# --------------------------------------------------------------------------- stamp parsing


class StampParseTest(unittest.TestCase):
    """Runtime defaulting: legacy lines -> claude-code; real trailing field honoured."""

    def test_legacy_start_no_runtime_defaults_claude_code(self):
        text = "**Start:** Federation Architect v2.28.0 · Laptop · 2026-07-15 09:30 UTC\n"
        s = metrics.parse_sessions(text)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["runtime"], "claude-code")
        self.assertEqual(s[0]["dt"].date(), date(2026, 7, 15))

    def test_legacy_end_duration_only_defaults_claude_code(self):
        text = ("**Start:** Arch v1 · Host · 2026-07-14 10:00 UTC\n"
                "**End:**   Arch v1 · Host · 2026-07-14 12:01 UTC · 2h 01m\n")
        s = metrics.parse_sessions(text)
        self.assertEqual(s[0]["runtime"], "claude-code")

    def test_s1_start_runtime_is_read(self):
        text = "**Start:** Arch v1 · Host · 2026-07-15 09:30 UTC · gemini-antigravity\n"
        s = metrics.parse_sessions(text)
        self.assertEqual(s[0]["runtime"], "gemini-antigravity")

    def test_s1_end_runtime_after_duration_is_read(self):
        text = ("**Start:** Arch v1 · Host · 2026-07-15 09:30 UTC · claude-code\n"
                "**End:**   Arch v1 · Host · 2026-07-15 10:57 UTC · 1h 27m · gemini-antigravity\n")
        s = metrics.parse_sessions(text)
        # End field wins as the authoritative close.
        self.assertEqual(s[0]["runtime"], "gemini-antigravity")

    def test_malformed_end_datetime_does_not_crash(self):
        text = ("**Start:** Arch v1 · Host · 2026-07-14 09:00 UTC\n"
                "**End:**   Arch v1 · Host · 2026-07-14 (emergency close — restarting)\n")
        s = metrics.parse_sessions(text)
        self.assertEqual(len(s), 1)
        self.assertEqual(s[0]["runtime"], "claude-code")

    def test_in_progress_trailing_start_is_kept(self):
        text = ("**Start:** Arch v1 · Host · 2026-07-14 09:00 UTC · claude-code\n"
                "**End:**   Arch v1 · Host · 2026-07-14 10:16 UTC · 1h 16m · claude-code\n"
                "**Start:** Arch v1 · Host · 2026-07-15 09:30 UTC · claude-code\n")
        s = metrics.parse_sessions(text)
        self.assertEqual(len(s), 2)
        self.assertIsNone(s[1]["end_dt"])


# --------------------------------------------------------------------------- handoff naming


class HandoffNameTest(unittest.TestCase):
    """A member may declare a non-default `handoff` filename in session.config.json
    (standard_check.py's `d_handoff` already respects this). Mining sessions from the
    hardcoded literal `session-handoff.md` would silently mine ZERO sessions for such
    a member while standard_check.py reports it present — the declare-what-a-check-
    assumes class (session 99)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_config_defaults_to_standard_name(self):
        self.assertEqual(metrics._handoff_name(self.repo), "session-handoff.md")

    def test_declared_handoff_name_is_honoured(self):
        (self.repo / "session.config.json").write_text(
            json.dumps({"handoff": "docs/session-handoff.md"}), encoding="utf-8")
        self.assertEqual(metrics._handoff_name(self.repo), "docs/session-handoff.md")

    def test_mine_sessions_finds_a_declared_nonstandard_handoff(self):
        (self.repo / "session.config.json").write_text(
            json.dumps({"handoff": "docs/session-handoff.md"}), encoding="utf-8")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "session-handoff.md").write_text(
            "**Start:** Arch v1 · Host · 2026-07-14 09:00 UTC · claude-code\n"
            "**End:**   Arch v1 · Host · 2026-07-14 10:16 UTC · 1h 16m · claude-code\n",
            encoding="utf-8")
        sessions = metrics.mine_sessions(self.repo)
        self.assertEqual(len(sessions), 1)


# --------------------------------------------------------------------------- guard firings


class GuardFiringsTest(unittest.TestCase):
    """Counts by guard; malformed lines counted and skipped, never fatal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self._tmp.name)
        (self.repo / ".session-state").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, body):
        (self.repo / ".session-state" / "guard-firings.jsonl").write_text(body, encoding="utf-8")

    def test_absent_file_is_zero_not_error(self):
        r = metrics.mine_guard_firings(self.repo)
        self.assertEqual(r, {"counts": {}, "total": 0, "malformed": 0})

    def test_counts_by_guard_and_skips_malformed(self):
        self._write(
            '{"ts": "2026-07-15T08:40:12Z", "guard": "check-bash", "op": "x", "repo": "r"}\n'
            '{"ts": "2026-07-15T08:41:00Z", "guard": "check-bash", "op": "y", "repo": "r"}\n'
            '{"ts": "2026-07-15T08:42:00Z", "guard": "check-question", "op": "z", "repo": "r"}\n'
            'THIS LINE IS NOT JSON\n'                       # malformed -> skipped
            '\n'                                            # blank -> ignored
            '{"ts": "x", "op": "no guard field", "repo": "r"}\n'  # no guard -> malformed
        )
        r = metrics.mine_guard_firings(self.repo)
        self.assertEqual(r["counts"], {"check-bash": 2, "check-question": 1})
        self.assertEqual(r["total"], 3)
        self.assertEqual(r["malformed"], 2)


# --------------------------------------------------------------------------- fixture builder


def _make_system(base, sid, *, last_active, blocked="false", sessions_text=None,
                 applied=None, pending=None):
    """Build a minimal system repo: STATUS.md + optional handoff + briefs."""
    repo = base / sid
    (repo).mkdir(parents=True, exist_ok=True)
    (repo / "STATUS.md").write_text(
        f"---\nid: {sid}\nphase: active\nversion: 1.0.0\n"
        f"last_active: {last_active}\nfocus: test\nblocked: {blocked}\n---\n",
        encoding="utf-8")
    if sessions_text is not None:
        (repo / "session-handoff.md").write_text(sessions_text, encoding="utf-8")
    for state, briefs in (("applied", applied or []), ("pending", pending or [])):
        for arch, name, body in briefs:
            d = repo / "proposed-edits" / arch / state
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(body, encoding="utf-8")
    return repo


class MineAndExceptionsTest(unittest.TestCase):
    """Staleness/parked exemption, pending dwell, empty-fleet all-clear, runtime counts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.today = date(2026, 7, 15)
        # now_ts anchored so pending-brief mtime ages are deterministic-ish (we override
        # mtime via the fixture's real mtime; dwell test uses a very old mtime by hand).

    def tearDown(self):
        self._tmp.cleanup()

    def _mine(self, cadence=None, now_ts=None):
        return metrics.mine(
            roots=[str(self.base)], repo_paths=None, fed_root=None,
            today=self.today, now_ts=now_ts,
            cadence=cadence or {"defaults": {"cadence_days": 7}, "systems": {}})

    def test_empty_fleet_is_all_clear(self):
        data = self._mine()
        self.assertEqual(metrics.compute_exceptions(data), [])
        rendered = metrics.render_exceptions(data)
        self.assertTrue(rendered.startswith("All clear — no attention items."))
        self.assertIn("# not yet mined:", rendered)  # omission is visible

    def test_stale_system_surfaces(self):
        _make_system(self.base, "alpha", last_active="2026-07-01")  # 14d stale, cadence 7
        data = self._mine()
        kinds = [(e["system"], e["kind"]) for e in metrics.compute_exceptions(data)]
        self.assertIn(("alpha", "stale"), kinds)

    def test_parked_system_is_staleness_exempt(self):
        _make_system(self.base, "parkedsys", last_active="2026-01-01")  # very stale
        cadence = {"defaults": {"cadence_days": 7},
                   "systems": {"parkedsys": {"cadence_days": 7, "parked": True}}}
        data = self._mine(cadence=cadence)
        stale = [e for e in metrics.compute_exceptions(data)
                 if e["system"] == "parkedsys" and e["kind"] == "stale"]
        self.assertEqual(stale, [])

    def test_custom_cadence_not_stale_within_window(self):
        # The anchor this used to carry — `date.today() - 11d`, with a comment about the
        # assertion rotting — was the symptom of WI-0264, not a fix: the fixture was bent
        # to match the ambient clock because `compute_exceptions` ignored the injected
        # one. It measures from the INJECTED date now, so the fixture states the date it
        # means. Under the old code this is 70+ real days stale and this test goes red.
        within = (self.today - timedelta(days=11)).isoformat()
        _make_system(self.base, "slowsys", last_active=within)  # 11d < 14d cadence
        cadence = {"defaults": {"cadence_days": 7},
                   "systems": {"slowsys": {"cadence_days": 14, "parked": False}}}
        data = self._mine(cadence=cadence)
        stale = [e for e in metrics.compute_exceptions(data) if e["kind"] == "stale"]
        self.assertEqual(stale, [])

    def test_stale_age_is_measured_from_the_injected_date(self):
        """WI-0264: one clock governs the whole call, and it is the caller's.

        Exactly 14, not merely `> 7`. `age_days` used to read `date.today()` itself, so
        this row reported the distance from the real day — which passed only because
        MORE stale still reads as stale. An exact assertion is what makes the ambient
        clock's contribution visible, and it cannot drift with the calendar.
        """
        _make_system(self.base, "alpha", last_active="2026-07-01")  # 14d before today
        stale = [e for e in metrics.compute_exceptions(self._mine())
                 if e["kind"] == "stale"]
        self.assertEqual(1, len(stale))
        self.assertEqual(14, stale[0]["age_days"])
        self.assertIn("14d since last_active", stale[0]["detail"])

    def test_blocked_age_is_measured_from_the_injected_date(self):
        """The blocked row read the real clock too — both age sites, one fix."""
        _make_system(self.base, "beta", last_active="2026-07-05", blocked="true")
        blocked = [e for e in metrics.compute_exceptions(self._mine())
                   if e["kind"] == "blocked"]
        self.assertEqual(1, len(blocked))
        self.assertEqual(10, blocked[0]["age_days"])

    def test_the_injected_date_is_the_only_clock_the_ages_can_come_from(self):
        """Move the fixture's `today` and every age moves with it, by exactly that much.

        `age_days` takes a required `today` now, so there is no machine day left to
        leak in; this asserts the property rather than the absence of the old call.
        """
        _make_system(self.base, "alpha", last_active="2026-07-01")
        base_age = metrics.compute_exceptions(self._mine())[0]["age_days"]
        self.today = self.today + timedelta(days=30)
        moved_age = metrics.compute_exceptions(self._mine())[0]["age_days"]
        self.assertEqual(base_age + 30, moved_age)

    def test_blocked_system_surfaces_with_reason(self):
        _make_system(self.base, "beta", last_active="2026-07-15",
                     blocked="waiting on operator decision")
        data = self._mine()
        blocked = [e for e in metrics.compute_exceptions(data) if e["kind"] == "blocked"]
        self.assertEqual(len(blocked), 1)
        self.assertIn("waiting on operator decision", blocked[0]["detail"])

    def test_pending_dwell_surfaces_when_old(self):
        repo = _make_system(self.base, "gamma", last_active="2026-07-15",
                            pending=[("federation-arch", "old.md", "**Edit ID:** old\n**State:** Pending\n")])
        # Age the pending brief's mtime well past the 7d dwell threshold.
        brief = repo / "proposed-edits" / "federation-arch" / "pending" / "old.md"
        import os
        old = self.today.toordinal()
        now_ts = 1_000_000_000.0
        os.utime(brief, (now_ts - 20 * 86400, now_ts - 20 * 86400))
        data = self._mine(now_ts=now_ts)
        dwell = [e for e in metrics.compute_exceptions(data) if e["kind"] == "pending-dwell"]
        self.assertEqual(len(dwell), 1)
        self.assertGreater(dwell[0]["age_days"], metrics.PENDING_DWELL_DAYS)

    def test_fresh_pending_brief_does_not_dwell(self):
        repo = _make_system(self.base, "delta", last_active="2026-07-15",
                            pending=[("federation-arch", "new.md", "**State:** Pending\n")])
        brief = repo / "proposed-edits" / "federation-arch" / "pending" / "new.md"
        import os
        now_ts = 1_000_000_000.0
        os.utime(brief, (now_ts - 2 * 86400, now_ts - 2 * 86400))  # 2d old
        data = self._mine(now_ts=now_ts)
        dwell = [e for e in metrics.compute_exceptions(data) if e["kind"] == "pending-dwell"]
        self.assertEqual(dwell, [])

    def test_runtime_counts_in_money_slide(self):
        handoff = (
            "**Start:** A v1 · Host · 2026-07-10 08:00 UTC · claude-code\n"
            "**End:**   A v1 · Host · 2026-07-10 09:00 UTC · 1h 00m · claude-code\n"
            "**Start:** A v1 · Host · 2026-07-12 08:00 UTC · gemini-antigravity\n"
            "**End:**   A v1 · Host · 2026-07-12 09:00 UTC · 1h 00m · gemini-antigravity\n"
            "**Start:** A v1 · Host · 2026-07-13 08:00 UTC\n"  # legacy -> claude-code
            "**End:**   A v1 · Host · 2026-07-13 09:00 UTC · 1h 00m\n"
        )
        _make_system(self.base, "epsilon", last_active="2026-07-15", sessions_text=handoff)
        data = self._mine()
        self.assertEqual(data["runtime_totals"], {"claude-code": 2, "gemini-antigravity": 1})
        slide = metrics.render_money_slide(data)
        self.assertIn("claude-code=2", slide)
        self.assertIn("gemini-antigravity=1", slide)
        self.assertIn("(7) runtimes operated", slide)

    def test_months_in_operation_from_earliest_stamp(self):
        handoff = "**Start:** A v1 · Host · 2026-01-15 08:00 UTC · claude-code\n"
        _make_system(self.base, "zeta", last_active="2026-07-15", sessions_text=handoff)
        data = self._mine()
        self.assertEqual(data["earliest_session_date"], date(2026, 1, 15))
        self.assertIn("months in operation", metrics.render_money_slide(data))


# --------------------------------------------------------------------------- n/a discipline


class NaPendingSourceTest(unittest.TestCase):
    """A signal whose source does not exist is `n/a (source pending: …)`, never fabricated."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _data(self):
        return metrics.mine(roots=[str(self.base)], fed_root=None, today=date(2026, 7, 15),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def test_human_intervention_ratio_is_na(self):
        slide = metrics.render_money_slide(self._data())
        self.assertIn("(5) human interventions/change: n/a (source pending: apply.py surfacing ledger",
                      slide)

    def test_half_apply_incidents_is_na(self):
        slide = metrics.render_money_slide(self._data())
        self.assertIn("(6) half-apply incidents     : n/a (source pending: half-apply incident ledger)",
                      slide)

    def test_propagation_na_when_no_briefs(self):
        slide = metrics.render_money_slide(self._data())
        self.assertIn("(4) median propagation time  : n/a (source pending: brief accept/apply timestamps)",
                      slide)

    def test_propagation_mined_when_dates_present(self):
        body = ("**Edit ID:** x\n**State:** Applied\n"
                "**Drafted on:** 2026-05-30\n**Applied on:** 2026-06-04\n")
        _make_system(self.base, "eta", last_active="2026-07-15",
                     applied=[("federation-arch", "x.md", body)])
        data = self._data()
        self.assertEqual(data["propagation_days"], [5])
        self.assertIn("median propagation time  : 5.0d", metrics.render_money_slide(data))

    def test_evidence_renders_banner_and_na(self):
        text = metrics.render_evidence(self._data())
        self.assertTrue(text.startswith(metrics.EVIDENCE_BANNER.rstrip("\n")))
        self.assertIn("n/a (source pending", text)
        self.assertIn("Sources not yet mined", text)


class AdoptRunnerStatusTest(unittest.TestCase):
    """The R3 adoption-runner status mine (ADR-0050) — the dashboard's proof-of-life +
    stale-streak. Absent = n/a (runner does not report here), never fabricated; a stale
    STREAK past threshold or a failed sweep surfaces as an attention item; a single stale
    night does not."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fed = pathlib.Path(self._tmp.name)
        (self.fed / ".session-state").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, status):
        (self.fed / ".session-state" / "adopt-runner.status").write_text(json.dumps(status))

    def _mine(self):
        return metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=date(2026, 7, 17),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def test_first_stale_auth_field_is_mined_and_rendered(self):
        auth = "unauthenticated since 2026-01-10T12:00:00Z"
        self._write({"auth": auth, "consecutive_stale": 1,
                     "stale_since": "2026-01-10T12:00:00Z"})
        self.assertEqual(metrics.mine_adopt_runner(self.fed)["auth"], auth)
        self.assertIn("- auth: " + auth, metrics.render_evidence(self._mine()))
        self._write({"auth": None, "consecutive_stale": 0})
        self.assertNotIn("- auth:", metrics.render_evidence(self._mine()))

    def test_absent_status_mines_none(self):
        self.assertIsNone(metrics.mine_adopt_runner(str(self.fed)))

    def test_absent_status_renders_na_in_evidence(self):
        text = metrics.render_evidence(self._mine())
        self.assertIn("Background adoption runner", text)
        self.assertIn("has not reported on this machine", text)

    def test_healthy_sweep_populates_evidence_no_exception(self):
        self._write({"last_run": "2026-07-17T03:15:00Z", "result": "no-eligible",
                     "last_success": "2026-07-17T03:15:00Z", "consecutive_stale": 0,
                     "adopted": 0, "failed": 0, "eligible": 0, "repos_scanned": 8})
        data = self._mine()
        text = metrics.render_evidence(data)
        self.assertIn("result: **no-eligible**", text)
        self.assertIn("last successful (authenticated) sweep: 2026-07-17T03:15:00Z", text)
        runner_items = [e for e in metrics.compute_exceptions(data) if e["kind"] == "adopt-runner"]
        self.assertEqual(runner_items, [])

    def test_single_stale_night_is_not_an_exception(self):
        self._write({"last_run": "2026-07-17T03:15:00Z", "result": "unauthenticated",
                     "last_success": "2026-07-15T03:15:00Z", "consecutive_stale": 1,
                     "stale_since": "2026-07-17T03:15:00Z"})
        items = [e for e in metrics.compute_exceptions(self._mine()) if e["kind"] == "adopt-runner"]
        self.assertEqual(items, [])

    def test_stale_streak_past_threshold_surfaces(self):
        self._write({"last_run": "2026-07-17T03:15:00Z", "result": "unauthenticated",
                     "last_success": None, "consecutive_stale": 5,
                     "stale_since": "2026-07-13T03:15:00Z"})
        items = [e for e in metrics.compute_exceptions(self._mine()) if e["kind"] == "adopt-runner"]
        self.assertEqual(len(items), 1)
        self.assertIn("5 consecutive", items[0]["detail"])
        self.assertIn("refresh the Runner login", items[0]["detail"])

    def test_failed_last_sweep_surfaces(self):
        self._write({"last_run": "2026-07-17T03:15:00Z", "result": "failed",
                     "last_success": "2026-07-17T03:15:00Z", "consecutive_stale": 0,
                     "detail": "verify exit 1: assertion failed"})
        items = [e for e in metrics.compute_exceptions(self._mine()) if e["kind"] == "adopt-runner"]
        self.assertEqual(len(items), 1)
        self.assertIn("last sweep failed", items[0]["detail"])


class CanonBudgetTest(unittest.TestCase):
    """The injected-doctrine-set size budget (ADR-0052). Size is mined in BYTES from the
    federation's CANON.md + STANDARD.md; over budget surfaces one attention item (a soft
    debt, never a block); under budget is silent. mine_canon_size(None) -> None so the
    fed-less test path simply omits the signal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fed = pathlib.Path(self._tmp.name)
        self._budget = metrics.CANON_BUDGET_CHARS
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(lambda: setattr(metrics, "CANON_BUDGET_CHARS", self._budget))

    def _write(self, canon="c", standard="s"):
        (self.fed / "CANON.md").write_text(canon)
        (self.fed / "STANDARD.md").write_text(standard)

    def _mine(self):
        return metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=date(2026, 7, 18),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def test_size_sums_both_files_in_bytes(self):
        self._write(canon="a" * 100, standard="b" * 250)
        cs = metrics.mine_canon_size(str(self.fed))
        self.assertEqual(cs["files"], {"CANON.md": 100, "STANDARD.md": 250})
        self.assertEqual(cs["total"], 350)

    def test_multibyte_counts_bytes_not_codepoints(self):
        # "·" is 2 bytes in UTF-8; byte count (wc -c) is the ADR-0052 unit, not len().
        self._write(canon="·", standard="")
        self.assertEqual(metrics.mine_canon_size(str(self.fed))["files"]["CANON.md"], 2)

    def test_absent_files_are_none_not_error(self):
        cs = metrics.mine_canon_size(str(self.fed))  # nothing written
        self.assertEqual(cs["files"], {"CANON.md": None, "STANDARD.md": None})
        self.assertEqual(cs["total"], 0)

    def test_none_fed_root_mines_none(self):
        self.assertIsNone(metrics.mine_canon_size(None))
        self.assertEqual(metrics.mine_canon_trend(None), [])

    def test_under_budget_no_exception(self):
        metrics.CANON_BUDGET_CHARS = 1000
        self._write(canon="a" * 100, standard="b" * 100)
        items = [e for e in metrics.compute_exceptions(self._mine()) if e["kind"] == "canon-budget"]
        self.assertEqual(items, [])

    def test_over_budget_surfaces_consolidation_owed(self):
        metrics.CANON_BUDGET_CHARS = 100
        self._write(canon="a" * 80, standard="b" * 80)  # 160 > 100
        items = [e for e in metrics.compute_exceptions(self._mine()) if e["kind"] == "canon-budget"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["system"], "federation")
        self.assertIn("over budget by 60", items[0]["detail"])
        self.assertIn("consolidation pass owed", items[0]["detail"])

    def test_evidence_renders_budget_section(self):
        metrics.CANON_BUDGET_CHARS = 100
        self._write(canon="a" * 80, standard="b" * 80)
        text = metrics.render_evidence(self._mine())
        self.assertIn("canon budget (ADR-0052)", text)
        self.assertIn("OVER by 60", text)


class FleetCanonSizeTest(unittest.TestCase):
    """What the FLEET actually carries, measured per repo (WI-0374).

    `mine_canon_size` measures ONE tree, the federation's. Multiplying that by the repo
    count was documented as the fleet's per-session cost, and nothing enforced it: the
    substrate push is a hand-run command with three per-target skip gates and no cadence.
    Measured 2026-09-13 the fleet was 82,139 B *under* what that assumption reported; on
    2026-09-17, after a consolidation landed on the federation and was never pushed, it
    was 154,669 B *over*, with nearly every repo above a ceiling the old signal
    called comfortably met. The error is neither small nor stably signed, so every number
    here comes from a repo's own two files.

    Sizes below are deliberately all different: a fixture where the fleet total happens to
    equal the multiplied-out total cannot fail for the reason it claims to test.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = pathlib.Path(self._tmp.name) / "fleet"
        self.base.mkdir(parents=True)
        self.fed = pathlib.Path(self._tmp.name) / "fed"
        self.fed.mkdir(parents=True)
        self._budget = metrics.CANON_BUDGET_CHARS
        self.addCleanup(lambda: setattr(metrics, "CANON_BUDGET_CHARS", self._budget))
        metrics.CANON_BUDGET_CHARS = 1000
        self._doctrine(self.fed, canon=300, standard=200)          # source: 500, under

    def _doctrine(self, repo, canon=None, standard=None):
        if canon is not None:
            (repo / "CANON.md").write_text("c" * canon, encoding="utf-8")
        if standard is not None:
            (repo / "STANDARD.md").write_text("s" * standard, encoding="utf-8")

    def _member(self, sid, canon=None, standard=None):
        repo = _make_system(self.base, sid, last_active=date(2026, 7, 15).isoformat())
        self._doctrine(repo, canon, standard)
        return repo

    def _mine(self, fed_root=None):
        return metrics.mine(
            roots=[str(self.base)], repo_paths=None,
            fed_root=str(self.fed) if fed_root is None else fed_root,
            today=date(2026, 7, 15),
            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def _fc(self, **kw):
        return self._mine(**kw)["fleet_canon"]

    def _drift(self, data):
        return [e for e in metrics.compute_exceptions(data) if e["kind"] == "canon-drift"]

    # -- the retired assumption ------------------------------------------------------

    def test_the_fleet_total_is_summed_per_repo_not_multiplied_from_the_source(self):
        self._member("alpha", canon=700, standard=600)   # 1300
        self._member("beta", canon=400, standard=350)    # 750
        fc = self._fc()
        self.assertEqual(fc["fed_total"], 500)
        self.assertEqual(fc["measured"], 3)              # alpha, beta, federation
        self.assertEqual(fc["fleet_total"], 1300 + 750 + 500)
        self.assertEqual(fc["if_uniform"], 500 * 3)      # what the old docstring implied
        self.assertEqual(fc["gap"], 2550 - 1500)
        totals = {m["system"]: m["total"] for m in fc["members"]}
        self.assertEqual(totals, {"alpha": 1300, "beta": 750, "federation": 500})

    def test_a_repo_not_carrying_the_source_is_named_stale(self):
        self._member("alpha", canon=700, standard=600)
        self._member("current", canon=300, standard=200)   # byte-identical to the source
        fc = self._fc()
        self.assertEqual(fc["stale"], ["alpha"])

    # -- the real defect this was found on -------------------------------------------

    def test_members_over_the_ceiling_surface_while_the_source_sits_under_it(self):
        """The live 2026-09-17 state, and the one the old signal could not see: the
        federation is under budget — so `canon-budget` is correctly silent — and eleven
        members are over it. Silence from both lines is the bug."""
        self._member("alpha", canon=700, standard=600)     # 1300 > 1000
        data = self._mine()
        self.assertEqual(
            [], [e for e in metrics.compute_exceptions(data) if e["kind"] == "canon-budget"],
            "the source is under budget, so the federation-scoped line must stay silent")
        rows = self._drift(data)
        self.assertEqual(1, len(rows))
        self.assertEqual("federation", rows[0]["system"])
        self.assertIn("1 of 2 located repo(s) carry a set over the 1000-byte ceiling",
                      rows[0]["detail"])
        self.assertIn("measured fleet per-session cost 1800 bytes vs 1000", rows[0]["detail"])
        self.assertIn("(+800)", rows[0]["detail"])

    def test_a_fleet_carrying_the_current_source_under_budget_is_silent(self):
        self._member("alpha", canon=300, standard=200)
        self._member("beta", canon=300, standard=200)
        self.assertEqual([], self._drift(self._mine()))

    # -- fail-soft, without letting an unreadable repo read as a cheap one ------------

    def test_a_repo_with_one_file_is_partial_and_is_not_summed(self):
        """The push asymmetry (STANDARD.md is refresh-only, so a member with CANON and no
        STANDARD never gets one created). Summing its CANON alone would report it as
        comfortably under the ceiling — the fail-soft trap the land gate refuses to
        inherit."""
        self._member("alpha", canon=700)                  # no STANDARD.md
        fc = self._fc()
        self.assertEqual(fc["partial"], ["alpha"])
        self.assertEqual(fc["measured"], 1)               # federation only
        self.assertEqual(fc["fleet_total"], 500)
        row = next(m for m in fc["members"] if m["system"] == "alpha")
        self.assertEqual(row["state"], "partial")
        self.assertIsNone(row["over_by"])
        self.assertEqual(row["files"], {"CANON.md": 700, "STANDARD.md": None})

    def test_a_repo_with_no_doctrine_set_is_absent_not_zero(self):
        self._member("alpha")
        fc = self._fc()
        self.assertEqual(fc["absent"], ["alpha"])
        self.assertEqual(fc["fleet_total"], 500)
        row = next(m for m in fc["members"] if m["system"] == "alpha")
        self.assertIsNone(row["total"])

    def test_nothing_located_mines_none(self):
        self.assertIsNone(metrics.mine_fleet_canon_size({}, str(self.fed), 500))

    def test_an_unknown_source_size_reports_the_fleet_without_a_comparison(self):
        self._member("alpha", canon=700, standard=600)
        fc = metrics.mine_fleet_canon_size(
            {"alpha": ({}, self.base / "alpha")}, None, None)
        self.assertEqual(fc["fleet_total"], 1300)
        self.assertIsNone(fc["if_uniform"])
        self.assertIsNone(fc["gap"])
        self.assertEqual(fc["stale"], [])

    # -- regression: the source tree is not the roster's federation row --------------

    def test_the_source_size_comes_from_fed_root_not_the_located_federation_row(self):
        """`_locate_systems` resolves the roster's `federation` row through
        `repo-paths.local`, which names the main checkout; `fed_root` is whichever
        worktree is running. The first cut of this picked the source out of the located
        map, found no path match on a lane, and silently reported no source at all —
        `if_uniform`, `gap` and `stale` all went quiet while reading as a clean measure."""
        self._member("federation", canon=900, standard=800)   # the walked row wins
        fc = self._fc()
        self.assertEqual(fc["fed_total"], 500, "the source is fed_root, not the roster row")
        self.assertEqual(fc["fleet_total"], 1700)
        self.assertEqual(fc["if_uniform"], 500)
        self.assertEqual(fc["gap"], 1200)
        self.assertEqual(fc["stale"], ["federation"])

    def test_evidence_renders_the_per_repo_table(self):
        self._member("alpha", canon=700, standard=600)
        text = metrics.render_evidence(self._mine())
        self.assertIn("What the fleet actually carries (WI-0374)", text)
        self.assertIn("| alpha | 700 | 600 | 1300 |", text)
        self.assertIn("measured fleet per-session cost: **1800 bytes**", text)
        self.assertIn("error +800 bytes", text)


# --------------------------------------------------------------------------- conformance (ADR-0053)


def _journal(repo, name, started, ended=None, body="### What happened\n\n- Session opened.\n",
             closed_by=None):
    d = repo / "sessions" / "journal"
    d.mkdir(parents=True, exist_ok=True)
    fm = [f"session-id: {name}", "ordinal: 1", "title: t", "machine: Host",
          "runtime: claude-code", f"started: {started}"]
    if ended:
        fm.append(f"ended: {ended}")
    if closed_by:
        fm.append(f"closed-by: {closed_by}")
    (d / f"{name}.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n" + body,
                                  encoding="utf-8")


class UnreadableJournalTest(unittest.TestCase):
    """WI-0332, fleet side. A journal rewritten with a whole-file write loses its `---`
    block, and `mine_journal_sessions` skipped it under the comment "README or stray
    file, not a journal" — an assumption stated as a fact. The session then left the
    session totals, the conformance counts and the escalation ledger with nothing
    reporting it. The harness-side guard in `sessionlib/journal.py` cannot reach here:
    metrics mines members whose harness may predate it, which is why it needs its own.

    OBSERVED PASSING SILENTLY before the miner existed — a mine over a clobbered journal
    returned a clean, complete-looking system with one fewer session in it."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)
    CLOBBERED = "# Session notes\n\nrewrote the whole file\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _mine(self):
        return metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def _write(self, repo, name, text):
        d = repo / "sessions" / "journal"
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(text, encoding="utf-8")

    def test_a_clobbered_journal_is_named_not_silently_skipped(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        self._write(repo, "20260715T1300Z-host-ab12.md", self.CLOBBERED)
        found = metrics.mine_unreadable_journals(repo)
        self.assertEqual([n for n, _why in found], ["20260715T1300Z-host-ab12.md"])
        self.assertIn("session-id", found[0][1], "the reason is named, not implied")

    def test_a_readme_is_still_a_stray_and_stays_quiet(self):
        """The discriminator is the FILENAME — a genuine stray must not become noise, or
        the signal gets switched off."""
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        self._write(repo, "README.md", "# journals live here\n")
        self.assertEqual(metrics.mine_unreadable_journals(repo), [])

    def test_a_healthy_journal_is_not_flagged(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        _journal(repo, "20260715T0800Z-host-ab12", "2026-07-15T08:00:00",
                 ended="2026-07-15T10:00:00")
        self.assertEqual(metrics.mine_unreadable_journals(repo), [])

    def test_it_reaches_the_attention_list(self):
        """A miner nothing renders is a detector that does not detect
        ([`ship-the-detector-with-the-capability`])."""
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        self._write(repo, "20260715T1300Z-host-ab12.md", self.CLOBBERED)
        items = metrics.compute_exceptions(self._mine())
        journal_items = [i for i in items if i["kind"] == "journal"]
        self.assertEqual(len(journal_items), 1, f"expected one journal item in {items}")
        self.assertIn("20260715T1300Z-host-ab12.md", journal_items[0]["detail"])

    def test_the_declared_journal_dir_is_honoured(self):
        """WI-0029: the path key is read from the member's layout, never assumed — a
        member that moved its journals would otherwise report a clean fleet by never
        looking."""
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        (repo / "session.config.json").write_text(
            json.dumps({"layout": {"journal_dir": "records/j"}}), encoding="utf-8")
        d = repo / "records" / "j"
        d.mkdir(parents=True, exist_ok=True)
        (d / "20260715T1300Z-host-ab12.md").write_text(self.CLOBBERED, encoding="utf-8")
        found = metrics.mine_unreadable_journals(repo)
        self.assertEqual([n for n, _why in found], ["20260715T1300Z-host-ab12.md"])


class JournalConformanceTest(unittest.TestCase):
    """Session-close discipline mined from journal frontmatter (ADR-0051 model):
    unclosed past grace, insane duration, closed-with-boilerplate-body. A live (young)
    open journal never flags; a rich narrative never flags."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _mine(self):
        return metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def _conf(self, sid):
        return self._mine()["systems"][sid]["conformance"]

    def test_closed_with_narrative_is_clean(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 body="### What happened\n\n- Shipped the thing.\n")
        conf = self._conf("a")
        for kind in ("missing-narrative", "unclosed", "insane-duration"):
            self.assertNotIn(kind, conf["recent"])
        self.assertEqual(conf["historic"], {})

    def test_closed_boilerplate_flags_missing_narrative(self):
        repo = _make_system(self.base, "b", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00")
        conf = self._conf("b")
        self.assertEqual(len(conf["recent"]["missing-narrative"]), 1)

    def test_open_journal_within_grace_is_live_not_unclosed(self):
        repo = _make_system(self.base, "c", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T09:00:00")  # 3h old
        self.assertNotIn("unclosed", self._conf("c")["recent"])

    def test_open_journal_past_grace_flags_unclosed(self):
        repo = _make_system(self.base, "d", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-10T09:00:00")  # 5d old, no ended
        self.assertEqual(len(self._conf("d")["recent"]["unclosed"]), 1)

    def test_old_unclosed_is_historic_not_exception(self):
        repo = _make_system(self.base, "e", last_active="2026-07-15")
        _journal(repo, "j1", "2026-05-01T09:00:00")  # far outside the 30d window
        conf = self._conf("e")
        self.assertNotIn("unclosed", conf["recent"])
        self.assertEqual(conf["historic"]["unclosed"], 1)

    def test_negative_span_flags_insane_duration(self):
        repo = _make_system(self.base, "f", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T10:00:00", ended="2026-07-15T08:00:00",
                 body="- real work\n")
        self.assertEqual(len(self._conf("f")["recent"]["insane-duration"]), 1)

    def test_over_24h_span_flags_insane_duration(self):
        repo = _make_system(self.base, "g", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-13T08:00:00", ended="2026-07-14T10:00:00",
                 body="- real work\n")
        self.assertEqual(len(self._conf("g")["recent"]["insane-duration"]), 1)

    def test_journals_suppress_handoff_double_count(self):
        # A journal-model repo's handoff is COMPILED from its journals; only the
        # journal branch may judge close discipline, or every violation doubles.
        repo = _make_system(
            self.base, "h", last_active="2026-07-15",
            sessions_text="**Start:** A v1 · Host · 2026-07-10 09:00 UTC · claude-code\n")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 body="- real work\n")
        conf = self._conf("h")
        self.assertNotIn("unclosed", conf["recent"])  # handoff's endless Start ignored


class CloseRefreshConformanceTest(unittest.TestCase):
    """STATUS/ROADMAP refresh-at-close: lag between the newest close and the stamped
    surfaces flags; a same-day refresh is clean. Only judged when a close exists."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _repo(self, sid, last_active, roadmap_asof=None):
        repo = _make_system(self.base, sid, last_active=last_active)
        _journal(repo, "j1", "2026-07-14T08:00:00", ended="2026-07-14T10:00:00",
                 body="- work\n")
        if roadmap_asof:
            (repo / "ROADMAP.md").write_text(
                f"# R\n\n**As of:** {roadmap_asof} · session 9 · v1.0.0\n", encoding="utf-8")
        return repo

    def _conf(self, sid):
        data = metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return data["systems"][sid]["conformance"]

    def test_fresh_status_and_roadmap_clean(self):
        self._repo("a", last_active="2026-07-14", roadmap_asof="2026-07-14")
        conf = self._conf("a")
        self.assertNotIn("status-stale-at-close", conf["recent"])
        self.assertNotIn("roadmap-stale", conf["recent"])
        self.assertNotIn("roadmap-missing", conf["recent"])

    def test_status_behind_close_flags(self):
        self._repo("b", last_active="2026-07-01", roadmap_asof="2026-07-14")
        self.assertEqual(len(self._conf("b")["recent"]["status-stale-at-close"]), 1)

    def test_missing_roadmap_flags(self):
        self._repo("c", last_active="2026-07-14")
        self.assertEqual(len(self._conf("c")["recent"]["roadmap-missing"]), 1)

    def test_stale_roadmap_flags(self):
        self._repo("d", last_active="2026-07-14", roadmap_asof="2026-06-20")
        self.assertEqual(len(self._conf("d")["recent"]["roadmap-stale"]), 1)

    def test_no_sessions_no_close_judgement(self):
        # A repo with no session history is not judged on refresh-at-close.
        _make_system(self.base, "e", last_active="2026-01-01")  # no sessions at all
        c = self._conf("e")
        self.assertNotIn("roadmap-missing", c["recent"])
        self.assertNotIn("status-stale-at-close", c["recent"])


class AppliedStampConformanceTest(unittest.TestCase):
    """An applied/ brief with no applied-stamp: recent mtime -> exception-window
    violation; old mtime -> historic count only; a stamped brief is clean."""

    TODAY = date(2026, 7, 15)
    NOW = 1_000_000_000.0

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _conf(self, sid):
        data = metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return data["systems"][sid]["conformance"]

    def test_stamped_brief_is_clean(self):
        _make_system(self.base, "a", last_active="2026-07-15",
                     applied=[("x-arch", "b.md",
                               "**Edit ID:** b\n**Applied on:** 2026-07-10\n")])
        conf = self._conf("a")
        self.assertNotIn("applied-unstamped", conf["recent"])
        self.assertNotIn("applied-unstamped", conf["historic"])

    # A schema brief carries a YAML frontmatter fence; the apply engine stamps it iff
    # it opens with `---`, so an unstamped schema brief is a real audit gap.
    #
    # `apply: auto` deliberately (WI-0207). This class's clock is 2001, i.e. long before
    # `MANUAL_STAMP_CUTOVER`, and a pre-cutover MANUAL brief is structurally unstampable
    # — no writer existed that could have stamped it, so scoring it was the defect. The
    # auto path is the class this check was always right about, and these three cases
    # are about the recency window, not about apply mode. The manual/cutover behaviour
    # has its own class: `ManualBriefStampabilityTest`.
    SCHEMA = "---\nedit-id: b\napply: auto\n---\n**Edit ID:** b\n"

    def test_recent_unstamped_flags(self):
        repo = _make_system(self.base, "b", last_active="2026-07-15",
                            applied=[("x-arch", "b.md", self.SCHEMA)])
        import os
        f = repo / "proposed-edits" / "x-arch" / "applied" / "b.md"
        os.utime(f, (self.NOW - 2 * 86400, self.NOW - 2 * 86400))
        self.assertEqual(len(self._conf("b")["recent"]["applied-unstamped"]), 1)

    def test_old_unstamped_is_historic(self):
        repo = _make_system(self.base, "c", last_active="2026-07-15",
                            applied=[("x-arch", "b.md", self.SCHEMA)])
        import os
        f = repo / "proposed-edits" / "x-arch" / "applied" / "b.md"
        os.utime(f, (self.NOW - 60 * 86400, self.NOW - 60 * 86400))
        conf = self._conf("c")
        self.assertNotIn("applied-unstamped", conf["recent"])
        self.assertEqual(conf["historic"]["applied-unstamped"], 1)

    def test_legacy_no_frontmatter_brief_is_exempt(self):
        # A pre-schema legacy brief (no `---` fence) applied by hand is structurally
        # un-stampable — a missing stamp is expected historic residue, not an audit
        # gap. It surfaces neither recent nor historic (ADR-0053; session 76).
        repo = _make_system(self.base, "d", last_active="2026-07-15",
                            applied=[("x-arch", "b.md", "**Edit ID:** b\n")])
        import os
        f = repo / "proposed-edits" / "x-arch" / "applied" / "b.md"
        os.utime(f, (self.NOW - 2 * 86400, self.NOW - 2 * 86400))  # recent, still exempt
        conf = self._conf("d")
        self.assertNotIn("applied-unstamped", conf["recent"])
        self.assertNotIn("applied-unstamped", conf["historic"])


class RoledocBumpTest(unittest.TestCase):
    """The C4 bump check mined from git: a role-doc commit without a **Version:** line
    change is a violation; a bumping commit is clean; creation is not judged."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # One level down, so `roots=[repo.parent]` is this test's own directory. It was
        # the system TMPDIR itself, and `metrics.mine` then walked every repo in it —
        # including the trunk check's scratch checkout, whose live store the guard
        # caught (WI-0427, first real trunk check, 2026-09-25).
        self.repo = pathlib.Path(self._tmp.name) / "rd"
        self.repo.mkdir()
        self.addCleanup(self._tmp.cleanup)
        self._git("init", "-q", "-b", "main")

    def _git(self, *args):
        import subprocess
        r = subprocess.run(["git", "-C", str(self.repo),
                            "-c", "user.email=t@t", "-c", "user.name=T",
                            "-c", "commit.gpgsign=false", *args],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _commit(self, version, extra, msg):
        (self.repo / "arch.md").write_text(
            f"# Arch\n\n**Version:** {version}\n\n{extra}\n", encoding="utf-8")
        self._git("add", "arch.md")
        self._git("commit", "-q", "-m", msg)

    def test_change_without_bump_is_violation(self):
        self._commit("1.0.0", "one", "feat: create")
        self._commit("1.0.0", "two", "docs: edit without bump")   # violation
        self._commit("1.1.0", "three", "feat: edit with bump")    # clean
        r = metrics.mine_roledoc_bumps(self.repo, "arch.md")
        self.assertEqual(r["checked"], 3)
        self.assertEqual(len(r["violations"]), 1)

    def test_all_bumped_is_clean(self):
        self._commit("1.0.0", "one", "feat: create")
        self._commit("1.1.0", "two", "feat: bump")
        r = metrics.mine_roledoc_bumps(self.repo, "arch.md")
        self.assertEqual(r["violations"], [])

    def test_colon_outside_bold_variant_is_parsed(self):
        # One member's spelling: `**Version**: `v0.1.9`` — colon outside the bold. An
        # unparsed version line turns EVERY role-doc commit into a false positive
        # (None == None), so the variant must parse (the session-67 lesson).
        def commit(version, extra, msg):
            (self.repo / "arch.md").write_text(
                f"# Arch\n\n**Version**: `{version}`\n\n{extra}\n", encoding="utf-8")
            self._git("add", "arch.md")
            self._git("commit", "-q", "-m", msg)
        commit("v0.1.0", "one", "feat: create")
        commit("v0.2.0", "two", "feat: bump")        # bumped -> clean
        commit("v0.2.0", "three", "docs: no bump")   # violation
        r = metrics.mine_roledoc_bumps(self.repo, "arch.md")
        self.assertEqual(len(r["violations"]), 1)

    def test_non_roledoc_commits_are_not_checked(self):
        self._commit("1.0.0", "one", "feat: create")
        (self.repo / "other.md").write_text("x", encoding="utf-8")
        self._git("add", "other.md")
        self._git("commit", "-q", "-m", "docs: unrelated")
        r = metrics.mine_roledoc_bumps(self.repo, "arch.md")
        self.assertEqual(r["checked"], 1)  # only the creation changed the role doc
        self.assertEqual(r["violations"], [])

    def test_not_a_git_repo_is_none_not_error(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(metrics.mine_roledoc_bumps(pathlib.Path(d), "arch.md"))

    def test_deep_flag_gates_the_git_check(self):
        # The startup path (deep=False) skips the git-history check; on-demand paths
        # (deep=True, the default) mine it.
        self._commit("1.0.0", "one", "feat: create")
        self._commit("1.0.0", "two", "docs: edit without bump")
        (self.repo / "STATUS.md").write_text(
            "---\nid: rd\nlast_active: 2026-07-15\nblocked: false\n---\n", encoding="utf-8")
        (self.repo / "session.config.json").write_text(
            json.dumps({"role_doc": "arch.md"}), encoding="utf-8")
        kw = dict(roots=[str(self.repo.parent)], repo_paths=None, fed_root=None,
                  today=date(2026, 7, 15),
                  cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        shallow = metrics.mine(deep=False, **kw)["systems"]["rd"]["conformance"]
        self.assertNotIn("roledoc-no-bump", shallow["recent"])
        deep = metrics.mine(**kw)["systems"]["rd"]["conformance"]
        self.assertEqual(len(deep["recent"]["roledoc-no-bump"]), 1)


class SpotAuditTest(unittest.TestCase):
    """Spot-audit recency (ADR-0053 mechanism 2): never-run and overdue surface;
    a fresh record is silent; fed_root=None omits the signal entirely."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fed = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _items(self, today):
        data = metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=today,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return [e for e in metrics.compute_exceptions(data) if e["kind"] == "spot-audit"]

    def test_never_run_surfaces(self):
        items = self._items(date(2026, 7, 18))
        self.assertEqual(len(items), 1)
        self.assertIn("never run", items[0]["detail"])

    def test_fresh_audit_is_silent(self):
        (self.fed / "audits").mkdir()
        (self.fed / "audits" / "2026-07-10-example-app.md").write_text("audit", encoding="utf-8")
        self.assertEqual(self._items(date(2026, 7, 18)), [])

    def test_overdue_audit_surfaces_with_age(self):
        (self.fed / "audits").mkdir()
        (self.fed / "audits" / "2026-05-01-example-app.md").write_text("audit", encoding="utf-8")
        items = self._items(date(2026, 7, 18))
        self.assertEqual(len(items), 1)
        self.assertIn("overdue", items[0]["detail"])
        self.assertEqual(items[0]["age_days"], 78)

    def test_non_conforming_names_ignored(self):
        (self.fed / "audits").mkdir()
        (self.fed / "audits" / "README.md").write_text("convention", encoding="utf-8")
        items = self._items(date(2026, 7, 18))
        self.assertIn("never run", items[0]["detail"])

    def test_none_fed_root_omits_signal(self):
        self.assertIsNone(metrics.mine_spot_audit(None))


class CurateRecencyTest(unittest.TestCase):
    """Curation recency (WI-0087) — the last load-bearing chore resting on human memory.

    The federation has systematically retired "remembering" everywhere else: adoption
    became the ADR-0050 runner, liveness the heartbeat sidecar. Curation was still
    waiting for someone to think of it, and it is the one that produces canon.

    The SOURCE is the design decision these pin. `curate-runs/REVIEW-*` is the obvious
    place to look and the wrong one — gitignored ephemeral staging, so it exists only on
    the machine that ran the gather and would report "never" everywhere else.
    `curate/seen.json` is tracked, carries a `reviewed_on` per accepted entry, and moves
    only when canon actually took something in — the same answer on every machine."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fed = pathlib.Path(self._tmp.name)
        (self.fed / "curate").mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _seen(self, *stamps):
        doc = {"reviewed": {f"e{i}": {"reviewed_on": s} for i, s in enumerate(stamps)}}
        (self.fed / "curate" / "seen.json").write_text(json.dumps(doc), encoding="utf-8")

    def _items(self, today):
        data = metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=today,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return [e for e in metrics.compute_exceptions(data) if e["kind"] == "curate"]

    def test_never_accepted_surfaces(self):
        self._seen()
        items = self._items(date(2026, 8, 30))
        self.assertEqual(len(items), 1)
        self.assertIn("never recorded", items[0]["detail"])

    def test_a_missing_cursor_reads_as_never_not_as_fine(self):
        """No seen.json at all must not read as a healthy recent pass."""
        items = self._items(date(2026, 8, 30))
        self.assertEqual(len(items), 1)
        self.assertIn("never recorded", items[0]["detail"])

    def test_a_recent_pass_is_silent(self):
        self._seen("20260825-120000Z")
        self.assertEqual(self._items(date(2026, 8, 30)), [])

    def test_an_overdue_pass_surfaces_with_its_age(self):
        self._seen("20260727-194210Z")
        items = self._items(date(2026, 8, 30))
        self.assertEqual(len(items), 1)
        self.assertIn("overdue", items[0]["detail"])
        self.assertEqual(items[0]["age_days"], 34)

    def test_the_threshold_is_driven_from_both_sides(self):
        """At the cadence exactly: silent. One day past: speaks. A `>` slipped to `>=`
        changes how often this fires, on a surface whose whole value is being rare."""
        self._seen("20260816-120000Z")     # 14 days before 2026-08-30
        self.assertEqual(self._items(date(2026, 8, 30)), [])
        self.assertEqual(len(self._items(date(2026, 8, 31))), 1)

    def test_the_newest_stamp_wins_not_the_last_one_read(self):
        self._seen("20260727-194210Z", "20260829-010000Z", "20260601-000000Z")
        self.assertEqual(self._items(date(2026, 8, 30)), [])

    def test_malformed_stamps_are_ignored_rather_than_crashing(self):
        self._seen("not-a-stamp", "20260227-999999Z", "20260825-120000Z")
        self.assertEqual(self._items(date(2026, 8, 30)), [])

    def test_a_corrupt_cursor_reads_as_never_not_as_fine(self):
        (self.fed / "curate" / "seen.json").write_text("{not json", encoding="utf-8")
        items = self._items(date(2026, 8, 30))
        self.assertIn("never recorded", items[0]["detail"])

    def test_none_fed_root_omits_the_signal(self):
        self.assertIsNone(metrics.mine_curate(None))

    def test_the_pending_count_is_none_when_it_cannot_be_counted(self):
        """`None` is not zero. A report that cannot reach the producer files must not
        claim the queue is empty (`declare-what-a-check-assumes`)."""
        self._seen("20260727-194210Z")
        self.assertIsNone(metrics.mine_curate(str(self.fed))["pending"])

    def test_a_pending_count_appears_in_the_detail_when_known(self):
        self._seen("20260727-194210Z")
        data = metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=date(2026, 8, 30),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        data["curate"]["pending"] = 38
        items = [e for e in metrics.compute_exceptions(data) if e["kind"] == "curate"]
        self.assertIn("38 producer entries waiting", items[0]["detail"])

    def test_a_zero_pending_count_adds_no_noise(self):
        self._seen("20260727-194210Z")
        data = metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=date(2026, 8, 30),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        data["curate"]["pending"] = 0
        items = [e for e in metrics.compute_exceptions(data) if e["kind"] == "curate"]
        self.assertNotIn("waiting", items[0]["detail"])

    def _detail_for_pending(self, pending):
        self._seen("20260727-194210Z")
        data = metrics.mine(roots=[], repo_paths=None, fed_root=str(self.fed),
                            today=date(2026, 8, 30),
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        data["curate"]["pending"] = pending
        items = [e for e in metrics.compute_exceptions(data) if e["kind"] == "curate"]
        return items[0]["detail"]

    def test_an_uncountable_queue_renders_differently_from_an_empty_one(self):
        """WI-0211, on this surface. `mine_curate` has always returned None for "could
        not count" and 0 for "counted, clear" — but the renderer tested them with
        `not pend`, which is true for both, so the two collapsed into the same silence
        on the one signal whose job is making canon debt visible."""
        blind = self._detail_for_pending(None)
        clear = self._detail_for_pending(0)
        self.assertNotEqual(blind, clear)
        self.assertIn("NOT COUNTED", blind)

    def test_the_three_pending_renderings_are_all_different(self):
        self.assertEqual(
            len({self._detail_for_pending(None),
                 self._detail_for_pending(0),
                 self._detail_for_pending(38)}), 3)


class ConformanceRenderingTest(unittest.TestCase):
    """One aggregated exception line per system; per-item detail in EVIDENCE.md."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _data(self):
        return metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def test_one_aggregated_line_with_counts(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-14T08:00:00", ended="2026-07-14T10:00:00")  # boilerplate
        _journal(repo, "j2", "2026-07-10T08:00:00")  # unclosed
        items = [e for e in metrics.compute_exceptions(self._data())
                 if e["kind"] == "conformance"]
        self.assertEqual(len(items), 1)
        self.assertIn("1 missing-narrative", items[0]["detail"])
        self.assertIn("1 unclosed", items[0]["detail"])
        # WI-0207 (c): the row points at the COMMAND, because EVIDENCE.md does not
        # exist until someone runs it — a reader sent to an absent file learns nothing.
        self.assertIn("--evidence", items[0]["detail"])
        self.assertNotIn("detail in EVIDENCE.md", items[0]["detail"])

    def test_evidence_carries_detail_and_clean_list(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-14T08:00:00", ended="2026-07-14T10:00:00")
        (repo / "ROADMAP.md").write_text("**As of:** 2026-07-14\n", encoding="utf-8")
        _make_system(self.base, "cleansys", last_active="2026-07-15")
        text = metrics.render_evidence(self._data())
        self.assertIn("## Ritual conformance (ADR-0053)", text)
        self.assertIn("missing-narrative", text)
        self.assertIn("j1.md", text)
        self.assertIn("Clean: cleansys", text)


class MachineClosedIsNotALapseTest(unittest.TestCase):
    """WI-0207. A journal closed by the janitor, `reap` or `resolve-orphan` was closed
    AFTER the session that owned it was already dead. It could not have written a
    narrative, and the span between its last live stamp and the sweep that found it is
    death evidence read correctly — so neither is a ritual violation by anyone.

    BURYING THE SIGNAL is what happened before this split existed: most narrative-less
    closes scored as `missing-narrative` were machine closes, and the few human closes
    actually worth reading were lost inside a violation count nobody read — the
    exact failure a conformance metric exists to prevent (ADR-0053).

    The fix is a THIRD answer, not a quieter violation: machine closes are counted and
    shown under `informational`, never scored. Dropping them instead would hide the
    reaper's behaviour, which is the `ship-the-detector-with-the-capability` failure in
    the other direction."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _conf(self, sid):
        data = metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return data["systems"][sid]["conformance"]

    def test_every_machine_closer_is_counted_but_not_scored(self):
        # Driven from the module's own list, so a closer added there without a decision
        # about scoring cannot slip through this test.
        for i, closer in enumerate(metrics.MACHINE_CLOSERS):
            sid = f"m{i}"
            repo = _make_system(self.base, sid, last_active="2026-07-15")
            _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                     closed_by=f"{closer} (some reason)")
            conf = self._conf(sid)
            self.assertNotIn("missing-narrative", conf["recent"],
                             f"{closer} closed it; the session could not have narrated")
            self.assertEqual(len(conf["informational"]["machine-closed-no-narrative"]), 1,
                             f"{closer} must stay VISIBLE, just unscored")

    def test_a_journal_that_does_not_say_who_closed_it_is_still_scored(self):
        # The honest direction: an unknown closer stays visible as a violation rather
        # than being quietly excused (`declare-what-a-check-assumes`).
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00")
        conf = self._conf("a")
        self.assertEqual(len(conf["recent"]["missing-narrative"]), 1)
        self.assertEqual(conf["informational"], {})

    def test_the_live_sessions_own_close_is_still_scored(self):
        # `session.py end` is code, but it is the LIVE session's close — a session that
        # reached it could have written a narrative, so it is a lapse and stays scored.
        repo = _make_system(self.base, "b", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 closed_by="session.py end")
        conf = self._conf("b")
        self.assertEqual(len(conf["recent"]["missing-narrative"]), 1)
        self.assertNotIn("machine-closed-no-narrative", conf["informational"])

    def test_a_hand_reconstructed_close_is_still_scored(self):
        # An Architect rebuilding a close by hand is a person, not machinery.
        repo = _make_system(self.base, "c", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 closed_by="reconstruction (session ~148)")
        self.assertEqual(len(self._conf("c")["recent"]["missing-narrative"]), 1)

    def test_the_impossible_span_a_correct_reap_produces_is_not_scored(self):
        # The reaper closes a dropped lane at its exit marker, hours after the session
        # died. The 35h span IS the evidence being read correctly.
        repo = _make_system(self.base, "d", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-13T08:00:00", ended="2026-07-14T10:00:00",
                 body="- real work\n",
                 closed_by="reap (dropped — closed at its exit marker)")
        conf = self._conf("d")
        self.assertNotIn("insane-duration", conf["recent"])
        self.assertEqual(len(conf["informational"]["machine-closed-duration"]), 1)

    def test_a_human_close_with_an_impossible_span_is_still_scored(self):
        repo = _make_system(self.base, "e", last_active="2026-07-15")
        _journal(repo, "j1", "2026-07-13T08:00:00", ended="2026-07-14T10:00:00",
                 body="- real work\n", closed_by="session.py end")
        self.assertEqual(len(self._conf("e")["recent"]["insane-duration"]), 1)

    def test_the_reason_in_parentheses_does_not_defeat_the_match(self):
        # Real closers append their reason: `reap (dropped — closed at its exit marker)`.
        self.assertTrue(metrics._machine_closed("reap (dropped — closed at its exit marker)"))
        self.assertTrue(metrics._machine_closed("janitor (no evidence, aged out)"))
        self.assertTrue(metrics._machine_closed("resolve-orphan"))
        self.assertFalse(metrics._machine_closed("session.py end"))
        self.assertFalse(metrics._machine_closed(None))
        self.assertFalse(metrics._machine_closed(""))

    def test_an_unscored_fact_outside_the_window_is_not_historic_debt(self):
        # It was never a violation, so there is nothing for a debt counter to mean.
        repo = _make_system(self.base, "f", last_active="2026-07-15")
        _journal(repo, "j1", "2026-05-01T08:00:00", ended="2026-05-01T10:00:00",
                 closed_by="reap (dropped)")
        conf = self._conf("f")
        self.assertNotIn("machine-closed-no-narrative", conf["historic"])
        self.assertNotIn("missing-narrative", conf["historic"])


class ManualBriefStampabilityTest(unittest.TestCase):
    """WI-0207. `applied-unstamped` demanded a stamp that `apply: manual` briefs were
    structurally incapable of carrying: `session.py file_applied_brief()` — the only
    `applied:` writer that existed — runs on the auto path only. Every brief
    firing this check was, by construction, an `apply: manual` one.

    The fix is NOT an exemption — that would leave a manual apply with no receipt at all
    (`ship-the-detector-with-the-capability`). The SUBSTRATE changed: WI-0223 gave
    `adopt-runner.py` a `file_and_stamp_brief()` that files and stamps manual briefs
    itself, minting a fence where there is none. So the check now asks the only question
    that means anything — could ANY writer have stamped this? — with the cutover operator
    ruled on WI-0256.

    Dates are derived from `metrics.MANUAL_STAMP_CUTOVER` and the mtimes are real, so
    this runs against the shipped constant rather than a patched stand-in
    (`verify-in-the-created-configuration`)."""

    CUTOVER = metrics.MANUAL_STAMP_CUTOVER
    TODAY = metrics.MANUAL_STAMP_CUTOVER + timedelta(days=10)
    NOW = datetime.combine(metrics.MANUAL_STAMP_CUTOVER + timedelta(days=10),
                           datetime.min.time()).replace(hour=12).timestamp()

    MANUAL = ("---\nedit-id: b\napply: manual\nmanual-reason: attended\n"
              "---\n**Edit ID:** b\n")
    AUTO = "---\nedit-id: b\napply: auto\n---\n**Edit ID:** b\n"
    FENCELESS = "**Edit ID:** b\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _at(self, days_from_cutover):
        """A real mtime, `days` either side of the shipped cutover date."""
        d = self.CUTOVER + timedelta(days=days_from_cutover)
        return datetime.combine(d, datetime.min.time()).replace(hour=12).timestamp()

    def _conf(self, sid, body, filed):
        repo = _make_system(self.base, sid, last_active=str(self.TODAY),
                            applied=[("x-arch", "b.md", body)])
        os.utime(repo / "proposed-edits" / "x-arch" / "applied" / "b.md", (filed, filed))
        data = metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})
        return data["systems"][sid]["conformance"]

    def test_a_manual_brief_filed_before_the_cutover_is_not_an_audit_gap(self):
        # No writer existed that could have stamped it. This is the 16-of-16 class.
        conf = self._conf("a", self.MANUAL, self._at(-1))
        self.assertNotIn("applied-unstamped", conf["recent"])
        self.assertNotIn("applied-unstamped", conf["historic"])

    def test_a_manual_brief_filed_after_the_cutover_IS_an_audit_gap(self):
        # The teeth. adopt-runner files-and-stamps from here on, so a missing stamp is
        # a real missing receipt — this is the assertion that fails if the fix became
        # an exemption.
        conf = self._conf("b", self.MANUAL, self._at(0))
        self.assertEqual(len(conf["recent"]["applied-unstamped"]), 1)

    def test_an_auto_brief_before_the_cutover_is_unchanged(self):
        # The zero-touch engine has stamped fenced auto-applies since it shipped; the
        # check was always right about this class and must stay right about it.
        conf = self._conf("c", self.AUTO, self._at(-1))
        self.assertEqual(len(conf["recent"]["applied-unstamped"]), 1)

    def test_a_fenceless_brief_before_the_cutover_stays_exempt(self):
        conf = self._conf("d", self.FENCELESS, self._at(-1))
        self.assertNotIn("applied-unstamped", conf["recent"])
        self.assertNotIn("applied-unstamped", conf["historic"])

    def test_a_fenceless_brief_after_the_cutover_is_an_audit_gap(self):
        # adopt-runner's `_inject_stamp` MINTS a fence, so `has_frontmatter` no longer
        # bounds what can carry a receipt. Nothing on disk is in this class today; the
        # check keeps its teeth for the class that can next occur.
        conf = self._conf("e", self.FENCELESS, self._at(0))
        self.assertEqual(len(conf["recent"]["applied-unstamped"]), 1)

    def test_a_brief_that_declares_no_apply_mode_is_not_read_as_manual(self):
        # `apply:` became mandatory at ADR-0049; silence means the brief predates the
        # partition. Reading it as manual would retroactively drop a scored class.
        conf = self._conf("f", "---\nedit-id: b\n---\n**Edit ID:** b\n",
                          self._at(-1))
        self.assertEqual(len(conf["recent"]["applied-unstamped"]), 1)

    def test_an_undateable_brief_reads_as_pre_cutover_not_as_post(self):
        # A brief with no readable mtime dates to the epoch, so the MANUAL class stays
        # out of the count rather than having a gap invented for it. Erring the other
        # way would resurrect exactly the 16-of-16 false lines this item removed.
        self.assertFalse(metrics._brief_was_stampable({}))
        self.assertFalse(metrics._brief_was_stampable(
            {"mtime": None, "has_frontmatter": True, "apply_mode": "manual"}))
        # An AUTO brief is unaffected: its date decides recent-vs-historic, never
        # whether a writer existed that could have stamped it.
        self.assertTrue(metrics._brief_was_stampable(
            {"mtime": None, "has_frontmatter": True, "apply_mode": "auto"}))

    def test_the_cutover_matches_the_guard_the_operator_ruled_on(self):
        # WI-0256 ruled 2026-09-04 for the sibling guard in session.py. If the two
        # surfaces disagree, one brief is a violation on one and clean on the other.
        self.assertEqual(metrics.MANUAL_STAMP_CUTOVER, date(2026, 9, 4))


class UnscoredFactsRideTheRowTest(unittest.TestCase):
    """WI-0207. Machine closes must be VISIBLE without being SCORED. They ride the
    system's existing conformance row and are named in EVIDENCE.md — but a system whose
    only finding is "the janitor closed four journals" has nothing to attend to, and an
    attention list that prints a row for it has learned nothing from what this fixed."""

    NOW = metrics.datetime(2026, 7, 15, 12, 0).timestamp()
    TODAY = date(2026, 7, 15)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _data(self):
        return metrics.mine(roots=[str(self.base)], repo_paths=None, fed_root=None,
                            today=self.TODAY, now_ts=self.NOW,
                            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def test_unscored_counts_ride_a_row_that_already_exists(self):
        repo = _make_system(self.base, "a", last_active="2026-07-15")
        (repo / "ROADMAP.md").write_text("**As of:** 2026-07-15\n", encoding="utf-8")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00")
        _journal(repo, "j2", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 closed_by="reap (dropped)")
        rows = [e for e in metrics.compute_exceptions(self._data())
                if e["kind"] == "conformance"]
        self.assertEqual(len(rows), 1)
        self.assertIn("1 missing-narrative", rows[0]["detail"])
        self.assertIn("not scored", rows[0]["detail"])
        self.assertIn("1 machine-closed-no-narrative", rows[0]["detail"])
        # THE ASSERTION THIS CLASS EXISTS FOR: two narrative-less journals, one of
        # them machine-closed, and the headline total says ONE. Before WI-0207 it said
        # two, and that is how 11 impossible violations buried 4 real ones.
        self.assertIn("1 ritual violation(s)", rows[0]["detail"])
        self.assertNotIn("2 ritual violation", rows[0]["detail"])

    def test_a_system_with_only_unscored_facts_gets_no_attention_row(self):
        repo = _make_system(self.base, "b", last_active="2026-07-15")
        (repo / "ROADMAP.md").write_text("**As of:** 2026-07-15\n", encoding="utf-8")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 closed_by="janitor (no evidence, aged out)")
        rows = [e for e in metrics.compute_exceptions(self._data())
                if e["kind"] == "conformance"]
        self.assertEqual(rows, [])

    def test_evidence_names_the_unscored_journals_and_who_closed_them(self):
        repo = _make_system(self.base, "c", last_active="2026-07-15")
        (repo / "ROADMAP.md").write_text("**As of:** 2026-07-15\n", encoding="utf-8")
        _journal(repo, "j1", "2026-07-15T08:00:00", ended="2026-07-15T10:00:00",
                 closed_by="janitor (no evidence, aged out)")
        text = metrics.render_evidence(self._data())
        self.assertIn("machine-closed-no-narrative (not scored)", text)
        self.assertIn("janitor", text)
        # And it is NOT filed away as clean — that would hide the reaper entirely.
        self.assertNotIn("Clean: c.", text)


class BriefRootIsDeclaredNotAssumedTest(unittest.TestCase):
    """WI-0029 — the substrate encoded the FEDERATION's layout as universal.

    `mine_briefs` read `<repo>/proposed-edits` literally, on a MEMBER's repo, while the
    member declares `inbox` in its own `session.config.json`. A member with a different
    layout mined zero briefs and reported a clean `pending_count: 0` — the pattern the
    parent brief names: the assurance surface reports success exactly where it is blind.
    `_handoff_name` in this same module already had the fix for the handoff filename; the
    inbox never got it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = pathlib.Path(self._tmp.name) / "member"
        self.repo.mkdir()

    def _declare(self, inbox):
        (self.repo / "session.config.json").write_text(
            json.dumps({"architect_id": "member-arch", "inbox": inbox}), encoding="utf-8")

    def _brief(self, rel):
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("---\napply: manual\nmanual-reason: attended\n---\n", encoding="utf-8")

    def test_a_declared_non_default_inbox_is_actually_mined(self):
        self._declare("comms/inbox/member-arch/pending")
        self._brief("comms/inbox/member-arch/pending/2026-08-01-a.md")
        out = metrics.mine_briefs(self.repo)
        self.assertTrue(out["checked"])
        self.assertEqual(len(out["pending"]), 1,
                         "a member that declares its own layout must still be mined")

    def test_the_default_layout_still_works_undeclared(self):
        """Every current member is on the default; none may shift."""
        self._brief("proposed-edits/member-arch/pending/2026-08-01-a.md")
        out = metrics.mine_briefs(self.repo)
        self.assertTrue(out["checked"])
        self.assertEqual(len(out["pending"]), 1)
        self.assertFalse(out["declared"], "fell back to the standard default")

    def test_an_unreadable_inbox_is_distinct_from_an_empty_one(self):
        """The half that matters: 'nothing waiting' and 'I could not look' used to be
        the same output, so a blind mine reported a clean zero."""
        self._declare("proposed-edits/member-arch/pending")
        out = metrics.mine_briefs(self.repo)          # nothing on disk at all
        self.assertFalse(out["checked"])
        self.assertEqual(out["pending"], [])

        self._brief("proposed-edits/member-arch/pending/x.md")
        (self.repo / "proposed-edits/member-arch/pending/x.md").unlink()
        out = metrics.mine_briefs(self.repo)          # the dir exists and IS empty
        self.assertTrue(out["checked"])
        self.assertEqual(out["pending"], [])

    def test_a_malformed_declaration_falls_back_rather_than_escaping_the_repo(self):
        """A one-segment inbox is not the `<root>/<arch>/pending` shape; deriving a
        grandparent from it would point above the repo."""
        self._declare("pending")
        root, declared = metrics._briefs_root(self.repo)
        self.assertFalse(declared)
        self.assertEqual(root, self.repo / "proposed-edits")


class ShallowMineDeclaresItsScopeTest(unittest.TestCase):
    """The startup `--status` path mines with `deep=False` and skips the role-doc bump
    check for cost. Until this was declared, its count could come out LOWER than
    `--exceptions` with nothing on either surface saying why — a session start reporting
    35 while the on-demand report listed more, which reads as one of them being broken.
    `declare-what-a-check-assumes`: a skipped check must not render as a clean one."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = pathlib.Path(self._tmp.name)
        self.today = date(2026, 7, 15)

    def tearDown(self):
        self._tmp.cleanup()

    def _mine(self, deep):
        return metrics.mine(
            roots=[str(self.base)], repo_paths=None, fed_root=None,
            today=self.today, deep=deep,
            cadence={"defaults": {"cadence_days": 7}, "systems": {}})

    def _system_with_a_shallow_visible_violation(self, sid, *, checkable=False):
        # An applied brief carrying frontmatter but no applied-stamp -> `applied-unstamped`,
        # which BOTH mines can see. So the row exists either way and the only difference
        # under test is whether the skipped check is named on it.
        repo = _make_system(self.base, sid, last_active=self.today.isoformat(),
                            applied=[("federation-arch", "b.md", "---\nedit-id: b\n---\n# b\n")])
        if checkable:
            # The deep check needs BOTH a declared role doc and a real git history to walk.
            # Without them it fail-softs to None and is legitimately "not checked" — which
            # is why the deep assertion below needs a repo where the check can actually run.
            (repo / "session.config.json").write_text(
                json.dumps({"role_doc": "role.md"}), encoding="utf-8")
            (repo / "role.md").write_text("**Version:** 1.0.0\n", encoding="utf-8")
            git = ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
                   "-c", "commit.gpgsign=false"]
            subprocess.run(git + ["init", "-q", "-b", "main"], check=True, capture_output=True)
            subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
            subprocess.run(git + ["commit", "-qm", "fixture"], check=True, capture_output=True)
        return repo

    def test_conformance_reports_the_roledoc_check_as_unchecked_when_shallow(self):
        self._system_with_a_shallow_visible_violation("alpha")
        conf = self._mine(deep=False)["systems"]["alpha"]["conformance"]
        self.assertEqual(conf["unchecked"], ["roledoc-no-bump"])

    def test_a_ran_roledoc_check_reports_nothing_unchecked(self):
        # Unit-level: a mine that DID run the check passes a violations dict, empty or not.
        conf = metrics.mine_conformance(
            self.base, sessions=[], journals=[], status_fm={}, applied_briefs=[],
            roledoc_bumps={"violations": []}, today=self.today,
            now_ts=1_000_000_000.0)
        self.assertEqual(conf["unchecked"], [])

    def test_the_shallow_row_names_the_check_it_skipped(self):
        self._system_with_a_shallow_visible_violation("beta")
        shallow = [e for e in metrics.compute_exceptions(self._mine(deep=False))
                   if e["kind"] == "conformance"]
        self.assertEqual(len(shallow), 1)
        self.assertIn("[not checked: roledoc-no-bump]", shallow[0]["detail"])

    def test_the_deep_row_claims_no_skipped_check(self):
        self._system_with_a_shallow_visible_violation("gamma", checkable=True)
        deep = [e for e in metrics.compute_exceptions(self._mine(deep=True))
                if e["kind"] == "conformance"]
        self.assertEqual(len(deep), 1)
        self.assertNotIn("not checked", deep[0]["detail"])

    def test_a_git_failure_reads_as_unchecked_not_as_clean(self):
        """The same fail-soft path the shallow mine uses: `mine_roledoc_bumps` returns
        None on any git error, and a repo with no git history is exactly that case. It
        must not come back looking like a check that ran and passed."""
        self._system_with_a_shallow_visible_violation("zeta")  # no git init
        conf = self._mine(deep=True)["systems"]["zeta"]["conformance"]
        self.assertEqual(conf["unchecked"], ["roledoc-no-bump"])

    def test_status_line_declares_startup_scope(self):
        self._system_with_a_shallow_visible_violation("delta")
        self.assertIn("startup scope", metrics.render_status(self._mine(deep=False)))

    def test_status_line_is_silent_about_scope_when_mined_deep(self):
        self._system_with_a_shallow_visible_violation("epsilon")
        self.assertNotIn("startup scope", metrics.render_status(self._mine(deep=True)))

    def test_an_all_clear_startup_line_also_carries_the_scope(self):
        """The dangerous case: 'all clear' from a mine that did not run every check."""
        line = metrics.render_status(self._mine(deep=False))
        self.assertTrue(line.startswith("Metrics: all clear"))
        self.assertIn("startup scope", line)


# ------------------------------------------------- WI-0159: escalations + fallback queue


class LedgerSectionTest(unittest.TestCase):
    """`_ledger_section` — the structural read that replaces phrase-mining.

    The property that matters most is the three-state one: an ABSENT section is None,
    and a caller that turns that into zero has built the instrument this metric exists
    to avoid. A present-but-empty section IS zero, and the two must not collapse.
    """

    def test_an_absent_section_is_none_not_zero(self):
        body = "### What happened\n\n- did a thing\n"
        self.assertIsNone(metrics._ledger_section(body, metrics.ESCALATION_HEADINGS))

    def test_a_present_but_empty_section_is_zero_not_none(self):
        body = "### Open questions for the user\n\nNone this session.\n"
        self.assertEqual(0, metrics._ledger_section(body, metrics.ESCALATION_HEADINGS))

    def test_both_heading_spellings_are_the_same_section(self):
        for head in ("### Open questions", "### Open questions for the user"):
            with self.subTest(head=head):
                self.assertEqual(
                    1, metrics._ledger_section(head + "\n\n- one\n",
                                               metrics.ESCALATION_HEADINGS))

    def test_bullets_and_numbers_both_count_as_entries(self):
        body = ("### Decisions I made without you, for review\n\n"
                "1. **First.** Reasoning.\n"
                "2) Second.\n"
                "- Third.\n"
                "* Fourth.\n")
        self.assertEqual(4, metrics._ledger_section(body, metrics.DECISION_HEADINGS))

    def test_the_section_stops_at_the_next_heading(self):
        body = ("### Open questions for the user\n\n- only mine\n\n"
                "### Notes\n\n- not mine\n- also not mine\n")
        self.assertEqual(1, metrics._ledger_section(body, metrics.ESCALATION_HEADINGS))

    def test_continuation_lines_are_not_counted_as_entries(self):
        """A wrapped entry is one entry; indentation alone must not mint another."""
        body = ("### Open questions for the user\n\n"
                "- **One question.** It runs on\n"
                "  to a second line, and a third.\n")
        self.assertEqual(1, metrics._ledger_section(body, metrics.ESCALATION_HEADINGS))


class LedgerSummaryTest(unittest.TestCase):
    """`summarize_ledger` — the window, and what silence is allowed to mean."""

    def _j(self, days_ago, esc, dec, today=date(2026, 9, 4)):
        started = None
        if days_ago is not None:
            started = (datetime(today.year, today.month, today.day)
                       - timedelta(days=days_ago))
        return {"started": started, "escalations": esc, "decisions": dec}

    def test_silent_journals_are_counted_apart_and_never_as_zero(self):
        today = date(2026, 9, 4)
        js = [self._j(1, 3, 0), self._j(2, None, None), self._j(3, None, None)]
        s = metrics.summarize_ledger(js, today)
        self.assertEqual(3, s["escalations"])
        self.assertEqual(1, s["escalation_sessions"])
        self.assertEqual(2, s["silent"])
        self.assertEqual(3, s["sessions"])

    def test_a_journal_outside_the_window_is_excluded(self):
        today = date(2026, 9, 4)
        s = metrics.summarize_ledger([self._j(200, 9, 0)], today, window_days=30)
        self.assertEqual(0, s["sessions"])
        self.assertEqual(0, s["escalations"])

    def test_the_window_is_driven_from_both_sides(self):
        """Guards the > vs >= slip: exactly at the edge is out, one day inside is in."""
        today = date(2026, 9, 4)
        self.assertEqual(0, metrics.summarize_ledger([self._j(30, 1, 0)], today,
                                                     window_days=30)["sessions"])
        self.assertEqual(1, metrics.summarize_ledger([self._j(29, 1, 0)], today,
                                                     window_days=30)["sessions"])

    def test_an_undated_journal_is_not_assumed_recent(self):
        today = date(2026, 9, 4)
        s = metrics.summarize_ledger([self._j(None, 5, 0)], today)
        self.assertEqual(0, s["sessions"])
        self.assertEqual(1, s["undated"])


class FallbackQueueTest(unittest.TestCase):
    """`mine_fallback_queue` — number (2), and every way it could lie about zero."""

    GROUP = metrics.FALLBACK_QUEUE_GROUP

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = pathlib.Path(self._tmp.name) / "repo"
        (self.repo / "work-items").mkdir(parents=True)
        self._run("init", "-q", "-b", "main")
        self._run("config", "user.email", "t@example.com")
        self._run("config", "user.name", "t")

    def _run(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                       capture_output=True, text=True)

    def _item(self, wid, *, status="open", group=None, blocked="", days_ago=1):
        """An item in the SHAPE `_wi_render_item` writes — blank fields included,
        because the blank-field line break is where the field parser used to break."""
        text = ("# %s: a title\n\n"
                "- status: %s\n"
                "- section: next\n"
                "- blocked-by: %s\n"
                "- group: %s\n"
                "- source: \n"
                "- impact: fix\n"
                "- version: \n\n"
                "Some notes.\n") % (wid, status, blocked, group or "")
        (self.repo / "work-items" / ("%s-a-title.md" % wid)).write_text(text)
        when = (datetime.now() - timedelta(days=days_ago)).isoformat()
        env = dict(os.environ)
        env.update({"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m", "add " + wid],
                       check=True, capture_output=True, env=env)

    def test_an_empty_preceding_field_does_not_steal_the_group_line(self):
        """The regression that made the live federation store read depth 0.

        `- blocked-by: ` is written EMPTY for most items. A field pattern whose
        post-colon whitespace class matches a newline swallows the break and captures
        `- group: lane-last-mile` as blocked-by's value — so `group` reads as absent and
        every genuinely-queued item vanishes from the count. Zero, from a store full of
        queued work, is the exact failure this metric exists to refuse.
        """
        self._item("WI-0001", group=self.GROUP, blocked="")
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(1, q["depth"])
        self.assertEqual(["WI-0001"], q["ids"])

    def test_no_store_is_none_not_an_empty_queue(self):
        empty = pathlib.Path(self._tmp.name) / "nostore"
        empty.mkdir()
        self.assertIsNone(metrics.mine_fallback_queue(empty, date.today()))

    def test_a_group_never_used_here_is_unmarked_not_empty(self):
        self._item("WI-0001", group="something-else")
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(0, q["depth"])
        self.assertFalse(q["group_used"])

    def test_a_closed_item_still_marks_the_group_as_in_use(self):
        self._item("WI-0001", group=self.GROUP, status="done")
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(0, q["depth"])
        self.assertTrue(q["group_used"])

    def test_open_in_progress_and_held_are_all_still_queued(self):
        for i, st in enumerate(("open", "in-progress", "held"), start=1):
            self._item("WI-000%d" % i, group=self.GROUP, status=st)
        for i, st in enumerate(("done", "superseded"), start=4):
            self._item("WI-000%d" % i, group=self.GROUP, status=st)
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(3, q["depth"])

    def test_age_comes_from_the_first_add_because_the_item_carries_no_date(self):
        self._item("WI-0001", group=self.GROUP, days_ago=40)
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(40, q["oldest_days"])
        self.assertEqual(0, q["added_in_window"])   # filed before the window

    def test_growing_is_net_flow_not_arrivals(self):
        """Two filed and two closed in the window is FLAT, and must not read as growth."""
        self._item("WI-0001", group=self.GROUP, days_ago=5)
        self._item("WI-0002", group=self.GROUP, days_ago=5)
        self._item("WI-0003", group=self.GROUP, status="done", days_ago=4)
        self._item("WI-0004", group=self.GROUP, status="done", days_ago=4)
        q = metrics.mine_fallback_queue(self.repo, date.today())
        self.assertEqual(2, q["added_in_window"])
        self.assertEqual(2, q["closed_in_window"])

    def test_a_store_git_cannot_date_reports_unknown_not_young(self):
        nogit = pathlib.Path(self._tmp.name) / "bare"
        (nogit / "work-items").mkdir(parents=True)
        (nogit / "work-items" / "WI-0001-x.md").write_text(
            "# WI-0001: t\n\n- status: open\n- blocked-by: \n- group: %s\n"
            % self.GROUP)
        q = metrics.mine_fallback_queue(nogit, date.today())
        self.assertEqual(1, q["depth"])
        self.assertFalse(q["dated"])
        self.assertIsNone(q["added_in_window"])


class EscalationExceptionTest(unittest.TestCase):
    """The paired row — above all, that the QUIET failure is as loud as the noisy one."""

    def _data(self, *, escalations, silent=0, sessions=4, decisions=0, queue=None):
        return {
            "today": date(2026, 9, 4),
            "now_ts": 0,
            "deep": True,
            "cadence": {"defaults": {"cadence_days": 7}, "systems": {}},
            "system_ids": ["alpha"],
            "systems": {"alpha": {
                "repo": "/nonexistent-so-the-git-checks-stay-quiet",
                "status": {"last_active": date.today().isoformat(), "blocked": "false"},
                "conformance": {"recent": {}, "unchecked": []},
                "ledger": {"window_days": 30, "sessions": sessions,
                           "escalations": escalations,
                           "escalation_sessions": 1 if escalations else 0,
                           "decisions": decisions, "decision_sessions": 0,
                           "silent": silent, "undated": 0},
                "fallback_queue": queue,
            }},
            "pending_briefs": [], "orphans": [], "adopt_runner": None,
            "canon_size": None, "spot_audit": None, "curate": None,
        }

    def _q(self, **kw):
        base = {"group": "lane-last-mile", "depth": 0, "ids": [], "oldest_days": 0,
                "added_in_window": 0, "closed_in_window": 0, "dated": True,
                "stale_days": 30, "group_used": True}
        base.update(kw)
        return base

    def _rows(self, data):
        return [e for e in metrics.compute_exceptions(data) if e["kind"] == "escalation"]

    def test_escalations_above_zero_surface_with_both_numbers(self):
        rows = self._rows(self._data(escalations=7,
                                     queue=self._q(depth=2, oldest_days=5)))
        self.assertEqual(1, len(rows))
        self.assertIn("7 escalation(s)", rows[0]["detail"])
        self.assertIn("depth 2", rows[0]["detail"])

    def test_zero_escalations_with_a_flat_queue_is_silent(self):
        """The one genuinely good state — and the only one allowed to say nothing."""
        self.assertEqual([], self._rows(self._data(escalations=0, queue=self._q())))

    def test_zero_escalations_with_a_growing_queue_is_LOUD(self):
        rows = self._rows(self._data(escalations=0, queue=self._q(
            depth=5, added_in_window=4, closed_in_window=1)))
        self.assertEqual(1, len(rows))
        self.assertIn("escalations at ZERO", rows[0]["detail"])
        self.assertIn("failure mode", rows[0]["detail"])

    def test_zero_escalations_with_a_DRAINING_queue_is_silent(self):
        """The row-level net-flow guard. Arrivals alone must not fire it: a fortnight
        that took four in and closed four is flat, and a loud row that is always on is a
        loud row nobody reads — which costs more than the signal is worth."""
        self.assertEqual([], self._rows(self._data(escalations=0, queue=self._q(
            depth=3, added_in_window=4, closed_in_window=4))))

    def test_zero_escalations_with_an_ageing_queue_is_LOUD(self):
        rows = self._rows(self._data(escalations=0,
                                     queue=self._q(depth=2, oldest_days=31)))
        self.assertEqual(1, len(rows))
        self.assertIn("escalations at ZERO", rows[0]["detail"])

    def test_the_ageing_threshold_is_driven_from_both_sides(self):
        at = self._rows(self._data(escalations=0,
                                   queue=self._q(depth=2, oldest_days=30)))
        past = self._rows(self._data(escalations=0,
                                     queue=self._q(depth=2, oldest_days=31)))
        self.assertEqual([], at)
        self.assertEqual(1, len(past))

    def test_an_undated_queue_is_not_read_as_healthy(self):
        rows = self._rows(self._data(escalations=0,
                                     queue=self._q(depth=3, dated=False)))
        self.assertEqual(1, len(rows))
        self.assertIn("age UNKNOWN", rows[0]["detail"])

    def test_an_unmarked_group_says_unknown_rather_than_zero(self):
        rows = self._rows(self._data(escalations=1, queue=self._q(group_used=False)))
        self.assertIn("UNMARKED", rows[0]["detail"])
        self.assertIn("not zero", rows[0]["detail"])

    def test_silent_journals_are_declared_on_the_row(self):
        rows = self._rows(self._data(escalations=2, silent=9, queue=self._q()))
        self.assertIn("NOT counted as zero", rows[0]["detail"])

    def test_no_store_reads_as_not_measurable_never_as_clean(self):
        rows = self._rows(self._data(escalations=1, queue=None))
        self.assertIn("not measurable", rows[0]["detail"])

    def test_the_blind_spot_is_no_longer_a_blind_spot(self):
        """WI-0278 built the source, so the name comes OFF the list.

        This test used to assert the opposite, and the inversion is the point rather than
        a maintenance chore: `NOT_YET_MINED` is a declaration that the report cannot see a
        signal, and a name left there after the source exists understates the number as
        thoroughly as the silent omission the list was invented to prevent. The mining
        half is pinned in `tests/test_escalation_ledger.py`; what belongs here is that the
        report no longer disclaims a number it now emits."""
        self.assertNotIn("mid-session-escalations", metrics.NOT_YET_MINED)

    def test_the_row_splits_close_time_from_mid_session(self):
        """"9 escalations" reads very differently when one was written down at close and
        eight interrupted a live session — and that difference IS WI-0274's finding."""
        data = self._data(escalations=9, queue=self._q())
        data["systems"]["alpha"]["ledger"].update(escalations_at_close=1,
                                                  escalations_midsession=8)
        detail = self._rows(data)[0]["detail"]
        self.assertIn("1 at close", detail)
        self.assertIn("8 mid-session", detail)


if __name__ == "__main__":
    unittest.main()
