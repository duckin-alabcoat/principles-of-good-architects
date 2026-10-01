"""ADR-0106 — residency: where a system runs, and where its production data lives.

The 2026-08-22 ruling split the machines (development on one, production on another)
and it lived in two work-item notes with nothing in the substrate able to read it. These tests pin the parts where being *almost* right is indistinguishable from
being right.

**Every case passes `machine` explicitly.** A test that called `detect_machine()` would
assert the Runner's behaviour from devbox and prove nothing about either — the same
discipline `test_data_root.py` holds for `$HOME`, and for the same reason: the whole
subject is two machines behaving differently.

The load-bearing cases, and what each exists to stop:

* **UNDECLARED never collapses into "no production".** Three of the four states are easy;
  the fourth is the one that matters, because a member that has simply not answered yet must
  not read as one that answered "no". That collapse is what printed `inbox: (empty)` over two
  live briefs in session 90.
* **`is_production_host` returns None, not False, on undeclared.** Both would let a launch
  through today. They stop being the same answer the moment anything downstream asks "is this
  a production host?" and gets a confident No it was never entitled to.
* **The D6 refusal is WIRED, not merely written.** A `_pf_residency` that exists and is not in
  `PREFLIGHT_GATE_CHECKS` never fires, and the surface reads compliant while the production
  host is developed on daily — the ADR-0102 `d_trunk_integrate` lesson on a new surface.
"""

import importlib.util
import pathlib
import re
import unittest

import harness_fixture

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


session = _load("session_res", "session.py")
standard_check = _load("standard_check_res", "standard_check.py")


MAP = {"example-runner": "Runner", "example-dev": "DevBox",
       "example-laptop": "Laptop"}

# The federation's own shape: a DEV system that nonetheless owns a production process on
# the Runner (the ADR-0050 adoption runner). This config is the ADR's counter-example and
# the reason residency is two axes rather than one.
FED = {"architect_id": "federation-arch", "machine_map": MAP,
       "residency": {"process": "Runner", "data": None}}

# The mirror image: production data with no daemon at all. A one-axis design would put
# these on the dev box, which is the objection that killed it.
RUNNER_MEMBER = {"architect_id": "example-app-arch", "machine_map": MAP,
                 "residency": {"process": None, "data": "Runner"}}

# Dev-only, both axes null. Not the same fact as declaring nothing.
BOARD = {"architect_id": "demo-member-arch", "machine_map": MAP,
         "residency": {"process": None, "data": None}}

SILENT = {"architect_id": "mystery-arch", "machine_map": MAP}


class ResidencyOfTest(unittest.TestCase):
    def test_a_declared_block_normalises_to_labels(self):
        self.assertEqual(session.residency_of(FED)["process"], "Runner")
        self.assertIsNone(session.residency_of(FED)["data"])

    def test_a_missing_block_is_None_not_an_empty_declaration(self):
        """None is the signal for UNDECLARED. An empty dict here would read downstream as
        'declared, nothing set', which is a different and unearned claim."""
        self.assertIsNone(session.residency_of(SILENT))
        self.assertIsNone(session.residency_of(None))
        self.assertIsNone(session.residency_of({"residency": "Runner"}))

    def test_a_path_is_not_a_machine_label(self):
        """D2: values are machine_map labels, never paths. One home behind two path prefixes
        is the whole reason, so an absolute path must not survive normalisation as if it were a
        label nothing can resolve."""
        cfg = {"machine_map": MAP, "residency": {"process": "/Users/example"}}
        self.assertIsNone(session.residency_of(cfg)["process"])

    def test_a_stale_restart_key_is_ignored_not_rejected(self):
        """`restart` was withdrawn when ADR-0106 D3/D4 were superseded by ADR-0103 — the
        deploy contract's own `restart` field owns it now. A member that declared one
        under the old design must still LOAD: the key is dropped from the result, never
        raised on. Pinning ignored-not-rejected because the failure mode of the other
        choice is a member that cannot start a session after a substrate push."""
        cfg = {"machine_map": MAP,
               "residency": {"process": "Runner", "restart": "/bin/launchctl kickstart x"}}
        r = session.residency_of(cfg)
        self.assertNotIn("restart", r)
        self.assertEqual(r["process"], "Runner")


class FourStatesTest(unittest.TestCase):
    """D8. Four outcomes, and the value is entirely in their not collapsing."""

    def test_declared_resolves(self):
        state, _ = session.residency_state(FED, "DevBox")
        self.assertEqual(state, session.RESIDENCY_DECLARED)

    def test_undeclared_is_its_own_state_not_a_quiet_no(self):
        state, why = session.residency_state(SILENT, "DevBox")
        self.assertEqual(state, session.RESIDENCY_UNDECLARED)
        self.assertNotEqual(state, session.RESIDENCY_DECLARED)
        self.assertIn("NOT the same", why)

    def test_declaring_both_axes_null_is_DECLARED_not_undeclared(self):
        """The distinction the whole ADR turns on: 'I have no production mode' is an
        answer, and it must not read the same as never having been asked."""
        state, _ = session.residency_state(BOARD, "DevBox")
        self.assertEqual(state, session.RESIDENCY_DECLARED)

    def test_a_label_no_map_resolves_is_unresolvable_not_a_pass(self):
        """The WI-0148 failure. Only the federation carries a devbox row today, so a
        member handed this declaration would otherwise silently read as 'not here'."""
        cfg = {"architect_id": "x-arch",
               "machine_map": {"example-runner": "Runner"},
               "residency": {"process": "DevBox"}}
        state, why = session.residency_state(cfg, "Runner")
        self.assertEqual(state, session.RESIDENCY_UNRESOLVABLE)
        self.assertIn("DevBox", why)

    def test_NO_map_at_all_is_unresolvable_not_the_cleanest_possible_pass(self):
        """The same failure as the row above, in its worst form: a member with no
        `machine_map` can resolve NOTHING, so a declaration naming any machine is
        maximally unresolvable — and it used to read `declared`, the cleanest verdict
        this function has.

        The guard was `if known and v not in known`, which short-circuits the whole
        resolvability check the moment the map is empty. The tell that this was never
        intended is one line down, in the unresolvable message itself: it already
        renders the empty case as `it yields nothing` — a branch the guard made
        unreachable. An error string written for a state that cannot occur is the same
        shape as a fixture stem that cannot occur, and it hides the defect the same way.

        Blast radius measured before changing it: every member locatable from devbox
        carry a non-empty `machine_map`, so this flags zero members today. It can only
        fire on a member that has declared while carrying no map — which is the WI-0148
        failure with nothing at all to resolve against."""
        for label, cfg in (
            ("absent", {"architect_id": "x-arch", "residency": {"process": "Runner"}}),
            ("empty", {"architect_id": "x-arch", "machine_map": {},
                       "residency": {"process": "Runner"}}),
        ):
            with self.subTest(machine_map=label):
                state, why = session.residency_state(cfg, "DevBox")
                self.assertEqual(state, session.RESIDENCY_UNRESOLVABLE)
                self.assertIn("nothing", why)

    def test_the_disk_contradicting_the_declaration_is_its_own_state(self):
        state, _ = session.residency_state(RUNNER_MEMBER, "Runner",
                                           production_root_present=False)
        self.assertEqual(state, session.RESIDENCY_CONTRADICTED)

    def test_a_caller_that_did_not_look_cannot_reach_the_contradiction(self):
        """`production_root_present=None` means 'I did not check'. Reporting a
        contradiction from a check nobody ran would be a fabricated finding."""
        state, _ = session.residency_state(RUNNER_MEMBER, "Runner")
        self.assertEqual(state, session.RESIDENCY_DECLARED)

    def test_contradiction_needs_THIS_machine_to_be_the_data_host(self):
        """An absent production root on devbox is not a contradiction — the root is
        supposed to be on the Runner."""
        state, _ = session.residency_state(RUNNER_MEMBER, "DevBox",
                                           production_root_present=False)
        self.assertEqual(state, session.RESIDENCY_DECLARED)


class ProductionHostTest(unittest.TestCase):
    def test_the_declared_host_is_production(self):
        self.assertIs(session.is_production_host(FED, "Runner"), True)

    def test_another_machine_is_not(self):
        self.assertIs(session.is_production_host(FED, "DevBox"), False)

    def test_undeclared_is_None_and_that_is_NOT_False(self):
        """Both let a launch through today, so the distinction looks academic — and is
        not. False is a claim ('this is not a production host'); None is the absence of
        one. Anything downstream that trusts a False it was never entitled to develops on
        production and reports success."""
        self.assertIsNone(session.is_production_host(SILENT, "Runner"))
        self.assertIsNot(session.is_production_host(SILENT, "Runner"), False)

    def test_a_declared_null_process_really_is_False(self):
        self.assertIs(session.is_production_host(RUNNER_MEMBER, "Runner"), False)


class DataRoleTest(unittest.TestCase):
    """D7. The path formula does not change; what was missing is the ANSWER to which copy
    you are standing on."""

    def test_the_production_data_host_holds_the_production_copy(self):
        role, _ = session.data_role_of(RUNNER_MEMBER, "Runner")
        self.assertEqual(role, session.DATA_ROLE_PRODUCTION)

    def test_everywhere_else_is_the_dev_copy(self):
        role, why = session.data_role_of(RUNNER_MEMBER, "DevBox")
        self.assertEqual(role, session.DATA_ROLE_DEV)
        self.assertIn("Runner", why)

    def test_no_production_data_means_one_root_everywhere(self):
        self.assertEqual(session.data_role_of(FED, "Runner")[0], session.DATA_ROLE_DEV)
        self.assertEqual(session.data_role_of(FED, "DevBox")[0], session.DATA_ROLE_DEV)

    def test_undeclared_will_not_guess_which_copy_this_is(self):
        role, _ = session.data_role_of(SILENT, "Runner")
        self.assertEqual(role, session.DATA_ROLE_UNKNOWN)


class GateWiringTest(unittest.TestCase):
    """D6. A refusal that is written and not wired never fires."""

    def test_the_residency_check_is_in_the_launch_gate(self):
        self.assertIn(session._pf_residency, session.PREFLIGHT_GATE_CHECKS)

    def test_it_is_also_in_the_full_report(self):
        src = harness_fixture.harness_source()
        m = re.search(r"checks = \(([^)]*)\)", src)
        self.assertIsNotNone(m)
        self.assertIn("_pf_residency", m.group(1))

    def test_poga_forwards_the_promote_verb(self):
        """A verb session.py answers and poga does not forward is unreachable from the
        front door every member is told to use."""
        self.assertIn("promote)", (ROOT / "poga").read_text(encoding="utf-8"))


class DetectorTest(unittest.TestCase):
    """The conformance row. `ship-the-detector-with-the-capability`: an exemption that
    defaults to pass does not merely fail to detect a gap, it certifies it."""

    def test_this_repo_carries_the_capability(self):
        self.assertTrue(standard_check.d_residency(ROOT, None))

    def test_it_is_in_the_manifest_at_1_14_0(self):
        self.assertIn("residency", standard_check.CAPABILITIES)
        self.assertIn("residency", standard_check.required_set("1.14.0"))
        self.assertNotIn("residency", standard_check.required_set("1.13.0"))

    def test_a_member_with_the_verb_but_an_UNWIRED_gate_reads_absent(self, ):
        """The realistic partial, and the one a single string match would miss: the code
        is all there, the refusal is simply never called."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            repo = harness_fixture.install_harness(pathlib.Path(d))
            unwired = harness_fixture.patch_harness(
                repo,
                "PREFLIGHT_GATE_CHECKS = (_pf_terminfo, _pf_workspace_trust, "
                "_pf_onboarding,\n                         _pf_residency, "
                "_pf_memory_pressure)",
                "PREFLIGHT_GATE_CHECKS = (_pf_terminfo, _pf_workspace_trust, "
                "_pf_onboarding,\n                         _pf_memory_pressure)")
            self.assertEqual(unwired, 1,
                             "the gate tuple's shape moved — fix this fixture")
            self.assertFalse(standard_check.d_residency(repo, None))

    def test_a_member_with_no_harness_at_all_reads_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(standard_check.d_residency(pathlib.Path(d), None))


class PreflightVerdictTest(unittest.TestCase):
    """The three verdicts the gate acts on, mapped from the four states."""

    def _verdict(self, cfg, machine, env=None):
        old_cfg, old_detect = session.CFG, session.detect_machine
        old_env = dict(session.os.environ)
        try:
            session.CFG = cfg
            session.detect_machine = lambda: machine
            session.os.environ.pop(session.RESIDENCY_BYPASS_ENV, None)
            if env:
                session.os.environ.update(env)
            return session._pf_residency()
        finally:
            session.CFG, session.detect_machine = old_cfg, old_detect
            session.os.environ.clear()
            session.os.environ.update(old_env)

    def test_the_production_host_FAILS_so_the_gate_refuses(self):
        r = self._verdict(FED, "Runner")
        self.assertEqual(r.verdict, session.PREFLIGHT_FAIL)
        # Names the SHIP verb (ADR-0103) and the escape that is actually allowed. This
        # assertion used to read `assertIn("promote", ...)` and so PINNED a verb that
        # ADR-0103 had withdrawn: the refusal handed the operator a second command that
        # only refuses, and the test certified it. Assert the live verb, and assert the
        # plain-session escape is stated — that is the half a blocked operator needs.
        self.assertIn("poga deploy", r.remedy)
        self.assertIn("plain session", r.remedy)

    def test_no_preflight_remedy_advertises_a_WITHDRAWN_verb(self):
        """The structural guard, not a spot fix (`add-structural-guard-on-recurrence`).

        This is the SECOND time the substrate has refused an operator and then handed
        them a command it also refuses — WI-0099 fixed it once in `reap-lanes`, and the
        residency remedy grew the identical defect. A recurrence means discipline is not
        the control, so derive the forbidden set from the code instead of listing it:
        any `cmd_*` whose body declares itself WITHDRAWN is a verb no remedy may name.

        Scoped to the COMMAND FORM (`poga <verb>` / `session.py <verb>`) so that prose
        using the same word — "promote a principle to Accepted" — is not a false hit."""
        import inspect
        withdrawn = set()
        for name in dir(session):
            if not name.startswith("cmd_"):
                continue
            fn = getattr(session, name)
            if not callable(fn):
                continue
            try:
                src = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            if "WITHDRAWN" in src:
                withdrawn.add(name[len("cmd_"):].replace("_", "-"))
        self.assertIn("promote", withdrawn,
                      "expected `promote` to be detected as withdrawn — if it was "
                      "reinstated or deleted outright, this guard needs re-aiming")

        offences = []
        for cfg, machine in ((FED, "Runner"), (FED, "DevBox")):
            r = self._verdict(cfg, machine)
            for text in (r.detail or "", r.remedy or ""):
                for verb in withdrawn:
                    for form in (f"poga {verb}", f"session.py {verb}"):
                        if form in text:
                            offences.append(f"{machine}: {form!r} in {text!r}")
        self.assertEqual(offences, [], "a preflight verdict advertises a withdrawn verb")

    def test_the_dev_machine_passes(self):
        self.assertEqual(self._verdict(FED, "DevBox").verdict, session.PREFLIGHT_OK)

    def test_undeclared_is_UNKNOWN_so_the_gate_warns_and_lets_through(self):
        """Fail OPEN. session.py ships byte-identical fleet-wide; refusing here would
        turn a missing DECLARATION into a missing CAPABILITY on every member at once —
        ADR-0096 D6's argument, unchanged."""
        r = self._verdict(SILENT, "Runner")
        self.assertEqual(r.verdict, session.PREFLIGHT_UNKNOWN)
        self.assertNotEqual(r.verdict, session.PREFLIGHT_FAIL)

    def test_an_unresolvable_label_is_UNKNOWN_not_a_pass(self):
        cfg = {"architect_id": "x-arch", "machine_map": {"example-runner": "Runner"},
               "residency": {"process": "DevBox"}}
        self.assertEqual(self._verdict(cfg, "Runner").verdict, session.PREFLIGHT_UNKNOWN)

    def test_the_bypass_passes_and_says_so_on_the_record(self):
        """ADR-0096 D7. A bypassed launch must never be mistakable later for a compliant
        one, so the verdict's own text carries the bypass name."""
        r = self._verdict(FED, "Runner", {session.RESIDENCY_BYPASS_ENV: "1"})
        self.assertEqual(r.verdict, session.PREFLIGHT_OK)
        self.assertIn(session.RESIDENCY_BYPASS_ENV, r.detail)


# ReleaseMarkerTest was REMOVED 2026-09-02. It covered ADR-0106 D3's release-marker
# design — `release-cut` writes a record, the promotable marker is the commit that
# introduced it — which is superseded by ADR-0103: the marker is a semver TAG and the
# runner reads it. Its helpers (`_release_records`, `_release_commit`, `_semver_key`)
# went with the withdrawn `cmd_promote`.
#
# One of the four is worth recording rather than just deleting. `test_the_marker_is_the
# _ADDING_commit` asserted only that the string `--diff-filter=A` appears somewhere in
# session.py. After the code it was written for was deleted it kept PASSING, because
# three unrelated call sites use that flag. A test that survives the removal of its own
# subject is not weak coverage, it is a false receipt — it would have reported this
# decision as still enforced forever. Same shape as `scan-bugs-that-fail-toward-
# reassurance`: work out which way a check fails before trusting its green.


if __name__ == "__main__":
    unittest.main()
