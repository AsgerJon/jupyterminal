"""Shared helpers and fixtures: a shell made of bytes, a real zsh running
under the session model, and the view and the window off screen."""

from __future__ import annotations

import os
import select
import shutil
import time

import pytest

#  Always off screen: a run of the tests must never open windows on the
#  desktop of whoever started it.
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from jupyterminal.config import Config  # noqa: E402
from jupyterminal.session import PROMPT, Session  # noqa: E402
from jupyterminal.ui.view import PAD_LEFT, TerminalView  # noqa: E402

ZSH = shutil.which("zsh")
assert ZSH is not None, "the tests start a real zsh; install zsh"

#  The fixtures that start a real shell on a pseudo-terminal.
LIVE_FIXTURES = frozenset({"shell", "live_window"})


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items) -> None:
    """Mark every test that starts a real shell as ``live`` and run those
    last, so that a broken build fails on the fast tests within seconds.
    ``-m "not live"`` runs the fast tests alone."""
    for item in items:
        if LIVE_FIXTURES.intersection(item.fixturenames):
            item.add_marker(pytest.mark.live)
    items.sort(key=lambda item: item.get_closest_marker("live") is not None)


# -- a shell made of bytes ---------------------------------------------------
#
# What zsh with the integration sends, written out by hand, so that the
# session and the view can be tested without a shell.

NONCE = "n0nce"
COLS = 40


def mark(kind: str, *fields: str) -> bytes:
    body = ";".join((kind,) + fields)
    return f"\x1b]7717;{NONCE};{body}\x07".encode()


def prompt(status: int = 0, text: str = "% ") -> bytes:
    """What zsh sends between the end of a command and a ready prompt."""
    return (mark("E", str(status)) + b" " * COLS + b"\r\r"
            + mark("P", str(status), "on", "/work")
            + b"\r\x1b[0m\x1b[27m\x1b[24m\x1b[J" + mark("A") + text.encode()
            + b"\x1b[K" + mark("I") + b"\x1b[?2004h")


def submit(command: str, typed: str | None = None) -> bytes:
    """What zsh sends when a line is accepted and its command starts."""
    typed = command if typed is None else typed
    return (typed.encode() + mark("F", typed) + b"\x1b[?2004l\r\r\n"
            + mark("T") + mark("C", "/work", command))


# -- a stand-in for nvidia-smi -----------------------------------------------
#
# The first example of the spec is the output of nvidia-smi: a table with
# fixed columns and spaces up to its right edge.  This program prints such
# a table, in color when its output is a terminal and plain when it is
# not, so the tests need no graphics card.  Where the real program is
# installed, they run it as well.

_SMI_TABLE = (
    "{B}Sat Nov 14 03:00:00 2026{N}       ",
    "+-------------------------------------------------------+",
    "| {B}STAND-IN-SMI 1.0{N}         Driver: 1.0     Devices: 1   |",
    "|-----------------------+-----------------+-------------|",
    "| Device  Name          | Memory          | Load  Temp  |",
    "|=======================+=================+=============|",
    "|   0     Stand-In Card | 123MiB / 11GiB  |   {Y}3%{N}   {G}41C{N}  |",
    "+-----------------------+-----------------+-------------+",
    "                                                         ",
    "+-------------------------------------------------------+",
    "| Processes:                               none running |",
    "+-------------------------------------------------------+",
)
_SMI_SCRIPT = """#!/bin/sh
if [ -t 1 ]; then
  B=$(printf '\\033[1m') G=$(printf '\\033[32m')
  Y=$(printf '\\033[33m') N=$(printf '\\033[0m')
else
  B= G= Y= N=
fi
cat <<END_OF_TABLE
%s
END_OF_TABLE
"""

#: What the stand-in prints, colors aside, and what copying that gives.
SMI_TEXT = "\n".join(_SMI_TABLE).format(B="", G="", Y="", N="")
SMI_COPIED = "\n".join(line.rstrip(" ") for line in SMI_TEXT.split("\n"))
#: The commands to try: the stand-in, and nvidia-smi itself where it exists.
SMI_PROGRAMS = ["./smi-stand-in"] + ["nvidia-smi"] * bool(
    shutil.which("nvidia-smi"))


def install_smi(home: str) -> None:
    """Put the stand-in into a shell's home directory."""
    path = os.path.join(home, "smi-stand-in")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(_SMI_SCRIPT % "\n".join(_SMI_TABLE).format(
            B="${B}", G="${G}", Y="${Y}", N="${N}"))
    os.chmod(path, 0o755)


# -- a real shell ------------------------------------------------------------


class LiveShell:
    """A session with a real shell, pumped by hand instead of by Qt."""

    def __init__(self, home, rc: str = "", cols: int = 100, rows: int = 30,
                 files: dict[str, str] | None = None, **kwargs) -> None:
        self.home = str(home)
        (home / ".zshrc").write_text(rc, encoding="utf-8")
        for name, text in (files or {}).items():
            path = home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("ZDOTDIR", "PS1", "PROMPT", "RPROMPT")}
        env["HOME"] = self.home
        self.session = Session(cols, rows, **kwargs)
        self.session.start(ZSH, cwd=self.home, env=env)

    # -- driving -------------------------------------------------------------

    def pump(self, until=None, timeout: float = 20.0) -> bool:
        """Process output until ``until()`` holds (or for ``timeout``)."""
        session = self.session
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if until is not None and until():
                return True
            pty = session.pty
            if pty is None or pty.fd < 0:
                return bool(until and until())
            ready, _, _ = select.select([pty.fd], [], [], 0.02)
            if ready and not session.read_ready():
                return bool(until and until())
            pty.flush()
            session.tick()
        return bool(until and until())

    def idle(self, seconds: float = 0.3) -> None:
        """Process output for a fixed time."""
        self.pump(None, seconds)

    def at_prompt(self) -> bool:
        session = self.session
        return session.state == PROMPT and session.line_ready

    def idle_prompt(self) -> bool:
        """The line editor has said that it waits for a key and has nothing
        else to do; what was typed before has been dealt with."""
        return self.session._hold is not None

    def wait_idle(self) -> None:
        """Wait until the prompt is idle, and then a moment longer: an
        interrupt that reaches zsh in the very instant it answers the idle
        check (some two milliseconds) is put off until the next key."""
        assert self.pump(self.idle_prompt), "the prompt never became idle"
        self.idle(0.3)

    def job_in_foreground(self) -> bool:
        """A job of the shell's, not the shell itself, has the terminal."""
        pty = self.session.pty
        return os.tcgetpgrp(pty.fd) != pty.pid

    def wait_prompt(self, timeout: float = 20.0) -> None:
        assert self.pump(self.at_prompt, timeout), "no prompt"

    def type(self, text: str | bytes) -> None:
        self.session.write(text if isinstance(text, bytes)
                           else text.encode())

    def run(self, command: str, timeout: float = 30.0):
        """Submit a command, wait for it to finish, return its block."""
        session = self.session
        first_new = session._next_id
        self.type(command + "\r")

        def done() -> bool:
            return session._next_id > first_new and self.at_prompt()

        assert self.pump(done, timeout), f"did not finish: {command!r}"
        new = [b for b in session.blocks if b.id >= first_new
               and b.is_command]
        assert new, f"no block for {command!r}"
        return new[0]

    def start(self, command: str, timeout: float = 20.0):
        """Submit a command and return its block while it still runs."""
        session = self.session
        first_new = session._next_id
        self.type(command + "\r")
        assert self.pump(lambda: session.running is not None
                         and session.running.id >= first_new, timeout)
        return session.running

    def input_text(self) -> str:
        screen = self.session.input
        return "\n".join(row.text().rstrip()
                         for row in screen.grid[:screen.extent()])

    def close(self) -> None:
        self.session.close()


@pytest.fixture
def shell(tmp_path):
    """Factory for live shells; every shell is closed afterwards."""
    made: list[LiveShell] = []

    def factory(rc: str = "", **kwargs) -> LiveShell:
        home = tmp_path / f"home{len(made)}"
        home.mkdir()
        live = LiveShell(home, rc, **kwargs)
        made.append(live)
        live.wait_prompt()
        return live

    yield factory
    for live in made:
        live.close()


# -- the user interface, off screen -------------------------------------------

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["test"])
    yield app


def pump_qt(app, condition=None, timeout: float = 15.0,
            settle: float = 0.03) -> bool:
    """Run the Qt event loop until ``condition()`` holds, then a moment
    longer; without a condition, just for ``settle`` seconds."""
    from PySide6.QtCore import QEventLoop
    flags = QEventLoop.ProcessEventsFlag.AllEvents
    if condition is not None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents(flags, 20)
            if condition():
                break
            time.sleep(0.003)
    end = time.monotonic() + settle
    while time.monotonic() < end:
        app.processEvents(flags, 10)
        time.sleep(0.003)
    return bool(condition()) if condition is not None else True


class Harness:
    """The view over a session fed by hand: no shell, no window."""

    def __init__(self, app, rows: int = 12, config: Config | None = None):
        self.app = app
        self.session = Session(COLS, rows, nonce=NONCE)
        self.sent: list[bytes] = []
        self.session.write = self.sent.append
        self.config = config or Config()
        self.view = TerminalView(self.session, self.config)
        self.view.resize(*self.view.size_for(COLS, rows))
        self.view.show()
        self.session.feed(prompt())
        self.pump()

    def pump(self, seconds: float = 0.02) -> None:
        pump_qt(self.app, None, settle=seconds)

    def run(self, command: str, output: str, status: int = 0):
        self.session.feed(submit(command) + output.encode()
                          + prompt(status))
        self.pump()
        return self.session.blocks[-1]

    def point(self, block, row: int | None = None, col: int = 0) -> QPoint:
        """A point on a block: its header, or an output row and column."""
        view = self.view
        frame = view.frame()
        top = frame.tops[frame.ids.index(block.id)] - view.scroll
        if row is None:
            return QPoint(PAD_LEFT + col * view.r.cw + 2, int(top) + 8)
        y = top + block.view.header_h + row * view.r.ch + view.r.ch // 2
        return QPoint(PAD_LEFT + col * view.r.cw + 2, int(y))

    def click(self, block, mods=NONE, row=None, col=0) -> None:
        QTest.mouseClick(self.view, LEFT, mods, self.point(block, row, col))
        self.pump()

    def drag(self, start: QPoint, end: QPoint) -> None:
        QTest.mousePress(self.view, LEFT, NONE, start)
        QTest.mouseMove(self.view, QPoint((start.x() + end.x()) // 2,
                                          (start.y() + end.y()) // 2))
        QTest.mouseMove(self.view, end)
        QTest.mouseRelease(self.view, LEFT, NONE, end)
        self.pump()

    def key(self, key, mods=NONE, text: str = "") -> None:
        QTest.keyClick(self.view, key, mods)
        self.pump()

    @staticmethod
    def clipboard() -> str:
        return QGuiApplication.clipboard().text()


@pytest.fixture
def ui(qapp):
    """Factory for views over hand-fed sessions; each is closed afterwards."""
    made = []

    def factory(**kwargs) -> Harness:
        harness = Harness(qapp, **kwargs)
        made.append(harness)
        return harness

    yield factory
    for harness in made:
        harness.view.close()


class LiveWindow:
    """The real window over a real zsh, driven through the Qt event loop."""

    def __init__(self, app, home, rc: str = "", cols: int = 100,
                 rows: int = 30, config=None) -> None:
        from jupyterminal import config as config_module
        from jupyterminal.ui import MainWindow
        self.app = app
        self.home = str(home)
        (home / ".zshrc").write_text(rc, encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if k not in ("ZDOTDIR", "PS1", "PROMPT", "RPROMPT")}
        env["HOME"] = self.home
        self.config = config or config_module.Config()
        self.session = Session(cols, rows)
        self.window = MainWindow(self.session, self.config)
        self.view = self.window.view
        self.session.start(ZSH, cwd=self.home, env=env)
        self.view.attach_io()
        self.window.show()
        assert self.wait(self.at_prompt), "no prompt"

    def wait(self, condition=None, timeout: float = 20.0) -> bool:
        return pump_qt(self.app, condition, timeout)

    def settle(self, seconds: float = 0.25) -> None:
        pump_qt(self.app, None, seconds, seconds)

    def at_prompt(self) -> bool:
        return self.session.state == PROMPT and self.session.line_ready

    def run(self, command: str, timeout: float = 60.0):
        session = self.session
        first_new = session._next_id
        session.write(command.encode() + b"\r")
        assert self.wait(lambda: session._next_id > first_new
                         and self.at_prompt(), timeout), command
        return [b for b in session.blocks if b.id >= first_new
                and b.is_command][0]

    def start(self, command: str, timeout: float = 20.0):
        session = self.session
        first_new = session._next_id
        session.write(command.encode() + b"\r")
        assert self.wait(lambda: session.running is not None
                         and session.running.id >= first_new, timeout)
        return session.running

    def block_point(self, block, dy: int = 3):
        """A point inside a block's header, in view coordinates."""
        from PySide6.QtCore import QPoint
        view = self.view
        frame = view.frame()
        index = frame.ids.index(block.id)
        return QPoint(120, int(frame.tops[index] - view.scroll) + dy + 4)

    def close(self) -> None:
        self.session.close()
        self.window.close()


@pytest.fixture
def live_window(qapp, tmp_path):
    made: list[LiveWindow] = []

    def factory(rc: str = "PS1='%% '\n", **kwargs) -> LiveWindow:
        home = tmp_path / f"uihome{len(made)}"
        home.mkdir()
        live = LiveWindow(qapp, home, rc, **kwargs)
        made.append(live)
        return live

    yield factory
    for live in made:
        live.close()
