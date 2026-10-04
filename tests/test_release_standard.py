"""WI-0226: reject obsolete templates even when generated copies match."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from curate import standardize
import session


class ReleaseStandardTest(unittest.TestCase):
    def test_template_and_start_stub_use_close_sections(self):
        body = standardize.SRC.read_text()
        template = body.split("## Session journal format", 1)[1].split("```markdown", 1)[1].split("```", 1)[0]
        for text in (template, session._STUB_BODY):
            for heading in ("### State at close", "### Parked question", "### Notes filed"):
                self.assertIn(heading, text)
            self.assertNotIn("What's next", text)

    def test_check_rejects_obsolete_template_even_when_outputs_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text("## Session journal format\n```markdown\n### What's next\n```\n")
            target = root / "STANDARD.md"
            target.write_text(standardize.render(source.read_text()))
            with mock.patch.object(standardize, "ROOT", root), mock.patch.object(standardize, "SRC", source), mock.patch.object(standardize, "TARGETS", [(target, "federation", "injected")]), mock.patch("sys.argv", ["standardize", "--check"]), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    standardize.main()
                self.assertNotEqual(raised.exception.code, 0)

    def test_ask_rule_carries_proof_and_operator_owned_cut(self):
        section = standardize.SRC.read_text().split("## What reaches the user", 1)[1].split("\n## ", 1)[0]
        for phrase in ("never a journal bullet", "last line", "alone", "one question",
                       "landed sha", "pushed and confirmed", "tests", "deploy's shape",
                       "what the tag contains", "release-cut"):
            self.assertIn(phrase, section)
