"""User configuration.

Read from ``$XDG_CONFIG_HOME/jupyterminal/config.toml`` (normally
``~/.config/jupyterminal/config.toml``).  Every key is optional; see
:data:`DEFAULT_CONFIG` for the full file with its defaults.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

#: action -> default shortcuts.  The first group works everywhere, the
#: ``blocks.*`` group only while a block is selected.
DEFAULT_KEYS: dict[str, list[str]] = {
    "copy": ["Ctrl+Shift+C"],
    "copy_command_and_output": ["Ctrl+Alt+Shift+C"],
    "paste": ["Ctrl+Shift+V"],
    "paste_selection": ["Shift+Ins"],
    "select_newest_block": ["Ctrl+Up"],
    "clear_blocks": ["Ctrl+Shift+K"],
    "find_in_block": ["Ctrl+Shift+F"],
    "filter_output": ["Ctrl+Shift+L"],
    "scroll_page_up": ["Shift+PgUp"],
    "scroll_page_down": ["Shift+PgDown"],
    "scroll_to_top": ["Shift+Home"],
    "scroll_to_bottom": ["Shift+End"],
    "zoom_in": ["Ctrl+=", "Ctrl++"],
    "zoom_out": ["Ctrl+-"],
    "zoom_reset": ["Ctrl+0"],
    "blocks.previous": ["Up"],
    "blocks.next": ["Down"],
    "blocks.extend_previous": ["Shift+Up"],
    "blocks.extend_next": ["Shift+Down"],
    "blocks.scroll_to_top": ["Ctrl+Shift+Up"],
    "blocks.scroll_to_bottom": ["Ctrl+Shift+Down"],
    "blocks.first": ["Home"],
    "blocks.last": ["End"],
    "blocks.page_up": ["PgUp"],
    "blocks.page_down": ["PgDown"],
    "blocks.focus_input": ["Esc"],
    "blocks.menu": ["Menu"],
    "blocks.toggle_collapse": [],
}

#: The ways a command can be typed; the first is the default.
INPUTS = ("editor", "shell")

DEFAULT_CONFIG = '''\
# jupyterminal configuration.  Every key is optional.

[font]
family = ""          # empty: the system's fixed-width font
size = 11.0

[shell]
program = ""         # empty: $SHELL if it is zsh, otherwise zsh from PATH
login = false        # start it as a login shell

[terminal]
# History cap, in output lines.  The oldest blocks are dropped whole.
scrollback_lines = 2000000
# Output that arrives while the prompt is idle goes into a background
# block instead of landing on the prompt.
divert_background_output = true
confirm_multiline_paste = true
# How a command is typed.  "editor": in a text box that edits like every
# other one on the desktop: the mouse places the cursor, Shift and the
# arrows select, Ctrl+C, Ctrl+X and Ctrl+V are the clipboard, Ctrl+Z takes
# back.  "shell": in the shell's own line editor, as in other terminals.
input = "editor"
# Characters besides letters and digits that a double-click treats as
# part of a word.
word_characters = "-_./~:@%+#=?&"
bell = "none"        # "none" or "visual"
# Let programs set the clipboard (OSC 52).
allow_clipboard_write = true

[keys]
# action = "Shortcut" or ["Shortcut", "Another"].  An empty list unbinds.
copy = "Ctrl+Shift+C"
copy_command_and_output = "Ctrl+Alt+Shift+C"
paste = "Ctrl+Shift+V"
paste_selection = "Shift+Ins"
select_newest_block = "Ctrl+Up"
clear_blocks = "Ctrl+Shift+K"
find_in_block = "Ctrl+Shift+F"
filter_output = "Ctrl+Shift+L"
scroll_page_up = "Shift+PgUp"
scroll_page_down = "Shift+PgDown"
scroll_to_top = "Shift+Home"
scroll_to_bottom = "Shift+End"
zoom_in = ["Ctrl+=", "Ctrl++"]
zoom_out = "Ctrl+-"
zoom_reset = "Ctrl+0"

[keys.blocks]
# These apply while a block is selected.
previous = "Up"
next = "Down"
extend_previous = "Shift+Up"
extend_next = "Shift+Down"
scroll_to_top = "Ctrl+Shift+Up"
scroll_to_bottom = "Ctrl+Shift+Down"
first = "Home"
last = "End"
page_up = "PgUp"
page_down = "PgDown"
focus_input = "Esc"
menu = "Menu"
toggle_collapse = []    # unbound: any printable key returns to the input

[colors]
# Any color of the theme, as "#rrggbb" or by name.  The defaults:
# background = "#1a1c22"
# foreground = "#d5d8df"
# dim = "#7d8594"
# header_background = "#22252d"
# header_foreground = "#eef0f4"
# divider = "#525a6e"
# selected_background = "#1c2738"
# selected_header = "#25344f"
# selected_bar = "#4d8eff"
# failed_background = "#271b1e"
# failed_header = "#382024"
# failed_bar = "#e5534b"
# failed_foreground = "#ff7b72"
# accent = "#58a6ff"
# input_background = "#14161b"
# input_border = "#333845"
# cursor = "#d5d8df"
# cursor_text = "#1a1c22"
# selection = "#34507f"
# find = "#6a5a19"
# find_current = "#d4a72c"
# find_current_text = "#1a1c22"
# chip_background = "#2d323d"
# chip_foreground = "#aab1bf"
# scrollbar = "#3a404d"
# The sixteen colors programs choose by number: black, red, green,
# yellow, blue, magenta, cyan and white, then the bright one of each.
# ansi = ["#2b303b", "#e06c75", "#98c379", "#e5c07b",
#         "#61afef", "#c678dd", "#56b6c2", "#c8ccd4",
#         "#5c6370", "#ff7b86", "#b1e18b", "#f2d08a",
#         "#7cc0ff", "#d7a1f0", "#74d2de", "#ffffff"]
'''


@dataclass
class Config:
    font_family: str = ""
    font_size: float = 11.0
    shell: str = ""
    login_shell: bool = False
    scrollback_lines: int = 2_000_000
    divert_background_output: bool = True
    confirm_multiline_paste: bool = True
    input: str = "editor"
    word_characters: str = "-_./~:@%+#=?&"
    bell: str = "none"
    allow_clipboard_write: bool = True
    keys: dict[str, list[str]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_KEYS.items()})
    colors: dict = field(default_factory=dict)
    #: Problems found while reading the file, for the user to see.
    warnings: list[str] = field(default_factory=list)


def default_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "jupyterminal", "config.toml")


def _shortcuts(value) -> list[str] | None:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v for v in value if v]
    return None


def load(path: str | None = None) -> Config:
    """Read the configuration; a missing file gives the defaults."""
    config = Config()
    path = path or default_path()
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return config
    except (OSError, tomllib.TOMLDecodeError) as error:
        config.warnings.append(f"{path}: {error}")
        return config

    def take(section: str, key: str, attr: str, kind) -> None:
        table = data.get(section)
        if not isinstance(table, dict) or key not in table:
            return
        value = table[key]
        if kind is float and isinstance(value, int) \
                and not isinstance(value, bool):
            value = float(value)
        if isinstance(value, kind) and not (kind is int
                                            and isinstance(value, bool)):
            setattr(config, attr, value)
        else:
            config.warnings.append(
                f"{path}: [{section}] {key} should be {kind.__name__}")

    take("font", "family", "font_family", str)
    take("font", "size", "font_size", float)
    take("shell", "program", "shell", str)
    take("shell", "login", "login_shell", bool)
    take("terminal", "scrollback_lines", "scrollback_lines", int)
    take("terminal", "divert_background_output",
         "divert_background_output", bool)
    take("terminal", "confirm_multiline_paste",
         "confirm_multiline_paste", bool)
    take("terminal", "input", "input", str)
    if config.input not in INPUTS:
        config.warnings.append(
            f"{path}: [terminal] input should be one of "
            + ", ".join(repr(name) for name in INPUTS)
            + f", not {config.input!r}")
        config.input = INPUTS[0]
    take("terminal", "word_characters", "word_characters", str)
    take("terminal", "bell", "bell", str)
    take("terminal", "allow_clipboard_write", "allow_clipboard_write", bool)

    keys = data.get("keys")
    if isinstance(keys, dict):
        for name, value in keys.items():
            if name == "blocks" and isinstance(value, dict):
                for sub, sub_value in value.items():
                    _bind(config, path, f"blocks.{sub}", sub_value)
            else:
                _bind(config, path, name, value)
    colors = data.get("colors")
    if isinstance(colors, dict):
        config.colors = colors
    return config


def _bind(config: Config, path: str, action: str, value) -> None:
    shortcuts = _shortcuts(value)
    if action not in DEFAULT_KEYS:
        config.warnings.append(f"{path}: unknown action {action!r}")
    elif shortcuts is None:
        config.warnings.append(
            f"{path}: {action} should be a shortcut or a list of them")
    else:
        config.keys[action] = shortcuts
