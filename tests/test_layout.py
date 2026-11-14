"""Laying logical lines out in rows: wrapping, and the row index of a
block's output."""

from __future__ import annotations

from array import array

import pytest

from jupyterminal.term import LineStore
from jupyterminal.ui import layout
from jupyterminal.ui.layout import (OutputLayout, offset_at, position_of,
                                    row_span, simple_rows, slice_columns,
                                    slice_offset, text_rows, wrap_command,
                                    wrap_complex)

ACUTE = "\u0301"                 # a combining mark: no cell of its own
WIDE = "你好你好你"               # five characters, ten cells
MARKED = ("e" + ACUTE) * 6       # twelve characters, six cells


def store_of(*lines: str) -> LineStore:
    store = LineStore()
    for line in lines:
        store.append(line)
    return store


def built(store: LineStore, cols: int, vis=None) -> OutputLayout:
    lay = OutputLayout()
    lay.update(store, cols, vis)
    return lay


def state(lay: OutputLayout) -> tuple:
    """Everything a layout knows, with the array as a plain list."""
    acc = None if lay.acc is None else list(lay.acc)
    return (lay.cols, lay.base, lay.count, acc, lay.vis, lay.vis_count,
            lay.rows)


@pytest.fixture
def passes(monkeypatch) -> list[str]:
    """The passes a layout makes over its store, by name, as they run."""
    seen: list[str] = []
    for name in ("_build", "_extend", "_extend_filtered"):
        original = getattr(OutputLayout, name)

        def spy(self, *args, _name=name, _original=original):
            seen.append(_name)
            return _original(self, *args)

        monkeypatch.setattr(OutputLayout, name, spy)
    return seen


@pytest.fixture
def wrapped(monkeypatch) -> list[str]:
    """The lines that were wrapped character by character."""
    seen: list[str] = []
    original = layout.wrap_complex

    def spy(text: str, cols: int):
        seen.append(text)
        return original(text, cols)

    monkeypatch.setattr(layout, "wrap_complex", spy)
    return seen


class CountingDict(dict):
    """A dict that counts how it is searched."""

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.walks = 0
        self.lookups = 0

    def items(self):
        self.walks += 1
        return super().items()

    def __contains__(self, key) -> bool:
        self.lookups += 1
        return super().__contains__(key)


# -- wrapping one complex line -----------------------------------------------

def test_wide_characters_take_two_columns_and_wrap_whole():
    assert wrap_complex("你好ab", 4) == ([0, 2], [0, 2, 0, 1])
    # One cell is left on the first row; a wide character does not split.
    assert wrap_complex("a你", 2) == ([0, 1], [0, 0])
    assert wrap_complex("ab你c", 4) == ([0, 3], [0, 1, 2, 0])


def test_a_wide_character_gets_a_row_even_when_the_row_is_too_narrow():
    assert wrap_complex("你好", 1) == ([0, 1], [0, 0])


def test_a_tab_runs_to_the_next_stop():
    assert wrap_complex("a\tb", 20) == ([0], [0, 1, 8])
    assert wrap_complex("\t\tb", 20) == ([0], [0, 8, 16])
    assert wrap_complex("abcdefgh\tb", 20) == ([0], [*range(8), 8, 16])


def test_a_tab_stops_at_the_right_margin():
    assert wrap_complex("abcdefghi\tZ", 10) == ([0, 10],
                                               [*range(9), 9, 0])
    assert wrap_complex("\tx", 5) == ([0, 1], [0, 0])


def test_a_tab_at_the_right_margin_starts_a_new_row():
    assert wrap_complex("abcdefgh\tZ", 8) == ([0, 8, 9],
                                              [*range(8), 0, 0])
    assert wrap_complex("abcde\tZ", 5) == ([0, 5, 6], [0, 1, 2, 3, 4, 0, 0])
    # On a row wider than a tab stop it runs to the first stop only.
    assert wrap_complex("abcdefghij\tZ", 10) == ([0, 10],
                                                 [*range(10), 0, 8])


def test_marks_share_the_column_of_the_character_before_them():
    assert wrap_complex("e" + ACUTE + "x", 10) == ([0], [0, 0, 1])
    assert wrap_complex("ab" + ACUTE, 10) == ([0], [0, 1, 1])
    assert wrap_complex("你" + ACUTE + "x", 10) == ([0], [0, 1, 2])
    # With nothing before it a mark sits in the first column.
    assert wrap_complex(ACUTE + "a", 10) == ([0], [0, 0])


def test_a_mark_never_starts_a_row():
    assert wrap_complex("ab" + ACUTE + "c", 2) == ([0, 3], [0, 1, 1, 0])


def test_a_layout_is_computed_once_and_the_cache_is_bounded(monkeypatch):
    cache: dict = {}
    monkeypatch.setattr(layout, "_complex_cache", cache)
    first = wrap_complex("a\tb", 20)
    assert wrap_complex("a\tb", 20) is first
    assert wrap_complex("a\tb", 21) is not first      # another width
    assert list(cache) == [("a\tb", 20), ("a\tb", 21)]
    cache.update(((n, 0), ([0], [])) for n in range(4094))
    assert len(cache) == 4096
    wrap_complex("\tq", 20)                # the limit itself is allowed
    assert len(cache) == 4097
    assert wrap_complex("a\tb", 20) is first
    again = wrap_complex("\tr", 20)        # one past it: start afresh
    assert list(cache) == [("\tr", 20)] and cache[("\tr", 20)] is again
    assert wrap_complex("a\tb", 20) == first
    assert wrap_complex("a\tb", 20) is not first


# -- rows of one line --------------------------------------------------------

@pytest.mark.parametrize("length,rows", [
    (0, 1), (1, 1), (9, 1), (10, 1), (11, 2), (12, 2), (20, 2), (21, 3),
])
def test_rows_of_a_line_of_single_cells(length, rows, wrapped):
    result = simple_rows(length, 10)
    assert result == rows and type(result) is int
    assert text_rows("x" * length, 10) == rows
    assert wrapped == []                   # counted, not laid out


def test_rows_of_a_line_follow_its_cells_not_its_characters():
    assert text_rows(WIDE, 4) == 3          # five characters, ten cells
    assert text_rows(WIDE, 10) == 1
    assert text_rows(MARKED, 6) == 1        # twelve characters, six cells
    assert text_rows("a\tb", 8) == 2


# -- which characters are on a row -------------------------------------------

def test_row_span_slices_a_simple_line_by_the_width():
    text = "x" * 25
    assert [row_span(text, 10, sub, None) for sub in range(3)] == [
        (0, 10), (10, 20), (20, 25)]
    assert row_span("", 10, 0, None) == (0, 0)


def test_row_span_follows_the_breaks_of_a_complex_line():
    text = "你好你好你"
    complex_layout = wrap_complex(text, 4)
    assert complex_layout[0] == [0, 2, 4]
    assert [row_span(text, 4, sub, complex_layout) for sub in range(3)] == [
        (0, 2), (2, 4), (4, 5)]


# -- from a column to a character --------------------------------------------

def test_offset_at_in_a_simple_line():
    text = "x" * 25
    assert offset_at(text, 10, 0, 0, None) == 0
    assert offset_at(text, 10, 1, 0, None) == 10
    assert offset_at(text, 10, 1, 3, None) == 13
    assert offset_at(text, 10, 1, 9, None) == 19
    # Past the end of the last row: one past the last character.
    assert offset_at(text, 10, 2, 9, None) == 25
    # Left of the row is its first character.
    assert offset_at(text, 10, 1, -1, None) == 10
    assert offset_at(text, 10, 1, -5, None) == 10


def test_offset_at_finds_the_character_that_covers_the_column():
    text = "a你b\tc"
    complex_layout = wrap_complex(text, 20)
    assert complex_layout == ([0], [0, 1, 3, 4, 8])
    found = [offset_at(text, 20, 0, col, complex_layout)
             for col in range(10)]
    #        a  你 你 b  tab--------  c  nothing
    assert found == [0, 1, 1, 2, 3, 3, 3, 3, 4, 5]


def test_offset_at_on_a_later_row_of_a_complex_line():
    text = "你好ab"
    complex_layout = wrap_complex(text, 4)
    assert [offset_at(text, 4, 1, col, complex_layout)
            for col in (-3, 0, 1, 2, 3)] == [2, 2, 3, 4, 4]
    assert [offset_at(text, 4, 0, col, complex_layout)
            for col in (-1, 0, 1, 2, 3, 4)] == [0, 0, 0, 1, 1, 2]


def test_a_tab_that_ends_its_row_is_hit_up_to_the_margin():
    text = "ab\t"
    complex_layout = wrap_complex(text, 20)
    assert [offset_at(text, 20, 0, col, complex_layout)
            for col in (1, 2, 15, 19, 20)] == [1, 2, 2, 2, 3]


def test_a_tab_one_cell_wide_is_hit_on_that_cell_only():
    text = "abcdefg\tZ"
    complex_layout = wrap_complex(text, 20)
    assert complex_layout[1] == [0, 1, 2, 3, 4, 5, 6, 7, 8]
    assert offset_at(text, 20, 0, 7, complex_layout) == 7
    assert offset_at(text, 20, 0, 8, complex_layout) == 8
    # Even when what follows it is a mark that shares its column.
    text = "abcdefghi\t" + ACUTE
    complex_layout = wrap_complex(text, 10)
    assert complex_layout == ([0], [*range(9), 9, 9])
    assert offset_at(text, 10, 0, 9, complex_layout) == 9


def test_a_mark_is_never_what_a_column_hits():
    text = "e" + ACUTE + "x"
    complex_layout = wrap_complex(text, 10)
    assert [offset_at(text, 10, 0, col, complex_layout)
            for col in (0, 1, 2)] == [0, 2, 3]
    # A mark with nothing before it occupies no cell either.
    text = ACUTE + "ab"
    complex_layout = wrap_complex(text, 10)
    assert [offset_at(text, 10, 0, col, complex_layout)
            for col in (0, 1, 2)] == [1, 2, 3]


# -- from a character to a column --------------------------------------------

def test_position_of_in_a_simple_line():
    text = "x" * 25
    found = [position_of(text, 10, offset, None)
             for offset in (0, 3, 9, 10, 13, 20, 24, 25)]
    assert found == [(0, 0), (0, 3), (0, 9), (1, 0), (1, 3), (2, 0),
                     (2, 4), (2, 5)]
    assert position_of("", 10, 0, None) == (0, 0)


def test_the_end_of_a_line_that_fills_its_rows_stays_on_the_last_row():
    sub, col = position_of("x" * 20, 10, 20, None)
    assert (sub, col) == (1, 10)
    assert type(sub) is int and type(col) is int
    assert position_of("x" * 10, 10, 10, None) == (0, 10)
    # Inside the line the same offset is the start of the next row.
    assert position_of("x" * 25, 10, 20, None) == (2, 0)


def test_position_of_in_a_complex_line():
    text = "你好ab"
    complex_layout = wrap_complex(text, 4)
    found = [position_of(text, 4, offset, complex_layout)
             for offset in range(5)]
    assert found == [(0, 0), (0, 2), (1, 0), (1, 1), (1, 2)]
    assert position_of(text, 4, 9, complex_layout) == (1, 2)


def test_the_end_of_a_complex_line_is_after_its_last_character():
    text = "a你"
    assert position_of(text, 10, 2, wrap_complex(text, 10)) == (0, 3)
    text = "ab\t"
    assert position_of(text, 20, 3, wrap_complex(text, 20)) == (0, 3)
    text = "xe" + ACUTE
    assert position_of(text, 10, 3, wrap_complex(text, 10)) == (0, 2)
    text = "你好你好你"
    assert position_of(text, 4, 5, wrap_complex(text, 4)) == (2, 2)
    assert position_of("", 10, 0, wrap_complex("", 10)) == (0, 0)


# -- the row index of a block ------------------------------------------------

def test_a_new_layout_covers_nothing():
    assert state(OutputLayout()) == (0, 0, 0, None, None, 0, 0)


def test_when_every_line_fits_rows_are_lines_and_nothing_is_computed():
    lay = built(store_of("one", "", "three"), 10)
    assert state(lay) == (10, 0, 3, None, None, 0, 3)
    assert [lay.locate(row) for row in range(3)] == [(0, 0), (1, 0), (2, 0)]
    assert [lay.first_row(n) for n in range(3)] == [0, 1, 2]
    assert [lay.line_rows(n) for n in range(3)] == [1, 1, 1]
    assert [lay.index_of(n) for n in range(3)] == [0, 1, 2]
    assert [lay.ordinal_of(n) for n in range(3)] == [0, 1, 2]


def test_a_line_exactly_as_wide_as_the_window_still_needs_no_index():
    lay = built(store_of("x" * 10, "y"), 10)
    assert lay.acc is None and lay.rows == 2
    lay = built(store_of("x" * 11, "y"), 10)
    assert lay.acc == array("q", [2, 3]) and lay.rows == 3


def test_wrapped_lines_are_indexed_by_their_rows():
    lay = built(store_of("a" * 25, "", "b" * 10, "c" * 11), 10)
    assert state(lay) == (10, 0, 4, [3, 4, 5, 7], None, 0, 7)
    assert lay.acc.typecode == "q"
    assert [lay.locate(row) for row in range(7)] == [
        (0, 0), (0, 1), (0, 2), (1, 0), (2, 0), (3, 0), (3, 1)]
    assert [lay.first_row(n) for n in range(4)] == [0, 3, 4, 5]
    assert [lay.line_rows(n) for n in range(4)] == [3, 1, 1, 2]


def test_complex_lines_are_counted_by_their_cells(wrapped):
    store = store_of(WIDE, MARKED, "x" * 17, "\tab", "你好你好")
    assert sorted(store.complex.items()) == [(0, 10), (1, 6), (3, 10),
                                             (4, 8)]
    lay = built(store, 8)
    # Ten cells in two rows, six cells in one, a tab that fills a row,
    # and eight cells that fit exactly.
    assert state(lay) == (8, 0, 5, [2, 3, 6, 8, 9], None, 0, 9)
    # Only the lines that are too wide were laid out in full.
    assert wrapped == [WIDE, "\tab"]


def test_a_block_of_nothing_but_complex_lines(wrapped):
    store = store_of(MARKED, WIDE, "a\tb")
    lay = built(store, 8)
    assert state(lay) == (8, 0, 3, [1, 3, 5], None, 0, 5)
    assert wrapped == [WIDE, "a\tb"]


def test_lines_are_addressed_by_an_index_that_survives_dropping():
    store = store_of("a" * 25, "", "b" * 10, "c" * 11)
    lay = built(store, 10)
    store.drop_front(1)
    lay.update(store, 10, None)
    assert state(lay) == (10, 1, 3, [1, 2, 4], None, 0, 4)
    assert [lay.index_of(n) for n in range(3)] == [1, 2, 3]
    assert [lay.ordinal_of(n) for n in (1, 2, 3)] == [0, 1, 2]
    assert lay.locate(3) == (2, 1)


def test_complex_lines_are_found_after_lines_were_dropped(wrapped):
    store = store_of("gone", "x" * 9, WIDE, "\tab")
    store.drop_front(1)
    assert store.base == 1 and sorted(store.complex) == [2, 3]
    lay = built(store, 8)
    assert state(lay) == (8, 1, 3, [2, 4, 6], None, 0, 6)
    assert wrapped == [WIDE, "\tab"]
    vis = [1, 2, 3]
    lay.update(store, 8, vis)
    assert state(lay) == (8, 1, 3, [2, 4, 6], vis, 3, 6)
    assert wrapped == [WIDE, "\tab"] * 2


# -- keeping the index up to date --------------------------------------------

def test_nothing_is_done_when_nothing_changed(passes):
    store = store_of("a" * 25, "b")
    lay = built(store, 10)
    before = state(lay)
    lay.update(store, 10, None)
    assert passes == ["_build"] and state(lay) == before
    vis = [0]
    lay.update(store, 10, vis)
    before = state(lay)
    lay.update(store, 10, vis)
    assert passes == ["_build", "_build"] and state(lay) == before


def test_new_lines_extend_the_index(passes):
    store = store_of("a" * 25, "b")
    lay = built(store, 10)
    for line in ("c" * 11, "", "d" * 30):
        store.append(line)
    lay.update(store, 10, None)
    assert passes == ["_build", "_extend"]
    assert state(lay) == (10, 0, 5, [3, 4, 6, 7, 10], None, 0, 10)
    assert state(lay) == state(built(store, 10))


def test_new_complex_lines_extend_the_index(passes, wrapped):
    # Few complex lines in a long tail: the block's complex lines are
    # walked, and the one before the tail is left alone.
    store = store_of(WIDE, "b")
    lay = built(store, 8)
    for line in ("\tab", "c", "d" * 9, MARKED, "e"):
        store.append(line)
    lay.update(store, 8, None)
    assert state(lay) == (8, 0, 7, [2, 3, 5, 6, 8, 9, 10], None, 0, 10)
    # Many complex lines and a short tail: the tail is walked.
    store = store_of(WIDE, MARKED, "\tab")
    lay = built(store, 8)
    store.append(WIDE)
    store.append("f")
    lay.update(store, 8, None)
    assert state(lay) == (8, 0, 5, [2, 3, 5, 7, 8], None, 0, 8)
    assert passes == ["_build", "_extend"] * 2
    assert wrapped == [WIDE, "\tab", WIDE, "\tab", WIDE]


def test_lines_that_fit_are_only_counted(passes):
    store = store_of("one", "two")
    lay = built(store, 10)
    store.append("x" * 10)                 # exactly the width still fits
    lay.update(store, 10, None)
    assert passes == ["_build", "_extend"]
    assert state(lay) == (10, 0, 3, None, None, 0, 3)


def test_the_first_line_too_wide_builds_the_index(passes):
    store = store_of("one", "two")
    lay = built(store, 10)
    store.append("x" * 11)
    lay.update(store, 10, None)
    assert passes == ["_build", "_extend", "_build"]
    assert state(lay) == (10, 0, 3, [1, 2, 4], None, 0, 4)


def test_fewer_lines_rebuild_the_index(passes):
    store = store_of("a" * 25, "b", "c" * 11)
    lay = built(store, 10)
    del store.texts[1:]
    lay.update(store, 10, None)
    assert passes == ["_build", "_build"]
    assert state(lay) == (10, 0, 1, [3], None, 0, 3)


def test_another_width_rebuilds_the_index(passes):
    store = store_of("a" * 25, "b")
    lay = built(store, 10)
    lay.update(store, 5, None)
    assert passes == ["_build", "_build"]
    assert state(lay) == (5, 0, 2, [5, 6], None, 0, 6)
    lay.update(store, 25, None)
    assert state(lay) == (25, 0, 2, None, None, 0, 2)


def test_dropped_lines_rebuild_the_index_even_when_the_count_is_the_same(
        passes):
    store = store_of("a" * 25, "b" * 25, "c")
    lay = built(store, 10)
    store.drop_front(2)
    store.append("d")
    store.append("e")
    assert len(store) == 3
    lay.update(store, 10, None)
    assert passes == ["_build", "_build"]
    # The width of the lines that left is remembered by the store, so
    # the index stays an array; what it holds is the lines that are here.
    assert state(lay) == (10, 2, 3, [1, 2, 3], None, 0, 3)


def test_a_store_emptied_of_wide_lines_has_no_rows(passes):
    store = store_of("a" * 25)
    lay = built(store, 10)
    store.drop_front(1)
    lay.update(store, 10, None)
    assert state(lay) == (10, 1, 0, [], None, 0, 0)
    store.append("b" * 11)
    store.append("c")
    lay.update(store, 10, None)
    assert passes == ["_build", "_build", "_extend"]
    assert state(lay) == (10, 1, 2, [2, 3], None, 0, 3)


# -- a filtered block --------------------------------------------------------

def filtered_store() -> LineStore:
    return store_of("x" * 17, "ok", WIDE, MARKED, "y" * 9)


def test_a_filter_shows_some_lines(wrapped):
    store = filtered_store()
    vis = [0, 2, 3]
    lay = built(store, 8, vis)
    assert state(lay) == (8, 0, 3, [3, 5, 6], vis, 3, 6)
    assert lay.vis is vis
    assert wrapped == [WIDE]
    assert [lay.index_of(n) for n in range(3)] == [0, 2, 3]
    # A hidden line stands for the next one that is shown.
    assert [lay.ordinal_of(n) for n in range(6)] == [0, 1, 1, 2, 3, 3]
    assert [lay.locate(row) for row in range(6)] == [
        (0, 0), (0, 1), (0, 2), (1, 0), (1, 1), (2, 0)]


def test_filtered_lines_wrap_like_any_other():
    store = store_of("", "z" * 8, "z" * 9, "z" * 16, "z" * 17)
    vis = [0, 1, 2, 3, 4]
    lay = built(store, 8, vis)
    assert state(lay) == (8, 0, 5, [1, 2, 4, 6, 9], vis, 5, 9)
    assert state(lay)[3] == state(built(store, 8))[3]


def test_filtered_complex_lines_that_fit_are_not_laid_out(wrapped):
    store = store_of(WIDE, "你好你好", MARKED, "x" * 9, "a\tb")
    vis = [1, 2, 4]
    lay = built(store, 8, vis)
    # Eight cells fit exactly, six cells in twelve characters fit; the
    # tab takes the third line to nine cells.
    assert state(lay) == (8, 0, 3, [1, 2, 4], vis, 3, 4)
    assert wrapped == ["a\tb"]


def test_a_filtered_block_whose_lines_all_fit_needs_no_index():
    store = filtered_store()
    vis = [1, 3]
    lay = built(store, 16, vis)
    assert state(lay) == (16, 0, 2, [1, 2], vis, 2, 2)
    lay.update(store, 17, vis)             # the widest line of the block
    assert state(lay) == (17, 0, 2, None, vis, 2, 2)
    assert lay.index_of(1) == 3 and lay.ordinal_of(2) == 1
    assert state(built(store, 17, vis)) == state(lay)


def test_lines_that_pass_the_filter_extend_the_index(passes):
    store = filtered_store()
    vis = [0, 2]
    lay = built(store, 8, vis)
    vis.append(3)
    vis.append(4)
    lay.update(store, 8, vis)
    assert passes == ["_build", "_extend_filtered"]
    assert state(lay) == (8, 0, 4, [3, 5, 6, 8], vis, 4, 8)
    assert state(lay) == state(built(store, 8, vis))


def test_a_filter_that_showed_nothing_starts_to_show_lines(passes):
    store = filtered_store()
    vis: list[int] = []
    lay = built(store, 8, vis)
    assert state(lay) == (8, 0, 0, [], vis, 0, 0)
    vis.append(0)
    lay.update(store, 8, vis)
    assert passes == ["_build", "_extend_filtered"]
    assert state(lay) == (8, 0, 1, [3], vis, 1, 3)


def test_filtered_lines_that_fit_are_only_counted(passes):
    store = store_of("one", "two", "x" * 8)
    vis = [0]
    lay = built(store, 8, vis)
    vis.append(2)                          # exactly the width still fits
    lay.update(store, 8, vis)
    assert passes == ["_build", "_extend_filtered"]
    assert state(lay) == (8, 0, 2, None, vis, 2, 2)


def test_the_first_filtered_line_too_wide_builds_the_index(passes):
    store = store_of("one", "two")
    vis = [0, 1]
    lay = built(store, 8, vis)
    store.append("x" * 20)
    vis.append(2)
    lay.update(store, 8, vis)
    assert passes == ["_build", "_extend_filtered", "_build"]
    assert state(lay) == (8, 0, 3, [1, 2, 5], vis, 3, 5)


def test_a_filter_that_shows_fewer_lines_rebuilds_the_index(passes):
    store = filtered_store()
    vis = [0, 2, 4]
    lay = built(store, 8, vis)
    del vis[1:]
    lay.update(store, 8, vis)
    assert passes == ["_build", "_build"]
    assert state(lay) == (8, 0, 1, [3], vis, 1, 3)


def test_another_filter_rebuilds_the_index(passes):
    store = filtered_store()
    first = [0, 1]
    lay = built(store, 8, first)
    second = [1, 4]                        # as many lines, other ones
    lay.update(store, 8, second)
    assert passes == ["_build", "_build"]
    assert state(lay) == (8, 0, 2, [1, 3], second, 2, 3)
    lay.update(store, 8, None)             # and no filter at all
    assert passes == ["_build"] * 3
    assert state(lay) == (8, 0, 5, [3, 4, 6, 7, 9], None, 0, 9)


# -- what the index costs ----------------------------------------------------

def test_few_complex_lines_among_many_are_reached_through_the_block():
    store = store_of("a", WIDE, "b", "c", "d", "e")
    store.complex = counted = CountingDict(store.complex)
    lay = built(store, 8)
    assert list(lay.acc) == [1, 3, 4, 5, 6, 7]
    assert (counted.walks, counted.lookups) == (1, 0)


def test_many_complex_lines_are_looked_up_line_by_line():
    store = store_of(WIDE, "a\tb")
    store.complex = counted = CountingDict(store.complex)
    lay = built(store, 8)
    assert list(lay.acc) == [2, 4]
    # As many complex lines as lines: looking each line up is no worse.
    assert (counted.walks, counted.lookups) == (0, 2)
    store.append("c")
    lay.update(store, 8, None)
    assert list(lay.acc) == [2, 4, 5]
    assert (counted.walks, counted.lookups) == (0, 3)


# -- a command in a block header ---------------------------------------------

def test_a_short_command_is_one_row():
    assert wrap_command("ls -la", 20, 10, 5) == ([(0, 0, 6)], False)
    assert wrap_command("", 20, 10, 5) == ([(0, 0, 0)], False)


def test_the_first_row_of_a_command_is_narrower():
    assert wrap_command("abcdefghijklmno", 6, 5, 9) == (
        [(0, 0, 5), (0, 5, 11), (0, 11, 15)], False)


def test_no_row_of_a_command_is_narrower_than_four_cells():
    assert wrap_command("abcdefghi", 2, 3, 9) == (
        [(0, 0, 4), (0, 4, 8), (0, 8, 9)], False)


def test_every_line_of_a_command_starts_a_row():
    assert wrap_command("ab\n\ncdefgh", 4, 4, 9) == (
        [(0, 0, 2), (1, 0, 0), (2, 0, 4), (2, 4, 6)], False)
    # Only the very first row shares its line with the status.
    assert wrap_command("a\nbcdefghij", 8, 4, 9) == (
        [(0, 0, 1), (1, 0, 8), (1, 8, 9)], False)


def test_a_command_is_cut_off_after_the_rows_allowed():
    assert wrap_command("abcdefghijkl", 4, 4, 2) == (
        [(0, 0, 4), (0, 4, 8)], True)
    assert wrap_command("a\nb\nc", 4, 4, 2) == ([(0, 0, 1), (1, 0, 1)], True)
    # Exactly as many rows as allowed is not cut off.
    assert wrap_command("abcdefgh", 4, 4, 2) == (
        [(0, 0, 4), (0, 4, 8)], False)
    assert wrap_command("a\nb", 4, 4, 2) == ([(0, 0, 1), (1, 0, 1)], False)


def test_a_command_with_no_rows_allowed_still_has_one_empty_row():
    assert wrap_command("echo hi", 20, 10, 0) == ([(0, 0, 0)], True)


def test_a_command_with_wide_characters_wraps_by_cells():
    assert wrap_command("你好你好", 4, 4, 9) == ([(0, 0, 2), (0, 2, 4)], False)
    assert wrap_command("a你好你", 4, 4, 9) == ([(0, 0, 2), (0, 2, 4)], False)
    assert wrap_command("x\n你好a你", 5, 4, 9) == (
        [(0, 0, 1), (1, 0, 3), (1, 3, 4)], False)


def test_a_control_character_in_a_command_takes_a_cell_as_it_is_drawn():
    # A line of plain ASCII is drawn one cell per character, whatever
    # the characters are; the rows are cut the same way.
    assert slice_columns("ab\x01cd", 0, 5) is None
    assert wrap_command("ab\x01cd", 4, 4, 9) == ([(0, 0, 4), (0, 4, 5)],
                                                 False)
    assert text_rows("ab\x01cd", 4) == 2


def test_a_tab_in_a_command_runs_to_the_next_stop():
    assert wrap_command("ab\tc", 10, 10, 9) == ([(0, 0, 4)], False)
    assert wrap_command("ab\tc", 8, 8, 9) == ([(0, 0, 3), (0, 3, 4)], False)
    # A tab wider than the row still takes a row.
    assert wrap_command("\tab", 4, 4, 9) == ([(0, 0, 1), (0, 1, 3)], False)


# -- one row of a command ----------------------------------------------------

def test_slice_columns_is_none_when_columns_are_offsets():
    assert slice_columns("plain text", 0, 10) is None
    assert slice_columns("", 0, 0) is None


def test_slice_columns_lays_a_slice_out_from_the_left():
    assert slice_columns("你好a\tb", 0, 5) == [0, 2, 4, 5, 8]
    assert slice_columns("ab\tc", 0, 4) == [0, 1, 2, 8]
    # The entries before the slice only pad the list.
    assert slice_columns("a你好\tb", 1, 4) == [0, 0, 2, 4]
    assert slice_columns("a你好\tb", 2, 5) == [0, 0, 0, 2, 8]


def test_slice_columns_puts_a_mark_on_the_character_before_it():
    assert slice_columns("ae" + ACUTE, 0, 3) == [0, 1, 1]
    assert slice_columns("你e" + ACUTE + "x", 0, 4) == [0, 2, 2, 3]
    # A slice that starts with the mark has nothing to put it on.
    assert slice_columns("e" + ACUTE + "x", 1, 3) == [0, 0, 0]


def test_slice_offset_in_simple_text():
    assert [slice_offset(None, 2, 8, col) for col in (-4, 0, 3, 6, 7)] == [
        2, 2, 5, 8, 8]


def test_slice_offset_is_the_first_character_at_or_after_the_column():
    text = "a你好\tb"
    columns = slice_columns(text, 0, 5)
    assert columns == [0, 1, 3, 5, 8]
    assert [slice_offset(columns, 0, 5, col) for col in range(10)] == [
        0, 1, 2, 2, 3, 3, 4, 4, 4, 5]
    columns = slice_columns(text, 1, 4)
    assert [slice_offset(columns, 1, 4, col) for col in (0, 1, 2, 3, 4, 5)
            ] == [1, 2, 2, 3, 3, 4]
