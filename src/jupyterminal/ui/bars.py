"""The bar used by "Find in block" and "Filter output", and the paste
confirmation."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QSpinBox, QToolButton,
                               QVBoxLayout, QWidget)

from ..session import FilterSpec

FIND = "find"
FILTER = "filter"


class _QueryEdit(QLineEdit):
    """The text field; hands Enter and Escape to the bar."""

    submitted = Signal(int)
    cancelled = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            back = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.submitted.emit(-1 if back else 1)
        elif key == Qt.Key.Key_Down:
            self.submitted.emit(1)
        elif key == Qt.Key.Key_Up:
            self.submitted.emit(-1)
        else:
            super().keyPressEvent(event)
        # Whatever the field did not use stops here; it must not travel on
        # to the terminal underneath.
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            event.accept()
            return True
        return super().event(event)


class SearchBar(QFrame):
    """One row of controls shown over the top-right of the block list."""

    changed = Signal()
    step = Signal(int)
    closed = Signal()

    def __init__(self, theme, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = FIND
        self.setObjectName("searchBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(4)
        self.title = QLabel("Find")
        self.edit = _QueryEdit()
        self.edit.setMinimumWidth(180)
        self.case = self._toggle("Aa", "Match case")
        self.regex = self._toggle(".*", "Regular expression")
        self.invert = self._toggle("!", "Show lines that do not match")
        self.context = QSpinBox()        # from 0 to 99, as it comes
        self.context.setPrefix("±")
        self.context.setToolTip("Context lines around each match")
        self.context.setFixedWidth(58)
        self.status = QLabel("")
        self.status.setMinimumWidth(70)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.previous = self._button("↑", "Previous match (Shift+Enter)")
        self.next = self._button("↓", "Next match (Enter)")
        self.close_button = self._button("×", "Close (Esc)")
        for widget in (self.title, self.edit, self.case, self.regex,
                       self.invert, self.context, self.status,
                       self.previous, self.next, self.close_button):
            layout.addWidget(widget)
        self.edit.textChanged.connect(self.changed)
        self.context.valueChanged.connect(self.changed)
        for toggle in (self.case, self.regex, self.invert):
            toggle.toggled.connect(self.changed)
        self.edit.submitted.connect(self.step)
        self.edit.cancelled.connect(self.closed)
        self.previous.clicked.connect(lambda: self.step.emit(-1))
        self.next.clicked.connect(lambda: self.step.emit(1))
        self.close_button.clicked.connect(self.closed)
        self.apply_theme(theme)
        self.hide()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Escape closes the bar from any of its controls: the text field
        sees to its own, and what another one does not use arrives here."""
        if event.key() == Qt.Key.Key_Escape:
            self.closed.emit()
        else:
            super().keyPressEvent(event)

    def _toggle(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _button(self, text: str, tip: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def apply_theme(self, theme) -> None:
        bg = theme.header_background.name()
        fg = theme.foreground.name()
        dim = theme.dim.name()
        edge = theme.input_border.name()
        accent = theme.accent.name()
        field = theme.input_background.name()
        self.setStyleSheet(f"""
            QFrame#searchBar {{ background: {bg}; border: 1px solid {edge};
                                border-radius: 6px; }}
            QLabel {{ color: {dim}; background: transparent; }}
            QLineEdit, QSpinBox {{ background: {field}; color: {fg};
                border: 1px solid {edge}; border-radius: 4px;
                padding: 2px 6px; selection-background-color: {accent}; }}
            QLineEdit:focus {{ border-color: {accent}; }}
            QToolButton {{ color: {fg}; background: transparent;
                border: 1px solid transparent; border-radius: 4px;
                padding: 2px 6px; }}
            QToolButton:hover {{ border-color: {edge}; }}
            QToolButton:checked {{ background: {accent}; color: {field}; }}
        """)

    def open(self, mode: str, spec: FilterSpec | None = None) -> None:
        """Show the bar for finding or filtering."""
        self.mode = mode
        finding = mode == FIND
        self.title.setText("Find" if finding else "Filter")
        self.invert.setVisible(not finding)
        self.context.setVisible(not finding)
        self.previous.setVisible(finding)
        self.next.setVisible(finding)
        spec = spec or FilterSpec("")
        # Filling the controls is not the user changing them: ``changed``
        # stays silent meanwhile.
        blocked = self.blockSignals(True)
        self.edit.setText(spec.pattern)
        self.case.setChecked(spec.case_sensitive)
        self.regex.setChecked(spec.regex)
        self.invert.setChecked(spec.invert)
        self.context.setValue(spec.context)
        self.blockSignals(blocked)
        self.status.setText("")
        self.adjustSize()
        self.show()
        self.raise_()
        self.edit.setFocus()
        self.edit.selectAll()

    def spec(self) -> FilterSpec:
        return FilterSpec(self.edit.text(), self.regex.isChecked(),
                          self.case.isChecked(),
                          self.invert.isChecked() if self.mode == FILTER
                          else False,
                          self.context.value() if self.mode == FILTER else 0)

    def set_status(self, text: str) -> None:
        self.status.setText(text)


def confirm_paste(parent: QWidget, text: str, font: QFont) -> bool:
    """Ask before pasting several lines."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        # A final newline ends the last line; it does not begin another.
        lines.pop()
    dialog = QDialog(parent)
    dialog.setWindowTitle("Paste several lines?")
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(
        f"You are about to paste {len(lines)} lines. Depending on the "
        "program that receives them, each line may run as it arrives."))
    preview = QPlainTextEdit()
    preview.setReadOnly(True)
    preview.setFont(font)
    preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
    shown = lines[:200]
    more = len(lines) - len(shown)
    if more:
        shown.append(f"… {more} more line" + ("s" if more > 1 else ""))
    preview.setPlainText("\n".join(shown))
    preview.setMinimumSize(520, 220)
    layout.addWidget(preview)
    buttons = QDialogButtonBox()
    paste = buttons.addButton("Paste", QDialogButtonBox.ButtonRole.AcceptRole)
    buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    paste.setDefault(True)
    layout.addWidget(buttons)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    # The dialog belongs to ``parent``; without this every paste that
    # asked would leave one behind for as long as the window lives.
    dialog.deleteLater()
    return accepted
