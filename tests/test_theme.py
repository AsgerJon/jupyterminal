"""The theme: its default colours, what the configuration may change, and
how a style becomes the colours to paint with."""

from __future__ import annotations

import tomllib

from PySide6.QtGui import QColor

from jupyterminal.config import DEFAULT_CONFIG
from jupyterminal.term import style
from jupyterminal.ui.theme import Theme

DEFAULTS = {
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

ANSI = ["#2b303b", "#e06c75", "#98c379", "#e5c07b", "#61afef", "#c678dd",
        "#56b6c2", "#c8ccd4", "#5c6370", "#ff7b86", "#b1e18b", "#f2d08a",
        "#7cc0ff", "#d7a1f0", "#74d2de", "#ffffff"]

#: Two colours whose blend, three parts to two, is easy to check by hand.
BRIGHT = "#c86432"               # 200, 100, 50
DARK = "#0a141e"                 # 10, 20, 30
BLEND = "#7c442a"                # 124, 68, 42


#: How every warning about a value that is no colour ends its advice.
HOW = 'use "#rrggbb" or a color name such as "steelblue"'


def colours(theme: Theme) -> dict[str, str]:
    return {name: getattr(theme, name).name() for name in DEFAULTS}


def named(resolved: tuple) -> tuple:
    """A resolved style with its colours by name."""
    fg, bg, attrs = resolved
    return fg.name(), bg if bg is None else bg.name(), attrs


def rgb(text: str) -> int:
    """A direct colour as a style stores it."""
    return style.RGB | int(text[1:], 16)


# -- the colours -------------------------------------------------------------

def test_the_line_between_blocks_stands_out_from_what_it_separates():
    """Clearly lighter than every background a block can have, so that
    where one block ends and the next begins is seen at a glance."""
    theme = Theme()
    line = theme.divider.lightness()
    for name in ("background", "header_background", "selected_background",
                 "selected_header", "failed_background", "failed_header"):
        assert line - getattr(theme, name).lightness() >= 30, name


def test_the_default_theme():
    for theme in (Theme(), Theme(None), Theme({})):
        assert colours(theme) == DEFAULTS
        assert theme.warnings == []
        assert [color.name() for color in theme.palette[:16]] == ANSI


def test_a_colour_of_the_theme_can_be_changed():
    theme = Theme({"background": "#102030", "accent": "red",
                   "scrollbar": "#abc", "dim": None})
    assert theme.warnings == []
    assert colours(theme) == DEFAULTS | {
        "background": "#102030", "accent": "#ff0000", "scrollbar": "#aabbcc"}


def test_a_value_that_is_no_colour_is_reported_and_the_default_kept():
    theme = Theme({"accent": "", "background": "no such colour",
                   "foreground": 5, "dim": True, "divider": ["#ffffff"],
                   "cursor": "#010203"})
    # Each warning can be acted upon: which setting, what it holds, what
    # it may hold, and what is used in the meantime.
    assert theme.warnings == [
        f'colors.background = "no such colour" is not a color: {HOW}; '
        'the default "#1a1c22" is used',
        f'colors.foreground = 5 is not a color: {HOW}; '
        'the default "#d5d8df" is used',
        f'colors.dim = true is not a color: {HOW}; '
        'the default "#7d8594" is used',
        f'colors.divider = ["#ffffff"] is not a color: {HOW}; '
        'the default "#525a6e" is used',
        f'colors.accent = "" is not a color: {HOW}; '
        'the default "#58a6ff" is used']
    assert colours(theme) == DEFAULTS | {"cursor": "#010203"}


def test_a_value_is_shown_as_the_file_writes_it():
    theme = Theme({"dim": "bl\u00e5", "accent": False, "find": 1.5})
    assert [warning.split(" is not ")[0] for warning in theme.warnings] == [
        'colors.dim = "bl\u00e5"', "colors.accent = false",
        "colors.find = 1.5"]


def test_only_the_colours_of_the_theme_are_taken_from_the_configuration():
    theme = Theme({"resolve": "#ffffff", "palette": "#000000",
                   "nonsense": "#222222"})
    assert colours(theme) == DEFAULTS
    assert not hasattr(theme, "nonsense")
    assert theme.resolve(0)[0] is theme.foreground
    assert len(theme.palette) == 256
    lists = "'jupyterminal --print-default-config' lists them"
    assert theme.warnings == [
        f"colors.resolve is not a color of the theme; {lists}",
        f"colors.palette is not a color of the theme; {lists}",
        f"colors.nonsense is not a color of the theme; {lists}"]


def test_a_misspelled_colour_is_reported_with_the_name_that_was_meant():
    theme = Theme({"backgroud": "#000000", "Cursor": "red",
                   "find-current": "#111111", "ansii": [],
                   "accent": "#222222"})
    assert theme.warnings == [
        'colors.backgroud is not a color of the theme; '
        'did you mean "background"?',
        'colors.Cursor is not a color of the theme; did you mean "cursor"?',
        'colors.find-current is not a color of the theme; '
        'did you mean "find_current"?',
        'colors.ansii is not a color of the theme; did you mean "ansi"?']
    # None of them changed anything; the one that is spelled right did.
    assert colours(theme) == DEFAULTS | {"accent": "#222222"}
    assert [color.name() for color in theme.palette[:16]] == ANSI


def test_the_documented_file_lists_every_colour_with_its_default():
    """A warning sends the reader to the documented file for the names,
    so every one of them has to be there, with the value it has."""
    section = DEFAULT_CONFIG.split("[colors]\n")[1]
    settings = [line[2:] for line in section.split("\n")
                if " = " in line or line.startswith('#         "')]
    assert tomllib.loads("\n".join(settings)) == DEFAULTS | {"ansi": ANSI}
    # Listed, not set: the file as it is changes nothing.
    assert tomllib.loads(DEFAULT_CONFIG)["colors"] == {}


# -- the palette -------------------------------------------------------------

def test_the_palette_has_the_256_colours_of_xterm():
    palette = Theme().palette
    assert len(palette) == 256
    assert all(type(color) is QColor for color in palette)
    names = [color.name() for color in palette]
    # The colour cube: six levels of red, green and blue.
    assert names[16] == "#000000" and names[231] == "#ffffff"
    assert names[21] == "#0000ff" and names[46] == "#00ff00"
    assert names[196] == "#ff0000"
    assert names[67] == "#5f87af"          # 16 + 36 * 1 + 6 * 2 + 3
    # The grey ramp.
    assert names[232] == "#080808" and names[255] == "#eeeeee"


def test_the_sixteen_named_colours_can_be_changed():
    custom = [f"#{n:02x}{n + 1:02x}{n + 2:02x}" for n in range(16)]
    theme = Theme({"ansi": custom})
    assert theme.warnings == []
    assert [color.name() for color in theme.palette[:16]] == custom
    assert theme.palette[16].name() == "#000000"
    assert len(theme.palette) == 256


def test_fewer_than_sixteen_colours_change_the_first_ones():
    theme = Theme({"ansi": ["#010101", "blue"]})
    assert theme.warnings == []
    assert [color.name() for color in theme.palette[:16]] == [
        "#010101", "#0000ff"] + ANSI[2:]


def test_more_than_sixteen_colours_are_cut_off_and_that_is_said():
    assert Theme({"ansi": ["#010101"] * 16}).warnings == []
    assert Theme({"ansi": ["#010101"] * 17}).warnings == [
        "colors.ansi has 17 entries, but only the first 16 are used"]
    theme = Theme({"ansi": ["#010101"] * 16 + ["#020202", "junk"]})
    assert theme.warnings == [
        "colors.ansi has 18 entries, but only the first 16 are used"]
    assert [color.name() for color in theme.palette[:17]] == [
        "#010101"] * 16 + ["#000000"]
    assert len(theme.palette) == 256


def test_a_bad_entry_of_the_named_colours_is_reported_and_skipped():
    theme = Theme({"ansi": ["#010101", "junk", 7, "#040404", None]})
    assert theme.warnings == [
        f'colors.ansi[1] = "junk" is not a color: {HOW}; '
        'the default "#e06c75" is used',
        f'colors.ansi[2] = 7 is not a color: {HOW}; '
        'the default "#98c379" is used',
        f'colors.ansi[4] = null is not a color: {HOW}; '
        'the default "#61afef" is used']
    assert [color.name() for color in theme.palette[:16]] == [
        "#010101", ANSI[1], ANSI[2], "#040404"] + ANSI[4:]


def test_named_colours_that_are_no_list_are_reported():
    advice = ('use up to 16 colors, as ["#rrggbb", ...]; '
              "the defaults are used")
    for value, shown in (("#010101", '"#010101"'), (3, "3"),
                         ({"0": "#010101"}, '{"0": "#010101"}'),
                         (("#010101",), '["#010101"]')):
        theme = Theme({"ansi": value})
        assert theme.warnings == [
            f"colors.ansi = {shown} is not a list: {advice}"]
        assert [color.name() for color in theme.palette[:16]] == ANSI
    assert Theme({"ansi": None}).warnings == []


# -- from a style to colours -------------------------------------------------

def test_a_colour_value_is_the_default_a_palette_entry_or_direct():
    theme = Theme()
    default = QColor("#123456")
    assert theme.color(None, default) is default
    assert theme.color(0, default) is theme.palette[0]
    assert theme.color(1, default) is theme.palette[1]
    assert theme.color(255, default) is theme.palette[255]
    assert theme.color(rgb("#a1b3c5"), default).name() == "#a1b3c5"
    assert theme.color(rgb("#000000"), default).name() == "#000000"


def test_the_default_style_is_the_foreground_on_nothing():
    theme = Theme()
    fg, bg, attrs = theme.resolve(0)
    assert fg is theme.foreground and bg is None and attrs == 0


def test_a_style_names_its_colours_and_keeps_its_attributes():
    theme = Theme()
    sid = style.intern(1, 4, style.BOLD | style.UNDERLINE)
    fg, bg, attrs = theme.resolve(sid)
    assert fg is theme.palette[1] and bg is theme.palette[4]
    assert attrs == style.BOLD | style.UNDERLINE
    sid = style.intern(rgb(BRIGHT), None, style.ITALIC)
    assert named(theme.resolve(sid)) == (BRIGHT, None, style.ITALIC)
    sid = style.intern(None, rgb(DARK), 0)
    fg, bg, attrs = theme.resolve(sid)
    assert fg is theme.foreground and bg.name() == DARK and attrs == 0


def test_a_style_is_resolved_once():
    theme = Theme()
    sid = style.intern(rgb(BRIGHT), rgb(DARK), style.DIM)
    assert theme.resolve(sid) is theme.resolve(sid)
    assert theme.resolve(0) is theme.resolve(0)


def test_inverse_swaps_the_colours():
    theme = Theme()
    sid = style.intern(1, 4, style.INVERSE)
    fg, bg, attrs = theme.resolve(sid)
    assert fg is theme.palette[4] and bg is theme.palette[1]
    assert attrs == style.INVERSE
    # Without a background of its own the text takes the theme's.
    sid = style.intern(1, None, style.INVERSE)
    fg, bg, _ = theme.resolve(sid)
    assert fg is theme.background and bg is theme.palette[1]
    sid = style.intern(None, None, style.INVERSE)
    fg, bg, _ = theme.resolve(sid)
    assert fg is theme.background and bg is theme.foreground


def test_dim_blends_the_text_three_to_two_with_what_is_behind_it():
    theme = Theme({"foreground": BRIGHT, "background": DARK})
    sid = style.intern(None, None, style.DIM)
    assert named(theme.resolve(sid)) == (BLEND, None, style.DIM)
    # Behind the text is the style's own background when it has one.
    sid = style.intern(rgb("#0a141e"), rgb("#c86432"), style.DIM)
    assert named(theme.resolve(sid)) == ("#563426", "#c86432", style.DIM)
    # The blend rounds down.
    sid = style.intern(rgb("#c96533"), None, style.DIM)
    assert named(theme.resolve(sid)) == (BLEND, None, style.DIM)


def test_dim_and_inverse_blend_the_swapped_colours():
    theme = Theme()
    attrs = style.DIM | style.INVERSE
    sid = style.intern(rgb(DARK), rgb(BRIGHT), attrs)
    assert named(theme.resolve(sid)) == (BLEND, DARK, attrs)


def test_hidden_text_has_the_colour_behind_it():
    theme = Theme()
    sid = style.intern(1, 4, style.HIDDEN)
    fg, bg, attrs = theme.resolve(sid)
    assert fg is theme.palette[4] and bg is theme.palette[4]
    assert attrs == style.HIDDEN
    sid = style.intern(1, None, style.HIDDEN)
    fg, bg, _ = theme.resolve(sid)
    assert fg is theme.background and bg is None
    attrs = style.HIDDEN | style.INVERSE
    sid = style.intern(rgb(DARK), rgb(BRIGHT), attrs)
    assert named(theme.resolve(sid)) == (DARK, DARK, attrs)


# -- answering a program that asks -------------------------------------------

def test_a_program_can_ask_for_the_colours():
    theme = Theme({"cursor": "#010203"})
    assert theme.rgb("fg") == (213, 216, 223)
    assert theme.rgb("bg") == (26, 28, 34)
    assert theme.rgb("cursor") == (1, 2, 3)
    assert theme.rgb("palette") == (43, 48, 59)
    assert theme.rgb("palette", 0) == (43, 48, 59)
    assert theme.rgb("palette", 67) == (95, 135, 175)
    assert theme.rgb("palette", 255) == (238, 238, 238)


def test_a_question_about_a_colour_that_does_not_exist_has_no_answer():
    theme = Theme()
    assert theme.rgb("palette", 256) is None
    assert theme.rgb("palette", -1) is None
    assert theme.rgb("selection") is None
    assert theme.rgb("") is None
    assert theme.rgb("fg", 999) == (213, 216, 223)
