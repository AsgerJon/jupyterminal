#!/usr/bin/env sh
#
# Apache-2.0 license
# Copyright (c) 2026 Asger Jon Vistisen
#

set -eu

#  Anchor the working directory so relative paths resolve
#  no matter where the script is invoked from.
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"

export PYTHONDONTWRITEBYTECODE=1

runTests() {
  reportDir="htmlcov"

  #  'pyproject.toml' already makes pytest measure the branch coverage of
  #  'jupyterminal' and of 'tests'; this adds the browsable report.
  pytest \
    tests \
    --cov-report=html:"$reportDir"

  if [ -f "$reportDir/index.html" ]; then
    setsid xdg-open "$reportDir/index.html" >/dev/null 2>&1
  fi
}

runTests
