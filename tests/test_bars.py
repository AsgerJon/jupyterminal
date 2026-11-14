"""The bar for "Find in block" and "Filter output", and the question asked
before several lines are pasted."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                               QPlainTextEdit, QWidget)

from conftest import CTRL, NONE, SHIFT, pump_qt
from jupyterminal.session import FilterSpec
from jupyterminal.ui import bars
from jupyterminal.ui.bars import FILTER, FIND, SearchBar, confirm_paste
from jupyterminal.ui.theme import Theme

NO_FOCUS = Qt.FocusPolicy.NoFocus


class Host(QWidget):
    """Stands in for the view under the bar: it remembers every key that
    gets as far as itself."""

    def __init__(self) -> None:
        super().__init__()
        self.keys: list = []
        self.bar = SearchBar(Theme(), self)
        #: Made after the bar, so it lies above it until the bar is raised.
        self.sibling = QWidget(self)

    def keyPressEvent(self, event) -> None:
        self.keys.append(event.key())


class Heard:
    """What a bar has announced."""

    def __init__(self, bar: SearchBar) -> None:
        self.changed = 0
        self.steps: list[int] = []
        self.closed = 0
        bar.changed.connect(self.on_changed)
        bar.step.connect(self.steps.append)
        bar.closed.connect(self.on_closed)

    def on_changed(self) -> None:
        self.changed += 1

    def on_closed(self) -> None:
        self.closed += 1

    def all(self) -> tuple:
        return self.changed, self.steps, self.closed


@pytest.fixture
def host(qapp):
    window = Host()
    window.resize(900, 300)
    window.show()
    window.activateWindow()
    pump_qt(qapp, None)
    yield window
    window.close()


def press(key, modifiers=NONE) -> QKeyEvent:
    """A key press that nobody has accepted yet."""
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifiers)
    event.ignore()
    return event


# -- how the bar is made -----------------------------------------------------

def test_the_two_uses_of_the_bar_have_names():
    assert (FIND, FILTER) == ("find", "filter")


def test_the_bar_waits_hidden_for_a_search(host):
    bar = host.bar
    assert bar.isHidden() and not bar.isVisible()
    assert bar.mode == FIND
    assert bar.parent() is host and bar.objectName() == "searchBar"


def test_the_controls_of_the_bar_stand_in_a_row(host):
    bar = host.bar
    row = bar.layout()
    assert type(row) is QHBoxLayout and row.spacing() == 4
    margins = row.contentsMargins()
    assert (margins.left(), margins.top(), margins.right(),
            margins.bottom()) == (10, 6, 6, 6)
    assert [row.itemAt(i).widget() for i in range(row.count())] == [
        bar.title, bar.edit, bar.case, bar.regex, bar.invert, bar.context,
        bar.status, bar.previous, bar.next, bar.close_button]


def test_the_controls_say_what_they_are(host):
    bar = host.bar
    assert bar.title.text() == "Find"
    assert bar.edit.text() == "" and bar.edit.minimumWidth() == 180
    assert [(b.text(), b.toolTip()) for b in (bar.case, bar.regex,
                                              bar.invert)] == [
        ("Aa", "Match case"), (".*", "Regular expression"),
        ("!", "Show lines that do not match")]
    assert [(b.text(), b.toolTip()) for b in (bar.previous, bar.next,
                                              bar.close_button)] == [
        ("↑", "Previous match (Shift+Enter)"), ("↓", "Next match (Enter)"),
        ("×", "Close (Esc)")]
    assert (bar.context.minimum(), bar.context.maximum(),
            bar.context.value()) == (0, 99, 0)
    assert bar.context.prefix() == "±"
    assert bar.context.toolTip() == "Context lines around each match"
    assert (bar.context.minimumWidth(), bar.context.maximumWidth()) == (
        58, 58)
    assert bar.status.text() == "" and bar.status.minimumWidth() == 70
    assert bar.status.alignment() == Qt.AlignmentFlag.AlignCenter


def test_the_buttons_never_take_the_cursor_out_of_the_field(host):
    bar = host.bar
    toggles = (bar.case, bar.regex, bar.invert)
    buttons = (bar.previous, bar.next, bar.close_button)
    assert all(b.focusPolicy() == NO_FOCUS for b in toggles + buttons)
    assert all(b.isCheckable() and not b.isChecked() for b in toggles)
    assert not any(b.isCheckable() for b in buttons)


def test_the_bar_takes_its_colours_from_the_theme(qapp):
    theme = Theme({"header_background": "#010101", "foreground": "#020202",
                   "dim": "#030303", "input_border": "#040404",
                   "accent": "#050505", "input_background": "#060606"})
    bar = SearchBar(theme)
    assert " ".join(bar.styleSheet().split()) == " ".join([
        "QFrame#searchBar { background: #010101; border: 1px solid #040404;",
        "border-radius: 6px; }",
        "QLabel { color: #030303; background: transparent; }",
        "QLineEdit, QSpinBox { background: #060606; color: #020202;",
        "border: 1px solid #040404; border-radius: 4px;",
        "padding: 2px 6px; selection-background-color: #050505; }",
        "QLineEdit:focus { border-color: #050505; }",
        "QToolButton { color: #020202; background: transparent;",
        "border: 1px solid transparent; border-radius: 4px;",
        "padding: 2px 6px; }",
        "QToolButton:hover { border-color: #040404; }",
        "QToolButton:checked { background: #050505; color: #060606; }"])
    bar.apply_theme(Theme())
    assert "background: #22252d; border: 1px solid #333845;" \
        in bar.styleSheet()
    assert "#010101" not in bar.styleSheet()


def test_the_bar_is_painted_in_the_colour_of_the_headers(host):
    bar = host.bar
    bar.apply_theme(Theme({"header_background": "#102030"}))
    bar.open(FIND)
    picture = host.grab().toImage()
    middle = bar.y() + bar.height() // 2
    assert picture.pixelColor(bar.x() + 5, middle).name() == "#102030"
    beside = picture.pixelColor(bar.x() + bar.width() + 5, middle).name()
    assert beside != "#102030"


# -- opening it --------------------------------------------------------------

def test_opening_to_find_shows_the_controls_of_a_search(host):
    bar = host.bar
    bar.set_status("3 / 7")
    bar.open(FIND)
    assert bar.mode == FIND and bar.title.text() == "Find"
    assert bar.isVisible() and bar.status.text() == ""
    assert [w.isHidden() for w in (bar.invert, bar.context, bar.previous,
                                   bar.next)] == [True, True, False, False]
    assert (bar.edit.text(), bar.case.isChecked(), bar.regex.isChecked(),
            bar.invert.isChecked(), bar.context.value()) == (
        "", False, False, False, 0)


def test_opening_to_filter_shows_the_controls_of_a_filter(host):
    bar = host.bar
    bar.open(FILTER)
    assert bar.mode == FILTER and bar.title.text() == "Filter"
    assert bar.isVisible()
    assert [w.isHidden() for w in (bar.invert, bar.context, bar.previous,
                                   bar.next)] == [False, False, True, True]
    bar.open(FIND)
    assert bar.invert.isHidden() and not bar.next.isHidden()


def test_opening_with_a_filter_fills_the_controls_without_announcing_it(host):
    bar = host.bar
    heard = Heard(bar)
    bar.open(FILTER, FilterSpec("error", True, True, True, 3))
    assert (bar.edit.text(), bar.case.isChecked(), bar.regex.isChecked(),
            bar.invert.isChecked(), bar.context.value()) == (
        "error", True, True, True, 3)
    assert bar.spec() == FilterSpec("error", True, True, True, 3)
    bar.open(FILTER, FilterSpec("warn", regex=True))
    assert bar.spec() == FilterSpec("warn", True, False, False, 0)
    bar.open(FILTER, FilterSpec("warn", case_sensitive=True))
    assert bar.spec() == FilterSpec("warn", False, True, False, 0)
    # Opening without one empties them again.
    bar.open(FILTER)
    assert bar.spec() == FilterSpec("")
    assert heard.all() == (0, [], 0)
    # What the user does afterwards is announced as before.
    bar.edit.setText("x")
    assert heard.all() == (1, [], 0)


def test_opening_puts_the_bar_on_top_sized_to_its_controls(host):
    bar = host.bar
    assert host.children()[-1] is host.sibling
    bar.open(FILTER, FilterSpec("error"))
    assert host.children()[-1] is bar
    assert bar.size() == bar.sizeHint()
    bar.open(FIND, FilterSpec("error"))
    assert bar.size() == bar.sizeHint()


def test_opening_puts_the_cursor_in_the_field_with_its_text_selected(host):
    bar = host.bar
    bar.open(FIND, FilterSpec("error"))
    assert bar.edit.hasFocus()
    assert bar.edit.selectedText() == "error"


# -- what is searched for ----------------------------------------------------

def test_the_bar_reports_what_is_searched_for(host):
    bar = host.bar
    bar.open(FILTER)
    assert bar.spec() == FilterSpec("", False, False, False, 0)
    bar.edit.setText("a.c")
    bar.regex.setChecked(True)
    assert bar.spec() == FilterSpec("a.c", True, False, False, 0)
    bar.regex.setChecked(False)
    bar.case.setChecked(True)
    assert bar.spec() == FilterSpec("a.c", False, True, False, 0)
    bar.invert.setChecked(True)
    bar.context.setValue(2)
    assert bar.spec() == FilterSpec("a.c", False, True, True, 2)


def test_a_search_neither_inverts_nor_shows_context(host):
    bar = host.bar
    bar.open(FIND, FilterSpec("a.c", True, True, True, 2))
    assert bar.invert.isChecked() and bar.context.value() == 2
    assert bar.spec() == FilterSpec("a.c", True, True, False, 0)


def test_the_status_says_what_it_is_told(host):
    host.bar.set_status("2 of 5")
    assert host.bar.status.text() == "2 of 5"
    host.bar.set_status("")
    assert host.bar.status.text() == ""


# -- what the bar announces --------------------------------------------------

def test_every_change_of_the_search_is_announced(host):
    bar = host.bar
    bar.open(FILTER)
    heard = Heard(bar)
    QTest.keyClicks(bar.edit, "ab")
    assert bar.edit.text() == "ab" and heard.all() == (2, [], 0)
    bar.case.click()
    assert heard.all() == (3, [], 0)
    bar.regex.click()
    assert heard.all() == (4, [], 0)
    bar.invert.click()
    assert heard.all() == (5, [], 0)
    bar.context.setValue(4)
    assert heard.all() == (6, [], 0)
    assert bar.spec() == FilterSpec("ab", True, True, True, 4)


def test_enter_and_the_arrows_step_through_the_matches(host):
    bar = host.bar
    bar.open(FIND)
    heard = Heard(bar)
    QTest.keyClick(bar.edit, Qt.Key.Key_Return)
    QTest.keyClick(bar.edit, Qt.Key.Key_Return, SHIFT)
    QTest.keyClick(bar.edit, Qt.Key.Key_Enter)
    QTest.keyClick(bar.edit, Qt.Key.Key_Enter, SHIFT)
    QTest.keyClick(bar.edit, Qt.Key.Key_Return, CTRL)
    QTest.keyClick(bar.edit, Qt.Key.Key_Down)
    QTest.keyClick(bar.edit, Qt.Key.Key_Up)
    assert heard.all() == (0, [1, -1, 1, -1, 1, 1, -1], 0)
    bar.previous.click()
    bar.next.click()
    assert heard.all() == (0, [1, -1, 1, -1, 1, 1, -1, -1, 1], 0)
    assert bar.edit.text() == "" and host.keys == []


def test_escape_and_the_cross_close_the_bar(host):
    bar = host.bar
    bar.open(FIND)
    heard = Heard(bar)
    QTest.keyClick(bar.edit, Qt.Key.Key_Escape)
    assert heard.all() == (0, [], 1)
    bar.close_button.click()
    assert heard.all() == (0, [], 2)
    assert host.keys == []


def test_escape_closes_the_bar_from_the_number_of_context_lines_too(host):
    bar = host.bar
    bar.open(FILTER)
    heard = Heard(bar)
    bar.context.setFocus()
    QTest.keyClick(bar.context, Qt.Key.Key_Escape)
    assert heard.all() == (0, [], 1)
    assert host.keys == []                   # and went no further
    # Any other key the number does not use is not the bar's business.
    QTest.keyClick(bar.context, Qt.Key.Key_F5)
    assert heard.all() == (0, [], 1)
    assert host.keys == [Qt.Key.Key_F5]


# -- keys stay in the field --------------------------------------------------

def test_no_key_typed_in_the_field_reaches_what_is_under_the_bar(host):
    bar = host.bar
    bar.open(FILTER)
    QTest.keyClick(bar.edit, Qt.Key.Key_A)
    QTest.keyClick(bar.edit, Qt.Key.Key_F5)
    QTest.keyClick(bar.edit, Qt.Key.Key_PageUp)
    QTest.keyClick(bar.edit, Qt.Key.Key_K, CTRL | SHIFT)
    QTest.keyClick(bar.edit, Qt.Key.Key_Tab)
    QTest.keyClick(bar.edit, Qt.Key.Key_Backtab, SHIFT)
    assert host.keys == []
    assert bar.edit.text() == "a"
    # The same key typed elsewhere in the window does arrive.
    QTest.keyClick(host.sibling, Qt.Key.Key_F5)
    assert host.keys == [Qt.Key.Key_F5]


def test_a_key_the_field_has_no_use_for_is_taken_all_the_same(host):
    for key in (Qt.Key.Key_F5, Qt.Key.Key_Escape, Qt.Key.Key_Return,
                Qt.Key.Key_Down, Qt.Key.Key_Up):
        event = press(key)
        host.bar.edit.keyPressEvent(event)
        assert event.isAccepted(), key


def test_tab_stays_in_the_field(host):
    bar = host.bar
    bar.open(FILTER)                       # the context box could take it
    assert bar.edit.hasFocus()
    QTest.keyClick(bar.edit, Qt.Key.Key_Tab)
    assert bar.edit.hasFocus()
    QTest.keyClick(bar.edit, Qt.Key.Key_Backtab, SHIFT)
    assert bar.edit.hasFocus() and bar.edit.text() == ""
    for key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
        event = press(key)
        assert bar.edit.event(event) is True
        assert event.isAccepted(), key


def test_only_the_press_of_tab_is_held_back(host):
    edit = host.bar.edit
    release = QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Tab, NONE)
    edit.event(release)
    assert not release.isAccepted()        # left to whoever wants it
    typed = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_B, NONE, "b")
    assert edit.event(typed) is True
    assert edit.text() == "b"


# -- the question before a paste of several lines ----------------------------

ACCEPTED = QDialog.DialogCode.Accepted
REJECTED = QDialog.DialogCode.Rejected


class Asked:
    """Answers the question in place of the user and keeps what the
    dialog showed.

    The dialog the function makes is of a class that does not wait for
    anybody: it is looked at, the button named by ``reply`` is pressed,
    and the dialog's result is returned.  The result is first set to the
    opposite, so a button that is not wired to the dialog gives the
    wrong answer instead of none.
    """

    def __init__(self, monkeypatch, reply: str) -> None:
        self.reply = reply
        asked = self

        class Dialog(QDialog):
            def exec(self) -> int:
                return asked.look(self)

        monkeypatch.setattr(bars, "QDialog", Dialog)

    def look(self, dialog: QDialog) -> int:
        self.parent = dialog.parent()
        self.title = dialog.windowTitle()
        self.label = dialog.findChild(QLabel).text()
        preview = dialog.findChild(QPlainTextEdit)
        self.preview = preview
        self.text = preview.toPlainText()
        box = dialog.findChild(QDialogButtonBox)
        self.buttons = {button.text(): button for button in box.buttons()}
        self.paste = self.buttons["Paste"]
        self.cancel = box.button(QDialogButtonBox.StandardButton.Cancel)
        self.role = box.buttonRole(self.paste)
        self.default = self.paste.isDefault()
        pasting = self.reply == "Paste"
        dialog.setResult(REJECTED if pasting else ACCEPTED)
        (self.paste if pasting else self.cancel).click()
        return dialog.result()


def test_the_question_names_the_number_of_lines_and_shows_them(
        host, monkeypatch):
    asked = Asked(monkeypatch, "Paste")
    font = QFont("Courier New", 17)
    assert confirm_paste(host, "one\ntwo\nthree", font) is True
    assert asked.parent is host
    assert asked.title == "Paste several lines?"
    assert asked.label == (
        "You are about to paste 3 lines. Depending on the program that "
        "receives them, each line may run as it arrives.")
    assert asked.text == "one\ntwo\nthree"
    preview = asked.preview
    assert preview.isReadOnly()
    assert preview.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
    assert (preview.font().family(), preview.font().pointSize()) == (
        "Courier New", 17)
    assert preview.minimumSize() == QSize(520, 220)


def test_the_question_offers_to_paste_or_to_cancel(host, monkeypatch):
    asked = Asked(monkeypatch, "Paste")
    assert confirm_paste(host, "a\nb", QFont()) is True
    assert len(asked.buttons) == 2 and asked.cancel is not asked.paste
    assert asked.cancel in asked.buttons.values()
    assert asked.role == QDialogButtonBox.ButtonRole.AcceptRole
    assert asked.default is True
    asked = Asked(monkeypatch, "Cancel")
    assert confirm_paste(host, "a\nb", QFont()) is False


def test_every_kind_of_line_end_counts_as_one(host, monkeypatch):
    asked = Asked(monkeypatch, "Cancel")
    confirm_paste(host, "a\r\nb\rc\nd", QFont())
    assert asked.label.startswith("You are about to paste 4 lines. ")
    assert asked.text == "a\nb\nc\nd"


def test_a_final_line_end_does_not_count_as_a_line(host, monkeypatch):
    asked = Asked(monkeypatch, "Cancel")
    confirm_paste(host, "a\nb\n", QFont())
    assert asked.label.startswith("You are about to paste 2 lines. ")
    assert asked.text == "a\nb"
    # Only the one that ends the text: an empty line before it is a line.
    confirm_paste(host, "a\n\nb\r\n\r\n", QFont())
    assert asked.label.startswith("You are about to paste 4 lines. ")
    assert asked.text == "a\n\nb\n"


def test_a_long_paste_is_shown_by_its_first_two_hundred_lines(
        host, monkeypatch):
    asked = Asked(monkeypatch, "Cancel")
    lines = [f"line {n}" for n in range(301)]
    confirm_paste(host, "\n".join(lines[:200]), QFont())
    assert asked.label.startswith("You are about to paste 200 lines. ")
    assert asked.text == "\n".join(lines[:200])
    confirm_paste(host, "\n".join(lines[:201]), QFont())
    assert asked.text == "\n".join(lines[:200] + ["… 1 more line"])
    confirm_paste(host, "\n".join(lines[:202]), QFont())
    assert asked.text == "\n".join(lines[:200] + ["… 2 more lines"])
    confirm_paste(host, "\n".join(lines), QFont())
    assert asked.label.startswith("You are about to paste 301 lines. ")
    assert asked.text == "\n".join(lines[:200] + ["… 101 more lines"])


def test_the_question_is_really_asked_and_leaves_nothing_behind(host):
    seen = []

    def answer(code) -> None:
        dialog = host.findChildren(QDialog)[-1]
        seen.append((dialog.isModal(), dialog.isVisible()))
        dialog.done(code)

    QTimer.singleShot(0, lambda: answer(ACCEPTED))
    assert confirm_paste(host, "a\nb", QFont()) is True
    QTimer.singleShot(0, lambda: answer(REJECTED))
    assert confirm_paste(host, "a\nb", QFont()) is False
    assert seen == [(True, True), (True, True)]
    QCoreApplication.sendPostedEvents(None,
                                      QEvent.Type.DeferredDelete.value)
    assert host.findChildren(QDialog) == []
