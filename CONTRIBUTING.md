# Contributing

Thank you for reading the code, and for any fix you send. This page says how to run the tests, what happens to an issue or a pull request, and how to update or remove an install.

## Run the tests

You need macOS, Git and Python 3.9 or newer. The harness runs on the system Python at `/usr/bin/python3` (see [`interpreter.py`](interpreter.py)), so test with that one if you can.

The checks CI runs on every push and pull request ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

```sh
python3 -B -m unittest tests.test_poga_install tests.test_local_init
test -x drills/basic-acceptance.sh
bash -n poga
python3 -B session.py wi-check
python3 -B curate/distill.py --check
python3 -B curate/check_canon_budget.py --check
python3 -B curate/check_substrate_docs.py --check
```

The full suite:

```sh
python3 -B -m unittest discover -s tests
```

It has about 6,700 tests and takes about 20 minutes. Run it from a git checkout: some tests make real commits in scratch repositories. It also needs `tmux` and the `gh` CLI on your `PATH` (`brew install tmux gh`). The tests never log in to GitHub or reach the network; without those two tools, about 26 tests fail.

The acceptance drill, [`drills/basic-acceptance.sh`](drills/basic-acceptance.sh), is not part of CI. It needs `sandbox-exec` and a `claude` binary on your machine. Its record, [`drills/basic-acceptance.md`](drills/basic-acceptance.md), says what it proves and what it does not.

## How a fix reaches this repository

This repository is not where POGA is developed. It is a generated copy: a **public cut** of a private working repository, published as a single commit. [`PUBLIC-CUT-RECEIPT.md`](PUBLIC-CUT-RECEIPT.md) lists what the cut holds and what it leaves out.

So a pull request here is **not merged**. That is not a judgment on the fix. It is how the copy is made: the next cut replaces this repository's contents, and any commit made only here would vanish.

What happens instead:

1. Your issue or pull request is read.
2. If the fix is wanted, it is made again in the private working repository, with a test.
3. It goes through that repository's own land gate, like every other change.
4. It arrives here in the next public cut.

An issue is just as useful as a pull request. A pull request is a good way to show the exact change, and the test that proves it. Please say what you saw, what you expected, and the commands that show it.

## Update an install

Each new cut replaces the repository's single commit, so `git pull` will not fast-forward. Fetch and reset instead. This discards local changes in the clone:

```sh
cd <your clone>
git fetch origin
git reset --hard origin/main
./poga install
```

Or delete the clone and clone it again. Then run `./poga install` in the new clone. It repairs the `~/.local/bin/poga` link if the clone moved, and does nothing if the link is already right.

Projects you already made with `poga init` keep the copy of the harness they were made with (`poga`, `session.py`, `sessionlib/` and the rest, inside each project). Updating the clone does not change them.

## Remove an install

`./poga install` writes two things in your home folder:

- a symbolic link, `~/.local/bin/poga`, pointing at the clone's `poga` script;
- if `~/.local/bin` was not on your `PATH`, one line at the end of `~/.zshrc`: `export PATH="$HOME/.local/bin:$PATH"`.

`poga init` writes a third: the local federation, under `~/.config/poga/`. It holds `federation.json`, the profile under `users/`, and the copies of each project's lessons under `inputs/`. If you made a `~/.config/poga/poga.local` yourself, it is there too.

To remove them:

```sh
# 1. The link. Removed only if it is a link, never a real file.
[ -L ~/.local/bin/poga ] && rm ~/.local/bin/poga

# 2. The PATH line. Skip this if other tools use ~/.local/bin, or if the line was
#    there before you installed POGA. A copy is kept at ~/.zshrc.bak.
sed -i.bak '/^export PATH="\$HOME\/\.local\/bin:\$PATH"$/d' ~/.zshrc

# 3. The local federation. This deletes your POGA profile and the shared lessons.
rm -rf ~/.config/poga

# 4. The clone itself.
rm -rf <your clone>
```

Projects made with `poga init` are ordinary git repositories. Removing the install does not touch them. Delete them yourself if you no longer want them. If you installed any scheduled job from [`deploy/`](deploy/README.md), remove it first, as that page's **Uninstall** section shows.

## Licence

By sending a contribution you agree that it is licensed under the Apache License 2.0, the licence of this repository. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
