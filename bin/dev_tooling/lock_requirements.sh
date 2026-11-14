#!/usr/bin/env sh
#
# Apache-2.0 license
# Copyright (c) 2026 Asger Jon Vistisen
#
#  Writes the two files the release workflows install from:
#
#    requirements-test.txt    what the tests run on: the dependencies of
#                             requirements.txt and the 'test' group of
#                             pyproject.toml
#    requirements-build.txt   what builds the distribution: 'build' and
#                             the build backend named in pyproject.toml
#
#  Each names every package, down to the last dependency of a
#  dependency, by its exact version and with the hashes of its files, for
#  every supported Python at once. A workflow installs with
#  'pip install --require-hashes', which refuses anything that is not in
#  the file or does not match its hash. A newer version of a dependency
#  therefore reaches a release only when this script is run and its
#  result committed: on purpose, never by surprise.
#
#  Needs 'uv' (https://docs.astral.sh/uv/). Run it from anywhere:
#
#    bin/dev_tooling/lock_requirements.sh
#
#  then run the tests and commit the two files.

set -eu

#  Anchor the working directory so relative paths resolve
#  no matter where the script is invoked from.
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"

#  The oldest supported Python; the files hold for it and all later ones.
oldest="3.12"

compile() {
  uv pip compile \
    --universal \
    --python-version "$oldest" \
    --generate-hashes \
    --upgrade \
    --custom-compile-command "bin/dev_tooling/lock_requirements.sh" \
    "$@"
}

compile requirements.txt --group test \
  --output-file requirements-test.txt

#  'python -m build --no-isolation' builds with what is installed, so the
#  backend is listed here and not fetched, unpinned, at build time. The
#  bound repeats 'requires' under [build-system] in pyproject.toml.
printf '%s\n' 'build' 'setuptools>=77.0.0' | compile - \
  --output-file requirements-build.txt
