"""The substrate's own voice: every command it emits must be runnable by its reader.

WI-0158 / ADR-0099 D4. This file replaces tests/test_substrate_advice.py, whose WI-0099
regression pins are preserved below — it covered one function, one sink (`print` with a
literal) and one rule, and it had a demonstrable blind spot: `inspect.getsource` plus a
regex loses the continuation line of an implicitly-concatenated print, so a denied command
sitting on a continuation line passed it. The AST extractor in tests/substrate_voice.py
folds those literals before matching.

THE TRAP THIS FILE IS BUILT AROUND. WI-0158 says it outright: *a lint that ships green has
proven nothing*, and per `ship-the-detector-with-the-capability` read the right way round,
a detector that misses the known specimen does not merely fail to detect — it certifies
the problem as solved. So the real-code sweep (`test_the_substrate_emits_no_unrunnable_command`)
is only HALF the file, and it is the half that proves the CODE is clean. The other half —
`DetectorFiresTest` — proves the DETECTOR is awake, by feeding it every known-bad string
from the evidence record and failing if any of them passes. Green here means both: the
substrate is clean AND the thing that judged it can still tell.

The false-negative pins matter as much as the positive ones. `MentionIsNotUseTest` exists
because ADR-0099 D3 requires every guard denial to NAME the form it refuses beside the one
it allows — so the substrate quotes `git branch -D` and `git -C <lane>` on purpose, and a
lint that cannot tell naming from advising would report the D3 message as the D4 defect.

stdlib unittest: python3 -m unittest discover -s tests
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import session  # noqa: E402
import substrate_voice as sv  # noqa: E402
import harness_fixture  # noqa: E402


def _judge(text, subject="session.py", qualname="fake", audience=sv.LANE):
    return sv.judge(sv.Found(subject, 1, qualname, text, text), audience=audience)


def _rules(text, **kw):
    return {v.rule for v in _judge(text, **kw)}


# ── The detector must FIRE ──────────────────────────────────────────────────────

class DetectorFiresTest(unittest.TestCase):
    """Every known-bad string from the evidence record, pinned as a fixture.

    These are not hypotheticals. Each one was emitted by this substrate, and most of them
    were carried out by operator personally — which is the cost this lint exists to stop
    paying (`never-route-your-own-work-through-the-user`).
    """

    def test_the_original_specimen_force_branch_delete(self):
        """2026-08-07, journal 50b1: `reap-lanes` printed this as its advice, check-bash
        denied it, and the operator ran the one line by hand. WI-0099, the founding case."""
        self.assertIn("destructive-git",
                      _rules("To discard the lane: `git branch -D worktree-poga-1`"))

    def test_the_push_substrate_remedy(self):
        """WI-0085: the suggested remedy for a stale main checkout, denied twice over —
        `reset --hard` by check-bash and `git -C` by the isolation guard."""
        rules = _rules("If this checkout is stale after a lane land, sync it: "
                       "`git -C /Users/x/repo reset --hard main` (verify no local edits).")
        self.assertIn("destructive-git", rules)
        self.assertIn("lane-isolation", rules)

    def test_the_bare_deictic_there(self):
        """views-refresh: the command is fine, the LOCATION is unnamed — and by ADR-0098
        the session it addresses never exists."""
        self.assertIn("deixis",
                      _rules("views: NOT refreshed — main checkout is on 'x', not main. "
                             "Run `python3 session.py compile` there."))

    def test_a_verb_that_does_not_exist_on_the_named_cli(self):
        """2026-09-03, found live: a stalled dispatch printed `poga dispatch-retry D-xxxx`.
        That is not a poga verb — the verb is real but it lives on session.py — and poga's
        unknown-verb branch prints usage. The reader follows the remedy and nothing
        happens."""
        vs = _judge("retry: `poga dispatch-retry D-4d41da`")
        self.assertEqual({"verb-exists"}, {v.rule for v in vs})
        self.assertIn("session.py dispatch-retry", vs[0].detail,
                      "naming the defect is half the job; the fix has to be named too")

    def test_a_verb_that_exists_on_neither(self):
        self.assertIn("verb-exists", _rules("run `poga frobnicate`"))
        self.assertIn("verb-exists", _rules("run `python3 session.py frobnicate`"))

    def test_a_cross_tree_git_read_is_still_a_cross_tree_git_call(self):
        """session.py:12812's class. `git -C <lane> status` is AUTO-ALLOWED by check-bash
        (it is read-only), so the destructive-git oracle says nothing — and the isolation
        guard refuses it anyway. Modelling only one guard loses this whole class."""
        self.assertIsNone(session.destructive_git_violation("git -C /some/lane status"),
                          "premise check: check-bash does not deny this")
        self.assertIn("lane-isolation",
                      _rules("stranded: poga-3 — UNCOMMITTED files. "
                             "Inspect: git -C /some/lane status"))

    def test_cd_then_bare_git_is_the_same_redirect(self):
        """WI-0122: the guard objects to the redirect, not to the syntax that reaches it."""
        self.assertIn("lane-isolation", _rules("Try `cd /other/lane && git status`"))

    def test_the_second_refusal_surface_is_consulted(self):
        """`gh repo delete` is invisible to check-bash and denied by permissions.deny.
        Consulting one surface and calling it the guard stack would miss it."""
        self.assertIsNone(session.destructive_git_violation("gh repo delete foo"),
                          "premise check: this is not a destructive-GIT hit")
        self.assertIn("settings-deny", _rules("clean up with `gh repo delete foo`"))

    def test_asking_for_a_session_of_a_particular_shape(self):
        """the operator's ruling, 2026-08-19: a session asking the operator to run a session 'not
        in a lane' is one of the three asks that must become impossible."""
        self.assertIn("deixis",
                      _rules("Open a main-checkout session and run `python3 "
                             "curate/push-substrate.py`."))

    def test_asking_for_a_terminal(self):
        self.assertIn("deixis",
                      _rules("In a terminal, run `python3 session.py compile`."))

    def test_a_remedy_bound_to_another_machine(self):
        """The 2026-09-03 consultant brief's hardware clause. ADR-0103 D1: neither machine
        holds credentials for the other, so a cross-host remedy is unfollowable in exactly
        the way "there" is."""
        self.assertIn("host-bound",
                      _rules("Fix it on the Runner with `python3 session.py compile`."))

    def test_an_unbackticked_command_is_still_a_command(self):
        """Backticks are the substrate's convention, not its guarantee — the live
        session.py:12812 defect carries none. Cue-anchored extraction is what catches it."""
        self.assertIn("lane-isolation",
                      _rules("stranded: poga-3 — Inspect: git -C /other/lane status"))


# ── The detector must NOT fire ──────────────────────────────────────────────────

class AudienceModelTest(unittest.TestCase):
    """The crux of WI-0158, exercised rather than assumed.

    `MAIN_AUDIENCE` is empty today, so `audience_for` returns LANE for every real site and
    the MAIN branch would never run — a capability with no detector, which is the trap
    `ship-the-detector-with-the-capability` names. These tests drive both answers directly,
    so the model is live code with a test that can fail, not a design written in a
    docstring.
    """

    SPECIMEN = "If the checkout is stale, sync it: `git -C /Users/x/repo status`"

    def test_the_same_string_is_judged_differently_by_audience(self):
        """The whole argument for an audience model in one assertion. `git -C <tree>
        status` is ordinary advice to a session standing in the main checkout and an
        unfollowable escalation to a lane — same bytes, opposite verdict. If this ever
        stops discriminating, the model has collapsed into a blocklist."""
        self.assertIn("lane-isolation", _rules(self.SPECIMEN, audience=sv.LANE))
        self.assertNotIn("lane-isolation", _rules(self.SPECIMEN, audience=sv.MAIN))

    def test_a_context_free_denial_binds_every_audience(self):
        """The limit of the model, and it matters more than the discrimination above.
        check-bash reads its stdin and nothing else, so what it denies it denies to
        everybody. An audience must never be usable as an exemption from it — that would
        turn the model into the guard-weakening ADR-0099 explicitly refuses."""
        destructive = "sync it: `git -C /Users/x/repo reset --hard main`"
        for audience in (sv.LANE, sv.MAIN):
            with self.subTest(audience=audience):
                self.assertIn("destructive-git", _rules(destructive, audience=audience))

    def test_audience_defaults_to_lane_and_honours_a_registered_exemption(self):
        key = "curate/push-substrate.py::_only_runs_in_main"
        self.assertEqual(sv.LANE, sv.audience_for("session.py", "anything"))
        self.assertEqual(sv.LANE, sv.audience_for(*key.split("::")))
        sv.MAIN_AUDIENCE[key] = ("fixture: a site whose reader is provably standing in "
                                 "the main checkout")
        try:
            self.assertEqual(sv.MAIN, sv.audience_for(*key.split("::")))
        finally:
            del sv.MAIN_AUDIENCE[key]
        self.assertEqual(sv.LANE, sv.audience_for(*key.split("::")),
                         "the exemption leaked past the test that registered it")


class MentionIsNotUseTest(unittest.TestCase):
    """ADR-0099 D3 requires a denial to name the refused form beside the allowed one. A
    lint that reads naming as advising would convict the message that exists to prevent
    this defect — and, worse, would pressure the substrate into dropping the D3 half."""

    def test_a_refused_form_named_as_refused_passes(self):
        self.assertEqual(set(), _rules(
            "cd <lane> && python3 session.py merge (the sanctioned cross-lane form — "
            "the isolation guard refuses `git -C <lane>`, NOT this)"))

    def test_the_historical_note_passes(self):
        self.assertEqual(set(), _rules(
            "This line used to print `git branch -D <branch>` — which check-bash DENIES."))

    def test_a_form_described_rather_than_offered_passes(self):
        self.assertEqual(set(), _rules(
            "The `git -C <path> …` form can't be matched by a settings allow-string, so "
            "the safe git surface is auto-approved here instead."))


class SanctionedAdvicePassesTest(unittest.TestCase):
    """If the replacement advice were itself flagged, this would be the same bug with a
    new command in it — the trap the original fell into."""

    def test_the_sanctioned_cross_lane_form(self):
        self.assertEqual(set(), _rules("Land it: `cd /some/lane && python3 session.py merge`"))

    def test_the_lane_last_mile_verbs(self):
        for cmd in ("python3 session.py main-restore", "python3 session.py integrate",
                    "python3 session.py recover-lanes --lane poga-3",
                    "python3 session.py discard-phantom-lanes",
                    "python3 session.py merge", "poga resume poga-3", "poga deploy example-app"):
            with self.subTest(cmd=cmd):
                self.assertEqual(set(), _rules(f"Try `{cmd}`."), cmd)

    def test_existential_there_is_not_a_location(self):
        """"there is no verb yet" is the sentence ADR-0099 D4 tells a blocked tool to
        print. Convicting it would have the lint condemning its own sanctioned fallback."""
        self.assertEqual(set(), _rules(
            "If none of those fit, there is no verb yet: park the work additively and "
            "file the item. `python3 session.py merge` still lands what you have."))

    def test_a_statement_of_fact_about_main_is_not_a_deixis(self):
        self.assertEqual(set(), _rules(
            "main:    no live session there — `python3 session.py main-restore --dry-run` "
            "says which of these the trunk has already superseded."))

    def test_a_host_clause_attached_to_a_different_clause_passes(self):
        """"Develop on devbox; ship here with X" — the host phrase governs the clause
        before the semicolon; the command runs right here."""
        self.assertEqual(set(), _rules(
            "Develop on devbox; ship here with `poga deploy example-app` (ADR-0103)."))

    def test_prose_in_backticks_is_not_a_command(self):
        for prose in ("poga lane · worktree-poga-3 (isolated worktree; lands by CAS)",
                      "poga lane launcher + WorktreeRemove teardown hook (ADR-0060)"):
            with self.subTest(prose=prose):
                self.assertEqual(set(), _rules(f"surface: `{prose}`"))

    def test_a_placeholder_is_not_a_verb(self):
        """Usage lines parameterise the verb. Judging the slot as a name would report every
        correct usage string as a defect, which is how a lint teaches people to ignore it."""
        for cmd in ("poga <verb>", "python3 session.py <verb>", "poga $1",
                    "python3 session.py $verb"):
            with self.subTest(cmd=cmd):
                self.assertEqual(set(), _rules(f"usage: `{cmd}`"))


# ── The extractor itself ────────────────────────────────────────────────────────

class ExtractorTest(unittest.TestCase):
    """Extraction is the half that fails silently, so it is pinned directly rather than
    inferred from the sweep being green."""

    def test_docstrings_are_not_advice(self):
        """The highest-value false positive in the repo: `cmd_reap_lanes`'s own docstring
        says it *used to* name `git branch -D`. A grep-based lint reports the sentence
        that records the fix as the defect."""
        found = sv.strings_in_python(harness_fixture.harness_subject(
            session.cmd_reap_lanes.__code__.co_filename))
        doc = session.cmd_reap_lanes.__doc__ or ""
        self.assertIn("git branch -D", doc,
                      "premise check: the docstring still carries the example")
        self.assertFalse([f for f in found if f.text == doc],
                         "a docstring was extracted as an emitted string")

    def test_fstrings_render_with_a_placeholder(self):
        texts = [t for s in harness_fixture.harness_files()
                 for t in (f.text for f in sv.strings_in_python(s))]
        self.assertTrue(any(sv._PLACEHOLDER in t for t in texts),
                        "no f-string was rendered — the JoinedStr path is dead")

    def test_the_indirect_sinks_are_reached(self):
        """Of WI-0158's three named specimens only ONE is a print() literal — the others
        are a RETURNED f-string and an `atomic_write` argument. A print-only extractor,
        which is what the precursor was, finds one defect in three.

        Pinned against a fixture rather than against production source on purpose: the
        first version of this test asserted that push-substrate still contained
        `reset --hard`, and went red the moment that defect was fixed. A test whose premise
        its own subject is supposed to delete measures the wrong thing.
        """
        import tempfile
        fixture = (
            'def f():\n'
            '    """A docstring naming `git branch -D` that must not be extracted."""\n'
            '    if x:\n'
            '        return (False, f"sync it: `git -C {p} reset --hard {t}`")\n'
            '    atomic_write(path, f"fix: `git -C {m} reset --hard {t}`\\n")\n'
            '    print("plain: `python3 session.py merge`")\n'
        )
        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d) / "fixture.py"
            tmp.write_text(fixture, encoding="utf-8")
            old, sv.ROOT = sv.ROOT, pathlib.Path(d)
            try:
                texts = [f.text for f in sv.strings_in_python("fixture.py")]
            finally:
                sv.ROOT = old
        self.assertTrue(any(t.startswith("sync it:") for t in texts),
                        "a RETURNED remedy was not extracted")
        self.assertTrue(any(t.startswith("fix:") for t in texts),
                        "a remedy written to a FILE was not extracted")
        self.assertTrue(any(t.startswith("plain:") for t in texts),
                        "a printed remedy was not extracted")
        self.assertFalse(any("must not be extracted" in t for t in texts),
                         "a docstring was extracted as an emitted string")

    def test_poga_verbs_are_read_from_the_dispatch_table(self):
        """Pins the extractor bug that would have made this lint lie. Requiring trailing
        whitespace after `case`'s `)` silently dropped seven live verbs, and a
        verb-existence check that under-reads the verb table manufactures the exact defect
        it hunts."""
        verbs = sv.poga_verbs()
        for v in ("session", "new", "lanes", "resume", "recover", "work", "ops", "test",
                  "preflight", "promote", "main-sync", "main-restore", "integrate",
                  "deploy", "diagnose", "dispatch", "dispatched", "attach", "drill",
                  "install",
                  "bootstrap", "restore"):
            with self.subTest(verb=v):
                self.assertIn(v, verbs)
        self.assertNotIn("dispatch-retry", verbs)
        self.assertNotIn("dispatch-cancel", verbs)
        self.assertNotIn("intake", verbs)

    def test_session_verbs_are_read_from_argparse(self):
        verbs = sv.session_verbs()
        for v in ("merge", "start", "end", "compile", "dispatch-retry", "dispatch-cancel",
                  "main-restore", "integrate", "recover-lanes", "discard-phantom-lanes"):
            with self.subTest(verb=v):
                self.assertIn(v, verbs)

    def test_pogas_own_verb_list_matches_its_dispatch_table(self):
        """A tool whose error message misstates its own verb set is this defect one level
        up: the reader is told the verb does not exist AND handed a wrong inventory. The
        hand-transcribed list this branch used to print had gone stale by EIGHT live verbs
        — resume, recover, ops, promote, main-sync, main-restore, integrate, deploy — so
        poga now DERIVES the list from its own case table (WI-0158).

        Which is why this RUNS the tool rather than reading its source. A derived list can
        only be checked by making it derive: reading the branch's text would now find a
        function call and prove nothing about what a reader is actually shown."""
        import re
        import subprocess
        import tempfile

        # OUTSIDE ANY REPO, deliberately (WI-0151). The refusal now consults origin before
        # firing, and from `ROOT` that consult would fetch the live federation's real
        # remote from inside the suite. The roster is derived from the script's own file
        # and owes nothing to a cwd, so running it nowhere tests the same thing and can
        # reach nothing ([`evidence-is-separated-from-state-by-construction`]).
        with tempfile.TemporaryDirectory() as nowhere:
            proc = subprocess.run([str(ROOT / "poga"), "definitely-not-a-verb"],
                                  cwd=nowhere, capture_output=True, text=True)
        self.assertEqual(2, proc.returncode,
                         "a verb-shaped token that is not a verb must exit non-zero — a "
                         "usage message under a success code is this defect in its purest "
                         f"form. stderr:\n{proc.stderr}")
        m = re.search(r"verbs:(.*?)\(`poga --help`", proc.stderr, re.S)
        self.assertIsNotNone(m, f"no verb listing in the refusal:\n{proc.stderr}")
        printed = {v for v in re.split(r"[,\s]+", m.group(1)) if v}

        # WITHHELD BY ARGUMENT, NOT BY OMISSION — which is the whole point of deriving the
        # rest. `run` is reserved (ADR-0061 D1) and answers with its own message, and
        # `promote` is withdrawn (ADR-0103), kept only as a route for stale muscle memory
        # and absent from `--help` on the same grounds. Pinned here so a later reader can
        # tell a decision from a lapse.
        self.assertEqual(sv.poga_verbs() - {"run", "promote"}, printed)

    def test_every_subject_parses(self):
        for s in sv.PY_SUBJECTS:
            with self.subTest(subject=s):
                self.assertTrue(sv.strings_in_python(s), f"{s} yielded nothing")
        for s in sv.SH_SUBJECTS:
            with self.subTest(subject=s):
                if not sv.shipped(s):
                    self.skipTest(f"{s} is internal-only (withheld from this tree)")
                self.assertTrue(sv.strings_in_shell(s), f"{s} yielded nothing")

    def test_every_internal_only_subject_is_a_real_subject(self):
        """An INTERNAL_ONLY name that is not a subject can never be skipped, so it would
        read as a working exemption while exempting nothing."""
        subjects = set(sv.PY_SUBJECTS) | set(sv.SH_SUBJECTS) | set(sv.JSON_SUBJECTS)
        self.assertLessEqual(sv.INTERNAL_ONLY, subjects)

    def test_the_fleet_shipped_files_are_covered(self):
        """session.py and poga are BYTE_IDENTICAL fleet substrate, so a bad advice string
        in either is a bad advice string on every converged member. This test is not
        shipped; its leverage is entirely in covering the files that are."""
        import re
        ps = (ROOT / "curate" / "push-substrate.py").read_text(encoding="utf-8")
        m = re.search(r"^BYTE_IDENTICAL = \[(.*?)\]", ps, re.S | re.M)
        self.assertIsNotNone(m, "BYTE_IDENTICAL moved — re-derive the coverage claim")
        shipped = set(re.findall(r'"([^"]+)"', m.group(1)))
        covered = set(sv.PY_SUBJECTS) | set(sv.SH_SUBJECTS)
        for f in shipped:
            if f.endswith(".py") or f == "poga":
                with self.subTest(file=f):
                    self.assertIn(f, covered, f"{f} ships fleet-wide and is not linted")

    def test_the_audience_exemption_list_stays_small(self):
        """The rule is not what a lint dies of; the exemption list is. Default is LANE
        (ADR-0059 steers every session into one) and a MAIN entry is an argued exception,
        so a growing list means the default is wrong, not that the list needs extending."""
        self.assertLessEqual(len(sv.MAIN_AUDIENCE), 5,
                             "if this many sites address a main reader, revisit the "
                             "default rather than extending the exemptions")
        for key, reason in sv.MAIN_AUDIENCE.items():
            with self.subTest(key=key):
                self.assertGreater(len(reason), 30,
                                   "an exemption whose argument is not written down is "
                                   "indistinguishable from an oversight")


# ── The oracles the rules delegate to ───────────────────────────────────────────

class GuardAgreesTest(unittest.TestCase):
    """Preserved from tests/test_substrate_advice.py. Without this, the rules above pin a
    claim about check-bash that could silently stop being true, and would then be
    enforcing a rule that guards nothing."""

    def test_force_branch_delete_is_actually_denied(self):
        self.assertIsNotNone(
            session.destructive_git_violation("git branch -D worktree-poga-1"),
            "the guard no longer denies this, so the advice rule above guards nothing")

    def test_the_lowercase_force_delete_is_denied_too(self):
        self.assertIsNotNone(
            session.destructive_git_violation("git branch -d worktree-poga-1 --force"))

    def test_reset_hard_is_denied_through_a_dash_c_redirect(self):
        self.assertIsNotNone(
            session.destructive_git_violation("git -C /some/repo reset --hard main"))

    def test_the_sanctioned_discard_verb_is_not_denied(self):
        self.assertIsNone(
            session.destructive_git_violation("python3 session.py discard-phantom-lanes"))

    def test_the_sanctioned_landing_route_is_not_denied(self):
        self.assertIsNone(session.destructive_git_violation("python3 session.py merge"))
        self.assertIsNone(
            session.destructive_git_violation("python3 session.py recover-lanes"))

    def test_the_settings_deny_list_is_readable_and_populated(self):
        pats = sv.settings_denials()
        self.assertTrue(pats, "permissions.deny is empty or unparseable")
        self.assertTrue(any(p.startswith("gh repo delete") for p in pats),
                        "the non-git half of the deny list is gone")


# ── WI-0099 regression pins, carried over ───────────────────────────────────────

class ReaperAdviceTest(unittest.TestCase):
    """From tests/test_substrate_advice.py. The sweep would catch a re-introduced
    `git branch -D` anywhere; these keep the POSITIVE half — that reap-lanes still names
    the route — which no general rule can express."""

    def _advice(self):
        src = session.cmd_reap_lanes.__code__
        # The reaper's advice is the strings NEXT TO ITS DEFINITION, so the window is
        # line numbers inside whichever harness file actually defines it. ADR-0118 moved
        # that file; `co_filename` is the only honest answer to which one, and asking the
        # function itself is what keeps the window from sliding to a different file's
        # lines the next time the split changes.
        subject = harness_fixture.harness_subject(src.co_filename)
        found = [f for f in sv.strings_in_python(subject)
                 if src.co_firstlineno <= f.line <= src.co_firstlineno + 200]
        return " ".join(f.text for f in found)

    def test_the_reaper_points_at_the_discard_verb(self):
        """Refusing to print the denied command is only half the fix — a surface that
        names a problem and offers no route is the same escalation with extra steps."""
        self.assertIn("discard-phantom-lanes", self._advice())

    def test_the_reaper_still_points_at_the_landing_route(self):
        """Discarding must never read as the primary answer. A lane holding real work is
        meant to be LANDED; the discard verb exists for lanes that hold nothing."""
        advice = self._advice()
        self.assertIn("recover-lanes", advice)
        self.assertIn("session.py merge", advice)

    def test_no_stale_no_verb_yet_claim_survives(self):
        """The advice block said "no verb yet — WI-0099" for long enough that the verb
        landed underneath it. A tool asserting its own capability gap is a fact that rots
        (`probe-live-state-before-acting-on-a-report`)."""
        self.assertNotIn("no verb yet — WI-0099", self._advice())


# ── The sweep ───────────────────────────────────────────────────────────────────

class SubstrateVoiceTest(unittest.TestCase):

    def test_the_substrate_emits_no_unrunnable_command(self):
        """The product. Every command-shaped string the substrate emits, judged against
        both refusal surfaces for the audience that will read it.

        This is only meaningful because DetectorFiresTest proves the judge is awake — a
        green sweep from a detector that had stopped looking is the failure mode WI-0158
        names outright."""
        violations = sv.sweep()
        if violations:
            report = "\n\n".join(str(v) for v in violations)
            self.fail(f"{len(violations)} unrunnable command(s) emitted by the "
                      f"substrate:\n\n{report}")


if __name__ == "__main__":
    unittest.main()
