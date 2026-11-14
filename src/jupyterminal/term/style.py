"""Text styles.

A style is the triple ``(fg, bg, attrs)``.  Styles are interned into small
integer ids so a screen can keep one int per cell and compare styles by
identity.  Style id 0 is always the default style.

Colors are stored unresolved, so a theme change recolors old output:

* ``None``                - the default foreground / background
* ``0 .. 255``            - an index into the 256-color palette
* ``RGB | 0xRRGGBB``      - a direct 24-bit color
"""

from __future__ import annotations

BOLD = 1
DIM = 2
ITALIC = 4
UNDERLINE = 8
BLINK = 16
INVERSE = 32
HIDDEN = 64
STRIKE = 128

RGB = 1 << 24

Style = tuple  # (fg, bg, attrs)

_styles: list[Style] = [(None, None, 0)]
_index: dict[Style, int] = {(None, None, 0): 0}


def intern(fg: int | None, bg: int | None, attrs: int) -> int:
    """Return the id of the style ``(fg, bg, attrs)``."""
    key = (fg, bg, attrs)
    sid = _index.get(key)
    if sid is None:
        sid = len(_styles)
        _styles.append(key)
        _index[key] = sid
    return sid


def lookup(sid: int) -> Style:
    """Return the ``(fg, bg, attrs)`` triple of a style id."""
    return _styles[sid]


def _num(text: str) -> int:
    """The number a parameter stands for; 0 when it is empty or none."""
    try:
        return int(text)
    except ValueError:
        return 0


def _byte(text: str) -> int:
    return min(255, max(0, _num(text)))


def _extended(items: list[str], i: int) -> tuple[int | None, int]:
    """Parse ``38;5;n`` / ``38;2;r;g;b`` starting at ``items[i]``.

    Returns ``(color, next_index)``; color is ``None`` when malformed.
    """
    n = len(items)
    if i + 1 >= n:
        return None, n
    mode = items[i + 1]
    if mode == "5":
        if i + 2 < n:
            return _byte(items[i + 2]), i + 3
        return None, n
    if mode == "2":
        if i + 4 < n:
            r, g, b = (_byte(x) for x in items[i + 2:i + 5])
            return RGB | (r << 16) | (g << 8) | b, i + 5
        return None, n
    return None, i + 2


def _extended_colon(sub: list[str]) -> int | None:
    """Parse the colon form: ``38:5:n``, ``38:2:r:g:b`` or ``38:2::r:g:b``."""
    if len(sub) < 3:
        return None
    if sub[1] == "5":
        return _byte(sub[2])
    if sub[1] == "2" and len(sub) >= 5:
        r, g, b = (_byte(x) for x in sub[-3:])
        return RGB | (r << 16) | (g << 8) | b
    return None


def apply_sgr(params: str, fg: int | None, bg: int | None,
              attrs: int) -> tuple[int | None, int | None, int]:
    """Apply an SGR parameter string to a style and return the new style.

    No parameters at all are one empty parameter: a reset.
    """
    items = params.split(";")
    n = len(items)
    i = 0
    while i < n:
        item = items[i]
        if ":" in item:
            sub = item.split(":")
            code = _num(sub[0])
            if code == 38:
                color = _extended_colon(sub)
                if color is not None:
                    fg = color
            elif code == 48:
                color = _extended_colon(sub)
                if color is not None:
                    bg = color
            elif code == 4:
                if sub[1] == "0":
                    attrs &= ~UNDERLINE
                else:
                    attrs |= UNDERLINE
            i += 1
            continue
        code = _num(item)
        if code == 0:
            fg = bg = None
            attrs = 0
        elif code == 1:
            attrs |= BOLD
        elif code == 2:
            attrs |= DIM
        elif code == 3:
            attrs |= ITALIC
        elif code == 4 or code == 21:
            attrs |= UNDERLINE
        elif code == 5 or code == 6:
            attrs |= BLINK
        elif code == 7:
            attrs |= INVERSE
        elif code == 8:
            attrs |= HIDDEN
        elif code == 9:
            attrs |= STRIKE
        elif code == 22:
            attrs &= ~(BOLD | DIM)
        elif code == 23:
            attrs &= ~ITALIC
        elif code == 24:
            attrs &= ~UNDERLINE
        elif code == 25:
            attrs &= ~BLINK
        elif code == 27:
            attrs &= ~INVERSE
        elif code == 28:
            attrs &= ~HIDDEN
        elif code == 29:
            attrs &= ~STRIKE
        elif 30 <= code <= 37:
            fg = code - 30
        elif code == 38:
            color, i = _extended(items, i)
            if color is not None:
                fg = color
            continue
        elif code == 39:
            fg = None
        elif 40 <= code <= 47:
            bg = code - 40
        elif code == 48:
            color, i = _extended(items, i)
            if color is not None:
                bg = color
            continue
        elif code == 49:
            bg = None
        elif code == 58:
            # Underline color: parsed so its arguments are skipped.
            _, i = _extended(items, i)
            continue
        elif 90 <= code <= 97:
            fg = code - 90 + 8
        elif 100 <= code <= 107:
            bg = code - 100 + 8
        i += 1
    return fg, bg, attrs


#: The SGR parameter that turns each attribute on, in the order reported.
_ATTR_CODES = ((BOLD, 1), (DIM, 2), (ITALIC, 3), (UNDERLINE, 4), (BLINK, 5),
               (INVERSE, 7), (HIDDEN, 8), (STRIKE, 9))


def sgr_params(fg: int | None, bg: int | None, attrs: int) -> str:
    """The SGR parameters that set a style from any other: what
    :func:`apply_sgr` turns back into ``(fg, bg, attrs)``."""
    out = ["0"]
    out.extend(str(code) for flag, code in _ATTR_CODES if attrs & flag)
    for color, base in ((fg, 30), (bg, 40)):
        if color is None:
            continue
        if color & RGB:
            out.append(f"{base + 8};2;{(color >> 16) & 255};"
                       f"{(color >> 8) & 255};{color & 255}")
        elif color < 8:
            out.append(str(base + color))
        elif color < 16:
            out.append(str(base + 52 + color))
        else:
            out.append(f"{base + 8};5;{color}")
    return ";".join(out)


def palette_256() -> list[int]:
    """The xterm 256-color palette as ``0xRRGGBB`` ints.

    The first 16 entries are placeholders that a theme overrides.
    """
    base = [
        0x000000, 0xCD0000, 0x00CD00, 0xCDCD00, 0x0000EE, 0xCD00CD,
        0x00CDCD, 0xE5E5E5, 0x7F7F7F, 0xFF0000, 0x00FF00, 0xFFFF00,
        0x5C5CFF, 0xFF00FF, 0x00FFFF, 0xFFFFFF,
    ]
    levels = (0, 95, 135, 175, 215, 255)
    cube = [(r << 16) | (g << 8) | b
            for r in levels for g in levels for b in levels]
    gray = [0x010101 * (8 + 10 * step) for step in range(24)]
    return base + cube + gray
