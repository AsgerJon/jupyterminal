"""The command line entry point: run inside this process, where a little
program stands in for the shell, and once as a program of its own."""

from __future__ import annotations

import os
import runpy
import signal
import subprocess
import sys
import time

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QIcon

import jupyterminal
from jupyterminal import app as app_module
from jupyterminal import config as config_module
from jupyterminal import desktop as desktop_module
from jupyterminal.session import CLOSED
from jupyterminal.ui import MainWindow

SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

#  A stand-in for the shell: it writes down how it was started, then waits
#  for a line of input.
REPORTING = '''printf '%s\\n' "$0" "$*" "$(pwd -P)" "$SHELL" "$(stty size)" \\
    > "$0.report"
read line
'''
#  Another one, which stays until it is hung up on.
STAYING = "exec sleep 600\n"


class Stage:
    """Runs main() and looks at the application from inside its event
    loop."""

    def __init__(self, app, tmp_path) -> None:
        self.app = app
        self.tmp = tmp_path
        self.run_dir = tmp_path / "run"
        self.window: MainWindow | None = None
        self.seen: dict = {}

    def program(self, name: str, body: str) -> str:
        """An executable to run as the shell."""
        path = self.tmp / name
        path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    def config(self, text: str) -> str:
        path = self.tmp / "given.toml"
        path.write_text(text, encoding="utf-8")
        return str(path)

    def report(self, program: str) -> list[str]:
        """What a reporting stand-in wrote down."""
        with open(program + ".report", encoding="utf-8") as handle:
            return handle.read().split("\n")

    def look(self) -> None:
        """Note what the running application is like."""
        self.window, = [widget for widget in self.app.topLevelWidgets()
                        if isinstance(widget, MainWindow)
                        and widget.isVisible()]
        session = self.window.view.session
        self.seen = dict(
            names=(self.app.applicationName(),
                   self.app.applicationDisplayName(),
                   self.app.desktopFileName()),
            icon=self.app.windowIcon().pixmap(QSize(64, 64)).toImage(),
            size=(session.cols, session.rows),
            cap=session.max_lines,
            divert=session.divert,
            reading=self.window.view._notifier is not None,
            pid=session.pty.pid,
            directories=os.listdir(self.run_dir),
        )

    def answer(self) -> None:
        """Give the reporting stand-in its line: it ends, and so does the
        application."""
        self.look()
        self.window.view.session.write(b"done\n")

    def run(self, *argv: str, act=None, limit: float = 20.0) -> int:
        """Run main() to its end.  ``act`` is called once, as soon as the
        event loop runs; a run that has not ended after ``limit`` seconds
        is ended with status 99."""
        self.app.closeAllWindows()
        first = QTimer()
        first.setSingleShot(True)
        first.timeout.connect(act or (lambda: None))
        first.start(0)
        watchdog = QTimer()
        watchdog.setSingleShot(True)
        watchdog.timeout.connect(lambda: self.app.exit(99))
        watchdog.start(int(limit * 1000))
        try:
            return app_module.main(list(argv))
        finally:
            first.stop()
            watchdog.stop()

    def shell_is_gone(self) -> None:
        """The shell's process has ended and been collected, and nothing is
        left of its directory."""
        with pytest.raises(ProcessLookupError):
            os.kill(self.seen["pid"], 0)
        assert os.listdir(self.run_dir) == []


@pytest.fixture
def stage(qapp, tmp_path, monkeypatch):
    """main() takes over the signals of the process and names the
    application; both are put back afterwards.  The signals are unblocked
    meanwhile, whatever started the tests."""
    handlers = [signal.getsignal(number) for number in SIGNALS]
    mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, SIGNALS)
    names = (qapp.applicationName(), qapp.desktopFileName())
    made = Stage(qapp, tmp_path)
    made.run_dir.mkdir(mode=0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(made.run_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-config"))
    # No desktop entry is installed unless a test puts one here.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "share"))
    monkeypatch.setenv("SHELL", "/nonexistent/login-shell")
    yield made
    for number, handler in zip(SIGNALS, handlers, strict=True):
        signal.signal(number, handler)
    signal.pthread_sigmask(signal.SIG_SETMASK, mask)
    # The name shown to people is left alone: it follows the name of the
    # application until it is set, and can never follow it again after.
    qapp.setApplicationName(names[0])
    qapp.setDesktopFileName(names[1])
    qapp.setWindowIcon(QIcon())


# -- answers that need no window ---------------------------------------------

def test_the_default_configuration_is_printed(capsys):
    assert app_module.main(["--print-default-config"]) == 0
    captured = capsys.readouterr()
    assert captured.out == config_module.DEFAULT_CONFIG
    assert captured.err == ""


def test_the_version_is_printed(capsys):
    with pytest.raises(SystemExit) as caught:
        app_module.main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out \
        == f"jupyterminal {jupyterminal.__version__}\n"


def test_the_help_names_every_option(capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/xdg")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    with pytest.raises(SystemExit) as caught:
        app_module.main(["--help"])
    assert caught.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert text.startswith("usage: jupyterminal [-h] [--version] "
                           "[--config FILE] [--shell PROGRAM] [--login] "
                           "[-d DIR] [--print-default-config] "
                           "[--install-desktop-entry]")
    for phrase in (
            "A terminal emulator whose session is a list of command blocks.",
            "--config FILE configuration file (default: "
            "/xdg/jupyterminal/config.toml)",
            "--shell PROGRAM shell to run (default: zsh)",
            "--login start the shell as a login shell",
            "--working-directory DIR directory to start in",
            "--print-default-config print a configuration file with every "
            "default and exit",
            "--install-desktop-entry put the program into the menus of the "
            "desktop, which also gives its windows their icon there, and "
            "exit"):
        assert phrase in text


def test_the_desktop_entry_is_installed_without_a_window(
        capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python")
    monkeypatch.setattr(sys, "argv", ["/work/main.py"])
    # Before any configuration is read, and without a display.
    monkeypatch.setattr(config_module, "load", None)
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", None)
    assert app_module.main(["--install-desktop-entry"]) == 0
    path = str(tmp_path / "applications" / "jupyterminal.desktop")
    captured = capsys.readouterr()
    assert captured.out == (f"jupyterminal: wrote {path}; delete that file "
                            "to take the program out of the menus again\n")
    assert captured.err == ""
    with open(path, encoding="utf-8") as handle:
        assert "Exec=/opt/py/bin/python /work/main.py\n" in handle.read()


def test_python_m_jupyterminal_is_the_same_program(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["jupyterminal",
                                      "--print-default-config"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("jupyterminal", run_name="__main__")
    assert caught.value.code == 0
    assert capsys.readouterr().out == config_module.DEFAULT_CONFIG


# -- running -----------------------------------------------------------------

def test_a_run_shows_the_window_and_ends_when_the_shell_does(stage, capsys):
    shell = stage.program("zsh", REPORTING)
    here = os.path.realpath(os.getcwd())
    assert stage.run("--shell", shell, act=stage.answer) == 0
    assert stage.report(shell) == [shell, "", here, shell, "30 100", ""]
    # No desktop entry is installed: none is claimed.
    assert stage.seen["names"] == ("jupyterminal", "jupyterminal", "")
    # The windows carry the program's icon, for desktops that take it
    # from them.
    icon = QIcon(desktop_module.icon_path()).pixmap(QSize(64, 64))
    assert stage.seen["icon"] == icon.toImage() and not icon.isNull()
    assert stage.seen["size"] == (100, 30)
    assert stage.seen["cap"] == 2_000_000 and stage.seen["divert"] is True
    assert stage.seen["reading"] is True
    assert len(stage.seen["directories"]) == 1       # the shell's own
    assert not stage.window.isVisible()
    assert stage.window.view.session.state == CLOSED
    stage.shell_is_gone()
    assert capsys.readouterr().err == ""             # zsh: nothing to say


def test_the_window_belongs_to_the_desktop_entry_when_one_is_installed(
        stage, tmp_path):
    entries = tmp_path / "data" / "applications"
    entries.mkdir(parents=True)
    (entries / "jupyterminal.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=jupyterminal\n"
        "Exec=jupyterminal\n", encoding="utf-8")
    shell = stage.program("zsh", REPORTING)
    assert stage.run("--shell", shell, act=stage.answer) == 0
    assert stage.seen["names"] == ("jupyterminal",) * 3


def test_another_shell_runs_with_a_warning(stage, capsys):
    shell = stage.program("fish", REPORTING)
    assert stage.run("--shell", shell, act=stage.answer) == 0
    assert stage.report(shell)[:2] == [shell, ""]
    assert capsys.readouterr().err == (
        f"jupyterminal: {shell} is not zsh; without its integration the "
        "whole session is one block.\n")


def test_the_application_is_made_with_the_programs_name_alone(
        stage, monkeypatch):
    """Qt gets none of the options: they are this program's to read."""
    made = []

    class Application:
        """Stands in for the class while there is no application yet."""

        @staticmethod
        def instance() -> None:
            return None

        def __new__(cls, argv):
            made.append(argv)
            return stage.app

    monkeypatch.setattr(QtWidgets, "QApplication", Application)
    monkeypatch.setattr(sys, "argv", ["/usr/bin/jupyterminal", "--shell",
                                      "/nonexistent/shell", "-d", "/tmp"])
    assert stage.run("--shell", "/nonexistent/shell") == 127
    assert made == [["/usr/bin/jupyterminal"]]


def test_a_shell_that_does_not_exist_is_status_127(stage, capsys):
    assert stage.run("--shell", "/nonexistent/shell") == 127
    assert capsys.readouterr().err == (
        "jupyterminal: cannot run /nonexistent/shell: no such program\n")
    assert os.listdir(stage.run_dir) == []
    assert [widget for widget in stage.app.topLevelWidgets()
            if widget.isVisible()] == []


def test_the_shell_is_the_one_on_the_command_line_before_any_other(
        stage, monkeypatch):
    chosen = stage.program("zsh", REPORTING)
    others = stage.tmp / "others"
    others.mkdir()
    configured = stage.program("others/zsh", REPORTING)
    config = stage.config(f'[shell]\nprogram = "{configured}"\n')
    monkeypatch.setenv("SHELL", configured)
    assert stage.run("--config", config, "--shell", chosen,
                     act=stage.answer) == 0
    assert stage.report(chosen)[0] == chosen
    assert not os.path.exists(configured + ".report")


def test_the_configured_shell_runs_when_none_is_named(stage, monkeypatch):
    configured = stage.program("zsh", REPORTING)
    others = stage.tmp / "others"
    others.mkdir()
    login = stage.program("others/zsh", REPORTING)
    config = stage.config(f'[shell]\nprogram = "{configured}"\n')
    monkeypatch.setenv("SHELL", login)
    assert stage.run("--config", config, act=stage.answer) == 0
    assert stage.report(configured)[0] == configured
    assert not os.path.exists(login + ".report")


def test_the_users_own_shell_runs_when_nothing_is_said(stage, monkeypatch):
    login = stage.program("zsh", REPORTING)
    monkeypatch.setenv("SHELL", login)
    assert stage.run(act=stage.answer) == 0
    assert stage.report(login)[0] == login


@pytest.mark.parametrize("option", ["-d", "--working-directory"])
def test_the_shell_starts_in_the_directory_asked_for(stage, option):
    shell = stage.program("zsh", REPORTING)
    there = stage.tmp / "there"
    there.mkdir()
    assert stage.run("--shell", shell, option, str(there),
                     act=stage.answer) == 0
    assert stage.report(shell)[2] == os.path.realpath(there)


def test_a_directory_that_is_none_is_said_and_passed_over(stage, capsys):
    shell = stage.program("zsh", REPORTING)
    missing = str(stage.tmp / "missing")
    assert stage.run("--shell", shell, "-d", missing, act=stage.answer) == 0
    assert stage.report(shell)[2] == os.path.realpath(os.getcwd())
    assert capsys.readouterr().err \
        == f"jupyterminal: {missing}: not a directory\n"


def test_a_login_shell_on_request(stage):
    shell = stage.program("zsh", REPORTING)
    assert stage.run("--shell", shell, "--login", act=stage.answer) == 0
    assert stage.report(shell)[1] == "-l"


def test_a_login_shell_by_configuration(stage):
    shell = stage.program("zsh", REPORTING)
    config = stage.config("[shell]\nlogin = true\n")
    assert stage.run("--shell", shell, "--config", config,
                     act=stage.answer) == 0
    assert stage.report(shell)[1] == "-l"


def test_the_configuration_reaches_the_session(stage):
    shell = stage.program("zsh", REPORTING)
    config = stage.config("[terminal]\nscrollback_lines = 1234\n"
                          "divert_background_output = false\n")
    assert stage.run("--shell", shell, "--config", config,
                     act=stage.answer) == 0
    assert stage.seen["cap"] == 1234 and stage.seen["divert"] is False


def test_what_is_wrong_with_the_configuration_is_said(stage, capsys):
    shell = stage.program("fish", REPORTING)
    config = stage.config('[font]\nsize = "big"\n'
                          '[colors]\nbackground = "nope"\n')
    missing = str(stage.tmp / "missing")
    assert stage.run("--shell", shell, "--config", config, "-d", missing,
                     act=stage.answer) == 0
    assert capsys.readouterr().err.split("\n") == [
        f"jupyterminal: {config}: [font] size should be float",
        f"jupyterminal: {shell} is not zsh; without its integration the "
        "whole session is one block.",
        f"jupyterminal: {missing}: not a directory",
        f'jupyterminal: {config}: colors.background = "nope" is not a '
        'color: use "#rrggbb" or a color name such as "steelblue"; the '
        'default "#1a1c22" is used',
        ""]


# -- ending ------------------------------------------------------------------

def test_the_status_is_the_event_loops(stage):
    """Leaving the event loop by other means than the end of the shell:
    the shell is hung up on and its directory removed all the same."""
    shell = stage.program("zsh", STAYING)

    def leave() -> None:
        stage.look()
        stage.app.exit(5)

    assert stage.run("--shell", shell, act=leave) == 5
    assert stage.window.view.session.state == CLOSED
    stage.shell_is_gone()
    assert not stage.window.isVisible()


@pytest.mark.parametrize("number", SIGNALS)
def test_being_told_to_stop_is_a_normal_way_to_end(stage, number):
    shell = stage.program("zsh", STAYING)

    def stop() -> None:
        stage.look()
        os.kill(os.getpid(), number)

    assert stage.run("--shell", shell, act=stop) == 0
    assert stage.window.view.session.state == CLOSED
    stage.shell_is_gone()


def test_told_to_stop_before_the_event_loop_runs(stage, monkeypatch):
    shell = stage.program("zsh", STAYING)
    show = MainWindow.show

    def show_and_stop(window) -> None:
        show(window)
        stage.look()
        os.kill(os.getpid(), signal.SIGTERM)

    monkeypatch.setattr(MainWindow, "show", show_and_stop)
    assert stage.run("--shell", shell) == 0
    stage.shell_is_gone()


def test_as_a_program_of_its_own(tmp_path):
    """Started the way a user starts it, and told to stop: it ends with
    status 0, the shell is gone and so is its directory."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    pid_file = tmp_path / "pid"
    shell = tmp_path / "sh"
    shell.write_text(f'#!/bin/sh\necho $$ > "{pid_file}.new"\n'
                     f'mv "{pid_file}.new" "{pid_file}"\nexec sleep 600\n',
                     encoding="utf-8")
    shell.chmod(0o755)
    source = os.path.dirname(os.path.dirname(jupyterminal.__file__))
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=source,
               XDG_RUNTIME_DIR=str(run_dir),
               XDG_CONFIG_HOME=str(tmp_path / "no-config"))
    process = subprocess.Popen(
        [sys.executable, "-m", "jupyterminal", "--shell", str(shell)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 60.0
        while not pid_file.exists():
            assert process.poll() is None and time.monotonic() < deadline
            time.sleep(0.01)
        pid = int(pid_file.read_text())
        assert len(os.listdir(run_dir)) == 1
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=60.0)
    finally:
        process.kill()
    assert process.returncode == 0 and out == ""
    assert f"jupyterminal: {shell} is not zsh" in err
    assert os.listdir(run_dir) == []
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
