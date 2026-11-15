"""The application window."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .cell import InputCell
from .view import TerminalView

APP_NAME = "jupyterminal"


class MainWindow(QWidget):
    """One window, one session."""

    def __init__(self, session, config) -> None:
        super().__init__()
        self.view = TerminalView(session, config, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.view, 1)
        #: The text box commands are typed in, unless the configuration
        #: leaves the typing to the shell's own line editor.
        self.cell = None
        extra = 0
        if config.input == "editor":
            self.cell = InputCell(self.view, self)
            layout.addWidget(self.cell)
            extra = self.cell.sizeHint().height()
        self.setWindowTitle(APP_NAME)
        self.view.titleChanged.connect(self._set_title)
        self.view.sessionEnded.connect(self.close)
        width, height = self.view.size_for(session.cols, session.rows)
        self.resize(width, height + extra)
        # What the window opens (a menu, a dialog, a tool tip) is drawn in
        # the colors of the theme as well.  With the background alone, a
        # desktop that writes in black would write on the dark background.
        theme = self.view.theme
        self.setStyleSheet(
            f"* {{ background: {theme.background.name()};"
            f" color: {theme.foreground.name()}; }}"
            f"QMenu {{ border: 1px solid {theme.input_border.name()};"
            " padding: 4px 0; }"
            "QMenu::item { padding: 4px 28px 4px 16px; }"
            f"QMenu::item:selected {{ background: {theme.selection.name()}; }}"
            f"QMenu::item:disabled {{ color: {theme.dim.name()}; }}"
            "QMenu::separator { height: 1px; margin: 4px 8px;"
            f" background: {theme.input_border.name()}; }}")
        self.view.setFocus()

    def _set_title(self, title: str) -> None:
        self.setWindowTitle(f"{title} - {APP_NAME}" if title else APP_NAME)

    def closeEvent(self, event) -> None:
        self.view.session.close()
        event.accept()
