"""Turning key presses and mouse actions into the bytes a program expects
(the xterm conventions)."""

from __future__ import annotations

SHIFT = 1
ALT = 2
CTRL = 4

# name -> (CSI final for the plain form, SS3 allowed in application mode)
_CURSOR = {"Up": "A", "Down": "B", "Right": "C", "Left": "D",
           "Home": "H", "End": "F"}
_TILDE = {"Insert": 2, "Delete": 3, "PageUp": 5, "PageDown": 6,
          "F5": 15, "F6": 17, "F7": 18, "F8": 19, "F9": 20, "F10": 21,
          "F11": 23, "F12": 24}
_SS3 = {"F1": "P", "F2": "Q", "F3": "R", "F4": "S"}

_CTRL_PUNCT = {"@": 0, " ": 0, "2": 0, "[": 27, "3": 27, "\\": 28, "4": 28,
               "]": 29, "5": 29, "^": 30, "6": 30, "~": 30, "_": 31,
               "7": 31, "/": 31, "-": 31, "?": 127, "8": 127}


def encode_key(name: str, mods: int = 0, app_cursor: bool = False
               ) -> bytes | None:
    """Encode a named (non-text) key, or return ``None`` if unknown."""
    modifier = 1 + mods if mods else 0
    final = _CURSOR.get(name)
    if final is not None:
        if modifier:
            return f"\x1b[1;{modifier}{final}".encode()
        return f"\x1b{'O' if app_cursor else '['}{final}".encode()
    number = _TILDE.get(name)
    if number is not None:
        if modifier:
            return f"\x1b[{number};{modifier}~".encode()
        return f"\x1b[{number}~".encode()
    final = _SS3.get(name)
    if final is not None:
        if modifier:
            return f"\x1b[1;{modifier}{final}".encode()
        return f"\x1bO{final}".encode()
    if name == "Enter":
        return b"\x1b\r" if mods & ALT else b"\r"
    if name == "Tab":
        if mods & SHIFT:
            return b"\x1b[Z"
        return b"\x1b\t" if mods & ALT else b"\t"
    if name == "Backspace":
        base = b"\x08" if mods & CTRL else b"\x7f"
        return b"\x1b" + base if mods & ALT else base
    if name == "Escape":
        return b"\x1b\x1b" if mods & ALT else b"\x1b"
    return None


def encode_char(char: str, mods: int = 0) -> bytes:
    """Encode a typed character with Ctrl / Alt applied."""
    if mods & CTRL and len(char) == 1:
        if "a" <= char <= "z":
            code = ord(char) - 96
        elif "A" <= char <= "Z":
            code = ord(char) - 64
        else:
            code = _CTRL_PUNCT.get(char)
        if code is not None:
            data = bytes((code,))
            return b"\x1b" + data if mods & ALT else data
    data = char.encode("utf-8", "replace")
    return b"\x1b" + data if mods & ALT else data


def encode_paste(text: str, bracketed: bool) -> bytes:
    """Encode pasted text as typed input."""
    text = text.replace("\r\n", "\n").replace("\n", "\r")
    if bracketed:
        # A paste must not be able to close its own bracket.
        text = text.replace("\x1b[201~", "")
        return b"\x1b[200~" + text.encode("utf-8", "replace") + b"\x1b[201~"
    return text.encode("utf-8", "replace")


def encode_mouse(button: int, col: int, row: int, mods: int, *,
                 release: bool = False, motion: bool = False,
                 sgr: bool = False, utf8: bool = False) -> bytes | None:
    """Encode a mouse event.

    ``button`` is 0 left, 1 middle, 2 right, 3 none (motion without a
    button), 64 wheel up, 65 wheel down.  ``col`` and ``row`` are
    zero-based cells.
    """
    code = button
    if motion:
        code += 32
    if mods & SHIFT:
        code += 4
    if mods & ALT:
        code += 8
    if mods & CTRL:
        code += 16
    if sgr:
        return f"\x1b[<{code};{col + 1};{row + 1}{'m' if release else 'M'}" \
            .encode()
    if release:
        code |= 3                   # a release does not say which button
    x = col + 33
    y = row + 33
    if utf8:
        if x > 0x7FF or y > 0x7FF:
            return None
        return ("\x1b[M" + chr(32 + code) + chr(x) + chr(y)).encode("utf-8")
    if x > 255 or y > 255:
        return None
    return b"\x1b[M" + bytes((32 + code, x, y))
