"""The Runner channel — `curate/channel.py`, and the sealed-tree fork in `curate/outbox.py`.

WHAT THIS MODULE IS DEFENDING. Sealing the Runner's process root (WI-0360) turns
`~/deploy/federation` into a detached checkout at a tag. Every unattended write the
federation makes there — a deploy receipt, a delivery record, the poller's persist — was
written against a clone somebody could push. On a detached HEAD none of that is true: the
commit belongs to no branch, `@{u}` does not resolve, and the code that reports on it says
"committed, not pushed", which reads in a log exactly like a deliberate decision. The mail
stops travelling and nothing goes red. `channel.py` is the replacement write surface and
these tests are the proof that the replacement actually carries mail, because the failure
it prevents is SILENT and a silent failure cannot be caught by noticing.

The cases here are chosen around the four ways this can go wrong rather than around the
functions:

* **Misclassifying WHERE we are.** `is_deploy_tree` decides whether a write goes through
  the channel at all, so every caller inherits its answer. The load-bearing case is the
  boring one: a directory that is not a git repository must answer False. `git
  symbolic-ref HEAD` fails there for the same reason it fails on a detached HEAD, so a
  check written on that fact alone would classify every fixture's scratch directory as a
  sealed Runner tree and route its writes into a real channel clone.

* **Reaching the network at import time.** `data_root` runs while `curate/outbox.py` and
  `curate/deliver.py` are still being imported. `git fetch` against an unreachable remote
  does not fail, it BLOCKS on DNS or a credential prompt, and a blocked import in a
  launchd job reads as "still working" forever. So `data_root` is pinned as a
  disk-only question with no side effect, not merely as one that returns the right path.

* **Two writers on a one-writer branch.** The whole design rests on `runner/mail` having
  exactly one writer, because that is what makes a push a fast-forward BY CONSTRUCTION
  rather than by retry. A second writer would bring back the stranded commit under a new
  name, and it would be found the way the first one was: by a human, hours later.

* **A refusal that writes something anyway.** Refusing is the correct outcome on a machine
  that may not write, and it is only correct if it leaves NOTHING behind — no commit on a
  detached HEAD, no half-written file, no channel clone conjured on a machine that had no
  business making one.

THE CENTRAL PROPERTY is `test_a_publish_still_pushes_after_the_trunk_moved_underneath`.
Everything else here is a guard around it. A branch only this machine commits to is always
ahead of its own remote tip, so its push cannot be rejected for non-fast-forward — and the
only honest way to test "cannot be rejected" is to run the race that used to reject it:
land a commit on `origin/main` between two publishes and require the second to push.

HERMETIC, DELIBERATELY AND LOUDLY. A fixture in this repo has committed into the real
repository before. Every test here sets `POGA_CHANNEL_ROOT` into a tempdir, patches
`channel.common.this_machine` rather than asking the machine running the suite, and builds
its `origin` as a local bare repo. Nothing reaches the network, `~/deploy`, or this
checkout. The environment is redirected before the modules are even imported, because
`outbox.DATA_ROOT` is resolved at IMPORT time — on a gate that runs the suite from a
detached worktree, an unguarded import would resolve the operator's real channel.
"""

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

_CURATE = pathlib.Path(__file__).resolve().parent.parent / "curate"
GIT = shutil.which("git")


def _load(name, filename):
    """Load one `curate/` module under a name of OUR OWN.

    NOT under its real name, which is the trap this repo's loader shape hides. `tests/` is
    flat and several modules load `curate/outbox.py` by hand; whichever module is imported
    LAST wins `sys.modules["outbox"]`, and every subsequent `import outbox` — including the
    one inside `curate/mail-poller.py`, which `test_outbox` drives — binds to that winner
    instead of to the copy the fixture patched. Measured while writing this file: importing
    this module beside `test_outbox` in one process turned 20 of its tests red, in a suite
    that is SHARDED and therefore free to pack them together. A private name, and putting
    back whatever was in `sys.modules` before, keeps the blast radius inside this file."""
    spec = importlib.util.spec_from_file_location(name, _CURATE / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# The import itself is fenced twice over.
#
# AGAINST THE MACHINE: `curate/outbox.py` calls `channel.data_root(ROOT)` at module scope,
# so merely importing it asks "is this tree sealed, and if so where is the channel?" — and
# where the answer is yes (a gate that runs the suite from a detached worktree), the
# unfenced answer is the operator's real `~/deploy/federation-channel`.
#
# AGAINST THE REST OF THE SUITE: `sys.modules` is put back exactly as it was, so a module
# imported after this one still finds the `outbox`/`channel`/`scrub` it expects.
_BEFORE = {n: sys.modules.get(n)
           for n in ("channel", "outbox", "scrub", "common", "deliver")}
with mock.patch.dict(os.environ, {"POGA_CHANNEL_ROOT": "/nonexistent/channel-at-import"}):
    channel = _load("channel_under_test_runner", "channel.py")
    outbox = _load("outbox_under_test_runner", "outbox.py")
    # `deliver` is here for ONE function: `audit_status_line`, which reads the channel and
    # runs on the SessionStart path. Its import also resolves `MAILBOXES` through
    # `channel.data_root`, so it belongs inside the same fence as the other two.
    deliver = _load("deliver_under_test_runner", "deliver.py")
# `outbox` resolved its own `import channel` through whatever `sys.modules` happened to
# hold. Bind it to the object these tests patch, so "the channel" means one thing here
# however the modules were ordered.
outbox.channel = channel
for _name, _mod in _BEFORE.items():
    if _mod is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _mod


def _git(repo, *args, check=True):
    """One git command against `repo`, always with `-C`, never with a shell.

    Raising on failure rather than returning a code is what keeps a broken fixture from
    being read as a finding about the code under test."""
    p = subprocess.run([GIT, "-C", str(repo), *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"fixture: git {' '.join(args)} in {repo} failed: "
                             f"{p.stderr.strip() or p.stdout.strip()}")
    return p.returncode, p.stdout.strip(), p.stderr.strip()


#: Settings that stop git forking work that OUTLIVES THE TEST. `gc.auto 0` and
#: `maintenance.auto false` say do not start it; `gc.autoDetach false` says that if
#: something starts it anyway, it runs in the foreground where `subprocess.run` waits for
#: it. All three, because the first two are about a decision and the third is about the
#: race, and it is the race that bites.
_NO_BACKGROUND_GIT = (("gc.auto", "0"),
                      ("gc.autoDetach", "false"),
                      ("maintenance.auto", "false"))

#: The same three as environment overrides, for the repository this fixture does NOT
#: create: `channel.ensure` clones the channel itself, so there is no moment between its
#: creation and its first push in which a test could configure it. `GIT_CONFIG_*` (git
#: 2.31+) applies to every git process started while it is set — including the
#: `receive-pack` a local push spawns inside the bare origin, which is where the detached
#: maintenance that broke teardown actually runs.
_NO_BACKGROUND_GIT_ENV = {"GIT_CONFIG_COUNT": str(len(_NO_BACKGROUND_GIT))}
for _i, (_k, _v) in enumerate(_NO_BACKGROUND_GIT):
    _NO_BACKGROUND_GIT_ENV[f"GIT_CONFIG_KEY_{_i}"] = _k
    _NO_BACKGROUND_GIT_ENV[f"GIT_CONFIG_VALUE_{_i}"] = _v


def _quiesce(repo):
    """Forbid background maintenance in one fixture repo.

    WHY THIS IS NOT HOUSEKEEPING. A push to a local bare origin runs `receive-pack` inside
    that origin, and modern git ends such an operation by forking `maintenance run --auto`,
    DETACHED — it returns success to the caller and goes on writing into `objects/`. The
    test body has finished by then and `TemporaryDirectory.cleanup` is already walking the
    directory, so `rmtree` lists `objects/`, deletes what it saw, and then `rmdir` fails
    with `[Errno 66] Directory not empty` on whatever the background process wrote in the
    gap. It is a teardown ERROR after a PASSING test, it needs no assertion to be wrong,
    and it appears and disappears with machine load — which is why it showed up under the
    sharded runner and never in isolation.

    This module clones and pushes more than anything else in the suite, so it meets the
    race first; nothing about the race is specific to it."""
    for key, value in _NO_BACKGROUND_GIT:
        _git(repo, "config", key, value)


def _identify(repo):
    """Per-repo identity and no signing. Never `--global`: a fixture that configures the
    machine running it has already failed the test it was written to pass, and
    `commit.gpgsign` inherited from an operator who signs turns every commit here into a
    pinentry prompt with no TTY to answer it."""
    _quiesce(repo)
    _git(repo, "config", "user.email", "channel-fixture@localhost")
    _git(repo, "config", "user.name", "channel fixture")
    _git(repo, "config", "commit.gpgsign", "false")


class ChannelCase(unittest.TestCase):
    """A tempdir, a redirected channel root, and a machine label that is not this machine's.

    `writer_label()` and `refuse_write_reason()` are the two places production code asks
    the world who it is. Both are pinned here for every test, including the ones that do
    not obviously care, because a test that passes only on the Runner is a test that
    reports on the Runner."""

    def setUp(self):
        if GIT is None:                       # pragma: no cover - git is a suite premise
            self.skipTest("git is not on PATH")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = pathlib.Path(tmp.name)
        self.deploy = self.tmp / "deploy"
        self.channel_root = self.deploy / "federation-channel"
        env = mock.patch.dict(os.environ, {
            "POGA_CHANNEL_ROOT": str(self.channel_root),
            "POGA_CHANNEL_WRITER": "TestRunner",
            "POGA_DEPLOY_ROOT": str(self.deploy),
            **_NO_BACKGROUND_GIT_ENV,
        })
        env.start()
        self.addCleanup(env.stop)
        self.be_machine("TestRunner")
        self.log = []

    def be_machine(self, label):
        """Answer `common.this_machine()` with `label` for the rest of this test."""
        p = mock.patch.object(channel.common, "this_machine", lambda: label)
        p.start()
        self.addCleanup(p.stop)

    # ---- fixtures -------------------------------------------------------------

    def origin_with_a_release(self):
        """A bare `origin` on `main` carrying one tagged release. Returns (bare, seed).

        `seed` stands in for devbox: the machine that moves the trunk. It exists so the
        race can be run for real rather than simulated by writing refs by hand."""
        bare = self.tmp / "origin.git"
        bare.mkdir(parents=True)
        _git(bare, "init", "--bare", "-q", "-b", "main")
        # The bare is where a push runs `receive-pack`, and therefore where the detached
        # maintenance that broke teardown was started. It has no identity to configure, so
        # this is its own line rather than part of `_identify`.
        _quiesce(bare)
        seed = self.tmp / "seed"
        _git(self.tmp, "clone", "-q", str(bare), "seed")
        _identify(seed)
        (seed / "outbox" / "to-x").mkdir(parents=True)
        (seed / "outbox" / "to-x" / ".gitkeep").write_text("", encoding="utf-8")
        (seed / "mailboxes.json").write_text('{"members": []}\n', encoding="utf-8")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-qm", "the release")
        _git(seed, "tag", "v1")
        _git(seed, "push", "-q", "origin", "main")
        _git(seed, "push", "-q", "origin", "v1")
        return bare, seed

    def land_on_trunk(self, seed, name, text="from devbox\n"):
        """Move `origin/main` from somewhere else, exactly as devbox does mid-cycle."""
        (seed / name).write_text(text, encoding="utf-8")
        _git(seed, "add", "--", name)
        _git(seed, "commit", "-qm", f"trunk: {name}")
        _git(seed, "push", "-q", "origin", "main")

    def deploy_tree(self, bare):
        """A SEALED process root: a clone put on a tag with `checkout --force --detach`,
        which is how every deploy tree in the fleet is placed at its version."""
        self.deploy.mkdir(parents=True, exist_ok=True)
        _git(self.deploy, "clone", "-q", str(bare), "federation")
        tree = self.deploy / "federation"
        _git(tree, "checkout", "--force", "--detach", "v1")
        _identify(tree)
        return tree

    def branch_checkout(self, bare, name="work"):
        """An ordinary clone on `main` with an upstream — a lane, the trunk, a developer."""
        _git(self.tmp, "clone", "-q", str(bare), name)
        tree = self.tmp / name
        _identify(tree)
        return tree

    # ---- reading the result ---------------------------------------------------

    def on_branch(self, bare, path, branch="runner/mail"):
        """The text of `path` as it exists on `branch` IN THE BARE ORIGIN.

        Reading the origin rather than the local clone is the point: "the commit is on the
        branch" and "the brief reached the other machine" are different claims, and the
        strand class this module ends is exactly the gap between them."""
        rc, out, err = _git(bare, "show", f"{branch}:{path}", check=False)
        if rc != 0:
            self.fail(f"{path} is not on {branch} in origin: {err}")
        return out

    def head_of(self, repo):
        return _git(repo, "rev-parse", "HEAD")[1]


class IsDeployTreeTest(ChannelCase):
    """The classifier every other decision hangs off."""

    def test_a_detached_checkout_is_a_deploy_tree(self):
        bare, _seed = self.origin_with_a_release()
        self.assertTrue(channel.is_deploy_tree(self.deploy_tree(bare)))

    def test_a_checkout_on_a_branch_is_NOT(self):
        """A lane, the trunk and a developer's clone all sit on a symbolic ref. Nothing
        about the fork may change for them — that is the promise the fork was allowed in
        on."""
        bare, _seed = self.origin_with_a_release()
        self.assertFalse(channel.is_deploy_tree(self.branch_checkout(bare)))

    def test_a_directory_that_is_no_repo_at_all_is_NOT(self):
        """LOAD-BEARING. `git symbolic-ref -q HEAD` fails in a plain directory for the same
        reason it fails on a detached HEAD, so a check written on that one fact would call
        every scratch directory in the suite a sealed Runner tree — and each of those
        writes would then be routed into a channel clone nobody asked for. The `rev-parse
        --git-dir` anchor is what separates 'no branch' from 'no repository'."""
        plain = self.tmp / "just-a-directory"
        plain.mkdir()
        self.assertFalse(channel.is_deploy_tree(plain))

    def test_a_path_that_does_not_exist_is_NOT(self):
        """Same class, one step further out: an unresolvable path is not a deploy tree, and
        the answer must be a False rather than an exception thrown through an import."""
        self.assertFalse(channel.is_deploy_tree(self.tmp / "no" / "such" / "place"))


class DataRootTest(ChannelCase):
    """The inbound leg in one function — and the one that runs at import time."""

    def test_a_branch_checkout_reads_its_OWN_tree_even_when_a_channel_exists(self):
        """The redirect is keyed on the tree being sealed, never on a channel being
        present. A developer who once ran the Runner's poller on their laptop has a channel
        clone on disk; their lane must still read its own `outbox/`."""
        bare, _seed = self.origin_with_a_release()
        work = self.branch_checkout(bare)
        channel.ensure(str(bare), log=self.log.append)
        self.assertTrue((self.channel_root / ".git").exists(), "fixture: no channel made")
        self.assertEqual(channel.data_root(work), work)

    def test_detached_development_tree_does_not_implicitly_select_the_channel(self):
        bare, _seed = self.origin_with_a_release()
        tree = self.deploy_tree(bare)
        channel.ensure(str(bare), log=self.log.append)
        self.assertEqual(channel.data_root(tree), tree)

    def test_unconfigured_detached_tree_keeps_development_roots(self):
        """Production now requires explicit configuration; no fallback is inferred."""
        bare, _seed = self.origin_with_a_release()
        tree = self.deploy_tree(bare)
        self.assertFalse(self.channel_root.exists())
        self.assertEqual(channel.data_root(tree), tree)

    def test_it_reaches_no_network_and_creates_nothing(self):
        """THE IMPORT-TIME CONTRACT, stated as a test because it is unenforceable by
        reading: `data_root` is called while `curate/outbox.py` and `curate/deliver.py` are
        being imported, and `git fetch` against an unreachable remote HANGS on DNS or a
        credential prompt rather than failing. A hang there is a launchd job that reads as
        'still working' forever. Every git command it issues is recorded and the network
        verbs must not appear."""
        bare, _seed = self.origin_with_a_release()
        tree = self.deploy_tree(bare)
        seen = []
        real = channel._git

        def spy(repo, args, timeout=300):
            seen.append(list(args))
            return real(repo, args, timeout=timeout)

        with mock.patch.object(channel, "_git", spy):
            got = channel.data_root(tree)

        self.assertEqual(got, tree)
        verbs = {a[0] for a in seen if a}
        self.assertEqual(verbs & {"fetch", "clone", "push", "pull", "ls-remote"}, set(),
                         f"data_root reached for the network: {seen}")
        self.assertFalse(self.channel_root.exists(),
                         "data_root created a channel clone; it only answers questions")


class RefuseWriteReasonTest(ChannelCase):
    """One writer, and a machine that cannot name itself is not it."""

    def test_the_declared_writer_may_write(self):
        self.be_machine("TestRunner")
        self.assertEqual(channel.refuse_write_reason(), "")

    def test_any_other_machine_is_refused_and_is_told_which_two_labels_disagree(self):
        """The refusal is read by whoever is holding the failure after an unattended run. 'Refused' alone
        sends them to the code; naming both labels ends it at the log line."""
        self.be_machine("DevBox")
        why = channel.refuse_write_reason()
        self.assertTrue(why)
        self.assertIn("DevBox", why)
        self.assertIn("TestRunner", why)

    def test_a_machine_that_cannot_name_itself_is_refused(self):
        """`common.this_machine()` returns "" rather than guessing. Not knowing where you
        are standing is a reason to WITHHOLD a write, never to take one — a one-writer
        branch written by a machine that cannot tell whether it is the writer is exactly
        the two-writer failure, arrived at by accident instead of by configuration."""
        self.be_machine("")
        why = channel.refuse_write_reason()
        self.assertTrue(why)
        self.assertIn("cannot name itself", why)


class EnsureTest(ChannelCase):
    """Making the write surface, without asking a human for a step on the Runner."""

    def test_it_clones_lands_on_the_branch_and_is_idempotent(self):
        bare, _seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        self.assertEqual(root, self.channel_root)
        self.assertTrue((root / ".git").exists())
        self.assertEqual(_git(root, "symbolic-ref", "--short", "-q", "HEAD")[1],
                         channel.BRANCH)
        # The tag's content is present: this clone is a real working tree, not an orphan.
        self.assertTrue((root / "mailboxes.json").is_file())

        before = self.head_of(root)
        again = channel.ensure(str(bare), log=self.log.append)
        self.assertEqual(again, self.channel_root)
        self.assertEqual(_git(root, "symbolic-ref", "--short", "-q", "HEAD")[1],
                         channel.BRANCH)
        self.assertEqual(self.head_of(root), before,
                         "a second ensure moved the branch with nothing to publish")

    def test_the_branch_tracks_its_OWN_remote_branch(self):
        """WHY THE UPSTREAM'S IDENTITY MATTERS, and not merely that one exists.

        `ensure`'s own comment states the design: with `origin/runner/mail` tracked, the
        channel is an ORDINARY clone on an ordinary branch, so `curate/outbox.py`'s
        existing add/commit/push works here unchanged and no other program in the fleet
        needs channel-specific code. That claim is only true if the tracked ref is the
        branch we push. Tracking `origin/main` from a one-writer branch is the trunk clone
        this module replaced, wearing the channel's name: the plain `git push` those
        callers make is then a `runner/mail` → `main` push, which git refuses under its
        default `push.default=simple` and which would be far worse if it did not."""
        bare, _seed = self.origin_with_a_release()
        # THE FIRST EVER ENSURE, which is the only run that can get this wrong: there is no
        # `origin/runner/mail` to fork from, so the branch is cut from `origin/main` and
        # git's `branch.autoSetupMerge` is standing right there offering to track it.
        self.assertNotEqual(
            _git(bare, "rev-parse", "--verify", "--quiet",
                 f"refs/heads/{channel.BRANCH}", check=False)[0], 0,
            "fixture: origin already carries the branch, so this is not a first ensure")

        root = channel.ensure(str(bare), log=self.log.append)

        rc, upstream, _ = _git(root, "rev-parse", "--abbrev-ref",
                               "--symbolic-full-name", "@{u}", check=False)
        self.assertEqual(rc, 0, "the channel branch tracks nothing at all")
        self.assertEqual(upstream, f"origin/{channel.BRANCH}")
        # And the tracking ref is real rather than merely configured: the first-use push
        # that CREATES it is the step a wrong upstream silently skipped.
        self.assertEqual(
            _git(bare, "rev-parse", "--verify", "--quiet",
                 f"refs/heads/{channel.BRANCH}", check=False)[0], 0,
            f"{channel.BRANCH} was never published to origin: {self.log}")

    def test_an_origin_that_cannot_be_pushed_leaves_NO_upstream_rather_than_a_WRONG_one(self):
        """The other half of the same fix, and the reason it is not just tidiness.

        If the first-use push cannot create `origin/runner/mail`, the branch is left
        tracking whatever it tracked before — and a WRONG upstream is worse than none,
        because the ordinary publish path inside this clone does a bare `git push` and would
        aim it somewhere nobody chose. Unset is the honest state: `outbox.publish` then
        takes its documented "committed, not pushed" road, which leaves the receipt
        recoverable and says so out loud.

        The push is made to fail WITHOUT A NETWORK by giving the local bare origin a
        `pre-receive` hook that exits non-zero — a refusing remote, on disk."""
        bare, _seed = self.origin_with_a_release()
        hook = bare / "hooks" / "pre-receive"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        root = channel.ensure(str(bare), log=self.log.append)
        self.assertIsNotNone(root, self.log)
        self.assertNotEqual(
            _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                 "@{u}", check=False)[0], 0,
            "a failed first-use push left an upstream pointing somewhere we did not choose")

        # The consequence, stated where a reader will meet it: a publish from inside the
        # channel now REPORTS the receipt as committed and unpublished instead of pushing
        # it at the trunk.
        _identify(root)
        _git(root, "config", "push.default", "simple")
        box = root / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)
        (box / "stranded.md").write_text("# unsendable\n", encoding="utf-8")
        self.log.clear()
        outbox.publish("mail: persisted", "body\n", root=root / "outbox",
                       log=self.log.append)
        self.assertTrue(any("tracks no upstream" in m for m in self.log),
                        f"the unpublished receipt was not announced: {self.log}")

    def test_a_rebuilt_channel_ADOPTS_the_branch_already_on_origin(self):
        """Re-forking from `origin/main` here would DROP MAIL, silently.

        The channel clone is a cache, not a record — it can be deleted by a cleanup, lost
        with a disk, or rebuilt after a Runner reinstall — and `origin/runner/mail` holds
        every receipt that has been published and not yet landed on the trunk. A fresh
        `ensure` that cut the branch from `origin/main` again would put the local branch
        BEHIND that remote tip: the next push is then a non-fast-forward on the one branch
        whose whole design claim is that it can never have one, and the recovery a human
        would reach for (force, or reset) is the one that discards the receipts.

        So the start point is `origin/runner/mail` whenever it exists, and the proof is
        that the earlier brief is still in the tree, the branch descends from the remote
        tip, and the NEXT publish still fast-forwards."""
        bare, _seed = self.origin_with_a_release()
        self.assertTrue(channel.publish({"outbox/to-x/earlier.md": "already sent\n"},
                                        "receipt: earlier", "b\n", origin=str(bare),
                                        log=self.log.append), self.log)
        remote_tip = _git(bare, "rev-parse", f"refs/heads/{channel.BRANCH}")[1]

        shutil.rmtree(self.channel_root)
        root = channel.ensure(str(bare), log=self.log.append)

        self.assertIsNotNone(root, self.log)
        self.assertEqual(_git(root, "symbolic-ref", "--short", "-q", "HEAD")[1],
                         channel.BRANCH)
        self.assertTrue((root / "outbox" / "to-x" / "earlier.md").is_file(),
                        f"the rebuilt channel re-forked from the trunk and lost the "
                        f"published mail: {self.log}")
        self.assertEqual(_git(root, "merge-base", "--is-ancestor", remote_tip, "HEAD",
                              check=False)[0], 0,
                         "the rebuilt branch does not contain origin's tip; its next push "
                         "is a non-fast-forward")

        self.assertTrue(channel.publish({"outbox/to-x/later.md": "sent after\n"},
                                        "receipt: later", "b\n", origin=str(bare),
                                        log=self.log.append),
                        f"the publish after a rebuild was rejected: {self.log}")
        self.assertEqual(self.on_branch(bare, "outbox/to-x/earlier.md"), "already sent")
        self.assertEqual(self.on_branch(bare, "outbox/to-x/later.md"), "sent after")

    def test_a_second_ensure_ABSORBS_a_trunk_commit_that_landed_meanwhile(self):
        """The inbound leg. The poller delivers from `outbox/` and resolves through
        `mailboxes.json`, so a channel that does not carry devbox's CURRENT trunk delivers
        the release's frozen queue forever — the exact staleness the sealing would
        otherwise introduce, and invisible because a stale answer and a correct one look
        the same."""
        bare, seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        self.land_on_trunk(seed, "from-devbox.md", "a brief queued on the trunk\n")

        self.assertIsNotNone(channel.ensure(str(bare), log=self.log.append))
        landed = root / "from-devbox.md"
        self.assertTrue(landed.is_file(),
                        f"the trunk commit was not absorbed into the working tree: "
                        f"{self.log}")
        self.assertEqual(landed.read_text(encoding="utf-8"),
                         "a brief queued on the trunk\n")


class TheChannelIsAnOrdinaryCloneTest(ChannelCase):
    """The claim the rest of the fleet depends on, exercised through the caller that
    depends on it.

    `curate/mail-poller.py:persist` calls `outbox.publish(subject, body)` with no `root`,
    so its box is `outbox.DATA_ROOT / "outbox"` — and on a sealed Runner `DATA_ROOT` IS the
    channel clone. That clone is on a branch, so `outbox.publish` does NOT take the sealed
    fork: it takes the ordinary path, which is exactly what `channel.ensure`'s comment
    promises will work here unchanged. It commits, sees an upstream, and runs a bare `git
    push`.

    `push.default` is pinned to `simple` — git's own default since 2.0 — rather than
    inherited, because the answer changes with it and a test that read the operator's
    config would report on the operator. Under `simple` a branch whose upstream has a
    different name is refused, so a mistracked channel strands the receipt locally: the
    precise failure `channel.py` exists to end, moved one directory over. Under
    `push.default=upstream` it would not be refused — it would push the channel branch onto
    the trunk."""

    def test_the_pollers_persist_path_publishes_from_inside_the_channel(self):
        bare, _seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        _identify(root)
        _git(root, "config", "push.default", "simple")

        box = root / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)
        (box / "persisted.md").write_text("# delivery record\n", encoding="utf-8")

        ok = outbox.publish("mail: persisted", "body\n", root=root / "outbox",
                            log=self.log.append)

        self.assertTrue(ok, self.log)
        self.assertEqual(self.on_branch(bare, "outbox/to-x/persisted.md"),
                         "# delivery record")
        self.assertEqual([m for m in self.log if "could not be published" in m], [],
                         "the receipt was committed into the channel and stranded there")
        # AND IT WENT WHERE WE MEANT. A bare `git push` aims at the upstream, so the same
        # defect that stranded this receipt could instead have delivered it onto the trunk
        # under a different `push.default`. Pinning the branch it reached is what makes the
        # assertion above about the right thing rather than about an exit code.
        self.assertNotEqual(_git(bare, "show", "main:outbox/to-x/persisted.md",
                                 check=False)[0], 0,
                            "the channel's publish landed on the TRUNK")

    def test_a_HOSTILE_upstream_cannot_steer_the_push_onto_the_trunk(self):
        """The route to `main` I went looking for, captured now that it is closed.

        `outbox.publish` runs inside the channel clone, and until `_push` existed it pushed
        with a bare `git push`, whose destination is the clone's upstream plus the
        operator's `push.default` — configuration written somewhere else, by something
        else, possibly long ago. `channel.ensure` sets that upstream correctly, but `ensure`
        is reached only from the deploy SWEEP and the poller's `refresh`, so the on-demand
        `deploy` verb and a `--no-refresh` poll push without it having run. Measured against
        the pre-fix shape: with the upstream at `origin/main` and `push.default=upstream`,
        the receipt landed ON `main` — an unattended Runner job writing the trunk.

        So the upstream is set to `origin/main` ON PURPOSE here and the most dangerous
        `push.default` is chosen ON PURPOSE. This is not a state the fixed `ensure`
        produces; it is the state a stale clone, a debugging session, or a future refactor
        that drops `--no-track` produces, and the point of naming `HEAD:<branch>` at the
        push site is that none of them can reach the trunk any more. A safety property has
        to hold against a hostile configuration or it is a convention."""
        bare, _seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        _identify(root)
        _git(root, "branch", "--set-upstream-to=origin/main", channel.BRANCH)
        _git(root, "config", "push.default", "upstream")
        trunk_before = _git(bare, "rev-parse", "refs/heads/main")[1]

        box = root / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)
        (box / "hostile.md").write_text("# receipt\n", encoding="utf-8")
        ok = outbox.publish("mail: hostile upstream", "body\n", root=root / "outbox",
                            log=self.log.append)

        self.assertTrue(ok, self.log)
        self.assertEqual(_git(bare, "rev-parse", "refs/heads/main")[1], trunk_before,
                         "a channel write moved the TRUNK")
        self.assertEqual(self.on_branch(bare, "outbox/to-x/hostile.md"), "# receipt")

    def test_push_on_a_DETACHED_head_refuses_and_sends_nothing(self):
        """`_push` has no branch to name, so it does not guess one.

        This is unreachable through `publish` — a detached tree forks to the channel long
        before the push — which is the reason to test the helper directly rather than
        through a caller. The guard exists for the next caller, and a guard whose only
        proof is that today's callers never reach it is a guard nobody can safely keep."""
        bare, _seed = self.origin_with_a_release()
        tree = self.deploy_tree(bare)
        refs_before = _git(bare, "for-each-ref", "--format=%(refname) %(objectname)")[1]

        rc, _out, _err = outbox._push(lambda args: _git(tree, *args, check=False),
                                      self.log.append)

        self.assertNotEqual(rc, 0, "a detached checkout pushed somewhere")
        self.assertTrue(any("REFUSED" in m for m in self.log), self.log)
        self.assertEqual(_git(bare, "for-each-ref",
                              "--format=%(refname) %(objectname)")[1], refs_before,
                         "the refused push still moved a ref on origin")

    def test_the_bare_push_reaches_runner_mail_under_every_push_default(self):
        """The fix verified rather than assumed, across the setting it turns on.

        The test above pins `push.default=simple` so it reports on the code and not on the
        operator — but that pin is also a place to hide, because `simple` is the one value
        that REFUSES a mismatched upstream and so is the most forgiving witness available.
        The defect it was written for (upstream `origin/main`) fails differently under each
        setting: `simple` refuses and strands the receipt, `upstream` pushes it onto the
        TRUNK, `matching` and `current` happen to be right by accident. A fix that only
        satisfies `simple` has closed one symptom of three.

        With the upstream actually `origin/runner/mail`, all four agree, and `matching` is
        the interesting one: it pushes every branch present at both ends, and the only
        reason it does not touch `main` is that this clone deliberately keeps no local
        `main` — a design note in the module header, pinned here as behaviour."""
        bare, _seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        _identify(root)
        box = root / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)

        for mode in ("simple", "current", "upstream", "matching"):
            with self.subTest(push_default=mode):
                _git(root, "config", "push.default", mode)
                (box / f"{mode}.md").write_text(f"# {mode}\n", encoding="utf-8")
                self.log.clear()
                ok = outbox.publish(f"mail: {mode}", "body\n", root=root / "outbox",
                                    log=self.log.append)
                self.assertTrue(ok, self.log)
                self.assertEqual(self.on_branch(bare, f"outbox/to-x/{mode}.md"),
                                 f"# {mode}")
                self.assertEqual([m for m in self.log if "could not be published" in m],
                                 [], f"stranded under push.default={mode}: {self.log}")
                self.assertNotEqual(
                    _git(bare, "show", f"main:outbox/to-x/{mode}.md", check=False)[0], 0,
                    f"push.default={mode} put the channel's receipt on the TRUNK")


class PublishTest(ChannelCase):
    """The outbound leg, and the race it exists to be immune to."""

    def test_it_writes_commits_and_PUSHES(self):
        bare, _seed = self.origin_with_a_release()
        ok = channel.publish({"outbox/to-x/first.md": "hello\n"},
                             "receipt: one", "body\n", origin=str(bare),
                             log=self.log.append)
        self.assertTrue(ok, self.log)
        self.assertEqual(self.on_branch(bare, "outbox/to-x/first.md"), "hello")

    def test_a_publish_still_pushes_after_the_trunk_moved_underneath(self):
        """THE CENTRAL PROPERTY of the whole design, run as the race rather than argued.

        A one-writer branch is always ahead of its own remote tip, so its push always
        fast-forwards — whatever happened to `main` in between. The failure this replaces
        was measured three times in one day on 2026-09-13: a receipt committed into the
        Runner's trunk clone after devbox had moved the trunk left it one ahead and N
        behind, unable to push and unable to fast-forward, and a human cleared it each
        time. So: publish, let devbox land on `main`, publish again. The second push must
        succeed, both briefs must be on `origin/runner/mail`, and the branch must carry the
        trunk commit too — because that is what makes devbox's landing merge trivial.

        A `push failed` in the log is a failure even if the call returned True, which it
        cannot: that path returns False. The log is asserted anyway so a future change that
        starts swallowing the rejection cannot pass this test quietly."""
        bare, seed = self.origin_with_a_release()
        self.assertTrue(channel.publish({"outbox/to-x/first.md": "one\n"},
                                        "receipt: one", "b\n", origin=str(bare),
                                        log=self.log.append), self.log)

        self.land_on_trunk(seed, "trunk-moved.md", "devbox landed while we wrote\n")

        self.assertTrue(channel.publish({"outbox/to-x/second.md": "two\n"},
                                        "receipt: two", "b\n", origin=str(bare),
                                        log=self.log.append),
                        f"the second publish did not reach origin: {self.log}")

        self.assertEqual(self.on_branch(bare, "outbox/to-x/first.md"), "one")
        self.assertEqual(self.on_branch(bare, "outbox/to-x/second.md"), "two")
        self.assertEqual(self.on_branch(bare, "trunk-moved.md"),
                         "devbox landed while we wrote")
        self.assertEqual([m for m in self.log if "push failed" in m], [], self.log)
        # And `main` is untouched by us: the channel writes one branch and only one.
        rc, _out, _err = _git(bare, "show", "main:outbox/to-x/first.md", check=False)
        self.assertNotEqual(rc, 0, "the channel wrote onto the trunk")

    def test_a_machine_that_is_not_the_writer_publishes_NOTHING_and_makes_no_clone(self):
        """Refusing has to be free of side effects to be the safe answer. A refusal that
        had already cloned a channel would leave a second machine holding a writable copy
        of the one-writer branch, which is the two-writer failure staged and waiting."""
        bare, _seed = self.origin_with_a_release()
        self.be_machine("DevBox")
        ok = channel.publish({"outbox/to-x/nope.md": "x\n"}, "s", "b\n",
                             origin=str(bare), log=self.log.append)
        self.assertFalse(ok)
        self.assertTrue(any("REFUSED" in m for m in self.log), self.log)
        self.assertFalse(self.channel_root.exists(),
                         "a refused write still created the channel clone")

    def test_an_absolute_path_is_refused_and_nothing_is_written(self):
        """The channel writes repo-relative paths, full stop. An absolute path handed to a
        writer that re-lays files by name is an arbitrary-file write on the machine holding
        the federation's records — and the refusal must come BEFORE the write, or the
        refusal is just a report on damage already done."""
        bare, _seed = self.origin_with_a_release()
        escape = self.tmp / "escaped.md"
        root = channel.ensure(str(bare), log=self.log.append)
        before = self.head_of(root)

        self.assertFalse(channel.publish({str(escape): "x\n"}, "s", "b\n",
                                         origin=str(bare), log=self.log.append))
        self.assertFalse(escape.exists(), "the refused absolute path was written anyway")
        self.assertEqual(self.head_of(root), before, "a refused publish still committed")

    def test_a_dot_dot_path_is_refused_and_nothing_is_written(self):
        """`outbox/../../x` normalises out of the clone just as surely as `/x` does, and
        only one of the two looks dangerous at a glance."""
        bare, _seed = self.origin_with_a_release()
        root = channel.ensure(str(bare), log=self.log.append)
        before = self.head_of(root)

        self.assertFalse(channel.publish({"../escaped.md": "x\n"}, "s", "b\n",
                                         origin=str(bare), log=self.log.append))
        self.assertFalse((self.channel_root.parent / "escaped.md").exists())
        self.assertEqual(self.head_of(root), before, "a refused publish still committed")


class OutboxDetachedDevelopmentRefusalTest(ChannelCase):
    """WI-0361 replaces WI-0360's implicit detached-HEAD production routing.

    The old routing tests asserted the rejected design. Production enqueue/concurrency/
    interruption checks now live in test_production_mail_state; legacy channel helpers
    keep their direct tests here for migration compatibility.
    """

    def test_detached_development_publish_does_not_create_a_channel_or_commit(self):
        bare, _seed = self.origin_with_a_release()
        tree = self.deploy_tree(bare)
        box = tree / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)
        payload = box / "receipt.md"
        payload.write_text("# original receipt\n", encoding="utf-8")
        before = self.head_of(tree)
        ok = outbox.publish("receipt", "body", root=tree / "outbox", log=self.log.append)
        self.assertFalse(ok)
        self.assertFalse(self.channel_root.exists())
        self.assertEqual(self.head_of(tree), before)
        self.assertEqual(payload.read_text(), "# original receipt\n")
        self.assertTrue(any("REFUSED" in line for line in self.log))


class OutboxBranchCheckoutIsUnchangedTest(ChannelCase):
    """The regression guard. The fork was allowed in on the promise that a lane, the trunk
    and a developer's clone behave exactly as they did — same commit, same push, same
    return value, and no channel clone anywhere near them."""

    def test_a_normal_checkout_still_commits_and_pushes_the_old_way(self):
        bare, _seed = self.origin_with_a_release()
        work = self.branch_checkout(bare)
        box = work / "outbox" / "to-x"
        box.mkdir(parents=True, exist_ok=True)
        (box / "brief.md").write_text("# ordinary\n", encoding="utf-8")

        ok = outbox.publish("outbox: one brief", "body\n", root=work / "outbox",
                            log=self.log.append)

        self.assertTrue(ok, self.log)
        self.assertEqual(self.on_branch(bare, "outbox/to-x/brief.md", branch="main"),
                         "# ordinary")
        self.assertEqual(_git(work, "status", "--porcelain", "--", "outbox")[1], "",
                         "the brief was left uncommitted in the working tree")
        self.assertFalse(self.channel_root.exists(),
                         "an ordinary checkout was routed through the channel")
        # THE TRUNK LANE, EXPLICITLY: naming `HEAD:<branch>` at the push site must not have
        # changed where a `main` checkout publishes. Its commit is origin's `main`, tip for
        # tip — the same assertion the old bare `git push` would have satisfied.
        self.assertEqual(_git(bare, "rev-parse", "refs/heads/main")[1],
                         _git(work, "rev-parse", "HEAD")[1],
                         "an ordinary checkout no longer pushes to origin/main")


class WaitingTest(ChannelCase):
    """What the trunk can SEE of the branch — the devbox half of the wave.

    The outbound leg is only half a delivery. A receipt published on `runner/mail` has left
    the Runner and reached `origin`, and if nothing on devbox ever reads that branch it has
    still arrived nowhere anybody looks, which is the same outcome as never leaving. The
    difference from the original failure is only that this one is tidy. `waiting` is the
    read that makes it visible, so what it must never do is answer with silence for the
    wrong reason: no branch yet, a path it should not have counted, or a file the trunk
    already has all have to be distinguishable from "nothing is waiting"."""

    def trunk_that_can_see_the_branch(self):
        """(bare, trunk checkout with `origin/runner/mail` fetched)."""
        bare, seed = self.origin_with_a_release()
        self.assertTrue(channel.publish(
            {"outbox/to-sample-svc-arch/new.md": "for a member\n",
             "outbox/delivered/filed.md": "already filed\n"},
            "receipt: two files", "b\n", origin=str(bare), log=self.log.append),
            self.log)
        _git(seed, "fetch", "--quiet", "origin")
        return bare, seed

    def test_no_branch_on_origin_is_an_EMPTY_LIST_not_an_error(self):
        """The normal state until a sealed machine first publishes, and the state on every
        machine in the fleet that is not the Runner. It runs on the SessionStart path, so
        "there is no such branch" has to be an ordinary answer rather than a stack trace."""
        _bare, seed = self.origin_with_a_release()
        self.assertEqual(channel.waiting(seed), [])

    def test_it_lists_what_the_branch_carries_and_the_trunk_lacks(self):
        bare, seed = self.trunk_that_can_see_the_branch()
        self.assertEqual(channel.waiting(seed),
                         [("sample-svc-arch", "outbox/to-sample-svc-arch/new.md")])

    def test_a_brief_the_trunk_ALREADY_HAS_is_not_counted_again(self):
        """The crying-wolf half. A startup line that keeps reporting a brief the trunk
        already carries is the WI-0235 shape: a count that never clears trains its reader
        to stop reading it, and then the one that matters goes past unseen."""
        bare, seed = self.trunk_that_can_see_the_branch()
        # A second brief, published on the branch AND already sitting in the trunk's tree —
        # which is what every brief looks like once it has been landed.
        self.assertTrue(channel.publish({"outbox/to-y/already.md": "the trunk has this\n"},
                                        "receipt: landed", "b\n", origin=str(bare),
                                        log=self.log.append), self.log)
        _git(seed, "fetch", "--quiet", "origin")
        landed = seed / "outbox" / "to-y" / "already.md"
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_text("the trunk has this\n", encoding="utf-8")

        got = channel.waiting(seed)

        self.assertNotIn("outbox/to-y/already.md", [p for _aid, p in got])
        self.assertEqual(got, [("sample-svc-arch", "outbox/to-sample-svc-arch/new.md")],
                         "the one genuinely waiting brief was lost along with the noise")

    def test_a_path_outside_a_recipient_queue_is_not_waiting_mail(self):
        """`outbox/delivered/` is the sender's own filing, not a queue — `_queued` has kept
        it out of the delivery path since it was written, for the same reason: it is not
        addressed to anybody. A surface that counted it would report the archive as
        undelivered mail forever."""
        _bare, seed = self.trunk_that_can_see_the_branch()
        self.assertEqual([p for _aid, p in channel.waiting(seed)
                          if not p.startswith("outbox/to-")], [])

    def test_it_reaches_no_network(self):
        """It runs on the SessionStart path, where a `git fetch` against an unreachable
        remote does not fail but HANGS — on DNS or a credential prompt — and takes the
        session's startup with it. Same contract as `data_root`, same proof."""
        _bare, seed = self.trunk_that_can_see_the_branch()
        seen = []
        real = channel._git

        def spy(repo, args, timeout=300):
            seen.append(list(args))
            return real(repo, args, timeout=timeout)

        with mock.patch.object(channel, "_git", spy):
            channel.waiting(seed)

        verbs = {a[0] for a in seen if a}
        self.assertEqual(verbs & {"fetch", "clone", "push", "pull", "ls-remote"}, set(),
                         f"waiting() reached for the network: {seen}")


class AuditStatusLineTest(unittest.TestCase):
    """`deliver.audit_status_line()` — the one surface on devbox that says so.

    It is a SessionStart hook, which makes it both the right place for this and the most
    dangerous one. Right, because it is the actor that demonstrably runs on the machine the
    work happens on. Dangerous, because an exception here costs every session its startup
    banner — so the channel read is wrapped, and the wrapping is worth a test of its own:
    the failure mode of an unguarded read is not "no channel clause", it is "no line, in
    every session, until someone connects the two"."""

    def _line(self, waiting_result=None, waiting_raises=False):
        """The status line with everything but the channel read silenced.

        Every other input is mocked flat so the assertion is about the channel clause and
        nothing else — and, just as importantly, so the test does not read this machine's
        real roster, mailboxes or `origin/runner/mail` and report on the operator."""
        if waiting_raises:
            def waiting(_repo, *_a, **_k):
                raise RuntimeError("the branch could not be read")
        else:
            def waiting(_repo, *_a, **_k):
                return list(waiting_result or [])

        with mock.patch.object(deliver, "audit", return_value=([], [])), \
             mock.patch.object(deliver, "reachability", return_value=[]), \
             mock.patch.object(deliver, "_registry_staleness", return_value=""), \
             mock.patch.object(deliver.channel, "waiting", waiting):
            return deliver.audit_status_line()

    def test_the_clause_appears_and_counts_what_is_waiting(self):
        line = self._line([("sample-svc-arch", "outbox/to-sample-svc-arch/a.md"),
                           ("demo-member-arch", "outbox/to-demo-member-arch/b.md")])
        self.assertIn("2 waiting on the Runner channel", line)

    def test_nothing_waiting_does_not_invent_a_clause(self):
        """A startup line that speaks when there is nothing to say is a line people learn
        to skip, and it takes the real warnings with it."""
        self.assertEqual(self._line([]), "")

    def test_a_RAISING_channel_read_does_not_cost_the_session_its_banner(self):
        """The whole audit line is what is at stake, not the clause. An unguarded read here
        turns any bad state on the channel — a corrupt ref, an unreadable object, a git
        that is not on PATH — into every session starting with no delivery signal at all,
        which is the silence this wave exists to end, arriving by a new road."""
        self.assertEqual(self._line(waiting_raises=True), "",
                         "a failing channel read broke the whole status line")


if __name__ == "__main__":
    unittest.main()
