"""A place on the desktop: the icon, and the entry that puts the program
into the desktop's menus.

A desktop knows an application by an entry, a file named after it.  A
window that says which entry it belongs to gets that entry's icon in the
task bar and in the window switcher; on Wayland that is the only way to
an icon of its own.  :func:`install` writes the entry for the user who
runs it.
"""

from __future__ import annotations

import os
import sys
from importlib import resources

#: What the desktop knows the program by: its entry is ``NAME.desktop``.
NAME = "jupyterminal"

#: Characters that an argument of a command may only hold in quotes.
_RESERVED = frozenset(" \t\n\"'\\><~|&;$*?#()`")
#: Those that take a backslash in front even between quotes.
_ESCAPED = frozenset("\"`$\\")


def icon_path() -> str:
    """The file that holds the icon."""
    return str(resources.files("jupyterminal.ui") / (NAME + ".svg"))


def launcher() -> list[str]:
    """The command that starts the program the way it was started this
    time: as a module, or by a script such as the installed command or
    the ``main.py`` of a checkout."""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if spec is not None and spec.name == NAME + ".__main__":
        return [sys.executable, "-m", NAME]
    return [sys.executable, os.path.abspath(sys.argv[0])]


def _word(word: str) -> str:
    """One argument the way the ``Exec`` line of an entry writes it."""
    word = word.replace("%", "%%")          # a single one starts a field code
    if word and not _RESERVED.intersection(word):
        return word
    quoted = "".join("\\" + ch if ch in _ESCAPED else ch for ch in word)
    # The line is a string to the desktop before it is a command: every
    # backslash is written twice.
    return '"' + quoted.replace("\\", "\\\\") + '"'


def entry(command: list[str]) -> str:
    """The text of the entry that starts the program with ``command``."""
    return "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        "Name=" + NAME,
        "GenericName=Terminal",
        "Comment=A terminal in which typing and copying work as everywhere"
        " else",
        "Exec=" + " ".join(_word(word) for word in command),
        "Icon=" + icon_path().replace("\\", "\\\\"),
        "Terminal=false",
        "Categories=System;TerminalEmulator;",
        # Windows on X11 are matched to the entry by this.
        "StartupWMClass=" + NAME,
        ""))


def entry_path() -> str:
    """Where the entry of the user who runs this goes."""
    base = os.environ.get("XDG_DATA_HOME") \
        or os.path.expanduser("~/.local/share")
    return os.path.join(base, "applications", NAME + ".desktop")


def install(command: list[str]) -> str:
    """Write the entry that starts the program with ``command`` and
    return its path.  An entry that is there already is replaced."""
    path = entry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(entry(command))
    return path
