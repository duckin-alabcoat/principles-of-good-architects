# Configuration

Each POGA project keeps its settings in `session.config.json`, at the top of the project. `poga init` writes it. You edit it by hand and commit the change like any other file.

This page lists the settings a newcomer is most likely to want. It is not the full list.

## session.config.json settings

### `interaction.pickers`: whether the agent may answer with a picker

```json
"interaction": {"pickers": "deny"}
```

**The agent may not answer you with a multiple-choice picker.** A picker narrows your answer to the options the agent thought of, and you lose the chance to say what it did not expect. So the agent asks in plain prose instead.

The ban is on by default. To turn it off, set:

```json
"interaction": {"pickers": "allow"}
```

`"allow"` is the only value that turns it off. Any other value, or no value, keeps the ban.

The setting changes two things at once: the guard that blocks a picker, and the instructions each session starts with. So an agent is never told one thing and allowed another. A session's start output says which policy is in force, for example `pickers: denied, the P17 default`.

### `gate`: what a land must pass

```json
"gate": ["python3 -m unittest discover -s tests"]
```

A list of commands. Each must exit 0 for a lane to land on `main`. If `gate` is absent, the default is Python's `unittest discover -s tests` when the project has a `tests/` folder, and nothing when it does not. A new project from `poga init` has no `gate` entry, so it gets that default.

`poga test` runs the gate's test suite in the checkout you are standing in, and records the result. A land of the same files then does not run the suite again ([ADR-0148](../adr/0148-a-land-is-a-merge.md)).

### `interpreter`: which Python runs the harness

```json
"interpreter": "/usr/bin/python3"
```

The Python the harness runs on. It is resolved, not taken from your `PATH`, so two machines agree. If it is not set, the harness uses `/usr/bin/python3` on macOS. To override it for one command, set `POGA_PYTHON`. Details are in [`interpreter.py`](../interpreter.py).
