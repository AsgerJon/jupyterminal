"""The text box commands are typed in: it edits like every other text
field on the desktop, and hands what was typed to the shell on
Ctrl+Enter.

Most of these tests feed the session by hand; the last ones type into
the window over a real zsh.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QGuiApplication,
                           QInputMethodEvent, QMouseEvent, QPalette,
                           QTextCharFormat, QTextCursor)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame

from conftest import (COLS, NONCE, answer, escaped, mark, prompt, pump_qt,
                      submit)
from jupyterminal.config import Config
from jupyterminal.session import Session
from jupyterminal.session import session as session_module
from jupyterminal.shell.integration import (EDIT_KEY, LOOK_KEY,
                                            SET_BUFFER_KEY)
from jupyterminal.ui import MainWindow
from jupyterminal.ui import cell as cell_module
from jupyterminal.ui.cell import MAX_LINES, Editor, InputCell, char_format
from jupyterminal.ui.view import IN_TOP, PAD_LEFT, SCROLL_W, TerminalView
from test_view_selection import repaints
from test_zsh import STAND_INS

Key = Qt.Key
NONE = Qt.KeyboardModifier.NoModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
CTRL = Qt.KeyboardModifier.ControlModifier
ALT = Qt.KeyboardModifier.AltModifier
LEFT = Qt.MouseButton.LeftButton


class Runtime:
    """What the session needs of the shell's side, without a shell."""

    nonce = NONCE

    def __init__(self) -> None:
        self.buffers: list[str] = []
        self.edits: list[tuple] = []
        self.looks: list[tuple] = []
        self.cleaned = 0

    def write_buffer(self, text: str) -> None:
        self.buffers.append(text)

    def write_edit(self, text: str, cursor: int, key: bytes,
                   fallback: str, edited: bool) -> None:
        self.edits.append((key, fallback, text, cursor))

    def write_look(self, text: str, cursor: int, number: int) -> None:
        self.looks.append((text, cursor, number))

    def cleanup(self) -> None:
        self.cleaned += 1


class Box:
    """The window with its text box, over a session fed by hand."""

    def __init__(self, app, config: Config | None = None,
                 plugins: bool = False) -> None:
        self.app = app
        #: Whether the shell says that it has plugins for command lines.
        self.plugins = plugins
        self.runtime = Runtime()
        self.session = Session(COLS, 12, runtime=self.runtime)
        self.sent: list[bytes] = []
        self.session.write = self.sent.append
        self.window = MainWindow(self.session, config or Config())
        self.view = self.window.view
        self.cell = self.window.cell
        self.editor = self.cell.editor
        self.window.show()
        self.session.feed(prompt(plugins=plugins))
        self.pump()

    def pump(self) -> None:
        pump_qt(self.app, None, settle=0.02)

    def type(self, text: str) -> None:
        QTest.keyClicks(self.editor, text)
        self.pump()

    def key(self, key, mods=NONE, widget=None) -> None:
        QTest.keyClick(widget or self.editor, key, mods)
        self.pump()

    def run(self, command: str, output: str = "", status: int = 0):
        self.session.feed(submit(command) + output.encode()
                          + prompt(status, plugins=self.plugins))
        self.pump()
        return self.session.blocks[-1]

    def answer(self, text: str, cursor: int | None = None,
               shown: bytes = b"") -> None:
        """What the shell sends when its line editor has done what the
        box asked for: what it draws, then text and cursor."""
        self.session.feed(shown + answer(text, cursor))
        self.pump()

    def hint(self, go: str = "", colors: str = "") -> None:
        """What the shell's plugins make of the text as it is now: the
        line they would go on to, and their colors.  (Of texts that
        follow each other quickly the shell is asked about the last, when
        it has answered about the first.)"""
        while self.session._look is not None:
            number = str(self.session._look[0])
            self.session.feed(mark("V", number, escaped(go),
                                   escaped(colors)))
        self.pump()

    def formats(self) -> list[list[tuple]]:
        """The colors that lie on the text: for every line, where each
        starts, how far it goes, and its format."""
        lines = []
        block = self.editor.document().firstBlock()
        while block.isValid():
            # (A copy of the format: the list it is in does not last.)
            lines.append([(one.start, one.length,
                           QTextCharFormat(one.format))
                          for one in block.layout().formats()])
            block = block.next()
        return lines

    def header(self, block) -> QPoint:
        frame = self.view.frame()
        top = frame.tops[frame.ids.index(block.id)] - self.view.scroll
        return QPoint(PAD_LEFT + 2, int(top) + 8)

    def line(self, block, row: int) -> QPoint:
        """A point in a line of a block's output."""
        height = self.view.r.ch
        down = block.view.header_h + row * height + height // 2
        return QPoint(PAD_LEFT + 2, self.header(block).y() - 8 + int(down))

    @staticmethod
    def clipboard() -> str:
        return QGuiApplication.clipboard().text()


@pytest.fixture
def box(qapp):
    made = []

    def factory(**kwargs) -> Box:
        made.append(Box(qapp, **kwargs))
        return made[-1]

    yield factory
    for one in made:
        one.window.close()


# -- the window ---------------------------------------------------------------

def test_the_text_box_sits_under_the_view(box):
    b = box()
    layout = b.window.layout()
    assert layout.count() == 2
    assert layout.itemAt(0).widget() is b.view
    assert layout.itemAt(1).widget() is b.cell
    assert b.view.cell is b.cell
    assert b.cell.geometry().top() == b.view.geometry().bottom() + 1
    assert b.cell.geometry().bottom() == b.window.rect().bottom()


def test_the_window_makes_room_for_the_text_box(qapp):
    session = Session(72, 17, nonce=NONCE)
    window = MainWindow(session, Config())
    width, height = window.view.size_for(72, 17)
    assert window.width() == width
    assert window.height() == height + window.cell.sizeHint().height()
    window.show()
    pump_qt(qapp)
    assert (session.cols, session.rows) == (72, 17)
    window.close()


def test_the_shells_own_input_line_is_not_drawn(box):
    b = box()
    assert b.session.prompt_active
    assert b.view._input_rows() == 0
    assert b.view.frame().input_h == 0
    assert b.view.frame().view_h == b.view.height()


def test_the_text_box_has_the_font_and_the_colors_of_the_view(box):
    b = box()
    theme = b.view.theme
    assert b.editor.font() == b.view.r.font
    assert b.cell.label.font() == b.view.r.font
    sheet = b.cell.styleSheet()
    assert f"background: {theme.input_background.name()}" in sheet
    assert f"border-top: 1px solid {theme.input_border.name()}" in sheet
    assert f"color: {theme.foreground.name()}" in sheet
    assert f"selection-background-color: {theme.selection.name()}" in sheet
    b.view.set_font_size(b.view.r.size + 3)            # zooming takes it along
    assert b.editor.font() == b.view.r.font
    assert b.editor.font().pointSizeF() == b.view.r.size


def test_the_text_starts_where_the_text_of_the_blocks_starts(box):
    b = box()
    margin = round(b.editor.document().documentMargin())
    assert margin > 0
    label, editor = b.cell.label.geometry(), b.editor.geometry()
    # The line about the shell, the box right under it, and four pixels
    # of air above and below the two.
    assert label.top() == 4 and editor.top() == label.bottom() + 1
    assert editor.bottom() == b.cell.height() - 4 - 1
    # The document of a text box keeps a margin of its own.
    assert label.left() == editor.left() == PAD_LEFT - margin
    assert label.right() == editor.right() == b.cell.width() - 1
    assert b.cell.label.indent() == 0
    # The box itself draws no frame and never scrolls sideways.
    assert b.editor.frameShape() == QFrame.Shape.NoFrame
    assert b.editor.horizontalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff


def test_the_box_is_drawn_in_the_colors_of_the_theme(box):
    b = box()
    theme = b.view.theme
    role = QPalette.ColorRole
    assert b.cell.label.palette().color(role.WindowText) == theme.dim
    palette = b.editor.palette()
    assert palette.color(role.Text) == theme.foreground
    assert palette.color(role.Highlight) == theme.selection
    assert palette.color(role.HighlightedText) == theme.foreground
    # While a command runs the box is not to be typed in, and looks it.
    assert palette.color(QPalette.ColorGroup.Disabled, role.Text) == theme.dim
    # A background of its own, under a line that sets it off from the
    # blocks.
    assert theme.input_background != theme.background
    image = b.cell.grab().toImage()
    assert image.pixelColor(2, 0) == theme.input_border
    assert image.pixelColor(2, 2) == theme.input_background


def test_the_scroll_bar_of_the_box_is_the_slim_one_of_the_view(box):
    b = box()
    theme = b.view.theme
    for _ in range(MAX_LINES + 4):
        b.key(Key.Key_Return)
    bar = b.editor.verticalScrollBar()
    assert bar.isVisible() and bar.width() == SCROLL_W
    middle = SCROLL_W // 2
    bar.setValue(0)
    b.pump()
    image = bar.grab().toImage()
    # The handle starts at the very top (no arrows), two pixels in, and
    # keeps three pixels from the sides; the rest is see-through.
    assert image.pixelColor(middle, 0).alpha() == 0
    assert image.pixelColor(middle, 4) == theme.scrollbar
    assert image.pixelColor(1, 4).alpha() == 0
    assert image.pixelColor(SCROLL_W - 2, 4).alpha() == 0
    assert image.pixelColor(middle, image.height() - 5).alpha() == 0
    bar.setValue(bar.maximum())
    b.pump()
    image = bar.grab().toImage()
    assert image.pixelColor(middle, 4).alpha() == 0
    assert image.pixelColor(middle, image.height() - 5) == theme.scrollbar
    assert image.pixelColor(middle, image.height() - 1).alpha() == 0


# -- where the shell is -------------------------------------------------------

def test_the_line_above_the_box_says_where_the_shell_is(box, tmp_path):
    b = box()
    assert b.cell.label.text() == "/work"
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    b.session.feed(submit("cd repo") + mark("E", "0")
                   + mark("P", "0", "on", str(repo)) + mark("A") + b"% "
                   + mark("I"))
    b.pump()
    assert b.cell.label.text().endswith("repo · main")


def test_before_the_shell_has_said_where_it_is_the_line_is_empty(qapp):
    session = Session(COLS, 12, nonce=NONCE)
    window = MainWindow(session, Config())
    assert window.cell.label.text() == ""
    assert not window.cell.editor.isEnabled()           # and not ready yet
    session.on_incomplete("for i in 1 2; do")           # a note stands alone
    assert window.cell.label.text() == "not run: the command is not complete"
    window.close()


def test_a_box_for_a_shell_that_waits_already_is_ready_at_once(qapp):
    session = Session(COLS, 12, nonce=NONCE)
    session.feed(prompt())
    window = MainWindow(session, Config())
    assert window.cell.label.text() == "/work"
    assert window.cell.editor.isEnabled()
    window.show()
    QTest.keyClicks(window.view, "ls")                  # and the view knows
    assert window.cell.text() == "ls"
    window.close()


def test_the_branch_is_looked_up_once_for_every_prompt(box, monkeypatch):
    looked: list[str] = []

    def branch(cwd: str) -> str:
        looked.append(cwd)
        return "main"

    monkeypatch.setattr(cell_module, "git_branch", branch)
    b = box()
    assert b.cell.label.text() == "/work · main"
    del looked[:]
    b.type("git status")                                # typing reads no disk
    b.view.sync_input()                                 # nor does time passing
    assert looked == []
    b.key(Key.Key_Return, CTRL)
    assert looked == []
    b.run("git status")                                 # a new prompt does
    assert looked == ["/work"]


# -- typing as in any other text box ------------------------------------------

def test_what_is_typed_stays_in_the_box_until_it_is_sent(box):
    b = box()
    assert b.editor.isEnabled() and b.editor.hasFocus()
    b.type("ls -la")
    assert b.cell.text() == "ls -la"
    assert b.sent == [] and b.runtime.buffers == []


def test_shift_and_the_arrows_select_and_typing_replaces_the_selection(box):
    b = box()
    b.type("git status")
    b.key(Key.Key_Left, SHIFT | CTRL)                   # the last word
    assert b.editor.textCursor().selectedText() == "status"
    b.type("log")
    assert b.cell.text() == "git log"
    b.key(Key.Key_Home, SHIFT)                          # back to the start
    assert b.editor.textCursor().selectedText() == "git log"
    b.key(Key.Key_Backspace)
    assert b.cell.text() == ""


def test_the_clipboard_keys_and_taking_back(box):
    b = box()
    b.type("echo one")
    b.key(Key.Key_A, CTRL)                              # everything
    assert b.editor.textCursor().selectedText() == "echo one"
    b.key(Key.Key_C, CTRL)
    assert b.clipboard() == "echo one" and b.cell.text() == "echo one"
    b.key(Key.Key_End)
    b.type(" two")
    b.key(Key.Key_Left, SHIFT | CTRL)
    b.key(Key.Key_X, CTRL)                              # cut the last word
    assert b.clipboard() == "two" and b.cell.text() == "echo one "
    b.key(Key.Key_V, CTRL)
    b.key(Key.Key_V, CTRL)
    assert b.cell.text() == "echo one twotwo"
    b.key(Key.Key_Z, CTRL)                              # one paste back
    assert b.cell.text() == "echo one two"
    assert b.sent == []                              # the shell saw none of it


def test_the_mouse_places_the_cursor(box):
    b = box()
    b.type("abcdef")
    rect = b.editor.cursorRect(b.editor.textCursor())
    width = b.editor.fontMetrics().horizontalAdvance("a")
    spot = QPoint(rect.left() - 4 * width + 1, rect.center().y())
    QTest.mouseClick(b.editor.viewport(), LEFT, NONE, spot)
    assert b.editor.textCursor().position() == 2
    b.type("X")
    assert b.cell.text() == "abXcdef"


def test_tab_is_not_a_character_of_a_command(box):
    b = box()
    b.type("ec")
    b.key(Key.Key_Tab)
    assert b.cell.text() == "ec" and b.editor.hasFocus()


# -- handing the command to the shell -----------------------------------------

def test_ctrl_enter_hands_the_text_to_the_shell_and_empties_the_box(box):
    b = box()
    b.type("ls -la")
    b.key(Key.Key_Return, CTRL)
    assert b.runtime.buffers == ["ls -la"]
    assert b.sent == [SET_BUFFER_KEY, b"\r"]
    assert b.cell.text() == ""
    assert not b.editor.document().isUndoAvailable()    # nothing to take back
    # The shell is busy now: the keys are for what runs.
    assert not b.editor.isEnabled() and b.view.hasFocus()
    b.run("ls -la", "total 0\r\n")
    assert b.editor.isEnabled() and b.editor.hasFocus()


def test_sending_a_command_shows_the_end_of_the_output(box):
    b = box()
    for i in range(6):
        b.run(f"cmd {i}", "x\r\n" * 4)
    assert b.view.frame().max_scroll > 0
    b.view._set_scroll(0)                               # reading further up
    assert b.view.scroll == 0
    b.type("ls")
    b.key(Key.Key_Return, CTRL)
    assert b.view.scroll == b.view.frame().max_scroll


def test_the_enter_of_the_number_pad_does_the_same(box):
    b = box()
    b.type("true")
    b.key(Key.Key_Enter, CTRL | Qt.KeyboardModifier.KeypadModifier)
    assert b.runtime.buffers == ["true"]


def test_ctrl_enter_on_an_empty_box_does_nothing(box):
    b = box()
    b.key(Key.Key_Return, CTRL)
    b.type("   ")
    b.key(Key.Key_Return, CTRL)
    assert b.sent == [] and b.runtime.buffers == []
    assert b.cell.text() == "   "
    assert b.editor.isEnabled()


def test_enter_starts_a_new_line(box):
    b = box()
    b.type("for i in 1 2")
    b.key(Key.Key_Return)
    b.type("do echo $i")
    b.key(Key.Key_Enter, Qt.KeyboardModifier.KeypadModifier)
    b.type("done")
    assert b.cell.text() == "for i in 1 2\ndo echo $i\ndone"
    assert b.sent == [] and b.editor.isEnabled()        # nothing was sent
    b.key(Key.Key_Return, CTRL)
    assert b.runtime.buffers == ["for i in 1 2\ndo echo $i\ndone"]


def test_only_ctrl_enter_alone_sends(box):
    b = box()
    b.type("ls")
    for mods in (SHIFT, ALT, CTRL | SHIFT, CTRL | ALT):
        b.key(Key.Key_Return, mods)
    assert b.cell.text() == "ls\n\n\n\n"
    assert b.sent == [] and b.runtime.buffers == []


def test_the_box_grows_with_its_lines_and_then_scrolls(box):
    b = box()
    line = b.editor.fontMetrics().lineSpacing()
    margins = round(2 * b.editor.document().documentMargin())
    off = Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert b.editor.height() == line + margins
    assert b.editor.verticalScrollBarPolicy() == off
    for count in range(2, MAX_LINES + 1):
        b.key(Key.Key_Return)
        assert b.editor.height() == count * line + margins
    assert b.editor.verticalScrollBarPolicy() == off
    b.key(Key.Key_Return)                        # one more than fits
    assert b.editor.height() == MAX_LINES * line + margins
    assert b.editor.verticalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAsNeeded
    b.key(Key.Key_A, CTRL)
    b.key(Key.Key_Delete)
    assert b.editor.height() == line + margins


def test_a_command_that_is_not_complete_comes_back(box):
    b = box()
    b.type("for i in 1 2; do")
    b.key(Key.Key_Return, CTRL)
    assert b.cell.text() == ""
    # The shell takes the line and asks for the rest of it.
    b.session.feed(mark("F", "for i in 1 2; do") + b"\r\nfor> " + mark("I"))
    b.pump()
    assert b.sent[-1] == b"\x03"                        # called off
    assert b.cell.text() == "for i in 1 2; do"
    assert b.cell.label.text() == \
        "/work · not run: the command is not complete"
    assert not b.editor.isEnabled()                     # until the new prompt
    b.session.feed(prompt(130))
    b.pump()
    assert b.editor.isEnabled() and b.editor.hasFocus()
    assert b.editor.textCursor().atEnd()
    b.type(" echo $i; done")                            # typing ends the note
    assert b.cell.label.text() == "/work"
    assert b.cell.text() == "for i in 1 2; do echo $i; done"


# -- Ctrl+C, Ctrl+D, Ctrl+L ---------------------------------------------------

def test_ctrl_c_with_nothing_selected_drops_the_line(box):
    b = box()
    b.type("rm -rf /")
    b.key(Key.Key_C, CTRL)
    assert b.cell.text() == ""
    assert b.sent == []                                 # the box's own doing
    b.key(Key.Key_Z, CTRL)                              # and it can come back
    assert b.cell.text() == "rm -rf /"


def test_ctrl_c_copies_what_is_selected_in_the_output(box):
    b = box()
    block = b.run("ls", "a.txt\r\nb.txt\r\n")
    b.type("echo")
    # Selected with the mouse while the keyboard stayed with the box.
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 5))
    b.key(Key.Key_C, CTRL)
    assert b.clipboard() == "a.txt"
    assert b.cell.text() == "echo"                      # the line stays
    assert b.view._sel is None                          # the selection goes
    b.key(Key.Key_C, CTRL)                              # so this one drops it
    assert b.cell.text() == ""


def test_ctrl_d_on_an_empty_box_ends_the_shells_input(box):
    b = box()
    b.type("x")
    b.key(Key.Key_D, CTRL)
    assert b.sent == []                                 # not with text in it
    b.key(Key.Key_A, CTRL)
    b.key(Key.Key_Backspace)
    b.key(Key.Key_D, CTRL)
    assert b.sent == [b"\x04"]


def test_ctrl_l_clears_the_screen(box):
    b = box()
    b.type("half a command")
    b.key(Key.Key_L, CTRL)
    assert b.sent == [b"\x0c"] and b.cell.text() == "half a command"


# -- what only the shell can do: completing, the history ----------------------

#: What the line editor draws when it lists two files under a line of
#: ``width`` cells, and comes back to the end of that line.
def listing(width: int) -> bytes:
    return (b"\r\nalphabet.txt  alpha.txt\x1b[A\r\x1b[%dC" % width)


def test_tab_has_the_shell_complete_the_word(box):
    b = box()
    b.type("ls be")
    b.key(Key.Key_Tab)
    assert b.runtime.edits == [(b"\t", "expand-or-complete", "ls be", 5)]
    assert b.sent == [EDIT_KEY] and b.session.editing
    assert b.cell.text() == "ls be"                     # until it answers
    assert b.editor.isEnabled() and b.editor.hasFocus()
    b.answer("ls beta.txt ")
    assert not b.session.editing
    assert b.cell.text() == "ls beta.txt "
    assert b.editor.textCursor().position() == 12
    b.key(Key.Key_Z, CTRL)                              # one step to take back
    assert b.cell.text() == "ls be"
    b.key(Key.Key_Backtab, SHIFT)                       # the other way round
    assert b.runtime.edits[-1] == (b"\x1b[Z", "", "ls be", 5)
    b.answer("ls be")                                   # nothing to do
    assert b.cell.text() == "ls be"
    b.key(Key.Key_Z, CTRL)                              # and nothing new to
    assert b.cell.text() != "ls be"                     # take back: typing is


def test_qt_counts_in_units_and_the_shell_in_characters():
    text = "a\U0001f600b\uffffc"        # the second one is two units wide
    assert [cell_module._characters(text, units) for units in range(7)] == \
        [0, 1, 2, 2, 3, 4, 5]           # (half of it counts as one)
    units = [cell_module._units(text, count) for count in range(6)]
    assert units == [0, 1, 3, 4, 5, 6]
    assert {type(number) for number in units} == {int}


def test_the_shell_counts_the_cursor_in_characters(box):
    b = box()
    b.cell.set_text("echo \U0001f600 al; ls")           # two units to Qt
    cursor = b.editor.textCursor()
    cursor.setPosition(10)                              # behind "al"
    b.editor.setTextCursor(cursor)
    b.key(Key.Key_Tab)
    assert b.runtime.edits == [
        (b"\t", "expand-or-complete", "echo \U0001f600 al; ls", 9)]
    b.answer("echo \U0001f600 alpha; ls", 12)
    assert b.cell.text() == "echo \U0001f600 alpha; ls"
    assert b.editor.textCursor().position() == 13
    assert not b.editor.textCursor().hasSelection()


def test_up_and_down_are_the_keys_of_the_shells_history(box):
    b = box()
    b.type("dra")
    b.key(Key.Key_Up)
    assert b.runtime.edits == [(b"\x1b[A", "up-line-or-history", "dra", 3)]
    b.answer("echo two")
    assert b.cell.text() == "echo two" and b.editor.textCursor().atEnd()
    b.key(Key.Key_Down)
    assert b.runtime.edits[-1] == (
        b"\x1b[B", "down-line-or-history", "echo two", 8)
    b.answer("dra")
    assert b.cell.text() == "dra"
    # The shell is asked for what the key does there, in the form the
    # key has for it just now.
    b.session.feed(b"\x1b[?1h")
    b.key(Key.Key_Up)
    assert b.runtime.edits[-1][0] == b"\x1bOA"


def test_inside_several_lines_up_and_down_move_the_cursor(box):
    b = box()
    b.type("first")
    b.key(Key.Key_Return)
    b.type("second")
    b.key(Key.Key_Up)                                   # to the first line
    assert b.editor.textCursor().blockNumber() == 0
    b.key(Key.Key_Down)                                 # and back
    assert b.editor.textCursor().blockNumber() == 1
    b.key(Key.Key_Up, SHIFT)                            # selecting, twice
    b.key(Key.Key_Up, SHIFT)
    b.key(Key.Key_Down, SHIFT)
    b.key(Key.Key_Down, SHIFT)
    b.key(Key.Key_Down, SHIFT)
    assert b.cell.text() == "first\nsecond" and b.runtime.edits == []
    b.key(Key.Key_Down)                                 # from the last line
    assert b.runtime.edits == [
        (b"\x1b[B", "down-line-or-history", "first\nsecond", 12)]
    b.answer("first\nsecond")
    b.key(Key.Key_Home, CTRL)
    b.key(Key.Key_Up)                                   # from the first one
    assert b.runtime.edits[-1] == (
        b"\x1b[A", "up-line-or-history", "first\nsecond", 0)


def test_keys_pressed_while_the_shell_works_wait_for_its_answer(box):
    b = box()
    b.type("ls be")
    b.key(Key.Key_Tab)
    b.type("-l")
    b.key(Key.Key_Tab)                                  # asked again later
    b.type("x")
    b.key(Key.Key_Return, CTRL)
    assert b.cell.text() == "ls be" and b.sent == [EDIT_KEY]
    b.answer("ls beta.txt ")
    # What was typed first is typed; the second Tab asks with it, and
    # holds back what came after it.
    assert b.cell.text() == "ls beta.txt -l"
    assert b.runtime.edits[-1] == (
        b"\t", "expand-or-complete", "ls beta.txt -l", 14)
    assert b.sent == [EDIT_KEY, EDIT_KEY] and b.runtime.buffers == []
    b.answer("ls beta.txt -l")
    assert b.runtime.buffers == ["ls beta.txt -lx"]     # and then it ran
    assert b.cell.text() == ""


def test_an_answer_of_the_shell_counts_as_a_change_of_the_text(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    b.session.on_incomplete("for f in al")              # with a note about it
    assert b.cell.label.text().endswith("not complete")
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 5))
    b.key(Key.Key_Tab)
    assert b.view.has_text_selection()                  # a key alone: no
    b.answer("for f in alpha")
    assert b.cell.text() == "for f in alpha"
    assert b.cell.label.text() == "/work"               # the note has gone
    assert not b.view.has_text_selection()              # and the selection


def test_ctrl_c_does_not_wait_for_an_answer_of_the_shell(box):
    b = box()
    b.type("ls al")
    b.key(Key.Key_Tab)
    b.type("x")
    b.key(Key.Key_C, CTRL)                              # this takes too long
    assert b.sent == [EDIT_KEY, b"\x03"]                # the shell is told
    assert b.cell.text() == "ls al"
    b.key(Key.Key_C, CTRL | SHIFT)                      # (copying can wait)
    assert b.sent == [EDIT_KEY, b"\x03"]
    b.session.feed(b"^C" + prompt(130))                 # and gives it up
    b.pump()
    assert b.cell.text() == "ls alx" and b.editor.isEnabled()


def test_nothing_is_asked_of_a_shell_that_cannot_answer(qapp):
    session = Session(COLS, 12, nonce=NONCE)            # no line editor
    sent: list[bytes] = []
    session.write = sent.append
    window = MainWindow(session, Config())
    window.show()
    session.feed(prompt())
    pump_qt(qapp, None, settle=0.02)
    QTest.keyClicks(window.cell.editor, "ls")
    QTest.keyClick(window.cell.editor, Key.Key_Tab)
    QTest.keyClick(window.cell.editor, Key.Key_Up)
    QTest.keyClicks(window.cell.editor, "x")
    assert window.cell.text() == "lsx" and sent == []
    window.close()


def test_what_the_shell_lists_shows_under_the_blocks(box):
    b = box()
    b.type("ls al")
    b.key(Key.Key_Tab)
    b.answer("ls alpha", shown=b"ls alpha")
    assert b.view._input_span() == (1, 0)               # nothing is listed
    assert b.view.frame().input_h == 0
    b.key(Key.Key_Tab)
    b.answer("ls alpha", shown=listing(10))
    assert b.view._input_span() == (1, 1)               # one row, the list
    frame = b.view.frame()
    assert frame.input_first == 1 and frame.input_h > 0
    # The list, and not the shell's own line with the command in it.
    assert b.session.input.grid[1].text().rstrip() == \
        "alphabet.txt  alpha.txt"
    image = b.view.grab().toImage()
    top = frame.view_h + IN_TOP
    ink = sorted({x for x in range(image.width())
                  for y in range(top, top + b.view.r.ch)
                  if image.pixelColor(x, y) != b.view.theme.input_background})
    assert ink and ink[0] >= PAD_LEFT
    assert ink[-1] < PAD_LEFT + 24 * b.view.r.cw        # 23 characters wide
    assert ink[-1] > PAD_LEFT + 20 * b.view.r.cw
    # The cursor of the shell's line is not drawn: it is in the box.
    assert b.session.input.cy == 0
    b.type("x")                                         # the text changes:
    assert b.view._input_span() == (0, 0)               # the list has gone
    assert b.view.frame().input_h == 0


def test_text_in_what_the_shell_lists_can_be_selected(box):
    b = box()
    b.type("ls al")
    b.key(Key.Key_Tab)
    b.answer("ls alpha", shown=b"ls alpha" + listing(10))
    frame = b.view.frame()
    y = frame.view_h + 6 + b.view.r.ch // 2
    cw = b.view.r.cw
    QTest.mousePress(b.view, LEFT, NONE, QPoint(PAD_LEFT + 2, y))
    QTest.mouseMove(b.view, QPoint(PAD_LEFT + 6 * cw, y))
    QTest.mouseMove(b.view, QPoint(PAD_LEFT + 12 * cw - 2, y))
    QTest.mouseRelease(b.view, LEFT, NONE, QPoint(PAD_LEFT + 12 * cw - 2, y))
    b.pump()
    assert b.view.selection_text() == "alphabet.txt"


def test_a_long_command_pushes_what_is_listed_further_down(box):
    b = box()
    text = "echo " + "a" * 50 + "\nls al"
    b.cell.set_text(text)
    cursor = b.editor.textCursor()
    cursor.setPosition(3)                               # in the first line
    b.editor.setTextCursor(cursor)
    b.key(Key.Key_Tab)
    # The prompt and 55 characters fill a row of 40 cells and a half; the
    # second line is a row of its own; the list comes under that.
    drawn = (text.replace("\n", "\r\n").encode()
             + b"\r\nalphabet.txt  alpha.txt\x1b[3A\r\x1b[5C")
    b.answer(text, 3, shown=drawn)
    assert (b.session.input.cy, b.session.input.cx) == (0, 5)
    assert b.view._input_span() == (3, 1)


def test_a_menu_of_the_shell_has_the_keyboard_until_it_is_done(
        box, monkeypatch):
    b = box()
    b.type("ls al")
    b.key(Key.Key_Tab)
    b.type("x")                                         # before it showed
    b.session.feed(b"ls alpha" + listing(10))           # a menu, no answer
    b.pump()
    assert b.editor.isEnabled() and not b.session.shell_asks    # not yet
    monkeypatch.setattr(session_module, "EDIT_SHOWS_AFTER", -1.0)
    b.view.sync_input()
    assert b.session.shell_asks and b.session.editing
    assert not b.editor.isEnabled() and b.view.hasFocus()
    assert b.sent == [EDIT_KEY, b"x"]                   # what waited is its
    assert b.view._input_span() == (0, 2)               # all of its lines
    b.key(Key.Key_Down, widget=b.view)                  # and so are the keys
    assert b.sent[-1] == b"\x1b[B" and b.cell.text() == "ls al"
    b.answer("ls alpha.txt ")
    assert not b.session.shell_asks
    assert b.editor.isEnabled() and b.editor.hasFocus()
    assert b.cell.text() == "ls alpha.txt "
    assert b.view._input_span() == (1, 1)               # the list is left


def test_a_shell_that_runs_what_it_was_asked_to_work_on_empties_the_box(box):
    b = box()
    b.type("ls")
    b.key(Key.Key_Tab)
    b.type("x")
    b.session.feed(submit("ls") + b"a.txt\r\n")         # it was accepted
    b.pump()
    assert not b.session.editing and not b.editor.isEnabled()
    assert b.cell.text() == "x"                         # typed since
    b.session.feed(prompt())
    b.pump()
    assert b.editor.isEnabled() and b.cell.text() == "x"


def test_keys_that_waited_are_typed_when_the_shell_gives_no_answer(box):
    b = box()
    b.type("ls")
    b.key(Key.Key_Tab)
    b.type("x")
    b.session.feed(b"^C" + prompt(130))                 # it was broken off
    b.pump()
    assert not b.session.editing
    assert b.editor.isEnabled() and b.cell.text() == "lsx"


# -- what the plugins of the shell make of the text ---------------------------

class Paints(QObject):
    """Notes where a widget is told to paint itself."""

    def __init__(self, widget) -> None:
        super().__init__()
        self.rects: list = []
        widget.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Paint:
            self.rects.append(event.rect())
        return False


@pytest.fixture
def still(qapp):
    """A cursor that does not blink: nothing is painted unless something
    has changed, and two pictures differ in nothing but that."""
    flash = qapp.cursorFlashTime()
    qapp.setCursorFlashTime(0)
    yield
    qapp.setCursorFlashTime(flash)


def changed(image, other) -> set[tuple[int, int]]:
    """The pixels in which two pictures of the same widget differ."""
    assert image.size() == other.size()
    return {(x, y) for y in range(image.height())
            for x in range(image.width())
            if image.pixel(x, y) != other.pixel(x, y)}


def lightness(image, pixels) -> int:
    return max(image.pixelColor(x, y).lightness() for x, y in pixels)


def test_the_plugins_of_the_shell_are_asked_about_what_is_typed(box):
    b = box(plugins=True)
    assert b.editor.ghost == "" and b.formats() == [[]]
    b.type("g")
    assert b.runtime.looks == [("g", 1, 1)] and b.sent == [LOOK_KEY]
    b.type("it")                                        # while that is out
    assert len(b.runtime.looks) == 1
    b.session.feed(mark("V", "1", escaped("gh pr list"), "0 1 fg=red"))
    assert b.editor.ghost == "" and b.formats() == [[]]     # no longer true
    assert b.runtime.looks[1:] == [("git", 3, 2)]       # the text as it is
    b.session.feed(mark("V", "2", escaped("git status"), "0 3 fg=green"))
    assert b.editor.ghost == " status"
    [[(start, length, made)]] = b.formats()
    assert (start, length) == (0, 3)
    assert made.foreground().color() == b.view.theme.palette[2]
    # None of it is text, or a change of the text: nothing was asked
    # again, and the one step to take back is the typing.
    assert b.cell.text() == "git" and b.sent == [LOOK_KEY] * 2
    assert not b.editor.document().isEmpty()
    b.key(Key.Key_Z, CTRL)
    assert b.cell.text() == ""
    assert b.editor.ghost == "" and b.formats() == [[]]     # told at once


def test_the_shell_counts_what_it_looks_at_in_characters(box):
    b = box(plugins=True)
    b.cell.insert("echo \U0001f600 ls")                 # two units to Qt
    assert b.runtime.looks == [("echo \U0001f600 ls", 9, 1)]
    b.hint("", "0 4 fg=green\n5 6 bold\n7 9 underline")
    [line] = b.formats()
    assert [(start, length) for start, length, _ in line] == \
        [(0, 4), (5, 2), (8, 2)]


def test_a_shell_without_such_plugins_is_not_asked(box):
    b = box()
    b.type("git st")
    b.key(Key.Key_Backspace)
    assert b.runtime.looks == [] and b.sent == []
    assert b.editor.ghost == "" and b.formats() == [[]]


def test_nothing_is_suggested_before_the_shell_has_suggested_something(box):
    b = box(plugins=True)
    for letter in "Xx-":                                # whatever is typed
        b.type(letter)
        assert b.editor.ghost == "", letter
        b.key(Key.Key_Backspace)
    assert b.cell._suggestion == ""


def test_what_is_suggested_shows_behind_the_cursor_at_the_end(box):
    b = box(plugins=True)
    b.type("git")
    b.hint("git status --short")
    assert b.editor.ghost == b.editor.ghost_shown() == " status --short"
    # Typed along, it stays: no waiting for the shell to say it again.
    b.type(" st")
    assert b.editor.ghost == "atus --short"
    b.hint("git stash")                                 # another one now
    assert b.editor.ghost == "ash"
    b.type("x")                                         # typed away from it
    assert b.editor.ghost == ""
    b.hint("git stx --all")
    assert b.editor.ghost == " --all"
    # Only where the cursor ends the text, and with nothing selected.
    b.key(Key.Key_Left)
    assert b.editor.ghost == " --all" and b.editor.ghost_shown() == ""
    b.key(Key.Key_End)                                  # to the end, no more
    assert b.cell.text() == "git stx" and b.editor.ghost_shown() == " --all"
    b.key(Key.Key_Home, SHIFT)                          # selected backwards
    assert b.editor.ghost_shown() == ""
    b.key(Key.Key_Home)
    b.key(Key.Key_End, SHIFT)                           # and forwards
    assert b.editor.textCursor().atEnd() and b.editor.ghost_shown() == ""
    b.key(Key.Key_Right)                                # lets go of it only
    assert b.cell.text() == "git stx" and b.editor.ghost_shown() == " --all"


def test_a_suggestion_for_another_text_is_not_shown(box):
    b = box(plugins=True)
    b.type("git")
    b.hint("git status")
    b.key(Key.Key_A, CTRL)
    b.type("ls")                                        # before any answer
    assert b.editor.ghost == ""
    # A box that was emptied shows none either, whatever was suggested.
    b.hint("ls -la")
    b.session.feed(mark("V", "0", "nobody", "asked"))
    b.type(" ")                                         # a look is out now
    assert b.editor.ghost == "-la"
    b.key(Key.Key_A, CTRL)
    b.key(Key.Key_Delete)
    assert b.cell.text() == "" and b.editor.ghost == ""
    b.hint("ls -la")                                    # about what it was
    assert b.editor.ghost == "" and b.cell._suggestion == ""


def test_an_answer_about_a_key_says_what_the_plugins_make_of_it(box):
    b = box(plugins=True)
    b.type("ls be")
    b.hint()
    b.key(Key.Key_Tab)
    looks = len(b.runtime.looks)
    b.session.feed(answer("ls beta.txt ", go="ls beta.txt alpha.txt",
                          colors="0 2 fg=green"))
    b.pump()
    assert b.cell.text() == "ls beta.txt "
    assert b.editor.ghost == "alpha.txt"
    assert [(start, length) for start, length, _ in b.formats()[0]] == \
        [(0, 2)]
    assert len(b.runtime.looks) == looks                # no need to ask
    # A key that waited for the answer makes another text of it: what the
    # answer says about its own is not for that one.
    b.key(Key.Key_Tab)
    b.type("x")
    b.session.feed(answer("ls be", go="ls beta.txt", colors="0 5 fg=red"))
    b.pump()
    assert b.cell.text() == "ls bex" and b.editor.ghost == ""
    assert [(start, length) for start, length, _ in b.formats()[0]] == \
        [(0, 2)]                                        # as they were
    assert b.runtime.looks[-1][0] == "ls bex"           # this is asked about


def test_right_and_end_take_what_is_suggested(box):
    b = box(plugins=True)

    def dealt(key, mods=NONE):
        return b.cell.eventFilter(b.editor, _key_event(key, mods))

    b.type("git")
    assert dealt(Key.Key_Right) is False                # nothing to take
    assert dealt(Key.Key_End) is False
    b.hint("git status --short")
    for key, mods in ((Key.Key_Right, SHIFT), (Key.Key_End, SHIFT),
                      (Key.Key_End, CTRL), (Key.Key_Right, ALT),
                      (Key.Key_A, NONE), (Key.Key_Left, NONE)):
        assert dealt(key, mods) is False, (key, mods)   # the box's own keys
    assert b.cell.text() == "git"
    b.key(Key.Key_Right)
    assert b.cell.text() == "git status --short" and b.editor.ghost == ""
    assert b.editor.textCursor().atEnd()
    assert b.runtime.looks[-1] == ("git status --short", 18, 3)
    b.key(Key.Key_Z, CTRL)                              # one step to take back
    assert b.cell.text() == "git"
    b.hint("git status --short")
    assert dealt(Key.Key_End) is True
    assert b.cell.text() == "git status --short"
    b.key(Key.Key_Z, CTRL)
    b.hint("git status --short")
    assert dealt(Key.Key_Right) is True                 # and the key is used
    assert b.cell.text() == "git status --short"
    # Several lines of it are taken as they are.
    b.cell.set_text("for")
    b.hint("for i in 1 2\ndo echo $i\ndone")
    b.key(Key.Key_End)
    assert b.cell.text() == "for i in 1 2\ndo echo $i\ndone"


def test_ctrl_right_takes_a_word_of_what_is_suggested(box):
    b = box(plugins=True)

    def dealt(key, mods=NONE):
        return b.cell.eventFilter(b.editor, _key_event(key, mods))

    b.type("git")
    b.hint("git status --short  -b")
    assert dealt(Key.Key_Right, CTRL) is True
    assert b.cell.text() == "git status "               # with the blanks
    assert b.editor.ghost == "--short  -b"              # the rest is left
    b.key(Key.Key_Right, CTRL)
    assert b.cell.text() == "git status --short  "
    b.key(Key.Key_Right, CTRL)
    assert b.cell.text() == "git status --short  -b"
    assert b.editor.ghost == ""
    b.key(Key.Key_Z, CTRL)                              # word by word back
    assert b.cell.text() == "git status --short  "
    # Other keys with Ctrl are what they are.
    b.hint("git status --short  -b")
    assert b.editor.ghost_shown() == "-b"
    assert dealt(Key.Key_A, CTRL) is False
    assert dealt(Key.Key_Right, CTRL | SHIFT) is False
    assert b.cell.text() == "git status --short  "
    # With nothing suggested the key moves by a word, as in any text box.
    b.cell.set_text("git status")
    b.hint()
    b.key(Key.Key_Home)
    assert dealt(Key.Key_Right, CTRL) is False
    b.key(Key.Key_Right, CTRL)
    assert b.editor.textCursor().position() == 4
    assert b.cell.text() == "git status"


def test_the_colors_of_the_shell_lie_on_the_lines_of_the_text(box):
    b = box(plugins=True)
    theme = b.view.theme
    b.cell.set_text("echo 'a\nb' x\n\nls")
    b.hint("", "0 4 fg=green\n5 10 fg=yellow,underline\n9 9 bold\n"
               "11 12 none\n0 8 bg=4\n14 16 fg=red,bold")
    first, second, third, fourth = b.formats()
    # What reaches over the end of a line goes on in the next one; what
    # ends with a line does not; and what changes nothing is left out.
    assert [(start, length) for start, length, _ in first] == \
        [(0, 4), (5, 2), (0, 7)]
    assert [(start, length) for start, length, _ in second] == [(0, 2)]
    assert third == []
    assert [(start, length) for start, length, _ in fourth] == [(0, 2)]
    assert first[0][2].foreground().color() == theme.palette[2]
    assert first[1][2].foreground().color() == theme.palette[3]
    assert first[1][2].fontUnderline() is True
    assert first[2][2].background().color() == theme.palette[4]
    assert second[0][2] == first[1][2]
    assert fourth[0][2].foreground().color() == theme.palette[9]
    assert fourth[0][2].fontWeight() == QFont.Weight.Bold
    # The next answer takes the place of this one.
    b.type("x")
    b.hint("", "0 2 fg=red")
    assert [[(start, length) for start, length, _ in line]
            for line in b.formats()] == [[(0, 2)], [], [], []]
    b.type("y")
    b.hint()
    assert b.formats() == [[], [], [], []]


@pytest.mark.parametrize("style, fg, bg, bold, underline", [
    ("none", None, None, False, False),
    ("", None, None, False, False),
    ("fg=green", 2, None, False, False),
    ("fg=black,bold", 8, None, True, False),            # bright in bold
    ("bold, fg=7", 15, None, True, False),
    ("fg=8,bold", 8, None, True, False),                # no brighter one
    ("fg=255", 255, None, False, False),
    ("fg=256", None, None, False, False),               # no such color
    ("fg=#ff8000", "#ff8000", None, False, False),
    ("fg=#nonsense", None, None, False, False),
    ("fg=orange", None, None, False, False),            # no name of zsh's
    ("fg=default", None, None, False, False),
    ("fg=", None, None, False, False),
    ("fg=b", 0, None, False, False),                    # short for black
    ("fg=blu", 4, None, False, False),
    ("fg=whiter", None, None, False, False),
    ("bg=red", None, 1, False, False),
    ("bg=red,bold", None, 1, True, False),              # no brighter ground
    ("fg=cyan,bg=#102030,underline", 6, "#102030", False, True),
    ("xx=3,fg", None, None, False, False),
    ("underline", None, None, False, True),
])
def test_a_style_of_zsh_becomes_a_format(box, style, fg, bg, bold, underline):
    theme = box().view.theme

    def color(value):
        return theme.palette[value] if isinstance(value, int) \
            else QColor(value)

    made = char_format(style, theme)
    assert made.isEmpty() is (
        fg is None and bg is None and not bold and not underline)
    assert made.hasProperty(made.Property.ForegroundBrush) is (fg is not None)
    assert made.hasProperty(made.Property.BackgroundBrush) is (bg is not None)
    assert fg is None or made.foreground().color() == color(fg)
    assert bg is None or made.background().color() == color(bg)
    assert made.hasProperty(made.Property.FontWeight) is bold
    assert not bold or made.fontWeight() == QFont.Weight.Bold
    assert made.hasProperty(made.Property.TextUnderlineStyle) is underline
    assert made.fontUnderline() is underline


def test_the_names_zsh_has_for_colors_are_the_first_eight_of_the_terminal(
        box):
    theme = box().view.theme
    names = ("black", "red", "green", "yellow", "blue", "magenta", "cyan",
             "white")
    for number, name in enumerate(names):
        made = char_format(f"fg={name}", theme)
        assert made.foreground().color() == theme.palette[number], name
        made = char_format(f"bg={name},bold", theme)
        assert made.background().color() == theme.palette[number], name
        made = char_format(f"fg={name},bold", theme)
        assert made.foreground().color() == theme.palette[number + 8], name
    assert len({color.name() for color in theme.palette[:16]}) == 16


def test_standout_is_the_text_in_the_colors_the_other_way_round(box):
    theme = box().view.theme
    made = char_format("fg=red,standout", theme)
    assert made.background().color() == theme.foreground
    assert made.foreground().color() == theme.input_background
    assert theme.foreground != theme.input_background
    assert not char_format("standouts", theme).hasProperty(
        made.Property.BackgroundBrush)


def test_a_box_of_its_own_suggests_nothing(qapp):
    editor = Editor()
    assert editor.ghost == "" and editor.ghost_shown() == ""
    assert editor.ghost_color == QColor() and not editor.ghost_color.isValid()


def test_the_suggestion_is_drawn_dimmed_from_the_cursor_on(box, still):
    b = box(plugins=True)
    theme = b.view.theme
    assert b.editor.ghost_color == theme.dim
    port = b.editor.viewport()
    b.type("aa")
    plain = port.grab().toImage()
    b.hint("aaaa")                                      # the same letters
    assert b.editor.ghost == "aa"
    drawn = port.grab().toImage()
    place = b.editor.cursorRect()
    cw = b.view.r.cw
    ghost = changed(drawn, plain)
    across = {x for x, _ in ghost}
    assert min(across) >= place.left() and max(across) < place.left() + 2 * cw
    assert min(across) < place.left() + cw <= max(across)   # both of them
    # On the line of the text, as high as the letters that were typed.
    ground = plain.pixel(0, 0)
    typed = {(x, y) for y in range(plain.height())
             for x in range(place.left() - 2 * cw, place.left())
             if plain.pixel(x, y) != ground}
    assert typed and {y for _, y in ghost} == {y for _, y in typed}
    # Dimmer than what was typed, and lighter than what it is written on.
    # (Not counting where the first letter runs into the cursor: the two
    # mix there.)
    between = (theme.foreground.lightness() + theme.dim.lightness()) // 2
    clear = {(x, y) for x, y in ghost if x > place.right()}
    assert lightness(plain, typed) > between
    assert theme.input_background.lightness() + 40 \
        < lightness(drawn, clear) < between
    # Nothing of it where the cursor is elsewhere.
    b.key(Key.Key_Home)
    b.key(Key.Key_End, SHIFT)
    b.key(Key.Key_Left)
    there = port.grab().toImage()
    b.editor.set_ghost("")
    assert changed(port.grab().toImage(), there) == set()


def test_of_several_lines_the_first_is_drawn_and_a_sign_for_the_rest(
        box, still):
    b = box(plugins=True)
    port = b.editor.viewport()
    b.type("a")
    plain = port.grab().toImage()
    place = b.editor.cursorRect()
    cw = b.view.r.cw

    def drawn(ghost: str) -> set:
        b.editor.set_ghost(ghost)
        return changed(port.grab().toImage(), plain)

    one = {x for x, _ in drawn("bb")}
    more = drawn("bb\ncc")
    assert max(one) < place.left() + 2 * cw <= max(x for x, _ in more)
    assert {y for _, y in more} <= set(range(place.top(), place.bottom() + 1))
    # Of one that begins with a new line, the sign alone.
    sign = drawn("\ncc")
    assert sign and min(x for x, _ in sign) >= place.left()
    assert {y for _, y in sign} <= {y for _, y in more}
    assert drawn("") == set()


def test_a_suggestion_too_long_for_the_line_is_cut_short(box, still):
    b = box(plugins=True)
    port = b.editor.viewport()
    margin = round(b.editor.document().documentMargin())
    cw = b.view.r.cw
    assert margin > 0
    b.type("ab")
    width, height = b.window.width(), b.window.height()
    # At every width of the window: some leave room for one letter more
    # than others.
    for extra in range(cw):
        b.window.resize(width + extra, height)
        b.pump()
        b.editor.set_ghost("")
        plain = port.grab().toImage()
        b.editor.set_ghost("M" * 300)
        across = {x for x, _ in changed(port.grab().toImage(), plain)}
        edge = port.width() - margin
        assert edge - 2 * cw <= max(across) < edge, extra


def test_the_box_is_drawn_again_when_what_it_shows_has_changed(box, still):
    b = box(plugins=True)
    b.type("git")
    b.hint()
    port = b.editor.viewport()
    b.pump()
    paints = Paints(port)
    b.pump()
    # (Whatever else happens to the box is not what this notes, and stays
    # the box's to deal with.)
    assert paints.eventFilter(port, QEvent(QEvent.Type.Leave)) is False
    assert paints.rects == []                           # nothing has changed
    b.editor.set_ghost(" status")
    b.pump()
    assert paints.rects == [port.rect()]
    b.editor.set_ghost(" status")                       # the same again
    b.pump()
    assert paints.rects == [port.rect()]
    # Where the cursor goes, the suggestion comes or goes: all of the box
    # is drawn again, not the two places of the cursor alone.
    b.key(Key.Key_Home)
    assert paints.rects[1:] == [port.rect()]
    # Colors that have come, or gone, are drawn.
    b.editor.set_ghost("")
    b.pump()
    del paints.rects[:]
    b.cell._hinted("git", "", [(0, 3, "fg=green")])
    b.pump()
    assert paints.rects == [port.rect()]
    b.cell._hinted("git", "", [])
    b.pump()
    assert paints.rects == [port.rect()] * 2


# -- keys pressed in the view -------------------------------------------------

def test_typing_in_the_view_goes_to_the_text_box(box):
    b = box()
    block = b.run("ls", "a\r\n")
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    assert b.view.selected == [block.id] and b.view.hasFocus()
    QTest.keyClicks(b.view, "ls")
    b.pump()
    assert b.cell.text() == "ls" and b.editor.hasFocus()
    assert b.view.selected == [] and b.sent == []
    # A key that types nothing goes there too, and lets go just the same.
    b.view.setFocus()
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 1))
    b.key(Key.Key_Left, widget=b.view)
    assert b.view._sel is None and b.editor.hasFocus()
    assert b.editor.textCursor().position() == 1 and b.cell.text() == "ls"


def test_composed_text_that_arrives_at_the_view_goes_to_the_box(box):
    b = box()
    block = b.run("ls", "a\r\n")
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    assert b.view.selected == [block.id] and b.view.hasFocus()
    event = QInputMethodEvent()
    event.setCommitString("日本")
    event.ignore()
    b.view.inputMethodEvent(event)
    assert b.cell.text() == "日本" and b.editor.hasFocus()
    assert b.view.selected == [] and b.sent == []
    assert event.isAccepted()
    b.key(Key.Key_Return, CTRL)                         # while it runs,
    b.session.feed(submit("日本"))                      # the program gets it
    b.pump()
    del b.sent[:]
    b.view.inputMethodEvent(event)
    assert b.sent == ["日本".encode()] and b.cell.text() == ""


def test_while_a_command_runs_the_keys_are_its_own(box):
    b = box()
    b.type("python")
    b.key(Key.Key_Return, CTRL)
    b.session.feed(submit("python") + b">>> ")
    b.pump()
    del b.sent[:]
    assert not b.editor.isEnabled() and b.view.hasFocus()
    QTest.keyClicks(b.view, "1+1")
    b.key(Key.Key_Return, widget=b.view)
    assert b"".join(b.sent) == b"1+1\r" and b.cell.text() == ""
    b.key(Key.Key_C, CTRL, widget=b.view)               # an interrupt
    assert b.sent[-1] == b"\x03"


def test_ctrl_c_in_the_view_copies_a_selection_before_it_interrupts(box):
    b = box()
    b.session.feed(submit("ping host") + b"reply 1\r\nreply 2\r\n")
    b.pump()
    block = b.session.blocks[-1]
    del b.sent[:]
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 7))
    b.key(Key.Key_C, CTRL, widget=b.view)
    assert b.clipboard() == "reply 1" and b.sent == []
    assert b.view._sel is None
    b.key(Key.Key_C, CTRL, widget=b.view)               # nothing selected now
    assert b.sent == [b"\x03"]


def test_a_selected_block_is_copied_by_ctrl_c_as_well(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    b.key(Key.Key_C, CTRL, widget=b.view)
    assert b.clipboard() == "a.txt" and b.view.selected == []
    assert b.view.copy_selection() is False             # nothing left to copy


def test_a_full_screen_program_gets_every_ctrl_c(box):
    b = box()
    b.session.feed(submit("vim") + b"\x1b[?1049h\x1b[Htext")
    b.pump()
    del b.sent[:]
    b.view._gsel = (b.session.term.screen, (0, 0), (0, 4))
    assert b.view.has_text_selection()
    b.key(Key.Key_C, CTRL, widget=b.view)
    assert b.sent == [b"\x03"] and b.view._gsel is not None


# -- one selection, and letting go of it --------------------------------------

def test_a_second_click_on_a_block_lets_go_of_it(box):
    b = box()
    block = b.run("ls", "a.txt\r\nb.txt\r\n")
    QTest.mouseClick(b.view, LEFT, NONE, b.line(block, 1))
    b.pump()
    assert b.view.selected == [block.id]
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    assert b.view.selected == [] and not b.view.has_text_selection()


def test_escape_in_the_box_lets_go_of_what_is_selected_in_the_output(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    b.type("half")
    b.view.select_block(block)
    assert b.editor.hasFocus()
    b.key(Key.Key_Escape)
    assert b.view.selected == [] and b.editor.hasFocus()
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 5))
    b.key(Key.Key_Escape)
    assert not b.view.has_text_selection() and b.cell.text() == "half"
    # With nothing selected up there the key is nobody's.
    assert b.cell.eventFilter(
        b.editor, _key_event(Key.Key_Escape, NONE)) is False


def test_typing_selecting_or_clicking_in_the_box_lets_go_of_the_output(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    chosen = ((block.id, 1, 0, 0), (block.id, 1, 0, 5))
    b.view._sel = chosen
    b.type("x")                                         # typing
    assert b.view._sel is None
    b.view._sel = chosen
    b.key(Key.Key_Left)                                 # not the cursor alone
    assert b.view._sel == chosen
    b.key(Key.Key_Right, SHIFT)                         # selecting here
    assert b.view._sel is None
    assert b.editor.textCursor().selectedText() == "x"
    b.view.select_block(block)
    b.key(Key.Key_End)
    assert b.view.selected == [block.id]
    spot = b.editor.cursorRect(b.editor.textCursor()).center()
    QTest.mouseClick(b.editor.viewport(), LEFT, NONE, spot)
    assert b.view.selected == []                        # a click elsewhere


def test_selecting_in_the_output_lets_go_of_what_is_selected_in_the_box(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    b.type("echo hi")
    b.key(Key.Key_Left, SHIFT | CTRL)
    assert b.editor.textCursor().selectedText() == "hi"
    beside = QPoint(PAD_LEFT + 20, 3)
    assert b.view._block_under(beside.y())[1] is None
    QTest.mouseClick(b.view, LEFT, NONE, beside)        # a click on nothing
    b.pump()
    cursor = b.editor.textCursor()
    assert not cursor.hasSelection() and cursor.position() == 5
    assert b.view.selected == []
    b.editor.setFocus()
    b.key(Key.Key_Left, SHIFT | CTRL)
    b.key(Key.Key_Left, SHIFT | CTRL)
    assert b.editor.textCursor().selectedText() == "echo "
    QTest.mouseClick(b.view, LEFT, NONE, b.line(block, 0))      # on a block
    b.pump()
    cursor = b.editor.textCursor()
    assert not cursor.hasSelection() and cursor.position() == 0
    assert b.view.selected == [block.id]
    b.key(Key.Key_Escape, widget=b.view)                # back to the box
    assert b.view.selected == [] and b.editor.hasFocus()
    b.key(Key.Key_Right, SHIFT)
    assert b.editor.textCursor().selectedText() == "e"
    b.key(Key.Key_Up, CTRL)                             # a block, by key
    assert b.view.selected == [block.id]
    assert not b.editor.textCursor().hasSelection()
    assert b.cell.text() == "echo hi"                   # nothing was replaced


def test_a_click_in_the_output_leaves_a_box_with_nothing_selected_alone(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    for _ in range(MAX_LINES + 4):
        b.key(Key.Key_Return)
    bar = b.editor.verticalScrollBar()
    bar.setValue(0)                                     # reading its top
    b.pump()
    assert bar.value() == 0 and bar.maximum() > 0
    QTest.mouseClick(b.view, LEFT, NONE, b.line(block, 0))
    b.pump()
    assert bar.value() == 0                             # and it stays there


# -- pasting ------------------------------------------------------------------

def test_pasting_goes_into_the_box_and_runs_nothing(box, monkeypatch):
    from jupyterminal.ui import view as view_module
    asked = []
    monkeypatch.setattr(view_module, "confirm_paste",
                        lambda *args: asked.append(args))
    b = box()
    QGuiApplication.clipboard().setText("echo 1\r\necho 2\recho 3\n")
    b.type("# ")
    b.key(Key.Key_V, CTRL | SHIFT)                      # from the box
    assert b.cell.text() == "# echo 1\necho 2\necho 3\n"
    b.view.setFocus()
    b.key(Key.Key_V, CTRL | SHIFT, widget=b.view)       # and from the view
    assert b.cell.text() == \
        "# echo 1\necho 2\necho 3\necho 1\necho 2\necho 3\n"
    assert b.editor.hasFocus()
    assert asked == [] and b.sent == []                 # nobody was asked


def test_pasting_with_a_block_selected_gives_the_keyboard_to_the_box(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    assert b.view.selected == [block.id] and b.view.hasFocus()
    QGuiApplication.clipboard().setText("")
    b.key(Key.Key_V, CTRL | SHIFT, widget=b.view)       # nothing to paste
    assert b.view.selected == [block.id] and b.view.hasFocus()
    QGuiApplication.clipboard().setText("echo 1")
    b.key(Key.Key_V, CTRL | SHIFT, widget=b.view)
    assert b.cell.text() == "echo 1" and b.editor.hasFocus()
    assert b.view.selected == []


def test_pasting_into_a_running_program_is_as_it_was(box):
    b = box()
    b.session.feed(submit("cat"))
    b.pump()
    del b.sent[:]
    QGuiApplication.clipboard().setText("one line")
    b.key(Key.Key_V, CTRL | SHIFT, widget=b.view)
    assert b.sent == [b"one line"] and b.cell.text() == ""


# -- the shortcuts of the view, from the box ----------------------------------

def test_the_copy_shortcut_takes_what_is_selected_in_the_box(box):
    b = box()
    block = b.run("ls", "a.txt\r\n")
    b.type("echo hi")
    b.key(Key.Key_Left, SHIFT | CTRL)
    b.key(Key.Key_C, CTRL | SHIFT)
    assert b.clipboard() == "hi"
    b.key(Key.Key_End)                                  # nothing selected here
    b.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 1))
    b.key(Key.Key_C, CTRL | SHIFT)
    assert b.clipboard() == "a" and b.editor.hasFocus()


def test_ctrl_up_goes_to_the_blocks_and_escape_comes_back(box):
    b = box()
    block = b.run("ls", "a\r\n")
    b.type("half")
    b.key(Key.Key_Up, CTRL)
    assert b.view.selected == [block.id] and b.view.hasFocus()
    b.key(Key.Key_Escape, widget=b.view)
    assert b.view.selected == [] and b.editor.hasFocus()
    assert b.cell.text() == "half"


def test_shift_home_selects_text_and_does_not_scroll(box):
    b = box()
    for i in range(6):
        b.run(f"cmd {i}", "x\r\n" * 4)
    assert b.view.frame().max_scroll > 0
    b.view._set_scroll(b.view.frame().max_scroll)
    at = b.view.scroll
    b.type("abc")
    b.key(Key.Key_Home, SHIFT)
    assert b.editor.textCursor().selectedText() == "abc"
    assert b.view.scroll == at
    b.key(Key.Key_PageUp, SHIFT)                        # this one does scroll
    assert b.view.scroll < at


def test_zooming_from_the_box(box):
    b = box()
    size = b.view.r.size
    b.key(Key.Key_Plus, CTRL)
    assert b.view.r.size == size + 1
    assert b.editor.font().pointSizeF() == size + 1
    b.key(Key.Key_0, CTRL)
    assert b.view.r.size == size


def test_a_key_that_is_nobodys_shortcut_is_the_text_boxs(box):
    b = box()
    event_type = QEvent.Type.KeyRelease
    assert b.cell.eventFilter(b.editor, QEvent(event_type)) is False
    assert b.view.cell_action(
        _key_event(Key.Key_Home, SHIFT)) is None       # the view's, not here
    assert b.view.cell_action(_key_event(Key.Key_Q, CTRL)) is None


def _key_event(key, mods):
    from PySide6.QtGui import QKeyEvent
    return QKeyEvent(QEvent.Type.KeyPress, key, mods)


def test_the_box_says_which_keys_it_has_dealt_with(box):
    b = box()

    def dealt(key, mods=NONE):
        return b.cell.eventFilter(b.editor, _key_event(key, mods))

    assert dealt(Key.Key_A) is False                    # the text box's own
    assert dealt(Key.Key_L, CTRL) is True               # the shell's
    assert dealt(Key.Key_D, CTRL) is True
    assert dealt(Key.Key_C, CTRL) is True               # nothing is selected
    assert dealt(Key.Key_Plus, CTRL) is True            # the view's
    assert b.sent == [b"\x0c", b"\x04"]
    for key in (Key.Key_Up, Key.Key_Down, Key.Key_Tab, Key.Key_Backtab):
        assert dealt(key) is True, key                  # the line editor's
        b.answer("")
    assert dealt(Key.Key_Tab) is True
    assert dealt(Key.Key_A) is True                     # it waits its turn
    # A click is always the text box's own.
    click = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(3, 3),
                        QPointF(3, 3), LEFT, LEFT, NONE)
    assert b.cell.eventFilter(b.editor.viewport(), click) is False


# -- running a block again ----------------------------------------------------

def test_running_a_block_again_puts_its_command_in_the_box(box):
    b = box()
    block = b.run("make test", "ok\r\n")
    b.type("something else")
    b.view.setFocus()
    b.view.rerun(block)
    assert b.cell.text() == "make test" and b.editor.hasFocus()
    assert b.editor.textCursor().atEnd()
    assert b.sent == [] and b.runtime.buffers == []


def test_a_block_run_again_while_a_command_runs_waits_in_the_box(box):
    b = box()
    block = b.run("make test", "ok\r\n")
    b.session.feed(submit("sleep 9"))
    b.pump()
    b.view.rerun(block)
    assert b.cell.text() == "make test"
    assert b.view.hasFocus() and not b.editor.isEnabled()


# -- who has the keyboard when the shell comes back ---------------------------

def test_a_selected_block_keeps_the_keyboard_when_the_shell_comes_back(box):
    b = box()
    b.session.feed(submit("sleep 1"))
    b.pump()
    block = b.session.blocks[-1]
    QTest.mouseClick(b.view, LEFT, NONE, b.header(block))
    b.pump()
    assert b.view.selected == [block.id]
    b.session.feed(prompt())
    b.pump()
    assert b.editor.isEnabled() and b.view.hasFocus()


def test_the_search_field_keeps_the_keyboard_when_the_shell_comes_back(box):
    b = box()
    b.session.feed(submit("make") + b"error: one\r\n")
    b.pump()
    b.view.open_find(b.session.blocks[-1])
    b.pump()
    field = b.view.search.edit
    assert field.hasFocus()
    b.session.feed(prompt())
    b.pump()
    assert b.editor.isEnabled() and field.hasFocus()


def test_the_search_field_keeps_the_keyboard_when_a_command_starts(box):
    b = box()
    block = b.run("make", "error: one\r\n")
    b.view.open_find(block)
    b.pump()
    field = b.view.search.edit
    assert field.hasFocus() and b.editor.isEnabled()
    b.cell.insert("half")
    b.session.feed(submit("sleep 9"))                   # not typed in the box
    b.pump()
    assert not b.editor.isEnabled() and field.hasFocus()
    assert b.cell.text() == "half"                      # which keeps its text
    assert b.view._input_span() == (0, 0)
    b.view.focus_input()                                # to what is running
    assert b.view.hasFocus()


def test_the_box_takes_the_keyboard_of_a_window_nobody_typed_in_yet(qapp):
    session = Session(COLS, 12, nonce=NONCE)
    window = MainWindow(session, Config())              # not shown: no focus
    session.feed(prompt())
    assert window.cell.editor.isEnabled()
    assert window.focusWidget() is window.cell.editor
    window.close()


def test_a_view_lays_itself_out_again_when_it_gets_a_text_box(qapp):
    session = Session(COLS, 12, nonce=NONCE)
    view = TerminalView(session, Config())
    session.feed(prompt())
    assert view.frame().input_rows == 1                 # the shell's own line
    asked = repaints(view)
    laid_out = view._ui
    cell = InputCell(view)
    assert view.cell is cell
    assert view._ui == laid_out + 1 and len(asked) == 1
    assert view.frame().input_rows == 0


# -- when the shell asks before it runs something -----------------------------

def test_a_question_of_the_shell_is_shown_and_answered_in_the_view(
        box, monkeypatch):
    b = box()
    b.type("sl")
    b.key(Key.Key_Return, CTRL)
    b.session.feed(b"sl" + mark("F", "sl")
                   + b"\r\nzsh: correct 'sl' to 'ls' [nyae]? ")
    b.pump()
    assert not b.session.shell_asks                     # it has only just gone
    assert b.view._input_rows() == 0
    monkeypatch.setattr(session_module, "ASK_AFTER", -1.0)
    assert b.session.shell_asks
    asked = repaints(b.view)
    laid_out = b.view._ui
    told: list[bool] = []
    b.view.inputReady.connect(told.append)
    b.view.sync_input()
    assert b.view._ui == laid_out + 1 and len(asked) == 1
    assert b.view._input_rows() == 2                   # its line, question too
    assert b.view.frame().input_h > 0
    b.view.sync_input()                                # nothing new: no change
    assert b.view._ui == laid_out + 1 and len(asked) == 1
    assert told == []
    # What it asks can be selected and copied like any output.
    b.view._gsel = (b.session.input, (1, 0), (1, 3))
    assert b.view.has_text_selection()
    del b.sent[:], asked[:]
    QTest.keyClick(b.view, Key.Key_C, CTRL)
    assert b.clipboard() == "zsh" and b.sent == []
    assert b.view._gsel is None and len(asked) == 1
    b.key(Key.Key_N, widget=b.view)                     # the answer is its own
    assert b.sent == [b"n"] and b.cell.text() == ""
    b.session.feed(mark("C", "/work", "sl") + b"zsh: command not found\r\n"
                   + prompt(127))
    b.pump()
    assert not b.session.shell_asks
    assert b.view._input_rows() == 0
    assert b.editor.isEnabled() and b.editor.hasFocus()


# -- over a real shell --------------------------------------------------------

def ran(live) -> bool:
    """The newest block is a command that is over.  Until the shell has
    answered there may be no block at all."""
    blocks = live.session.blocks
    return bool(blocks) and blocks[-1].is_command \
        and blocks[-1].exit_code is not None


def test_a_command_typed_in_the_box_runs_in_the_shell(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "echo hi")
    QTest.keyClick(cell.editor, Key.Key_Left, SHIFT | CTRL)
    QTest.keyClicks(cell.editor, "ho; false")           # in place of "hi"
    assert cell.text() == "echo ho; false"
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live))
    block = live.session.blocks[-1]
    assert block.command == "echo ho; false"
    assert block.lines() == ["ho"] and block.exit_code == 1
    assert live.wait(lambda: cell.editor.isEnabled())
    assert cell.text() == ""


def test_ctrl_c_copies_a_selection_and_then_stops_the_program(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "while :; do echo reply; sleep 0.2; done")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: live.session.running is not None
                     and len(live.session.running.lines()) >= 3)
    block = live.session.running
    live.view._sel = ((block.id, 1, 0, 0), (block.id, 1, 0, 5))
    QTest.keyClick(live.view, Key.Key_C, CTRL)
    assert QGuiApplication.clipboard().text() == "reply"
    live.settle(0.5)
    assert block.running                                # copied, not stopped
    QTest.keyClick(live.view, Key.Key_C, CTRL)          # nothing selected now
    assert live.wait(lambda: not block.running)
    assert block.exit_code == 130
    assert live.wait(lambda: cell.editor.isEnabled()
                     and cell.editor.hasFocus())


def test_several_lines_typed_in_the_box_run_as_one_command(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "for i in 1 2")
    QTest.keyClick(cell.editor, Key.Key_Return)
    QTest.keyClicks(cell.editor, "do echo n$i")
    QTest.keyClick(cell.editor, Key.Key_Return)
    QTest.keyClicks(cell.editor, "done")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live))
    assert live.session.blocks[-1].lines() == ["n1", "n2"]


def test_the_shell_gives_back_a_command_it_cannot_run_yet(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    assert live.wait(lambda: cell.editor.isEnabled())
    before = len(live.session.blocks)
    QTest.keyClicks(cell.editor, "for i in 1 2; do")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: cell.text() == "for i in 1 2; do")
    assert "not complete" in cell.label.text()
    assert live.wait(lambda: cell.editor.isEnabled())   # at a prompt again
    assert len([b for b in live.session.blocks[before:]
                if b.is_command]) == 0
    cursor = cell.editor.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cell.editor.setTextCursor(cursor)
    QTest.keyClicks(cell.editor, " echo n$i; done")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live))
    assert live.session.blocks[-1].lines() == ["n1", "n2"]


def test_the_shell_asks_about_a_misspelt_command(live_window):
    live = live_window(rc="PS1='%% '\nsetopt correct\n", config=Config())
    cell = live.window.cell
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "ecoh hi")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: live.view._input_rows() > 0)
    shown = "\n".join(row.text() for row in live.session.input.grid)
    assert "correct" in shown and "ecoh" in shown
    QTest.keyClick(live.view, Key.Key_Y)                # yes: echo was meant
    assert live.wait(lambda: ran(live))
    assert live.session.blocks[-1].lines() == ["hi"]
    assert live.wait(lambda: cell.editor.isEnabled())
    assert live.view._input_rows() == 0


def files(live, *names: str) -> None:
    for name in names:
        with open(os.path.join(live.home, name), "w") as handle:
            handle.write("x\n")


def test_tab_completes_in_the_box_as_it_does_in_the_shell(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    files(live, "alpha.txt", "alphabet.txt", "beta.txt")
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "ls be")
    QTest.keyClick(cell.editor, Key.Key_Tab)
    assert live.wait(lambda: cell.text() == "ls beta.txt ")
    assert cell.editor.hasFocus()
    QTest.keyClick(cell.editor, Key.Key_A, CTRL)
    QTest.keyClicks(cell.editor, "ls al")
    QTest.keyClick(cell.editor, Key.Key_Tab)            # as far as they agree
    assert live.wait(lambda: cell.text() == "ls alpha")
    assert live.view._input_rows() == 0
    QTest.keyClick(cell.editor, Key.Key_Tab)            # the list
    assert live.wait(lambda: live.view._input_span() == (1, 1))
    assert live.session.input.grid[1].text().split() == \
        ["alpha.txt", "alphabet.txt"]
    assert cell.text() == "ls alpha" and cell.editor.hasFocus()
    QTest.keyClick(cell.editor, Key.Key_Tab)            # the first of them
    assert live.wait(lambda: cell.text() == "ls alpha.txt")
    QTest.keyClick(cell.editor, Key.Key_Tab)            # the next one
    assert live.wait(lambda: cell.text() == "ls alphabet.txt")
    QTest.keyClicks(cell.editor, " -1")                 # typing on:
    assert live.wait(lambda: live.view._input_rows() == 0)     # no list now
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live))
    assert live.session.blocks[-1].command == "ls alphabet.txt -1"
    assert live.session.blocks[-1].lines() == ["alphabet.txt"]


def test_up_and_down_walk_through_the_history_of_the_shell(live_window):
    live = live_window(config=Config())
    cell = live.window.cell
    for command in ("echo one", "echo two"):
        assert live.wait(lambda: cell.editor.isEnabled())
        QTest.keyClicks(cell.editor, command)
        QTest.keyClick(cell.editor, Key.Key_Return, CTRL)
        assert live.wait(lambda: ran(live) and cell.editor.isEnabled()
                         and live.session.blocks[-1].command == command)
    QTest.keyClicks(cell.editor, "dra")
    for key, text in ((Key.Key_Up, "echo two"), (Key.Key_Up, "echo one"),
                      (Key.Key_Down, "echo two"), (Key.Key_Down, "dra")):
        QTest.keyClick(cell.editor, key)
        assert live.wait(lambda: cell.text() == text), text
    QTest.keyClick(cell.editor, Key.Key_Up)
    assert live.wait(lambda: cell.text() == "echo two")
    QTest.keyClick(cell.editor, Key.Key_C, CTRL)        # thrown away
    assert cell.text() == ""
    live.settle(0.2)
    QTest.keyClick(cell.editor, Key.Key_Up)             # the newest again
    assert live.wait(lambda: cell.text() == "echo two")
    QTest.keyClick(cell.editor, Key.Key_Return, CTRL)   # and it runs
    assert live.wait(lambda: len([b for b in live.session.blocks
                                  if b.command == "echo two"]) == 2)


def test_a_menu_of_the_shell_takes_the_keyboard_and_gives_it_back(
        live_window):
    live = live_window(
        rc="PS1='%% '\nzmodload zsh/complist\n"
           "autoload -Uz compinit && compinit -u -d $HOME/.zcompdump\n"
           "zstyle ':completion:*' menu select\n", config=Config())
    cell = live.window.cell
    files(live, "alpha.txt", "alphabet.txt", "beta.txt")
    assert live.wait(lambda: cell.editor.isEnabled())
    QTest.keyClicks(cell.editor, "ls al")
    QTest.keyClick(cell.editor, Key.Key_Tab)
    assert live.wait(lambda: cell.text() == "ls alpha")
    QTest.keyClick(cell.editor, Key.Key_Tab)            # the list
    assert live.wait(lambda: live.view._input_span() == (1, 1))
    QTest.keyClick(cell.editor, Key.Key_Tab)            # the menu
    assert live.wait(lambda: live.session.shell_asks
                     and not cell.editor.isEnabled())
    assert live.view.hasFocus()
    assert live.view._input_span()[0] == 0              # its own line too
    assert "ls alpha.txt" in live.session.input.grid[0].text()
    QTest.keyClick(live.view, Key.Key_Right)            # with its own keys
    assert live.wait(
        lambda: "ls alphabet.txt" in live.session.input.grid[0].text())
    QTest.keyClick(live.view, Key.Key_Return)
    assert live.wait(lambda: cell.editor.isEnabled()
                     and cell.text() == "ls alphabet.txt ")
    assert cell.editor.hasFocus() and not live.session.shell_asks
    assert not [b for b in live.session.blocks if b.is_command]


def test_the_plugins_of_a_real_shell_show_in_the_box(live_window):
    live = live_window(rc="PS1='%% '\n" + STAND_INS, config=Config())
    cell = live.window.cell
    editor = cell.editor

    def colors() -> list:
        """Where the colors of the first line are, and how bold."""
        line = editor.document().firstBlock().layout()
        return [(one.start, one.length, one.format.fontWeight())
                for one in line.formats()]

    assert live.wait(lambda: editor.isEnabled())
    QTest.keyClicks(editor, "echo one two")
    QTest.keyClick(editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live) and editor.isEnabled())
    QTest.keyClicks(editor, "echo o")
    assert live.wait(lambda: editor.ghost == "ne two")  # as the line before
    assert colors() == [(0, 4, QFont.Weight.Normal)]
    QTest.keyClicks(editor, "ne")                       # typed along
    assert editor.ghost == " two"
    QTest.keyClick(editor, Key.Key_Right)               # and the rest taken
    assert cell.text() == "echo one two" and editor.ghost == ""
    QTest.keyClick(editor, Key.Key_A, CTRL)
    QTest.keyClicks(editor, "nosuch")
    assert live.wait(lambda: colors() == [(0, 6, QFont.Weight.Bold)])
    assert editor.ghost == ""
    QTest.keyClick(editor, Key.Key_A, CTRL)
    QTest.keyClicks(editor, "echo t")
    QTest.keyClick(editor, Key.Key_Return, CTRL)
    assert live.wait(lambda: ran(live) and editor.isEnabled()
                     and live.session.blocks[-1].command == "echo t")
    # All that asking has printed nothing, and shows nowhere.
    assert [block.command for block in live.session.blocks] == \
        ["echo one two", "echo t"]
    assert live.view._input_rows() == 0
    assert live.wait(lambda: colors() == []) and editor.ghost == ""
