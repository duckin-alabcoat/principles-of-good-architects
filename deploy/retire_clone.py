#!/usr/bin/env python3
"""Clone-retirement preflight — can this dev clone be deleted without losing anything?

WI-0225. Member systems cut over to `~/deploy/<system>` on a always-on runner host,
leaving their old dev clones as a second copy that can be edited and silently diverge.
The clones are to be DELETED, not archived (an operator ruling) — but only once each one
is confirmed to hold nothing the remote and the deploy tree do not.

This script answers that question and nothing else. IT NEVER DELETES ANYTHING. Deletion
stays a separate, human, explicit act; this produces the evidence that act requires.

WHY THIS IS CODE AND NOT A CHECKLIST
------------------------------------
The item has been re-derived by hand more than once, and each pass
found the previous pass's method too weak. That is the signature of a rule living in prose
([`add-structural-guard-on-recurrence`](../habits/master.md#add-structural-guard-on-recurrence)).
It is also gated on a machine this lane cannot reach directly: development and the runner
host are deliberately isolated from each other by policy
([ADR-0106](../adr/0106-local-promotion-process-and-data-residency-are-declared-separately.md):
the ruling is that the repo is the only channel between them, the isolation is
deliberate, and nothing works around it). So the method must travel to the runner host as a tracked file and run there,
because git is the only channel that exists.

THE DEFECT THIS EXISTS TO KILL, AND WHY THE OBVIOUS COMMAND HAS IT
------------------------------------------------------------------
WI-0225's revised acceptance prescribes `git status --porcelain --ignored` to enumerate
what would be lost. **That command cannot produce an enumeration.** It collapses an ignored
DIRECTORY into a single entry and never descends. For a clone with ignored directories,
for example:

    git status --porcelain --ignored        ->  3 entries   (.claude/worktrees/,
                                                             .session-state/,
                                                             architect-learnings.md)
    git ls-files -o -i --exclude-standard   -> 14 named files

Among the files the first command hides is a member's own
`.session-state/architect-learnings.<host>.md` producer file, whose
merge the acceptance's own ordering clause gates the deletion on. An operator working the
acceptance literally would tick `.session-state/` off as one enumerated path and delete the
only copy of that machine's producer entries.

This is the SAME defect the item was opened to fix, one layer down. The 2026-09-11 note
found that a "the remote holds everything" check silently means "everything TRACKED";
the revised acceptance fixed that by demanding ignored paths be enumerated, then prescribed
a command that collapses ignored directories. A check whose roster is bounded by what it
happens to see, reporting completeness it did not establish
([`derive-a-checks-subjects-from-the-authority`](../habits/master.md#derive-a-checks-subjects-from-the-authority),
[`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

So this script enumerates with `git ls-files -z -o -i --exclude-standard` (named files) and
treats any entry git returns as a DIRECTORY — a trailing slash, meaning git declined to
descend because it is a nested repository or submodule — as OPAQUE: an explicit
NOT-ENUMERATED finding that refuses, never a path silently counted as one.

WHAT "SAFE" MEANS HERE
----------------------
Four conditions, all of which must hold:

  1. Every commit the clone holds is on the remote      (`log --branches --not --remotes`)
  2. Every branch the clone holds is merged to origin   (`branch --no-merged origin`)
  3. Every untracked/ignored path is named, and each one is either byte-identical to the
     same relative path in the deploy tree, or matched by a `disposable` rule that carries
     a written reason.
  4. Nothing is opaque, and nothing is `blocking`.

`blocking` outranks `disposable` unconditionally. A producer file (`architect-learnings*`)
is never on the remote by construction, is read-only to the federation (role doc §3), and
must be merged by its own Architect before its clone dies. A later hand-broadened
`disposable` glob must not be able to swallow it, so the precedence is mechanical and
tested, not a matter of rule-ordering care.

USAGE (on the runner host)
--------------------------
    python3 deploy/retire_clone.py --system example-app --clone ~/Projects/example-app
    python3 deploy/retire_clone.py --system another-app --clone ~/Projects/another-app

Exit status: 0 = SAFE-TO-DELETE, 1 = REFUSE, 2 = could not run the check at all.
The receipt it writes is the note the item's acceptance asks for; the last line printed is
the exact `poga work edit` command that files it.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import textwrap
import datetime

HERE = pathlib.Path(__file__).resolve().parent
RULES_PATH = HERE / "clone-retirement.json"

VERDICT_SAFE = "SAFE-TO-DELETE"
VERDICT_REFUSE = "REFUSE"


class CheckError(Exception):
    """The check could not be run at all — distinct from 'ran and refused'."""


def _machine_name():
    """This machine's name, for the receipt's provenance line.

    `scutil --get ComputerName` is what every session stamp in the fleet is written from,
    so the receipt reads in the same vocabulary as the journals. Falls back to the
    hostname, and says `unknown` rather than guessing.
    """
    try:
        proc = subprocess.run(["scutil", "--get", "ComputerName"],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import socket
        return socket.gethostname() or "unknown"
    except OSError:
        return "unknown"


def _git(clone, *args, check=True):
    """Run a git command in `clone`. Returns stdout. Raises CheckError on failure."""
    proc = subprocess.run(
        ("git", "-C", str(clone)) + args,
        capture_output=True, text=True,
    )
    if check and proc.returncode != 0:
        raise CheckError(
            f"git {' '.join(args)} failed in {clone} (exit {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:400]}")
    return proc.stdout


def _git_z(clone, *args):
    """Run a NUL-separated git command; returns a list of entries.

    NUL separation, not newlines: a path may legally contain a newline, and a check that
    splits on them would silently merge two paths into one malformed entry — in a tool
    whose whole job is to not miss a path.
    """
    out = _git(clone, *args)
    return [e for e in out.split("\0") if e]


def load_rules(path=RULES_PATH):
    if not path.exists():
        raise CheckError(f"rules file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise CheckError(f"rules file is not valid JSON: {path}: {exc}") from exc
    if data.get("schema") != 1:
        raise CheckError(f"rules file schema {data.get('schema')!r} is not 1: {path}")
    return data


def _match(path, rules):
    """First rule whose glob matches `path`, or None.

    fnmatch, not pathlib.match: `*` here spans separators deliberately, so a rule written
    `**/__pycache__/*` and one written `*__pycache__*` behave the same way rather than
    failing open on the spelling.
    """
    for rule in rules:
        if fnmatch.fnmatch(path, rule["glob"]):
            return rule
    return None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_path(rel, clone, deploy_tree, system_rules):
    """Disposition one named path. Returns (state, detail, rule).

    `rule` is the GLOB that decided it, or "" where no rule did. It exists so the receipt
    can group paths by the rule that blocked them: a system's production data and
    migrating data can be many paths sharing a few reasons, and printing one reason per path buries the
    finding as thoroughly as printing it nowhere. Returned rather than re-derived by a
    second `_match` call in `render` — one matcher, one set of bugs (P16).

    States, in the order they are decided — the order IS the safety property:
      OPAQUE        git declined to descend; contents unknown. Never safe.
      BLOCKING      matched a blocking rule. Never safe, whatever else matches.
      IN-DEPLOY     present in the deploy tree and byte-identical.
      DIFFERS       present in the deploy tree with different content. Not safe: the copy
                    that survives is not the copy being deleted, and which one is current
                    is a question only a human can answer.
      DISPOSABLE    matched a disposable rule carrying a reason.
      UNDISPOSITIONED  none of the above. The default is refusal, never assumption.
    """
    if rel.endswith("/"):
        return ("OPAQUE",
                "git did not descend (nested repo or submodule); contents unknown", "")

    blocking = _match(rel, system_rules.get("blocking", []))
    if blocking:
        return "BLOCKING", blocking["why"], blocking["glob"]

    src = pathlib.Path(clone) / rel
    dst = pathlib.Path(deploy_tree) / rel
    if dst.is_file() and src.is_file():
        if _sha256(src) == _sha256(dst):
            return "IN-DEPLOY", f"byte-identical to {dst}", ""
        return "DIFFERS", f"present at {dst} but content differs", ""

    disposable = _match(rel, system_rules.get("disposable", []))
    if disposable:
        return "DISPOSABLE", disposable["why"], disposable["glob"]

    return "UNDISPOSITIONED", "not in the deploy tree and matched by no rule", ""


def check_clone(system, clone, deploy_tree, rules, fetch=True):
    """Run the full preflight. Returns a result dict. Never writes to the clone."""
    clone = pathlib.Path(clone).expanduser().resolve()
    deploy_tree = pathlib.Path(deploy_tree).expanduser()

    if not clone.exists():
        raise CheckError(
            f"NOT FOUND: {clone}\n"
            f"  This is NOT evidence that the clone was deleted, and must not be read as "
            f"the retirement being done. Both clones were once MOVED into a differently "
            f"named directory instead of being deleted, and the leaf directory names were "
            f"never recorded. A `test -e` that "
            f"exits non-zero cannot tell 'deleted' from 'moved' from 'never here' -- which "
            f"is this item's own defect in a third costume. Locate the clone and re-run "
            f"against its real path.")

    # THE DOCUMENTED PATH TRAP (WI-0011). A member's repo root may be one directory with
    # the runnable code in a SUBDIRECTORY of it (deploy/registry.json "subdir", a supported
    # layout). Pointed one level down at the system-id path, every probe here still
    # succeeds -- git resolves upward to the same repo -- but `ls-files` is scoped to the
    # subdirectory and reports absence for everything above it, including the
    # `.session-state/` directory holding the producer file. That is a clean-looking
    # enumeration that silently covers a fraction of the clone, which is this item's own
    # failure mode wearing a different costume. Refuse rather than narrow.
    #
    # This is checked BEFORE looking for a `.git` entry, deliberately: `rev-parse` answers
    # from anywhere inside the repo, so it can say "you meant this root" where a bare
    # `.git` test can only say "no". The worse message is the one that gets a subdirectory
    # re-probed by hand rather than corrected.
    try:
        toplevel = _git(clone, "rev-parse", "--show-toplevel").strip()
    except CheckError as exc:
        raise CheckError(f"not a git clone: {clone} ({exc})") from exc
    if toplevel and pathlib.Path(toplevel).resolve() != clone:
        raise CheckError(
            f"--clone {clone} is not the repository root; git says the root is "
            f"{toplevel}. Enumerating from a subdirectory silently scopes `ls-files` to "
            f"it and reports absence for everything above. Re-run with --clone {toplevel}.")

    systems = rules.get("systems", {})
    if system not in systems:
        raise CheckError(
            f"system {system!r} has no rules entry. The retirement list is CLOSED to "
            f"{sorted(systems)} (WI-0225: one member excluded by an operator ruling; "
            f"another absent for want of a cutover receipt). Do not extend it "
            f"without one.")
    system_rules = systems[system]

    assumptions = []
    if fetch:
        try:
            _git(clone, "fetch", "--all", "--prune", "--quiet")
            assumptions.append("remote-tracking refs refreshed by this run (git fetch --all --prune)")
        except CheckError as exc:
            # A stale-ref answer is still usable and errs toward refusing, so report the
            # failure loudly rather than aborting — but never let it read as a clean fetch.
            assumptions.append(f"FETCH FAILED, remote-tracking refs are as of the clone's "
                               f"last fetch and may be stale: {exc}")
    else:
        assumptions.append("--no-fetch: remote-tracking refs are as of the clone's last "
                           "fetch and may be stale")

    if not deploy_tree.is_dir():
        assumptions.append(
            f"DEPLOY TREE ABSENT at {deploy_tree} — every path will read as not-present, "
            f"so a SAFE verdict is impossible. This is reported, not silently folded in.")

    findings = {"unpushed_commits": _git(clone, "log", "--branches", "--not",
                                         "--remotes", "--oneline").split("\n")}
    findings["unpushed_commits"] = [c for c in findings["unpushed_commits"] if c.strip()]
    findings["unmerged_branches"] = [
        b.strip() for b in _git(clone, "branch", "--no-merged", "origin").split("\n")
        if b.strip()]

    ignored = _git_z(clone, "ls-files", "-z", "-o", "-i", "--exclude-standard")
    untracked = _git_z(clone, "ls-files", "-z", "-o", "--exclude-standard")

    paths = []
    for rel in sorted(set(ignored) | set(untracked)):
        state, detail, rule = classify_path(rel, clone, deploy_tree, system_rules)
        paths.append({
            "path": rel,
            "kind": "ignored" if rel in ignored else "untracked",
            "state": state,
            "detail": detail,
            "rule": rule,
        })

    blockers = []
    if findings["unpushed_commits"]:
        blockers.append(f"{len(findings['unpushed_commits'])} commit(s) not on the remote")
    if findings["unmerged_branches"]:
        blockers.append(f"{len(findings['unmerged_branches'])} branch(es) not merged to origin")
    for state in ("OPAQUE", "BLOCKING", "DIFFERS", "UNDISPOSITIONED"):
        n = sum(1 for p in paths if p["state"] == state)
        if n:
            blockers.append(f"{n} path(s) {state}")

    return {
        "system": system,
        "clone": str(clone),
        "deploy_tree": str(deploy_tree),
        # The receipt must name the machine it was PRODUCED on, never the machine it was
        # meant to run on. A receipt that says "on the Runner" because the docstring says
        # so is a fabricated provenance line, and the whole item is about checks that
        # report something they did not establish.
        "machine": _machine_name(),
        "checked_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "assumptions": assumptions,
        "findings": findings,
        "paths": paths,
        "blockers": blockers,
        "verdict": VERDICT_REFUSE if blockers else VERDICT_SAFE,
    }


def blocking_groups(paths, sample=3):
    """[(glob, why, [paths], n_total)] for the BLOCKING paths, in first-seen rule order.

    WHY GROUPED (WI-0392). A real run can enumerate hundreds of paths. A blocking
    reason printed once per path would repeat one production-data finding
    dozens of times inside that listing, which is not more visible than printing it once — it is
    less, because it makes the listing unreadable and trains the reader to skim it. The
    reason belongs ABOVE the enumeration, once per rule, with a sample and a count; the
    per-path lines below stay complete and unchanged, because the enumeration is the
    acceptance's own requirement and nothing here may thin it.

    Keyed on the rule GLOB, which `classify_path` now returns. Keying on the `why` text
    would merge two rules that happened to share wording and split one whose wording was
    edited between runs — a grouping that answers a different question from the one the
    operator is asking, which is *which rule do I have to clear*."""
    groups = {}
    order = []
    for p in paths:
        if p["state"] != "BLOCKING":
            continue
        key = p.get("rule") or p["path"]
        if key not in groups:
            groups[key] = {"why": p["detail"], "paths": []}
            order.append(key)
        groups[key]["paths"].append(p["path"])
    return [(k, groups[k]["why"], groups[k]["paths"][:sample], len(groups[k]["paths"]))
            for k in order]


def render(result):
    """Render the receipt as markdown — this text IS the note the acceptance asks for."""
    L = []
    a = L.append
    a(f"WI-0225 clone-retirement preflight — {result['system']}")
    a("")
    a(f"Ran `deploy/retire_clone.py` on **{result['machine']}** at {result['checked_at']}.")
    a(f"Clone: `{result['clone']}`  ·  deploy tree: `{result['deploy_tree']}`")
    a("")
    a(f"VERDICT: {result['verdict']}")
    if result["blockers"]:
        a("")
        a("Refused because:")
        for b in result["blockers"]:
            a(f"  - {b}")
    groups = blocking_groups(result["paths"])
    if groups:
        a("")
        a("WHAT BLOCKS DELETION, AND WHY — one entry per rule, ahead of the enumeration.")
        a("Each of these is a path that is NOT in the deploy tree and is NOT disposable.")
        for glob, why, sample, total in groups:
            a("")
            a(f"  RULE `{glob}` — {total} path(s)")
            for line in textwrap.wrap(why, width=84):
                a(f"      {line}")
            for p in sample:
                a(f"      · {p}")
            if total > len(sample):
                a(f"      · … and {total - len(sample)} more, all listed below")
    a("")
    a("What this check assumed:")
    for s in result["assumptions"]:
        a(f"  - {s}")
    a("")
    a("(1) Does the remote hold every commit and branch?")
    up = result["findings"]["unpushed_commits"]
    ub = result["findings"]["unmerged_branches"]
    a(f"  git log --branches --not --remotes --oneline : "
      f"{'no output — clean' if not up else f'{len(up)} COMMIT(S) NOT ON THE REMOTE'}")
    for c in up:
        a(f"      {c}")
    a(f"  git branch --no-merged origin                : "
      f"{'no output — clean' if not ub else f'{len(ub)} BRANCH(ES) NOT MERGED'}")
    for b in ub:
        a(f"      {b}")
    a("")
    a(f"(2) Every untracked and gitignored path, BY NAME ({len(result['paths'])} total).")
    a("    Enumerated with `git ls-files -o -i --exclude-standard`, NOT")
    a("    `git status --porcelain --ignored` — the latter collapses an ignored directory")
    a("    into one entry and would hide every file inside it.")
    a("")
    width = max([len(p["state"]) for p in result["paths"]] + [5])
    grouped = {g[0] for g in groups}
    for p in result["paths"]:
        a(f"  [{p['state']:<{width}}] {p['path']}")
        # A BLOCKING path whose rule was stated in full above gets a back-reference, not
        # the same paragraph again. The enumeration stays complete — every path is named
        # with its state, which is what the acceptance requires — while each reason is
        # stated EXACTLY ONCE in the receipt. Repeating a 90-word reason 13 times is how
        # a listing becomes unreadable, and an unreadable listing is one nobody checks.
        if p["state"] == "BLOCKING" and p.get("rule") in grouped:
            a(f"  {'':<{width + 3}} see rule `{p['rule']}` above")
        else:
            a(f"  {'':<{width + 3}} {p['detail']}")
    if not result["paths"]:
        a("  (none)")
    a("")
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="WI-0225 clone-retirement preflight. Never deletes anything.")
    ap.add_argument("--system", required=True,
                    help="system id; must appear in deploy/clone-retirement.json")
    ap.add_argument("--clone", required=True, help="path to the dev clone being retired")
    ap.add_argument("--deploy-tree", default=None,
                    help="deploy tree to compare against (default ~/deploy/<system>)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip refreshing remote-tracking refs (answer may be stale; said so in the receipt)")
    ap.add_argument("--receipt", default=None,
                    help="write the receipt here (default .session-state/wi-0225-<system>.md)")
    ap.add_argument("--json", action="store_true", help="also print the raw result as JSON")
    args = ap.parse_args(argv)

    deploy_tree = args.deploy_tree or os.path.expanduser(f"~/deploy/{args.system}")

    try:
        rules = load_rules()
        result = check_clone(args.system, args.clone, deploy_tree, rules,
                             fetch=not args.no_fetch)
    except CheckError as exc:
        print(f"CANNOT CHECK: {exc}", file=sys.stderr)
        print("This is not a clean bill of health — nothing was established.", file=sys.stderr)
        return 2

    text = render(result)
    print(text)
    if args.json:
        print(json.dumps(result, indent=2))

    receipt = pathlib.Path(args.receipt) if args.receipt else (
        HERE.parent / ".session-state" / f"wi-0225-{args.system}.md")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(text)
    print(f"Receipt written: {receipt}")
    print(f"File it on the item:  poga work edit WI-0225 --append-notes-file {receipt}")
    if result["verdict"] == VERDICT_SAFE:
        print(f"\nThis clone may now be deleted:  rm -rf {result['clone']}")
    else:
        print(f"\nDO NOT DELETE {result['clone']} — resolve the refusals above and re-run.")
    return 0 if result["verdict"] == VERDICT_SAFE else 1


if __name__ == "__main__":
    sys.exit(main())
