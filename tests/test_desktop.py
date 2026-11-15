"""The program's place on the desktop: its icon, and the entry that puts
it into the desktop's menus."""

from __future__ import annotations

import configparser
import os
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon

from jupyterminal import desktop

#: Everything an argument may hold that a command line takes specially.
RESERVED = " \t\n\"'\\><~|&;$*?#()`"


def exec_words(line: str) -> list[str]:
    """The arguments an ``Exec`` line stands for, read the way the Desktop
    Entry Specification says a desktop reads it: first as a string, in
    which a backslash escapes, then as a command of words, of which those
    in double quotes may hold anything, then with ``%%`` as a percent
    sign."""
    text = ""
    escaped = False
    for ch in line:
        if escaped:
            text += {"s": " ", "n": "\n", "t": "\t", "r": "\r"}.get(ch, ch)
            escaped = False
        elif ch == "\\":
            escaped = True
        else:
            text += ch
    words: list[str] = []
    word: str | None = None
    quoted = False
    letters = iter(text)
    for ch in letters:
        if quoted and ch == "\\":
            word += next(letters)
        elif ch == '"':
            quoted = not quoted
            word = word or ""
        elif ch == " " and not quoted:
            words.append(word)
            word = None
        else:
            word = (word or "") + ch
    words.append(word)
    return [word.replace("%%", "%") for word in words]


# -- the icon ----------------------------------------------------------------

def test_the_icon_comes_with_the_package():
    path = desktop.icon_path()
    assert path.endswith(os.path.join("jupyterminal", "ui",
                                      "jupyterminal.svg"))
    with open(path, encoding="utf-8") as handle:
        assert handle.read().startswith("<svg ")


def test_the_icon_is_a_picture_of_the_window(qapp):
    icon = QIcon(desktop.icon_path())
    assert not icon.isNull()
    blue = QColor("#4d8eff")
    for size in (16, 22, 64, 256):
        image = icon.pixmap(QSize(size, size)).toImage()
        assert image.size() == QSize(size, size)
        assert image.pixelColor(0, 0).alpha() == 0      # its corners are round
        assert image.pixelColor(size // 2, size * 5 // 64) == blue
    # A dark pane for what was printed over a white box for what is
    # typed, some of that selected.
    assert image.pixelColor(200, 120) == QColor("#1a1c22")
    assert image.pixelColor(208, 192) == QColor("#ffffff")
    assert image.pixelColor(140, 192) == blue


# -- the entry ---------------------------------------------------------------

def test_the_entry_names_the_program_its_command_and_its_icon(monkeypatch):
    monkeypatch.setattr(desktop, "icon_path", lambda: "/opt/jt/icon.svg")
    assert desktop.entry(["/usr/bin/python3", "-m", "jupyterminal"]) == (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=jupyterminal\n"
        "GenericName=Terminal\n"
        "Comment=A terminal in which typing and copying work as everywhere"
        " else\n"
        "Exec=/usr/bin/python3 -m jupyterminal\n"
        "Icon=/opt/jt/icon.svg\n"
        "Terminal=false\n"
        "Categories=System;TerminalEmulator;\n"
        "StartupWMClass=jupyterminal\n")
    # The icon is a string to the desktop as well: a backslash is doubled.
    monkeypatch.setattr(desktop, "icon_path", lambda: "/opt/a\\b.svg")
    assert "Icon=/opt/a\\\\b.svg\n" in desktop.entry(["x"])
    # And by default it is the one that comes with the package.
    monkeypatch.undo()
    assert f"Icon={desktop.icon_path()}\n" in desktop.entry(["x"])


@pytest.mark.parametrize("word, written", [
    ("/usr/bin/python3", "/usr/bin/python3"),           # as it is
    ("", '""'),                                         # nothing, in quotes
    ("two words", '"two words"'),
    ("50%", "50%%"),                                    # no field code
    ("100% sure", '"100%% sure"'),
    ("it's", '"it\'s"'),
    ('say "hi"', '"say \\\\"hi\\\\""'),                 # escaped, and the
    ("$HOME", '"\\\\$HOME"'),                           # backslash doubled
    ("back`tick", '"back\\\\`tick"'),
    ("back\\slash", '"back\\\\\\\\slash"'),             # four for one
])
def test_an_argument_is_written_the_way_the_desktop_reads_it(word, written):
    line = desktop.entry([word]).split("\n")[5]
    assert line == "Exec=" + written
    assert exec_words(written) == [word]


def test_every_special_character_puts_an_argument_in_quotes():
    for ch in RESERVED:
        written = desktop._word(f"a{ch}b")
        assert written.startswith('"a') and written.endswith('b"'), ch
        assert exec_words(written) == [f"a{ch}b"], ch
    command = ["/opt/my py/python", "-c", 'print("$x", `y`, \'%d\' % 1)',
               "C:\\dir", ""]
    line = desktop.entry(command).split("\n")[5]
    assert exec_words(line.removeprefix("Exec=")) == command


# -- how the program was started ---------------------------------------------

def test_the_program_is_started_again_the_way_it_was_started(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/opt/py/bin/python")
    monkeypatch.setattr(sys, "argv", ["main.py", "--install-desktop-entry"])
    script = ["/opt/py/bin/python", os.path.abspath("main.py")]
    monkeypatch.setitem(sys.modules, "__main__",
                        SimpleNamespace(__spec__=None))         # a script
    assert desktop.launcher() == script
    assert os.path.isabs(script[1])
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace(
        __spec__=SimpleNamespace(name="jupyterminal.__main__")))
    assert desktop.launcher() == ["/opt/py/bin/python", "-m", "jupyterminal"]
    # Another module run with -m is the script it is.
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace(
        __spec__=SimpleNamespace(name="pytest.__main__")))
    assert desktop.launcher() == script
    monkeypatch.setitem(sys.modules, "__main__", SimpleNamespace())
    assert desktop.launcher() == script
    monkeypatch.delitem(sys.modules, "__main__")
    assert desktop.launcher() == script


# -- installing it -----------------------------------------------------------

def test_the_entry_goes_where_the_desktop_looks_for_this_users(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/xdg/data")
    assert desktop.entry_path() == \
        "/xdg/data/applications/jupyterminal.desktop"
    monkeypatch.setenv("HOME", "/home/someone")
    for unset in (lambda: monkeypatch.setenv("XDG_DATA_HOME", ""),
                  lambda: monkeypatch.delenv("XDG_DATA_HOME")):
        unset()
        assert desktop.entry_path() == \
            "/home/someone/.local/share/applications/jupyterminal.desktop"


def test_installing_writes_the_entry_and_says_where(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    command = ["/opt/my py/python", "/home/me/main.py"]
    path = desktop.install(command)
    assert path == str(tmp_path / "data" / "applications"
                       / "jupyterminal.desktop")
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == desktop.entry(command)
    # A file of the kind every desktop reads.
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path, encoding="utf-8")
    assert parser.sections() == ["Desktop Entry"]
    read = dict(parser["Desktop Entry"])
    assert read["Type"] == "Application" and read["Terminal"] == "false"
    assert read["Name"] == read["StartupWMClass"] == desktop.NAME
    assert read["Icon"] == desktop.icon_path()
    assert exec_words(read["Exec"]) == command
    # Installing again puts the new one in its place.
    assert desktop.install(["/usr/bin/jupyterminal"]) == path
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == desktop.entry(["/usr/bin/jupyterminal"])
