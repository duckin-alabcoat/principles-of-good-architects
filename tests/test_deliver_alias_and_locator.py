"""Two routing defects behind the Runner's growing pending count (2026-09-29).

ALIASES. The `Orchestrator 1` row's `architect_id` became `member-1-arch` (WI-0422), and
22 queued messages already addressed to `Orchestrator 1-arch` were unroutable forever: routing
matched `architect_id` only, and an envelope's destination is immutable. A row may now
carry `aliases`; a message to an alias resolves to that row, the member is located by
the row's CANONICAL id, and the acknowledgment still names the envelope's own
destination so the sender's `_apply_ack` matches it. An alias that is another row's id
would make one address mean two members, so it is refused when the registry loads.

LINKED WORKTREES. devbox's walk root holds ~55 `Orchestrator 1-wt-*` linked worktrees beside the
member's main checkout, all carrying the same STATUS.md id. The walk kept the first hit,
which was a worktree whose `proposed-edits/` does not exist. The locator now never picks
a linked worktree and refuses, naming the candidates, when two main checkouts claim an id.
"""

import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

CURATE = pathlib.Path(__file__).resolve().parent.parent / "curate"
sys.path.insert(0, str(CURATE))
sys.path.insert(0, str(CURATE.parent / "deploy"))

import deliver  # noqa: E402
import maildelivery  # noqa: E402
import mailqueue  # noqa: E402
import migrate  # noqa: E402
import reconcile  # noqa: E402
from production import Roots  # noqa: E402

MAILBOX = "proposed-edits/orbit-assist-arch/pending"


def _rows(**over):
    rows = {
        "orbit": {"architect_id": "orbit-assist-arch", "aliases": ["orbit-arch"],
                   "mailbox": MAILBOX, "tracked": False, "reachable": True},
        "alpha": {"architect_id": "alpha-arch", "mailbox": "proposed-edits/alpha-arch/pending",
                  "tracked": False, "reachable": True},
    }
    rows.update(over)
    return rows


def _brief(to):
    return ("---\nedit-id: alias-fixture\nfrom: federation-arch\n"
            f"to: {to}\napply: manual\nmanual-reason: attended\n---\n\n# Fixture\n")


def _member(path, architect_id="orbit-assist-arch", sid="orbit-assist"):
    (path / MAILBOX).mkdir(parents=True)
    (path / "session.config.json").write_text(
        json.dumps({"architect_id": architect_id}), encoding="utf-8")
    (path / "STATUS.md").write_text(f"---\nid: {sid}\nversion: 1.0.0\n---\n",
                                    encoding="utf-8")
    return path


class AliasResolveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Located under the member's STATUS id, not the registry key — the live shape:
        # only the `_repo_by_architect_id` fallback can find it, and it must be asked the
        # row's CANONICAL id, since the repo never declared the alias.
        self.repo = _member(self.tmp / "orbit")
        self.repos = {"orbit-assist": self.repo}

    def test_a_message_to_an_alias_resolves_to_the_aliased_row(self):
        repo, box, row = deliver.resolve("orbit-arch", _rows(), self.repos)
        self.assertEqual(repo, self.repo)
        self.assertEqual(box, self.repo / MAILBOX)
        self.assertEqual(row["architect_id"], "orbit-assist-arch")

    def test_the_canonical_id_still_resolves(self):
        _repo, box, _row = deliver.resolve("orbit-assist-arch", _rows(), self.repos)
        self.assertEqual(box, self.repo / MAILBOX)

    def test_an_alias_reaches_the_mailbox_tree_and_the_row_lookup(self):
        tree, why = deliver.recipient_mailbox_tree("orbit-arch", _rows(), self.repos)
        self.assertEqual((tree, why), ((self.repo / MAILBOX).parent, None))
        self.assertEqual(deliver._row_for("orbit-arch", _rows())["architect_id"],
                         "orbit-assist-arch")

    def test_delivery_to_an_alias_writes_into_the_canonical_mailbox(self):
        src = self.tmp / "outbox" / "brief.md"
        src.parent.mkdir()
        src.write_text(_brief("orbit-arch"), encoding="utf-8")
        dest = deliver.deliver("orbit-arch", src, _rows(), self.repos)
        self.assertEqual(dest, self.repo / MAILBOX / "brief.md")

    def test_an_unknown_id_is_still_unknown(self):
        with self.assertRaises(deliver.RecipientUnavailable) as e:
            deliver.resolve("nobody-arch", _rows(), self.repos)
        self.assertEqual(e.exception.outcome, "unroutable")

    def test_an_alias_equal_to_another_rows_id_is_refused(self):
        rows = _rows(alpha={"architect_id": "alpha-arch", "aliases": ["orbit-assist-arch"],
                            "mailbox": "x", "tracked": False, "reachable": True})
        with self.assertRaises(ValueError) as e:
            deliver.resolve("alpha-arch", rows, self.repos)
        self.assertIn("orbit-assist-arch", str(e.exception))
        self.assertIn("alias", str(e.exception))

    def test_one_alias_on_two_rows_is_refused(self):
        rows = _rows(alpha={"architect_id": "alpha-arch", "aliases": ["orbit-arch"],
                            "mailbox": "x", "tracked": False, "reachable": True})
        with self.assertRaisesRegex(ValueError, "orbit-arch"):
            deliver.resolve("alpha-arch", rows, self.repos)

    def test_the_registry_refuses_a_colliding_alias_at_load(self):
        path = self.tmp / "mailboxes.json"
        rows = _rows(alpha={"architect_id": "alpha-arch", "aliases": ["orbit-assist-arch"],
                            "mailbox": "x"})
        path.write_text(json.dumps({"members": rows}), encoding="utf-8")
        roots = types.SimpleNamespace(mailboxes_path=path)
        with self.assertRaisesRegex(ValueError, "alias"):
            deliver.load_mailboxes(roots)
        with mock.patch.object(deliver, "MAILBOXES", path), \
                mock.patch.object(deliver.production, "resolve", return_value=None):
            with self.assertRaisesRegex(ValueError, "alias"):
                deliver.load_mailboxes()

    def test_aliases_must_be_a_list_of_ids(self):
        rows = _rows(alpha={"architect_id": "alpha-arch", "aliases": "orbit-arch",
                            "mailbox": "x"})
        with self.assertRaisesRegex(ValueError, "aliases"):
            deliver.check_aliases(rows)

    def test_the_real_registry_carries_the_resident_alias_and_loads(self):
        path = CURATE.parent / "mailboxes.json"
        members = json.loads(path.read_text(encoding="utf-8"))["members"]
        deliver.check_aliases(members)
        if "Orchestrator 1" not in members:
            self.skipTest("the registry here is the public cut's reduced copy, with no rows")
        self.assertIn("Orchestrator 1-arch", members["Orchestrator 1"].get("aliases", []))

    def test_an_alias_gets_a_recipient_ref_so_its_ack_is_accepted(self):
        """`_apply_ack` on the SENDER keys `recipient_refs` by the envelope's destination,
        which for the stranded messages is the alias. Without an entry there every ack is
        refused and the pending count never falls."""
        ids = migrate.recipient_ids({"members": _rows()}, [])
        self.assertIn("orbit-arch", ids)
        self.assertIn("orbit-assist-arch", ids)


class AliasAckTest(unittest.TestCase):
    """The full receive path: an envelope to the alias is delivered into the canonical
    mailbox, and the acknowledgment names the envelope's OWN destination."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = self.base = pathlib.Path(self.temp.name).resolve()
        for name in ("code", "state", "config", "inbox"):
            (base / name).mkdir()
        self.roots = Roots(base / "code", base / "state", base / "transport",
                           base / "config", base / "inbox",
                           {"owned_ref": "refs/heads/receiver/mail",
                            "input_refs": ["refs/heads/sender/mail"],
                            "recipient_refs": {"orbit-arch": "refs/heads/sender/mail"}})
        self.repo = _member(base / "orbit")

    def test_the_ack_carries_the_alias_destination(self):
        payload = _brief("orbit-arch").encode()
        envelope = {"schema_version": 1, "message_id": "alias-1", "destination": "orbit-arch",
                    "filename": "brief.md", "sha256": hashlib.sha256(payload).hexdigest(),
                    "provenance": {}, "created_at": "2026-09-20T00:00:00+00:00"}
        outcome = maildelivery.deliver_one(
            self.roots, "refs/heads/sender/mail", "a" * 40, envelope, payload,
            mailboxes=_rows(), repos={"orbit-assist": self.repo})
        self.assertEqual(outcome, "delivered")
        self.assertEqual((self.repo / MAILBOX / "brief.md").read_bytes(), payload)
        acks = mailqueue.pending(self.roots)
        self.assertEqual(len(acks), 1)
        ack = json.loads(acks[0].payload_path.read_text())
        self.assertEqual(ack["destination"], "orbit-arch")


def _git(*args, cwd):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "user.name=t",
                    "-c", "user.email=t@example.invalid", *args],
                   cwd=cwd, check=True, capture_output=True)


def _main_checkout(path, sid="orbit-assist"):
    _member(path, sid=sid)
    _git("init", "-q", "-b", "main", cwd=path)
    _git("add", "STATUS.md", "session.config.json", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)
    return path


class LinkedWorktreeLocatorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.projects = self.tmp / "Projects"
        self.projects.mkdir()
        self.config = self.tmp / "config"
        self.config.mkdir()
        (self.config / "reconcile-roots.local").write_text(f"{self.projects}\n")
        (self.config / "repo-paths.local").write_text("")
        self.roots = types.SimpleNamespace(config_root=self.config)
        # self_entry reads the federation's main checkout; keep the live store out.
        empty = self.tmp / "no-federation"
        empty.mkdir()
        patcher = mock.patch.object(reconcile, "_MAIN_CHECKOUT", empty)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _worktrees(self, main, *names):
        # Named to sort BEFORE the main checkout, so a first-hit walk meets them first.
        for name in names:
            _git("worktree", "add", "-q", "-b", name, str(self.projects / name), cwd=main)

    def test_a_linked_worktree_is_skipped_for_the_main_checkout(self):
        """Run in BOTH walk orders. Directory order is the filesystem's, not ours, so a
        test that only sorted its names would prove nothing on a filesystem that
        happened to list the main checkout first — and a first-hit locator passes then."""
        main = _main_checkout(self.projects / "orbit")
        self._worktrees(main, "orbit-wt-v160b", "orbit-wt-v161")
        real_walk = reconcile.walk_tree
        for main_first in (False, True):
            def ordered(base, depth, main_first=main_first):
                hits = list(real_walk(base, depth))
                return sorted(hits, key=lambda h: (pathlib.Path(h[0]) == main) != main_first)
            with self.subTest(main_first=main_first), \
                    mock.patch.object(reconcile, "walk_tree", ordered):
                repos = deliver.locate_repos(self.roots)
                self.assertEqual(repos.get("orbit-assist"), main)
                _repo, box, _row = deliver.resolve("orbit-assist-arch", _rows(), repos)
                self.assertEqual(box, main / MAILBOX)

    def test_only_worktrees_locates_nothing_and_says_why(self):
        main = _main_checkout(self.tmp / "elsewhere-orbit")
        self._worktrees(main, "orbit-wt-1")
        repos = deliver.locate_repos(self.roots)
        self.assertNotIn("orbit-assist", repos)
        with self.assertRaises(deliver.RecipientUnavailable) as e:
            deliver.resolve("orbit-arch", _rows(), repos)
        self.assertIn("orbit-wt-1", str(e.exception))
        self.assertIn("worktree", str(e.exception))

    def test_two_main_checkouts_for_one_id_are_refused_naming_both(self):
        one = _main_checkout(self.projects / "orbit")
        two = _main_checkout(self.projects / "orbit-clone")
        repos = deliver.locate_repos(self.roots)
        self.assertNotIn("orbit-assist", repos)
        with self.assertRaises(deliver.RecipientUnavailable) as e:
            deliver.resolve("orbit-arch", _rows(), repos)
        msg = str(e.exception)
        self.assertIn(str(one), msg)
        self.assertIn(str(two), msg)
        self.assertIn("repo-paths.local", msg)

    def test_a_mapped_path_settles_the_ambiguity(self):
        one = _main_checkout(self.projects / "orbit")
        _main_checkout(self.projects / "orbit-clone")
        (self.config / "repo-paths.local").write_text(f"orbit-assist = {one}\n")
        repos = deliver.locate_repos(self.roots)
        self.assertEqual(repos["orbit-assist"], one)
        self.assertEqual(deliver.resolve("orbit-arch", _rows(), repos)[0], one)

    def test_a_plain_directory_outside_git_still_locates(self):
        plain = _member(self.projects / "plain")
        repos = deliver.locate_repos(self.roots)
        self.assertEqual(repos.get("orbit-assist"), plain)


if __name__ == "__main__":
    unittest.main()
