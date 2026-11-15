"""Run jupyterminal as it is in this checkout.

    python main.py

For looking at the work in progress, from a terminal or with the Run
button of an editor.  What starts is the code under ``src`` next to this
file, whether or not some version of the package is installed.  Arguments
go to the application unchanged: ``python main.py --help`` lists them.
"""

from __future__ import annotations

import os
import sys

#: The package of this checkout.
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")


def main() -> int:
    """Start the application from the source tree and return its exit
    status."""
    # First on the path: an installed jupyterminal must not be the one
    # that runs.
    sys.path.insert(0, SRC)
    from jupyterminal.app import main as run
    return run()


if __name__ == "__main__":
    sys.exit(main())
