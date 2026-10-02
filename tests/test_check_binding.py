"""Tests for curate/check-binding.py — the ADR-0041 binding-manifest validator.

The load-bearing property: "no-subtraction" (ADR-0041 §3) survives only if every
obligation carries a real disposition and every waiver names a compensating
control. These tests pin that — a placeholder disposition, an unknown disposition,
a missing obligation, or a bare waiver must all FAIL; a fully-specified manifest
(including a properly-compensated waiver) must PASS.

stdlib unittest: python3 -m unittest discover -s tests
"""

import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "check_binding", ROOT / "curate" / "check-binding.py"
)
check_binding = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_binding)


# Default: a fully-conformant manifest. Each obligation row is overridable so a
# test can corrupt exactly one thing and assert the single resulting problem.
DEFAULT_ROWS = {
    "canon-delivery": ("satisfied-differently", "canon embedded in architect_prompt.md, refreshed on push"),
    "state-continuity": ("satisfied-differently", "env_check.py ritual + governance-session handoff"),
    "operational-guards": ("satisfied-differently", "retrofit-installed pre-push git hook (agent-agnostic)"),
    "auditable-evolution": ("satisfied", "semver + Keep-a-Changelog + ADRs + pre-commit bump hook"),
    "identity-provenance": ("satisfied", "4-slot identity, portfolio registration"),
}


def _manifest(runtime="gemini-antigravity", rows=None, drop=None, dup=None):
    rows = dict(DEFAULT_ROWS if rows is None else rows)
    if drop:
        rows.pop(drop, None)
    lines = [
        "# Binding manifest — Example App (`example-app`)",
        "",
        f"**Runtime:** {runtime}",
        "**Contract:** ADR-0041",
        "",
        "| Obligation | Disposition | Mechanism / Compensation |",
        "|---|---|---|",
    ]
    for key, (disp, comp) in rows.items():
        lines.append(f"| C? {key} | {disp} | {comp} |")
    if dup:
        disp, comp = rows.get(dup, DEFAULT_ROWS[dup])
        lines.append(f"| C? {dup} | {disp} | {comp} |")
    return "\n".join(lines) + "\n"


class CheckBindingTest(unittest.TestCase):
    def _check(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            path = fh.name
        return check_binding.check_file(path)

    def test_conformant_manifest_passes(self):
        self.assertEqual(self._check(_manifest()), [])

    def test_missing_runtime_fails(self):
        text = _manifest().replace("**Runtime:** gemini-antigravity\n", "")
        problems = self._check(text)
        self.assertTrue(any("Runtime" in p for p in problems), problems)

    def test_placeholder_runtime_fails(self):
        problems = self._check(_manifest(runtime="<runtime-id>"))
        self.assertTrue(any("Runtime" in p for p in problems), problems)

    def test_missing_obligation_fails(self):
        problems = self._check(_manifest(drop="operational-guards"))
        self.assertTrue(any("operational-guards" in p and "missing" in p for p in problems), problems)

    def test_invalid_disposition_fails(self):
        rows = dict(DEFAULT_ROWS)
        rows["canon-delivery"] = ("mostly-fine", "whatever")
        problems = self._check(_manifest(rows=rows))
        self.assertTrue(any("canon-delivery" in p and "invalid disposition" in p for p in problems), problems)

    def test_unfilled_disposition_placeholder_fails(self):
        rows = dict(DEFAULT_ROWS)
        rows["canon-delivery"] = ("<disposition>", "text")
        problems = self._check(_manifest(rows=rows))
        self.assertTrue(any("canon-delivery" in p and "not filled" in p for p in problems), problems)

    def test_waiver_without_compensation_fails(self):
        rows = dict(DEFAULT_ROWS)
        rows["operational-guards"] = ("waived-with-compensation", "—")
        problems = self._check(_manifest(rows=rows))
        self.assertTrue(any("operational-guards" in p and "no compensating" in p for p in problems), problems)

    def test_waiver_with_compensation_passes(self):
        rows = dict(DEFAULT_ROWS)
        rows["operational-guards"] = (
            "waived-with-compensation",
            "no PreToolUse analog on Antigravity; governance sessions run in Claude Code (ADR-0041 §6)",
        )
        self.assertEqual(self._check(_manifest(rows=rows)), [])

    def test_duplicate_obligation_fails(self):
        problems = self._check(_manifest(dup="canon-delivery"))
        self.assertTrue(any("canon-delivery" in p and "times" in p for p in problems), problems)

    def test_unreadable_file(self):
        problems = check_binding.check_file(str(ROOT / "does-not-exist-xyz.md"))
        self.assertTrue(any("could not read" in p for p in problems), problems)


# --- symmetric per-runtime matrix form ------------------------------------------

MATRIX_ROWS = {
    "canon-delivery": {
        "claude-code": ("satisfied", "session.py start injects CANON.md"),
        "gemini-antigravity": ("satisfied-differently", "env_check -> session.py stamp surfaces CANON.md"),
    },
    "state-continuity": {
        "claude-code": ("satisfied", "session.py start handoff + stamp"),
        "gemini-antigravity": ("satisfied-differently", "env_check -> session.py stamp"),
    },
    "operational-guards": {
        "claude-code": ("satisfied", "check-bash / check-question + destructive-git guard"),
        "gemini-antigravity": ("waived-with-compensation", "agent-agnostic pre-push / pre-commit git hooks"),
    },
    "auditable-evolution": {
        "claude-code": ("satisfied", "semver + Keep-a-Changelog + ADRs"),
        "gemini-antigravity": ("satisfied", "semver + Keep-a-Changelog + ADRs + pre-commit bump hook"),
    },
    "identity-provenance": {
        "claude-code": ("satisfied", "4-slot identity, portfolio registration"),
        "gemini-antigravity": ("satisfied-differently", "per-turn version banners + ADR-0006 name"),
    },
}


def _matrix(runtimes=("claude-code", "gemini-antigravity"), rows=None, drop=None, dup=None):
    rows = {k: dict(v) for k, v in (MATRIX_ROWS if rows is None else rows).items()}
    lines = [
        "# Binding manifest — Example App (`example-app`)",
        "",
        f"**Runtimes:** {', '.join(runtimes)}",
        "**Contract:** ADR-0041",
        "",
        "| Obligation | Runtime | Disposition | Mechanism / Compensation |",
        "|---|---|---|---|",
    ]
    for key, per_rt in rows.items():
        for rt, (disp, comp) in per_rt.items():
            if drop == (key, rt):
                continue
            lines.append(f"| C? {key} | {rt} | {disp} | {comp} |")
    if dup:
        key, rt = dup
        disp, comp = rows[key][rt]
        lines.append(f"| C? {key} | {rt} | {disp} | {comp} |")
    return "\n".join(lines) + "\n"


class CheckBindingMatrixTest(unittest.TestCase):
    def _check(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write(text)
            path = fh.name
        return check_binding.check_file(path)

    def test_conformant_matrix_passes(self):
        self.assertEqual(self._check(_matrix()), [])

    def test_matrix_missing_runtime_row_fails(self):
        problems = self._check(_matrix(drop=("state-continuity", "gemini-antigravity")))
        self.assertTrue(any("state-continuity" in p and "gemini-antigravity" in p and "missing" in p
                            for p in problems), problems)

    def test_undeclared_runtime_in_row_fails(self):
        rows = {k: dict(v) for k, v in MATRIX_ROWS.items()}
        rows["canon-delivery"]["openai-codex"] = ("satisfied", "some mechanism")
        problems = self._check(_matrix(rows=rows))
        self.assertTrue(any("not a declared runtime" in p for p in problems), problems)

    def test_matrix_waiver_without_compensation_fails(self):
        rows = {k: dict(v) for k, v in MATRIX_ROWS.items()}
        rows["operational-guards"]["gemini-antigravity"] = ("waived-with-compensation", "—")
        problems = self._check(_matrix(rows=rows))
        self.assertTrue(any("operational-guards" in p and "gemini-antigravity" in p and "no compensating" in p
                            for p in problems), problems)

    def test_matrix_duplicate_pair_fails(self):
        problems = self._check(_matrix(dup=("canon-delivery", "claude-code")))
        self.assertTrue(any("canon-delivery" in p and "claude-code" in p and "times" in p
                            for p in problems), problems)

    def test_matrix_invalid_disposition_fails(self):
        rows = {k: dict(v) for k, v in MATRIX_ROWS.items()}
        rows["canon-delivery"]["claude-code"] = ("mostly-fine", "whatever")
        problems = self._check(_matrix(rows=rows))
        self.assertTrue(any("canon-delivery" in p and "invalid disposition" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
