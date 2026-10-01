#!/usr/bin/env python3
"""The Runner channel — the one clone a resident machine is allowed to write.

WHY THIS EXISTS AT ALL. Every other system on the Runner runs from a sealed tree under
`~/deploy/<system>`, checked out at a tag and written by nothing. The federation was the
exception in two ways at once: it ran from a live trunk clone, and its own unattended jobs
(`curate/outbox.publish`, `deploy/runner.escalate`, the mail-poller's persist) committed
INTO that clone and then tried to publish. Every such write that landed after devbox had
moved the trunk left the clone one ahead and N behind — it could neither fast-forward nor
push — and each one needed a human to clear it. That is the class this module ends.

Sealing the process root (ADR-0103's deploy tree) fixes the first half and BREAKS the
second, which is the trap: a deploy tree is a DETACHED checkout at a tag with no upstream,
so `publish()` there commits onto a detached HEAD that nothing will ever push, and
`refresh()` there can never fast-forward `@{u}` because there is no `@{u}`. Receipts would
stop travelling and inbound mail to the Runner-resident members would stop arriving, both
silently, both reported in the logs as a benign "no upstream". The channel is the write and
read surface that replaces the trunk clone the sealing takes away.

THE SHAPE, and every part of it is load-bearing.

  * ONE CLONE, at `~/deploy/federation-channel`. It is neither a process root (nothing
    execs from it) nor a trunk clone (nobody develops in it). Nothing but this module and
    the poller touches it.

  * ONE BRANCH WE WRITE, `runner/mail`, with exactly ONE writer in the fleet. That is what
    makes the strand impossible BY CONSTRUCTION rather than by retry: a branch only one
    machine ever commits to is always ahead of its own remote tip, so its push always
    fast-forwards. The rebase-on-reject recovery in `outbox.publish` is a correct answer to
    a shared branch and is simply never needed here — the rejection cannot happen.

  * WE NEVER KEEP A LOCAL `main`. There is no local trunk in this clone to fall behind, so
    there is no fast-forward to refuse and nothing to diverge. devbox's trunk is read
    through `origin/main`, which a fetch advances unconditionally.

  * `origin/main` IS MERGED INTO `runner/mail`, not the other way round. This is the part
    that looks wrong and is the reason the design is small. The working tree has to carry a
    CURRENT view of `outbox/` and `mailboxes.json` for the poller to deliver from — the
    tag's frozen copy is exactly the stale data the sealing would otherwise introduce — and
    merging the trunk in is how it gets one while staying push-able: the push only requires
    `runner/mail` to fast-forward from ITS OWN remote tip, which it always does, whatever
    merges it absorbed on the way. devbox then takes the files back with `land()`, which is
    file-level and pathspec'd rather than a merge — see that function for why the narrower
    verb is the right one even though the merge would also be clean.

REJECTED, and why, so the next reader does not re-derive them:

  * An ORPHAN `runner/mail` carrying only the mail payload. Push-able for the same reason,
    and it cannot carry a current `outbox/`/`mailboxes.json` for the inbound leg, so the
    poller would need a second mechanism to read the trunk. Two mechanisms where one does.
  * WRITING BY PLUMBING (`hash-object` / `mktree` / `commit-tree` / `update-ref`) against a
    clone checked out on a `main` mirror. Works, needs no second tree, and is unreadable at
    03:15 by whoever is holding the failure — this code's readers are the people it wakes.
  * A LOCAL `main` HARD-RESET to `origin/main` each cycle. Fine until the day a reset drops
    mail that was committed and not yet landed on the trunk, which is silent data loss in
    the one program whose job is not losing mail.

WHAT REFUSES, rather than guessing. A write from a machine that is not the declared writer
is refused (`WRITER`): two writers on a one-writer branch is the strand coming back wearing
the channel's name, and it would be found the way the first one was — by hand, hours later.
A machine that cannot name itself is refused too, for `curate/common.this_machine`'s reason:
not knowing where you are standing is a reason to WITHHOLD a write, never to take one.
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import common                                                        # noqa: E402

#: The branch the resident machine writes. Namespaced so it can never be mistaken for a
#: trunk and can never be pushed by a `git push` that guesses a default.
BRANCH = "runner/mail"

#: The machine label allowed to write `BRANCH`. Overridable ONLY so the suite can exercise
#: the write path without pretending to be the Runner — the same seam every production path
#: in `deploy/runner.py` carries, and for the same stated reason: a suite that can reach a
#: production path will eventually reach it.
WRITER_ENV = "POGA_CHANNEL_WRITER"
DEFAULT_WRITER = "Runner"


def writer_label() -> str:
    return os.environ.get(WRITER_ENV) or DEFAULT_WRITER


def channel_root() -> pathlib.Path:
    """Where the channel clone lives on this machine.

    Beside the deploy trees rather than inside one: it outlives any single tag, and a
    deploy runs `checkout --force --detach` over its tree, so a channel kept in there would
    exist right up until the first moment anyone wanted it — `pre_cutover_dir`'s argument,
    one directory over."""
    env = os.environ.get("POGA_CHANNEL_ROOT")
    if env:
        return pathlib.Path(env)
    root = os.environ.get("POGA_DEPLOY_ROOT")
    base = pathlib.Path(root) if root else pathlib.Path.home() / "deploy"
    return base / "federation-channel"


def _git(repo, args, timeout=300):
    """One git command with an ARGUMENT LIST, never an interpolated string
    ([`enforce-the-data-code-boundary-where-it-exists`]). Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, "", str(e)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _git_blob(repo, ref_path):
    """One file's EXACT bytes out of a ref, as text. (rc, content).

    Separate from `_git` because `_git` strips its stdout, which is right for a branch name
    or a URL and silently CORRUPTS a file: it eats leading whitespace and every trailing
    blank line. A brief is content, not a value, and a transport that quietly reshapes what
    it carries is worse than one that drops it -- the dropped one gets noticed."""
    try:
        p = subprocess.run(["git", "-C", str(repo), "show", ref_path],
                           capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def is_deploy_tree(repo) -> bool:
    """Is `repo` a sealed deploy checkout rather than a working clone?

    THE TEST IS A DETACHED HEAD, and it is the right one because it is the same fact the
    hazard is made of. `checkout --force --detach <tag>` is how every deploy tree in the
    fleet is put at its version, and a detached HEAD is precisely the state in which a
    commit belongs to no branch, has no upstream, and is unreachable the moment the next
    deploy checks a different tag out over it. Testing the PATH instead (`is it under
    ~/deploy?`) would ask about a convention; this asks about the property that bites.

    A lane, the trunk, and a developer's clone all sit on a symbolic ref and answer False,
    which is why nothing changes for them. A non-repo answers False as well: `git
    symbolic-ref` fails there too, so the check is anchored on `rev-parse` first — treating
    "not a git repository at all" as "deploy tree" would route a test fixture's writes into
    a channel nobody asked for."""
    repo = pathlib.Path(repo)
    if _git(repo, ["rev-parse", "--git-dir"], timeout=30)[0] != 0:
        return False
    return _git(repo, ["symbolic-ref", "-q", "HEAD"], timeout=30)[0] != 0


def refuse_write_reason() -> str:
    """Why this machine may not write the channel, or "" when it may."""
    here = common.this_machine()
    want = writer_label()
    if not here:
        return ("this machine cannot name itself (`scutil --get ComputerName` gave "
                "nothing the machine map knows), and a one-writer branch cannot be "
                "written by a machine that does not know whether it is the writer")
    if here != want:
        return (f"this machine is {here!r} and {BRANCH!r} has exactly one writer, "
                f"{want!r}. Two writers is the stranded-commit class coming back under a "
                f"new name")
    return ""


def ensure(origin: str, log=print) -> pathlib.Path | None:
    """Make the channel clone exist, current, and checked out on `BRANCH`. Path or None.

    CREATED BY THE RUNNER, NEVER BY A PERSON. The brief this implements forbids asking operator
    for a step on the Runner — "if the wave needs one, the wave is wrong" — so the first
    cutover has to be able to make its own write surface. `origin` is taken from the tree
    that is already a clone of it, so this needs no new credential and no new configuration.

    EVERY FAILURE RETURNS None AND IS LOGGED, never raised. Callers are a deploy that has
    already happened or a delivery that has already landed; a channel problem must not turn
    a completed act into a reported failure. The caller's own refusal path then says what
    was not published, which is the loud outcome — silence is what this replaces."""
    root = channel_root()
    if not (root / ".git").exists():
        if not origin:
            log("channel: no origin URL to clone from — the channel was not created")
            return None
        root.parent.mkdir(parents=True, exist_ok=True)
        rc, _, err = _git(root.parent, ["clone", "--quiet", origin, root.name], timeout=900)
        if rc != 0:
            log(f"channel: could not clone {origin} → {root} "
                f"({err.splitlines()[0] if err else 'no detail'})")
            return None
        log(f"channel: cloned {origin} → {root}")

    if _git(root, ["fetch", "--quiet", "origin"], timeout=600)[0] != 0:
        # NOT fatal. A fetch that fails leaves the tree exactly as it was, and writing a
        # receipt onto a slightly stale `runner/mail` still publishes it — the branch is
        # ours alone, so the push does not care how old our copy of the trunk is.
        log("channel: fetch failed — working from the branch already on disk")

    rc, head, _ = _git(root, ["symbolic-ref", "--short", "-q", "HEAD"])
    if rc != 0 or head != BRANCH:
        # `origin/runner/mail` on the first ever write does not exist; fork it from the
        # trunk so the branch's history contains main from its first commit, which is what
        # makes devbox's landing merge trivial.
        start = f"origin/{BRANCH}"
        if _git(root, ["rev-parse", "--verify", "--quiet", start])[0] != 0:
            start = "origin/main"
        # `--no-track` IS LOAD-BEARING. Git's default `branch.autoSetupMerge` makes a branch
        # created from a remote-tracking start point track THAT ref — so `-B runner/mail
        # origin/main` silently sets our upstream to `origin/main`. Everything downstream
        # then goes wrong quietly: the `@{u}` probe below succeeds, so the first-use push
        # that CREATES `origin/runner/mail` never runs; and `outbox.publish`'s ordinary path,
        # which is what runs inside this clone, does a bare `git push` that `push.default=
        # simple` refuses because the branch name and its upstream name differ. The receipt
        # is then committed and stranded in the channel — this module's own failure class,
        # moved one directory over. Found by the gate tests for this change.
        rc, _, err = _git(root, ["checkout", "--quiet", "-B", BRANCH, "--no-track", start])
        if rc != 0:
            log(f"channel: could not check out {BRANCH} from {start} "
                f"({err.splitlines()[0] if err else 'no detail'})")
            return None
        log(f"channel: {BRANCH} checked out from {start}")

    # SET THE UPSTREAM ON FIRST USE, and this is what makes the rest of the fleet need no
    # channel-specific code at all. With `origin/runner/mail` tracked, the channel clone is
    # an ORDINARY clone on an ordinary branch: `curate/outbox.py`'s existing add/commit/push
    # works here unchanged, and the only thing that had to change anywhere was WHICH
    # DIRECTORY the mail data lives in. A branch with no upstream would send that same code
    # down its "committed, not pushed" path — the exact silence this module exists to end.
    rc, upstream, _ = _git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name",
                                  "@{u}"])
    # NOT "is there an upstream" but "is it OURS". An upstream pointing anywhere but
    # `origin/runner/mail` is the bug above in any of its forms, and answering the weaker
    # question is what let it through: a wrong upstream reads as a present one.
    if rc != 0 or upstream != f"origin/{BRANCH}":
        if not refuse_write_reason():
            rc, _, err = _git(root, ["push", "--quiet", "-u", "origin",
                                     f"{BRANCH}:{BRANCH}"], timeout=600)
            if rc != 0:
                # A push that cannot create the branch leaves the upstream wrong, and a wrong
                # upstream is worse than none: the ordinary publish path would push somewhere
                # we did not mean. Unset it so that path reports "no upstream" — committed,
                # not pushed — which is honest and recoverable.
                _git(root, ["branch", "--unset-upstream"])
            log(f"channel: {BRANCH} published to origin and tracked" if rc == 0 else
                f"channel: could not publish {BRANCH} to origin "
                f"({err.splitlines()[0] if err else 'no detail'}) — writes stay local "
                f"until it can be pushed")

    _absorb_trunk(root, log)
    return root


def _absorb_trunk(root: pathlib.Path, log) -> bool:
    """Merge `origin/main` into `BRANCH` so the tree carries a CURRENT trunk.

    The inbound leg needs it: the poller delivers from `outbox/` and resolves through
    `mailboxes.json`, and reading either from a frozen tag is the staleness the sealing
    would otherwise introduce. Merging the trunk in rather than rebasing onto it is what
    keeps the push a fast-forward — see this module's header.

    A CONFLICT IS SURVIVABLE AND IS ABORTED. The Runner writes only under `outbox/to-*` and
    `comms/`, so a conflict means something unmodelled happened; carrying on against the
    tree already on disk is still correct (a queued brief is delivered from what is staged
    here) and is strictly better than leaving a half-merged tree for the next scheduled job
    to run — `outbox.publish`'s rebase-abort, for its reason."""
    if _git(root, ["rev-parse", "--verify", "--quiet", "origin/main"])[0] != 0:
        return False
    rc, out, _ = _git(root, ["status", "--porcelain"])
    if rc != 0 or out:
        log("channel: tree is dirty — not absorbing the trunk this cycle")
        return False
    rc, _, err = _git(root, ["-c", "user.name=poga substrate",
                             "-c", "user.email=substrate@localhost",
                             "-c", "commit.gpgsign=false",
                             "merge", "--quiet", "--no-edit", "origin/main"])
    if rc != 0:
        _git(root, ["merge", "--abort"])
        log(f"channel: could not absorb origin/main "
            f"({err.splitlines()[0] if err else 'no detail'}) — ABORTED, tree unchanged")
        return False
    return True


def publish(files: dict, subject: str, body: str, origin: str = "", log=print) -> bool:
    """Write `files` (repo-relative path → text) onto `BRANCH` and push. True if pushed.

    This is the whole outbound leg. `outbox.publish` and `runner.escalate` route here when
    the tree they were about to commit into is sealed, and they REFUSE rather than fall
    back to committing on a detached HEAD — a commit nothing can ever push is not a delayed
    publish, it is a lost one that logs success."""
    why = refuse_write_reason()
    if why:
        log(f"channel: REFUSED to write {BRANCH} — {why}. Nothing was committed.")
        return False
    root = ensure(origin, log=log)
    if root is None:
        return False

    rel = []
    for path, text in files.items():
        p = pathlib.Path(path)
        if p.is_absolute() or ".." in p.parts:
            log(f"channel: REFUSED {path!r} — the channel writes repo-relative paths only")
            return False
        dest = root / p
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
        except OSError as e:
            log(f"channel: could not write {path} — {e}")
            return False
        rel.append(str(p))

    # PATHSPEC'D to what we were handed (ADR-0091): whatever else the absorbed trunk
    # brought in is not this call's to commit.
    if _git(root, ["add", "--", *rel])[0] != 0:
        log(f"channel: could not stage {', '.join(rel)}")
        return False
    rc, staged, _ = _git(root, ["diff", "--cached", "--name-only", "--", *rel])
    if rc != 0 or not staged:
        # ALREADY CARRIED, AND THAT IS A SUCCESS. The writer's local copy survives in its own
        # tree until the next deploy overwrites it, so the steady state is that a later cycle
        # offers the same brief again and the branch already has it byte-for-byte. Reporting
        # that as a failure would print a REFUSAL every ten minutes about mail that arrived —
        # the false alarm that gets a guard switched off. Published is published.
        return True
    rc, _, err = _git(root, ["-c", "user.name=poga substrate",
                             "-c", "user.email=substrate@localhost",
                             "-c", "commit.gpgsign=false",
                             "commit", "--quiet", "-m", f"{subject}\n\n{body}\n"])
    if rc != 0:
        log(f"channel: commit failed ({err.splitlines()[0] if err else 'no detail'}) — the "
            f"files are staged in {root} and the next write commits them")
        return False
    rc, _, err = _git(root, ["push", "--quiet", "origin", f"{BRANCH}:{BRANCH}"], timeout=600)
    if rc != 0:
        # A one-writer branch cannot be rejected for non-fast-forward, so this is the
        # network or the remote, not a race. The commit is on the branch and the NEXT push
        # carries it — which is the difference between this and the trunk clone it
        # replaces: there, the commit blocked every later refresh; here it just waits.
        log(f"channel: push failed ({err.splitlines()[0] if err else 'no detail'}) — the "
            f"commit is on {BRANCH} in {root} and the next publish pushes it too")
        return False
    log(f"channel: published {len(rel)} file(s) on {BRANCH}")
    return True


def rebind_data_roots(root, log=print) -> list:
    """Re-point the already-imported mail modules at `root`. Returns what was moved.

    THE ONE-CYCLE HOLE THIS CLOSES, and it exists because `data_root` is deliberately
    side-effect-free. `curate/outbox.py` and `curate/deliver.py` resolve their roots AT
    IMPORT, and on the very first run after a machine is sealed the channel does not exist
    yet — `ensure()` is what creates it, and by then those two have already answered with
    the deploy tree. That run would then deliver from the TAG's frozen queue and resolve
    recipients against the tag's roster, report a completely healthy cycle, and self-heal ten
    minutes later. A ten-minute window that fixes itself is exactly the kind of defect this
    whole wave is about: it is invisible, it is survivable, and it teaches everyone that the
    logs mean what they say.

    Rebinding module globals is action at a distance and is chosen with that cost in view.
    The alternative is resolving these roots lazily on every access, which touches the two
    modules the rest of the fleet reads most and would put a `git` call behind an attribute
    lookup. This is one explicit, named, logged call, made at the single moment the answer is
    known to have changed."""
    moved = []
    for name, attrs in (("outbox", ("DATA_ROOT", "OUTBOX", "DELIVERED")),
                        ("deliver", ("MAILBOXES",))):
        mod = sys.modules.get(name)
        if mod is None:
            continue
        before = getattr(mod, attrs[0], None)
        if name == "outbox":
            mod.DATA_ROOT = pathlib.Path(root)
            mod.OUTBOX = mod.DATA_ROOT / "outbox"
            mod.DELIVERED = mod.OUTBOX / "delivered"
        else:
            mod.MAILBOXES = pathlib.Path(root) / "mailboxes.json"
        if str(before) != str(getattr(mod, attrs[0])):
            moved.append(name)
    if moved:
        log(f"channel: re-pointed {', '.join(moved)} at {root} — the channel was created "
            f"after they resolved their roots, and this run would otherwise have worked "
            f"from the tag's frozen copy")
    return moved


def origin_of(repo) -> str:
    """`repo`'s origin URL, or "" — the URL to clone the channel from."""
    rc, url, _ = _git(repo, ["remote", "get-url", "origin"], timeout=30)
    return url if rc == 0 else ""


def data_root(default) -> pathlib.Path:
    """Compatibility accessor; production state is explicit, never the transport clone."""
    import production
    roots = production.resolve(default)
    return roots.state_root if roots else pathlib.Path(default)

#: What a resident machine is allowed to send, and therefore all the land will carry back.
#: A prefix list rather than "whatever changed" for the reason every unattended commit in
#: this repo is pathspec'd (ADR-0091): the branch is transport, and transport that can carry
#: arbitrary paths is a way to land code without a gate.
LANDABLE_PREFIXES = ("outbox/", "comms/")


def _landable(name: str) -> bool:
    return any(name.startswith(pre) for pre in LANDABLE_PREFIXES) and ".." not in name


def land(repo, log=print) -> int:
    """Bring the channel's files back onto the trunk. Returns the number landed.

    THE RETURN LEG OF THE RETURN LEG. The Runner's receipts, escalations and queued mail are
    published on `runner/mail`, which nothing on devbox reads. Without this they accumulate
    on a branch forever, which is a tidier failure than a stranded commit and still a
    failure: the mail has left the Runner and not arrived.

    FILE-LEVEL, NOT A MERGE, and that is a deliberate narrowing. `runner/mail` contains main
    (see `_absorb_trunk`), so `git merge runner/mail` would also be clean -- and it would let
    the branch carry ANY path into the trunk without passing a gate, and it would create a
    merge whose parent is a branch a different machine moves. Copying the files the trunk
    does not have, under two declared prefixes, can do neither: it cannot move `main`
    backwards, it cannot land code, and what it did is legible in one commit.

    CONSERVATIVE ABOUT WHEN, the same way `mail-poller.refresh` is. This runs unattended in a
    checkout a human may be working in, so it declines on a dirty tree, a detached head, or a
    branch that is not the trunk, and never overwrites a file the trunk already has -- a
    brief the trunk carries is one that already arrived, and a delivered brief that gets
    re-written is a delivery that happens twice."""
    repo = pathlib.Path(repo)
    rc, head, _ = _git(repo, ["symbolic-ref", "--short", "-q", "HEAD"])
    if rc != 0 or head != "main":
        return 0
    rc, dirty, _ = _git(repo, ["status", "--porcelain"])
    if rc != 0 or dirty:
        return 0
    if _git(repo, ["fetch", "--quiet", "origin", BRANCH], timeout=600)[0] != 0:
        return 0
    ref = f"origin/{BRANCH}"
    if _git(repo, ["rev-parse", "--verify", "--quiet", ref])[0] != 0:
        return 0
    rc, listing, _ = _git(repo, ["ls-tree", "-r", "--name-only", ref, *LANDABLE_PREFIXES])
    if rc != 0:
        return 0

    landed = []
    for name in listing.splitlines():
        name = name.strip()
        if not name or not _landable(name):
            continue
        dest = repo / name
        if dest.exists():
            continue
        rc, blob = _git_blob(repo, f"{ref}:{name}")
        if rc != 0:
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(blob, encoding="utf-8")
        except OSError as e:
            log(f"channel: could not land {name} — {e}")
            continue
        landed.append(name)
    if not landed:
        return 0

    if _git(repo, ["add", "--", *landed])[0] != 0:
        log(f"channel: could not stage {len(landed)} landed file(s)")
        return 0
    rc, _, err = _git(repo, ["-c", "user.name=poga substrate",
                             "-c", "user.email=substrate@localhost",
                             "-c", "commit.gpgsign=false",
                             "commit", "--quiet", "-m",
                             f"chore(channel): land {len(landed)} file(s) from {BRANCH}"
                             f"\n\nCarried from the Runner on the one-writer channel "
                             f"(WI-0360, ADR-0135). File-level and pathspec'd to "
                             f"{', '.join(LANDABLE_PREFIXES)} — the channel is transport, "
                             f"and transport that can carry any path is a way to land code "
                             f"without a gate.\n"])
    if rc != 0:
        log(f"channel: could not commit the landed files "
            f"({err.splitlines()[0] if err else 'no detail'})")
        return 0
    log(f"channel: landed {len(landed)} file(s) from {BRANCH} onto the trunk")
    return len(landed)


def waiting(repo, log=print) -> list:
    """[(architect_id, path-on-the-branch)] queued on the channel that the trunk lacks.

    A READ, NEVER A WRITE, and no network: it answers from the `origin/runner/mail` the last
    fetch brought down. Session start already fetches, so on devbox this is current without
    anything new having to run.

    WHY A READ RATHER THAN A LAND. The Runner's receipts are published on a branch that is
    on `origin`, which is exactly as durable as `main` for provenance — the reason to fold
    them onto the trunk is history, not delivery, and that is a later lane's call. What is
    NOT optional is that a brief waiting there be VISIBLE: mail that left the Runner and
    arrived nowhere anyone looks is the same outcome as mail that never left, and the whole
    point of this wave is that outcome stops happening silently.

    EMPTY IS THE NORMAL ANSWER and is not an error — the branch does not exist at all until
    a sealed machine first publishes on it."""
    repo = pathlib.Path(repo)
    ref = f"origin/{BRANCH}"
    if _git(repo, ["rev-parse", "--verify", "--quiet", ref])[0] != 0:
        return []
    rc, listing, _ = _git(repo, ["ls-tree", "-r", "--name-only", ref, "outbox/"])
    if rc != 0:
        return []
    out = []
    for name in listing.splitlines():
        name = name.strip()
        if not name.startswith("outbox/to-") or not _landable(name):
            continue
        if (repo / name).exists():
            continue                      # the trunk already carries it
        aid = name[len("outbox/to-"):].split("/", 1)[0]
        out.append((aid, name))
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(
        prog="channel", description="The Runner channel (ADR-0135).")
    ap.add_argument("--land", action="store_true",
                    help="Land what the channel carries onto this trunk checkout.")
    ap.add_argument("--status", action="store_true",
                    help="Say what this checkout is and where its mail lives.")
    ap.add_argument("--waiting", action="store_true",
                    help="List briefs queued on the channel that this trunk does not have.")
    ap.add_argument("--repo", default=None, help="Checkout to act on (default: this one).")
    args = ap.parse_args(argv)
    repo = pathlib.Path(args.repo) if args.repo else pathlib.Path(__file__).resolve().parent.parent
    if args.land:
        return 0 if land(repo) >= 0 else 1
    if args.waiting:
        rows = waiting(repo)
        for aid, name in rows:
            print(f"{aid}\t{name}")
        if not rows:
            print(f"nothing waiting on {BRANCH} that this trunk lacks")
        return 0
    sealed = is_deploy_tree(repo)
    print(f"repo:    {repo}")
    print(f"sealed:  {sealed} ({'detached at a tag — writes go to the channel' if sealed else 'on a branch — writes go here, unchanged'})")
    print(f"channel: {channel_root()} ({'present' if (channel_root() / '.git').exists() else 'not created'})")
    print(f"mail:    {data_root(repo)}")
    print(f"writer:  {writer_label()}; this machine: {common.this_machine() or '(cannot tell)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
