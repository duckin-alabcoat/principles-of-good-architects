"""WI-0224: the runner deploying the system it is MADE OF.

The federation is the thing running the sweep. Deploying it checks a tag out over the
tree this process executes from, so anything the sweep does afterwards runs against
source that was swapped underneath it. The answer adopted in session ~241 is ordering,
not an in-process re-exec: force the self system LAST so the sweep has no further work to
do against a replaced tree, and let the next scheduled fire of each one-shot unit run the
new tag. (Ordering empties the SWEEP's remainder, not the self deploy's own later steps —
that residue is real and belongs to the cutover decision, not here.)

That claim is about what happens at RUNTIME and in WHAT ORDER, so reading the code cannot
settle it ([`a-timing-claim-is-measured-never-read`]). Every test here runs a REAL sweep
over REAL git repos with REAL tags and reads the order back out of a file the deploys
themselves wrote. The central one is differential: the self system's smoke plants a
sentinel, every other system's smoke FAILS if it sees that sentinel, and the suite asserts
both directions — green with the flag, red without it. A test that only passes proves the
ordering exists; the negative control proves the flag is what produces it.

`launchctl` is never exercised, exactly as in `test_deploy_runner.py`.
"""

from __future__ import annotations

import io
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "deploy"))
import runner  # noqa: E402
# The one owner of "names a deploy fixture removes" (WI-0361); read, not copied, so
# this fixture cannot drift from it (WI-0432: XPC_SERVICE_NAME was the first drift).
from test_deploy_runner import CLEARED_ENV  # noqa: E402


def _git(args, cwd):
    return subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True)


class SelfDeployCase(unittest.TestCase):
    """Several registered systems, each with a smoke gate that appends its own name to a
    shared order log. The log is the measurement: it is written by the deploys, in the
    order the sweep actually ran them, not by anything the test controls."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.order_log = self.root / "order.log"
        self.upstreams: dict[str, Path] = {}
        self.systems: dict[str, dict] = {}

        # The fixture's own federation repo — the runner commits receipts into it, and
        # leaving it unredirected would queue fixture receipts in the real outbox.
        self.fedrepo = self.root / "fed"
        (self.fedrepo / "outbox").mkdir(parents=True)
        _git(["init", "-b", "main"], self.fedrepo)
        (self.fedrepo / "outbox" / ".keep").write_text("")
        _git(["add", "-A"], self.fedrepo)
        _git(["commit", "-m", "fed"], self.fedrepo)

        self.mailboxes = self.root / "mailboxes.json"
        self.mailboxes.write_text(json.dumps({"members": {}}))
        self.registry = self.root / "registry.json"

        self._env = {
            "POGA_DEPLOY_REGISTRY": str(self.registry),
            "POGA_DEPLOY_ROOT": str(self.root / "deploy-trees"),
            "POGA_DEPLOY_LEDGER_DIR": str(self.root / "ledger"),
            "POGA_DEPLOY_COMMS_DIR": str(self.root / "comms"),
            "POGA_DEPLOY_OUTBOX_DIR": str(self.fedrepo / "outbox"),
            "POGA_DEPLOY_MAILBOXES": str(self.mailboxes),
        }
        self._cleared = tuple(CLEARED_ENV)
        self._saved = {k: os.environ.get(k) for k in (*self._env, *self._cleared)}
        os.environ.update(self._env)
        for k in self._cleared:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    # -- fixture builders ------------------------------------------------------

    def _smoke_cmd(self, name: str, refuse_after_self: bool) -> list[str]:
        """Append this system's name to the order log.

        With `refuse_after_self`, first EXIT 1 if the self system's sentinel is already
        there. That is the clause-4 property made executable: a system deployed after the
        self system would be acting on a tree the self deploy had already replaced, and
        here it says so by failing rather than by being reasoned about."""
        sentinel = f"grep -q '^SELF$' {self.order_log} && exit 1;" if refuse_after_self else ""
        return ["/bin/sh", "-c", f"{sentinel} echo {name} >> {self.order_log}; exit 0"]

    def _contract(self, name: str, *, is_self: bool, restart: str,
                  units: list[str] | None, apply_cmd: list[str] | None = None,
                  verify_ok: bool = True) -> dict:
        contract = {
            "contract_version": 1,
            "system": name,
            "units": units if units is not None else [],
            "restart": restart,
            "deps": {"kind": "none"},
            # A non-self system refuses if the self system already ran; the self system
            # plants the sentinel the others refuse on.
            "smoke": {"cmd": self._smoke_cmd("SELF" if is_self else name,
                                             refuse_after_self=not is_self)},
            "verify": {"cmd": ["/bin/sh", "-c", f"exit {0 if verify_ok else 1}"],
                       "settle_seconds": 0},
        }
        if apply_cmd is not None:
            contract["apply"] = {"cmd": apply_cmd}
        return contract

    def _add_system(self, name: str, *, is_self: bool = False, restart: str = "none",
                    units: list[str] | None = None,
                    apply_cmd: list[str] | None = None) -> None:
        up = self.root / f"up-{name}"
        up.mkdir()
        (up / "deploy").mkdir()
        (up / "deploy" / "deploy.json").write_text(json.dumps(
            self._contract(name, is_self=is_self, restart=restart, units=units,
                           apply_cmd=apply_cmd), indent=2))
        (up / "marker.txt").write_text("v1\n")
        _git(["init", "-b", "main"], up)
        _git(["add", "-A"], up)
        _git(["commit", "-m", "first"], up)
        _git(["tag", "v1.0.0"], up)
        self.upstreams[name] = up

        row: dict = {"remote": str(up), "subdir": None}
        if is_self:
            row["self"] = True
        self.systems[name] = row
        self._write_registry()

    def _release(self, name: str, tag: str, contract: dict) -> None:
        up = self.upstreams[name]
        (up / "deploy" / "deploy.json").write_text(json.dumps(contract, indent=2))
        (up / "marker.txt").write_text(tag)
        _git(["add", "-A"], up)
        _git(["commit", "-m", tag], up)
        _git(["tag", tag], up)

    def _set_self(self, name: str, value: bool) -> None:
        if value:
            self.systems[name]["self"] = True
        else:
            self.systems[name].pop("self", None)
        self._write_registry()

    def _write_registry(self) -> None:
        self.registry.write_text(json.dumps(
            {"contract_version": 1, "systems": self.systems}, indent=2))

    def _drop_self_flag(self) -> None:
        for row in self.systems.values():
            row.pop("self", None)
        self._write_registry()

    # -- measurement -----------------------------------------------------------

    def _sweep(self) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = runner.sweep()
        return rc, buf.getvalue()

    def _order(self) -> list[str]:
        if not self.order_log.exists():
            return []
        return self.order_log.read_text().split()


class TestTheSelfSystemIsSweptLast(SelfDeployCase):

    def test_a_real_sweep_runs_the_self_system_last(self):
        """The measurement, not the reading: three systems whose names would sort the
        self one FIRST, and the order log says it ran last."""
        self._add_system("alpha", is_self=True)
        self._add_system("operator")
        self._add_system("zulu")
        rc, _ = self._sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(self._order(), ["operator", "zulu", "SELF"])

    def test_without_the_flag_the_same_registry_sweeps_self_first_and_fails(self):
        """The negative control, and the whole reason the flag exists.

        Identical registry, `self` removed. Alphabetically `alpha` goes first, so the two
        systems after it are deployed against a tree the self deploy had already replaced
        — and their smoke gates say so by failing. If this test ever goes green, the one
        above stopped proving anything."""
        self._add_system("alpha", is_self=True)
        self._add_system("operator")
        self._add_system("zulu")
        self._drop_self_flag()
        rc, out = self._sweep()
        self.assertEqual(self._order(), ["SELF"])
        self.assertEqual(rc, 1)
        self.assertIn("smoke", out.lower())

    def test_the_order_follows_the_flag_not_the_name_federation(self):
        """`federation` is the only system that carries the flag today, which makes
        hardcoding the string the cheapest possible implementation and the wrong one: the
        next system to deploy itself would get no protection and nothing would say so.
        Here a system NAMED federation is not flagged and does not go last."""
        self._add_system("aaa-real-self", is_self=True)
        self._add_system("federation")
        rc, _ = self._sweep()
        self.assertEqual(rc, 0)
        # Both halves matter. Plain `sorted()` would answer ["SELF", "federation"], and a
        # hardcoded "federation last" would answer the same — only reading the flag gives
        # this. The first version of this test put the decoy FIRST alphabetically, so its
        # expected order WAS the alphabetical one and it survived the plain-sorted
        # mutation while claiming to rule it out.
        self.assertEqual(self._order(), ["federation", "SELF"])

    def test_a_registry_with_no_self_row_is_plain_alphabetical(self):
        """No self system is the common case and must be untouched by this change."""
        self._add_system("zulu")
        self._add_system("operator")
        rc, _ = self._sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(self._order(), ["operator", "zulu"])

    def test_two_self_rows_are_refused_rather_than_ordered(self):
        """A runner is made of one tree. Two claims is a contradiction, and picking one
        would be right half the time with no way to tell which half you got."""
        self._add_system("alpha", is_self=True)
        self._add_system("beta", is_self=True)
        with self.assertRaises(runner.DeployError) as caught:
            runner.sweep_order(json.loads(self.registry.read_text()))
        self.assertIn("alpha", str(caught.exception))
        self.assertIn("beta", str(caught.exception))

    def test_a_non_boolean_self_value_is_refused_not_ignored(self):
        """`"self": 1` and `"self": "true"` both look right and both read as NOT self —
        `1 is True` is False. There is no registry schema (`load_registry` is a bare
        `json.loads`), so nothing else would catch it, and the silent outcome is the
        dangerous one: the pre-WI-0224 order is restored AND the restart guard disarmed.
        Two selves already refuses loudly; a malformed self must not degrade quietly."""
        self._add_system("alpha", is_self=True)
        for bad in (1, "true", "yes", 0):
            with self.subTest(value=bad):
                self.systems["alpha"]["self"] = bad
                self._write_registry()
                with self.assertRaises(runner.DeployError) as caught:
                    runner.sweep_order(json.loads(self.registry.read_text()))
                self.assertIn("alpha", str(caught.exception))

    def test_a_mis_cased_self_key_is_refused_not_ignored(self):
        """Same failure through the other door: `"Self": true` is not the key anything
        reads, so the row is silently not-self. Refusing a near-miss costs a typo's worth
        of friction and buys the one guarantee this whole change exists for."""
        self._add_system("alpha", is_self=True)
        for bad_key in ("Self", "SELF", "sElf"):
            with self.subTest(key=bad_key):
                row = self.systems["alpha"]
                row.pop("self", None)
                row.pop(bad_key, None)
                row[bad_key] = True
                self._write_registry()
                with self.assertRaises(runner.DeployError) as caught:
                    runner.sweep_order(json.loads(self.registry.read_text()))
                self.assertIn(bad_key, str(caught.exception))
                row.pop(bad_key)

    def test_status_orders_the_same_way_as_the_sweep(self):
        """Two surfaces computing one roster fact separately is WI-0175's defect: the
        hook printed 7 and the command it told a human to run printed 15. Both route
        through `sweep_order` here, so they cannot disagree."""
        self._add_system("alpha", is_self=True)
        self._add_system("operator")
        buf = io.StringIO()
        with redirect_stdout(buf):
            runner.status(fetch_only=True)
        rows = [ln.split()[0] for ln in buf.getvalue().splitlines()[1:] if ln.strip()]
        self.assertEqual(rows, ["operator", "alpha"])


class TestTheSelfSystemMayNotRestartItself(SelfDeployCase):

    def test_a_self_contract_that_would_kickstart_its_units_is_refused(self):
        """Every unit of the self system runs code this process is made of — the sweep's
        own unit is necessarily among them — so `kickstart -k` kills the sweep issuing the
        command, mid-deploy, before the ledger write."""
        self._add_system("alpha", is_self=True, restart="kickstart",
                         units=["com.example.sweep"])
        rc, out = self._sweep()
        self.assertEqual(rc, 1)
        self.assertIn("REFUSED", out)
        self.assertIn("restarts the sweep", out)

    def test_the_refusal_happens_before_anything_is_checked_out(self):
        """A refusal that has already replaced the tree is not a refusal. The contract is
        read from the tag before checkout, so the guard fires with the previous version
        still in place — here, with nothing deployed at all."""
        self._add_system("alpha", is_self=True, restart="kickstart",
                         units=["com.example.sweep"])
        self._sweep()
        entry = json.loads(self.registry.read_text())["systems"]["alpha"]
        self.assertIsNone(runner.deployed_tag("alpha", entry))
        self.assertEqual(self._order(), [])

    def test_the_guard_does_not_turn_a_good_rollback_into_a_critical(self):
        """The guard's first home was inside `read_contract`, which also reads the
        PREVIOUS tag's contract during a rollback. So a restore that had already put the
        old tree back correctly would raise here, be caught as `RollbackFailed`, and exit
        2 — the loudest state this program has, over a rollback that worked.

        Built as it would actually arise: v1.0.0 is deployed while the row is NOT flagged
        (its kickstart contract is legal then), the row is flagged afterwards, and v1.1.0
        fails verification. The rollback must read v1.0.0's contract without the guard
        firing on it. v1.0.0 declares an `apply` strategy so the restore takes the apply
        fork and `launchctl` is never reached."""
        self._add_system("alpha", restart="kickstart", units=["com.example.sweep"],
                         apply_cmd=["/bin/sh", "-c", "exit 0"])
        rc, _ = self._sweep()
        self.assertEqual(rc, 0, "the unflagged first deploy should succeed")

        self._set_self("alpha", True)
        self._release("alpha", "v1.1.0", self._contract(
            "alpha", is_self=True, restart="none", units=[], verify_ok=False))

        rc, out = self._sweep()
        self.assertEqual(rc, 1, f"expected a clean rolled-back (1), not CRITICAL (2):\n{out}")
        self.assertNotIn("CRITICAL", out)
        self.assertEqual(runner.read_ledger("alpha").get("status"), "rolled-back")
        entry = json.loads(self.registry.read_text())["systems"]["alpha"]
        self.assertEqual(runner.deployed_tag("alpha", entry), "v1.0.0")

    def test_a_non_self_system_may_still_declare_kickstart(self):
        """The guard is scoped to the self row. Narrowing matters: a guard that fires on
        correct code gets deleted, and then it is not there for the case it was for."""
        self._add_system("alpha", restart="kickstart")
        rc, _ = self._sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(self._order(), ["alpha"])

    def test_a_self_contract_declaring_no_units_is_not_refused(self):
        """There is nothing to restart, so there is nothing to refuse. The guard is about
        restarting the runner's own units, not about carrying the flag."""
        self._add_system("alpha", is_self=True, restart="kickstart", units=[])
        rc, _ = self._sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(self._order(), ["SELF"])


class TestTheShippedFederationRow(unittest.TestCase):
    """The three artifacts this item ships, checked as they actually sit on disk. The
    tests above prove the MECHANISM; these prove the federation is wired into it — a
    capability whose subject never declares it reads as absent
    ([`ship-the-detector-with-the-capability`])."""

    def setUp(self):
        self.registry = json.loads((REPO / "deploy" / "registry.json").read_text())
        self.contract = json.loads((REPO / "deploy" / "deploy.json").read_text())

    def test_the_registry_carries_a_federation_row_flagged_self(self):
        row = self.registry["systems"].get("federation")
        self.assertIsNotNone(row, "the federation has no deploy registry row")
        self.assertIs(row.get("self"), True)

    def test_the_federation_is_last_in_the_live_sweep_order(self):
        self.assertEqual(runner.sweep_order(self.registry)[-1], "federation")

    def test_a_plain_alphabetical_sweep_would_not_put_it_last(self):
        """Not a control over the implementation — it never calls it. It pins a fact about
        THIS registry: the alphabet does not do the ordering's job here, so the flag is
        load-bearing rather than decorative. An alphabetical sweep is not an order
        guarantee, and this test is the reminder that the alphabet was never the
        mechanism."""
        if (REPO / "PUBLIC-CUT-RECEIPT.md").is_file():
            # The public cut ships the registry with the `self` row alone, so there is no
            # other row for the alphabet to order. The fact pinned here is about the
            # internal fleet's registry; the mechanism tests above still run.
            self.skipTest("public cut: the registry ships its self row only")
        self.assertNotEqual(sorted(self.registry["systems"])[-1], "federation")

    def test_the_federation_contract_validates_against_the_shipped_schema(self):
        schema = json.loads((REPO / "deploy" / "contract.schema.json").read_text())
        runner._validate_contract(self.contract, schema, "federation")
        self.assertEqual(self.contract["system"], "federation")
        self.assertEqual(self.contract["contract_version"],
                         runner.SUPPORTED_CONTRACT_VERSION)

    def test_the_real_guard_passes_on_the_real_shipped_pair(self):
        """Every other test here runs the guard against fixtures. This runs the shipped
        predicate over the shipped registry row and the shipped contract — the pair that
        actually deploys — so a contract edited out of agreement with its own row is
        caught here rather than on the Runner at 3am."""
        row = self.registry["systems"]["federation"]
        runner.refuse_self_restart("federation", row, self.contract, "v0.0.0-test")

    def test_the_guard_would_fire_if_that_pair_ever_disagreed(self):
        """The control for the test above, which passes by doing nothing observable."""
        row = self.registry["systems"]["federation"]
        with self.assertRaises(runner.DeployError):
            runner.refuse_self_restart(
                "federation", row, {**self.contract, "restart": "kickstart"}, "v0.0.0-test")

    def test_the_federation_contract_declares_restart_none(self):
        """Its units are the runner's own. `none` is not a skipped step: all three are
        scheduled one-shots, so the next fire runs the new tag."""
        self.assertEqual(self.contract["restart"], "none")

    def test_the_contracts_units_are_the_units_the_repo_actually_ships(self):
        """A contract naming a unit that does not exist would cut over nothing and report
        nothing — `cutover_state` reads `launchctl print` per unit, so a typo reads as
        'not loaded' forever rather than as a typo."""
        shipped = set()
        for tpl in (REPO / "deploy").glob("com.federation.*.plist.template"):
            shipped.add(tpl.name[: -len(".plist.template")])
        self.assertTrue(shipped, "no federation plist templates found")
        self.assertEqual(set(self.contract["units"]), shipped)

    def test_the_federation_does_not_seed_its_session_state(self):
        """`.session-state/` is each unit's own log and scratch directory; every one of
        them recreates it. Seeding it would copy one machine's logs into another's tree."""
        paths = {item["path"] for item in self.contract.get("state", [])}
        self.assertNotIn(".session-state/", paths)
        self.assertNotIn(".session-state", paths)

    def test_every_declared_state_path_is_one_a_checkout_would_not_carry(self):
        """The other direction, and the one an `assertNotIn` cannot reach. `state` exists
        for what a tag checkout does NOT bring: if a declared path were tracked, the
        checkout already places it and the entry is dead weight that reads as coverage.
        Asked of git rather than of a list, so it stays true as `.gitignore` moves."""
        paths = [item["path"] for item in self.contract.get("state", [])]
        self.assertTrue(paths, "the contract declares no state at all")
        for path in paths:
            with self.subTest(path=path):
                r = subprocess.run(["git", "check-ignore", "-q", path],
                                   cwd=str(REPO), capture_output=True)
                self.assertEqual(r.returncode, 0,
                                 f"{path} is not gitignored, so the checkout carries it "
                                 f"and seeding it is dead weight")


class TheShippedTemplatesLeaveTheirPathToTheRendererTest(unittest.TestCase):
    """WI-0395, the tracked-artifact half. The sweep could not see
    a verb installed in the operator's `~/.local/bin`, and a release stalled for a day; the fix
    puts the operator's own bin directory on every rendered unit's PATH. This asserts the
    part that lives in the repo rather than in the renderer — that the templates state a
    PATH for the renderer to widen, and that not one of them names a home directory to do
    it, because a committed `/Users/...` is WI-0132's defect and would be wrong on every
    machine but one.

    Subjects come from the glob, not from a list written here: a fourth unit added later
    is covered the day it lands ([`derive-a-checks-subjects-from-the-authority`])."""

    TEMPLATES = sorted((REPO / "deploy").glob("com.federation.*.plist.template"))

    def test_there_are_templates_to_check(self):
        """A guard whose subject has moved reads as a clean pass and is not one."""
        self.assertTrue(self.TEMPLATES, "no com.federation.*.plist.template found")

    def test_every_template_declares_a_path_for_the_renderer_to_widen(self):
        """The renderer only widens a PATH that is already declared — handing a unit that
        declares none a PATH of one directory would take `/usr/bin` away from it. So a
        template that stops declaring one silently opts out of the fix."""
        for t in self.TEMPLATES:
            with self.subTest(template=t.name):
                declared = plistlib.loads(t.read_bytes())
                path = (declared.get("EnvironmentVariables") or {}).get("PATH")
                self.assertTrue(path, f"{t.name} declares no PATH, so the renderer has "
                                      f"nothing to prepend the operator's bin to")

    def test_no_template_spells_a_home_directory(self):
        """WI-0132. Asked of the PARSED VALUES, never of the source: these templates
        document themselves in XML comments, and this one now discusses `~/.local/bin` in
        prose on purpose — a check over the raw text would fire on its own explanation
        ([`a-guard-that-fires-on-correct-code-gets-deleted`])."""
        for t in self.TEMPLATES:
            with self.subTest(template=t.name):
                for value in self._strings(plistlib.loads(t.read_bytes())):
                    self.assertNotRegex(
                        value, r"(^|:)/(Users|home)/",
                        f"{t.name} names a home directory in a VALUE. It is wrong on "
                        f"every other machine that deploys the same tag; the renderer "
                        f"is where a per-machine path belongs.")

    def test_the_operator_bin_the_renderer_adds_is_relative_to_home(self):
        """The other end of the same claim. The constant must stay RELATIVE — an
        absolute default would put one machine's home into the module and render the
        same wrong path everywhere."""
        self.assertFalse(Path(runner.OPERATOR_BIN_RELATIVE).is_absolute())
        self.assertEqual(runner.OPERATOR_BIN_RELATIVE, ".local/bin")

    def _strings(self, value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield from self._strings(key)
                yield from self._strings(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                yield from self._strings(item)


if __name__ == "__main__":
    unittest.main()
