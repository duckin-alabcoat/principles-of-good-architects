"""Two released identities exchange real mail through actual unattended entrypoints.

Every module is copied intact into fixture Git releases; no delivery/publisher function
is mocked. Both hosts, their mailboxes/configuration, and all remotes are temporary.
This rehearses release/state independence, not installation or live promotion.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FederationMailReleaseE2E(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="federation-mail-release-")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("POGA_", "GIT_", "CLAUDE_"))
                    and key not in ("PYTHONPATH", "PYTHONPYCACHEPREFIX")}
        self.env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
                         "GIT_CONFIG_COUNT": "3", "GIT_CONFIG_KEY_0": "gc.auto",
                         "GIT_CONFIG_VALUE_0": "0", "GIT_CONFIG_KEY_1": "commit.gpgsign",
                         "GIT_CONFIG_VALUE_1": "false", "GIT_CONFIG_KEY_2": "maintenance.auto",
                         "GIT_CONFIG_VALUE_2": "false", "PYTHONDONTWRITEBYTECODE": "1"})
        self.remote = self.base / "mail.git"
        self.remote.mkdir()
        self.git(self.remote, "init", "--bare", "-q", "-b", "main")
        self.seed = self.base / "release-source"
        self.seed.mkdir()
        self.git(self.seed, "init", "-q", "-b", "main")
        sources = [ROOT / "session.py", ROOT / "interpreter.py"]
        sources += list((ROOT / "curate").glob("*.py"))
        sources += list((ROOT / "sessionlib").rglob("*.py"))
        for src in sources:
            dest = self.seed / src.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        (self.seed / "session.config.json").write_text(json.dumps({
            "architect_id": "federation-arch", "role_doc": "federation-arch.md",
            "timezone": "UTC", "machine_map": {}, "interpreter": sys.executable,
        }))
        (self.seed / "federation-arch.md").write_text("# Fixture federation\nVersion: 1.0.0\n")
        (self.seed / "release-id.txt").write_text("first fixture release\n")
        self.git(self.seed, "add", "--all")
        self.git(self.seed, "commit", "-qm", "first fixture release")
        self.git(self.seed, "tag", "v1.0.0")
        (self.seed / "release-id.txt").write_text("second fixture release\n")
        self.git(self.seed, "add", "release-id.txt")
        self.git(self.seed, "commit", "-qm", "second fixture release")
        self.git(self.seed, "tag", "v1.0.1")
        self.program_snapshots = {}
        self.dev = self.host("dev", authoritative=True)
        self.runner = self.host("runner", authoritative=False)
        self.select_release(self.dev, "v1.0.0")
        self.select_release(self.runner, "v1.0.0")

    def git(self, directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.com", "-c", "commit.gpgsign=false",
             "-c", "gc.auto=0", "-c", "maintenance.auto=false", *args],
            env=self.env, capture_output=True, check=True).stdout

    @staticmethod
    def files(directory):
        if not directory.exists():
            return {}
        return {str(path.relative_to(directory)): path.read_bytes()
                for path in directory.rglob("*") if path.is_file()
                and ".git" not in path.relative_to(directory).parts}

    def host(self, name, authoritative):
        home = self.base / name
        state, config = home / "state", home / "config"
        inbox = home / "authority" / "pending"
        for path in (state, config, inbox):
            path.mkdir(parents=True)
        member = home / "member"
        member_inbox = member / "proposed-edits" / "runner-test-arch" / "pending"
        member_inbox.mkdir(parents=True)
        (member / "STATUS.md").write_text("---\nid: runner-test\nversion: 1.0.0\n---\n")
        (member / "runner-test-arch.md").write_text("# Fixture recipient\nVersion: 1.0.0\n")
        (member / "session.config.json").write_text(json.dumps({
            "architect_id": "runner-test-arch", "role_doc": "runner-test-arch.md"}))
        roster = {"federation": {"architect_id": "federation-arch", "tracked": False,
                                  "reachable": True, "mailbox": "proposed-edits/federation-arch/pending"},
                  "runner-test": {"architect_id": "runner-test-arch", "tracked": False,
                                    "reachable": True, "mailbox": "proposed-edits/runner-test-arch/pending"}}
        (config / "mailboxes.json").write_text(json.dumps({"members": roster}))
        (config / "reconcile-roots.local").write_text("")
        (config / "repo-paths.local").write_text("" if authoritative else "runner-test = " + str(member) + "\n")
        peer = "runner" if authoritative else "dev"
        doc = {"schema_version": 1, "state_root": str(state), "config_root": str(config),
               "transport_root": str(home / "transport.git"),
               "inbox_root": str(inbox) if authoritative else None,
               # NOT `<host>/mail`: that spelling collides with `curate/channel.py`'s
               # branch, so the migration now corrects it (WI-0418) and this fixture would
               # be rewritten mid-run.
               "transport": {"remote": str(self.remote),
                             "owned_ref": "refs/heads/" + name + "/messages",
                             "input_refs": ["refs/heads/" + peer + "/messages"],
                             "recipient_refs": {("runner-test-arch" if authoritative else "federation-arch"):
                                                "refs/heads/" + peer + "/messages"}}}
        return {"name": name, "root": home, "state": state, "config": config,
                "inbox": inbox, "member_inbox": member_inbox, "doc": doc,
                "service": home / "service.json"}

    def select_release(self, host, tag):
        code = host["root"] / "releases" / tag
        if not code.exists():
            code.parent.mkdir(exist_ok=True)
            self.git(code.parent, "clone", "--no-hardlinks", "--quiet", str(self.seed), tag)
            self.git(code, "checkout", "--quiet", "--detach", tag)
            self.program_snapshots[code] = (self.git(code, "rev-parse", "HEAD"), self.files(code))
        host["code"] = code
        host["doc"]["code_root"] = str(code)
        host["service"].write_text(json.dumps(host["doc"]))

    def child(self, host, args, *, payload=None, cli_config=False):
        env = dict(self.env)
        if not cli_config:
            env["POGA_FEDERATION_CONFIG"] = str(host["service"])
        return subprocess.run([sys.executable, "-B", *args], input=payload,
                              cwd=self.base, env=env, text=True, capture_output=True, timeout=30)

    def enqueue(self, host, name, destination):
        text = ("---\nedit-id: 2026-09-17-" + name + "\nfrom: fixture-arch\nto: " + destination +
                "\napply: manual\nmanual-reason: attended\n---\n\nHarmless fixture " + name + ".\n\n")
        script = ("import json,sys\nfrom pathlib import Path\n"
                  "code=Path(sys.argv[1]);sys.path.insert(0,str(code/'curate'))\n"
                  "import production,mailqueue\nroots=production.resolve(code)\n"
                  "data=json.load(sys.stdin)\nmailqueue.enqueue(roots,**data)\n")
        proc = self.child(host, ["-c", script, str(host["code"])], payload=json.dumps({
            "destination": destination, "filename": name + ".md", "text": text,
            "message_id": name, "provenance": {"sender": host["name"]}}))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return text.encode()

    def cycle(self, host, *, poller=False, allow_failure=False):
        program = host["code"] / "curate" / ("mail-poller.py" if poller else "mailworker.py")
        args = [str(program), "--quiet"] if poller else [str(program), "--config", str(host["service"]), "--json"]
        proc = self.child(host, args, cli_config=not poller)
        health_file = host["state"] / "runtime" / "mail-worker.health.json"
        self.assertTrue(health_file.exists(), "entrypoint returned without running the production worker: " + proc.stderr)
        result = json.loads(health_file.read_text())
        self.assertEqual(result["release"], self.git(host["code"], "rev-parse", "HEAD").decode().strip())
        if not allow_failure:
            self.assertEqual(proc.returncode, 0, (proc.stdout, proc.stderr, result))
            self.assertEqual(result["status"], "ok", result)
        return result

    def settle(self):
        # Initial missing peer ref is honestly unavailable; the first peer publication
        # establishes it. Later cycles must complete using only the persistent queues.
        self.cycle(self.dev, allow_failure=True)
        self.cycle(self.runner, poller=True)
        self.cycle(self.dev)
        self.cycle(self.runner, poller=True)

    def lifecycle(self, host, name):
        key = hashlib.sha256(name.encode()).hexdigest()
        path = host["state"] / "message-state" / (key + ".json")
        return json.loads(path.read_text()) if path.exists() else {"state": "queued"}

    def assert_delivered(self, host, name, payload, inbox):
        state = self.lifecycle(host, name)
        self.assertEqual(state["state"], "delivered", state)
        self.assertEqual(state["acknowledgment"]["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual((inbox / (name + ".md")).read_bytes(), payload)

    def assert_programs_unchanged(self):
        for code, (head, contents) in self.program_snapshots.items():
            self.assertEqual(self.git(code, "rev-parse", "HEAD"), head)
            self.assertEqual(self.files(code), contents, "mail processing wrote inside released code")
            self.assertEqual(self.git(code, "status", "--porcelain"), b"")

    def test_two_releases_resume_bidirectional_mail_and_rollback_preserves_pending(self):
        outward = self.enqueue(self.dev, "first-out", "runner-test-arch")
        returning = self.enqueue(self.runner, "first-return", "federation-arch")
        self.settle()
        self.assert_delivered(self.dev, "first-out", outward, self.runner["member_inbox"])
        self.assert_delivered(self.runner, "first-return", returning, self.dev["inbox"])
        self.assert_programs_unchanged()
        # A filed brief must stay filed when the next release re-reads old snapshots.
        accepted = self.runner["member_inbox"].parent / "accepted"
        accepted.mkdir()
        (self.runner["member_inbox"] / "first-out.md").rename(accepted / "first-out.md")
        next_out = self.enqueue(self.dev, "upgrade-out", "runner-test-arch")
        next_return = self.enqueue(self.runner, "upgrade-return", "federation-arch")
        unavailable = self.base / "offline-mail.git"
        self.remote.rename(unavailable)
        self.assertEqual(self.cycle(self.dev, allow_failure=True)["status"], "failed")
        queues = {h["name"]: self.files(h["state"] / "queue") for h in (self.dev, self.runner)}
        configs = {h["name"]: self.files(h["config"]) for h in (self.dev, self.runner)}
        prior = {h["name"]: self.git(h["code"], "rev-parse", "HEAD") for h in (self.dev, self.runner)}
        for host in (self.dev, self.runner):
            self.select_release(host, "v1.0.1")
            self.assertNotEqual(self.git(host["code"], "rev-parse", "HEAD"), prior[host["name"]])
            self.assertEqual(self.files(host["state"] / "queue"), queues[host["name"]])
            self.assertEqual(self.files(host["config"]), configs[host["name"]])
        unavailable.rename(self.remote)
        self.settle()
        self.assert_delivered(self.dev, "upgrade-out", next_out, self.runner["member_inbox"])
        self.assert_delivered(self.runner, "upgrade-return", next_return, self.dev["inbox"])
        self.assertFalse((self.runner["member_inbox"] / "first-out.md").exists())
        self.assertEqual((accepted / "first-out.md").read_bytes(), outward)
        for host in (self.dev, self.runner):
            current = self.files(host["state"] / "queue")
            for path, payload in queues[host["name"]].items():
                self.assertEqual(current[path], payload)
        rollback = self.enqueue(self.dev, "rollback-out", "runner-test-arch")
        self.select_release(self.dev, "v1.0.0")
        self.select_release(self.runner, "v1.0.0")
        self.settle()
        self.assert_delivered(self.dev, "rollback-out", rollback, self.runner["member_inbox"])
        self.assert_programs_unchanged()
        refs = self.git(self.remote, "for-each-ref", "--format=%(refname)").decode().splitlines()
        self.assertEqual(sorted(refs),
                         ["refs/heads/dev/messages", "refs/heads/runner/messages"])

    def test_negative_control_detects_poller_that_returns_without_worker(self):
        poller = self.runner["code"] / "curate" / "mail-poller.py"
        original = poller.read_text()
        anchor = "return mailworker.main(worker_args)"
        self.assertEqual(original.count(anchor), 1)
        poller.write_text(original.replace(anchor, "return 0  # injected missing delegation"))
        self.enqueue(self.runner, "mutation-out", "federation-arch")
        with self.assertRaisesRegex(AssertionError, "without running the production worker"):
            self.cycle(self.runner, poller=True)
        self.assertEqual(self.lifecycle(self.runner, "mutation-out")["state"], "queued")
        self.assertFalse((self.runner["root"] / "transport.git").exists())


if __name__ == "__main__":
    unittest.main()
