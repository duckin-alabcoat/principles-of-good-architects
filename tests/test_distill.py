"""Tests for curate/distill.py — the universal-set projection.

Load-bearing property (ADR-0042): the generated CANON digest is the UNIVERSAL set.
A class-bound habit (`Binds-to: <class>`) lives in the registry but must NOT appear
in the digest; an absent binding (or `all`) is the universal default and is kept.
Without this, Accepting a cloud-deployed habit would silently leak it into every
Architect's injected canon.

stdlib unittest: python3 -m unittest discover -s tests
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "curate"))

import distill  # noqa: E402


def _habit(slug, status="Accepted", binds_to=None, parent="P19"):
    lines = [f"## {slug}", "", f"- **Status:** {status}",
             f"- **Parent:** [{parent} — x](../principles/master.md#x)"]
    if binds_to is not None:
        lines.append(f"- **Binds-to:** {binds_to}")
    lines += ["", f"**Statement.** Statement for {slug}.", ""]
    return "\n".join(lines)


def _registry(*habits):
    return "# Universal habits\n\n" + "\n---\n\n".join(habits) + "\n"


class BindsToExclusion(unittest.TestCase):
    def _slugs(self, text):
        return {h["slug"] for h in distill.parse_habits(text)}

    def test_universal_habit_included_when_no_binding(self):
        self.assertIn("plain-universal", self._slugs(_registry(_habit("plain-universal"))))

    def test_binds_to_all_is_universal(self):
        self.assertIn("all-bound", self._slugs(_registry(_habit("all-bound", binds_to="all"))))

    def test_binds_to_all_case_insensitive(self):
        self.assertIn("caps-all", self._slugs(_registry(_habit("caps-all", binds_to="All"))))

    def test_class_bound_habit_excluded(self):
        self.assertNotIn("cloud-thing",
                         self._slugs(_registry(_habit("cloud-thing", binds_to="cloud-deployed"))))

    def test_proposed_class_bound_excluded_by_status(self):
        self.assertNotIn("prop",
                         self._slugs(_registry(_habit("prop", status="Proposed", binds_to="cloud-deployed"))))

    def test_mixed_registry_keeps_only_universal(self):
        text = _registry(
            _habit("keep-me"),
            _habit("drop-me", binds_to="cloud-deployed"),
            _habit("keep-me-too", binds_to="all"),
        )
        self.assertEqual(self._slugs(text), {"keep-me", "keep-me-too"})


class LiftedLinksResolveFromTheDigest(unittest.TestCase):
    """A Statement is written inside habits/ or principles/ and emitted at the repo root
    (CANON.md) and in a member repo (the kit copy). A relative link must be re-based, or
    the root digest ships `../adr/...`, which points outside the repository."""

    STATEMENT = ("See [ADR-0091](../adr/0091-x.md), [`batch-user-asks`](#batch-user-asks), "
                 "[sib](master.md#sib), [P5](../principles/master.md#p5--y) and "
                 "[web](https://example.com/a).")

    def _links(self, text):
        return [m.group(2) for m in distill.LINK_RE.finditer(text)]

    def test_federation_copy_rebases_onto_the_repo_root(self):
        self.assertEqual(self._links(distill.relink(self.STATEMENT, "habits")), [
            "adr/0091-x.md", "habits/master.md#batch-user-asks", "habits/master.md#sib",
            "principles/master.md#p5--y", "https://example.com/a"])

    def test_principle_statement_rebases_from_principles(self):
        self.assertEqual(self._links(distill.relink("[h](../habits/master.md#h) [q](#p2--q)",
                                                    "principles")),
                         ["habits/master.md#h", "principles/master.md#p2--q"])

    def test_kit_copy_keeps_text_and_drops_relative_targets(self):
        out = distill.relink(self.STATEMENT, "habits", audience="architect")
        self.assertEqual(self._links(out), ["https://example.com/a"])
        self.assertIn("See ADR-0091, `batch-user-asks`, sib, P5 and", out)

    def test_rendered_root_digest_has_no_link_escaping_the_root(self):
        habits = [{"slug": "s", "parent": "P1", "statement": self.STATEMENT}]
        out = distill.render([], habits, "federation")
        for target in self._links(out):
            self.assertFalse(target.startswith(("../", "#")), target)

    def test_real_digest_links_resolve(self):
        """Every relative link in the generated root digest names a file in this tree."""
        for target in self._links(distill.build("federation")):
            if distill._SCHEME_RE.match(target):
                continue
            path = target.partition("#")[0]
            self.assertTrue((ROOT / path).is_file(), target)


if __name__ == "__main__":
    unittest.main()
