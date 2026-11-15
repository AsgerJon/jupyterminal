# Contributing

Thank you for taking an interest in the development of *jupyterminal*!
This project is the work of a single developer, and this document gives
an overview of how it is developed: how the tests are run, what a change
has to pass, and how a release is published.

## Setup

*jupyterminal* needs Python 3.12 or later, PySide6 and zsh. The
development process uses 'mamba' to provide a virtual environment. You do
not need 'mamba', but some virtual environment rather than system Python
is strongly recommended.

```terminaloutput
mamba env create -f environment.yml
mamba activate jupyterminal_env
```

Without 'mamba', the same development tools are declared as dependency
groups in `pyproject.toml`, which pip 25.1 or later installs into
whatever environment is active:

```terminaloutput
pip install -e .
pip install --group dev
```

The tests start real programs. Besides zsh they need `vim`, `less`,
`htop` and `ping` on the `PATH`. No test is skipped when one of them is
missing: it fails.

## Testing and Coverage

The suite has 100% branch coverage, of the package and of the tests
themselves. This requirement is non-negotiable. `pyproject.toml` makes
every run of `pytest` measure it, so from the repository root

```terminaloutput
pytest
```

runs everything and lists whatever is not covered. Every release workflow
runs the suite on each supported Python, from 3.12 to 3.15, and the
workflows of release candidates and releases refuse to publish below 100%
on 3.15. That measurement is authoritative;
`bin/dev_tooling/coverage_test.sh` gives the same numbers locally and
opens the HTML report in the system browser.

The tests come in two kinds. Most feed the emulator, the session and the
view by hand and take a few seconds together. The rest start a real zsh
on a pseudo-terminal; they carry the marker `live` and run last. While
working on a change, the fast ones alone are usually enough:

```terminaloutput
pytest -m "not live"
```

The window is always drawn off screen, so a run never opens anything on
the desktop. `tests/test_acceptance.py` holds one test for each
acceptance criterion of the functional spec the program was built from.

A test asserts what the user would see: the exact text on the clipboard,
the exact bytes sent to the shell, the colour of a cell. A test that only
runs a line without checking its result does not count, and the mutation
run below finds it.

### Mutation Testing Before a Release

Coverage says that every line ran; it does not say that the tests would
notice a wrong line. Mutation testing does: the runner changes one small
thing at a time, a `<` into a `<=`, a constant by one, a statement into
`pass`, and runs the tests against each change. The rule for releases:

> Before every release, the code changed since the last release is
> mutation tested. Every surviving mutant must either be killed by a new
> test or confirmed equivalent and recorded as such. A release does not
> ship with non-equivalent survivors.

The runner is `bin/dev_tooling/mutation/mutate.py`. It never touches the
working tree: each mutant is written into a copy of `src`, `tests` and
`pyproject.toml` under `bin/dev_tooling/mutation/runs/`, which is not
committed. A run survives being interrupted; the same command resumes it.

```terminaloutput
python bin/dev_tooling/mutation/mutate.py list --package jupyterminal.term
python bin/dev_tooling/mutation/mutate.py run --package jupyterminal.term --mark "not live"
python bin/dev_tooling/mutation/mutate.py run --package jupyterminal.term --pass 1
python bin/dev_tooling/mutation/mutate.py report --package jupyterminal.term
```

The first `run` sweeps every mutant of the package against the fast
tests alone, which settles most of them in a second or two each. The
second reruns its survivors against the whole suite; only what that
pass lets through is a survivor. `show --id ID` prints one mutant as a
diff, and `check --id ID` runs one without recording it.

A sweep of the whole package is some thirteen thousand mutants and a
matter of hours: the one of 2026-11-14 needed 16.7 hours of test time,
shared out over the jobs. `--jobs` says how many mutants are tried side
by side (4 unless given); each job may take 1.5 GiB of memory at the
most, so `--jobs 8` wants 12 GiB to be free in the worst case. The
runner works at the lowest priority and waits when memory gets short.
A test that takes ten times as long under a mutant as it does without,
and four seconds on top, has noticed the mutant: the run ends there and
the mutant counts as killed, with a line that starts `SLOW`.

While working on a change there is no need to sweep everything again:
`check` with the IDs of the mutants in the functions that changed says
within minutes what the change did to them.

Each survivor is read, not trusted. An equivalent mutant is accepted
only with a written reason, in the `verdicts.json` of the run:

```json
{
  "screen.py:120:8:cmp:0": {
    "verdict": "equivalent",
    "reason": "the index is never negative here, so < and <= agree"
  }
}
```

Everything else gets a test, and `run --pass 2` shows that the test
kills it. `report` writes `survivors.md` with the totals and each
survivor's verdict. Once the survivors and the equivalents with their
reasons are written down, the sandboxes and logs of a run can go; the
written lists stay.

An ID is a position in a file, so the sweep after an edit knows the same
mutants by other IDs. The verdicts go along with

```terminaloutput
python bin/dev_tooling/mutation/mutate.py carry --package jupyterminal
```

which gives every survivor of the latest run the verdict that an earlier
run has for the same mutant: the same function, the same line of source,
the same change. A verdict whose function has changed since is marked
`"review": true`, and the report says so until its reason has been read
against the code as it is and the mark taken out. A verdict whose own
line has changed is listed and left behind. A reason that names a
function holds for longer than one that names a line number.

### Pinned Dependencies

The release workflows do not install "whatever is newest today". They
install from two files that name every package, down to the last
dependency of a dependency, by exact version and with the hashes of its
files:

- `requirements-test.txt`: what the tests run on, PySide6 included;
- `requirements-build.txt`: what builds the distribution, the build
  backend included.

pip is run with `--require-hashes`, so it refuses anything that is not
in the file or does not match its hash, and the distribution is built
with `python -m build --no-isolation`, so nothing is fetched on the
side. A release candidate and the release after it are thereby tested
against the same Qt, and a new version of a dependency reaches a
release only on purpose:

```terminaloutput
bin/dev_tooling/lock_requirements.sh
pytest
```

and commit the two files. The script needs [uv](https://docs.astral.sh/uv/)
and takes the newest versions that `requirements.txt`, the `test`
group and the `[build-system]` table of `pyproject.toml` allow. The
development environment itself is not pinned: `environment.yml` and
`pip install --group dev` take the ranges.

## Releases

A release is published by pressing a button, never by hand. There are
nine buttons, one GitHub workflow each, named level first and kind
second: `micro-dev`, `micro-rc` and `micro-lts`, and the same three for
`minor` and `major`. A workflow starts only when its button is pressed.
No workflow runs on a push or on a pull request.

The file `bin/release_tooling/VERSION` holds the state the buttons
read, and `release_state.py` beside it decides what a press does. A
`dev` press opens a cycle of development builds, or publishes the next
build of the open one. The `rc` press of the same level closes that
cycle with a release candidate, and the `lts` press publishes the
release, which needs at least one release candidate before it. A press
that makes no sense in the present state is refused in the first job of
its workflow.

The presses that change the kind of cycle need a safety: the press that
opens a cycle, the first release candidate, and the release. The safety
of a button is the file `<button>.safety` beside `VERSION`. It is armed
by replacing its one word `STOP` with `ALLOW` and committing, right
before the press, and every press locks all nine again.

Before it publishes, a workflow runs the suite on every supported
Python and measures the coverage. It stamps the version into
`pyproject.toml`, `src/jupyterminal/__init__.py` and the heading of
`README.md`, so none of the three is edited by hand, and neither is
`VERSION`. Until the first release the three read 0.0.0.

`packaging/arch/PKGBUILD` is the package for Arch Linux. It is kept in
this repository and not in the AUR, and it installs the release whose
tag is stamped into it. Only the `lts` buttons stamp it, so from `main`
it always installs the newest release and never a development build or
a release candidate; until the first release it names the first
development build. pacman takes the full stop in `0.0.1.dev0` for a sign of a later
version than `0.0.1`, so the package names a development build
`0.0.1dev0` and a release candidate `0.0.1rc0`. `pacman -R` removes
everything the package installed; `jupyterminal.install` beside the
PKGBUILD holds what pacman says then about what is each user's own.

The token badge in `README.md` is stamped by the workflows of release
candidates and releases from `bin/release_tooling/token_stats.json`.
The count itself can only be taken on the machine that ran the Claude
Code sessions, so before such a press:

```terminaloutput
python bin/release_tooling/token_count.py
```

and commit the changed ledger.

The buttons run on `main` alone, and every press names its version from
the one `VERSION` file. The tooling therefore publishes one line of
releases: a 1.1.1 cannot be published once 1.2.0 is out. The tooling has
tests of its own:

```terminaloutput
cd bin/release_tooling
python -m unittest discover -s tests -t .
```

Publishing needs two things set up once, outside the repository: the
nine workflow files registered with PyPI as trusted publishers of
`jupyterminal`, and the secret `CODECOV_TOKEN` for the coverage upload.
The workflows also push to `main`, which a branch protection rule must
not forbid.

## Style

The package and its tests follow PEP 8 with four-space indentation and
lines of at most 79 characters. The tooling under `bin` was taken over
from *worktoy* and keeps the style of that project: two-space
indentation and lines of at most 77 characters.

## LICENSE

*jupyterminal* is released under Apache-2.0, and contributions are
accepted under the same license. This is not a separate agreement you
have to sign. Section 5 of the license already provides that anything you
intentionally submit for inclusion is licensed under Apache-2.0 unless
you state otherwise, so the inbound and outbound terms match and no
contributor license agreement is needed.
