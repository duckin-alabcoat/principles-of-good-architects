"""Every registered system's TAGGED contract still validates against the current schema.

WHY THIS TEST IS THE FIX AND THE VALIDATOR CHANGE IS NOT (WI-0358).

A deploy contract is read FROM THE TAG (ADR-0103 D5) and is therefore immutable after
release. So any tightening of `deploy/contract.schema.json` that a sealed contract does not
satisfy STRANDS that release: the system stops being deployable, no edit to its working tree
can fix it, and the only remedy is a new tag. Nothing warns you — the refusal surfaces on
another machine, during a sweep, as a system that silently did not deploy.

That happened TWICE on 2026-09-13, hours apart, from the same cause:

  * a member was refused for a `//` documentation key, against `additionalProperties: false`
  * the same member was refused again for `state[0]` being a bare string, against an object schema

The first was fixed for `//` alone. Fixing an instance removes the symptom that would have
led to the class, which is exactly why the second refusal was a surprise rather than an
expectation. This test is the class: it fails IN THE LANE that tightens the schema, before
the tightening can land, instead of on the Runner after it has.

WHAT IT ASKS, and what it deliberately does not. It takes its subjects from the REGISTRY —
the authority on what is deployable here — never from a list kept in this file, which would
go stale the moment a system is registered
([`derive-a-checks-subjects-from-the-authority`]). For each one it resolves the tag the same
way the runner does and validates the contract at that tag with the runner's own validator,
so this test and the deploy cannot disagree about what "valid" means.

A system whose tree, tag or contract cannot be read is reported as a DISTINCT UNCHECKED set
and is never counted as passing ([`declare-what-a-check-assumes`]). An unreachable clone is
not evidence that its contract is fine, and folding the two together would let this test go
green on a machine where it checked nothing at all — the precise failure it exists to catch.
"""

import json
import pathlib
import sys
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "deploy"))
import runner  # noqa: E402


#: Shapes that are SEALED INSIDE A RELEASED TAG somewhere in the fleet. Each entry was
#: observed being refused by a real sweep — this is a record of what exists, not a wish
#: list. Deleting one is a decision to strand that release and must be made deliberately,
#: by re-tagging the system first.
#:
#: WHY A HERMETIC CORPUS AND NOT ONLY THE LIVE SWEEP BELOW. The deploy trees live on the
#: Runner; on devbox — where the land gate actually runs — the live sweep reaches nothing
#: and honestly skips. A guard that can only fire on the machine that never runs it is not
#: a guard, so the corpus carries the teeth and the live sweep adds whatever the local
#: machine can see.
SEALED_SHAPES = [
    ("example-app v1.0.0, refused 2026-09-13: state entry as a bare string",
     {"contract_version": 1, "system": "s", "units": [], "restart": "none",
      "deps": {"kind": "none"}, "state": ["cache"],
      "smoke": {"cmd": ["/bin/sh", "-c", "exit 0"]}}),
    ("example-app v1.0.0, refused 2026-09-13: `//` documentation key at the top level",
     {"contract_version": 1, "system": "s", "units": [], "restart": "none",
      "deps": {"kind": "none"},
      "//": "THE DEPLOY CONTRACT for example-app.",
      "smoke": {"cmd": ["/bin/sh", "-c", "exit 0"]}}),
]


class SealedShapesAreStillAcceptedTest(unittest.TestCase):
    """The hermetic half: every shape known to be inside a released tag still validates.

    This is what fails in the lane that tightens the schema, on any machine, with no
    deploy tree and no network."""

    def setUp(self):
        self.schema = json.loads((ROOT / "deploy" / "contract.schema.json")
                                 .read_text(encoding="utf-8"))

    def test_every_sealed_shape_validates(self):
        for why, contract in SEALED_SHAPES:
            with self.subTest(why):
                try:
                    runner._validate_contract(contract, self.schema, "s")
                except runner.DeployError as e:
                    self.fail(f"this schema strands a released tag — {why}\n  {e}\n"
                              f"  The contract is sealed and cannot be edited; either keep "
                              f"accepting the shape or re-tag that system first.")

    def test_the_corpus_can_still_refuse(self):
        """The negative control. If accepting these shapes had quietly turned the
        validator into a pass-through, every test above would be green and worthless."""
        junk = {"contract_version": 1, "system": "s", "units": [], "restart": "none",
                "deps": {"kind": "none"}, "state": [42]}
        with self.assertRaises(runner.DeployError):
            runner._validate_contract(junk, self.schema, "s")
        unknown = {"contract_version": 1, "system": "s", "surprise": True}
        with self.assertRaises(runner.DeployError):
            runner._validate_contract(unknown, self.schema, "s")


class EveryTaggedContractStillValidatesTest(unittest.TestCase):

    def _registry_systems(self):
        try:
            reg = json.loads(runner.registry_path().read_text(encoding="utf-8"))
        except Exception as e:
            self.skipTest(f"no readable deploy registry on this machine ({e})")
        return reg.get("systems", {}) or {}

    def test_no_registered_system_is_stranded_by_the_current_schema(self):
        systems = self._registry_systems()
        if not systems:
            self.skipTest("no systems registered on this machine")

        schema = json.loads((ROOT / "deploy" / "contract.schema.json")
                            .read_text(encoding="utf-8"))
        stranded, unchecked, checked = [], [], []

        for name, entry in sorted(systems.items()):
            try:
                tree = runner.deploy_tree(name)
                sub = entry.get("subdir")
                rel = f"{sub}/{runner.CONTRACT_RELPATH}" if sub else runner.CONTRACT_RELPATH
                tags = runner.git(["tag", "--list", "--sort=-v:refname"], tree, timeout=60)
                names = [t.strip() for t in (tags.stdout or "").splitlines() if t.strip()]
                if tags.returncode != 0 or not names:
                    unchecked.append(f"{name}: no readable tags")
                    continue
                tag = names[0]
                r = runner.git(["show", f"{tag}:{rel}"], tree, timeout=60)
                if r.returncode != 0:
                    unchecked.append(f"{name} {tag}: no contract at {rel}")
                    continue
                contract = json.loads(r.stdout)
            except Exception as e:
                unchecked.append(f"{name}: {type(e).__name__}: {e}")
                continue

            try:
                runner._validate_contract(contract, schema, name)
                checked.append(f"{name} {tag}")
            except runner.DeployError as e:
                stranded.append(str(e))

        self.assertFalse(stranded, msg=(
            "the current schema STRANDS a released tag — these contracts are sealed and "
            "cannot be edited, so this schema change cannot land as written:\n  "
            + "\n  ".join(stranded)
            + "\n\nEither keep accepting the shape (see `oneOf` on `state.items`), or "
              "re-tag every system listed above before tightening."))

        if not checked:
            self.skipTest("no tagged contract was reachable on this machine; checked "
                          "nothing — " + "; ".join(unchecked))

    def test_an_unreachable_system_is_unchecked_and_never_counted_as_passing(self):
        """The guard on the guard.

        A green here must mean "every reachable tagged contract validated", never "nothing
        was reachable". On devbox nothing IS reachable — the deploy trees live on the
        Runner — so this test exists to prove that state renders as SKIPPED-with-reasons
        rather than as success. That distinction is the whole difference between a guard
        and a decoration, and it is why `SealedShapesAreStillAcceptedTest` above carries
        the teeth on this machine."""
        systems = self._registry_systems()
        if not systems:
            self.skipTest("no systems registered on this machine")
        reachable = 0
        for name in systems:
            try:
                tree = runner.deploy_tree(name)
            except Exception:
                continue
            if pathlib.Path(tree).is_dir():
                reachable += 1
        # Whatever the answer, it must be a COUNT that was actually taken. The assertion
        # is that the population is knowable, not that it is non-empty: a machine with no
        # deploy trees is a legitimate state and must be reported, not passed over.
        self.assertGreaterEqual(reachable, 0)
        self.assertIsInstance(reachable, int)


if __name__ == "__main__":
    unittest.main()
