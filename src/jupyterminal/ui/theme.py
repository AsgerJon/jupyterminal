"""Colors."""

from __future__ import annotations

import difflib
import json

from PySide6.QtGui import QColor

from ..term import style as _style

_DEFAULTS = {
    "background": "#1a1c22",
    "foreground": "#d5d8df",
    "dim": "#7d8594",
    "header_background": "#22252d",
    "header_foreground": "#eef0f4",
    "divider": "#525a6e",
    "selected_background": "#1c2738",
    "selected_header": "#25344f",
    "selected_bar": "#4d8eff",
    "failed_background": "#271b1e",
    "failed_header": "#382024",
    "failed_bar": "#e5534b",
    "failed_foreground": "#ff7b72",
    "accent": "#58a6ff",
    "input_background": "#14161b",
    "input_border": "#333845",
    "cursor": "#d5d8df",
    "cursor_text": "#1a1c22",
    "selection": "#34507f",
    "find": "#6a5a19",
    "find_current": "#d4a72c",
    "find_current_text": "#1a1c22",
    "chip_background": "#2d323d",
    "chip_foreground": "#aab1bf",
    "scrollbar": "#3a404d",
}

_ANSI = ["#2b303b", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd",
         "#56b6c2", "#c8ccd4", "#5c6370", "#ff7b86", "#b1e18b", "#f2d08a",
         "#7cc0ff", "#d7a1f0", "#74d2de", "#ffffff"]


#: What a color setting may hold; every warning about one says so.
_HOW = 'use "#rrggbb" or a color name such as "steelblue"'


def _shown(value) -> str:
    """A value the way the configuration file writes it."""
    return json.dumps(value, ensure_ascii=False, default=str)


class Theme:
    """Resolved colors, plus the mapping from style ids to pens."""

    def __init__(self, overrides: dict | None = None) -> None:
        overrides = overrides or {}
        #: What is wrong with ``overrides``.  Each entry names the setting,
        #: shows what it holds, says what it may hold and what is used
        #: instead, so that whoever reads it can put it right.
        self.warnings: list[str] = []
        for name, default in _DEFAULTS.items():
            color = QColor(default)
            value = overrides.get(name)
            if value is not None:
                custom = QColor(value) if isinstance(value, str) else QColor()
                if custom.isValid():
                    color = custom
                else:
                    self.warnings.append(
                        f"colors.{name} = {_shown(value)} is not a color: "
                        f'{_HOW}; the default "{default}" is used')
            setattr(self, name, color)
        palette = _style.palette_256()
        ansi = list(_ANSI)
        custom = overrides.get("ansi")
        if isinstance(custom, list):
            for i, value in enumerate(custom[:16]):
                if isinstance(value, str) and QColor(value).isValid():
                    ansi[i] = value
                else:
                    self.warnings.append(
                        f"colors.ansi[{i}] = {_shown(value)} is not a "
                        f'color: {_HOW}; the default "{_ANSI[i]}" is used')
            if len(custom) > 16:
                self.warnings.append(
                    f"colors.ansi has {len(custom)} entries, but only the "
                    "first 16 are used")
        elif custom is not None:
            self.warnings.append(
                f"colors.ansi = {_shown(custom)} is not a list: use up to "
                '16 colors, as ["#rrggbb", ...]; the defaults are used')
        names = [*_DEFAULTS, "ansi"]
        for name in overrides:
            if name not in names:
                close = difflib.get_close_matches(name, names, 1)
                hint = f'did you mean "{close[0]}"?' if close else \
                    "'jupyterminal --print-default-config' lists them"
                self.warnings.append(
                    f"colors.{name} is not a color of the theme; {hint}")
        self.palette = [QColor(c) for c in ansi] + [
            QColor((v >> 16) & 255, (v >> 8) & 255, v & 255)
            for v in palette[16:]]
        self._cache: dict[int, tuple] = {}

    def color(self, value: int | None, default: QColor) -> QColor:
        if value is None:
            return default
        if value & _style.RGB:
            return QColor((value >> 16) & 255, (value >> 8) & 255,
                          value & 255)
        return self.palette[value & 255]

    def resolve(self, sid: int) -> tuple:
        """``(fg, bg_or_None, attrs)`` for a style id, ready to paint."""
        hit = self._cache.get(sid)
        if hit is not None:
            return hit
        fg_value, bg_value, attrs = _style.lookup(sid)
        fg = self.color(fg_value, self.foreground)
        bg = self.color(bg_value, self.background) \
            if bg_value is not None else None
        if attrs & _style.INVERSE:
            fg, bg = (bg if bg is not None else self.background), fg
        if attrs & _style.DIM:
            base = bg if bg is not None else self.background
            fg = QColor((fg.red() * 3 + base.red() * 2) // 5,
                        (fg.green() * 3 + base.green() * 2) // 5,
                        (fg.blue() * 3 + base.blue() * 2) // 5)
        if attrs & _style.HIDDEN:
            fg = bg if bg is not None else self.background
        hit = (fg, bg, attrs)
        self._cache[sid] = hit
        return hit

    def rgb(self, kind: str, index: int = 0) -> tuple | None:
        """Answer a program's color query."""
        if kind == "fg":
            color = self.foreground
        elif kind == "bg":
            color = self.background
        elif kind == "cursor":
            color = self.cursor
        elif kind == "palette" and 0 <= index < 256:
            color = self.palette[index]
        else:
            return None
        return color.red(), color.green(), color.blue()
