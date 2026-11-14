"""The cells of the screen and the lines of the store, at their edges:
continuation cells, the unused cell of a wrapped row, tabs and marks that
join a character, styles across the parts of a line, and what erasing,
inserting and deleting leave behind."""

from __future__ import annotations

from jupyterminal.term import LineStore, Screen, Terminal
from jupyterminal.term import style
from jupyterminal.term.screen import row_text_runs


def make(cols: int = 20, rows: int = 5):
    store = LineStore()
    screen = Screen(cols, rows, store)
    return Terminal(screen), screen, store


def feed(term: Terminal, text: str) -> None:
    term.feed(text.encode())


def lines(screen: Screen) -> list[str]:
    return [text for text, _ in screen.all_lines()]


# -- the line store -----------------------------------------------------------

def test_a_new_store_holds_nothing():
    store = LineStore()
    assert (store.texts, store.styles, store.complex) == ([], {}, {})
    assert (store.base, store.dropped, store.max_width) == (0, 0, 0)
    assert (store.open_parts, store.open_runs) == (None, [])
    assert store.open_len == 0


def test_a_default_gap_inside_a_line_that_scrolls_off_in_parts():
    term, screen, store = make(cols=4, rows=2)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    # Two rows have gone: half red and half default, then all green.
    feed(term, "\x1b[31mab\x1b[0mcd\x1b[32mefgh\x1b[0mijklm")
    assert store.open_parts == ["abcd", "efgh"]
    assert store.open_line_runs() == ((2, red), (4, 0), (8, green))
    assert screen.all_lines() == [
        ("abcdefghijklm", ((2, red), (4, 0), (8, green)))]


def test_a_line_that_is_complete_leaves_nothing_open():
    term, screen, store = make(cols=5, rows=2)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mabcdefg\x1b[0m\r\nx\r\ny")
    # It went in two parts, "abcde" and "fg", and is one line now.
    assert store.texts == ["abcdefg"] and store.runs(0) == ((7, red),)
    assert (store.open_parts, store.open_runs) == (None, [])
    assert store.open_len == 0
    assert store.open_line_runs() is None


def test_taking_all_lines_takes_the_one_that_is_still_open_too():
    term, screen, store = make(cols=5, rows=2)
    red = style.intern(1, None, 0)
    feed(term, "one\r\n\x1b[31mabcde\x1b[0mfghijkl")
    assert store.texts == ["one"] and store.open_parts == ["abcde"]
    assert store.take_all() == [("one", None), ("abcde", ((5, red),))]
    assert (store.texts, store.styles, store.open_parts) == ([], {}, None)
    assert store.base == 2


def test_closing_an_open_line_forgets_its_text():
    term, screen, store = make(cols=5, rows=2)
    feed(term, "abcdefghijkl\x1b[2J\x1b[H")     # none of it is on screen
    assert store.open_text() == "abcde"
    screen.finish()
    assert store.texts == ["abcde"]
    assert store.open_text() == ""


def test_dropping_from_the_front_keeps_what_the_first_line_left_has():
    store = LineStore()
    for text in ("a", "b", "c"):
        store.append(text)
    store.append("日本", ((2, 5),))
    store.drop_front(3)
    assert store.text(3) == "日本"
    assert store.styles == {3: ((2, 5),)} and store.complex == {3: 4}


def test_taking_every_line_gives_each_line_its_own_runs():
    store = LineStore()
    store.append("zero")
    store.append("one", ((1, 3),))
    store.append("two", ((2, 4),))
    assert store.take_all() == [("zero", None), ("one", ((1, 3),)),
                                ("two", ((2, 4),))]


# -- the text and the style runs of a row -------------------------------------

def test_a_row_with_text_is_not_blank():
    term, screen, _ = make()
    feed(term, "x")
    assert screen.grid[0].is_blank() is False


def test_a_wrapped_row_keeps_the_blanks_at_its_end():
    term, screen, _ = make(cols=5)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mab\x1b[0m   cd")
    assert [row.wrapped for row in screen.grid] == [True, False]
    assert screen.all_lines() == [("ab   cd", ((2, red),))]


def test_the_unused_cell_of_a_row_that_wraps_no_more_is_no_text():
    term, screen, _ = make(cols=5, rows=3)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31ma\x1b[0m\x1b[5G日")      # no room: to the next row
    feed(term, "\x1b[1;2H\x1b[P")
    row = screen.grid[0]
    assert (row.cells, row.wrapped) == (["a", " ", " ", ""], False)
    assert screen.all_lines() == [("a", ((1, red),)), ("日", None)]


def test_blanks_after_styled_text_do_not_cost_it_its_style():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mab\x1b[0m  \r\n")
    assert screen.grid[0].cells == ["a", "b", " ", " "]
    assert screen.all_lines() == [("ab", ((2, red),))]


def test_the_cells_a_tab_spans_have_no_style_of_their_own():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    # The tab starts on a red blank; the cells after it are default.
    feed(term, "\x1b[31m \x1b[0m\r\t\x1b[32mx")
    assert screen.grid[0].cells == ["\t", "", "", "", "", "", "", "", "x"]
    assert screen.all_lines() == [("\tx", ((1, red), (2, green)))]


def test_a_row_whose_styles_are_all_default_again_has_no_runs():
    term, screen, _ = make()
    feed(term, "\x1b[31ma\x1b[0m\rx")
    assert screen.grid[0].styles == [0]
    assert row_text_runs(screen.grid[0], False) == ("x", None)


def test_what_scrolled_off_keeps_its_styles_apart_from_the_rest():
    term, screen, store = make(cols=5, rows=2)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    feed(term, "abcde\x1b[31mfghij\x1b[0mk")     # a plain row has gone
    assert store.open_parts == ["abcde"] and store.open_runs == []
    assert screen.all_lines() == [("abcdefghijk", ((5, 0), (10, red)))]
    term, screen, store = make(cols=5, rows=2)
    feed(term, "\x1b[31mab\x1b[0mcde\x1b[32mfghij\x1b[0mk")
    assert store.open_line_runs() == ((2, red),)    # red at its start only
    assert screen.all_lines() == [
        ("abcdefghijk", ((2, red), (5, 0), (10, green)))]


def test_rewrapping_a_line_whose_start_scrolled_off_in_two_styles():
    term, screen, store = make(cols=8, rows=2)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    feed(term, "\x1b[31mab\x1b[32mcd\x1b[0mefghijklmnopq")
    assert store.open_line_runs() == ((2, red), (4, green))
    screen.resize(10, 2)
    assert store.open_parts is None             # it is on the grid again
    assert screen.grid[0].cells == list("abcdefghij")
    assert screen.grid[0].styles == [red, red, green, green] + [0] * 6
    assert screen.all_lines() == [
        ("abcdefghijklmnopq", ((2, red), (4, green)))]


# -- writing over cells -------------------------------------------------------

def test_cells_skipped_in_a_styled_row_are_default_blanks():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mab\x1b[0m\x1b[6Gx")
    row = screen.grid[0]
    assert row.cells == ["a", "b", " ", " ", " ", "x"]
    assert row.styles == [red, red, 0, 0, 0, 0]


def test_writing_on_the_unused_cell_keeps_the_character_before_it():
    term, screen, _ = make(cols=5, rows=3)
    feed(term, "abcd日")                         # too wide for the last cell
    assert screen.grid[0].cells == ["a", "b", "c", "d", ""]
    feed(term, "\x1b[1;5Hx")
    assert screen.grid[0].cells == ["a", "b", "c", "d", "x"]
    assert lines(screen) == ["abcdx日"]


def test_a_character_with_a_tab_joined_to_it_is_not_a_tab():
    term, screen, _ = make(cols=5, rows=3)
    feed(term, "abcd\t日")                       # the tab joins the "d"
    assert screen.grid[0].cells == ["a", "b", "c", "d\t", ""]
    feed(term, "\x1b[1;5Hx")
    assert screen.grid[0].cells == ["a", "b", "c", "d\t", "x"]
    assert lines(screen) == ["abcd\tx日"]


def test_overwriting_half_of_a_wide_character_with_a_mark_clears_it():
    term, screen, _ = make(cols=10)
    feed(term, "日\u0301\x1b[1;2Hx")
    assert screen.grid[0].cells == [" ", "x"]


def test_overwriting_the_first_half_of_a_wide_character_clears_it():
    term, screen, _ = make(cols=10)
    feed(term, "日本\rx")
    assert screen.grid[0].cells == ["x", " ", "本", ""]
    assert lines(screen) == ["x 本"]


def test_a_wide_character_written_over_text_replaces_it():
    term, screen, _ = make(cols=10)
    feed(term, "abcd\r日")
    assert screen.grid[0].cells == ["日", "", "c", "d"]


# -- characters at the margin -------------------------------------------------

def test_a_combining_mark_joins_what_is_before_the_cursor_not_under_it():
    term, screen, _ = make(cols=10)
    feed(term, "abc\x1b[2G\u0301")
    assert screen.grid[0].cells == ["a\u0301", "b", "c"]
    term, screen, _ = make(cols=10)
    feed(term, "ab\x1b[C\u0301")                # one column beyond the text
    assert screen.grid[0].cells == ["a", "b\u0301"]


def test_a_wide_character_dropped_in_one_column_still_cancels_the_wrap():
    screen = Screen(1, 3, LineStore())
    term = Terminal(screen)
    feed(term, "a\x1b7\x1b[?7l\x1b8")           # pending, never to happen
    assert screen.wrap_pending and not screen.autowrap
    feed(term, "日")
    assert screen.wrap_pending is False
    feed(term, "\x1b[?7hb")
    assert [row.cells for row in screen.grid] == [["b"]]


def test_a_wide_character_wraps_whole_in_two_columns():
    screen = Screen(2, 3, LineStore())
    term = Terminal(screen)
    feed(term, "a日")
    assert [row.cells for row in screen.grid] == [["a", ""], ["日", ""]]
    assert lines(screen) == ["a日"]


def test_the_unused_cell_of_a_styled_row_is_default():
    term, screen, _ = make(cols=5, rows=3)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mabcd\x1b[0m日")
    assert screen.grid[0].cells == ["a", "b", "c", "d", ""]
    assert screen.grid[0].styles == [red, red, red, red, 0]


def test_repeating_the_last_character_no_times_prints_nothing():
    term, screen, _ = make()
    feed(term, "a")
    screen.repeat_last(0)
    assert screen.grid[0].cells == ["a"] and screen.cx == 1


def test_repeating_after_a_mark_repeats_the_character_it_stands_on():
    term, screen, _ = make()
    feed(term, "e\u0301\x1b[2b")
    assert screen.grid[0].cells == ["e\u0301", "e", "e"]
    term, screen, _ = make()
    feed(term, "o")
    feed(term, "\u0308")                        # the mark comes by itself
    feed(term, "\x1b[b")
    assert screen.grid[0].cells == ["o\u0308", "o"]


def test_repeating_prints_the_character_as_it_was_drawn():
    term, screen, _ = make()
    feed(term, "q\x1b(0\x1b[2b")                 # line drawing comes after it
    assert screen.grid[0].text() == "qqq"
    feed(term, "q")                             # and is still on afterwards
    assert screen.grid[0].text() == "qqq─"
    feed(term, "\x1b[b\x1b(B\x1b[b")             # a line stays a line
    assert screen.grid[0].text() == "qqq───"


# -- tabs ---------------------------------------------------------------------

def test_a_tab_by_no_count_is_one_tab():
    term, screen, _ = make()
    screen.tab(0)
    assert screen.cx == 8
    assert lines(screen) == ["\t"]


def test_a_tab_from_beyond_the_text_starts_in_its_own_column():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mab\x1b[0m\x1b[5G\tx")
    row = screen.grid[0]
    assert row.cells == ["a", "b", " ", " ", "\t", "", "", "", "x"]
    assert row.styles == [red, red, 0, 0, 0, 0, 0, 0, 0]
    assert lines(screen) == ["ab  \tx"]


def test_a_tab_over_blanks_with_a_colour_leaves_them_as_they_are():
    term, screen, _ = make()
    blue = style.intern(None, 4, 0)
    # The tab starts on a blank without a colour and crosses two blue ones.
    feed(term, "\x1b[3G\x1b[44m  \x1b[0m\r\tx")
    row = screen.grid[0]
    assert row.cells == [" "] * 8 + ["x"]
    assert row.styles == [0, 0, blue, blue, 0, 0, 0, 0, 0]
    assert screen.cx == 9
    # And it starts on a blue one.
    term, screen, _ = make()
    feed(term, "\x1b[44m \x1b[0m\r\tx")
    assert screen.grid[0].cells == [" "] * 8 + ["x"]
    assert screen.grid[0].styles == [blue] + [0] * 8


def test_a_tab_minds_only_the_blanks_it_crosses():
    term, screen, _ = make()
    blue = style.intern(None, 4, 0)
    feed(term, "\x1b[10G\x1b[44m \x1b[0m\r\t")   # a blue one beyond its stop
    assert screen.grid[0].cells == ["\t"] + [""] * 7 + [" ", " "]
    assert screen.grid[0].styles == [0] * 9 + [blue]
    term, screen, _ = make()
    feed(term, "\x1b[44m \x1b[0m  \r\x1b[C\t")   # and one before its start
    assert screen.grid[0].cells == [" ", "\t"] + [""] * 6
    assert screen.grid[0].styles == [blue] + [0] * 7


def test_a_tab_from_the_second_cell_of_a_wide_character_leaves_it_whole():
    term, screen, _ = make()
    feed(term, "日\r\x1b[C\tx")
    assert screen.grid[0].cells == ["日", ""] + [" "] * 6 + ["x"]
    assert lines(screen) == ["日      x"]


def test_a_mark_after_a_tab_that_joined_a_character_is_dropped():
    term, screen, _ = make(cols=5, rows=3)
    feed(term, "abcd\t\u0301")                  # the tab joins the "d"
    assert screen.grid[0].cells == ["a", "b", "c", "d\t"]


def test_several_tabs_at_once_at_the_margin_all_stay_in_the_text():
    term, screen, _ = make(cols=4)
    feed(term, "abc\x1b[2Id")
    assert screen.grid[0].cells == ["a", "b", "c\t\t", "d"]
    assert lines(screen) == ["abc\t\td"]


def test_a_tab_at_the_margin_joins_a_wide_character_in_the_first_cells():
    screen = Screen(2, 3, LineStore())
    term = Terminal(screen)
    feed(term, "日\t")
    assert screen.grid[0].cells == ["日\t", ""]
    assert lines(screen) == ["日\t"]


def test_a_back_tab_in_the_first_column_stays_there():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[Z")
    assert screen.cx == 0
    feed(term, "\x1b[9G\x1b[2Z")                # one stop back and no more
    assert screen.cx == 0


def test_a_back_tab_by_no_count_goes_back_one_stop():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[20G")
    screen.back_tab(0)
    assert screen.cx == 16


# -- erasing ------------------------------------------------------------------

def test_erasing_from_a_character_after_a_tab_keeps_the_tab():
    term, screen, _ = make()
    feed(term, "\tb\x1b[9G\x1b[K")
    assert screen.grid[0].cells == ["\t", "", "", "", "", "", "", ""]
    assert lines(screen) == ["\t"]


def test_erasing_a_whole_row_empties_it_and_ends_its_line_there():
    term, screen, _ = make(cols=5)
    feed(term, "abcdefg\x1b[1;1H\x1b[2K")
    assert [row.cells for row in screen.grid] == [[], ["f", "g"]]
    assert [row.wrapped for row in screen.grid] == [False, False]
    assert lines(screen) == ["", "fg"]


def test_erasing_the_whole_screen_cancels_a_pending_wrap():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\x1b[2J")
    assert screen.wrap_pending is False
    feed(term, "x")
    assert [row.text() for row in screen.grid] == ["    x"]


def test_erasing_characters_cancels_a_pending_wrap():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\x1b[X")
    assert screen.wrap_pending is False
    feed(term, "x")
    assert [row.text() for row in screen.grid] == ["abcdx"]


def test_erasing_characters_erases_one_unless_told_more():
    term, screen, _ = make(cols=10)
    feed(term, "abcdef\x1b[2G\x1b[X")
    assert screen.grid[0].cells == ["a", " ", "c", "d", "e", "f"]
    feed(term, "\x1b[4G")
    screen.erase_chars(0)                       # no count is one as well
    assert screen.grid[0].cells == ["a", " ", "c", " ", "e", "f"]


def test_erasing_characters_beyond_the_text_changes_nothing():
    term, screen, _ = make(cols=10)
    feed(term, "ab\x1b[6G\x1b[X")
    assert screen.grid[0].cells == ["a", "b"]
    assert (screen.cy, screen.cx) == (0, 5)


# -- inserting and deleting ---------------------------------------------------

def test_inserting_blanks_at_the_end_of_the_text_changes_nothing():
    term, screen, _ = make(cols=10)
    feed(term, "ab\x1b[2@")
    assert screen.grid[0].cells == ["a", "b"]


def test_inserting_blanks_inserts_at_least_one():
    term, screen, _ = make(cols=10)
    feed(term, "abc\r")
    screen.insert_chars(0)
    assert screen.grid[0].cells == [" ", "a", "b", "c"]


def test_blanks_inserted_without_a_color_bring_no_styles():
    term, screen, _ = make(cols=10)
    feed(term, "abc\r\x1b[2@")
    assert screen.grid[0].cells == [" ", " ", "a", "b", "c"]
    assert screen.grid[0].styles is None


def test_inserting_blanks_on_the_second_half_of_a_wide_character():
    term, screen, _ = make(cols=10)
    feed(term, "日本\x1b[2G\x1b[@")
    assert screen.grid[0].cells == [" ", " ", " ", "本", ""]


def test_inserting_blanks_into_a_wrapped_row_ends_its_line_there():
    term, screen, _ = make(cols=5)
    feed(term, "abcdefg\x1b[1;1H\x1b[@")
    assert screen.grid[0].cells == [" ", "a", "b", "c", "d"]
    assert lines(screen) == [" abcd", "fg"]


def test_a_wide_character_with_a_mark_pushed_half_off_the_row_is_blanked():
    term, screen, _ = make(cols=4)
    feed(term, "ab日\u0301\r\x1b[@")
    assert screen.grid[0].cells == [" ", "a", "b", " "]


def test_deleting_characters_cancels_a_pending_wrap():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\x1b[P")
    assert screen.wrap_pending is False
    feed(term, "x")
    assert [row.text() for row in screen.grid] == ["abcdx"]


def test_deleting_characters_deletes_at_least_one():
    term, screen, _ = make(cols=10)
    feed(term, "abc\r")
    screen.delete_chars(0)
    assert screen.grid[0].cells == ["b", "c"]


def test_deleting_the_character_after_a_tab_keeps_the_tab():
    term, screen, _ = make()
    feed(term, "\tb\x1b[9G\x1b[P")
    assert screen.grid[0].cells == ["\t", "", "", "", "", "", "", ""]
    assert lines(screen) == ["\t"]


def test_the_alignment_test_cancels_a_pending_wrap():
    term, screen, _ = make(cols=4, rows=3)
    feed(term, "abcd\x1b#8")
    assert screen.wrap_pending is False
    feed(term, "x")
    assert [row.text() for row in screen.grid] == ["xEEE", "EEEE", "EEEE"]


# -- attributes ---------------------------------------------------------------

def test_restoring_the_cursor_brings_back_the_background_it_had():
    term, screen, _ = make(cols=4)
    blue = style.intern(None, 4, 0)
    feed(term, "\x1b[44m\x1b7\x1b[0m\x1b8")
    assert (screen.fg, screen.bg, screen.attrs) == (None, 4, 0)
    assert (screen.sid, screen.bce) == (blue, blue)
    feed(term, "x\x1b[K")                       # erasing paints with it
    assert screen.grid[0].cells == ["x", " ", " ", " "]
    assert screen.grid[0].styles == [blue] * 4


def test_restoring_the_cursor_brings_back_attributes_and_no_background():
    term, screen, _ = make(cols=4)
    bold = style.intern(None, None, style.BOLD)
    feed(term, "\x1b[1m\x1b7\x1b[0;44m\x1b8")
    assert (screen.fg, screen.bg, screen.attrs) == (None, None, style.BOLD)
    assert (screen.sid, screen.bce) == (bold, 0)
    feed(term, "x\x1b[K")                       # erasing paints nothing
    assert screen.grid[0].cells == ["x"]
    assert screen.grid[0].styles == [bold]
