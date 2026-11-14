"""Display widths: which characters take no cell, one cell or two."""

from __future__ import annotations

import pytest

from jupyterminal.term.charwidth import (TAB_SIZE, char_width, is_simple,
                                         str_width)


@pytest.mark.parametrize("code,width", [
    (0x00, 0), (0x1F, 0),                       # C0 controls
    (0x20, 1), (0x41, 1), (0x7E, 1),            # printable ASCII
    (0x7F, 0), (0x80, 0), (0x9F, 0),            # DEL and the C1 controls
    (0xA0, 1), (0xAD, 1), (0xE9, 1), (0x2FF, 1),
    (0x300, 0),                                 # a combining accent
    (0x20DD, 0),                                # an enclosing mark
    (0x200B, 0), (0x200D, 0), (0x2060, 0), (0xFEFF, 0), (0x600, 0),
    (0x115F, 2),                                # Hangul leading filler
    (0x1160, 0), (0x11A8, 0), (0x11FF, 0),      # vowels and finals join
    (0x1200, 1),
    (0x436, 1), (0x2192, 1), (0x2500, 1),       # Cyrillic, arrows, boxes
    (0x65E5, 2), (0xFF21, 2), (0x3000, 2), (0x1F600, 2),
])
def test_char_width(code, width):
    assert char_width(chr(code)) == width
    assert char_width(chr(code)) == width       # and again, from the cache


@pytest.mark.parametrize("text,simple", [
    ("", True),
    ("plain ascii", True),
    ("tab\there", False),
    ("café", True),                     # Latin-1 letters need no lookup
    ("soft\xadhyphen", True),           # looked up, and one cell wide
    ("→ arrows and ─ boxes", True),
    ("жук", True),
    ("日本", False),                    # wide
    ("e\u0301", False),                 # a combining accent
    ("a\u200bb", False),                # a zero-width space
    ("é\t", False),                     # a tab after non-ASCII text
    ("ж\t", False),
    ("жx\u0301", False),                # the offender is not the first hit
])
def test_is_simple(text, simple):
    assert is_simple(text) is simple


@pytest.mark.parametrize("text,width", [
    ("", 0),
    ("abc", 3),
    ("\t", 8),
    ("a\tb", 9),
    ("1234567\t", 8),
    ("12345678\t", 16),
    ("\t\t", 16),
    ("café", 4),
    ("日本", 4),
    ("ab日", 4),
    ("e\u0301", 1),
    ("日\tx", 9),
])
def test_str_width(text, width):
    assert str_width(text) == width


def test_tab_stops_are_eight_cells_apart():
    assert TAB_SIZE == 8
    assert [str_width("x" * n + "\t") for n in (0, 7, 8, 15, 16)] == \
        [8, 8, 16, 16, 24]
