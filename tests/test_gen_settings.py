"""Tests for curate/gen_settings.py — the .claude/settings.json generator.

Load-bearing properties (ADR-0034):
  - the shared FLOOR reaches every generated file (git -C allow present,
    destructive git in deny);
  - a system's settings_extras ADD to the floor and never subtract;
  - extras that duplicate a floor entry don't double up;
  - the output is valid JSON carrying the do-not-edit banner.

stdlib unittest: python3 -m unittest discover -s tests
"""

import json
import pathlib
import sys
import contextlib
import io
import shutil
import tempfile
from unittest import mock
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))

import gen_settings  # noqa: E402

FLOOR = json.loads((ROOT / "standard-settings.json").read_text(encoding="utf-8"))


class GenSettingsTest(unittest.TestCase):
    def test_pure_floor_when_no_extras(self):
        out = gen_settings.generate(FLOOR, {})
        allow = out["permissions"]["allow"]
        self.assertIn("Bash(git -C:*)", allow)
        self.assertIn("Bash(git -C *)", allow)
        # no federation-only extras leaked into the floor
        self.assertNotIn("Bash(python3 curate/*)", allow)
        # three shared startup SessionStart entries: session.py start+announce, the
        # ADR-0047 standard_check.py self-check, and the ADR-0039 apply-briefs auto-adopt
        # (floor-promoted session 66 — every member auto-adopts, not just the federation).
        self.assertEqual(len(out["hooks"]["SessionStart"]), 3)
        cmds = json.dumps(out["hooks"]["SessionStart"])
        self.assertIn("apply-briefs", cmds)

    def test_the_grant_read_side_is_in_the_floor_not_a_systems_extras(self):
        """WI-0292. ADR-0112's read side has to be runnable by a DISPATCHED lane.

        A dispatched lane is a detached tmux TUI with the default permission posture and
        nobody attached, so a command matching no allow entry hangs on a prompt instead of
        running. That made `authorize check` unrunnable in exactly the population grants
        exist for (measured 2026-09-05, lane poga-28 and G-47b435).

        The assertion is specifically against `generate(FLOOR, {})` — a member with NO
        settings_extras at all — because that is the claim: the pair is a fleet-floor
        capability, not a federation privilege. Asserting it against the federation's own
        render would pass on the strength of `Bash(python3 session.py:*)`, which is an
        extra, and would certify a guarantee members do not have."""
        allow = gen_settings.generate(FLOOR, {})["permissions"]["allow"]
        # The spelling a lane types at the wrapper — the one that was gated.
        self.assertIn("Bash(poga authorize check:*)", allow)
        self.assertIn("Bash(poga authorize list)", allow)
        # And the harness spelling, because `poga authorize check` execs session.py as a
        # CHILD (never permission-checked) — only what a lane TYPES is matched, so a lane
        # reaching for either spelling must find it admitted.
        self.assertIn("Bash(python3 session.py authorize-check:*)", allow)
        self.assertIn("Bash(python3 session.py authorize-list)", allow)

    def test_the_floor_does_not_admit_the_grant_write_verbs(self):
        """WI-0292. Read-only pair only — minting and revoking are the OPERATOR's acts.

        This is the half that keeps the promotion honest: `authorize check`/`list` are
        safe to run unattended because they cannot write (`resolve_grant` opens the store
        without create=True), while `authorize grant` writes a record that authorizes
        other writes. A lane that could mint its own grant would be self-authorizing,
        which is the exact shape ADR-0112 exists to remove."""
        allow = gen_settings.generate(FLOOR, {})["permissions"]["allow"]
        for forbidden in ("Bash(poga authorize grant:*)", "Bash(poga authorize:*)",
                          "Bash(poga authorize *)", "Bash(poga authorize revoke:*)",
                          "Bash(python3 session.py authorize:*)",
                          "Bash(python3 session.py authorize-revoke:*)"):
            self.assertNotIn(forbidden, allow)

    def test_floor_carries_liveness_hooks(self):
        # ADR-0036: the sidecar hooks ship in the FLOOR, so every generated
        # settings.json gets them — Stop heartbeats, SessionEnd marks clean exit.
        out = gen_settings.generate(FLOOR, {})
        self.assertIn("heartbeat", json.dumps(out["hooks"]["Stop"]))
        self.assertIn("record-end", json.dumps(out["hooks"]["SessionEnd"]))

    def test_destructive_git_stays_denied(self):
        out = gen_settings.generate(FLOOR, {})
        deny = out["permissions"]["deny"]
        self.assertIn("Bash(git push --force:*)", deny)
        self.assertIn("Bash(git reset --hard:*)", deny)

    def test_extras_add_allow_and_session_start_hooks(self):
        cfg = {"settings_extras": {
            "allow": ["Bash(npm test:*)"],
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
        }}
        out = gen_settings.generate(FLOOR, cfg)
        self.assertIn("Bash(npm test:*)", out["permissions"]["allow"])
        # floor allow still present
        self.assertIn("Bash(git -C:*)", out["permissions"]["allow"])
        # three floor SessionStart entries (start+announce, standard_check, apply-briefs)
        # + one extra hook
        self.assertEqual(len(out["hooks"]["SessionStart"]), 4)

    def test_extras_can_add_a_pretooluse_guard(self):
        # the member close_guard case: a system's own PreToolUse(Bash) guard, added
        # on TOP of the floor's two guards without displacing them (ADR-0034).
        cfg = {"settings_extras": {"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 close_guard.py"}]},
        ]}}}
        out = gen_settings.generate(FLOOR, cfg)
        pre = out["hooks"]["PreToolUse"]
        # floor's check-bash + check-question + all-tools heartbeat (ADR-0054) + the local guard
        self.assertEqual(len(pre), 4)
        self.assertIn("python3 close_guard.py", json.dumps(pre))

    def test_extras_do_not_duplicate_a_floor_entry(self):
        cfg = {"settings_extras": {"allow": ["Bash(git -C:*)"]}}
        out = gen_settings.generate(FLOOR, cfg)
        self.assertEqual(out["permissions"]["allow"].count("Bash(git -C:*)"), 1)

    def test_generated_banner_present_and_authoring_comment_dropped(self):
        out = gen_settings.generate(FLOOR, {})
        self.assertIn("//generated", out)
        self.assertNotIn("//", out)  # the floor's plain authoring "//" is dropped

    def test_render_is_valid_json(self):
        text = gen_settings.render({})
        parsed = json.loads(text)  # must not raise
        self.assertIn("permissions", parsed)
        self.assertTrue(text.endswith("\n"))

    def test_federation_config_yields_sixteen_session_start_hooks(self):
        cfg = json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))
        out = gen_settings.generate(FLOOR, cfg)
        # 3 floor startup entries (session.py start+announce; standard_check.py self-check
        # ADR-0047; session.py apply-briefs auto-adopt ADR-0039 Fork A, floor-promoted
        # session 66) + 8 federation extras: gather + reconcile + standard_version FLEET
        # parity + metrics exception-report (ADR-0048) + member registry render
        # + inbox triage-debt guard (ADR-0074) + release/version surface
        # (ADR-0078 D6 — the machine half, or the capability discussion silently lapses)
        # + the OUTBOUND mail audit (ADR-0088, wired session ~128 — deliver.py had shipped
        # advertising this exact status line and was wired nowhere, so the channel was
        # fixable but unwatched, which from outside is indistinguishable from broken)
        # + the inbox-index generator (WI-0088, wired session ~148 — same shape as the
        # line above it, and the reason the count went to 12: gen_indexes.py had shipped
        # 2026-07-14 wired nowhere, its only enforcement a prose line in the session-end
        # sweep, and the index drifted from the directory nine recorded times. It runs at
        # STARTUP rather than session-end deliberately: sessions 145 and 147 both died
        # without `session.py end` running, so the end path is the one this repo has
        # proved unreliable).
        # + the substrate-description check (WI-0339, wired session ~269 — the reason the
        # count went to 13). The three `--check`s in the land `gate` all guard files a
        # GENERATOR writes, and all three held; what drifted was the prose nobody
        # generates. ADR-0118 split session.py into sessionlib/ on 2026-09-06 and five
        # days later no prose surface in the repo contained the string 'sessionlib'.
        # It is in the gate AND here for different reasons: the gate catches prose going
        # stale as it lands, and this catches it at ORIENTATION — which is the one that
        # matters, because the session that read adr/README.md and reported a missing
        # ADR-0121 that has never existed was reasoning from the file hours before any
        # land, and never reached a gate at all.
        #
        # + the two finish-line probes (WI-0285, wired session ~314 — the reason the count
        # went to 15 and 16). Same shape as the four lines above, and the sharpest instance
        # of it: `curate/token_ledger.py` and `curate/finish_line.py` landed on 2026-09-04
        # with `--status` in their own docstrings as "one line, for a SessionStart hook",
        # and were wired nowhere for nine days. The board could answer "is the harness
        # mature yet" the whole time and nothing asked it. Each costs a second or two
        # warm, the second including a re-run of the first. Both fail-open and exit 0 on every branch.
        #
        # This count is a deliberate canary, not incidental: it is what makes ADDING a
        # startup hook a decision someone states rather than a thing that accumulates.
        # When it fails, update the number AND add the corresponding assertIn below —
        # the count alone would let a hook be swapped for another and still pass.
        self.assertEqual(len(out["hooks"]["SessionStart"]), 16)
        cmds = " ".join(hk["command"]
                        for entry in out["hooks"]["SessionStart"] for hk in entry["hooks"])
        self.assertIn("apply-briefs", cmds)                       # ADR-0039 Fork A (lifted)
        self.assertIn("curate/metrics.py", cmds)                  # ADR-0048 metrics probe
        self.assertIn("standard_check.py", cmds)                  # floor self-check
        self.assertIn("curate/standard_version.py", cmds)         # federation fleet view
        self.assertIn("release-list --status", cmds)              # ADR-0078 D6 machine half
        self.assertIn("curate/gen_registry.py", cmds)             # member registry view
        self.assertIn("inbox-check", cmds)                        # ADR-0074 triage debt
        self.assertIn("curate/deliver.py", cmds)                  # ADR-0088 outbound audit
        self.assertIn("curate/gen_indexes.py", cmds)              # WI-0088 index generator
        self.assertIn("curate/check_substrate_docs.py", cmds)     # WI-0339 prose-drift guard
        self.assertIn("curate/check_citations.py", cmds)          # WI-0342 dead-citation guard
        self.assertIn("curate/token_ledger.py", cmds)             # WI-0285 transcript ledger
        self.assertIn("curate/finish_line.py", cmds)              # WI-0285 maturity verdict
        self.assertIn("Bash(python3 curate/*)", out["permissions"]["allow"])


class ExtraTopLevelKeysTest(unittest.TestCase):
    """A member must be able to declare top-level settings the floor doesn't define —
    `model` above all. Until this existed, `settings_extras` silently dropped anything
    that wasn't allow/deny/hooks, so a member could not pin its Architect's model
    in-repo, and the omission was indistinguishable from a setting that had applied.
    Found live: a member's lane came up on the account-default model."""

    def test_model_is_carried_through(self):
        out = gen_settings.generate(FLOOR, {"settings_extras": {"model": "opus"}})
        self.assertEqual(out["model"], "opus")

    def test_extras_still_add_permissions_and_hooks_alongside(self):
        cfg = {"settings_extras": {
            "model": "opus",
            "allow": ["Bash(npm test:*)"],
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
        }}
        out = gen_settings.generate(FLOOR, cfg)
        self.assertEqual(out["model"], "opus")
        self.assertIn("Bash(npm test:*)", out["permissions"]["allow"])
        self.assertIn("Bash(git -C:*)", out["permissions"]["allow"])
        self.assertEqual(len(out["hooks"]["SessionStart"]), 4)

    def test_a_floor_defined_key_is_not_overridable(self):
        """The floor is a floor (ADR-0023 no-subtraction). A member that redefines
        `permissions` wholesale would subtract from it silently."""
        out = gen_settings.generate(FLOOR, {"settings_extras": {"permissions": {"allow": []}}})
        self.assertIn("Bash(git -C:*)", out["permissions"]["allow"])
        self.assertIn("Bash(git push --force:*)", out["permissions"]["deny"])

    def test_hooks_cannot_be_replaced_wholesale(self):
        out = gen_settings.generate(FLOOR, {"settings_extras": {"hooks": {}}})
        self.assertIn("apply-briefs", json.dumps(out["hooks"]["SessionStart"]))

    def test_no_extras_is_unchanged(self):
        self.assertEqual(gen_settings.render({"settings_extras": {}}),
                         gen_settings.render({}))


class DialogSurfacesAreOffTest(unittest.TestCase):
    """WI-0241 — the two blocking surfaces a SETTING can close stay closed.

    A dispatched lane is a detached tmux pane nobody is watching, so any UI that waits on
    a keystroke stalls it silently while every surface still reports it live. operator ruled it
    out: no poga session may block on a modal dialog. These two
    keys are the structural half of that, and they are asserted here rather than trusted
    because the failure is invisible: a lane with the key missing looks exactly like a lane
    with it present, right up until it stops.

    Turned OFF rather than guarded with a PreToolUse deny on purpose — `off` makes the
    runtime drop the tool from the model's schema (`isEnabled()`), so there is nothing to
    call, where a deny only refuses the call after the model has spent a turn on it.

    Not in the floor BY DESIGN (ADR-0101 D6's shape — federation first, floor after one
    cycle): the floor is fleet-wide substrate and promoting it is not a lane's call. The
    floor assertion below is what will fail, deliberately and visibly, on the day someone
    promotes it — at which point this test is the place to record the decision."""

    def _fed(self):
        return json.loads((ROOT / "session.config.json").read_text(encoding="utf-8"))

    def test_the_federation_declares_both_dialog_surfaces_off(self):
        extras = self._fed()["settings_extras"]
        self.assertEqual(extras.get("feedbackDrafts"), "off")
        self.assertEqual(extras.get("modelProposedGoals"), "disabled")

    def test_they_survive_the_render_into_the_generated_file(self):
        out = gen_settings.generate(FLOOR, self._fed())
        self.assertEqual(out["feedbackDrafts"], "off")
        self.assertEqual(out["modelProposedGoals"], "disabled")

    def test_the_floor_still_denies_the_picker_so_all_three_surfaces_are_covered(self):
        """The third surface needs no key: AskUserQuestion is denied fleet-wide by the
        floor's own PreToolUse guard. Pinned together so 'all three are closed' is one
        checkable claim rather than three scattered ones."""
        pre = json.dumps(FLOOR["hooks"]["PreToolUse"])
        self.assertIn("AskUserQuestion", pre)
        self.assertIn("check-question", pre)

    def test_the_keys_are_federation_local_and_not_yet_floor_promoted(self):
        self.assertNotIn("feedbackDrafts", FLOOR)
        self.assertNotIn("modelProposedGoals", FLOOR)


class HarnessDirTest(unittest.TestCase):
    """`harness_dir` — the resident-runtime floor-path adaptation.

    A member whose Architect launches from a pad OUTSIDE the repo (by that member's
    own rule, no CLAUDE.md may sit in the resident runtime's load path) has a
    `$CLAUDE_PROJECT_DIR` that is not the harness dir, so the verbatim floor
    produces dead hooks. That is why such a member hand-maintained its copy and drifted on
    every floor movement. These pin the rewrite AND — the load-bearing half — that
    every other member's render is untouched."""

    def _commands(self, out):
        return [hk["command"]
                for groups in out["hooks"].values()
                for entry in groups
                for hk in entry["hooks"]]

    def test_default_render_is_byte_identical_to_no_declaration(self):
        # The regression that would matter fleet-wide: adding the key must not move
        # a single byte for the members that never declare it.
        base = gen_settings.render({})
        self.assertEqual(gen_settings.render({"harness_dir": "."}), base)
        self.assertEqual(gen_settings.render({"harness_dir": ""}), base)
        self.assertEqual(gen_settings.render({"harness_dir": None}), base)

    def test_floor_commands_are_retargeted(self):
        out = gen_settings.generate(FLOOR, {"harness_dir": "../example-agent"})
        cmds = self._commands(out)
        # every floor hook, not just the SessionStart ones — a half-rewritten file
        # is the drift class this replaces
        self.assertTrue(cmds)
        for cmd in cmds:
            self.assertIn('"$CLAUDE_PROJECT_DIR/../example-agent/', cmd)
            self.assertNotIn('"$CLAUDE_PROJECT_DIR/session.py"', cmd)
        # reproduces the form a hand-edited pad already runs
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/../example-agent/session.py" start', cmds)
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/../example-agent/standard_check.py" --status', cmds)
        # and carries the hook a hand-maintained copy was missing
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/../example-agent/session.py" apply-briefs', cmds)

    def test_trailing_slash_does_not_double_up(self):
        out = gen_settings.generate(FLOOR, {"harness_dir": "../example-agent/"})
        for cmd in self._commands(out):
            self.assertNotIn("//", cmd)
            self.assertIn('"$CLAUDE_PROJECT_DIR/../example-agent/', cmd)

    def test_extras_are_left_verbatim(self):
        # extras are member-authored with full knowledge of the member's own layout,
        # so rewriting them would corrupt a correct path.
        extra = 'python3 "$CLAUDE_PROJECT_DIR/local_probe.py"'
        cfg = {"harness_dir": "../example-agent", "settings_extras": {"hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": extra}]}]}}}
        out = gen_settings.generate(FLOOR, cfg)
        self.assertIn(extra, self._commands(out))

    def test_authoring_notes_and_permissions_untouched(self):
        out = gen_settings.generate(FLOOR, {"harness_dir": "../example-agent"})
        self.assertEqual(out["permissions"], gen_settings.generate(FLOOR, {})["permissions"])
        self.assertEqual(out["//hooks"], FLOOR["//hooks"])

    def test_bad_declaration_fails_loud(self):
        # A silently-broken settings file is exactly the failure this key exists to
        # end, so an unusable declaration must raise rather than ship dead hooks.
        for bad in ("/abs/path", '../or"chestrator'):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    gen_settings.generate(FLOOR, {"harness_dir": bad})


if __name__ == "__main__":
    unittest.main()


class MissingConfigIsRefusedTest(unittest.TestCase):
    """WI-0076 — the one write point that had no guard was the one a human drives.

    A missing `--config` used to become `{}`, which renders the BARE FLOOR — and the bare
    floor is identical for every target, because everything that makes a member's settings
    its own lives in `settings_extras`. That is precisely the recorded signature:
    several members' settings rewritten in the same minute, byte-identical, each
    missing its own enrollment hook, which per-target rendering cannot produce.

    `push-substrate` never had this hole — `settings_target()` returns None for a repo with
    no config, and `_assert_extras_survived` refuses a shrinking render — but both guards
    live there, while this script takes arbitrary `--config`/`--out` and had neither.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_two_targets_rendered_from_a_missing_config_are_identical(self):
        """The property that makes this dangerous, pinned directly: floor-only carries
        nothing that distinguishes one member from another."""
        self.assertEqual(gen_settings.render({}), gen_settings.render({}))

    def test_a_real_config_renders_something_other_than_the_floor(self):
        """The control. If these were equal, the refusal below would be pointless."""
        cfg = {"settings_extras": {"hooks": {"SessionStart": [
            {"matcher": "startup", "hooks": [
                {"type": "command", "command": "memberctl sweep"}]}]}}}
        self.assertNotEqual(gen_settings.render(cfg), gen_settings.render({}))

    def test_a_missing_config_refuses_and_writes_nothing(self):
        out = self.tmp / "settings.json"
        argv = ["gen_settings.py", "--config", str(self.tmp / "nope.json"),
                "--out", str(out)]
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                with self.assertRaises(SystemExit) as e:
                    gen_settings.main()
        self.assertEqual(e.exception.code, 2)
        self.assertFalse(out.exists(), "a refused render must write nothing")
        self.assertIn("REFUSING", err.getvalue())
        self.assertIn("WI-0076", err.getvalue(),
                      "name the defect, so the next reader does not re-derive it")

    def test_a_present_config_still_writes(self):
        """The guard must not become a refusal to generate at all."""
        cfgp = self.tmp / "session.config.json"
        cfgp.write_text(json.dumps({"architect_id": "t-arch"}), encoding="utf-8")
        out = self.tmp / "settings.json"
        argv = ["gen_settings.py", "--config", str(cfgp), "--out", str(out)]
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(io.StringIO()):
                gen_settings.main()
        self.assertTrue(out.is_file())


class KitTemplateIsTheFloorTest(unittest.TestCase):
    """WI-0465. `bootstrap-kit/claude-settings-template.json` is the fallback
    `bootstrap.install_settings` copies when it cannot render the floor. It went stale
    once (no WorktreeRemove hook) because nothing checked it. These keep it equal to the
    render a fresh project gets, so a fallback install lands on the same floor."""

    def test_kit_template_equals_the_bare_floor_render(self):
        current = gen_settings.KIT_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            current, gen_settings.kit_template_text(),
            "kit settings template is stale: run `python3 curate/gen_settings.py --kit`")

    def test_the_kit_config_renders_the_same_as_no_config(self):
        # install_settings renders with the NEW project's session.config.json, which
        # starts as the kit's. If the kit config ever gains settings_extras or a
        # harness_dir, the bare-floor fallback would no longer match that render.
        import re
        text = (ROOT / "bootstrap-kit" / "session.config.json").read_text(encoding="utf-8")
        text = text.replace("<<MACHINE_MAP>>", "{}")
        cfg = json.loads(re.sub(r"<<[A-Z_]+>>", "x", text))
        self.assertEqual(gen_settings.render(cfg), gen_settings.kit_template_text())

    def test_kit_check_flags_a_stale_copy(self):
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        stale = tmp / "claude-settings-template.json"
        stale.write_text("{}\n", encoding="utf-8")
        with mock.patch.object(gen_settings, "KIT_TEMPLATE_PATH", stale), \
                mock.patch.object(sys, "argv", ["gen_settings.py", "--kit", "--check"]), \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                gen_settings.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertEqual(stale.read_text(encoding="utf-8"), "{}\n", "--check never writes")
