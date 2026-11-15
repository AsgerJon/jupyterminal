"""The input as a text box.

A terminal's command line is edited with the keys of a line editor of the
1970s: Ctrl+A goes to the start of the line, nothing is selected with
Shift, and the mouse does not place the cursor.  Every other text field
on the desktop follows the conventions IBM wrote down in 1987 as Common
User Access: a click places the cursor, Shift and the arrows select,
typing replaces what is selected, Ctrl+C, Ctrl+X and Ctrl+V are the
clipboard and Ctrl+Z takes back.

:class:`InputCell` is such a field for the commands of a terminal.  What
is typed in it stays in the application until Ctrl+Enter hands it to the
shell, which runs it as if it had been typed at its prompt.

What only the shell can do with a command line, it still does: Tab, Up and
Down hand the text to the shell's own line editor, which completes a word
or fetches a line of its history as those keys do in the shell, and hands
the text back.  And what the plugins of the shell's configuration see in a
command line shows here as well: the colors of zsh-syntax-highlighting,
and behind the cursor, dimmed, the line zsh-autosuggestions would go on
to.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import (QColor, QFont, QKeyEvent, QPainter,
                           QTextCharFormat, QTextCursor, QTextLayout)
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QPlainTextEdit,
                               QVBoxLayout, QWidget)

from ..session.block import git_branch
from .view import PAD_LEFT, SCROLL_W, short_path

#: The box grows with its text up to this many lines; then it scrolls.
MAX_LINES = 8

_Key = Qt.Key
_SHIFT = Qt.KeyboardModifier.ShiftModifier
_CTRL = Qt.KeyboardModifier.ControlModifier
_ALT = Qt.KeyboardModifier.AltModifier
_META = Qt.KeyboardModifier.MetaModifier
_PLAIN = Qt.KeyboardModifier.NoModifier
_ENTER = (_Key.Key_Return, _Key.Key_Enter)
_UP = QTextCursor.MoveOperation.Up
_DOWN = QTextCursor.MoveOperation.Down

#: The keys whose work is the shell's, and the widget of the shell's line
#: editor that does it where the key itself is bound to nothing of use.
_ASKED = {_Key.Key_Tab: "expand-or-complete", _Key.Key_Backtab: "",
          _Key.Key_Up: "up-line-or-history",
          _Key.Key_Down: "down-line-or-history"}


#: The colors zsh has names for, in the order a terminal numbers them.
_NAMED = ("black", "red", "green", "yellow", "blue", "magenta", "cyan",
          "white")
#: As much of a suggestion as one more word: Ctrl+Right takes that.
_WORD = re.compile(r"\s*\S*\s*")


def _color(value: str, theme, bold: bool = False) -> QColor | None:
    """A color the way zsh writes one: a number of the terminal's 256,
    ``#rrggbb``, or a name, of which the beginning is enough.  Bold text
    in one of the first eight is shown in the bright one of the pair, as
    the styles of the plugins expect.  None for what is no color."""
    named = [number for number, name in enumerate(_NAMED)
             if value and name.startswith(value)]
    if named:
        value = str(named[0])
    if value.isdecimal() and int(value) < 256:
        number = int(value)
        return theme.palette[number + 8 if bold and number < 8 else number]
    color = QColor(value) if value.startswith("#") else QColor()
    return color if color.isValid() else None


def char_format(style: str, theme) -> QTextCharFormat:
    """A style of zsh's, such as ``fg=green,bold`` or ``underline``, as
    the format of the characters it is for.  What it does not say stays
    as the text has it."""
    made = QTextCharFormat()
    parts = [part.strip() for part in style.split(",")]
    bold = "bold" in parts
    paint = {"fg": made.setForeground, "bg": made.setBackground}
    for part in parts:
        name, _, value = part.partition("=")
        color = _color(value, theme, bold and name == "fg")
        if name in paint and color is not None:
            paint[name](color)
    if bold:
        made.setFontWeight(QFont.Weight.Bold)
    if "underline" in parts:
        made.setFontUnderline(True)
    if "standout" in parts:
        made.setBackground(theme.foreground)
        made.setForeground(theme.input_background)
    return made


def _modifiers(event):
    """The modifiers of a key that make it another key."""
    return event.modifiers() & (_SHIFT | _CTRL | _ALT | _META)


def _characters(text: str, units: int) -> int:
    """How many characters of ``text`` come before its UTF-16 code unit
    number ``units``.  Qt counts a cursor in such units, the shell in
    characters; the two differ from the first character beyond U+FFFF."""
    return len(text.encode("utf-16-le")[:2 * units]
               .decode("utf-16-le", "replace"))


def _units(text: str, characters: int) -> int:
    """The other way round: a character beyond U+FFFF is two units."""
    return characters + sum(ch > "\uffff" for ch in text[:characters])


class Editor(QPlainTextEdit):
    """A plain text box that can show, dimmed and behind its text, how the
    text might go on.  That is no part of the text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: What might follow the text, and the color to show it in.
        self.ghost = ""
        self.ghost_color = QColor()
        # It shows only behind the cursor: wherever that goes, look again.
        self.cursorPositionChanged.connect(self.viewport().update)

    def set_ghost(self, ghost: str) -> None:
        if ghost != self.ghost:
            self.ghost = ghost
            self.viewport().update()

    def ghost_shown(self) -> str:
        """What is shown of it: all of it where the cursor ends the text
        and nothing is selected, nothing elsewhere."""
        cursor = self.textCursor()
        if cursor.hasSelection() or not cursor.atEnd():
            return ""
        return self.ghost

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        # One line of it, as far as there is room; a sign for the rest.
        first, _, more = self.ghost_shown().partition("\n")
        place = self.cursorRect()
        metrics = self.fontMetrics()
        room = self.viewport().width() - place.left() \
            - round(self.document().documentMargin())
        shown = metrics.elidedText(first + ("\u2026" if more else ""),
                                   Qt.TextElideMode.ElideRight, room)
        with QPainter(self.viewport()) as painter:
            painter.setPen(self.ghost_color)
            painter.drawText(QPointF(place.left(),
                                     place.top() + metrics.ascent()), shown)


class InputCell(QWidget):
    """The text box commands are typed in, under a line that says where
    the shell is."""

    def __init__(self, view, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = view
        self.session = view.session
        self.label = QLabel(self)
        self.label.setIndent(0)
        self.editor = Editor(self)
        self.editor.setFrameShape(QFrame.Shape.NoFrame)
        self.editor.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.editor.installEventFilter(self)
        self.editor.viewport().installEventFilter(self)     # for the clicks
        layout = QVBoxLayout(self)
        # The text starts where the text of the blocks does: the document
        # of a text box keeps a margin of its own.
        margin = round(self.editor.document().documentMargin())
        layout.setContentsMargins(PAD_LEFT - margin, 4, 0, 4)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.editor)
        self._note = ""
        #: Keys pressed while the shell works on the text; they are typed
        #: once it has handed the text back.
        self._held: list[QKeyEvent] = []
        #: The line the shell's plugins would go on to from the text.
        self._suggestion = ""
        self.editor.document().documentLayout().documentSizeChanged.connect(
            self._fit)
        self.editor.textChanged.connect(self._edited)
        self.editor.selectionChanged.connect(self._selected)
        self.apply_style()
        # Not to be typed in before the view says that the shell is ready.
        self.editor.setEnabled(False)
        view.attach_cell(self)
        view.inputReady.connect(self.set_ready)
        self.session.on_incomplete = self._incomplete
        self.session.on_edited = self._answered
        self.session.on_hints = self._hinted
        view.sync_input()               # where the shell is by now

    # ------------------------------------------------------------------
    # looks
    # ------------------------------------------------------------------

    def apply_style(self) -> None:
        """Take the font and the colors of the view."""
        theme = self.view.theme
        self.editor.setFont(self.view.r.font)
        self.editor.ghost_color = theme.dim
        self.label.setFont(self.view.r.font)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(
            f"InputCell {{ background: {theme.input_background.name()};"
            f" border-top: 1px solid {theme.input_border.name()}; }}"
            f"QLabel {{ color: {theme.dim.name()};"
            " background: transparent; padding: 0 4px; }"
            f"QPlainTextEdit {{ color: {theme.foreground.name()};"
            " background: transparent;"
            f" selection-background-color: {theme.selection.name()};"
            f" selection-color: {theme.foreground.name()}; }}"
            f"QPlainTextEdit:disabled {{ color: {theme.dim.name()}; }}"
            "QScrollBar:vertical { background: transparent;"
            f" width: {SCROLL_W}px; margin: 0; }}"
            "QScrollBar::handle:vertical {"
            f" background: {theme.scrollbar.name()};"
            " min-height: 14px; border-radius: 3px; margin: 2px 3px; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical"
            " { height: 0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical"
            " { background: transparent; }")
        self._fit()

    def _fit(self, *_) -> None:
        """As tall as the text, between one line and :data:`MAX_LINES`."""
        document = self.editor.document()
        # The layout of a plain text document counts its height in lines,
        # and a document without text has one.
        lines = round(document.size().height())
        # A scroll bar only once there is something to scroll.
        self.editor.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if lines > MAX_LINES
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        spacing = self.editor.fontMetrics().lineSpacing()
        self.editor.setFixedHeight(round(
            min(lines, MAX_LINES) * spacing + 2 * document.documentMargin()))

    def show_place(self) -> None:
        """Say above the box where the shell is: its directory and the
        branch checked out there, as its prompt would."""
        cwd = self.session.cwd
        parts = [short_path(cwd)] if cwd else []
        branch = git_branch(cwd)
        if branch:
            parts.append(branch)
        if self._note:
            parts.append(self._note)
        self.label.setText(" · ".join(parts))

    # ------------------------------------------------------------------
    # the text
    # ------------------------------------------------------------------

    def text(self) -> str:
        return self.editor.toPlainText()

    def set_text(self, text: str) -> None:
        """Replace everything, leaving the cursor at the end."""
        self.editor.setPlainText(text)
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.editor.setTextCursor(cursor)

    def insert(self, text: str) -> None:
        """Put ``text`` where the cursor is, in place of what is
        selected, as typing it would."""
        self.editor.insertPlainText(text)

    def drop_selection(self) -> None:
        """Let go of what is selected here; the cursor stays where it is."""
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.clearSelection()
            self.editor.setTextCursor(cursor)

    def _selected(self) -> None:
        # One selection at a time: text selected here takes the place of
        # what is selected in the output.
        if self.editor.textCursor().hasSelection():
            self.view.drop_selection()

    def _edited(self) -> None:
        # Typed, pasted, cut: what the shell listed was for the text as
        # it was, and its plugins are asked about the text as it is.
        self.session.drop_list()
        self._changed()
        text = self.text()
        self.session.look(text, _characters(
            text, self.editor.textCursor().position()))

    # ------------------------------------------------------------------
    # what the shell's plugins see in the text
    # ------------------------------------------------------------------

    def _hinted(self, text: str, suggestion: str, spans: list) -> None:
        """The plugins have looked at ``text``: the line they would go on
        to, and the colors they give it."""
        if text != self.text():
            return                      # there is another text by now
        self._suggestion = suggestion
        self._show_ghost()
        self._color(spans)

    def _take(self, text: str) -> None:
        """Make ``text``, which was suggested, text of the box: a step of
        its own to take back, not one with what was typed before it."""
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(text)
        cursor.endEditBlock()

    def _show_ghost(self) -> None:
        """Show what is left of the suggestion behind the text; it stays
        while the text is typed along it."""
        text = self.text()
        self.editor.set_ghost(
            self._suggestion[len(text):]
            if text and self._suggestion.startswith(text) else "")

    def _color(self, spans: list) -> None:
        """Give the characters from ``start`` to ``end`` of each of
        ``spans`` its style.  Colors lie on the text; they are no change
        of it."""
        text = self.text()
        theme = self.view.theme
        colors = [(_units(text, start), _units(text, end),
                   char_format(style, theme)) for start, end, style in spans]
        document = self.editor.document()
        block = document.firstBlock()
        while block.isValid():
            first = block.position()
            last = first + block.length() - 1
            ranges = []
            for start, end, made in colors:
                if min(end, last) > max(start, first) \
                        and not made.isEmpty():
                    span = QTextLayout.FormatRange()
                    span.start = max(start, first) - first
                    span.length = min(end, last) - max(start, first)
                    span.format = made
                    ranges.append(span)
            block.layout().setFormats(ranges)
            # Formats on a layout are no news to the document: say that
            # the line is to be drawn again.
            document.markContentsDirty(first, block.length())
            block = block.next()

    def _changed(self) -> None:
        """The text is another one: what is selected in the output is let
        go of, a note about the old text has had its day, and of what was
        suggested only what still fits is left."""
        self.view.drop_selection()
        self._show_ghost()
        if self._note:
            self._note = ""
            self.show_place()

    # ------------------------------------------------------------------
    # the shell
    # ------------------------------------------------------------------

    def set_ready(self, ready: bool) -> None:
        """The shell takes a command now, or it does not: while it does
        not, the keys are for whatever is running."""
        had_focus = self.editor.hasFocus()
        self.editor.setEnabled(ready)
        if ready:
            focus = QApplication.focusWidget()
            if focus is None or (focus is self.view
                                 and not self.view.selected):
                self.editor.setFocus()
            return
        if had_focus:
            self.view.setFocus()
        # Keys that waited for an answer of the shell are its own now: it
        # is showing a menu or asking something.
        held, self._held = self._held, []
        for event in held:
            self.view.type_key(event)

    def submit(self) -> None:
        """Hand the text to the shell and start afresh."""
        text = self.text()
        if not self.session.run_command(text):
            return
        # Not clear(): what was run is not something to take back.
        self.editor.setPlainText("")
        self.view.scroll_to_bottom()
        self.view.sync_input()      # the shell is busy from this moment

    def _incomplete(self, text: str) -> None:
        """The shell would not run ``text``: it is back for finishing."""
        self.set_text(text)
        self._note = "not run: the command is not complete"
        self.show_place()

    def _interrupt(self) -> None:
        """Ctrl+C with nothing selected here: copy what is selected in
        the output, or else drop the line, as a shell does."""
        if self.view.copy_selection():
            return
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.removeSelectedText()

    # ------------------------------------------------------------------
    # what only the shell can do
    # ------------------------------------------------------------------

    def _ask(self, event) -> None:
        """Have the shell do on the text what this key does there."""
        text = self.text()
        cursor = _characters(text, self.editor.textCursor().position())
        self.session.ask_editor(self.view.key_bytes(event),
                                _ASKED[event.key()], text, cursor)

    def _answered(self, text: str, cursor: int) -> None:
        """The shell has done it: its text takes the place of what is in
        the box, as one step that can be taken back, and the keys pressed
        meanwhile are typed."""
        editor = self.editor
        # Without a word to those who listen for typing: this change does
        # not make what the shell lists out of date.
        editor.blockSignals(True)
        if text != self.text():
            whole = editor.textCursor()
            whole.select(QTextCursor.SelectionType.Document)
            whole.insertText(text)
        place = editor.textCursor()
        place.setPosition(_units(text, cursor))
        editor.setTextCursor(place)
        editor.blockSignals(False)
        self._changed()
        held, self._held = self._held, []
        self._type(held)

    def _type(self, events: list) -> None:
        """Type keys that were held back; one that asks the shell again
        holds back the ones after it."""
        for event in events:
            QApplication.sendEvent(self.editor, event)

    # ------------------------------------------------------------------
    # keys
    # ------------------------------------------------------------------

    def take_key(self, event) -> None:
        """A key pressed elsewhere that is meant for the text box."""
        self.editor.setFocus()
        QApplication.sendEvent(self.editor, event)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            # A click in the box is a click elsewhere for the output.
            self.view.drop_selection()
            return False
        if kind != QEvent.Type.KeyPress:
            return False
        if self.session.editing:
            if event.key() == _Key.Key_C and _modifiers(event) == _CTRL:
                # No waiting for an answer that takes too long: the shell
                # is interrupted, and hands the text back as it was.
                self.session.write(b"\x03")
            else:
                # The shell is at work on the text: the key waits.
                self._held.append(QKeyEvent(
                    kind, event.key(), event.modifiers(), event.text(),
                    event.isAutoRepeat(), event.count()))
            return True
        return self._key(event)

    def _key(self, event) -> bool:
        """Handle the keys that mean something else here than in a plain
        text box; True when the key was one of them."""
        key = event.key()
        mods = _modifiers(event)
        editor = self.editor
        if key in _ENTER:
            # Enter is a key of the text, as in every box of several lines;
            # a command is sent on purpose, with Ctrl.
            if mods == _CTRL:
                self.submit()
            else:
                editor.insertPlainText("\n")
            return True
        if key in (_Key.Key_Tab, _Key.Key_Backtab):
            self._ask(event)            # the shell completes
            return True
        ghost = editor.ghost_shown()
        if ghost and key == _Key.Key_Right and mods == _CTRL:
            self._take(_WORD.match(ghost).group())
            return True                 # a word of what is suggested
        if ghost and key in (_Key.Key_Right, _Key.Key_End) \
                and mods == _PLAIN:
            self._take(ghost)
            return True                 # all of it
        if mods == _CTRL:
            if key == _Key.Key_C and not editor.textCursor().hasSelection():
                self._interrupt()
                return True
            if key == _Key.Key_D and not self.text():
                self.session.write(b"\x04")     # end of input: the shell exits
                return True
            if key == _Key.Key_L:
                self.session.write(b"\x0c")     # the shell clears the screen
                return True
        # On the first line Up, and on the last one Down, have nowhere
        # to go in the text: there they are the keys of the history.
        if mods == _PLAIN and key == _Key.Key_Up \
                and not editor.textCursor().movePosition(_UP):
            self._ask(event)
            return True
        if mods == _PLAIN and key == _Key.Key_Down \
                and not editor.textCursor().movePosition(_DOWN):
            self._ask(event)
            return True
        action = self.view.cell_action(event)
        if action is None:
            return False
        if action == "copy" and editor.textCursor().hasSelection():
            editor.copy()
        else:
            self.view.do(action)
            if self.view.selected:
                self.view.setFocus()    # the keys are for the blocks now
        return True
