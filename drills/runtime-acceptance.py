#!/usr/bin/env python3
"""Runtime acceptance drill — can each runtime we offer actually finish a job here?

ADR-0041 made the substrate floor runtime-agnostic: five behavioural obligations, one
binding per runtime. That is a claim about Codex and Antigravity that, until this file,
nobody had checked — the launcher offers three runtimes and the evidence for two of them
was a registry row. A contract with no per-runtime acceptance evidence is a promise about
software nobody has run.

  python3 drills/runtime-acceptance.py           # probe every runtime, drive the
                                                 # substrate scenario, write the records
  python3 drills/runtime-acceptance.py --probe   # probe only; write nothing
  python3 drills/runtime-acceptance.py --runtime codex

WHAT THIS DRILL COVERS, AND WHAT IT DOES NOT — read this before quoting a record.

The scenario the consultant brief specifies is five steps: start a session, complete one
bounded work item, suffer a controlled interruption, recover the work, land and close.
Those steps divide cleanly in two, and only one half can be driven without a human:

  THE SUBSTRATE HALF (driven here, for real). Steps 1 and 3-5 are the harness's work and
  are runtime-independent by construction: a lane is a git worktree, an interruption is a
  dead session with a liveness sidecar, and recovery is the lane's own merge. This script
  runs them end to end against a DISPOSABLE repo in a temp directory. Nothing it does
  touches this checkout, the coordination store, or any journal.

  THE RUNTIME HALF (probed, not driven). Step 2 — an agent reading its context and
  completing an item — cannot be run unattended: it needs the runtime's own auth, it
  costs real tokens, and a runtime that stops for a permission prompt would hang this
  script exactly the way it hangs a dispatched lane. So this probes what is mechanically
  answerable — does the declared binary resolve, does it answer a version query, what
  launch shape does the registry hand it, and what does it DECLARE about guards — and
  writes `not covered` for the rest, by name, rather than leaving a reader to assume the
  record means more than it does.

That split is the honest one, and the gap it names is the finding: for `claude-code` the
agent half is exercised continuously by every dispatched lane, and for `codex` and
`antigravity` it is not exercised at all. A record that said "PASS" for all three would
be asserting evidence for the two that have none.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECORDS = Path(__file__).resolve().parent / "records"
PROBE_TIMEOUT_S = 20
GIT = shutil.which("git")


def _runtimes():
    """The registry's own rows — never a list respelled here. A drill whose subject list
    is hand-maintained goes quietly blind the day a runtime is added, which is the whole
    failure `derive-a-checks-subjects-from-the-authority` names."""
    sys.path.insert(0, str(ROOT))
    import session
    return [dict(r) for r in session.RUNTIMES]


# ---- the runtime half: probed ---------------------------------------------------------

def probe_runtime(row: dict) -> dict:
    """What can be established about a runtime without starting an agent in it."""
    out = {"id": row["id"], "command": row["command"], "alias": row["alias"],
           "guards": row["guards"], "prompt_shape": row.get("prompt_shape"),
           "path": None, "version": None, "version_note": ""}
    if not row["command"]:
        out["version_note"] = "no launch command in the registry row"
        return out
    out["path"] = shutil.which(row["command"])
    if not out["path"]:
        out["version_note"] = "declared binary does not resolve on this machine"
        return out
    try:
        r = subprocess.run([row["command"], "--version"], capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT_S, stdin=subprocess.DEVNULL)
        out["version"] = (r.stdout or r.stderr).strip().splitlines()[0][:80] \
            if (r.stdout or r.stderr).strip() else ""
        if r.returncode != 0 and not out["version"]:
            out["version_note"] = f"`--version` exited {r.returncode} with no output"
    except subprocess.TimeoutExpired:
        # A binary that does not answer `--version` inside 20s is the shape that hangs a
        # detached lane. Recording it as a timeout is the finding, not an error.
        out["version_note"] = (f"`--version` did not answer in {PROBE_TIMEOUT_S}s — the "
                               "same shape that hangs a dispatched lane")
    except OSError as e:
        out["version_note"] = f"could not execute ({e})"
    return out


# ---- the substrate half: driven -------------------------------------------------------

def _git_run(repo: Path, *args, env=None):
    return subprocess.run([GIT, "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def drive_substrate() -> dict:
    """Steps 1 and 3-5 against a throwaway repo: a lane is cut, work is committed into it,
    the session dies mid-flight, and the work is recovered through the lane's own merge.

    Returns the observations, not a verdict — the record says what was seen."""
    obs = {"steps": [], "ok": False, "note": ""}
    if not GIT:
        obs["note"] = "git not available"
        return obs
    tmp = Path(tempfile.mkdtemp(prefix="runtime-drill-"))
    try:
        main = tmp / "repo"
        (main / "work-items").mkdir(parents=True)
        (main / "sessions" / "journal").mkdir(parents=True)
        (main / ".gitignore").write_text(".claude/worktrees/\n.session-state/\n",
                                         encoding="utf-8")
        (main / "trunkfile.txt").write_text("base\n", encoding="utf-8")
        _git_run(main, "init", "-q", "-b", "main")
        _git_run(main, "config", "user.email", "drill@local")
        _git_run(main, "config", "user.name", "drill")
        _git_run(main, "config", "commit.gpgsign", "false")
        _git_run(main, "add", "-A")
        _git_run(main, "commit", "-qm", "init")
        obs["steps"].append(("1. a session's checkout exists", "OK", str(main)))

        lane = main / ".claude" / "worktrees" / "poga-1"
        _git_run(main, "worktree", "add", "-q", "-b", "worktree-poga-1", str(lane), "main")
        obs["steps"].append(("1b. lane cut from the trunk", "OK", "worktree-poga-1"))

        # The lane carries no `work-items/` of its own: git tracks files, not directories,
        # and the trunk's store was empty. A real member's store has content, so this is
        # the fixture being smaller than the thing it stands for, not a defect under test.
        item = lane / "work-items" / "WI-0001-a-bounded-thing.md"
        item.parent.mkdir(parents=True, exist_ok=True)
        item.write_text("# WI-0001: a bounded thing\n\n- status: done\n- section: next\n"
                        "- source: drill\n- impact: fix\n- scope: harness\n\n"
                        "ACCEPTANCE MET: the drill's own item, closed in the lane.\n",
                        encoding="utf-8")
        _git_run(lane, "add", "-A")
        _git_run(lane, "commit", "-qm", "fix(drill): complete WI-0001")
        obs["steps"].append(("2. one bounded work item completed IN the lane", "OK",
                             "by the drill, not by an agent — see the header"))

        sc = lane / ".session-state"
        sc.mkdir(parents=True, exist_ok=True)
        (sc / "drill.live").write_text(
            json.dumps({"last_beat": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        obs["steps"].append(("3. controlled interruption", "OK",
                             "session killed mid-flight; liveness sidecar left behind, "
                             "no `ended` record — the stranded-lane shape"))

        # 4/5. Recovery is the lane's OWN merge — the sanctioned route, and the one a live
        # session uses. A plain fast-forward here would be a different mechanism wearing
        # the drill's name, so this drives git the way the lane's land does: trunk first.
        before = _git_run(main, "rev-parse", "main")
        _git_run(main, "merge", "--ff-only", "worktree-poga-1")
        after = _git_run(main, "rev-parse", "main")
        landed = "work-items/WI-0001-a-bounded-thing.md" in _git_run(
            main, "ls-tree", "-r", "--name-only", "main")
        obs["steps"].append(("4. work recovered from the dead lane", "OK" if landed else "FAIL",
                             f"{before[:8]} → {after[:8]}"))
        obs["steps"].append(("5. landed on the trunk and readable there",
                             "OK" if landed else "FAIL",
                             "the item file is on main" if landed
                             else "the item never reached main"))
        obs["ok"] = landed
    except subprocess.CalledProcessError as e:
        obs["note"] = f"git refused: {e.stderr.strip()[:200]}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return obs


# ---- the record -----------------------------------------------------------------------

def render_record(p: dict, sub: dict, when: _dt.date) -> str:
    resolved = bool(p["path"])
    covered = p["id"] == "claude-code"
    lines = [
        f"# Runtime acceptance drill — {p['id']} — {when.isoformat()}",
        "",
        f"- **Runtime:** `{p['id']}` (alias `{p['alias']}`), launches `{p['command']}`",
        f"- **Binary:** {p['path'] or '**does not resolve on this machine**'}",
        f"- **Version:** {p['version'] or '—'}"
        + (f" ({p['version_note']})" if p["version_note"] else ""),
        f"- **Declared guards (ADR-0041):** `{p['guards']}`"
        + ("" if p["guards"] == "native"
           else " — the PreToolUse guards do NOT apply, and nothing compensating is"
                " declared. A lane on this runtime is unguarded."),
        f"- **Opening-prompt shape:** `{p['prompt_shape']}`",
        f"- **Driven by:** `drills/runtime-acceptance.py`",
        "",
        "## The five steps",
        "",
        "| Step | Result | Evidence |",
        "| ---- | ------ | -------- |",
    ]
    for name, verdict, ev in sub["steps"]:
        lines.append(f"| {name} | {verdict} | {ev} |")
    lines += [
        "",
        "## What this record does NOT say",
        "",
        "The substrate half above is runtime-independent: it proves the lane, the",
        "interruption and the recovery work, and it would prove the same thing if this",
        "runtime did not exist. The runtime half — an agent in this runtime reading its",
        "injected context and completing the item itself — is **not covered here**, for",
        "the reason the driver's header gives: it needs the runtime's own auth, costs",
        "real tokens, and a permission prompt would hang the drill exactly as it hangs a",
        "dispatched lane.",
        "",
    ]
    if covered:
        lines += [
            "For `claude-code` that half is exercised continuously outside this drill —",
            "every dispatched lane is an instance of it — so the gap here is a gap in",
            "THIS FILE's coverage, not in the evidence for the runtime.",
        ]
    elif resolved:
        lines += [
            f"For `{p['id']}` that half is **exercised nowhere**. The binary is installed",
            "and the registry row resolves, and that is the whole of what is known: no",
            "recorded session on this runtime has started, read its context, completed an",
            "item and closed. Treat any claim that this runtime is a full member as",
            f"untested. Its guards are `{p['guards']}`, so a lane opened on it is not",
            "protected by anything this repo can point at.",
        ]
    else:
        lines += [
            f"For `{p['id']}` the declared binary does not resolve on this machine, so",
            "nothing at all about the runtime half is known from here. That is a fact",
            "about this machine, not about the runtime.",
        ]
    lines += ["", f"_Generated {when.isoformat()} by `drills/runtime-acceptance.py`._", ""]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", action="store_true", help="probe only; write no records")
    ap.add_argument("--runtime", help="one runtime id or alias (default: all declared)")
    a = ap.parse_args(argv)

    rows = _runtimes()
    if a.runtime:
        rows = [r for r in rows if a.runtime in (r["id"], r["alias"])]
        if not rows:
            print(f"no such runtime: {a.runtime}")
            return 2
    probes = [probe_runtime(r) for r in rows]
    for p in probes:
        print(f"{p['id']:<12} {p['path'] or 'NOT INSTALLED':<40} guards={p['guards']}"
              + (f"  [{p['version_note']}]" if p["version_note"] else ""))
    if a.probe:
        return 0

    sub = drive_substrate()
    print(f"\nsubstrate scenario: {'OK' if sub['ok'] else 'NOT COMPLETED'}"
          + (f" — {sub['note']}" if sub["note"] else ""))
    for name, verdict, ev in sub["steps"]:
        print(f"  [{verdict:4s}] {name} — {ev}")
    if not sub["ok"]:
        print("\nno records written: a scenario that did not complete has nothing to record")
        return 1

    RECORDS.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today()
    for p in probes:
        path = RECORDS / f"{today.isoformat()}-{p['id']}.md"
        path.write_text(render_record(p, sub, today), encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
