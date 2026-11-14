"""The application window."""

from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .view import TerminalView

APP_NAME = "jupyterminal"


class MainWindow(QWidget):
    """One window, one session."""

    def __init__(self, session, config) -> None:
        super().__init__()
        self.view = TerminalView(session, config, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        self.setWindowTitle(APP_NAME)
        self.view.titleChanged.connect(self._set_title)
        self.view.sessionEnded.connect(self.close)
        width, height = self.view.size_for(session.cols, session.rows)
        self.resize(width, height)
        self.setStyleSheet(
            f"background: {self.view.theme.background.name()};")
        self.view.setFocus()

    def _set_title(self, title: str) -> None:
        self.setWindowTitle(f"{title} - {APP_NAME}" if title else APP_NAME)

    def closeEvent(self, event) -> None:
        self.view.session.close()
        event.accept()
