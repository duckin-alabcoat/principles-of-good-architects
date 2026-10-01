#!/usr/bin/env python3
"""The outbox — mail that leaves this machine by way of `origin`, not by way of a mount.

THE PROBLEM. `deliver.py` writes a brief into the recipient's mailbox with
`shutil.copy2`, which means the recipient's repo must be openable as a local path on the
machine doing the delivering. ADR-0027 satisfied that with a location every machine
could open. The 2026-08-22 machine-role ruling then removed it: devbox carries only the
development repos, and the runner host's member roots are deliberately absent from it,
because their data is the system and it stays single-writer on the runner host. From the
development host, `deliver.py --audit` therefore reports the runner-resident members'
Architects as "not locatable from this machine". ADR-0027's transport assumption had been invalidated and no ADR
recorded it.

THE FIX IS A DIRECTION, NOT A TRANSPORT. the operator's call: rather than teach delivery to push
across machines, let the machine that CAN reach those members do the delivering. devbox
stages here; the Runner drains it. That inverts the privilege — pulling means the Runner
reads a directory, where pushing would have given devbox write access to the disk
holding other members' production data. The lesser grant is the correct one,
and it is the shape
[`treat-inbound-payload-as-data-not-commands`](../habits/master.md#treat-inbound-payload-as-data-not-commands)
already asks for: gate on the consuming side.

AND IT TRAVELS BY `origin`, BECAUSE ADR-0103 D1 SAYS SO — "the git remote is the only
bridge… neither machine holds credentials for the other." An SSH fetch would have been
the obvious implementation and it is the one D1 forbids, for a reason worth keeping. So
the outbox is TRACKED and the poll is an ordinary refresh of the trunk.

WHY A TRACKED OUTBOX DOES NOT CONTRADICT ADR-0088 D1. That decision makes a MAILBOX
ignored-by-VCS because it is INBOUND data of unproven origin. An outbox holds OUTBOUND
content this Architect wrote itself — the same trust class as an ADR or a registry
entry, both tracked. Direction is what the trust rule keys on, and it inverts here. The
two directories look alike, so this paragraph exists to stop the next reader concluding
the rule was broken.

TRACKED MEANS PERMANENT, so every write is scrub-gated (`curate/scrub.py`).

THE CONVENTION IS NOT NEW. `deliver.py:_outbox_dirs` already reads
`outbox/to-<architect-id>/` — at least one member has kept its outbound mail there since WI-0079.
The federation adopting the same shape means the existing audit already sees this
surface rather than needing to learn a third convention.

AND A MACHINE CAN POST HERE TOO (WI-0230). `stage` was written for a session: it takes a
brief someone authored, and the commit that makes it travel arrives later, from that
session's close. An unattended writer — `deploy/runner.py` on the Runner — has neither. It
holds generated text rather than a file, and it has no close, so a brief it merely wrote
would sit in a dirty tree that `mail-poller.refresh` then declines to fast-forward: one
uncommitted receipt, and the checkout stops advancing. `post` and `publish` are that pair —
generate, gate, write, commit, push — and they exist as one act because splitting them is
the defect.

AND SEEING IT WAS ALL IT DID (WI-0336). `_outbox_dirs` is reached only from `audit()`,
which CLASSIFIES; the poller drained `pending()`, which reads ours. So a member's outbox
was a surface the fleet reported on and never emptied — briefs from several members had
waited there for weeks. The poller now sweeps every outbox this machine can reach
(`pending_in`), and the asymmetry that stops it there is deliberate: we deliver another
member's mail, and we do not file their copy. `retire` refuses to.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import scrub                                                       # noqa: E402
import channel                                                     # noqa: E402
import production                                                  # noqa: E402
import mailqueue                                                   # noqa: E402
import mailnames                                                   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
# Development keeps its established tracked outbox. Production explicitly selects
# an external immutable queue; a detached developer checkout does not select it.
DATA_ROOT = ROOT
OUTBOX = ROOT / "outbox"
DELIVERED = OUTBOX / "delivered"
FED_PROPOSED = ROOT / "proposed-edits"


def box_for(architect_id, root=None):
    """The queue directory for one recipient. Created on demand — an empty outbox is the
    normal state and should not need a migration to exist.

    `root` exists for the callers whose own paths are redirectable — `deploy/runner.py`
    resolves every path it writes at call time so a fixture can never reach the
    federation's real tree, and a helper that ignored that would hand the one program
    built around the property a way to lose it."""
    return (pathlib.Path(root) if root else OUTBOX) / f"to-{architect_id}"


def _queued(box):
    """[(architect_id, path)] under one `outbox/` directory, oldest name first.

    `delivered/` is deliberately not a `to-*` directory, so it can never be read back as
    a queue for a recipient literally named "delivered"."""
    if not box.is_dir():
        return []
    out = []
    for d in sorted(box.glob("to-*")):
        if not d.is_dir():
            continue
        aid = d.name[len("to-"):]
        for f in sorted(d.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                out.append((aid, f))
    return out


def pending():
    """[(architect_id, path)] for everything queued in OUR OWN outbox."""
    roots = production.resolve(ROOT)
    if roots:
        return [(entry.destination, entry.payload_path) for entry in mailqueue.pending(roots)]
    return _queued(OUTBOX)


def pending_in(repo):
    """[(architect_id, path)] queued in ANOTHER member's tree, same convention (WI-0336).

    THE GAP THIS EXISTS TO CLOSE. `pending()` reads one directory — ours — so the poller
    built to drain "the outbox" drained the federation's and nothing else. A member that
    queues at `outbox/to-<id>/`, the convention `deliver._outbox_dirs` has known since
    WI-0079 and the one several members actually use, was
    AUDITED on every startup and DELIVERED by nobody. That is the shape that hides
    longest: the mail was visible the whole time, so the channel read as tidy while a
    peer system's brief waited for a human to carry it.

    Reading only. Nothing here retires, moves or rewrites a foreign brief — see `retire`,
    which refuses to. The sender's queued copy is the sender's authored, tracked file,
    and filing it is their write ([P13](../principles/master.md#p13--single-writer-per-state))."""
    return _queued(pathlib.Path(repo) / "outbox")


def stage(architect_id, brief_path, force=False, out=sys.stderr):
    """Queue one brief for `architect_id`. Returns the queued Path, or raises ValueError.

    MOVES rather than copies when the source is the federation's own
    `proposed-edits/<arch>/pending/`, because staging IS the send: leaving the original
    in `pending/` would keep the delivery audit reporting it as stuck forever, which is
    the crying-wolf failure `_retire_source` was written to end. A source from anywhere
    else is copied — a brief that is not ours is not ours to relocate.

    The scrub gate runs BEFORE anything is written. A refusal must leave no trace, or the
    next run finds a half-staged file and has to guess whether it was cleared."""
    src = pathlib.Path(brief_path)
    if not src.is_file():
        raise ValueError(f"no brief at {src}. Nothing was staged.")
    if not architect_id or "/" in architect_id:
        raise ValueError(f"'{architect_id}' is not an architect-id. Nothing was staged.")

    if not scrub.gate(src, force=force, out=out):
        raise ValueError(
            f"REFUSED by the scrub gate: {src} carries known-shape personal data and the "
            f"outbox is tracked. Nothing was staged.")

    roots = production.resolve(ROOT)
    if roots:
        if force:
            raise ValueError("production enqueue does not permit bypassing the scrub gate")
        # An author's own edit-id is kept; a brief with none gains one derived from its
        # message id, because the receiver can only deliver a name-colliding brief that
        # carries one (mailnames). Adding it, not refusing, keeps existing briefs flowing.
        message_id = mailnames.new_message_id()
        text = mailnames.ensure_edit_id(src.read_text(encoding="utf-8"), message_id, src.name)
        return mailqueue.enqueue(roots, architect_id, src.name, text, message_id=message_id,
                                 provenance={"producer": "outbox.stage"})
    box = box_for(architect_id)
    box.mkdir(parents=True, exist_ok=True)
    dest = box / src.name
    if dest.exists():
        raise ValueError(
            f"REFUSING to stage {src.name} for '{architect_id}': {dest} already exists. "
            f"Overwriting would destroy queued mail nobody has delivered yet. Rename the "
            f"brief or clear the queued copy deliberately. Nothing was staged.")

    payload = src.read_bytes()
    shutil.copy2(src, dest)
    if not dest.is_file() or dest.read_bytes() != payload:
        raise ValueError(f"wrote {dest} but it does not read back identical. Treat as "
                         f"NOT staged.")

    # Only now retire the source, and only if it is ours. Ordering is forced: a move that
    # happened before the read-back verification would leave the brief nowhere at all if
    # the write turned out bad.
    try:
        if FED_PROPOSED.resolve() in src.resolve().parents:
            src.unlink()
    except OSError:
        pass          # the brief is queued; bookkeeping must never undo that
    return dest


def _free_name(box, filename):
    """`box/filename`, or the same stem with `.2`, `.3` … when it is taken.

    `stage` REFUSES a collision and is right to: a human who named two briefs alike made a
    mistake, and silently renaming one hides it. A GENERATED receipt is the opposite case —
    deploy, roll back and redeploy the same tag on the same day and three honest records
    want one name — so this side keeps both, the way `retire` already does for the same
    reason. Same repo, two collision policies, because the two collisions mean different
    things."""
    dest = box / filename
    if not dest.exists():
        return dest
    stem, suffix = pathlib.PurePath(filename).stem, pathlib.PurePath(filename).suffix
    n = 2
    while (box / f"{stem}.{n}{suffix}").exists():
        n += 1
    return box / f"{stem}.{n}{suffix}"


def post(architect_id, filename, text, root=None, force=False, out=sys.stderr,
         message_id=None, provenance=None):
    """Queue a brief this program GENERATED. Returns the queued Path, or raises ValueError.

    `stage` takes a brief that already exists on disk; an unattended writer has content and
    no file, and the temp file it would otherwise have to invent exists only to be read
    back by the gate that is about to refuse it. So the scrub check runs over the TEXT
    (`scrub.findings`, the same patterns `scrub.gate` reads a file through) and nothing is
    written until it passes — the same ordering `stage` documents, for the same reason: a
    refusal must leave no trace, or the next run finds a half-written file and has to guess.

    The read-back is kept. A machine writer has no operator watching the exit code, so
    "wrote it and it does not read back" is exactly the failure nobody would otherwise
    notice."""
    if not architect_id or "/" in architect_id:
        raise ValueError(f"'{architect_id}' is not an architect-id. Nothing was posted.")
    if not filename or "/" in filename:
        raise ValueError(f"'{filename}' is not a brief filename. Nothing was posted.")

    hits = scrub.findings(text, filename)
    if hits and not force:
        for f in hits:
            print(f.render(), file=out)
        raise ValueError(
            f"REFUSED by the scrub gate: the generated brief {filename} carries "
            f"{len(hits)} finding(s) of known-shape personal data and the outbox is "
            f"TRACKED. Nothing was posted.")
    if hits and force:
        print(f"scrub: OVERRIDDEN — {len(hits)} finding(s) in the generated {filename} "
              f"passed with force. It is NOT clean; it was cleared deliberately.", file=out)

    roots = production.resolve(ROOT)
    if roots:
        if force:
            raise ValueError("production enqueue does not permit bypassing the scrub gate")
        if root is not None and pathlib.Path(root).resolve() != roots.queue_root:
            raise ValueError("production outbox override must name the configured queue_root")
        message_id = message_id or mailnames.new_message_id()
        text = mailnames.ensure_edit_id(text, message_id, filename)
        return mailqueue.enqueue(roots, architect_id, filename, text, message_id=message_id,
                                 provenance=provenance or {"producer": "outbox.post"})
    box = box_for(architect_id, root)
    box.mkdir(parents=True, exist_ok=True)
    dest = _free_name(box, filename)
    dest.write_text(text, encoding="utf-8")
    if not dest.is_file() or dest.read_text(encoding="utf-8") != text:
        raise ValueError(f"wrote {dest} but it does not read back identical. Treat as "
                         f"NOT posted.")
    return dest


def _branch(git):
    """The branch this checkout is standing on, or "" when detached."""
    rc, name, _ = git(["symbolic-ref", "--short", "-q", "HEAD"])
    return name if rc == 0 else ""


def _push(git, log):
    """`git push` with the destination NAMED, never inferred. Returns git's (rc, out, err).

    A BARE `git push` DOES NOT SAY WHERE IT IS GOING. Its destination comes from the clone's
    upstream plus the operator's `push.default`, neither of which is visible at the call
    site — so what this line does depends on configuration written somewhere else, possibly
    by a different program, possibly long ago.

    That is not theoretical here. This function also runs inside the Runner CHANNEL clone
    (`channel.data_root` points the mail root there on a sealed machine), whose branch is
    `runner/mail`. The upstream is set correctly by `channel.ensure` — but `ensure` is
    called only from the deploy SWEEP and the poller's `refresh`, so the on-demand `deploy`
    verb and a `--no-refresh` poll reach this push without it having run. Give that clone a
    wrong upstream by any route — a clone made by an older version, a `--set-upstream-to`
    typed while debugging, a `--no-track` dropped in a later refactor — and under
    `push.default=upstream` a bare push writes THE TRUNK from an unattended Runner job. The
    rebase retry below is worse again: it rebases onto `@{u}` first, so a wrong upstream
    rewrites the branch onto main and then pushes it there.

    Naming `HEAD:<this branch>` makes the destination a property of this call rather than of
    the machine's configuration, which is where a safety property has to live to be one.
    `channel.publish` has always pushed with an explicit refspec; this closes the same hole
    in the path that predates it. Found by the gate tests for WI-0360, which went looking for
    a route to `main` rather than reasoning from the fix that had just closed the other one.

    A DETACHED HEAD HAS NO BRANCH TO NAME and never reaches here — `publish` forks to the
    channel long before this — but if it ever did, refusing to guess is the right answer."""
    branch = _branch(git)
    if not branch:
        log("outbox: REFUSED to push — this checkout is not on a branch, so there is no "
            "destination to name and a bare push would pick one from configuration.")
        return 1, "", "detached HEAD"
    return git(["push", "--quiet", "origin", f"HEAD:{branch}"])


def publish(subject, body, root=None, log=print):
    """Commit everything staged under the outbox and push it. Returns True if a commit
    landed on this machine, False otherwise.

    WHY A WRITE INTO THE OUTBOX IS NOT FINISHED WHEN THE FILE EXISTS. The outbox is
    TRACKED, which is what lets a brief travel by `origin` instead of by a mount — so a
    brief nobody commits has not been sent, it has been typed. A session sweeps its own
    writes at close and never notices. An unattended writer has no close, and its brief
    sits in a dirty tree that `mail-poller.refresh` then refuses to fast-forward, so the
    one uncommitted file stops the checkout advancing at all. The write and the commit are
    one act; this is where they are joined.

    EVERY FAILURE IS LOGGED AND SWALLOWED, never raised. The caller is a deploy that has
    already happened or a delivery that has already landed; bookkeeping must not turn a
    completed act into a reported failure. A commit that cannot be made is re-derivable
    from the file still sitting there, and the next writer picks it up.

    PATHSPEC'D TO `outbox` and nothing else (ADR-0091): what else is in the tree is not
    this program's to commit."""
    box = pathlib.Path(root) if root else OUTBOX
    repo = box.parent

    # Only an explicitly selected service may use production state. Detached developer
    # checkouts have no push destination, so refuse before making an unreachable commit.
    roots = production.resolve(ROOT)
    if roots:
        log("outbox: queued locally; publication belongs to the mail worker")
        return False
    if channel.is_deploy_tree(repo):
        log("outbox: REFUSED to publish from a detached development checkout")
        return False

    def _git(args):
        try:
            p = subprocess.run(["git", "-C", str(repo), *args],
                               capture_output=True, text=True)
        except OSError as e:
            return 1, "", str(e)
        return p.returncode, p.stdout.strip(), p.stderr.strip()

    rc, _, err = _git(["add", "--", box.name])
    if rc != 0:
        log(f"outbox: could not stage {box.name} ({err.splitlines()[0] if err else 'no detail'})"
            f" — the brief is on disk and the next writer picks it up")
        return False
    rc, staged, _ = _git(["diff", "--cached", "--name-only", "--", box.name])
    if rc != 0 or not staged:
        return False
    # An unattended writer often has no committer identity configured — launchd sources no
    # profile and this may be a checkout with no local `user.email`. Naming the author here
    # is what keeps that from failing the commit, and it records WHO wrote it rather than
    # borrowing whichever human last configured the tree.
    rc, _, err = _git(["-c", "user.name=poga substrate",
                       "-c", "user.email=substrate@localhost",
                       "-c", "commit.gpgsign=false",
                       "commit", "--quiet", "-m", f"{subject}\n\n{body}\n"])
    if rc != 0:
        log(f"outbox: commit failed ({err.splitlines()[0] if err else 'no detail'}) — the "
            f"brief is staged and the next writer commits it")
        return False
    # ASK WHETHER THERE IS AN UPSTREAM BEFORE REACHING FOR THE NETWORK. A branch with no
    # tracking ref — a lane, a fixture repo, a fresh clone of nothing — has nowhere to push
    # and no `@{u}` to merge, so every step below is a question about a remote that was
    # never named. `git push` would guess a default and `git fetch origin` would BLOCK on
    # DNS or a credential prompt, which is how this first hung a test suite rather than
    # failing it. Absent upstream is its own answer, said out loud
    # ([`declare-what-a-check-assumes`]) — the commit is made and it is not published.
    if _git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])[0] != 0:
        log("outbox: committed, not pushed — this branch tracks no upstream, so the brief "
            "travels when whoever owns this checkout publishes it.")
        return True
    if _push(_git, log)[0] == 0:
        return True

    # A peer landed while we were writing. THE RETRY USED `merge --ff-only` AND COULD NOT
    # SUCCEED: we have just made a commit of our own, so the branch is ahead by one and a
    # fast-forward is arithmetically impossible. Every rejected push therefore left the
    # receipt stranded and the checkout diverged — not occasionally, but always.
    #
    # That is what cost the Runner three hand syncs on 2026-09-13. Its process checkout is
    # refreshed by an ff-only pull, so ONE stranded receipt makes every subsequent refresh
    # refuse: the runner goes on executing older and older code while each new receipt adds
    # another commit nobody can publish. A write path whose recovery cannot run is worse
    # than one with none, because the log says it tried.
    #
    # REBASE, not merge: the receipt is a new fact appended to a shared history, not a
    # divergence to reconcile. Putting it on top of origin is what it always meant.
    _git(["fetch", "--quiet", "origin"])
    # ONTO `origin/<this branch>`, NEVER `@{u}` — the same defect the push above just had,
    # one line down, and it would have survived the fix. `@{u}` is read from configuration,
    # so a clone whose upstream points somewhere else rebases this branch onto THAT: on the
    # Runner channel, with a hostile upstream, `runner/mail` would be silently rewritten
    # onto the trunk. The push would still go to the right ref — but the branch is now
    # non-fast-forward against its own remote, so the retry fails and the channel is stuck
    # until a person resets it. Not dangerous, and it puts the branch's state back under the
    # control of a setting nobody at this call site can see.
    rebase_base = f"origin/{_branch(_git)}" if _branch(_git) else "@{u}"
    rc, _, err = _git(["-c", "user.name=poga substrate",
                       "-c", "user.email=substrate@localhost",
                       "-c", "commit.gpgsign=false",
                       "rebase", "--quiet", rebase_base])
    if rc == 0:
        if _push(_git, log)[0] == 0:
            return True
    else:
        # NEVER LEAVE A REBASE IN PROGRESS ON A CHECKOUT THAT RUNS PRODUCTION. A detached
        # mid-rebase tree is the one state worse than a stranded commit: the working tree
        # is half-applied and the next scheduled job runs whatever that happens to be.
        _git(["rebase", "--abort"])
        log(f"outbox: rebase onto the upstream failed ({err.splitlines()[0] if err else 'no detail'})"
            f" and was ABORTED; the tree is unchanged.")

    # Say the consequence, not just the fact. "Recorded either way" was true and useless:
    # on a checkout refreshed by an ff-only pull, a local commit is not a delayed publish,
    # it is a stopped one, and it stops the code updates too.
    log("outbox: the receipt is committed LOCALLY and could not be published. On a "
        "checkout that is refreshed by a fast-forward-only pull, this commit will make "
        "every future refresh REFUSE until it is published — the runner will keep "
        "executing older code and each new receipt adds another unpublishable commit. "
        "Publish it from here (`git push`) or the divergence compounds.")
    return True


def retire(path):
    """Move a delivered brief to `outbox/delivered/`. Idempotent by construction: the
    move IS the record, so a second run simply finds nothing queued.

    Never deletes. The sender's copy is the receipt that we sent it, and the whole
    channel is built on the principle that a missing record and a clean one must not look
    alike."""
    p = pathlib.Path(path)
    roots = production.resolve(ROOT)
    if roots:
        # The local queue is immutable. A sender may retire only from matching remote
        # acknowledgment evidence, handled by the transport worker (WI-0363).
        raise ValueError("production queue retirement requires a verified delivery acknowledgment")
    # A brief queued in ANOTHER member's outbox is NEVER ours to relocate (WI-0336). The
    # courier delivers it and stops there: moving the sender's own tracked file would put
    # a commit in their history under our authorship, which is the one line
    # `curate/push-substrate.py` draws and keeps — the federation may write its OWN
    # generated substrate into any tree it reaches, never a target's authored files.
    #
    # Scoped to a foreign `to-*` QUEUE rather than to "anything outside OUTBOX", because
    # retiring a brief handed in from somewhere else is an existing, deliberate use
    # (`_cmd_stage` copies such a brief in; a hand-delivery retires one out). A blanket
    # containment check would have broken that and looked like a tightening.
    try:
        resolved = p.resolve()
        foreign = (resolved.parent.name.startswith("to-")
                   and OUTBOX.resolve() not in resolved.parents)
    except OSError:
        foreign = False                    # cannot tell where it is; the old path stands
    if foreign:
        raise ValueError(
            f"REFUSING to retire {p}: it is queued in another member's outbox, not ours. "
            f"We courier such a brief; filing the sender's copy is the sender's write. "
            f"Nothing was moved.")
    DELIVERED.mkdir(parents=True, exist_ok=True)
    dest = DELIVERED / p.name
    if dest.exists():
        # Two briefs of one name, delivered at different times. Keep both rather than let
        # the newer silently replace the older — the history is the point.
        stem, suffix = p.stem, p.suffix
        n = 2
        while (DELIVERED / f"{stem}.{n}{suffix}").exists():
            n += 1
        dest = DELIVERED / f"{stem}.{n}{suffix}"
    p.replace(dest)
    return dest


def _cmd_stage(args):
    try:
        dest = stage(args.architect_id, args.brief, force=args.force)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if production.resolve(ROOT):
        print(f"queued: {dest}")
        print("The mail worker will publish it and track recipient acknowledgment.")
    else:
        print(f"staged: {dest.relative_to(ROOT)}")
        print("It travels on the next push; whichever machine can reach the recipient "
              "delivers it.")
    return 0


def _cmd_list(_args):
    items = pending()
    if not items:
        print("outbox: nothing queued.")
        return 0
    print(f"outbox: {len(items)} brief(s) queued")
    for aid, p in items:
        print(f"  {aid:<28} {p.name}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Queue outbound briefs for delivery by "
                                             "whichever machine can reach the recipient.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("stage", help="queue a brief for a recipient")
    s.add_argument("architect_id")
    s.add_argument("brief")
    s.add_argument("--force", action="store_true",
                   help="stage despite scrub findings, announcing the override")
    s.set_defaults(fn=_cmd_stage)

    ls = sub.add_parser("list", help="what is queued")
    ls.set_defaults(fn=_cmd_list)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
