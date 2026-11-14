"""Display width of characters, in terminal cells.

A *simple* string is one in which every character occupies exactly one
cell: no tabs, no wide (East Asian) characters and no zero-width marks.
Simple strings can be wrapped by plain slicing, which is what keeps very
large outputs cheap.
"""

from __future__ import annotations

import re
import unicodedata

TAB_SIZE = 8

_cache: dict[str, int] = {}

#: Anything that could be other than one cell wide.  Deliberately a
#: superset; :func:`is_simple` confirms with the real table.
_MAYBE_COMPLEX = re.compile("[\t\u00ad\u0300-\U0010ffff]")


def char_width(ch: str) -> int:
    """Width of one character: 0, 1 or 2 cells."""
    width = _cache.get(ch)
    if width is None:
        code = ord(ch)
        if code < 0x300:
            width = 0 if (code < 0x20 or 0x7F <= code < 0xA0) else 1
        elif 0x1160 <= code <= 0x11FF:
            # Hangul vowels and final consonants join the syllable block
            # that the leading consonant before them started.
            width = 0
        else:
            category = unicodedata.category(ch)
            if category in ("Mn", "Me", "Cf"):
                width = 0
            elif unicodedata.east_asian_width(ch) in ("W", "F"):
                width = 2
            else:
                width = 1
        _cache[ch] = width
    return width


def is_simple(text: str) -> bool:
    """True when every character of ``text`` is exactly one cell wide."""
    if text.isascii():
        return "\t" not in text
    match = _MAYBE_COMPLEX.search(text)
    if match is None:
        return True
    width = char_width
    for ch in text[match.start():]:
        if width(ch) != 1:              # a tab counts as no cell here
            return False
    return True


def str_width(text: str) -> int:
    """Display width of ``text`` laid out from column 0 on an endless row."""
    col = 0
    width = char_width
    for ch in text:
        if ch == "\t":
            col = (col // TAB_SIZE + 1) * TAB_SIZE
        else:
            col += width(ch)
    return col
