"""The screen model: moving the cursor, scrolling, inserting and deleting
rows, freezing rows into the store, and what a resize keeps."""

from __future__ import annotations

import pytest

from jupyterminal.term import LineStore, Screen, Terminal, style
from jupyterminal.term import terminal as tmod


def make(cols: int = 20, rows: int = 5):
    store = LineStore()
    screen = Screen(cols, rows, store)
    return Terminal(screen), screen, store


def feed(term: Terminal, text: str) -> None:
    term.feed(text.encode())


def texts(screen: Screen) -> list[str]:
    """The text of every row the grid holds."""
    return [row.text() for row in screen.grid]


def lines(screen: Screen) -> list[str]:
    return [text for text, _ in screen.all_lines()]


# -- moving the cursor --------------------------------------------------------

def test_without_a_count_the_cursor_moves_one_column():
    term, screen, _ = make()
    feed(term, "abcd\x1b[D")
    assert screen.cx == 3
    feed(term, "\x1b[C")
    assert screen.cx == 4


def test_a_count_below_one_moves_the_cursor_by_one():
    term, screen, _ = make(cols=10, rows=6)
    feed(term, "\x1b[3;5H")
    screen.cursor_up(0)
    assert (screen.cy, screen.cx) == (1, 4)
    screen.cursor_down(-2)
    assert (screen.cy, screen.cx) == (2, 4)
    screen.cursor_forward(0)
    assert (screen.cy, screen.cx) == (2, 5)
    screen.cursor_back(-2)
    assert (screen.cy, screen.cx) == (2, 4)


def test_the_cursor_does_not_leave_the_scroll_region_from_its_edge_rows():
    term, screen, _ = make(rows=8)
    feed(term, "\x1b[3;6r\x1b[3;1H\x1b[A")      # on its first row, and up
    assert screen.cy == 2
    feed(term, "\x1b[6;1H\x1b[B")               # on its last row, and down
    assert screen.cy == 5


def test_the_cursor_can_be_sent_to_the_first_column():
    term, screen, _ = make()
    feed(term, "abc\x1b[GX")
    assert texts(screen) == ["Xbc"]
    assert screen.cx == 1


def test_a_position_off_the_screen_is_brought_to_its_nearest_edge():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "ab\r\ncd")
    screen.cursor_to(-3, -3)
    assert (screen.cy, screen.cx) == (0, 0)
    screen.cursor_to(9, 99)
    assert (screen.cy, screen.cx) == (3, 9)
    screen.cursor_col(-3)
    assert (screen.cy, screen.cx) == (3, 0)


@pytest.mark.parametrize("sequence, rows", [
    ("\n", ["", "0123456789", "         X"]),
    ("\x1bM", ["         X", "0123456789"]),
    ("\x1b[L", ["", "X", "0123456789"]),
    ("\x1b[M", ["", "X"]),
], ids=["linefeed", "reverse index", "insert line", "delete line"])
def test_leaving_the_row_or_shifting_rows_cancels_a_pending_wrap(
        sequence, rows):
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "\r\n0123456789" + sequence)
    assert not screen.wrap_pending
    feed(term, "X")
    assert texts(screen) == rows


# -- inserting and deleting rows ----------------------------------------------

def test_a_count_below_one_inserts_or_deletes_one_row():
    term, screen, _ = make(rows=4)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[2;1H")
    screen.insert_lines(0)
    assert texts(screen) == ["a", "", "b", "c"]
    screen.delete_lines(-1)
    assert texts(screen) == ["a", "b", "c", ""]


@pytest.mark.parametrize("sequence", ["\x1b[L", "\x1b[M"],
                         ids=["insert line", "delete line"])
def test_inserting_or_deleting_on_the_last_row_of_the_region_blanks_it(
        sequence):
    term, screen, _ = make(rows=5)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\x1b[2;4r\x1b[4;2H" + sequence)
    assert texts(screen) == ["a", "b", "c", "", "e"]
    assert screen.cx == 0


def test_deleting_a_row_of_a_screen_that_is_not_full_adds_no_row():
    term, screen, _ = make(rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[2;1H\x1b[M")
    assert texts(screen) == ["a", "c", "d"]


def test_deleting_a_row_of_a_full_screen_adds_one_in_the_erase_color():
    term, screen, _ = make(cols=4, rows=3)
    blue = style.intern(None, 4, 0)
    feed(term, "a\r\nb\r\nc\x1b[H\x1b[44m\x1b[M")
    assert texts(screen) == ["b", "c", "    "]
    assert screen.grid[2].styles == [blue] * 4


# -- scrolling ----------------------------------------------------------------

def test_a_count_below_one_scrolls_by_one_row():
    term, screen, store = make(rows=4)
    feed(term, "a\r\nb\r\nc\r\nd")
    screen.scroll_up(0)
    assert store.texts == ["a"] and texts(screen) == ["b", "c", "d", ""]
    screen.scroll_down(-1)
    assert texts(screen) == ["", "b", "c", "d"]


def test_a_full_screen_scrolls_into_a_new_row_of_the_erase_color():
    term, screen, store = make(cols=4, rows=3)
    blue = style.intern(None, 4, 0)
    feed(term, "a\r\nb\r\nc\x1b[44m\n")
    assert store.texts == ["a"]
    assert texts(screen) == ["b", "c", "    "]
    assert screen.grid[2].styles == [blue] * 4


def test_scrolling_up_a_screen_that_is_not_full_adds_no_row():
    term, screen, store = make(rows=5)
    feed(term, "a\r\nb\r\nc\x1b[H\x1b[S")
    assert store.texts == ["a"]
    assert texts(screen) == ["b", "c"]
    feed(term, "\x1b[9S")                       # all of it: no blank lines
    assert store.texts == ["a", "b", "c"]
    assert texts(screen) == [""]


def test_scrolling_down_a_region_not_yet_written_adds_its_first_row():
    term, screen, _ = make(cols=4, rows=6)
    blue = style.intern(None, 4, 0)
    feed(term, "x\x1b[3;5r\x1b[44m\x1b[T")
    # The grid grows as far as the region, whose first row is the new one.
    assert texts(screen) == ["x", "", "    "]
    assert [row.styles for row in screen.grid] == [None, None, [blue] * 4]


# -- the saved cursor and resets ----------------------------------------------

def test_restoring_the_cursor_brings_back_a_row_that_was_erased():
    term, screen, _ = make(rows=6)
    feed(term, "\x1b[5;3H\x1b7\x1b[H\x1b[J")
    assert texts(screen) == [""]
    feed(term, "\x1b8X")
    assert texts(screen) == ["", "", "", "", "  X"]
    assert (screen.cy, screen.cx) == (4, 3)


def test_a_soft_reset_puts_both_character_sets_back():
    term, screen, _ = make()
    feed(term, "\x1b)0\x0eq")                   # the second set draws lines
    feed(term, "\x1b[!p\x1b(0q")                # the first is in use again
    feed(term, "\x1b(B\x0eq")                   # and the second is plain
    assert texts(screen) == ["──q"]


# -- reading the screen -------------------------------------------------------

def test_a_blank_row_below_the_output_is_no_output_with_the_cursor_above():
    term, screen, _ = make()
    feed(term, "a\r\nb\r\n\x1b[H")
    assert texts(screen) == ["a", "b", ""]
    assert (screen.extent(), screen.content_rows()) == (2, 2)


def test_a_plain_stretch_between_two_styled_rows_of_a_line_stays_plain():
    term, screen, _ = make(cols=5, rows=4)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    feed(term, "\x1b[31mab\x1b[0mcde\x1b[32mfg\x1b[0m")
    assert screen.all_lines() == [
        ("abcdefg", ((2, red), (5, 0), (7, green)))]


def test_the_style_runs_of_a_line_on_the_screen_count_from_its_start():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    feed(term, "a\r\n\x1b[31mb\x1b[0m")
    assert screen.all_lines() == [("a", None), ("b", ((1, red),))]


def test_the_first_rows_of_a_long_line_read_as_one_piece_of_it():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcdefghijkl")
    assert screen.live_lines(2) == [("abcdefghij", None)]


def test_frozen_lines_keep_their_own_style_runs():
    term, screen, store = make(rows=2)
    red = style.intern(1, None, 0)
    feed(term, "a\r\n\x1b[31mb\x1b[0m\r\nc\r\nd")
    assert store.texts == ["a", "b"]
    assert screen.all_lines() == [("a", None), ("b", ((1, red),)),
                                  ("c", None), ("d", None)]


# -- freezing -----------------------------------------------------------------

def test_finishing_closes_a_line_of_which_only_the_start_was_kept():
    term, screen, store = make(cols=5, rows=2)
    feed(term, "abcdefghijkl\x1b[2J\x1b[H")
    assert store.texts == [] and store.open_parts == ["abcde"]
    screen.finish()
    assert store.texts == ["abcde"] and store.open_parts is None


def test_clearing_the_store_forgets_the_text_and_styles_of_an_open_line():
    term, screen, store = make(cols=5, rows=2)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mabcde\x1b[0mfghijkl")
    assert (store.open_text(), store.open_line_runs()) == \
        ("abcde", ((5, red),))
    feed(term, "\x1b[3J")
    assert (store.open_text(), store.open_line_runs()) == ("", None)


def test_with_nothing_above_the_cursor_a_line_cut_off_at_the_top_stays_open():
    term, screen, store = make(cols=5, rows=2)
    feed(term, "abcdefghijkl\x1b[H")
    screen.flush_above_cursor()
    assert store.texts == [] and store.open_parts == ["abcde"]
    assert lines(screen) == ["abcdefghijkl"]


def test_freezing_above_the_cursor_ends_a_line_that_wraps_onto_its_row():
    term, screen, store = make(cols=5, rows=4)
    feed(term, "abcdefgh")
    screen.flush_above_cursor()
    assert store.texts == ["abcde"] and store.open_parts is None
    assert texts(screen) == ["fgh"]
    assert lines(screen) == ["abcde", "fgh"]


def test_a_cursor_saved_below_the_frozen_rows_moves_up_with_them():
    term, screen, store = make(rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\x1b7\x1b[3;1H")
    screen.flush_above_cursor()
    assert store.texts == ["a", "b"]
    feed(term, "\x1b8X")
    assert texts(screen) == ["c", "d", "eX"]


def test_a_cursor_saved_above_the_frozen_rows_is_held_on_the_top_row():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "\x1b[1;5H\x1b7\r\nx\r\nabcde")
    screen.flush_above_cursor()
    # That is where the cursor is now, so a resize takes the saved one
    # along with it, and with the wrap that is pending there.
    screen.resize(5, 6)
    feed(term, "\x1b[H\x1b8X")
    assert texts(screen) == ["abcde", "X"]


# -- resizing -----------------------------------------------------------------

def test_a_screen_that_clips_drops_a_pending_wrap_when_it_gets_narrower():
    screen = Screen(10, 3, LineStore(), reflow=False)
    term = Terminal(screen)
    feed(term, "0123456789")
    screen.resize(5, 3)
    assert (screen.cx, screen.wrap_pending) == (4, False)
    feed(term, "X")
    assert texts(screen) == ["0123X"]


def test_rows_dropped_from_the_top_leave_the_cursor_on_its_row_of_text():
    term, screen, store = make(rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\r\nf\x1b[5;1H")
    screen.resize(20, 4)
    assert store.texts == ["a", "b"]
    assert screen.cy == 2
    feed(term, "X")
    assert texts(screen) == ["c", "d", "X", "f"]


def test_a_cursor_saved_where_the_cursor_is_keeps_its_pending_wrap():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcde\x1b7")
    screen.resize(5, 6)
    feed(term, "\x1b[H\x1b8X")
    assert texts(screen) == ["abcde", "X"]
    assert screen.all_lines() == [("abcdeX", None)]


def test_a_cursor_saved_where_the_cursor_is_follows_it_through_a_rewrap():
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "abcdefghijkl\x1b7")
    screen.resize(5, 6)
    assert (screen.cy, screen.cx) == (2, 2)
    feed(term, "\x1b[H\x1b8X")
    assert texts(screen) == ["abcde", "fghij", "klX"]
    assert screen.all_lines() == [("abcdefghijklX", None)]


def test_a_cursor_saved_elsewhere_keeps_its_row_when_no_row_is_dropped():
    term, screen, _ = make(rows=6)
    feed(term, "a\r\nb\r\nc\x1b[2;2H\x1b7\x1b[3;1H")
    screen.resize(20, 8)
    feed(term, "\x1b8X")
    assert texts(screen) == ["a", "bX", "c"]


def test_a_saved_cursor_pushed_up_by_a_smaller_screen_stays_up_there():
    term, screen, _ = make(rows=6)
    feed(term, "\x1b[6;3H\x1b7\x1b[H")
    screen.resize(20, 3)
    screen.resize(20, 6)
    feed(term, "\x1b8X")
    assert texts(screen) == ["", "", "  X"]


def test_a_cursor_saved_elsewhere_loses_its_pending_wrap_in_a_resize():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcde\x1b7\r\n")
    screen.resize(5, 6)
    feed(term, "\x1b8X")
    assert texts(screen) == ["abcdX", ""]
    assert screen.all_lines() == [("abcdX", None)]


# -- re-wrapping --------------------------------------------------------------

def test_rewrapping_plain_text_leaves_it_without_styles():
    term, screen, store = make(cols=10, rows=3)
    feed(term, "y" * 45)
    assert len(store.open_parts) == 2           # two rows have scrolled off
    screen.resize(15, 3)
    assert texts(screen) == ["y" * 15] * 3
    assert [row.styles for row in screen.grid] == [None] * 3
    assert screen.all_lines() == [("y" * 45, None)]


def test_rewrapping_finds_the_cursor_again_behind_wide_characters():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "日本abc\x1b[1;5H")              # on the "a"
    screen.resize(12, 4)
    assert (screen.cy, screen.cx) == (0, 4)
    feed(term, "X")
    assert lines(screen) == ["日本Xbc"]


def test_a_tab_in_the_part_that_scrolled_off_is_laid_out_as_a_tab_again():
    term, screen, store = make(cols=10, rows=2)
    feed(term, "ab\tcdefghijklmnopq")
    assert store.open_parts == ["ab\tcd"]
    screen.resize(12, 2)
    assert store.open_parts is None
    assert [row.cells for row in screen.grid] == [
        ["a", "b", "\t", "", "", "", "", "", "c", "d", "e", "f"],
        list("ghijklmnopq")]
    assert (screen.cy, screen.cx) == (1, 11)


def test_a_tab_kept_at_the_right_margin_stays_with_its_character():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "0123456789\t")
    assert screen.grid[0].cells == list("012345678") + ["9\t"]
    screen.resize(20, 4)                        # there is room on the row
    assert [row.cells for row in screen.grid] == [
        list("012345678") + ["9\t"]]
    assert (screen.cy, screen.cx) == (0, 10)
    screen.resize(9, 4)                         # and now it starts a row
    assert [row.cells for row in screen.grid] == [
        list("012345678"), ["9\t"]]
    assert (screen.cy, screen.cx) == (1, 1)
    assert lines(screen) == ["0123456789\t"]


def test_a_tab_of_one_cell_is_one_cell_wide_after_rewrapping():
    term, screen, _ = make(cols=20, rows=4)
    feed(term, "abcdefg\tz")
    screen.resize(30, 4)
    assert screen.grid[0].cells == list("abcdefg") + ["\t", "z"]
    assert (screen.cy, screen.cx) == (0, 9)


def test_a_wide_character_with_a_mark_keeps_two_cells_when_rewrapped():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "日\u0301x")
    screen.resize(12, 4)
    assert screen.grid[0].cells == ["日\u0301", "", "x"]
    assert (screen.cy, screen.cx) == (0, 3)


def test_the_cell_left_unused_before_a_wide_character_has_no_style():
    term, screen, _ = make(cols=12, rows=6)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mabcd日\x1b[0m")
    screen.resize(5, 6)
    assert [row.cells for row in screen.grid] == [
        ["a", "b", "c", "d", ""], ["日", ""]]
    assert [row.styles for row in screen.grid] == [
        [red, red, red, red, 0], [red, red]]


def test_a_tab_that_starts_a_row_after_rewrapping_is_a_whole_tab():
    term, screen, _ = make(cols=10, rows=6)
    feed(term, "abcd\tx")
    screen.resize(4, 6)
    assert [row.cells for row in screen.grid] == [
        list("abcd"), ["\t", "", "", ""], ["x"]]
    assert (screen.cy, screen.cx) == (2, 1)
    assert lines(screen) == ["abcd\tx"]


def test_rewrapping_leaves_no_wrap_pending_on_a_last_column_that_is_free():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "abcdefg")
    screen.resize(8, 4)
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 7, False)
    feed(term, "X")
    assert texts(screen) == ["abcdefgX"]


def test_rewrapping_a_cursor_onto_a_character_leaves_no_wrap_pending():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcdefgh\x1b[HABCDE")           # ends a row that goes on
    assert screen.wrap_pending and screen.grid[0].wrapped
    screen.resize(6, 4)
    assert texts(screen) == ["ABCDEf", "gh"]
    # On the "f", which happens to be in the last column now.
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 5, False)


def test_rewrapping_keeps_a_last_row_whose_next_one_is_gone():
    term, screen, _ = make(cols=5, rows=2)
    feed(term, "abcdefgh\x1b[H\x1b[L")          # its second row is pushed out
    assert texts(screen) == ["", "abcde"] and screen.grid[1].wrapped
    screen.resize(6, 2)
    assert texts(screen) == ["", "abcde"]
    assert (screen.cy, screen.cx) == (0, 0)


def test_rewrapping_keeps_a_cursor_beyond_the_text_on_the_screen():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "ab\x1b[9G")
    screen.resize(5, 4)
    assert texts(screen) == ["ab"]
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 4, False)


def test_a_screen_that_clips_keeps_the_cursor_on_it_and_no_wrap_pending():
    screen = Screen(10, 4, LineStore(), reflow=False)
    term = Terminal(screen)
    feed(term, "0123456789")
    assert (screen.cx, screen.wrap_pending) == (9, True)
    screen.resize(6, 4)
    assert texts(screen) == ["012345"]
    assert (screen.cx, screen.wrap_pending) == (5, False)
    screen.resize(8, 4)                         # wider: nothing comes back
    assert (screen.cx, screen.wrap_pending) == (5, False)
    feed(term, "678")
    assert (screen.cx, screen.wrap_pending) == (7, True)
    screen.resize(8, 2)                         # the rows alone: as it was
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 7, True)


def test_a_screen_that_clips_has_no_row_going_on_after_it_was_cut():
    screen = Screen(10, 4, LineStore(), reflow=False)
    term = Terminal(screen)
    feed(term, "0123456789ab")
    assert lines(screen) == ["0123456789ab"]
    screen.resize(6, 4)
    assert texts(screen) == ["012345", "ab"]
    assert lines(screen) == ["012345", "ab"]


def test_a_wide_character_that_moves_to_the_next_row_of_a_plain_line():
    term, screen, _ = make(cols=12, rows=6)
    feed(term, "abcd日")
    screen.resize(5, 6)
    assert [row.cells for row in screen.grid] == [
        ["a", "b", "c", "d", ""], ["日", ""]]
    assert [row.styles for row in screen.grid] == [None, None]


# -- many plain lines at once -------------------------------------------------

def test_the_fast_path_keeps_the_last_lines_on_the_screen():
    screen = Screen(8, 4, LineStore())
    assert screen.bulk_ok()
    screen.bulk_lines("l0\r\nl1\r\nl2\r\nl3\r\nl4\r\n")
    assert screen.store.texts == ["l0", "l1"]
    assert texts(screen) == ["l2", "l3", "l4", ""]
    assert (screen.cy, screen.cx) == (3, 0)


def test_the_fast_path_counts_the_rows_of_long_lines_exactly():
    screen = Screen(5, 6, LineStore())
    assert screen.bulk_ok()
    screen.bulk_lines("l0\r\nl1\r\nl2\r\nl3\r\n" + "a" * 10 + "\r\n"
                      + "b" * 10 + "\r\n")
    assert screen.store.texts == ["l0", "l1", "l2"]
    assert texts(screen) == ["l3", "aaaaa", "aaaaa", "bbbbb", "bbbbb", ""]
    assert [row.wrapped for row in screen.grid] == [
        False, True, False, True, False, False]


def test_fewer_lines_than_rows_through_the_fast_path_stay_on_the_screen():
    screen = Screen(8, 4, LineStore())
    assert screen.bulk_ok()
    screen.bulk_lines("ab\r\ncd\r\n")
    assert screen.store.texts == []
    assert texts(screen) == ["ab", "cd", ""]
    assert (screen.cy, screen.cx) == (2, 0)


def test_the_fast_path_scrolls_only_as_far_as_the_new_lines_need():
    term, screen, store = make(cols=8, rows=4)
    feed(term, "one\r\ntwo\r\n")
    assert screen.bulk_ok()
    screen.bulk_lines("l0\r\nl1\r\n")
    assert store.texts == ["one"]
    assert texts(screen) == ["two", "l0", "l1", ""]
    assert (screen.cy, screen.cx) == (3, 0)


def test_blanks_at_the_end_of_plain_lines_through_the_fast_path():
    text = ("ab   \r\n"            # a row filled with them
            "abcdefg  \r\n"        # at the end of its second row
            "abc       \r\n"       # its second row is nothing else
            "x \r\n"
            "yz  \r\n")
    fast = Screen(5, 3, LineStore())
    fast.bulk_lines(text)
    # A line loses the blanks at the end of its last row when it goes to
    # the store; on the screen they are cells like any other.
    assert fast.store.texts == ["ab", "abcdefg", "abc  "]
    assert fast.store.max_width == 7
    assert [row.cells for row in fast.grid] == [
        ["x", " "], ["y", "z", " ", " "], []]
    slow = Screen(5, 3, LineStore())
    Terminal(slow).feed(text.encode())          # too short for the fast path
    assert slow.store.texts == fast.store.texts
    assert [row.cells for row in slow.grid] == [
        row.cells for row in fast.grid]


def test_the_fast_path_waits_for_a_line_that_is_open_in_the_store(
        monkeypatch):
    def run() -> tuple:
        term, screen, store = make(cols=5, rows=2)
        feed(term, "abcdefghijk")               # "abcde" has scrolled off
        assert store.open_parts == ["abcde"]
        feed(term, "\x1b[H\x1b[J")
        assert texts(screen) == [""] and not screen.bulk_ok()
        feed(term, "".join(f"L{i}\r\n" for i in range(700)))
        return store.texts, texts(screen)

    fast = run()
    # The row at the top goes on with the open line, whatever is on it.
    assert fast[0][:3] == ["abcdeL0", "L1", "L2"]
    assert fast[0][-1] == "L698" and fast[1] == ["L699", ""]
    monkeypatch.setattr(tmod, "_BULK_MIN", 10 ** 9)
    assert run() == fast


def test_restoring_the_cursor_in_origin_mode_keeps_it_inside_the_region():
    term, screen, _ = make(cols=20, rows=10)
    replies: list[bytes] = []
    term.respond = replies.append
    feed(term, "\x1b[2;4r\x1b[?6h\x1b[1;1H\x1b7")   # on a region's first row
    feed(term, "\x1b[5;7r\x1b8\x1b[6n")              # which is lower by now
    assert screen.cy == 4 and replies == [b"\x1b[1;1R"]
    feed(term, "\x1b[3;1H\x1b7")                     # on its last row
    feed(term, "\x1b[2;4r\x1b8\x1b[6n")              # and it is higher again
    assert screen.cy == 3 and replies[1:] == [b"\x1b[3;1R"]


def test_restoring_the_cursor_without_origin_mode_ignores_the_region():
    term, screen, _ = make(cols=20, rows=10)
    feed(term, "\x1b[9;3H\x1b7\x1b[2;4r\x1b8")
    assert (screen.cy, screen.cx) == (8, 2)
    assert (screen.top, screen.bot) == (1, 3)


def test_rows_cut_off_below_the_cursor_leave_no_line_to_be_continued():
    term, screen, store = make(cols=5, rows=4)
    feed(term, "abcdefgh\x1b[H")
    screen.resize(5, 1)
    assert texts(screen) == ["abcde"]
    feed(term, "\r\nXYZ\r\n")
    assert store.texts == ["abcde", "XYZ"]


def test_rows_that_scroll_up_have_the_erase_colour_on_a_screen_barely_used():
    term, screen, _ = make(cols=4, rows=5)
    blue = style.intern(None, 4, 0)
    feed(term, "ab\r\n\x1b[S")                  # no colour: no row to add
    assert texts(screen) == ["", ""]
    feed(term, "\x1b[44m\x1b[S")
    assert texts(screen) == ["", "", "", "", "    "]
    assert [row.styles for row in screen.grid] == [
        None, None, None, None, [blue] * 4]


def test_a_row_that_moves_up_for_a_deleted_one_has_the_erase_colour():
    term, screen, _ = make(cols=4, rows=5)
    blue = style.intern(None, 4, 0)
    feed(term, "ab\r\ncd\x1b[H\x1b[M")          # no colour: no row to add
    assert texts(screen) == ["cd"]
    feed(term, "\x1b[44m\x1b[M")
    assert texts(screen) == ["", "", "", "", "    "]
    assert screen.grid[4].styles == [blue] * 4
