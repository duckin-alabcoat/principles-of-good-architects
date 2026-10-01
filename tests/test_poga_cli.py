"""ADR-0061 — the `poga` lifecycle CLI: bootstrap and restore on one code path.

What these tests are actually defending:

1. **The identity pin (D4 / drill-#1 finding 9).** The federation has a PUBLIC,
   sanitized sibling repo. A restore that rebuilt from it would produce a
   plausible-looking system silently missing whatever the sanitizer stripped — the
   worst failure mode available, because it looks like success. `test_pin_*` asserts
   the mismatch is refused, and that a clone THIS RUN created is removed while a
   pre-existing checkout is never touched.

2. **The honesty contract (D2).** `restore` must never report plain success while
   credentials are owed. The exit code is the machine-checkable form of that promise:
   3 = incomplete, 0 = genuinely complete. `test_exit_*` pins both directions, so a
   future change that makes restore exit 0 with credentials outstanding fails here
   rather than in a real disaster.

3. **Refusing to guess.** An old-shape (pre-ADR-0094) manifest, an unknown `dest`
   token, declared data that was never placed or came back wrong — each must stop
   with an explanation. A restore that guesses where data goes is worse than one that
   stops.

4. **Atomic-or-cleanup acquire (finding 7).** An interrupted clone must not leave a
   skeleton `.git` that poisons every retry.

Everything here is hermetic: repos are local `file://`-reachable git repos, so
`git ls-remote` and `git clone` work with no network and no GitHub auth.
"""

import contextlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import poga_cli  # noqa: E402

GIT = shutil.which("git")


def _git(repo, *args):
    subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True)


def make_repo(path: pathlib.Path, marker: str = "hello") -> str:
    """Create a real git repo and return its root-commit sha."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "marker.txt").write_text(marker, encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "root")
    out = subprocess.run([GIT, "-C", str(path), "rev-list", "--max-parents=0", "HEAD"],
                         capture_output=True, text=True)
    return out.stdout.strip()


class Base(unittest.TestCase):
    """Each test gets its own fake federation ROOT holding the manifests + spec."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.root = self.tmp / "fed"
        self.root.mkdir()
        self.origin = self.tmp / "origin"
        self.root_commit = make_repo(self.origin)
        self.patcher = mock.patch.object(poga_cli, "ROOT", self.root)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        # The CLI's whole job is printing a report; 29 of them would drown the
        # merge gate's output. Capture it — assertions are on exit codes and disk.
        self.out = io.StringIO()
        redirect = contextlib.redirect_stdout(self.out)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def write_spec(self, name="widget", **over):
        spec = {
            "system_name": "Widget",
            "user_id": "operator",
            "user_name": "operator",
            "repo_owner": "o",
            "repo_name": "widget",
            "repo_url": str(self.origin),
            "root_commit": self.root_commit,
            "install_dir": str(self.tmp / "install"),
            "federation_repo_url": "https://example.invalid/blob/main",
            "mission_prose": "x",
        }
        spec.update(over)
        p = self.root / f"bootstrap-spec.{name}.json"
        p.write_text(json.dumps(spec), encoding="utf-8")
        return p

    def write_manifests(self, name="widget", paths=None, credentials=None, state=None):
        """`paths` is the ADR-0094 declared data set; `state` replaces the whole state
        manifest, for the tests that need a shape the helper would never write."""
        (self.root / f"state-manifest.{name}.json").write_text(json.dumps(state or {
            "system": name,
            "version": "2.0.0",
            "spec": f"bootstrap-spec.{name}.json",
            "data": {"dest": "${SYSTEM_ROOT}", "paths": paths if paths is not None else []},
        }), encoding="utf-8")
        (self.root / f"credentials-manifest.{name}.json").write_text(json.dumps({
            "system": name,
            "version": "1.0.0",
            "credentials": credentials if credentials is not None else [],
        }), encoding="utf-8")

    def restore(self, name="widget", **kw):
        argv = ["restore", name]
        for k, v in kw.items():
            flag = "--" + k.replace("_", "-")
            argv += [flag] if v is True else [flag, str(v)]
        return poga_cli.main(argv)


# ---------------------------------------------------------------------------
# 1. the identity pin — the finding-9 guard
# ---------------------------------------------------------------------------


class TestIdentityPin(Base):

    def test_pin_matches_restore_proceeds(self):
        self.write_spec()
        self.write_manifests()
        rc = self.restore(into=self.tmp / "out")
        self.assertEqual(rc, poga_cli.EXIT_OK)
        self.assertTrue((self.tmp / "out" / "marker.txt").is_file())

    def test_pin_mismatch_refuses_and_removes_the_clone_it_made(self):
        """A wrong root commit means a DIFFERENT repository — most likely the public
        sanitized sibling. Refuse, and leave nothing behind that a retry could mistake
        for a good restore."""
        self.write_spec(root_commit="0" * 40)
        self.write_manifests()
        out = self.tmp / "out"
        rc = self.restore(into=out)
        self.assertEqual(rc, poga_cli.EXIT_FAIL)
        self.assertFalse(out.exists(), "the clone this run created must be removed")

    def test_pin_mismatch_never_deletes_a_preexisting_checkout(self):
        """The guard must not become a data-loss path: if the checkout was already
        there, we refuse but do NOT delete it — it is not ours to destroy."""
        out = self.tmp / "out"
        subprocess.run([GIT, "clone", "-q", str(self.origin), str(out)], check=True,
                       capture_output=True)
        (out / "precious.txt").write_text("do not delete", encoding="utf-8")
        self.write_spec(root_commit="0" * 40)
        self.write_manifests()
        rc = self.restore(into=out)
        self.assertEqual(rc, poga_cli.EXIT_FAIL)
        self.assertTrue((out / "precious.txt").is_file())

    def test_restore_without_a_pin_is_refused(self):
        """No `root_commit` means no identity check is possible. Refuse rather than
        build something unverifiable."""
        self.write_spec(root_commit=None)
        self.write_manifests()
        rc = self.restore(into=self.tmp / "out")
        self.assertEqual(rc, poga_cli.EXIT_FAIL)
        self.assertFalse((self.tmp / "out").exists())


# ---------------------------------------------------------------------------
# 2. the honesty contract — exit codes
# ---------------------------------------------------------------------------


class TestHonestyContract(Base):

    def test_exit_incomplete_when_credentials_are_owed(self):
        """D2, the whole point: data landed, but the system cannot authenticate. This
        is a CORRECT restore exit, and it is not 0."""
        self.write_spec()
        self.write_manifests(credentials=[
            {"name": "root-identity", "transport": "human-only", "provision": ["sign in"]},
            {"name": "gh", "transport": "human-only", "provision": ["gh auth login"]},
        ])
        rc = self.restore(into=self.tmp / "out")
        self.assertEqual(rc, poga_cli.EXIT_INCOMPLETE)

    def test_machine_injectable_credentials_do_not_count_toward_N(self):
        """Only human-only secrets are terms in `incomplete — N`; a fetch-at-use
        credential is not work owed to a human."""
        self.write_spec()
        self.write_manifests(credentials=[
            {"name": "token", "transport": "machine-injectable", "provision": ["fetch"]},
        ])
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_OK)

    def test_exit_ok_only_when_nothing_is_owed(self):
        self.write_spec()
        self.write_manifests()
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_OK)

    def test_blocked_data_leg_forces_incomplete_even_with_no_credentials(self):
        """A data leg that did not land must never be reported as a clean restore."""
        self.write_spec()
        self.write_manifests(paths=[{"path": "users/"}])   # declared, never placed
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_INCOMPLETE)

    def test_plan_never_mutates_and_always_exits_zero(self):
        """`--plan` is a read-only freshness check; it reports what WOULD be owed
        without doing anything, so it must not adopt the incomplete exit code."""
        self.write_spec()
        self.write_manifests(credentials=[
            {"name": "gh", "transport": "human-only", "provision": ["gh auth login"]}])
        out = self.tmp / "out"
        self.assertEqual(self.restore(into=out, plan=True), poga_cli.EXIT_OK)
        self.assertFalse(out.exists())


# ---------------------------------------------------------------------------
# 3. refusing to guess
# ---------------------------------------------------------------------------


class TestRefusesToGuess(Base):

    OLD_SHAPE = {
        "system": "widget", "version": "1.0.0", "spec": "bootstrap-spec.widget.json",
        "data_sources": [{
            "name": "snap", "paths": ["users/"], "dest": "${SYSTEM_ROOT}",
            "source": {"kind": "local-snapshot", "failure_domain": "local",
                       "automation": "scriptable", "coordinates": {"source_path": "/s"},
                       "retrieval": ["step"]}}],
    }

    def test_old_shape_manifest_is_refused_with_a_named_reason(self):
        """ADR-0094 is a breaking change. A 1.x manifest carries retrieval the project
        no longer owns, so reading it anyway would quietly undo the ADR. It is refused
        BEFORE anything is built, and the refusal names the key, the ADR and the way out."""
        self.write_spec()
        self.write_manifests(state=self.OLD_SHAPE)
        out = self.tmp / "out"
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_USAGE)
        self.assertFalse(out.exists(), "an old shape must be refused before the clone")
        with self.assertRaises(poga_cli.Abort) as caught:
            poga_cli.declared_data(self.OLD_SHAPE, "state-manifest.widget.json")
        msg = str(caught.exception)
        for named in ("state-manifest.widget.json", "data_sources", "ADR-0094",
                      "state-manifest.md"):
            self.assertIn(named, msg)

    def test_manifest_with_no_data_block_is_refused(self):
        """Neither shape is a guessable shape. A manifest that declares no `data` is not
        read as 'no data'; the empty-list claim has to be made explicitly."""
        self.write_spec()
        self.write_manifests(state={"system": "widget", "version": "2.0.0",
                                    "spec": "bootstrap-spec.widget.json"})
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_USAGE)
        self.assertIn("no `data` block", self.out.getvalue())

    def test_hydrate_reads_the_new_shape(self):
        self.write_spec()
        self.write_manifests(paths=[{"path": "a"}, {"path": "b/", "contains": ["c"]}])
        ctx = poga_cli.phase_resolve("restore", None, "widget", str(self.tmp / "out"),
                                     "machine-loss", True)
        poga_cli.phase_hydrate(ctx)
        [leg] = ctx.legs
        self.assertEqual(leg.paths, ["a", "b/"])
        self.assertEqual(leg.dest, ctx.target)
        self.assertEqual(leg.status, "pending", "--plan verifies nothing")

    def test_hydrate_never_retrieves(self):
        """The backup layer places the data and the project verifies it. Hydrate runs no
        process at all: there is no driver left to run."""
        self.write_spec()
        self.write_manifests(paths=[{"path": "users/"}])
        ctx = poga_cli.phase_resolve("restore", None, "widget", str(self.tmp / "out"),
                                     "site-loss", False)
        with mock.patch.object(poga_cli.subprocess, "run") as run:
            poga_cli.phase_hydrate(ctx)
        run.assert_not_called()
        [leg] = ctx.legs
        self.assertEqual(leg.status, "human-owed")
        self.assertIn("the backup layer", " ".join(leg.checklist))

    def _placed(self, files):
        """Seed the ORIGIN so the clone lands them, standing in for the backup layer.
        The target must not exist before restore, or it reads as a foreign checkout."""
        for rel, body in files.items():
            p = self.origin / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "placed")

    def test_placed_data_that_passes_its_checks_restores_clean(self):
        self._placed({"users/operator/profile.md": "x", "notes.md": "y"})
        self.write_spec()
        self.write_manifests(paths=[{"path": "users/", "contains": ["operator/profile.md"]},
                                    {"path": "notes.md"}])
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_OK)

    def test_present_but_not_correct_is_blocked(self):
        """Presence is not integrity: `users/` came back, but without the profile that
        makes it correct. That must not read as a clean restore."""
        self._placed({"users/someone-else/profile.md": "x"})
        self.write_spec()
        self.write_manifests(paths=[{"path": "users/", "contains": ["operator/profile.md"]}])
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_INCOMPLETE)
        self.assertIn("users/ lacks operator/profile.md", self.out.getvalue())

    def test_an_empty_placed_file_is_not_placed(self):
        self._placed({"notes.md": ""})
        self.write_spec()
        self.write_manifests(paths=[{"path": "notes.md"}])
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_INCOMPLETE)
        self.assertIn("not placed yet: notes.md", self.out.getvalue())


# ---------------------------------------------------------------------------
# the schema — ADR-0094 shape, checked by a real validator run
# ---------------------------------------------------------------------------


def _validate(value, schema, root, path="$"):
    """A stdlib validator for exactly the keywords state-manifest.schema.json uses, so
    the check runs on the pinned interpreter, which has no `jsonschema`. It returns every
    error, so nothing short-circuits. `test_schema_uses_only_keywords_the_validator_knows`
    is what keeps it honest: a new keyword in the schema fails that test instead of
    being silently ignored here."""
    import re as _re
    errs = []
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _validate(value, root["$defs"][name], root, path)
    if "not" in schema and not _validate(value, schema["not"], root, path):
        errs.append(f"{path}: matches a forbidden shape")
    types = {"object": dict, "array": list, "string": str}
    t = schema.get("type")
    if t and not isinstance(value, types[t]):
        return errs + [f"{path}: expected {t}"]
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errs.append(f"{path}: too short")
        if "pattern" in schema and not _re.search(schema["pattern"], value):
            errs.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errs.append(f"{path}: too few items")
        for i, item in enumerate(value):
            if "items" in schema:
                errs += _validate(item, schema["items"], root, f"{path}[{i}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errs.append(f"{path}: missing required {key!r}")
        props, patterns = schema.get("properties", {}), schema.get("patternProperties", {})
        for key, sub in value.items():
            if key in props:
                errs += _validate(sub, props[key], root, f"{path}.{key}")
                continue
            hits = [p for p in patterns if _re.search(p, key)]
            for p in hits:
                errs += _validate(sub, patterns[p], root, f"{path}.{key}")
            if not hits and schema.get("additionalProperties") is False:
                errs.append(f"{path}: {key!r} is not allowed")
    return errs


class TestSchema(unittest.TestCase):
    """The reference manifest was once published as 'validates against the schema' while
    a `//coordinates` key made it fail, and nothing ever ran the check. These run it."""

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    KNOWN = {"$schema", "$id", "$ref", "$defs", "title", "description", "examples", "type",
             "required", "properties", "patternProperties", "additionalProperties", "items",
             "minItems", "minLength", "pattern", "not"}

    FED = ROOT / "state-manifest.federation.json"
    # A manifest this class owns, so the schema's own refusals are checked in any tree,
    # including the public cut, which does not ship the federation's real manifest.
    SAMPLE = {
        "//": "fixture: a minimal 2.0.0 state manifest",
        "system": "widget",
        "version": "2.0.0",
        "spec": "bootstrap-spec.widget.json",
        "data": {"dest": "${WIDGET_ROOT}",
                 "paths": [{"path": "notes/", "contains": ["a.md"]}, {"path": "b.md"}]},
    }

    def setUp(self):
        self.schema = json.loads((self.ROOT / "state-manifest.schema.json").read_text("utf-8"))
        self.sample = json.loads(json.dumps(self.SAMPLE))

    def _fed(self):
        """The federation's real manifest. Internal only: the public cut withholds it,
        so the two tests about it skip there and run on the trunk."""
        if not self.FED.is_file():
            self.skipTest("state-manifest.federation.json is internal-only (not in this tree)")
        return json.loads(self.FED.read_text("utf-8"))

    def test_the_sample_validates(self):
        """The fixture the refusal tests below mutate must itself be valid, or a refusal
        they see could come from the fixture rather than from the mutation."""
        self.assertEqual(_validate(self.sample, self.schema, self.schema), [])

    def test_schema_uses_only_keywords_the_validator_knows(self):
        def walk(node, where):
            if not isinstance(node, dict):
                return
            for key, sub in node.items():
                if where in ("properties", "patternProperties", "$defs"):
                    walk(sub, "schema")          # keys here are names, not keywords
                    continue
                self.assertIn(key, self.KNOWN, f"unhandled schema keyword {key!r}")
                walk(sub, key)
        walk(self.schema, "schema")

    def test_federation_manifest_validates(self):
        self.assertEqual(_validate(self._fed(), self.schema, self.schema), [])

    def test_federation_manifest_names_no_backup_medium(self):
        """ADR-0094 section 2. Only the dated `//` quarantine blocks may still carry
        retrieval text, and no code reads them."""
        live = {k: v for k, v in self._fed().items() if not k.startswith("//")}
        text = json.dumps(live)
        for word in ("failure_domain", "retrieval", "coordinates", "local-snapshot"):
            self.assertNotIn(word, text)

    def test_old_shape_fails_the_schema_by_name(self):
        errs = _validate(TestRefusesToGuess.OLD_SHAPE, self.schema, self.schema)
        self.assertIn("$: 'data_sources' is not allowed", errs)
        self.assertIn("$: missing required 'data'", errs)

    def test_a_hardcoded_home_dest_fails(self):
        bad = self.sample
        bad["data"]["dest"] = "/Users/someone/repo"
        self.assertEqual(_validate(bad, self.schema, self.schema),
                         ["$.data.dest: matches a forbidden shape"])

    def test_retrieval_cannot_come_back_in_through_a_path_entry(self):
        bad = self.sample
        bad["data"]["paths"][0]["retrieval"] = ["mount the snapshot"]
        self.assertEqual(_validate(bad, self.schema, self.schema),
                         ["$.data.paths[0]: 'retrieval' is not allowed"])


class TestDestResolution(Base):

    def _ctx(self, system="widget"):
        self.write_spec(system)
        self.write_manifests(system)
        ctx = poga_cli.phase_resolve("restore", None, system, str(self.tmp / "out"),
                                     "machine-loss", True)
        return ctx

    def test_system_root_token_resolves(self):
        ctx = self._ctx()
        self.assertEqual(poga_cli.resolve_dest("${SYSTEM_ROOT}", ctx), ctx.target)

    def test_per_system_token_resolves(self):
        ctx = self._ctx()
        self.assertEqual(poga_cli.resolve_dest("${WIDGET_ROOT}", ctx), ctx.target)

    def test_relative_dest_lands_under_the_install_root(self):
        ctx = self._ctx()
        self.assertEqual(poga_cli.resolve_dest("data/x", ctx), ctx.target / "data/x")

    def test_unknown_token_is_refused_not_guessed(self):
        ctx = self._ctx()
        with self.assertRaises(poga_cli.Abort):
            poga_cli.resolve_dest("${SOMEONE_ELSES_ROOT}", ctx)


# ---------------------------------------------------------------------------
# 4. acquire — idempotent, resumable, atomic-or-cleanup
# ---------------------------------------------------------------------------


class TestAcquire(Base):

    def test_interrupted_clone_leaves_no_poisoned_target(self):
        """Finding 7, structurally: the checkout only ever appears at its final path as
        a COMPLETE clone, so a failed clone cannot poison the retry."""
        self.write_spec(repo_url=str(self.tmp / "does-not-exist"))
        self.write_manifests()
        out = self.tmp / "out"
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_FAIL)
        self.assertFalse(out.exists())
        self.assertEqual(list(self.tmp.glob("out.poga-partial-*")), [],
                         "the staging dir must be cleaned up")

    def test_skeleton_checkout_is_recognised_and_cleared(self):
        """An interrupted clone from BEFORE this fix (or from any other tool) leaves a
        `.git` with no HEAD. Recognise it and clear it, rather than dying forever on
        'already exists'."""
        out = self.tmp / "out"
        (out / ".git").mkdir(parents=True)
        self.assertEqual(poga_cli.checkout_state(out, str(self.origin)), "skeleton")
        self.write_spec()
        self.write_manifests()
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_OK)
        self.assertTrue((out / "marker.txt").is_file())

    def test_foreign_directory_is_never_touched(self):
        """A directory that is not our repo is someone else's data. Refuse."""
        other = self.tmp / "other"
        make_repo(other, marker="someone else")
        out = self.tmp / "out"
        subprocess.run([GIT, "clone", "-q", str(other), str(out)], check=True,
                       capture_output=True)
        self.assertEqual(poga_cli.checkout_state(out, str(self.origin)), "foreign")
        self.write_spec()
        self.write_manifests()
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_FAIL)
        self.assertEqual((out / "marker.txt").read_text(encoding="utf-8"), "someone else")

    def test_rerun_reuses_the_existing_checkout(self):
        self.write_spec()
        self.write_manifests()
        out = self.tmp / "out"
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_OK)
        self.assertEqual(self.restore(into=out), poga_cli.EXIT_OK)

    def test_normalize_url_ignores_dotgit_and_case(self):
        self.assertEqual(poga_cli.normalize_url("https://x/a/B.git"),
                         poga_cli.normalize_url("https://x/a/b/"))

    def test_slug_from_url(self):
        self.assertEqual(poga_cli.slug_from_url("https://github.com/o/r.git"), "o/r")


# ---------------------------------------------------------------------------
# 5. the shared path — what makes restore trustworthy
# ---------------------------------------------------------------------------


class TestSharedPath(Base):

    def test_restore_only_spec_refuses_bootstrap(self):
        """A system whose files are AUTHORED (the federation itself) must never have
        the kit rendered over them."""
        spec = self.write_spec(bootstrappable=False)
        self.assertEqual(poga_cli.main(["bootstrap", "--spec", str(spec), "--plan"]),
                         poga_cli.EXIT_USAGE)

    def test_manifest_system_must_match_the_spec_identity(self):
        """The manifest and the build input are one connected artifact set; a drift
        between them is caught before anything is built."""
        self.write_spec("widget", system_name="Gadget")
        self.write_manifests("widget")
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_FAIL)

    def test_missing_manifest_is_a_usage_error_not_a_crash(self):
        self.assertEqual(self.restore("nosuch", into=self.tmp / "out"), poga_cli.EXIT_USAGE)

    def test_both_verbs_run_the_same_phase_sequence(self):
        """The D1 claim, asserted rather than asserted-in-prose: bootstrap and restore
        call the SAME phases in the SAME order. If someone forks the pipeline, this
        fails."""
        import inspect
        src = inspect.getsource(poga_cli.execute)
        order = [p for p in ("phase_resolve", "phase_identity", "phase_acquire",
                             "phase_materialize", "phase_hydrate", "phase_verify",
                             "phase_report") if p in src]
        self.assertEqual(order, ["phase_resolve", "phase_identity", "phase_acquire",
                                 "phase_materialize", "phase_hydrate", "phase_verify",
                                 "phase_report"])
        self.assertEqual(src.count("if verb"), 0,
                         "execute() must not branch per-verb — the phases do")

    def test_empty_data_sources_is_a_positive_claim(self):
        """An empty `data.paths` asserts 'this system has no non-derivable state'. It
        must restore clean, not warn. (The name predates ADR-0094's `data` block.)"""
        self.write_spec()
        self.write_manifests(paths=[])
        self.assertEqual(self.restore(into=self.tmp / "out"), poga_cli.EXIT_OK)


# ---------------------------------------------------------------------------
# 6. ADR-0065 — the trust boundary
# ---------------------------------------------------------------------------


class TestTrustBoundary(Base):
    """Restore hydrates an inbox from unverifiable backup media; `apply-briefs` is a
    FLOOR startup hook that applies conforming briefs before the model wakes. Without
    a gate, a restore is a delivery vehicle for an unreviewed role-doc edit at the one
    moment nobody is inspecting anything. The gate is the version check (see
    TestVersionCheckIsTheGate), not a human hold."""

    def setUp(self):
        super().setUp()
        # give the origin repo an inbox declaration, as every real member has
        _git(self.origin, "config", "user.email", "t@example.com")
        _git(self.origin, "config", "user.name", "T")
        _git(self.origin, "config", "commit.gpgsign", "false")
        (self.origin / "session.config.json").write_text(
            json.dumps({"inbox": "proposed-edits/widget-arch/pending"}), encoding="utf-8")
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "config")

    def _blocked_source(self):
        # declared and never placed, so the data leg is owed and the run incomplete
        return [{"path": "users/"}]

    def test_restore_leaves_no_hold_on_the_inbox(self):
        """ADR-0065 D3, after the operator's rejection of the held-inbox draft: a restore must
        require no human step to reach a working system. The gate is the version check,
        performed by code every time — not a marker somebody has to remember to clear."""
        self.write_spec()
        self.write_manifests(paths=self._blocked_source())
        out = self.tmp / "out"
        self.restore(into=out)
        inbox = out / "proposed-edits/widget-arch/pending"
        leftovers = list(inbox.glob(".poga*")) if inbox.is_dir() else []
        self.assertEqual(leftovers, [], "restore must not leave a hold marker behind")

    def test_hydrated_briefs_are_counted_and_reported(self):
        """They are not held, but the operator is told they are there (D4)."""
        self.write_spec()
        self.write_manifests(paths=self._blocked_source())
        # seed the brief in the ORIGIN so the clone materialises it — the target dir
        # must not exist before restore, or it reads as a foreign checkout
        seeded = self.origin / "proposed-edits/widget-arch/pending"
        seeded.mkdir(parents=True)
        (seeded / "a-brief.md").write_text("---\napply: auto\n---\n", encoding="utf-8")
        _git(self.origin, "add", "-A")
        _git(self.origin, "commit", "-q", "-m", "brief")
        self.restore(into=self.tmp / "out")
        self.assertIn("1 brief(s) came off backup media", self.out.getvalue())

    def test_restore_activates_nothing(self):
        """D1: restore lays down files and exits. It must not run restored code or
        install hooks. Asserted by proving no subprocess is ever launched from the
        restored tree after the clone."""
        self.write_spec()
        self.write_manifests()
        out = self.tmp / "out"
        self.restore(into=out)
        # nothing from the restored tree may have been executed or wired
        self.assertFalse((out / ".session-state").exists())
        self.assertFalse((out / "sessions" / "journal").exists())


class TestVersionCheckIsTheGate(unittest.TestCase):
    """The other half of the boundary, in the shipped harness.

    ADR-0065 D3 rests entirely on this: an unverified brief is gated by comparing its
    `expected-base-version` against the role doc, which a restore has already
    identity-verified via the root-commit pin. If this check ever stops refusing a
    stale brief, the restore path silently loses its only gate — so it is pinned here
    as well as in test_apply.py, with the restore rationale attached.
    """

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
        import session
        self.session = session
        self.pending = self.tmp / "pending"
        self.pending.mkdir(parents=True)

    def _brief(self, expects: str) -> pathlib.Path:
        p = self.pending / "e-1.md"
        p.write_text(
            "---\n"
            "edit-id: e-1\n"
            "target-file: role.md\n"
            f"expected-base-version: {expects}\n"
            "proposed-new-version: 9.9.9\n"
            "apply: auto\n"
            "---\n", encoding="utf-8")
        return p

    def _role_doc_at(self, version: str) -> None:
        (self.tmp / "role.md").write_text(f"**Version:** {version}\n", encoding="utf-8")

    def _evaluate(self, brief):
        with mock.patch.object(self.session, "ROOT", self.tmp):
            with contextlib.redirect_stdout(io.StringIO()):
                return self.session.evaluate_brief(brief)

    def test_stale_brief_from_a_backup_is_refused(self):
        """The realistic restore failure: a brief already applied before the backup was
        taken. Applying it bumped the role doc, so the version it expects no longer
        matches — and it is refused with no human involved."""
        self._role_doc_at("2.0.0")          # role doc from git: already moved on
        plan = self._evaluate(self._brief("1.0.0"))
        self.assertEqual(plan.action, "surface")
        self.assertIn("version drift", plan.reason)

    def test_matching_brief_still_applies(self):
        """The gate must not become a blanket brake: a legitimately in-flight brief
        whose base matches is still auto-appliable."""
        self._role_doc_at("1.0.0")
        plan = self._evaluate(self._brief("1.0.0"))
        self.assertNotIn("version drift", plan.reason)

    def test_no_hold_mechanism_remains(self):
        """operator rejected the held inbox; make sure it does not creep back."""
        self.assertFalse(hasattr(self.session, "QUARANTINE_MARKER"))
        self.assertFalse(hasattr(self.session, "inbox_quarantined"))


class TestDiscovery(Base):
    """`poga restore` restores ONE system. Discovery is what makes that usable on a
    fresh machine — and what makes the un-restorable systems visible BEFORE a disaster
    rather than during one."""

    def _portfolio(self, ids):
        rows = "\n".join(f"| x | y | z | `{i}` | `{i}-arch` | Active |" for i in ids)
        (self.root / "portfolio.md").write_text(
            "| System | Agent | Architect | System ID | Architect ID | Status |\n" + rows,
            encoding="utf-8")

    def test_bare_restore_lists_instead_of_erroring(self):
        """On a fresh machine, 'the following arguments are required' is the least
        useful thing the tool could say."""
        self.write_spec()
        self.write_manifests()
        self._portfolio(["widget"])
        self.assertEqual(poga_cli.main(["restore"]), poga_cli.EXIT_OK)
        self.assertIn("widget", self.out.getvalue())

    def test_list_flags_a_system_missing_its_manifests(self):
        self.write_spec()
        self.write_manifests()
        self._portfolio(["widget", "gadget"])
        poga_cli.main(["restore", "--list"])
        text = self.out.getvalue()
        self.assertIn("NOT restorable", text)
        self.assertIn("gadget", text)
        self.assertIn("total data loss", text)

    def test_list_marks_an_incomplete_trio(self):
        """A state manifest alone is not enough — without its spec and credentials
        manifest the system cannot actually be rebuilt, and must not read as ready."""
        self.write_manifests()          # state + creds, but no spec file written
        self._portfolio(["widget"])
        poga_cli.main(["restore", "--list"])
        self.assertIn("INCOMPLETE", self.out.getvalue())

    def test_portfolio_parser_ignores_other_tables(self):
        """portfolio.md carries disposition tables too; only the system/architect id
        column pair identifies a roster system."""
        (self.root / "portfolio.md").write_text(
            "| a | b | c | `real` | `real-arch` | Active |\n"
            "| `~/old-architect-dir/` | Folded | Example Architect | 2026-07-14 | x |\n",
            encoding="utf-8")
        self.assertEqual(poga_cli.portfolio_system_ids(), ["real"])

    def test_schema_file_is_not_mistaken_for_a_system(self):
        (self.root / "state-manifest.schema.json").write_text("{}", encoding="utf-8")
        self.assertNotIn("schema", poga_cli.restorable_systems())


# ---------------------------------------------------------------------------
# adopt/plan parity — 2026-07-24 consultant brief
#
# `poga bootstrap` used to carry less than `bootstrap.py`, the CLI it calls itself a
# legacy alias for: `--adopt` and `--create-remote` were unreachable, and the
# plan-only flag had two different names on the two surfaces. These exercise
# `poga_cli.py`'s own argv handling end-to-end against the REAL kit (like
# AdoptPreflightTest in test_bootstrap_adopt.py) rather than the fake fed root the
# other classes in this file use — adopt's engine lives in bootstrap.py and reads
# the real bootstrap-kit/, so faking ROOT here would test nothing real.
# ---------------------------------------------------------------------------

REAL_ROOT = pathlib.Path(__file__).resolve().parent.parent


class BootstrapAdoptParityTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = pathlib.Path(self._tmp.name) / "sys"
        self.target.mkdir()
        _git(self.target, "init", "-q", "-b", "main")
        _git(self.target, "config", "user.email", "t@t")
        _git(self.target, "config", "user.name", "t")
        _git(self.target, "config", "commit.gpgsign", "false")
        (self.target / "server.js").write_text("// app\n", encoding="utf-8")
        _git(self.target, "add", "-A")
        _git(self.target, "commit", "-qm", "the system, already built")

        # A throwaway profile under the REAL federation root — `users/` is gitignored
        # data and absent from a poga worktree lane, so this must not depend on the
        # real operator profile being present (same approach as AdoptPreflightTest).
        # PER-PROCESS id (WI-0054): `users/` is a symlink to the main checkout in every
        # poga lane, so a fixed name lets two concurrent lanes rmtree each other's
        # fixture mid-run — the flake that randomly blocked fleet pushes.
        self.user_id = f"_test_poga_adopt_user_{os.getpid()}"
        self.profile_dir = REAL_ROOT / "users" / self.user_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.profile_dir, ignore_errors=True)
        (self.profile_dir / "profile.md").write_text("# test profile\n", encoding="utf-8")

        self.spec_path = pathlib.Path(self._tmp.name) / "spec.json"
        self.spec_path.write_text(json.dumps({
            "system_name": "Test Sys", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main",
            "mission_prose": "Keep the thing running.",
        }), encoding="utf-8")

    def _run(self, *extra):
        return subprocess.run(
            [sys.executable, str(REAL_ROOT / "poga_cli.py"), "bootstrap",
             "--spec", str(self.spec_path), "--adopt", str(self.target), *extra],
            capture_output=True, text=True, cwd=str(REAL_ROOT),
        )

    def test_adopt_reachable_from_poga_bootstrap(self):
        """The gap the brief named: --adopt was invisible from `poga bootstrap`."""
        before = sorted(p.name for p in self.target.iterdir())
        proc = self._run("--plan")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("adopt-in-place", proc.stdout)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), before)

    def test_dry_run_alias_still_works_through_poga(self):
        proc = self._run("--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("adopt-in-place", proc.stdout)

    def test_create_remote_flag_reaches_the_engine(self):
        without = self._run("--plan")
        self.assertIn("LEFT AS-IS", without.stdout)
        with_flag = self._run("--plan", "--create-remote")
        self.assertIn("will create", with_flag.stdout)

    def test_refusal_surfaces_through_poga_too(self):
        """Reachability, not re-verification of adopt's own correctness (already
        covered by test_bootstrap_adopt.py) — a refusal must still surface, not get
        swallowed by the extra dispatch layer in poga_cli.main()."""
        (self.target / "wip.txt").write_text("half done\n", encoding="utf-8")
        proc = self._run("--plan")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("uncommitted", proc.stderr)


class BootstrapPlanAliasTest(unittest.TestCase):
    """`--plan` (poga's spelling) and `--dry-run` (bootstrap.py's) must set the same
    thing on `poga bootstrap` — the reconciliation half of the parity brief."""

    def test_plan_and_dry_run_are_the_same_dest(self):
        p = poga_cli.build_parser()
        self.assertTrue(p.parse_args(["bootstrap", "--spec", "x", "--plan"]).plan)
        self.assertTrue(p.parse_args(["bootstrap", "--spec", "x", "--dry-run"]).plan)
        self.assertFalse(p.parse_args(["bootstrap", "--spec", "x"]).plan)


class BootstrapAdoptDefaultsTest(unittest.TestCase):
    """The 2026-07-24 UX request (operator): `poga bootstrap --adopt` with no PATH and no
    `--spec` should default to 'wherever the user is standing' and 'the spec sitting
    right there.' Also proves the `POGA_INVOKED_FROM` fix — without it, a bare/relative
    `--adopt` would silently resolve against the federation ROOT (where the `poga`
    wrapper has already `cd`'d before exec'ing `poga_cli.py`), not the project the
    user actually typed `poga` from."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = pathlib.Path(self._tmp.name) / "sys"
        self.target.mkdir()
        (self.target / "app.py").write_text("# the system\n", encoding="utf-8")

        self.user_id = f"_test_poga_cli_adopt_defaults_user_{os.getpid()}"   # WI-0054
        self.profile_dir = REAL_ROOT / "users" / self.user_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.profile_dir, ignore_errors=True)
        (self.profile_dir / "profile.md").write_text("# test profile\n", encoding="utf-8")

        (self.target / "bootstrap-spec.json").write_text(json.dumps({
            "system_name": "Test Sys", "user_id": self.user_id, "user_name": "operator",
            "federation_repo_url": "https://example/blob/main",
            "mission_prose": "Keep the thing running.",
        }), encoding="utf-8")

    def test_bare_adopt_and_missing_spec_default_from_actual_cwd(self):
        """Direct invocation (no wrapper): plain cwd already IS the invocation
        directory, so bare --adopt + no --spec should just work from inside it.

        Must clear POGA_INVOKED_FROM from the child's environment — see the sibling
        test right below this one for why it's set at all (the real `poga` wrapper
        flow); `subprocess.run` inherits the parent's environment by default, so
        running this suite from inside an actual poga-launched lane (where the var
        is legitimately set for the whole session) would leak it into the child and
        silently test the wrapped case instead of the bare one this test targets."""
        env = {k: v for k, v in os.environ.items() if k != "POGA_INVOKED_FROM"}
        proc = subprocess.run(
            [sys.executable, str(REAL_ROOT / "poga_cli.py"), "bootstrap",
             "--adopt", "--plan"],
            capture_output=True, text=True, cwd=str(self.target), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(self.target.resolve()), proc.stdout)

    def test_poga_invoked_from_wins_over_process_cwd(self):
        """Simulates the `poga` wrapper: the PROCESS cwd is the federation ROOT
        (where the wrapper `cd`s before exec'ing this), but POGA_INVOKED_FROM carries
        the real directory the user typed `poga` from. Without honoring it, a bare
        --adopt would silently try to adopt the federation repo itself."""
        env = dict(os.environ, POGA_INVOKED_FROM=str(self.target))
        proc = subprocess.run(
            [sys.executable, str(REAL_ROOT / "poga_cli.py"), "bootstrap",
             "--adopt", "--plan"],
            capture_output=True, text=True, cwd=str(REAL_ROOT), env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(self.target.resolve()), proc.stdout)

    def test_missing_spec_notifies_clearly_instead_of_guessing(self):
        (self.target / "bootstrap-spec.json").unlink()
        proc = subprocess.run(
            [sys.executable, str(REAL_ROOT / "poga_cli.py"), "bootstrap",
             "--adopt", str(self.target), "--plan"],
            capture_output=True, text=True, cwd=str(REAL_ROOT))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("no --spec given and no bootstrap-spec.json found", proc.stderr)


class BootstrapFindsItsOwnFolderTest(unittest.TestCase):
    """`poga bootstrap` standing in the project folder, with NO flags (session ~157).

    operator tried four commands in a row against `~/projects/example-app` — a folder holding
    a build brief and a bootstrap spec — and got four refusals, each asking for
    information the situation already contained:

        poga bootstrap                          -> "bootstrap needs --spec <spec.json>"
        poga bootstrap --spec bootstrap-spec.json
                                                -> "not found at <federation-root>/bootstrap-spec.json"
        poga bootstrap --spec /abs/path/spec.json
                                                -> "spec must provide either `install_dir` or ..."
        (and beyond that, `phase_acquire` would have called the folder foreign)

    The request: bootstrap should work in the folder it is run in, find the files it
    needs there without being told, and ask for nothing else. Each test below is one
    of those commands.

    Everything here runs `--plan`, so nothing is created and no remote is touched.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.target = pathlib.Path(self._tmp.name) / "example-app"
        self.target.mkdir()
        # The inputs a spec is authored FROM — present before any substrate exists.
        (self.target / "2026-08-18-brief.md").write_text("# brief\n", encoding="utf-8")

        self.user_id = f"_test_poga_cli_folder_user_{os.getpid()}"           # WI-0054
        self.profile_dir = REAL_ROOT / "users" / self.user_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.profile_dir, ignore_errors=True)
        (self.profile_dir / "profile.md").write_text("# test profile\n", encoding="utf-8")

        # Deliberately declares NO install_dir and NO projects_dir — the observed shape.
        # `friendly_folder` alone used to be a hard error.
        (self.target / "bootstrap-spec.json").write_text(json.dumps({
            "system_name": "Example App", "friendly_folder": "example-app",
            "user_id": self.user_id, "user_name": "operator",
            "repo_owner": "o", "repo_name": "example-app",
            "federation_repo_url": "https://example/blob/main",
            "mission_prose": "Build the thing the brief describes.",
        }), encoding="utf-8")

    def _run(self, *args):
        """Run as the `poga` wrapper does: process cwd is the federation checkout,
        POGA_INVOKED_FROM carries where the user actually stood."""
        env = dict(os.environ, POGA_INVOKED_FROM=str(self.target))
        return subprocess.run(
            [sys.executable, str(REAL_ROOT / "poga_cli.py"), "bootstrap", *args, "--plan"],
            capture_output=True, text=True, cwd=str(REAL_ROOT), env=env)

    def test_bare_bootstrap_finds_the_spec_in_the_folder(self):
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bootstrap-spec.json", proc.stdout)

    def test_relative_spec_resolves_against_the_folder_not_the_federation_root(self):
        """The failure that named a path operator never typed. `bootstrap-spec.json` is
        also absent from the federation root, so a regression here fails loudly."""
        proc = self._run("--spec", "bootstrap-spec.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn(str(REAL_ROOT / "bootstrap-spec.json"), proc.stdout + proc.stderr)

    def test_absolute_spec_with_no_declared_location_installs_beside_the_spec(self):
        """`friendly_folder` with no `projects_dir` used to abort. The spec's own
        folder is the answer — asserted as the resolved install path, not merely as
        exit 0, so an install that silently landed elsewhere still fails."""
        proc = self._run("--spec", str(self.target / "bootstrap-spec.json"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(str(self.target.resolve()), proc.stdout)

    def test_a_folder_with_files_but_no_history_gets_the_full_kit(self):
        """The heart of it (the operator's ruling: a folder with no history has nothing to adopt). A brief sitting in
        the folder must not downgrade a brand-new system to the adopt plan, which
        omits STATUS.md — the ADR-0021 surface `curate/reconcile.py` reads."""
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("full kit", proc.stdout)
        self.assertNotIn("adopt-in-place", proc.stdout)

    def test_an_existing_repo_is_still_treated_as_an_adopt(self):
        """The other direction: an adopted-in-place member must not start getting our README/STATUS/adr
        layout imposed on it. Same folder, now with real history."""
        for args in (["init", "-q", "-b", "main"], ["add", "-A"]):
            subprocess.run([GIT, "-C", str(self.target), *args], check=True)
        subprocess.run([GIT, "-C", str(self.target), "-c", "user.email=t@e.invalid",
                        "-c", "user.name=T", "-c", "commit.gpgsign=false",
                        "commit", "-q", "-m", "root"], check=True)
        proc = self._run()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("adopt", proc.stdout)

    def test_no_spec_anywhere_names_the_folder_it_looked_in(self):
        """A refusal is fine; a refusal that hides where it looked is what made the
        original session take four tries."""
        (self.target / "bootstrap-spec.json").unlink()
        proc = self._run()
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + proc.stderr
        self.assertIn(str(self.target), combined)
        # WI-0158: this pinned `poga intake`, a verb poga does not have — the test was
        # holding the defect in place. The named remedy has to be the one that runs.
        self.assertIn("poga new", combined)
        self.assertNotIn("poga intake", combined)


if __name__ == "__main__":
    unittest.main()
