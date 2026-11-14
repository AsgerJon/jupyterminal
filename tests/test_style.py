"""Text styles: interning, and what each SGR parameter does."""

from __future__ import annotations

import pytest

from jupyterminal.term import style
from jupyterminal.term.style import (BLINK, BOLD, DIM, HIDDEN, INVERSE,
                                     ITALIC, RGB, STRIKE, UNDERLINE,
                                     apply_sgr, intern, lookup, palette_256,
                                     sgr_params)

ALL = BOLD | DIM | ITALIC | UNDERLINE | BLINK | INVERSE | HIDDEN | STRIKE


def sgr(params: str, fg=None, bg=None, attrs: int = 0):
    return apply_sgr(params, fg, bg, attrs)


# -- interning ----------------------------------------------------------------

def test_attribute_bits_are_distinct():
    assert [BOLD, DIM, ITALIC, UNDERLINE, BLINK, INVERSE, HIDDEN, STRIKE] \
        == [1, 2, 4, 8, 16, 32, 64, 128]
    assert RGB == 0x1000000


def test_the_default_style_is_number_zero():
    assert intern(None, None, 0) == 0
    assert lookup(0) == (None, None, 0)


def test_a_style_gets_one_id_for_good():
    key = (RGB | 0x123456, 201, BOLD | STRIKE)
    sid = intern(*key)
    assert sid > 0
    assert intern(*key) == sid
    assert lookup(sid) == key
    other = intern(RGB | 0x123456, 201, BOLD)
    assert other not in (0, sid)
    assert lookup(other) == (RGB | 0x123456, 201, BOLD)
    assert lookup(sid) == key


def test_a_new_style_takes_the_next_free_id():
    before = len(style._styles)
    key = (RGB | 0xABCDEF, RGB | 0xFEDCBA, ALL)
    assert key not in style._index
    assert intern(*key) == before
    assert len(style._styles) == before + 1
    assert intern(*key) == before
    assert len(style._styles) == before + 1


# -- attributes ---------------------------------------------------------------

def test_no_parameters_reset_everything():
    assert sgr("", 1, 2, ALL) == (None, None, 0)
    assert sgr("0", 1, 2, ALL) == (None, None, 0)
    # An empty parameter between others counts as zero.
    assert sgr("1;;3", 1, 2, 0) == (None, None, ITALIC)


@pytest.mark.parametrize("code,bit", [
    ("1", BOLD), ("2", DIM), ("3", ITALIC), ("4", UNDERLINE),
    ("21", UNDERLINE), ("5", BLINK), ("6", BLINK), ("7", INVERSE),
    ("8", HIDDEN), ("9", STRIKE),
])
def test_setting_an_attribute(code, bit):
    assert sgr(code) == (None, None, bit)
    assert sgr(code, 3, 4, bit) == (3, 4, bit)
    assert sgr(code, 3, 4, ALL & ~bit) == (3, 4, ALL)


@pytest.mark.parametrize("code,cleared", [
    ("22", BOLD | DIM), ("23", ITALIC), ("24", UNDERLINE), ("25", BLINK),
    ("27", INVERSE), ("28", HIDDEN), ("29", STRIKE),
])
def test_clearing_an_attribute(code, cleared):
    assert sgr(code, 3, 4, ALL) == (3, 4, ALL & ~cleared)
    assert sgr(code, 3, 4, 0) == (3, 4, 0)


def test_parameters_apply_in_order():
    assert sgr("1;4;7") == (None, None, BOLD | UNDERLINE | INVERSE)
    assert sgr("1;0;3") == (None, None, ITALIC)
    assert sgr("31;1;44;22") == (1, 4, 0)


def test_unknown_parameters_change_nothing():
    assert sgr("10;26;50;59;89;98;99;108;999", 3, 4, BOLD) == (3, 4, BOLD)
    # Something that is no number at all counts as zero: a reset.
    assert sgr("1;x", 3, 4, 0) == (None, None, 0)


# -- colors -------------------------------------------------------------------

def test_the_eight_colors_and_their_bright_versions():
    assert [sgr(str(code))[0] for code in range(30, 38)] == list(range(8))
    assert [sgr(str(code))[1] for code in range(40, 48)] == list(range(8))
    assert [sgr(str(code))[0] for code in range(90, 98)] == \
        list(range(8, 16))
    assert [sgr(str(code))[1] for code in range(100, 108)] == \
        list(range(8, 16))
    # Each touches its own side only.
    assert sgr("30", 5, 6, BOLD) == (0, 6, BOLD)
    assert sgr("37", 5, 6, BOLD) == (7, 6, BOLD)
    assert sgr("40", 5, 6, BOLD) == (5, 0, BOLD)
    assert sgr("47", 5, 6, BOLD) == (5, 7, BOLD)
    assert sgr("90", 5, 6, BOLD) == (8, 6, BOLD)
    assert sgr("97", 5, 6, BOLD) == (15, 6, BOLD)
    assert sgr("100", 5, 6, BOLD) == (5, 8, BOLD)
    assert sgr("107", 5, 6, BOLD) == (5, 15, BOLD)


def test_default_colors():
    assert sgr("39", 5, 6, BOLD) == (None, 6, BOLD)
    assert sgr("49", 5, 6, BOLD) == (5, None, BOLD)


def test_palette_colors():
    assert sgr("38;5;200") == (200, None, 0)
    assert sgr("48;5;17") == (None, 17, 0)
    assert sgr("38;5;0", 9, 9, 0) == (0, 9, 0)
    assert sgr("48;5;255", 9, 9, 0) == (9, 255, 0)
    # What follows the color still applies.
    assert sgr("38;5;200;1") == (200, None, BOLD)
    assert sgr("1;48;5;17;3") == (None, 17, BOLD | ITALIC)


def test_direct_colors():
    assert sgr("38;2;1;2;3") == (RGB | 0x010203, None, 0)
    assert sgr("48;2;255;128;0") == (None, RGB | 0xFF8000, 0)
    assert sgr("38;2;10;20;30;4") == (RGB | 0x0A141E, None, UNDERLINE)
    assert sgr("48;2;10;20;30;4") == (None, RGB | 0x0A141E, UNDERLINE)


def test_color_components_are_held_to_a_byte():
    assert sgr("38;5;256") == (255, None, 0)
    assert sgr("38;5;-3") == (0, None, 0)
    assert sgr("38;2;300;-1;255") == (RGB | 0xFF00FF, None, 0)
    assert sgr("48;5;abc") == (None, 0, 0)
    assert sgr("48;5;") == (None, 0, 0)


def test_a_color_cut_short_is_ignored_with_everything_after_it():
    assert sgr("38", 5, 6, 0) == (5, 6, 0)
    assert sgr("48", 5, 6, 0) == (5, 6, 0)
    assert sgr("38;5", 5, 6, 0) == (5, 6, 0)
    assert sgr("48;5", 5, 6, 0) == (5, 6, 0)
    assert sgr("38;2;1;2", 5, 6, 0) == (5, 6, 0)
    assert sgr("48;2;1;2", 5, 6, 0) == (5, 6, 0)
    assert sgr("1;38;2;1", 5, 6, 0) == (5, 6, BOLD)


def test_a_color_of_an_unknown_kind_is_skipped():
    assert sgr("38;9;1", 5, 6, 0) == (5, 6, BOLD)
    assert sgr("48;9;3", 5, 6, 0) == (5, 6, ITALIC)
    assert sgr("38;9", 5, 6, 0) == (5, 6, 0)


def test_underline_color_is_read_past():
    assert sgr("58;5;3;1", 5, 6, 0) == (5, 6, BOLD)
    assert sgr("58;2;1;2;3;4", 5, 6, 0) == (5, 6, UNDERLINE)
    assert sgr("58", 5, 6, BOLD) == (5, 6, BOLD)


# -- the forms with colons ----------------------------------------------------

def test_colon_colors():
    assert sgr("38:5:200", 5, 6, 0) == (200, 6, 0)
    assert sgr("48:5:17", 5, 6, 0) == (5, 17, 0)
    assert sgr("38:2:10:20:30", 5, 6, 0) == (RGB | 0x0A141E, 6, 0)
    assert sgr("48:2:10:20:30", 5, 6, 0) == (5, RGB | 0x0A141E, 0)
    # With the (empty) color space in the middle.
    assert sgr("38:2::10:20:30", 5, 6, 0) == (RGB | 0x0A141E, 6, 0)
    assert sgr("48:2:0:300:-1:7", 5, 6, 0) == (5, RGB | 0xFF0007, 0)
    assert sgr("38:5:999", 5, 6, 0) == (255, 6, 0)


def test_a_colon_color_that_makes_no_sense_changes_nothing():
    assert sgr("38:5", 5, 6, 0) == (5, 6, 0)
    assert sgr("48:5", 5, 6, 0) == (5, 6, 0)
    assert sgr("38:2:1:2", 5, 6, 0) == (5, 6, 0)
    assert sgr("48:2:1:2", 5, 6, 0) == (5, 6, 0)
    assert sgr("38:9:1", 5, 6, 0) == (5, 6, 0)
    assert sgr("48:9:1:2:3", 5, 6, 0) == (5, 6, 0)


def test_colon_forms_take_one_parameter_and_the_rest_goes_on():
    assert sgr("38:5:200;1", 5, 6, 0) == (200, 6, BOLD)
    assert sgr("1;48:2::1:2:3;3", 5, 6, 0) == \
        (5, RGB | 0x010203, BOLD | ITALIC)
    assert sgr("38:5;3", 5, 6, 0) == (5, 6, ITALIC)


def test_underline_styles():
    assert sgr("4:0", 5, 6, UNDERLINE | BOLD) == (5, 6, BOLD)
    assert sgr("4:1", 5, 6, BOLD) == (5, 6, BOLD | UNDERLINE)
    assert sgr("4:3", 5, 6, 0) == (5, 6, UNDERLINE)
    assert sgr("4:", 5, 6, 0) == (5, 6, UNDERLINE)


def test_other_parameters_with_colons_are_ignored():
    assert sgr("1:2;3", 5, 6, 0) == (5, 6, ITALIC)
    assert sgr("0:1", 5, 6, BOLD) == (5, 6, BOLD)
    assert sgr("x:1", 5, 6, BOLD) == (5, 6, BOLD)


# -- the palette --------------------------------------------------------------

def test_a_style_written_as_the_parameters_that_set_it():
    assert sgr_params(None, None, 0) == "0"
    assert sgr_params(None, None, ALL) == "0;1;2;3;4;5;7;8;9"
    for flag, code in ((BOLD, 1), (DIM, 2), (ITALIC, 3), (UNDERLINE, 4),
                       (BLINK, 5), (INVERSE, 7), (HIDDEN, 8), (STRIKE, 9)):
        assert sgr_params(None, None, flag) == f"0;{code}"
    # The eight colors, the eight bright ones, the rest of the palette.
    assert sgr_params(0, 7, 0) == "0;30;47"
    assert sgr_params(7, 0, 0) == "0;37;40"
    assert sgr_params(8, 15, 0) == "0;90;107"
    assert sgr_params(15, 8, 0) == "0;97;100"
    assert sgr_params(16, 255, 0) == "0;38;5;16;48;5;255"
    assert sgr_params(255, 16, 0) == "0;38;5;255;48;5;16"
    # Direct colors; black is one, and not the same as no color.
    assert sgr_params(RGB | 0x010203, RGB | 0xFFFEFD, BOLD) \
        == "0;1;38;2;1;2;3;48;2;255;254;253"
    assert sgr_params(RGB, None, 0) == "0;38;2;0;0;0"
    assert sgr_params(None, RGB, 0) == "0;48;2;0;0;0"


def test_a_style_written_out_and_applied_is_itself_again():
    colors = [None, 0, 7, 8, 15, 16, 255, RGB, RGB | 0x123456,
              RGB | 0xFFFFFF]
    for fg in colors:
        for bg in colors:
            for attrs in (0, BOLD, DIM | STRIKE, ALL):
                params = sgr_params(fg, bg, attrs)
                # Onto another style altogether: it sets, it does not add.
                assert apply_sgr(params, 5, RGB | 9, ITALIC) \
                    == (fg, bg, attrs), params


def test_the_256_color_palette():
    palette = palette_256()
    assert len(palette) == 256
    assert palette[:16] == [
        0x000000, 0xCD0000, 0x00CD00, 0xCDCD00, 0x0000EE, 0xCD00CD,
        0x00CDCD, 0xE5E5E5, 0x7F7F7F, 0xFF0000, 0x00FF00, 0xFFFF00,
        0x5C5CFF, 0xFF00FF, 0x00FFFF, 0xFFFFFF]
    # The color cube: six levels each of red, green and blue.
    assert palette[16] == 0x000000
    assert palette[17] == 0x00005F
    assert palette[18] == 0x000087
    assert palette[21] == 0x0000FF
    assert palette[22] == 0x005F00
    assert palette[52] == 0x5F0000
    assert palette[110] == 0x87AFD7
    assert palette[196] == 0xFF0000
    assert palette[231] == 0xFFFFFF
    # The gray ramp.
    assert palette[232] == 0x080808
    assert palette[233] == 0x121212
    assert palette[255] == 0xEEEEEE
    assert all(0 <= color <= 0xFFFFFF for color in palette)
