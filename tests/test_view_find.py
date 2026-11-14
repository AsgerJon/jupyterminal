"""Finding in a block and filtering blocks: the bar, what it does to the
blocks, and how the view follows the matches."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from conftest import Harness, prompt, submit
from jupyterminal.session import FilterSpec
from jupyterminal.ui.bars import FILTER, FIND
from jupyterminal.ui.view import SCROLL_W
from test_view_selection import capped, numbered, repaints, scene


def make(h: Harness):
    return h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nError: b error\r\n")


def haystack(h: Harness):
    """Two hundred lines with a needle on three of them."""
    lines = [f"line {i}" for i in range(200)]
    for i in (17, 60, 150):
        lines[i] = f"needle {i}"
    return h.run("cat big", "".join(line + "\r\n" for line in lines))


def row_y(view, block, line: int) -> int:
    """Where a line of a block is in the content, every line one row."""
    frame = view.frame()
    top = frame.tops[frame.ids.index(block.id)]
    return top + block.view.header_h + line * view.r.ch


def bar_place(view) -> tuple[int, int]:
    """Top right, clear of the scroll bar; in a view narrower than the
    bar, as far left as it goes."""
    bar = view.search
    return max(4, view.width() - SCROLL_W - bar.width() - 10), 8


def focus_requests(view) -> list:
    """Record every time the view asks for the keyboard from now on."""
    asked: list = []
    set_focus = view.setFocus

    def recording(*args) -> None:
        asked.append(args)
        set_focus(*args)

    view.setFocus = recording
    return asked


def status(view) -> str:
    return view.search.status.text()


# -- opening the bar ---------------------------------------------------------

def test_opening_find_shows_the_bar_for_that_block(ui):
    h = ui()
    block = make(h)
    view = h.view
    bar = view.search
    assert not bar.isVisible()
    wanted = focus_requests(view)
    view.open_find(block)
    assert bar.isVisible() and bar.mode == FIND
    assert bar.title.text() == "Find" and bar.edit.text() == ""
    assert (bar.x(), bar.y()) == bar_place(view)
    # The keyboard goes to the bar, and never by way of the view.
    assert QApplication.focusWidget() is bar.edit and wanted == []
    assert view._find_block is block and block.find is None
    assert status(view) == ""
    bar.edit.setText("error")
    assert block.find.matches == [(1, 0, 5), (3, 0, 5), (3, 9, 14)]
    assert status(view) == "1 / 3"


def test_opening_find_again_keeps_what_was_searched_for(ui):
    h = ui()
    block = make(h)
    view = h.view
    bar = view.search
    view.find(block, FilterSpec("error", case_sensitive=True))
    view.open_find(block)
    assert bar.edit.text() == "error"
    assert bar.case.isChecked() and not bar.regex.isChecked()
    assert block.find.spec == FilterSpec("error", case_sensitive=True)
    assert block.find.matches == [(1, 0, 5), (3, 9, 14)]
    assert status(view) == "1 / 2"
    bar.edit.setText("E.r")
    bar.regex.setChecked(True)
    view.open_find(block)                             # asked for again
    assert bar.edit.text() == "E.r"
    assert bar.case.isChecked() and bar.regex.isChecked()
    assert block.find.matches == [(3, 0, 3)]
    assert status(view) == "1 / 1"


def test_opening_find_for_another_block_ends_the_search_in_the_first(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.open_find(loop)
    view.search.edit.setText("a")
    assert loop.find.matches == [(0, 0, 1)]
    view.open_find(ls)
    assert loop.find is None and view._find_block is ls
    assert view.search.edit.text() == "" and ls.find is None
    view.search.edit.setText("txt")
    assert ls.find.matches == [(0, 2, 5), (0, 9, 12), (1, 2, 5)]
    assert loop.find is None


def test_opening_the_filter_shows_the_bar_for_those_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    bar = view.search
    chosen = [one, ls]
    wanted = focus_requests(view)
    view.open_filter(chosen)
    assert bar.isVisible() and bar.mode == FILTER
    assert bar.title.text() == "Filter" and bar.edit.text() == ""
    assert (bar.x(), bar.y()) == bar_place(view)
    assert QApplication.focusWidget() is bar.edit and wanted == []
    assert view._bar_targets == chosen and view._bar_targets is not chosen
    assert status(view) == ""
    bar.edit.setText("t")
    assert one.filter == ls.filter == FilterSpec("t")
    assert status(view) == "2 of 3"


def test_opening_the_filter_again_shows_the_filter_of_the_first_block(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    bar = view.search
    spec = FilterSpec("^c", regex=True, case_sensitive=True, invert=True,
                      context=2)
    view.set_filter([ls], spec)
    view.open_filter([ls, one])
    assert bar.edit.text() == "^c" and bar.context.value() == 2
    assert bar.regex.isChecked() and bar.case.isChecked()
    assert bar.invert.isChecked()
    # Opening the bar changes nothing: the other block has no filter yet,
    # and what the bar reports is how things are.
    assert ls.filter == spec and one.filter is None
    assert status(view) == "3 of 3"
    # The first change in the bar makes it the filter of all of them.
    bar.context.setValue(0)
    changed = FilterSpec("^c", regex=True, case_sensitive=True, invert=True)
    assert ls.filter == one.filter == changed
    assert status(view) == "2 of 3"
    view.open_filter([])                              # no blocks: an empty bar
    assert bar.isVisible() and bar.edit.text() == ""
    assert view._bar_targets == [] and status(view) == ""
    assert ls.filter == changed


def test_opening_the_filter_leaves_every_filter_as_it_is(ui):
    """The bar opens on the first block's filter, here none.  The filter
    of another block must not vanish because the bar was opened."""
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    spec = FilterSpec("c.txt")
    view.set_filter([ls], spec)
    view.frame()
    assert ls.visible == [1]
    view.open_filter([one, ls])
    assert view.search.edit.text() == "" and status(view) == ""
    assert one.filter is None and ls.filter == spec and ls.visible == [1]
    QTest.keyClick(view.search.edit, Qt.Key.Key_Escape)   # looked, left
    assert not view.search.isVisible()
    assert one.filter is None and ls.filter == spec and ls.visible == [1]


def test_opening_the_filter_ends_a_search(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_find(block)
    view.search.edit.setText("error")
    assert block.find is not None
    view.open_filter([block])
    assert block.find is None and view._find_block is None
    assert view.search.mode == FILTER and view.search.edit.text() == ""
    assert block.filter is None
    view.open_find(block)                             # and the other way
    assert view._bar_targets == [] and view.search.mode == FIND


# -- what the bar reports ----------------------------------------------------

def test_the_filter_bar_counts_the_lines_it_shows(ui):
    h = ui()
    block = make(h)
    other = h.run("build", "error: c\r\nfine\r\nfine\r\nfine\r\nfine\r\n")
    view = h.view
    bar = view.search
    view.open_filter([block, other])
    bar.edit.setText("Error")
    assert status(view) == "3 of 9"
    assert block.visible == [1, 3] and other.visible == [0]
    bar.case.setChecked(True)
    assert status(view) == "1 of 9"
    assert block.visible == [3] and other.visible == []
    bar.invert.setChecked(True)
    assert status(view) == "8 of 9"
    assert block.visible == [0, 1, 2] and other.visible == [0, 1, 2, 3, 4]
    bar.invert.setChecked(False)
    bar.context.setValue(1)
    assert status(view) == "2 of 9"
    assert block.visible == [2, 3] and other.visible == []
    bar.context.setValue(0)
    bar.regex.setChecked(True)
    bar.edit.setText("(")
    assert status(view) == "bad pattern"
    assert block.filter == FilterSpec("(", regex=True, case_sensitive=True)
    view.frame()                                      # every line shows
    assert block.visible is None and block.view.out_rows == 4
    bar.edit.setText("")
    assert status(view) == ""
    assert block.filter is None and other.filter is None
    assert view.frame().heights == [block.view.height, other.view.height]
    assert (block.view.out_rows, other.view.out_rows) == (4, 5)


def test_the_filter_bar_counts_the_lines_of_a_folded_block_too(ui):
    h = ui()
    block = make(h)
    other = h.run("build", "error: c\r\nfine\r\n")
    view = h.view
    view.set_collapsed([other], True)
    view.open_filter([block, other])
    view.search.edit.setText("error")
    assert status(view) == "3 of 6"
    assert other.filter == FilterSpec("error") and other.visible == [0]
    assert other.view.out_rows == 0                   # folded all the same


def test_the_find_bar_tells_how_the_search_went(ui):
    h = ui()
    block = make(h)
    view = h.view
    bar = view.search
    view.open_find(block)
    bar.edit.setText("error")
    assert status(view) == "1 / 3"
    bar.edit.setText("nothing like it")
    assert status(view) == "no matches"
    assert block.find.matches == [] and block.find.current == -1
    bar.regex.setChecked(True)
    bar.edit.setText("(")
    assert status(view) == "bad pattern" and block.find is None
    bar.edit.setText("")
    assert status(view) == "" and block.find is None
    bar.edit.setText("^ok")
    assert status(view) == "1 / 2"
    assert block.find.matches == [(0, 0, 2), (2, 0, 2)]
    # In a block that filters, hidden lines are not searched.
    view.set_filter([block], FilterSpec("1"))
    bar.edit.setText("ok")
    assert status(view) == "1 / 1" and block.find.matches == [(0, 0, 2)]


def test_typing_in_a_bar_that_was_never_opened_does_nothing(ui):
    h = ui()
    block = make(h)
    view = h.view
    asked = repaints(view)
    view.search.edit.setText("error")
    view._bar_changed()                               # and asked directly
    view._bar_step(1)
    assert block.find is None and block.filter is None
    assert status(view) == "" and asked == []
    assert view._find_block is None


# -- stepping through the matches --------------------------------------------

def test_stepping_goes_round_and_reports_where_it_is(ui):
    h = ui()
    block = make(h)
    view = h.view
    bar = view.search
    view.open_find(block)
    bar.edit.setText("error")
    view._bar_step(1)
    assert block.find.current == 1 and status(view) == "2 / 3"
    view._bar_step(1)
    assert block.find.current == 2 and status(view) == "3 / 3"
    asked = repaints(view)
    scroll = view.scroll
    view._bar_step(1)                                 # round to the first
    assert block.find.current == 0 and status(view) == "1 / 3"
    assert view.scroll == scroll and len(asked) == 1  # in view already
    view._bar_step(-1)                                # and back to the last
    assert block.find.current == 2 and status(view) == "3 / 3"
    view._bar_step(-1)
    assert block.find.current == 1 and status(view) == "2 / 3"
    bar.previous.click()
    assert block.find.current == 0
    bar.next.click()
    assert block.find.current == 1


def test_stepping_without_matches_changes_nothing(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_find(block)
    view.search.edit.setText("nothing like it")
    asked = repaints(view)
    laid_out = view._ui
    view._bar_step(1)
    assert status(view) == "no matches" and block.find.current == -1
    assert asked == [] and view._ui == laid_out
    view.search.edit.setText("")
    asked.clear()
    laid_out = view._ui
    view._bar_step(1)                                 # no search at all
    view.find_step(block, 1)
    assert block.find is None and status(view) == "" and asked == []
    assert view._ui == laid_out


def test_stepping_means_nothing_to_the_filter_bar(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_filter([block])
    view.find(block, FilterSpec("ok"))                # a search underneath
    view.search.edit.setText("ok")
    assert block.find.matches == [(0, 0, 2), (2, 0, 2)]
    asked = repaints(view)
    view._bar_step(1)
    assert block.find.current == 0 and status(view) == "2 of 4"
    assert asked == []


# -- closing the bar ---------------------------------------------------------

def test_closing_the_find_bar_ends_the_search(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_find(block)
    view.search.edit.setText("error")
    wanted = focus_requests(view)
    laid_out = view._ui
    view._bar_closed()
    assert block.find is None and view._find_block is None
    assert not view.search.isVisible()
    assert len(wanted) == 1 and view._ui == laid_out + 1
    assert QApplication.focusWidget() is view


def test_closing_the_filter_bar_leaves_the_filter(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_filter([block])
    view.search.edit.setText("error")
    view._bar_closed()
    assert view._bar_targets == [] and not view.search.isVisible()
    assert block.filter == FilterSpec("error") and block.visible == [1, 3]
    view.search.edit.setText("ok")                    # reaches nothing now
    assert block.filter == FilterSpec("error")


def test_closing_the_bar_quietly_leaves_the_keyboard_alone(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.open_find(block)
    wanted = focus_requests(view)
    view._bar_closed(False)
    assert not view.search.isVisible() and wanted == []
    view._bar_closed(False)                           # closed already
    assert not view.search.isVisible() and wanted == []
    view._bar_closed()
    assert len(wanted) == 1
    # A search that no bar shows ends the same way, and its marks go.
    view.find(block, FilterSpec("error"))
    asked = repaints(view)
    view._bar_closed(False)
    assert block.find is None and view._find_block is None
    assert len(asked) == 1 and len(wanted) == 1


# -- filtering ---------------------------------------------------------------

def test_a_filter_is_decided_when_the_block_is_next_laid_out(ui):
    h = ui()
    block = make(h)
    view = h.view
    spec = FilterSpec("error")
    view._sel = ((block.id, 1, 0, 0), (block.id, 1, 1, 2))
    asked = repaints(view)
    before = view.frame()
    tall = block.view.height
    laid_out = view._ui
    view.set_filter([block], spec)
    assert block.filter is spec and block.visible is None
    assert view._sel is None                          # its lines may be gone
    assert len(asked) == 1 and view._ui == laid_out + 1
    frame = view.frame()
    assert frame is not before
    assert block.visible == [1, 3] and block.view.out_rows == 2
    assert frame.heights == [tall - 2 * view.r.ch]
    assert view.copy_blocks([block], "output") == "error: a\nError: b error"


def test_an_equal_filter_is_not_applied_again(ui):
    h = ui()
    block = make(h)
    view = h.view
    spec = FilterSpec("error")
    view.set_filter([block], spec)
    view.frame()
    shown = block.visible
    view.set_filter([block], FilterSpec("error"))
    view.frame()
    assert block.filter is spec and block.visible is shown
    view.set_filter([block], FilterSpec("Error", case_sensitive=True))
    view.frame()
    assert block.visible == [3] and block.visible is not shown
    # Without a filter, clearing changes nothing either.
    view.set_filter([block], None)
    view.frame()
    assert block.filter is None and block.visible is None
    key = block.view.skey
    assert key is not None
    view.set_filter([block], None)
    assert block.view.skey == key


def test_a_filter_that_is_no_pattern_shows_every_line(ui):
    h = ui()
    block = make(h)
    view = h.view
    bad = FilterSpec("(", regex=True)
    view.set_filter([block], bad)
    view.frame()
    assert block.filter is bad and block.visible is None
    assert block.view.out_rows == 4
    assert view.copy_blocks([block], "output").count("\n") == 3
    view.set_filter([block], FilterSpec("(", regex=True))
    assert block.view.skey is None                    # to be decided again


def test_filtering_a_block_that_was_never_laid_out(ui):
    h = ui()
    h.session.feed(submit("make") + b"ok 1\r\nerror: a\r\n")
    block = h.session.blocks[-1]
    view = h.view
    assert block.view is None
    view.set_filter([block], FilterSpec("error"))
    assert block.filter == FilterSpec("error") and block.view is None
    view.frame()
    assert block.view.live_lines == [1] and block.view.live_rows == 1


def test_filtering_several_blocks_at_once(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.set_filter([one, ls], FilterSpec("b"))
    view.frame()
    assert (one.visible, ls.visible) == ([], [0])
    assert loop.filter is None and bad.filter is None
    assert (one.view.out_rows, ls.view.out_rows) == (0, 1)
    view.set_filter([one, ls], None)
    view.frame()
    assert (one.view.out_rows, ls.view.out_rows) == (1, 2)


# -- finding -----------------------------------------------------------------

def test_find_marks_every_match_and_starts_at_the_first(ui):
    h = ui()
    block = make(h)
    view = h.view
    asked = repaints(view)
    laid_out = view._ui
    view.find(block, FilterSpec("error"))
    find = block.find
    assert find.spec == FilterSpec("error")
    assert find.matches == [(1, 0, 5), (3, 0, 5), (3, 9, 14)]
    assert find.lines == [1, 3, 3]
    assert find.current == 0 and find.stamp == h.session.version
    assert view._find_block is block
    assert len(asked) == 1 and view._ui == laid_out + 1
    view.find(block, FilterSpec("ERROR", case_sensitive=True))
    assert block.find.matches == [] and block.find.lines == []
    assert block.find.current == -1
    view.find(block, FilterSpec("Error", case_sensitive=True))
    assert block.find.matches == [(3, 0, 5)]
    view.find(block, FilterSpec("o. [12]", regex=True))
    assert block.find.matches == [(0, 0, 4), (2, 0, 4)]
    view.find(block, FilterSpec("o. [12]"))           # taken literally
    assert block.find.matches == []


def test_find_without_a_pattern_clears_the_search(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.find(block, FilterSpec("error"))
    asked = repaints(view)
    laid_out = view._ui
    view.find(block, FilterSpec(""))
    assert block.find is None and len(asked) == 1
    assert view._ui == laid_out + 1
    view.find(block, FilterSpec("error"))
    view.find(block, FilterSpec("(", regex=True))     # not a pattern
    assert block.find is None


def test_only_one_block_is_searched_at_a_time(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.find(loop, FilterSpec("a"))
    assert loop.find.matches == [(0, 0, 1)] and view._find_block is loop
    view.find(ls, FilterSpec("txt"))
    assert loop.find is None and view._find_block is ls
    assert ls.find.matches == [(0, 2, 5), (0, 9, 12), (1, 2, 5)]
    view.find(ls, FilterSpec("c"))
    assert ls.find.matches == [(1, 0, 1)]


def test_find_looks_only_at_the_lines_a_filter_shows(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.set_filter([block], FilterSpec("ok"))
    view.find(block, FilterSpec("o"))                 # before any repaint
    assert block.visible == [0, 2]
    assert block.find.matches == [(0, 0, 1), (2, 0, 1)]


def test_matches_are_counted_from_the_first_line_a_block_ever_had(ui):
    h = ui()
    block = capped(h)
    view = h.view
    base = block.store.base
    numbers = range(base, 1200)
    view.find(block, FilterSpec("9$", regex=True))
    assert block.find.matches == [
        (i, len(f"n{i}") - 1, len(f"n{i}")) for i in numbers if i % 10 == 9]
    assert block.find.lines == [i for i in numbers if i % 10 == 9]
    view.set_filter([block], FilterSpec("9"))
    view.find(block, FilterSpec("n1"))
    shown = [i for i in numbers if "9" in str(i)]
    assert block.visible == shown and shown[0] == base
    assert block.find.matches == [(i, 0, 2) for i in shown]
    # A filter gone stale may name lines the block does not have: the
    # one before its first, the one after its last.
    block.visible = [base - 1, base + 1, 1200]
    assert view._find_matches(block, FilterSpec("n")) == [(base + 1, 0, 1)]


# -- going to a match --------------------------------------------------------

def test_find_brings_each_match_to_the_middle_of_the_view(ui):
    h = ui(rows=8)
    block = haystack(h)
    view = h.view
    view.resize(view.width(), view.height() + 1)      # an odd height
    h.pump()
    frame = view.frame()
    middle = frame.view_h // 2
    assert frame.view_h % 2 == 1
    assert view.follow is True and view.scroll == frame.max_scroll
    view.find(block, FilterSpec("needle"))
    assert block.find.matches == [(17, 0, 6), (60, 0, 6), (150, 0, 6)]
    assert view.scroll == row_y(view, block, 17) - middle
    assert view.follow is False
    asked = repaints(view)
    laid_out = view._ui
    view.find_step(block, 1)
    assert block.find.current == 1
    assert view.scroll == row_y(view, block, 60) - middle
    assert asked and view._ui == laid_out + 1
    view.find_step(block, 1)
    assert view.scroll == row_y(view, block, 150) - middle
    view.find_step(block, 1)                          # round to the first
    assert block.find.current == 0
    assert view.scroll == row_y(view, block, 17) - middle
    view.find_step(block, -1)
    assert block.find.current == 2
    view.find_step(block, -2)
    assert block.find.current == 0
    view.find_step(block, 5)
    assert block.find.current == 2
    assert view.scroll == row_y(view, block, 150) - middle


def test_a_match_that_can_be_read_stays_where_it_is(ui):
    h = ui(rows=8)
    block = numbered(h, 200)
    view = h.view
    frame = view.frame()
    ch = view.r.ch
    y = row_y(view, block, 40)
    margin = block.view.header_h + ch                 # the pinned command
    middle = frame.view_h // 2
    assert margin + 2 * ch < frame.view_h
    # Right below the pinned command and a spare row: stays.
    view._set_scroll(y - margin)
    view._reveal_text(block, 40, 0)
    assert view.scroll == y - margin
    # A pixel further up, it would be partly under them.
    view._set_scroll(y - margin + 1)
    view._reveal_text(block, 40, 0)
    assert view.scroll == y - middle
    # With one row to spare below it: stays.
    view._set_scroll(y + 2 * ch - frame.view_h)
    view._reveal_text(block, 40, 0)
    assert view.scroll == y + 2 * ch - frame.view_h
    # A pixel less than that.
    view._set_scroll(y + 2 * ch - frame.view_h - 1)
    view._reveal_text(block, 40, 0)
    assert view.scroll == y - middle
    # A match near the start cannot come to the middle.
    view._reveal_text(block, 1, 0)
    assert view.scroll == 0


def test_find_stops_the_view_following_even_when_the_match_shows(ui):
    h = ui(rows=8)
    lines = [f"line {i}" for i in range(200)]
    lines[197] = "needle"
    block = h.run("cat big", "".join(line + "\r\n" for line in lines))
    view = h.view
    bottom = view.frame().max_scroll
    assert view.follow is True and view.scroll == bottom
    view.find(block, FilterSpec("needle"))
    assert block.find.matches == [(197, 0, 6)]
    assert view.scroll == bottom and view.follow is False


def test_finding_in_a_folded_block_opens_it(ui):
    h = ui(rows=8)
    block = haystack(h)
    view = h.view
    view.set_collapsed([block], True)
    assert view.frame().max_scroll == 0
    laid_out = view._ui
    view.find(block, FilterSpec("needle"))
    assert block.collapsed is False
    assert view._ui == laid_out + 2                   # opened, and found
    assert view.scroll == \
        row_y(view, block, 17) - view.frame().view_h // 2 > 0
    view.set_collapsed([block], True)
    laid_out = view._ui
    view.find_step(block, 1)
    assert block.collapsed is False and view._ui == laid_out + 2
    assert view.scroll == row_y(view, block, 60) - view.frame().view_h // 2


# -- a search in a block that still prints -----------------------------------

def running_build(h: Harness):
    h.session.feed(submit("build") + b"error: early\r\n")
    for i in range(10):
        h.session.feed(f"line {i}\r\n".encode())
    h.session.feed(b"error: one\r\n")
    h.pump()
    return h.session.blocks[-1]


def test_a_search_keeps_up_with_a_block_that_still_prints(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.open_find(block)
    view.search.edit.setText("error")
    find = block.find
    found = find.matches
    assert found == [(0, 0, 5), (11, 0, 5)] and status(view) == "1 / 2"
    view._bar_step(1)
    assert find.current == 1
    asked = repaints(view)
    laid_out = view._ui
    view._refresh_find()                              # nothing new
    assert find.matches is found and asked == []
    assert view._ui == laid_out
    h.session.feed(b"error: two\r\nfine\r\nerror: three\r\n")
    assert find.matches is found                      # not looked at yet
    asked.clear()
    view._refresh_find()
    assert view._ui == laid_out + 1
    assert block.find is find
    assert find.matches == [(0, 0, 5), (11, 0, 5), (12, 0, 5), (14, 0, 5)]
    assert find.lines == [0, 11, 12, 14]
    assert find.current == 1 and find.stamp == h.session.version
    assert status(view) == "2 / 4"
    assert len(asked) == 1


def test_a_search_notices_when_its_matches_go(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.open_find(block)
    view.search.edit.setText("one|two|three")
    view.search.regex.setChecked(True)
    find = block.find
    assert find.matches == [(11, 7, 10)] and find.current == 0
    h.session.feed(b"\x1b[H\x1b[2J")                  # the screen is cleared
    view._refresh_find()
    assert find.matches == [] and find.lines == [] and find.current == -1
    assert status(view) == "no matches"
    h.session.feed(b"nothing of the kind\r\n")
    view._refresh_find()                              # and still none
    assert find.matches == [] and find.current == -1
    assert status(view) == "no matches"
    h.session.feed(b"two\r\nthree\r\n")
    view._refresh_find()
    assert [m[1:] for m in find.matches] == [(0, 3), (0, 5)]
    assert find.current == 0 and status(view) == "1 / 2"


def test_the_current_match_falls_back_to_the_last_one_left(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.find(block, FilterSpec("error"))
    view.find_step(block, 1)
    find = block.find
    assert find.matches == [(0, 0, 5), (11, 0, 5)] and find.current == 1
    h.session.feed(b"\x1b[H\x1b[2J")                  # the second one goes
    view._refresh_find()
    assert find.matches == [(0, 0, 5)] and find.current == 0
    # No bar is showing this search, so none reports it.
    assert status(view) == ""


def test_a_search_under_the_filter_bar_does_not_touch_its_report(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.open_filter([block])
    view.search.edit.setText("line")
    # The bar counts every line of the block: the seven that have left
    # its screen and the five still on it.
    assert block.visible == [1, 2, 3, 4, 5, 6] and block.store.end == 7
    assert block.view.live_lines == [7, 8, 9, 10]
    assert status(view) == "10 of 12"
    view.find(block, FilterSpec("line 9"))
    assert block.find.matches == [(10, 0, 6)]
    h.session.feed(b"line 9 again\r\n")
    view._refresh_find()
    assert block.find.matches == [(10, 0, 6), (12, 0, 6)]
    assert status(view) == "10 of 12"


def test_the_chip_of_a_block_that_still_prints_counts_its_screen_too(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.set_filter([block], FilterSpec("error"))
    view.frame()
    # "error: early" has left the screen, "error: one" is still on it.
    assert block.visible == [0] and block.view.live_lines == [11]
    assert view._filter_counts(block) == (2, 12)
    assert ("filter", "filter 2/12", 16) in block.view.items
    h.session.feed(prompt())                          # the build is over
    view.frame()
    assert block.visible == [0, 11] and block.view.live_lines is None
    assert view._filter_counts(block) == (2, 12)
    # A filter that cannot be used shows everything, and says so.
    view.set_filter([block], FilterSpec("(", regex=True))
    view.frame()
    assert block.visible is None
    assert view._filter_counts(block) == (12, 12)


def test_the_row_the_cursor_waits_on_is_no_line_of_a_running_block(ui):
    """A filter turned around shows that empty row as well; the count of
    lines must not."""
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    view.set_filter([block], FilterSpec("no such text", invert=True))
    view.frame()
    assert block.visible == [0, 1, 2, 3, 4, 5, 6]
    assert block.view.live_lines == [7, 8, 9, 10, 11, 12]
    assert block.line_count() == 12
    assert view._filter_counts(block) == (12, 12)
    assert ("filter", "filter 12/12", 17) in block.view.items


def test_the_count_keeps_step_with_lines_the_cap_has_dropped(ui):
    h = ui()
    block = capped(h)
    view = h.view
    held = len(block.store.texts)
    assert block.store.base > 0 and held < 1200
    view.set_filter([block], FilterSpec("n1199"))
    view.frame()
    assert block.visible == [1199]
    assert view._filter_counts(block) == (1, held)


def test_refreshing_without_a_search_does_nothing(ui):
    h = ui(rows=6)
    block = running_build(h)
    view = h.view
    asked = repaints(view)
    view._refresh_find()                              # no block is searched
    view.open_find(block)                             # nothing typed yet
    asked.clear()
    h.session.feed(b"more\r\n")
    asked.clear()
    laid_out = view._ui
    view._refresh_find()
    assert block.find is None and asked == []
    assert view._ui == laid_out


# -- found in testing --------------------------------------------------------

def test_a_search_is_redone_when_the_filter_changes(ui):
    h = ui()
    block = make(h)
    view = h.view
    view.set_filter([block], FilterSpec("error"))
    view.open_find(block)
    view.search.edit.setText("b")
    find = block.find
    assert find.matches == [(3, 7, 8)] and status(view) == "1 / 1"
    asked = repaints(view)
    laid_out = view._ui
    view.set_filter([block], None)                    # every line shows again
    assert block.find is find
    assert find.matches == [(3, 7, 8)]
    assert view._ui == laid_out + 2                   # filtered, and found
    view.set_filter([block], FilterSpec("ok"))        # its line is hidden
    assert find.matches == [] and find.current == -1
    assert status(view) == "no matches"
    view.set_filter([block], None)
    assert find.matches == [(3, 7, 8)] and find.current == 0
    assert status(view) == "1 / 1"
    assert asked
    # The match the search was on stays the current one.
    view.search.edit.setText("o")
    find = block.find
    assert find.matches == [(0, 0, 1), (1, 3, 4), (2, 0, 1), (3, 3, 4),
                            (3, 12, 13)]
    view.set_filter([block], FilterSpec("error"))
    assert find.matches == [(1, 3, 4), (3, 3, 4), (3, 12, 13)]
    assert find.current == 0 and status(view) == "1 / 3"
    view.set_filter([block], None)                    # one more before it
    assert find.current == 1 and status(view) == "2 / 5"
    view.set_filter([block], FilterSpec("error"))
    assert find.current == 0
    view._bar_step(1)
    assert find.current == 1 and status(view) == "2 / 3"
    view.set_filter([block], None)
    assert find.current == 3 and status(view) == "4 / 5"
    # A filter on another block is none of the search's business.
    other = h.run("true", "")
    stamp = find.stamp
    view.set_filter([other], FilterSpec("x"))
    assert find.stamp == stamp
