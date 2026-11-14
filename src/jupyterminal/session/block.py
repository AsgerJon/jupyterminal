"""The block: one command and everything it printed."""

from __future__ import annotations

import os
import time

from ..term import LineStore, Screen

COMMAND = "command"
BACKGROUND = "background"

RUNNING = "running"
FINISHED = "finished"


class Block:
    """One entry of the session.

    While the command runs, ``screen`` is the live terminal screen it draws
    on and ``store`` receives the lines that scroll off it.  When the
    command finishes the screen is frozen into the store and dropped; from
    then on the block is just its logical lines.
    """

    __slots__ = ("id", "kind", "command", "cwd", "branch", "label",
                 "started", "_t0", "duration", "exit_code", "state",
                 "not_run", "fullscreen", "cleared", "store", "screen",
                 "collapsed", "filter", "visible", "find", "view")

    def __init__(self, kind: str, cols: int, rows: int, command: str = "",
                 cwd: str = "", branch: str = "") -> None:
        self.id = 0
        self.kind = kind
        #: The exact text the user submitted ("" for background output).
        self.command = command
        self.cwd = cwd
        self.branch = branch
        self.label = "background output" if kind == BACKGROUND else ""
        self.started = time.time()
        self._t0 = time.monotonic()
        self.duration: float | None = None
        self.exit_code: int | None = None
        self.state = RUNNING
        #: The shell turned the text down before running anything.
        self.not_run = False
        #: A full-screen program ran in this block.
        self.fullscreen = False
        self.cleared = False
        self.store = LineStore()
        self.screen: Screen | None = Screen(cols, rows, self.store)
        # View state, owned by the user interface.
        self.collapsed = False
        self.filter = None
        self.visible: list[int] | None = None
        self.find = None
        self.view = None

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    @property
    def failed(self) -> bool:
        return self.state == FINISHED and self.exit_code not in (None, 0)

    @property
    def is_command(self) -> bool:
        return self.kind == COMMAND

    def elapsed(self) -> float:
        if self.duration is not None:
            return self.duration
        return time.monotonic() - self._t0

    def finish(self, exit_code: int | None) -> None:
        """The command is over: freeze its output."""
        if self.state == FINISHED:
            return
        self.duration = time.monotonic() - self._t0
        self.exit_code = exit_code
        self.state = FINISHED
        # A running block always has its screen: this is the one place
        # that takes it away.
        self.screen.finish()
        self.screen = None

    def has_output(self) -> bool:
        store = self.store
        if store.texts or store.open_parts is not None:
            return True
        screen = self.screen
        if screen is not None:
            for row in screen.grid:
                if not row.is_blank():
                    return True
        return False

    def lines(self) -> list[str]:
        """Every logical line of output so far."""
        screen = self.screen
        if screen is None:
            return self.store.texts
        return [text for text, _ in screen.all_lines()]

    def line_count(self) -> int:
        """``len(self.lines())``, at no more than the cost of what is on
        the screen."""
        screen = self.screen
        if screen is None:
            return len(self.store.texts)
        return screen.line_count()


def git_branch(path: str) -> str:
    """The checked-out branch of the repository containing ``path``.

    Read straight from the repository files; returns "" outside one.
    """
    if not path:
        return ""
    try:
        current = os.path.abspath(path)
        while True:
            dot_git = os.path.join(current, ".git")
            if os.path.isdir(dot_git):
                git_dir = dot_git
                break
            if os.path.isfile(dot_git):
                with open(dot_git, encoding="utf-8",
                          errors="replace") as handle:
                    pointer = handle.read().strip()
                if not pointer.startswith("gitdir:"):
                    return ""
                git_dir = os.path.join(current, pointer[7:].strip())
                break
            parent = os.path.dirname(current)
            if parent == current:
                return ""
            current = parent
        with open(os.path.join(git_dir, "HEAD"), encoding="utf-8",
                  errors="replace") as handle:
            head = handle.read().strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head[4:].strip()
        prefix = "refs/heads/"
        return ref[len(prefix):] if ref.startswith(prefix) else ref
    return head[:7]
