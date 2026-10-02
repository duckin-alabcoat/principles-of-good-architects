# ADR-0085: The bootstrap intake is a session, launched from the empty folder

**Status:** Accepted
**Date:** 2026-08-02
**Deciders:** the operator, Federation Architect

## Context

`poga bootstrap --adopt --create-remote --spec <spec>` has been able to build an entire system from a completely empty folder for some time: `git init` with nothing to commit, the full substrate install, a private GitHub repo created and pushed, and the portfolio row registered. It has been exercised several times, on different members, and their spec files live in this repo.

**The only missing piece was the spec.** [`bootstrap.py`](../bootstrap.py) consumes one; nothing produced one. The spec was authored by hand by the Federation Architect, from an intake conversation, in a *federation* session. So the judgment half of bootstrap — the half that decides what the system actually is — had never moved to the point of use. Standing up a new project began with "open the federation repo," not with the project.

the operator, session ~115: a new project should start by typing `poga` in an empty folder — and, clarified immediately, not necessarily a bare `poga`; switches and flags are acceptable. The ask is **one command in an empty folder**, not a bare verb.

Three facts, probed rather than inferred, shaped what follows.

**The no-spec refusal is already the right failure.** `load_adopt_spec` runs *before* `adopt_in_place` ([`bootstrap.py:804`](../bootstrap.py)), so a run with no spec touches nothing at all — no `git init`, no files, no remote. It exits 1 naming the five required keys and saying *"Nothing changed."* Identical with or without `--plan`; the refusal is upstream of the dry-run gate. That refusal message is, in effect, the intake's acceptance criteria already written down.

**Almost nothing can run before the spec exists, because the spec *is* the system's identity.** The architect name and id derive from `system_name` (`bootstrap.py:625`), the repo name from the spec (`:630`), and the role doc, `session.config.json`, `CLAUDE.md`, README, `STATUS.md`, `ROADMAP.md` and the portfolio row are all templates rendered from spec values. Only three steps are spec-free: `git init`, `.gitignore`, and the byte-identical shared substrate.

**Those three spec-free steps are exactly the idempotency markers.** `ADOPTED_MARKERS` is precisely `["session.py", "CANON.md", "STANDARD.md"]` (`bootstrap.py:157`) — which *is* the shared substrate. Installing them early to "save time" would convert an abandoned intake from *nothing changed* into a folder whose re-run is refused as a retrofit. The gain was under a second of work; the cost was resumability.

the operator specified the flow in five steps, session ~115: `poga` in an empty folder says it needs the spec → explains what is about to happen → asks which LLM if `-r` was not given → y/n gate → launches that LLM with a prompt that sets it up to build the spec. The runtime roster already exists ([ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md): `c` / `cx` / `g`), so "which LLM" is a solved question.

## Decision

**D1 — A folder with no git repo is the new-system intake, not a dead end.** `poga` there previously hit `require_repo` and exited 2 with *"cd into a member checkout and re-run."* It now routes to the intake. `poga new` is the same arm, named, for when the operator wants to be explicit.

The trigger is **"not a git repository,"** deliberately not **"empty directory."** `bootstrap --adopt` handles a folder with existing content identically — it commits what is there as the root commit first — so keying on emptiness would block the adopt-in-place path ([ADR-0067](0067-adopt-in-place-is-the-third-onboarding-path.md)) for no reason. Only `session` routes this way; `lanes` / `work` / `dispatch` keep the plain refusal, because there is nothing for them to stand up.

**D2 — The intake is an agent session, not an argparse questionnaire.** The spec's load-bearing fields — `mission_prose`, `scope_do`/`scope_dont`, `voice`, `system_principles` — are judgment, not data entry ([P15](../principles/master.md#p15--code-for-mechanism-not-judgment)). A `read`-loop would collect the five *required* keys and produce a thin Architect, which is the outcome this path exists to prevent. Bootstrapping is the Federation Architect's own job under mission #1 ([ADR-0017](0017-bootstrapping-is-a-federation-responsibility.md)) — it is the *actor*, not merely the toolmaker — so the intake belongs in a session.

**D3 — Five steps, in the operator's order, with the gate where the outward-facing act is.** Name the missing spec → explain what is about to happen (including, explicitly, that a private GitHub repo will be created and pushed) → ask which runtime unless `-r` answered it → y/N → launch. The runtime question is a shell `read` in the operator's own terminal, not the picker [P17](../principles/master.md#p17--ask-in-prose-not-pickers) forbids. The gate sits before repo and remote creation, and it is the only one: the agent does **not** ask again before installing, because that approval already happened here.

The runtime is resolved *before* the gate, so an unknown alias or an uninstalled binary is a plain refusal rather than a yes the operator then watches fail — the same ordering [ADR-0082](0082-lifecycle-inversion-and-per-session-runtime.md) D4 applies to lane allocation.

**D4 — The intake prompt is self-contained text, not a pointer.** [`bootstrap-kit/intake-prompt.md`](../bootstrap-kit/intake-prompt.md) carries the spec schema, the interview, and the ask-in-prose rules in its own body. Two reasons, and both are structural: the target folder is empty, so there is no role doc, no `CANON.md` and no `CLAUDE.md` to read; and the runtime may be Codex or Gemini, which inherit no federation canon at all. Pointing at a source the agent cannot resolve is not delivery — [`single-source-and-deliver`](../habits/master.md#single-source-and-deliver) names three mechanisms and this is *inject*, the one that applies when there is a runtime to inject at and no readable source on the other side.

`poga` renders the target path, the federation doc-link base, and the bound user into the template before launch, deriving each from declared state (the checkout's `origin` remote, `session.config.json`'s `user_name` and `user_profile`) rather than a literal pasted into the script. **An unresolved placeholder is a hard failure**: a literal `{{TARGET_DIR}}` reaching the agent would be interviewed about or, worse, invented ([`declare-what-a-check-assumes`](../habits/master.md#declare-what-a-check-assumes)).

**D5 — The agent runs the install itself, as its last act.** Interview → write `bootstrap-spec.json` → `poga bootstrap --adopt --create-remote`. the operator answers questions and gets a finished, remote-backed repo without touching the keyboard again. The pipeline underneath does not move: no change to `bootstrap.py` or `poga_cli.py`.

**D6 — Nothing is installed before the agent runs.** Per the Context: the three spec-free steps are the idempotency markers, so front-loading them trades resumability for under a second of work. An intake that is abandoned partway leaves the folder exactly as it was found, and the prompt instructs the agent to the same standard — write nothing until the required keys are settled. A `bootstrap-spec.json` already present is treated as a **resume**: the intake offers the install rather than re-interviewing.

**D7 — Federation-only, and it says so before the interview.** Like `bootstrap` and `restore`, the intake resolves against `SCRIPT_DIR` — the checkout this copy of `poga` physically lives in — because the install half needs `poga_cli.py`, which is not shipped to members. A `poga` symlinked out of a member checkout refuses **up front**. Refusing after a ten-minute interview would be the worst possible moment to discover it.

## Consequences

Starting a new system is now: `cd` to a new folder, type `poga`, answer questions. The federation repo never has to be opened, and the spec is authored where the project is.

`poga` gains a federation-only arm inside a file that ships byte-identical fleet-wide. This is the established shape — `bootstrap` and `restore` are already federation-only verbs in the same file, and a members-only fork would be a maintained duplicate ([P16](../principles/master.md#p16--avoid-duplication)). `bootstrap-kit/` is not part of the pushed substrate set, so the template's absence on a member is expected and handled by the same refusal as the missing `poga_cli.py`.

The flow is runtime-agnostic by construction rather than by aspiration, which is the 7.0.0 direction: because the prompt carries its own context, a Codex or Gemini intake produces the same spec a Claude intake does.

**What is untested until it is used for real:** the *quality* of the interview. The mechanics are covered end-to-end by [`tests/test_poga_intake.py`](../tests/test_poga_intake.py) — refusals, ordering, the gate, and the rendered prompt actually reaching a stub runtime — but no test can assert that the resulting `mission_prose` is good. The first real intake is the acceptance test, and the failure mode to watch for is the one D2 exists to prevent: a spec that fills the five required keys and leaves the judgment fields thin.

A verb-name question was raised and settled by building both: bare `poga` is the primary entry point the operator specified, and `poga new` is its explicit alias. `poga bootstrap --intake`, the earlier placeholder, is not built — the intake is not a flag on the installer, it is what happens when you have not got a spec yet.
