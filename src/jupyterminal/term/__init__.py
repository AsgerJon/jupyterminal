"""Terminal emulation core: styles, the screen model and the parser.

Nothing in this package knows about shells, blocks or the user interface.
"""

from .screen import LineStore, Row, Screen
from .terminal import Terminal

__all__ = ["LineStore", "Row", "Screen", "Terminal"]
