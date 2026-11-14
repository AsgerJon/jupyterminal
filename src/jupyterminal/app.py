"""Command line entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys

from . import __version__
from . import config as _config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jupyterminal",
        description="A terminal emulator whose session is a list of "
                    "command blocks.")
    parser.add_argument("--version", action="version",
                        version=f"jupyterminal {__version__}")
    parser.add_argument("--config", metavar="FILE",
                        help="configuration file (default: "
                             f"{_config.default_path()})")
    parser.add_argument("--shell", metavar="PROGRAM",
                        help="shell to run (default: zsh)")
    parser.add_argument("--login", action="store_true",
                        help="start the shell as a login shell")
    parser.add_argument("-d", "--working-directory", metavar="DIR",
                        help="directory to start in")
    parser.add_argument("--print-default-config", action="store_true",
                        help="print a configuration file with every "
                             "default and exit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.print_default_config:
        sys.stdout.write(_config.DEFAULT_CONFIG)
        return 0

    path = args.config or _config.default_path()
    config = _config.load(path)
    for warning in config.warnings:
        print(f"jupyterminal: {warning}", file=sys.stderr)

    # Imported late so that --help and --version need no display.
    from PySide6.QtCore import QMetaObject, Qt
    from PySide6.QtWidgets import QApplication

    from .session import Session
    from .shell import find_shell, is_zsh
    from .ui import MainWindow

    app = QApplication.instance() or QApplication(sys.argv[:1])
    # Being told to stop is a normal way to end: hang up the shell and
    # remove the session's private directory.  Python only notices a
    # signal when it gets to run; the view's timer, which looks after the
    # shell several times a second, sees to that.  The request is queued,
    # because one that comes before the event loop has started would
    # otherwise be forgotten.
    for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, lambda *_: QMetaObject.invokeMethod(
            app, "quit", Qt.ConnectionType.QueuedConnection))
    app.setApplicationName("jupyterminal")
    app.setDesktopFileName("jupyterminal")

    shell = find_shell(args.shell or config.shell)
    if shutil.which(shell) is None:
        print(f"jupyterminal: cannot run {shell}: no such program",
              file=sys.stderr)
        return 127
    if not is_zsh(shell):
        print(f"jupyterminal: {shell} is not zsh; without its integration "
              "the whole session is one block.", file=sys.stderr)
    cwd = args.working_directory
    if cwd and not os.path.isdir(cwd):
        # Said, and no more: a shell that cannot enter its directory
        # starts where this program was started.
        print(f"jupyterminal: {cwd}: not a directory", file=sys.stderr)

    session = Session(100, 30, max_lines=config.scrollback_lines,
                      divert=config.divert_background_output)
    window = MainWindow(session, config)
    for warning in window.view.theme.warnings:
        print(f"jupyterminal: {path}: {warning}", file=sys.stderr)
    session.start(shell, cwd=cwd, login=args.login or config.login_shell)
    window.view.attach_io()
    window.show()
    status = app.exec()
    session.close()
    return status
