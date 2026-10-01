"""Tests for deploy/legacydeps.py — the WI-0366 legacy-dependency inspector.

THE LOAD-BEARING TEST IS `ADetectorThatNeverFiresIsNotOneTest`. Everything this module
produces is an argument that a list is EMPTY, and an empty list is exactly what a broken
detector also produces. So the fixture is built clean — every plist, every loaded job,
every cron line and the poga link all name the RELEASE tree — and the control asserts
both halves from that one starting point: the clean host exits 0 with `remaining == []`,
and adding a single old-root reference to that same host flips it to exit 1. A green
suite without that pair would look identical if the matcher never matched anything
([`verify-in-the-created-configuration`](../habits/master.md#verify-in-the-created-configuration)).

`APathComparisonIsNotASubstringSearchTest` guards the two traps that have already been
paid for here: `/a/b` must not match `/a/bc`, and a case-differing spelling of the same
directory on a case-insensitive volume MUST match — the defect WI-0392 recorded when a
production data path reached a clone through a symlink stub "on a case-insensitive
filesystem". The case test asserts the contrast explicitly, because a fold-aware match
and a plain one only differ on the input nobody thought to try.

NOTHING HERE TOUCHES THE REAL MACHINE. `launchctl` and `crontab` are shell scripts in a
temporary directory, the LaunchAgents directory is a temporary directory, and the poga
link is a temporary symlink. A test that could read or alter this machine's launchd, its
crontab or the operator's `poga` command would be a worse bug than any it caught — and
the fake `crontab` refuses any argv but `-l` precisely so that "it only ever reads" is
asserted rather than asserted-in-a-docstring.

stdlib unittest: python3 -m unittest tests.test_legacydeps -v
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import plistlib
import shutil
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import legacydeps                                                    # noqa: E402

SECRET = "sso-token-topsecret-never-store-this"


def loaded_output(label, working_directory, arguments, config=None, extra=()):
    """A `launchctl print` transcript in the shape `parse_loaded_job` reads.

    Written out in full rather than reduced to the two fields the parser needs: the
    surrounding noise — including an environment block with a credential in it — is the
    reason `mailacceptance` refuses to store this output, and a fixture without it could
    not assert that refusal.
    """
    lines = [f"{label} = {{",
             "\tactive count = 1",
             f"\tpath = /Users/nobody/Library/LaunchAgents/{label}.plist",
             "\tstate = running",
             "\tpid = 4242",
             "\tlast exit code = 0",
             f"\tworking directory = {working_directory}",
             "\targuments = {"]
    lines += [f"\t\t{argument}" for argument in arguments]
    lines.append("\t}")
    lines.append("\tenvironment = {")
    if config:
        lines.append(f"\t\tPOGA_FEDERATION_CONFIG => {config}")
    lines.append(f"\t\tSSO_TOKEN => {SECRET}")
    lines += [f"\t\t{line}" for line in extra]
    lines.append("\t}")
    lines.append("}")
    return "\n".join(lines) + "\n"


class Host:
    """A synthetic machine, clean by construction.

    Old checkout, release tree, LaunchAgents directory, poga symlink and fake
    launchctl/crontab executables. Everything in it names the RELEASE tree, so any test
    that wants a finding has to introduce exactly one, and the test named after that
    finding is the only thing that could have produced it.
    """

    LIST_HEADER = "PID\tStatus\tLabel"

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.host = self.tmp / "host"
        # `trace/` records what the fake executables were asked to do. It lives OUTSIDE
        # the hashed tree so that recording a read does not read as a write.
        self.trace = self.tmp / "trace"
        self.fake = self.tmp / "fake"
        self.old = self.host / "Projects" / "federation"
        self.release = self.host / "deploy" / "federation"
        self.agents = self.host / "LaunchAgents"
        self.poga = self.host / "bin" / "poga"
        for directory in (self.trace, self.fake, self.agents, self.old, self.release,
                          self.host / "bin"):
            directory.mkdir(parents=True, exist_ok=True)
        (self.old / "poga").write_text("#!/bin/sh\n")
        (self.release / "poga").write_text("#!/bin/sh\n")
        self.poga.symlink_to(self.release / "poga")

        self.launchctl = self.fake / "launchctl"
        self.crontab = self.fake / "crontab"
        self._labels = ["com.apple.Finder", "com.apple.dock.extra"]
        self._write_launchctl()
        self._write_crontab()
        self.set_labels_rc(0)
        self.loaded("com.federation.mail-poller",
                    working_directory=str(self.release),
                    arguments=["/usr/bin/python3", str(self.release / "curate/mailworker.py")],
                    config="/Users/nobody/federation-config.json")
        self.cron([f"*/5 * * * * {self.release}/poga sweep"])
        self.plist("com.federation.mail-poller",
                   ProgramArguments=["/usr/bin/python3",
                                     str(self.release / "curate/mailworker.py")],
                   WorkingDirectory=str(self.release),
                   StandardOutPath=str(self.release / "logs/poller.out"))

    # ── the fakes ────────────────────────────────────────────────────────────────

    def _script(self, path, body):
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _write_launchctl(self):
        self._script(self.launchctl, f"""#!/bin/sh
echo "$*" >> "{self.trace}/launchctl.log"
case "$1" in
  list)
    cat "{self.fake}/list.txt"
    exit "$(cat "{self.fake}/list.rc")"
    ;;
  print)
    label="${{2##*/}}"
    if [ -f "{self.fake}/print-$label.txt" ]; then
      cat "{self.fake}/print-$label.txt"
      exit 0
    fi
    echo "Could not find service \\"$2\\"" >&2
    exit 113
    ;;
esac
echo "legacydeps ran an unexpected launchctl subcommand: $*" >&2
exit 99
""")

    def _write_crontab(self):
        self._script(self.crontab, f"""#!/bin/sh
echo "$*" >> "{self.trace}/crontab.log"
if [ "$1" != "-l" ]; then
  echo "legacydeps ran crontab with something other than -l: $*" >&2
  exit 99
fi
if [ -s "{self.fake}/cron.err" ]; then cat "{self.fake}/cron.err" >&2; fi
cat "{self.fake}/cron.txt"
exit "$(cat "{self.fake}/cron.rc")"
""")

    # ── what the machine looks like ──────────────────────────────────────────────

    def set_labels_rc(self, code):
        (self.fake / "list.rc").write_text(str(code))
        self._flush_labels()

    def _flush_labels(self):
        rows = [self.LIST_HEADER]
        rows += [f"-\t0\t{label}" for label in self._labels]
        (self.fake / "list.txt").write_text("\n".join(rows) + "\n")

    def set_list_output(self, text, code=0):
        (self.fake / "list.txt").write_text(text)
        (self.fake / "list.rc").write_text(str(code))

    def loaded(self, label, working_directory, arguments, config=None, raw=None):
        """Declare a job as both LISTED and PRINTABLE."""
        if label not in self._labels:
            self._labels.append(label)
            self._flush_labels()
        text = raw if raw is not None else loaded_output(label, working_directory,
                                                         arguments, config)
        (self.fake / f"print-{label}.txt").write_text(text)
        return text

    def listed_only(self, label):
        """A label the inventory names and `launchctl print` will not report."""
        if label not in self._labels:
            self._labels.append(label)
            self._flush_labels()

    def cron(self, lines, code=0, stderr=""):
        (self.fake / "cron.txt").write_text("".join(line + "\n" for line in lines))
        (self.fake / "cron.rc").write_text(str(code))
        (self.fake / "cron.err").write_text(stderr)

    def plist(self, name, **fields):
        data = {"Label": fields.pop("Label", name)}
        data.update(fields)
        path = self.agents / f"{name}.plist"
        path.write_bytes(plistlib.dumps(data))
        return path

    def point_poga_at(self, target):
        self.poga.unlink()
        self.poga.symlink_to(target)

    # ── running it ───────────────────────────────────────────────────────────────

    def argv(self, **override):
        args = {"--old-root": str(self.old), "--release-root": str(self.release),
                "--poga": str(self.poga), "--launch-agents": str(self.agents),
                "--launchctl": str(self.launchctl), "--crontab": str(self.crontab)}
        args.update(override)
        out = []
        for key, value in args.items():
            if value is not None:
                out += [key, str(value)]
        return out + ["--json"]

    def run(self, **override):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = legacydeps.main(self.argv(**override))
        return code, out.getvalue(), err.getvalue()

    def inspect(self, **override):
        code, out, _ = self.run(**override)
        return code, json.loads(out)


class LegacyDepsCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.h = Host(tmp)

    def sources(self, report):
        return {name: detail.get("status") for name, detail in report["sources"].items()}

    def gap_sources(self, report):
        return [gap["source"] for gap in report["gaps"]]

    def assertClean(self, report, code):
        self.assertEqual(report["remaining"], [], "a clean host must find nothing")
        self.assertEqual(report["gaps"], [], "a clean host must have read every source")
        self.assertEqual(report["verdict"], "clean")
        self.assertEqual(code, 0)


class ADetectorThatNeverFiresIsNotOneTest(LegacyDepsCase):
    """The control. Both halves, from one fixture, or neither half means anything."""

    def test_a_clean_host_exits_zero_with_an_empty_remaining_list(self):
        code, report = self.h.inspect()
        self.assertClean(report, code)
        self.assertEqual(self.sources(report),
                         {"launch_agents": "clean", "launchd": "clean",
                          "cron": "clean", "poga": "clean"})

    def test_one_added_reference_flips_the_same_fixture_to_exit_one(self):
        """The same host, one line different — this is what proves the zero above."""
        before_code, before = self.h.inspect()
        self.assertClean(before, before_code)

        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/python3",
                                       str(self.h.old / "curate/mailworker.py")])

        after_code, after = self.h.inspect()
        self.assertEqual(after_code, 1)
        self.assertEqual(after["verdict"], "remaining")
        self.assertEqual(len(after["remaining"]), 1)
        self.assertEqual(after["gaps"], [], "the flip must come from a finding, not a gap")

    def test_the_record_is_the_shape_the_acceptance_ledger_reads(self):
        code, report = self.h.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["kind"], "legacy-dependency-inspection")
        self.assertEqual(report["old_root"], str(self.h.old))
        self.assertTrue(report["hostname"])
        self.assertIn("+00:00", report["observed_at"])
        for key in ("sources", "remaining", "gaps"):
            self.assertIn(key, report)


class WhatALaunchAgentDeclaresIsInspectedTest(LegacyDepsCase):

    def test_a_plist_whose_program_arguments_name_the_old_root_is_remaining(self):
        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/python3",
                                       str(self.h.old / "curate/mailworker.py")])

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        found = report["remaining"][0]
        self.assertEqual(found["source"], "launch_agents")
        self.assertEqual(found["field"], "ProgramArguments[1]")
        self.assertEqual(found["matched"], str(self.h.old / "curate/mailworker.py"))
        self.assertTrue(found["where"].endswith("com.federation.leftover.plist"))

    def test_a_plist_whose_working_directory_names_the_old_root_is_remaining(self):
        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/true"],
                     WorkingDirectory=str(self.h.old))

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertEqual([hit["field"] for hit in report["remaining"]], ["WorkingDirectory"])

    def test_a_plist_naming_only_the_release_root_is_clean(self):
        self.h.plist("com.federation.migrated",
                     ProgramArguments=["/usr/bin/python3",
                                       str(self.h.release / "curate/mailworker.py")],
                     WorkingDirectory=str(self.h.release))

        code, report = self.h.inspect()

        self.assertClean(report, code)
        migrated = [item for item in report["sources"]["launch_agents"]["plists"]
                    if item["plist"].endswith("com.federation.migrated.plist")][0]
        self.assertFalse(migrated["references_old_root"])
        self.assertTrue(migrated["references_release"],
                        "the release tree is reported for contrast, not only the old one")

    def test_an_environment_variable_pointing_into_the_old_root_is_remaining(self):
        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/true"],
                     EnvironmentVariables={"POGA_FEDERATION_CONFIG":
                                           str(self.h.old / "poga.local")})

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertEqual(report["remaining"][0]["field"],
                         "EnvironmentVariables[POGA_FEDERATION_CONFIG]")

    def test_a_log_path_inside_the_old_root_is_remaining(self):
        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/true"],
                     StandardErrorPath=str(self.h.old / "logs/err.log"))

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertEqual(report["remaining"][0]["field"], "StandardErrorPath")

    def test_an_unparseable_plist_is_a_gap_and_the_verdict_is_cannot_tell(self):
        """Unreadable is not absent — the whole point of the exit-2 lane."""
        (self.h.agents / "com.federation.broken.plist").write_text("this is not a plist")

        code, report = self.h.inspect()

        self.assertEqual(code, 2, "an unread source must never exit 0")
        self.assertEqual(report["verdict"], "cannot-tell")
        self.assertEqual(report["remaining"], [])
        self.assertEqual(self.gap_sources(report), ["launch_agents"])
        self.assertIn("UNKNOWN, not absent", report["gaps"][0]["why"])

    def test_a_missing_launch_agents_directory_is_not_an_empty_one(self):
        shutil.rmtree(self.h.agents)

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["launch_agents"])
        self.assertEqual(report["sources"]["launch_agents"]["status"], "unreadable")


class APathComparisonIsNotASubstringSearchTest(LegacyDepsCase):

    def test_a_sibling_directory_whose_name_extends_the_old_root_is_not_a_match(self):
        """`/a/bc` is not under `/a/b`, and a substring search says it is."""
        sibling = Path(str(self.h.old) + "-notes")
        sibling.mkdir()
        self.h.plist("com.federation.notes",
                     ProgramArguments=["/usr/bin/true", str(sibling / "run.py")],
                     WorkingDirectory=str(sibling))

        code, report = self.h.inspect()

        self.assertClean(report, code)
        self.assertIn(str(self.h.old), str(sibling),
                      "the fixture must actually contain the old root as a substring, "
                      "or this test is not exercising the trap it is named for")

    def test_a_case_differing_path_is_caught_where_an_exact_comparison_would_miss_it(self):
        """macOS APFS is case-insensitive: `.../Projects/f` and `.../projects/f` are one
        directory. WI-0392 is this repo's receipt that the difference reaches production."""
        lower = str(self.h.host / "projects" / "federation" / "curate/mailworker.py")

        # The contrast IS the finding: a component-boundary comparison that does not fold
        # case answers "no" to this exact string.
        self.assertFalse(legacydeps._is_under(os.path.normpath(lower), str(self.h.old)))

        self.h.plist("com.federation.leftover",
                     ProgramArguments=["/usr/bin/python3", lower])

        code, report = self.h.inspect()

        self.assertEqual(code, 1, "a case-differing spelling of the old root is the old root")
        self.assertEqual(report["remaining"][0]["matched"], lower)
        self.assertTrue(report["remaining"][0]["case_folded"],
                        "a fold-only match must say so — on a case-sensitive volume these "
                        "really would be two directories")

    def test_the_old_root_named_with_no_suffix_at_all_is_a_match(self):
        self.h.plist("com.federation.leftover", WorkingDirectory=str(self.h.old))

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertEqual(report["remaining"][0]["matched"], str(self.h.old))


class TheLoadedJobsAreReadByNamedFieldTest(LegacyDepsCase):

    def test_a_loaded_job_whose_working_directory_is_the_old_root_is_remaining(self):
        self.h.loaded("com.federation.deploy-sweep",
                      working_directory=str(self.h.old),
                      arguments=["/usr/bin/python3", str(self.h.old / "deploy/runner.py")],
                      config="/Users/nobody/federation-config.json")

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        fields = [hit["field"] for hit in report["remaining"]]
        self.assertEqual(fields, ["working directory", "arguments[1]"])
        self.assertTrue(all(hit["source"] == "launchd" for hit in report["remaining"]))
        self.assertIn("com.federation.deploy-sweep", report["remaining"][0]["where"])

    def test_the_inventory_is_enumerated_rather_than_asked_by_known_label(self):
        """A label nobody declared is exactly the one this tool exists to find."""
        self.h.loaded("com.federation.undeclared-legacy",
                      working_directory=str(self.h.old),
                      arguments=[str(self.h.old / "poga")])

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertIn("com.federation.undeclared-legacy",
                      report["sources"]["launchd"]["federation_labels"])

    def test_raw_launchctl_output_never_reaches_the_json(self):
        raw = self.h.loaded("com.federation.deploy-sweep",
                            working_directory=str(self.h.release),
                            arguments=["/usr/bin/python3",
                                       str(self.h.release / "deploy/runner.py")])

        code, report = self.h.inspect()

        self.assertEqual(code, 0)
        serialized = json.dumps(report)
        self.assertNotIn(SECRET, serialized,
                         "the loaded job's environment must never be stored")
        self.assertNotIn("active count", serialized)
        job = [item for item in report["sources"]["launchd"]["jobs"]
               if item["label"] == "com.federation.deploy-sweep"][0]
        self.assertEqual(job["raw_output_sha256"], legacydeps._sha(raw),
                         "the digest must be of the observation that was actually made")

    def test_a_listed_label_that_will_not_print_is_a_gap(self):
        self.h.listed_only("com.federation.ghost")

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["launchd"])
        self.assertIn("UNKNOWN", report["gaps"][0]["why"])

    def test_an_inventory_that_cannot_be_read_is_a_gap_not_an_empty_one(self):
        self.h.set_labels_rc(1)

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["launchd"])
        self.assertEqual(report["sources"]["launchd"]["status"], "unreadable")

    def test_an_inventory_in_an_unrecognised_format_is_not_zero_jobs(self):
        self.h.set_list_output("launchctl: a format nobody here has seen\n", code=0)

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertIn("unread inventory, not an empty one", report["gaps"][0]["why"])

    def test_a_loaded_record_the_shared_parser_rejects_is_a_gap(self):
        self.h.loaded("com.federation.mail-poller", working_directory="ignored",
                      arguments=[], raw="com.federation.mail-poller = {\n\tstate = running\n}\n")

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["launchd"])
        self.assertIn("not readable", report["gaps"][0]["why"])


class CronIsReadAndOnlyEverReadTest(LegacyDepsCase):

    def test_a_cron_line_naming_the_old_root_is_remaining(self):
        self.h.cron([f"0 * * * * {self.h.old}/poga sweep >> /tmp/sweep.log"])

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        found = report["remaining"][0]
        self.assertEqual(found["source"], "cron")
        self.assertEqual(found["matched"], f"{self.h.old}/poga")

    def test_no_crontab_for_this_user_is_clean_rather_than_a_gap(self):
        self.h.cron([], code=1, stderr="no crontab for nobody\n")

        code, report = self.h.inspect()

        self.assertClean(report, code)
        self.assertTrue(report["sources"]["cron"]["no_crontab"])

    def test_any_other_failure_is_a_gap(self):
        self.h.cron([], code=2, stderr="crontab: cannot open /var/at/tabs/nobody\n")

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["cron"])
        self.assertEqual(report["sources"]["cron"]["status"], "unreadable")

    def test_a_comment_is_not_a_cron_entry(self):
        self.h.cron([f"# retired: {self.h.old}/poga sweep", ""])

        code, report = self.h.inspect()

        self.assertClean(report, code)
        self.assertEqual(report["sources"]["cron"]["entries"], 0)

    def test_only_the_list_subcommand_is_ever_run(self):
        """`crontab` with the wrong argv REPLACES the user's crontab from stdin."""
        self.h.inspect()

        invocations = (self.h.trace / "crontab.log").read_text().split()
        self.assertEqual(set(invocations), {"-l"})


class ThePogaLinkTest(LegacyDepsCase):

    def test_a_link_resolving_into_the_old_checkout_is_remaining(self):
        self.h.point_poga_at(self.h.old / "poga")

        code, report = self.h.inspect()

        self.assertEqual(code, 1)
        self.assertEqual(len(report["remaining"]), 1,
                         "the link and its target are one fact, reported once")
        found = report["remaining"][0]
        self.assertEqual(found["source"], "poga")
        self.assertEqual(found["field"], "resolves_to")
        self.assertEqual(found["matched"], os.path.realpath(str(self.h.old / "poga")))

    def test_a_link_resolving_into_the_release_is_clean(self):
        code, report = self.h.inspect()

        self.assertClean(report, code)
        poga = report["sources"]["poga"]
        self.assertTrue(poga["is_symlink"])
        self.assertTrue(poga["target_exists"])
        self.assertTrue(poga["references_release"])

    def test_an_absent_link_is_a_gap_and_not_a_clean_answer(self):
        self.h.poga.unlink()

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(self.gap_sources(report), ["poga"])


class AGapOutranksAFindingTest(LegacyDepsCase):
    """`remaining` is only acceptance evidence when the list is COMPLETE."""

    def test_an_unreadable_source_beside_a_real_finding_still_reports_cannot_tell(self):
        self.h.plist("com.federation.leftover", WorkingDirectory=str(self.h.old))
        self.h.cron([], code=2, stderr="crontab: unreadable\n")

        code, report = self.h.inspect()

        self.assertEqual(code, 2)
        self.assertEqual(report["verdict"], "cannot-tell")
        self.assertTrue(report["remaining"], "the finding is still reported, not dropped")
        self.assertTrue(report["gaps"])


class TheProbeDoesNotWriteTest(LegacyDepsCase):
    """Reading the code cannot establish the absence of a write."""

    def _fingerprint(self):
        acc = {}
        for base in (self.h.host, self.h.fake):
            for root, dirs, files in os.walk(base, followlinks=False):
                dirs.sort()
                for name in sorted(files) + sorted(
                        d for d in dirs if os.path.islink(os.path.join(root, d))):
                    path = os.path.join(root, name)
                    key = os.path.relpath(path, self.h.tmp)
                    if os.path.islink(path):
                        acc[key] = "link:" + os.readlink(path)
                    else:
                        acc[key] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        return acc

    def test_an_inspection_changes_nothing_on_disk(self):
        before = self._fingerprint()
        code, report = self.h.inspect()
        self.assertEqual(code, 0)
        self.assertEqual(self._fingerprint(), before)
        self.assertTrue(report["read_only"])


class TheCommandLineRefusesWhatItCannotAnswerTest(LegacyDepsCase):

    def test_a_relative_old_root_is_refused_rather_than_guessed_at(self):
        code, out, err = self.h.run(**{"--old-root": "Projects/federation"})

        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("absolute", err)
        self.assertIn("not a clean result", err)

    def test_a_release_root_inside_the_old_root_is_refused(self):
        code, _, err = self.h.run(**{"--release-root": str(self.h.old / "deploy")})

        self.assertEqual(code, 2)
        self.assertIn("release root", err)

    def test_the_release_root_is_optional(self):
        code, report = self.h.inspect(**{"--release-root": None})

        self.assertEqual(code, 0)
        self.assertIsNone(report["release_root"])


if __name__ == "__main__":
    unittest.main()
