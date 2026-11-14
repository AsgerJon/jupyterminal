"""Drawing text on the cell grid: the segments a row is cut into, the
fonts and their metrics, and what reaches the painter."""

from __future__ import annotations

import pytest
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF, QImage,
                           QPainter, QPen)

from jupyterminal.term import style
from jupyterminal.term.screen import Row
from jupyterminal.ui import render
from jupyterminal.ui.layout import wrap_complex
from jupyterminal.ui.render import (Renderer, cell_of, cells_to_offset,
                                    complex_segments, grid_segments,
                                    offset_to_cells, simple_segments)
from jupyterminal.ui.theme import Theme

pytestmark = pytest.mark.usefixtures("qapp")

ACUTE = "\u0301"                 # a combining mark: no cell of its own
RED = QColor("#ff0000")
BLACK = QColor("#000000")
#: Where the rows of these tests are painted: never at the origin, so a
#: position that forgets its offset shows.
X, Y = 40, 30


def row_of(cells, styles=None) -> Row:
    row = Row()
    row.cells = list(cells)
    row.styles = styles
    return row


# -- segments of a line of single cells --------------------------------------

RUNS = ((3, 5), (6, 0), (8, 7))  # abc in 5, def plain, gh in 7, the rest plain


def test_nothing_to_draw_gives_no_segments():
    assert simple_segments("hello", None, 3, 3) == []
    assert simple_segments("hello", None, 4, 2) == []
    assert simple_segments("abcdefghij", RUNS, 6, 6) == []


def test_unstyled_text_is_one_segment():
    assert simple_segments("hello", None, 0, 5) == [(0, "hello", 0, 5)]
    assert simple_segments("hello", (), 0, 5) == [(0, "hello", 0, 5)]
    assert simple_segments("hello", None, 1, 4) == [(0, "ell", 0, 3)]
    assert simple_segments("hello", None, 2, 5) == [(0, "llo", 0, 3)]
    assert simple_segments("hello", None, 0, 2) == [(0, "he", 0, 2)]


def test_styled_text_is_a_segment_for_each_run_of_one_style():
    assert simple_segments("abcdefghij", RUNS, 0, 10) == [
        (0, "abc", 5, 3), (3, "def", 0, 3), (6, "gh", 7, 2),
        (8, "ij", 0, 2)]


def test_a_slice_of_styled_text_starts_its_columns_at_zero():
    assert simple_segments("abcdefghij", RUNS, 4, 9) == [
        (0, "ef", 0, 2), (2, "gh", 7, 2), (4, "i", 0, 1)]
    assert simple_segments("abcdefghij", RUNS, 2, 5) == [
        (0, "c", 5, 1), (1, "de", 0, 2)]


def test_a_slice_that_meets_the_ends_of_runs_has_no_empty_segments():
    assert simple_segments("abcdefghij", RUNS, 3, 8) == [
        (0, "def", 0, 3), (3, "gh", 7, 2)]
    assert simple_segments("abcdefghij", RUNS, 0, 3) == [(0, "abc", 5, 3)]
    assert simple_segments("abcdefghij", RUNS, 0, 2) == [(0, "ab", 5, 2)]
    assert simple_segments("abcdefghij", RUNS, 8, 10) == [(0, "ij", 0, 2)]


# -- segments of a line with tabs, wide characters and marks -----------------

def segments_of(text: str, cols: int = 20, runs=None, start: int = 0,
                end: int | None = None) -> list[tuple]:
    end = len(text) if end is None else end
    columns = wrap_complex(text, cols)[1]
    return complex_segments(text, runs, start, end, columns, cols)


def test_single_cells_join_and_a_wide_character_stands_alone():
    assert segments_of("ab你cd") == [(0, "ab", 0, 2), (2, "你", 0, 2),
                                    (4, "cd", 0, 2)]
    assert segments_of("你好") == [(0, "你", 0, 2), (2, "好", 0, 2)]


def test_a_tab_is_an_empty_segment_up_to_what_follows_it():
    assert segments_of("a\tb") == [(0, "a", 0, 1), (1, "", 0, 7),
                                   (8, "b", 0, 1)]
    assert segments_of("\t\tb") == [(0, "", 0, 8), (8, "", 0, 8),
                                    (16, "b", 0, 1)]


def test_a_tab_that_ends_the_row_reaches_the_margin():
    assert segments_of("ab\t") == [(0, "ab", 0, 2), (2, "", 0, 18)]
    # On its own row after a wrap, too.
    assert segments_of("abcdefgh\tZ", cols=8, start=8, end=9) == [
        (0, "", 0, 8)]


def test_a_mark_is_drawn_with_the_character_before_it():
    assert segments_of("e" + ACUTE + "xy") == [(0, "e" + ACUTE, 0, 1),
                                               (1, "xy", 0, 2)]
    assert segments_of("a你" + ACUTE + ACUTE + "b") == [
        (0, "a", 0, 1), (1, "你" + ACUTE + ACUTE, 0, 2), (3, "b", 0, 1)]


def test_a_mark_with_nothing_before_it_is_a_segment_of_its_own():
    assert segments_of(ACUTE + "ab") == [(0, ACUTE, 0, 1), (0, "ab", 0, 2)]
    # After a tab one cell wide it shares the tab's cell.
    assert segments_of("abcdefg\t" + ACUTE) == [
        (0, "abcdefg", 0, 7), (7, "", 0, 1), (7, ACUTE, 0, 1)]


def test_a_tab_does_not_belong_to_the_character_before_it():
    assert segments_of("你\tb") == [(0, "你", 0, 2), (2, "", 0, 6),
                                   (8, "b", 0, 1)]


def test_complex_segments_follow_the_style_runs():
    runs = ((1, 3), (3, 0), (4, 9))        # a in 3, b and the wide one plain,
    assert segments_of("ab你cd", runs=runs) == [       # c in 9, d plain
        (0, "a", 3, 1), (1, "b", 0, 1), (2, "你", 0, 2), (4, "c", 9, 1),
        (5, "d", 0, 1)]
    assert segments_of("ab你cd", runs=runs, start=1, end=4) == [
        (1, "b", 0, 1), (2, "你", 0, 2), (4, "c", 9, 1)]
    assert segments_of("a\tb", runs=((2, 6),)) == [
        (0, "a", 6, 1), (1, "", 6, 7), (8, "b", 0, 1)]


def test_single_cells_of_one_style_join_and_others_do_not():
    runs = ((2, 4), (4, 5), (6, 5))
    assert segments_of("abcdef你", runs=runs) == [
        (0, "ab", 4, 2), (2, "cdef", 5, 4), (6, "你", 0, 2)]


# -- segments of a screen row ------------------------------------------------

def test_an_empty_row_has_no_segments():
    assert grid_segments(row_of([])) == []


def test_a_row_of_single_cells_is_one_segment_per_style():
    assert grid_segments(row_of("hello")) == [(0, "hello", 0, 5)]
    assert grid_segments(row_of("hello", [5, 5, 7, 7, 0])) == [
        (0, "he", 5, 2), (2, "ll", 7, 2), (4, "o", 0, 1)]


def test_a_styled_row_of_single_cells_is_grouped_in_one_pass(monkeypatch):
    passes = []
    groupby = render.groupby

    def grouping(styles):
        passes.append(styles)
        return groupby(styles)

    monkeypatch.setattr(render, "groupby", grouping)
    styles = [1, 1, 2]
    assert grid_segments(row_of("abc", styles)) == [(0, "ab", 1, 2),
                                                    (2, "c", 2, 1)]
    assert passes == [styles]


def test_a_wide_character_on_the_screen_covers_two_cells():
    assert grid_segments(row_of(["a", "b", "你", "", "c", "d"])) == [
        (0, "ab", 0, 2), (2, "你", 0, 2), (4, "cd", 0, 2)]
    assert grid_segments(row_of(["a", "你", ""])) == [(0, "a", 0, 1),
                                                     (1, "你", 0, 2)]


def test_a_tab_on_the_screen_is_an_empty_segment():
    assert grid_segments(row_of(["a", "\t", "", "", "b", "c"])) == [
        (0, "a", 0, 1), (1, "", 0, 3), (4, "bc", 0, 2)]
    # One cell wide, it has no empty cells after it.
    assert grid_segments(row_of(["a", "\t", "b"])) == [
        (0, "a", 0, 1), (1, "", 0, 1), (2, "b", 0, 1)]
    # At the right margin it has no cell at all and is not drawn.
    assert grid_segments(row_of(["a", "b\t"])) == [(0, "ab", 0, 2)]


def test_a_cell_with_a_mark_is_a_segment_of_its_own():
    assert grid_segments(row_of(["e" + ACUTE, "x", "y"])) == [
        (0, "e" + ACUTE, 0, 1), (1, "xy", 0, 2)]


def test_styles_split_the_segments_of_a_complex_row():
    row = row_of(["a", "b", "你", "", "c", "d"], [1, 2, 2, 2, 2, 2])
    assert grid_segments(row) == [(0, "a", 1, 1), (1, "b", 2, 1),
                                  (2, "你", 2, 2), (4, "cd", 2, 2)]


def test_a_mark_and_a_wide_character_do_not_cancel_out():
    # Three characters in three cells, but not one in each: the styles
    # are per cell and must not be read as per character.
    cells = ["e" + ACUTE, "你", ""]
    assert grid_segments(row_of(cells, [1, 2, 2])) == [
        (0, "e" + ACUTE, 1, 1), (1, "你", 2, 2)]
    assert grid_segments(row_of(cells)) == [(0, "e" + ACUTE, 0, 1),
                                            (1, "你", 0, 2)]


def test_a_row_that_starts_with_an_empty_cell():
    assert grid_segments(row_of(["", "a"])) == [(0, "", 0, 1),
                                                (1, "a", 0, 1)]


# -- cells and characters of a screen row ------------------------------------

CELLS = ["a", "你", "", "b"]


def test_characters_in_the_cells_before_a_column():
    assert [cells_to_offset(CELLS, col) for col in (-1, 0, 1, 2, 3, 4, 9)
            ] == [0, 0, 1, 2, 2, 3, 3]


def test_the_cell_at_which_a_character_starts():
    assert [offset_to_cells(CELLS, offset) for offset in (-2, 0, 1, 2, 3, 5)
            ] == [0, 0, 1, 3, 4, 6]
    assert offset_to_cells([], 0) == 0 and offset_to_cells([], 2) == 2


def test_an_offset_inside_a_cell_is_the_next_cell():
    assert offset_to_cells(["e" + ACUTE, "b"], 1) == 1
    assert offset_to_cells(["a", "e" + ACUTE], 2) == 2


def test_the_cell_that_holds_a_character():
    # "a", a wide character in two cells, "b": characters 0, 1 and 2.
    assert [cell_of(CELLS, offset) for offset in (0, 1, 2)] == [0, 1, 3]
    # Beyond the text: the blank cells after it, one for each character.
    assert [cell_of(CELLS, offset) for offset in (3, 4, 6)] == [4, 5, 7]
    assert cell_of([], 0) == 0 and cell_of([], 2) == 2


def test_a_mark_is_held_by_the_cell_of_its_character():
    cells = ["e" + ACUTE, "b", "o" + ACUTE + ACUTE]
    assert [cell_of(cells, offset) for offset in range(6)] == [
        0, 0, 1, 2, 2, 2]
    # ... which is where it differs from the cell a character starts at.
    assert offset_to_cells(cells, 1) == 1 and cell_of(cells, 1) == 0


def test_the_first_character_is_in_the_first_cell_whatever_the_cell_holds():
    assert offset_to_cells(["", "a"], 0) == 0
    assert offset_to_cells(["", "a"], 1) == 2


# -- fonts and metrics, with numbers the test chooses ------------------------

class Numbers:
    """What the font is said to measure: the advance of one character in
    each of its four faces, its height, and the advance of every other
    character it has."""

    def __init__(self, cell=8.0, bold=8.0, italic=8.0, both=8.0,
                 ascent=10.2, descent=3.1, glyphs=None) -> None:
        self.advances = {(False, False): cell, (True, False): bold,
                         (False, True): italic, (True, True): both}
        self.ascent = ascent
        self.descent = descent
        self.glyphs = dict(glyphs or {})
        #: The characters whose advance was asked for, in that order.
        self.measured: list[str] = []


class Metrics:
    """Stands in for ``QFontMetricsF``."""

    def __init__(self, numbers: Numbers, font) -> None:
        self.numbers = numbers
        self.advance = numbers.advances[font.bold(), font.italic()]

    def horizontalAdvance(self, text: str) -> float:
        if text.startswith("M"):
            return self.advance * len(text)
        self.numbers.measured.append(text)
        return self.numbers.glyphs[text]

    def inFont(self, ch: str) -> bool:
        return ch in self.numbers.glyphs

    def ascent(self) -> float:
        return self.numbers.ascent

    def descent(self) -> float:
        return self.numbers.descent


@pytest.fixture
def measured(monkeypatch):
    """Make the renderer read font metrics from numbers the test gives."""

    def use(**numbers) -> Numbers:
        chosen = Numbers(**numbers)
        monkeypatch.setattr(render, "QFontMetricsF",
                            lambda font: Metrics(chosen, font))
        return chosen

    return use


class StrictTheme(Theme):
    """A theme that must not be asked about the default style: text in
    it is by far the most common, and needs no lookup."""

    def resolve(self, sid: int) -> tuple:
        assert sid != 0
        return super().resolve(sid)


def test_a_cell_is_as_wide_as_the_font_advances_rounded_up(measured):
    for advance, cell in ((8.0, 8), (8.04, 8), (8.06, 9), (7.5, 8),
                          (17.0, 17), (0.01, 1)):
        measured(cell=advance)
        r = Renderer(Theme())
        assert r.cw == cell and type(r.cw) is int, advance


def test_a_row_is_as_high_as_the_font_and_one_more(measured):
    for ascent, descent, height, baseline in ((10.2, 3.1, 15, 11),
                                              (10.0, 3.0, 14, 10),
                                              (20.5, 6.5, 28, 21),
                                              (0.5, -2.0, 1, 1)):
        measured(ascent=ascent, descent=descent)
        r = Renderer(Theme())
        assert (r.ch, r.ascent) == (height, baseline), (ascent, descent)
        assert type(r.ch) is int and type(r.ascent) is int


def test_every_face_of_the_font_advances_exactly_one_cell(measured):
    measured(cell=8.0, bold=8.5, italic=7.9996, both=6.0)
    r = Renderer(Theme())
    assert r.cw == 8
    assert [(f.bold(), f.italic()) for f in r.fonts] == [
        (False, False), (True, False), (False, True), (True, True)]
    absolute = QFont.SpacingType.AbsoluteSpacing
    untouched = QFont.SpacingType.PercentageSpacing
    assert [(f.letterSpacingType(), f.letterSpacing()) for f in r.fonts] == [
        (untouched, 0.0), (absolute, -0.5), (untouched, 0.0),
        (absolute, 2.0)]
    assert r.font is r.fonts[0]
    assert isinstance(r.metrics, Metrics) and r.metrics.advance == 8.0


def test_the_font_is_the_one_asked_for_made_fit_for_a_grid(measured):
    measured()
    theme = Theme()
    r = Renderer(theme, "Courier New", 13.5)
    assert r.theme is theme
    assert (r.family, r.size) == ("Courier New", 13.5)
    for font in r.fonts:
        assert font.family() == "Courier New"
        assert font.pointSizeF() == 13.5
        assert font.styleHint() == QFont.StyleHint.Monospace
        assert font.fixedPitch() is True and font.kerning() is False


def test_without_a_family_the_font_is_the_fixed_font_of_the_system():
    r = Renderer(Theme())
    fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    assert (r.family, r.size) == ("", 11.0)
    assert r.font.family() == fixed.family()
    assert r.font.pointSizeF() == 11.0


def test_the_font_size_is_kept_within_reason():
    r = Renderer(Theme(), "", 2.0)
    assert r.size == 4.0 and r.font.pointSizeF() == 4.0
    r.set_font("", 100.0)
    assert r.size == 72.0 and r.font.pointSizeF() == 72.0
    r.set_font("", 4.0)
    assert r.size == 4.0
    r.set_font("", 72.0)
    assert r.size == 72.0


def test_a_real_font_fits_the_grid_in_every_face():
    r = Renderer(Theme())
    for font in r.fonts:
        advance = QFontMetricsF(font).horizontalAdvance("M" * 16) / 16.0
        assert abs(advance - r.cw) < 0.02
    metrics = QFontMetricsF(r.font)
    assert r.ch >= metrics.ascent() + metrics.descent() + 1.0 > r.ch - 1
    assert 0 < r.ascent < r.ch
    small = (r.cw, r.ch)
    r.set_font("", 33.0)
    assert r.cw > small[0] and r.ch > small[1]


# -- what reaches the painter ------------------------------------------------

class Recorder:
    """Stands in for the painter and writes down what it is asked."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def fillRect(self, rect, color) -> None:
        self.calls.append(("fill", rect.x(), rect.y(), rect.width(),
                           rect.height(), color))

    def setFont(self, font) -> None:
        self.calls.append(("font", font))

    def setPen(self, pen) -> None:
        if isinstance(pen, QPen):
            self.calls.append(("line pen", pen.color(), pen.widthF()))
        else:
            self.calls.append(("pen", pen))

    def drawText(self, point, text) -> None:
        self.calls.append(("text", point.x(), point.y(), text))

    def drawLine(self, start, end) -> None:
        self.calls.append(("line", start.x(), start.y(), end.x(), end.y()))


class Scene:
    """A renderer with a cell of 8 by 15 and a baseline at 11, a painter
    that records, and the calls a correct renderer makes."""

    def __init__(self, measured, **numbers) -> None:
        self.numbers = measured(**numbers)
        self.theme = StrictTheme()
        self.r = Renderer(self.theme)
        assert (self.r.cw, self.r.ch, self.r.ascent) == (8, 15, 11)
        self.p = Recorder()

    def row(self, segments, *args, **kwargs) -> list[tuple]:
        self.p.calls.clear()
        self.r.paint_row(self.p, X, Y, segments, *args, **kwargs)
        return list(self.p.calls)

    def fill(self, col: int, cells: int, color) -> tuple:
        return ("fill", X + col * 8, Y, cells * 8, 15, color)

    def font(self, index: int) -> tuple:
        return ("font", self.r.fonts[index])

    def text(self, col: int, text: str) -> tuple:
        return ("text", X + col * 8, Y + 11, text)

    def line(self, col: int, cells: int, y: float) -> tuple:
        return ("line", X + col * 8, y, X + (col + cells) * 8, y)


@pytest.fixture
def scene(measured):
    return Scene(measured)


def test_a_row_with_nothing_on_it_costs_nothing(scene):
    scene.r.theme = None                   # it is not even looked at
    assert scene.row([]) == []
    assert scene.row([], [], []) == []
    assert scene.row((), None, None) == []


def test_plain_text_is_drawn_at_its_column_on_the_baseline(scene):
    fg = scene.theme.foreground
    assert scene.row([(2, "hello", 0, 5)]) == [
        scene.font(0), ("pen", fg), scene.text(2, "hello")]
    assert scene.p.calls[1][1] is fg


def test_the_caller_can_choose_the_colour_and_the_face(scene):
    assert scene.row([(0, "x", 0, 1)], fg=RED) == [
        scene.font(0), ("pen", RED), scene.text(0, "x")]
    assert scene.p.calls[1][1] is RED
    assert scene.row([(0, "x", 0, 1)], bold=True)[0] == scene.font(1)
    assert scene.row([(0, "x", 0, 1)], italic=True)[0] == scene.font(2)
    assert scene.row([(0, "x", 0, 1)], None, None, None, True, True)[0] \
        == scene.font(3)


def test_a_style_gives_the_colours_and_the_face(scene):
    palette = scene.theme.palette
    plain = style.intern(1, 4, 0)
    assert scene.row([(2, "abc", plain, 3)]) == [
        scene.fill(2, 3, palette[4]), scene.font(0), ("pen", palette[1]),
        scene.text(2, "abc")]
    bold = style.intern(2, None, style.BOLD)
    assert scene.row([(0, "a", bold, 1)]) == [
        scene.font(1), ("pen", palette[2]), scene.text(0, "a")]
    italic = style.intern(3, None, style.ITALIC)
    assert scene.row([(0, "a", italic, 1)])[0] == scene.font(2)
    both = style.intern(3, None, style.BOLD | style.ITALIC)
    assert scene.row([(0, "a", both, 1)])[0] == scene.font(3)
    # The colour the caller chose is for unstyled text only.
    assert scene.row([(0, "a", bold, 1)], fg=RED)[1] == ("pen", palette[2])


def test_backgrounds_come_first_and_cover_blank_text_too(scene):
    palette = scene.theme.palette
    on_blue = style.intern(1, 4, 0)
    on_green = style.intern(None, 2, 0)
    assert scene.row([(0, "ab", on_blue, 2), (2, "   ", on_green, 3),
                      (5, "", on_blue, 4), (9, "c", 0, 1)]) == [
        scene.fill(0, 2, palette[4]), scene.fill(2, 3, palette[2]),
        scene.fill(5, 4, palette[4]),
        scene.font(0), ("pen", palette[1]), scene.text(0, "ab"),
        ("pen", scene.theme.foreground), scene.text(9, "c")]


def test_blank_and_empty_text_is_not_drawn(scene):
    assert scene.row([(0, "", 0, 4), (4, "  ", 0, 2), (6, "\t", 0, 1)]) == []


def test_the_font_and_the_pen_are_set_only_when_they_change(scene):
    palette = scene.theme.palette
    fg = scene.theme.foreground
    bold = style.intern(1, None, style.BOLD)
    assert scene.row([(0, "ab", 0, 2), (2, "cd", 0, 2)]) == [
        scene.font(0), ("pen", fg), scene.text(0, "ab"),
        scene.text(2, "cd")]
    assert scene.row([(0, "ab", bold, 2), (2, "cd", 0, 2),
                      (4, "ef", bold, 2), (6, "gh", bold, 2)]) == [
        scene.font(1), ("pen", palette[1]), scene.text(0, "ab"),
        scene.font(0), ("pen", fg), scene.text(2, "cd"),
        scene.font(1), ("pen", palette[1]), scene.text(4, "ef"),
        scene.text(6, "gh")]


def test_underline_and_strike_are_lines_across_the_cells(scene):
    palette = scene.theme.palette
    under = style.intern(1, None, style.UNDERLINE)
    struck = style.intern(2, None, style.STRIKE)
    both = style.intern(3, None, style.UNDERLINE | style.STRIKE)
    assert scene.row([(1, "abc", under, 3)]) == [
        scene.font(0), ("pen", palette[1]), scene.text(1, "abc"),
        ("line pen", palette[1], 1.0), scene.line(1, 3, Y + 11 + 2.5)]
    assert scene.row([(2, "ab", struck, 2)]) == [
        scene.font(0), ("pen", palette[2]), scene.text(2, "ab"),
        ("line pen", palette[2], 1.0), scene.line(2, 2, Y + 15 * 0.55)]
    assert scene.row([(0, "a", both, 1)])[3:] == [
        ("line pen", palette[3], 1.0), scene.line(0, 1, Y + 11 + 2.5),
        scene.line(0, 1, Y + 15 * 0.55)]


def test_blank_cells_are_underlined_too(scene):
    under = style.intern(1, None, style.UNDERLINE)
    assert scene.row([(3, "", under, 5)]) == [
        ("line pen", scene.theme.palette[1], 1.0),
        scene.line(3, 5, Y + 11 + 2.5)]


def test_text_after_an_underline_gets_its_pen_back(scene):
    colour = scene.theme.palette[1]
    under = style.intern(1, None, style.UNDERLINE)
    same = style.intern(1, None, 0)
    assert scene.row([(0, "ab", under, 2), (2, "cd", same, 2)]) == [
        scene.font(0), ("pen", colour), scene.text(0, "ab"),
        ("line pen", colour, 1.0), scene.line(0, 2, Y + 11 + 2.5),
        ("pen", colour), scene.text(2, "cd")]


def test_highlights_are_bands_behind_the_text(scene):
    blue = style.intern(None, 4, 0)
    green, grey = QColor("#00ff00"), QColor("#808080")
    assert scene.row([(0, "ab", blue, 2)],
                     [(1, 3, green), (5, 5, RED), (7, 4, RED),
                      (6, 9, grey)]) == [
        scene.fill(0, 2, scene.theme.palette[4]),
        scene.fill(1, 2, green), scene.fill(6, 3, grey),
        scene.font(0), ("pen", scene.theme.foreground),
        scene.text(0, "ab")]
    # A row with nothing on it but a highlight is painted as well.
    assert scene.row([], [(2, 4, green)]) == [scene.fill(2, 2, green)]


def test_an_overlay_repaints_the_text_under_it(scene):
    fg = scene.theme.foreground
    assert scene.row([(0, "abcdef", 0, 6)], None, [(2, 4, RED, BLACK)]) == [
        scene.font(0), ("pen", fg), scene.text(0, "abcdef"),
        scene.fill(2, 2, RED), ("pen", BLACK), scene.font(0),
        scene.text(2, "cd")]
    assert scene.row([(3, "abcdef", 0, 6)], None,
                     [(0, 5, RED, BLACK)])[3:] == [
        scene.fill(0, 5, RED), ("pen", BLACK), scene.font(0),
        scene.text(3, "ab")]


def test_an_overlay_on_an_empty_row_is_just_its_band(scene):
    assert scene.row([], None, [(4, 5, RED, BLACK)]) == [
        scene.fill(4, 1, RED), ("pen", BLACK)]


def test_an_overlay_without_width_is_passed_over(scene):
    assert scene.row([(0, "ab", 0, 2)], None,
                     [(1, 1, RED, BLACK), (2, 0, RED, BLACK),
                      (0, 1, BLACK, RED)])[3:] == [
        scene.fill(0, 1, BLACK), ("pen", RED), scene.font(0),
        scene.text(0, "a")]


def test_an_overlay_takes_only_the_segments_it_touches(scene):
    segments = [(0, "ab", 0, 2), (2, "cd", 0, 2), (4, "", 0, 2),
                (6, "ef", 0, 2), (8, "gh", 0, 2)]
    assert scene.row(segments, None, [(2, 4, RED, BLACK)])[6:] == [
        scene.fill(2, 2, RED), ("pen", BLACK), scene.font(0),
        scene.text(2, "cd")]
    assert scene.row(segments, None, [(1, 7, RED, BLACK)])[6:] == [
        scene.fill(1, 6, RED), ("pen", BLACK),
        scene.font(0), scene.text(1, "b"),
        scene.font(0), scene.text(2, "cd"),
        scene.font(0), scene.text(6, "e")]


def test_an_overlay_keeps_the_face_of_the_text(scene):
    bold = style.intern(1, None, style.BOLD)
    italic = style.intern(1, None, style.ITALIC)
    segments = [(0, "a", bold, 1), (1, "b", italic, 1), (2, "c", 0, 1)]
    assert scene.row(segments, None, [(0, 3, RED, BLACK)])[8:] == [
        scene.fill(0, 3, RED), ("pen", BLACK),
        scene.font(1), scene.text(0, "a"),
        scene.font(2), scene.text(1, "b"),
        scene.font(0), scene.text(2, "c")]
    plain = [(0, "a", 0, 1)]
    assert scene.row(plain, None, [(0, 1, RED, BLACK)], None, True)[5] \
        == scene.font(1)
    assert scene.row(plain, None, [(0, 1, RED, BLACK)], None, False,
                     True)[5] == scene.font(2)


def test_an_overlay_repaints_a_wide_character_from_its_first_cell(scene):
    scene.numbers.glyphs["你"] = 16.0
    wide = [(2, "你", 0, 2)]
    assert scene.row(wide, None, [(2, 4, RED, BLACK)])[3:] == [
        scene.fill(2, 2, RED), ("pen", BLACK), scene.font(0),
        scene.text(2, "你")]
    assert scene.row(wide, None, [(1, 3, RED, BLACK)])[3:] == [
        scene.fill(1, 2, RED), ("pen", BLACK), scene.font(0),
        scene.text(2, "你")]
    # From its second cell on, the character is left as it is.
    assert scene.row(wide, None, [(3, 5, RED, BLACK)])[3:] == [
        scene.fill(3, 2, RED), ("pen", BLACK), scene.font(0)]


def test_interface_text_is_drawn_from_its_top_left_corner(scene):
    scene.p.calls.clear()
    scene.r.paint_text(scene.p, X, Y, "status", RED)
    scene.r.paint_text(scene.p, X, Y, "status", BLACK, True)
    scene.r.paint_text(scene.p, X, Y, "status", BLACK, italic=True)
    scene.r.paint_text(scene.p, X, Y, "status", BLACK, True, True)
    scene.r.paint_text(scene.p, X, Y, "", RED)
    assert scene.p.calls == [
        scene.font(0), ("pen", RED), ("text", X, Y + 11, "status"),
        scene.font(1), ("pen", BLACK), ("text", X, Y + 11, "status"),
        scene.font(2), ("pen", BLACK), ("text", X, Y + 11, "status"),
        scene.font(3), ("pen", BLACK), ("text", X, Y + 11, "status")]


# -- text that may not land on the grid by itself ----------------------------

def drawn(scene: Scene, text: str) -> list[tuple]:
    """The calls that draw ``text`` as interface text, without the font
    and the pen."""
    scene.p.calls.clear()
    scene.r.paint_text(scene.p, X, Y, text, RED)
    return scene.p.calls[2:]


def test_ascii_text_is_drawn_in_one_go_without_a_look_at_the_font(scene):
    scene.r._is_safe = None                # calling it would fail
    assert drawn(scene, "plain ascii") == [("text", X, Y + 11, "plain ascii")]
    assert scene.numbers.measured == []


def test_text_whose_characters_fill_one_cell_each_is_drawn_in_one_go(
        measured):
    scene = Scene(measured, glyphs={"é": 8.0, "─": 8.019, "│": 7.981})
    assert drawn(scene, "aé─│b") == [("text", X, Y + 11, "aé─│b")]
    # Each of them was measured, the ASCII characters were not.
    assert sorted(scene.numbers.measured) == ["é", "─", "│"]
    assert drawn(scene, "│é") == [("text", X, Y + 11, "│é")]
    assert len(scene.numbers.measured) == 3


def test_text_with_a_character_of_another_width_is_drawn_cell_by_cell(
        measured):
    scene = Scene(measured, glyphs={"你": 16.0, "é": 8.0})
    assert drawn(scene, "a你bé") == [
        ("text", X, Y + 11, "a"), ("text", X + 8, Y + 11, "你"),
        ("text", X + 24, Y + 11, "b"), ("text", X + 32, Y + 11, "é")]
    assert sorted(scene.numbers.measured) == ["é", "你"]
    # What is known about a character is not asked again.
    assert len(drawn(scene, "你你")) == 2
    assert len(scene.numbers.measured) == 2


def test_a_character_a_little_off_the_cell_is_drawn_cell_by_cell(measured):
    scene = Scene(measured, glyphs={"╴": 8.021, "╶": 7.979})
    assert drawn(scene, "╴╴") == [("text", X, Y + 11, "╴"),
                                  ("text", X + 8, Y + 11, "╴")]
    assert drawn(scene, "╶╶") == [("text", X, Y + 11, "╶"),
                                  ("text", X + 8, Y + 11, "╶")]


def test_a_character_the_font_lacks_is_drawn_cell_by_cell(scene):
    assert drawn(scene, "жж") == [("text", X, Y + 11, "ж"),
                                  ("text", X + 8, Y + 11, "ж")]
    assert scene.numbers.measured == []    # nothing to measure it in


def test_marks_are_drawn_with_the_character_they_sit_on(scene):
    assert drawn(scene, "e" + ACUTE + "x") == [
        ("text", X, Y + 11, "e" + ACUTE), ("text", X + 8, Y + 11, "x")]
    assert drawn(scene, "e" + ACUTE + ACUTE + "x" + ACUTE) == [
        ("text", X, Y + 11, "e" + ACUTE + ACUTE),
        ("text", X + 8, Y + 11, "x" + ACUTE)]
    # A mark with nothing to sit on takes a cell of its own.
    assert drawn(scene, ACUTE + "a") == [("text", X, Y + 11, ACUTE),
                                         ("text", X + 8, Y + 11, "a")]


def test_another_font_measures_the_characters_again(measured):
    scene = Scene(measured, glyphs={"é": 8.0})
    assert len(drawn(scene, "éé")) == 1
    scene.numbers.glyphs["é"] = 12.0
    scene.r.set_font("", 11.0)
    assert len(drawn(scene, "éé")) == 2


def test_text_in_a_row_takes_the_same_care(scene):
    segments = [(1, "e" + ACUTE, 0, 1), (2, "你", 0, 2)]
    assert scene.row(segments)[2:] == [
        ("text", X + 8, Y + 11, "e" + ACUTE), ("text", X + 16, Y + 11, "你")]
    # A cell that holds more than one character is repainted whole.
    assert scene.row(segments, None, [(1, 2, RED, BLACK)])[4:] == [
        scene.fill(1, 1, RED), ("pen", BLACK), scene.font(0),
        ("text", X + 8, Y + 11, "e" + ACUTE)]


# -- the picture a real painter makes of it ----------------------------------

MARGIN = 12


class Canvas:
    """One row of twelve cells painted for real, on black."""

    def __init__(self) -> None:
        self.theme = Theme({"background": "#000000"})
        self.r = Renderer(self.theme)
        self.image = QImage(12 * self.r.cw + 2 * MARGIN,
                            self.r.ch + 2 * MARGIN,
                            QImage.Format.Format_RGB32)
        self.image.fill(BLACK)

    def paint(self, segments, highlights=None, overlays=None) -> None:
        painter = QPainter(self.image)
        self.r.paint_row(painter, MARGIN, MARGIN, segments, highlights,
                         overlays)
        painter.end()

    def colour(self, col: int, dx: int, dy: int) -> str:
        """The pixel ``(dx, dy)`` from the top left corner of a cell."""
        return self.image.pixelColor(MARGIN + col * self.r.cw + dx,
                                     MARGIN + dy).name()

    def ink(self) -> list[tuple[int, int, str]]:
        """Every pixel that is not black: ``(x, y, colour)`` counted
        from the top left corner of the row."""
        image = self.image
        return [(x - MARGIN, y - MARGIN, image.pixelColor(x, y).name())
                for y in range(image.height()) for x in range(image.width())
                if image.pixelColor(x, y) != BLACK]


def test_a_background_fills_exactly_its_cells():
    canvas = Canvas()
    cw, ch = canvas.r.cw, canvas.r.ch
    canvas.paint([(2, "   ", style.intern(None, style.RGB | 0x0000FF, 0), 3)])
    xs = {x for x, _, _ in canvas.ink()}
    ys = {y for _, y, _ in canvas.ink()}
    assert xs == set(range(2 * cw, 5 * cw)) and ys == set(range(ch))
    assert {colour for _, _, colour in canvas.ink()} == {"#0000ff"}


def test_bands_are_painted_in_their_order():
    canvas = Canvas()
    last = canvas.r.ch - 1
    canvas.paint([(0, "  ", style.intern(None, style.RGB | 0x0000FF, 0), 2)],
                 [(1, 4, QColor("#00ff00"))],
                 [(3, 6, QColor("#ff00ff"), BLACK)])
    assert [canvas.colour(col, 0, last) for col in range(7)] == [
        "#0000ff", "#00ff00", "#00ff00", "#ff00ff", "#ff00ff", "#ff00ff",
        "#000000"]


def test_text_is_inked_in_its_own_colour_inside_its_cells():
    canvas = Canvas()
    cw, ch = canvas.r.cw, canvas.r.ch
    canvas.paint([(3, "HH", style.intern(style.RGB | 0xFF0000, None, 0), 2)])
    ink = canvas.ink()
    assert ink and all(colour[3:] == "0000" for _, _, colour in ink)
    xs = [x for x, _, _ in ink]
    ys = [y for _, y, _ in ink]
    assert 3 * cw - 1 <= min(xs) and max(xs) <= 5 * cw
    assert 0 <= min(ys) and max(ys) < ch
    # Both letters are there, one in each cell.
    assert any(x < 4 * cw - 1 for x in xs) and any(x > 4 * cw for x in xs)


def test_an_overlay_inks_its_text_in_its_own_colour():
    canvas = Canvas()
    cw = canvas.r.cw
    canvas.paint([(0, "HHHH", 0, 4)], None,
                 [(1, 3, QColor("#0000ff"), QColor("#ff0000"))])
    inside = [colour for x, _, colour in canvas.ink()
              if cw < x < 3 * cw - 1]
    # Blue band, red letters, and nothing of the grey they had before.
    assert "#0000ff" in inside
    assert any(colour != "#0000ff" for colour in inside)
    assert all(colour[3:5] == "00" for colour in inside)


def test_the_underline_and_the_strike_are_one_pixel_lines():
    for attrs, where in ((style.UNDERLINE, "under"),
                         (style.STRIKE, "through")):
        canvas = Canvas()
        cw, ch, ascent = canvas.r.cw, canvas.r.ch, canvas.r.ascent
        canvas.paint([(1, "   ", style.intern(style.RGB | 0xFF0000, None,
                                             attrs), 3)])
        ink = canvas.ink()
        assert {colour for _, _, colour in ink} == {"#ff0000"}
        rows = {y for _, y, _ in ink}
        assert len(rows) == 1, where
        row = rows.pop()
        middle = ascent + 2.5 if where == "under" else ch * 0.55
        assert middle - 1 <= row <= middle + 1, where
        xs = {x for x, _, _ in ink}
        assert set(range(cw + 1, 4 * cw - 1)) <= xs
        assert min(xs) >= cw - 1 and max(xs) <= 4 * cw + 1
