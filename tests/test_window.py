"""The application window around the view, over a session fed by hand,
and the helpers the other tests wait with.  Here the commands are typed
in the shell's own line editor; the window with a text box for them is
in test_cell.py."""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QMargins, QPoint, QSize
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QMenu

from conftest import COLS, NONCE, prompt, pump_qt
from jupyterminal.config import Config
from jupyterminal.session import CLOSED, Session
from jupyterminal.ui import MainWindow, TerminalView


@pytest.fixture
def window(qapp):
    """A shown window over a session at its first prompt."""
    session = Session(COLS, 12, nonce=NONCE)
    made = MainWindow(session, Config(input="shell"))
    made.show()
    session.feed(prompt())
    pump_qt(qapp)
    yield made
    made.close()


def test_the_window_is_the_view_and_nothing_around_it(window):
    view = window.view
    assert isinstance(view, TerminalView) and view.parent() is window
    assert window.layout().contentsMargins() == QMargins(0, 0, 0, 0)
    assert window.layout().count() == 1
    assert window.layout().itemAt(0).widget() is view
    assert view.geometry() == window.rect()
    assert window.cell is None and view.cell is None


def test_the_window_is_sized_for_the_sessions_terminal(qapp):
    session = Session(72, 17, nonce=NONCE)
    made = MainWindow(session, Config(input="shell"))
    assert made.size() == QSize(*made.view.size_for(72, 17))
    assert made.focusWidget() is made.view           # before it is shown
    made.show()
    pump_qt(qapp)
    assert (session.cols, session.rows) == (72, 17)  # and it fits exactly
    made.close()


def test_the_window_gives_the_terminal_the_keys(window, qapp):
    assert window.focusWidget() is window.view


def lightest(image, rect) -> int:
    """How light the lightest pixel of ``rect`` is.  Where text is lighter
    than what it is written on, that is the text."""
    return max(image.pixelColor(x, y).lightness()
               for y in range(rect.top(), rect.bottom() + 1)
               for x in range(rect.left(), rect.right() + 1))


def test_a_menu_of_the_window_is_drawn_in_the_colors_of_the_theme(window):
    """And not in those of the desktop: the black text of a desktop with
    a light color scheme does not show on the dark background."""
    theme = window.view.theme
    menu = QMenu(window.view)
    live = menu.addAction("Copy output")
    chosen = menu.addAction("Copy command")
    line = menu.addSeparator()
    off = menu.addAction("Re-run command")
    off.setEnabled(False)
    menu.ensurePolished()
    menu.adjustSize()
    menu.setActiveAction(chosen)
    image = menu.grab().toImage()
    place = menu.actionGeometry
    inside = QPoint(2, 1)
    assert image.pixelColor(0, 0) == theme.input_border         # a frame
    assert image.pixelColor(place(live).topLeft() + inside) == \
        theme.background
    assert image.pixelColor(place(chosen).topLeft() + inside) == \
        theme.selection
    # A separator is one line in the middle of its room, short of the sides.
    room = place(line)
    middle = room.center()
    assert image.pixelColor(middle) == theme.input_border
    assert image.pixelColor(middle.x(), room.top() + 1) == theme.background
    assert image.pixelColor(room.left() + 2, middle.y()) == theme.background
    # What can be chosen is written light, what cannot is written dim.
    light, dim = theme.foreground.lightness(), theme.dim.lightness()
    assert lightest(image, place(live)) > (light + dim) // 2
    assert lightest(image, place(chosen)) > (light + dim) // 2
    assert theme.background.lightness() < lightest(image, place(off)) <= dim


def test_the_title_follows_the_program(window, qapp):
    assert window.windowTitle() == "jupyterminal"
    window.view.session.feed(b"\x1b]2;vim notes.txt\x07")
    assert window.windowTitle() == "vim notes.txt - jupyterminal"
    window.view.session.feed(b"\x1b]2;\x07")         # and takes it back
    assert window.windowTitle() == "jupyterminal"


def test_the_window_closes_when_the_session_ends(window, qapp):
    assert window.isVisible()
    window.view.session.close()
    assert not window.isVisible()


def test_closing_the_window_ends_the_session(window, qapp):
    session = window.view.session
    assert session.state != CLOSED
    assert window.close() is True
    assert session.state == CLOSED and not window.isVisible()


def test_a_request_to_close_is_never_turned_down(window):
    event = QCloseEvent()
    event.ignore()
    window.closeEvent(event)
    assert event.isAccepted()
    assert window.view.session.state == CLOSED


# -- the helpers that wait ---------------------------------------------------

def test_waiting_for_something_that_never_comes_says_so(qapp):
    began = time.monotonic()
    assert pump_qt(qapp, lambda: False, timeout=0.05) is False
    assert 0.05 <= time.monotonic() - began < 5.0
    assert pump_qt(qapp, lambda: True, timeout=0.05) is True
    assert pump_qt(qapp) is True                     # nothing to wait for
