"""The mouse in the view: clicks, drags, header buttons, reports to
programs, hovering, the wheel and tooltips (no shell involved)."""

from __future__ import annotations

import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import (QContextMenuEvent, QCursor, QGuiApplication,
                           QHelpEvent, QMouseEvent, QWheelEvent)
from PySide6.QtWidgets import QApplication

from conftest import ALT, CTRL, LEFT, NONE, SHIFT, Harness, mark, submit
from jupyterminal.session import BACKGROUND, FilterSpec
from jupyterminal.ui import view as view_module
from jupyterminal.ui.view import HEAD_TOP, IN_TOP, PAD_LEFT

MIDDLE = Qt.MouseButton.MiddleButton
RIGHT = Qt.MouseButton.RightButton
BACK = Qt.MouseButton.BackButton
NO_BUTTON = Qt.MouseButton.NoButton
ARROW = Qt.CursorShape.ArrowCursor
IBEAM = Qt.CursorShape.IBeamCursor
HAND = Qt.CursorShape.PointingHandCursor


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """The view's clock stands still unless a test moves it, so that
    whether two clicks make a double click never depends on the machine."""
    now = SimpleNamespace(value=0.0)
    monkeypatch.setattr(view_module, "time", SimpleNamespace(
        monotonic=lambda: now.value, strftime=time.strftime,
        localtime=time.localtime))
    return now


@pytest.fixture
def pointer():
    """Puts the screen's pointer somewhere, and parks it far from every
    window afterwards so that no later test finds it over its view."""
    yield QCursor.setPos
    QCursor.setPos(QPoint(-20000, -20000))


# -- events built by hand ----------------------------------------------------
#
# Each goes straight to its handler, so that an error in the handler fails
# the test instead of being printed and forgotten by the event loop.

def mouse_event(h: Harness, kind, point: QPoint, button, buttons, mods):
    return QMouseEvent(kind, QPointF(point),
                       QPointF(h.view.mapToGlobal(point)), button, buttons,
                       mods)


def press(h: Harness, point: QPoint, button=LEFT, mods=NONE) -> None:
    h.view.mousePressEvent(mouse_event(
        h, QEvent.Type.MouseButtonPress, point, button, button, mods))


def move(h: Harness, point: QPoint, buttons=LEFT, mods=NONE) -> None:
    h.view.mouseMoveEvent(mouse_event(
        h, QEvent.Type.MouseMove, point, NO_BUTTON, buttons, mods))


def release(h: Harness, point: QPoint, button=LEFT, mods=NONE) -> None:
    h.view.mouseReleaseEvent(mouse_event(
        h, QEvent.Type.MouseButtonRelease, point, button, NO_BUTTON, mods))


def click(h: Harness, point: QPoint, button=LEFT, mods=NONE) -> None:
    press(h, point, button, mods)
    release(h, point, button, mods)


def second_click(h: Harness, point: QPoint) -> None:
    """What Qt sends for the second click of a double click."""
    h.view.mouseDoubleClickEvent(mouse_event(
        h, QEvent.Type.MouseButtonDblClick, point, LEFT, LEFT, NONE))
    release(h, point)


def drag(h: Harness, start: QPoint, end: QPoint, mods=NONE) -> None:
    press(h, start, mods=mods)
    move(h, end, mods=mods)
    release(h, end, mods=mods)


def hover(h: Harness, point: QPoint, mods=NONE):
    """Move the pointer with no button down; what is marked, and the
    pointer's shape."""
    move(h, point, NO_BUTTON, mods)
    return h.view._hover, h.view.cursor().shape()


def wheel(h: Harness, angle: int = 0, pixels: int = 0, mods=NONE,
          at: QPoint | None = None) -> None:
    at = at or QPoint(PAD_LEFT, h.view.r.ch)
    h.view.wheelEvent(QWheelEvent(
        QPointF(at), QPointF(h.view.mapToGlobal(at)), QPoint(0, pixels),
        QPoint(0, angle), NO_BUTTON, mods, Qt.ScrollPhase.NoScrollPhase,
        False))


# -- where things are --------------------------------------------------------

def out(block, line: int, offset: int) -> tuple:
    """A position in a block's output, as the view names it."""
    return (block.id, 1, line, offset)


def input_point(h: Harness, row: int, col: int) -> QPoint:
    """A cell of the input area."""
    view = h.view
    top = view.frame().view_h + IN_TOP
    return QPoint(PAD_LEFT + col * view.r.cw + 2,
                  top + row * view.r.ch + view.r.ch // 2)


def cell_point(h: Harness, row: int, col: int) -> QPoint:
    """A cell of the full-screen terminal."""
    view = h.view
    return QPoint(PAD_LEFT + col * view.r.cw + 2,
                  2 + row * view.r.ch + view.r.ch // 2)


def button(h: Harness, block, name: str) -> QPoint:
    """The middle of one of a block's header buttons, as painted now."""
    h.view.repaint()
    rects = [rect for rect, owner, kind in h.view._buttons
             if owner is block and kind == name]
    return rects[-1].center().toPoint()


def beside(h: Harness) -> QPoint:
    """A point of the block list where no block is."""
    point = QPoint(PAD_LEFT + 5 * h.view.r.cw, 3)
    assert h.view._block_under(point.y())[1] is None
    return point


def count_updates(h: Harness) -> list[tuple]:
    """Note every repaint the view asks for."""
    calls: list[tuple] = []
    h.view.update = lambda *args: calls.append(args)
    return calls


def record_menus(h: Harness) -> list[tuple]:
    """Note the menus the view opens instead of showing them."""
    opened: list[tuple] = []
    h.view.open_menu = lambda block, where: opened.append((block, where))
    return opened


def three_blocks(h: Harness):
    return [h.run("echo one", "one\r\n"),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"),
            h.run("false", "", status=1)]


def tall(ui, lines: int = 100):
    """A view too short for its one block, scrolled to the block's end."""
    h = ui(rows=8)
    block = h.run(f"seq {lines}", "".join(f"{i}\r\n" for i in range(lines)))
    assert h.view.frame().max_scroll > 20 * h.view.r.ch
    return h, block


def full_screen(h: Harness, text: bytes = b"") -> None:
    h.session.feed(submit("vim") + b"\x1b[?1049h\x1b[2J\x1b[H" + text)
    h.pump()
    assert h.session.fullscreen


def widen(h: Harness, cols: int) -> None:
    h.view.resize(*h.view.size_for(cols, h.view.rows))
    h.pump()
    assert h.session.cols == cols


# -- zones and cells ---------------------------------------------------------

def test_the_view_is_blocks_above_the_input_area_or_one_screen(ui):
    h = ui()
    three_blocks(h)
    view = h.view
    view_h = view.frame().view_h
    assert 0 < view_h < view.height()
    assert view._zone(0) == "blocks"
    assert view._zone(view_h - 1) == "blocks"
    assert view._zone(view_h) == "input"
    assert view._zone(view.height() - 1) == "input"
    full_screen(h)
    assert view._zone(0) == "screen"
    assert view._zone(view.height() - 1) == "screen"


def test_cells_of_the_input_area_count_from_its_first_row(ui):
    h = ui()
    view = h.view
    cw, ch = view.r.cw, view.r.ch
    top = view.frame().view_h + IN_TOP
    screen = h.session.input
    x = PAD_LEFT + 7 * cw
    assert view._screen_cell(x, top) == (screen, 0, 7)
    assert view._screen_cell(x + cw - 1, top + ch - 1) == (screen, 0, 7)
    assert view._screen_cell(x + cw, top + ch) == (screen, 1, 8)
    assert view._screen_cell(x, top + 3 * ch) == (screen, 3, 7)
    # The padding above the first row belongs to that row.
    assert view._screen_cell(x, top - IN_TOP) == (screen, 0, 7)
    assert view._screen_cell(x, top)[0] is screen


def test_cells_of_a_full_screen_program_fill_the_view(ui):
    h = ui()
    full_screen(h)
    view = h.view
    cw, ch = view.r.cw, view.r.ch
    screen = h.session.term.screen
    x = PAD_LEFT + 4 * cw
    assert view._screen_cell(x, 2) == (screen, 0, 4)
    assert view._screen_cell(x, 1 + ch) == (screen, 0, 4)
    assert view._screen_cell(x, 2 + ch) == (screen, 1, 4)
    assert view._screen_cell(x, 0) == (screen, 0, 4)     # the margin
    last = view.rows - 1
    assert view._screen_cell(x, 2 + last * ch) == (screen, last, 4)
    assert view._screen_cell(x, 2 + (last + 3) * ch) == (screen, last, 4)
    assert view._screen_cell(x, 2)[0] is screen
    assert screen is not h.session.input


# -- pressing ----------------------------------------------------------------

def test_a_press_takes_the_keyboard_from_the_search_field(ui):
    h = ui()
    block = h.run("make", "ok\r\nerror\r\n")
    h.view.open_find(block)
    h.pump()
    assert QApplication.focusWidget() is h.view.search.edit
    press(h, h.point(block, 0, 0))
    h.pump()
    assert QApplication.focusWidget() is h.view


def test_the_middle_button_pastes_the_selection_wherever_it_is(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    view.select_block(ls)
    pasted = []
    view.paste = pasted.append
    click(h, h.point(one, 0, 1), MIDDLE)
    assert pasted == [True]
    click(h, input_point(h, 0, 3), MIDDLE)
    assert pasted == [True, True]
    assert view._press is None and view.selected == [ls.id]
    assert h.sent == []


def test_the_right_button_opens_the_menu_of_the_block_under_it(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    opened = record_menus(h)
    spot = h.point(ls, 1, 2)
    click(h, spot, RIGHT)
    assert opened == [(ls, view.mapToGlobal(spot))]
    assert view.selected == [ls.id]      # the menu is about a selected block
    # A block that is part of the selection leaves the selection alone ...
    view.select_block(bad, "toggle")
    click(h, h.point(bad), RIGHT)
    assert opened[1:] == [(bad, view.mapToGlobal(h.point(bad)))]
    assert view.selected == [ls.id, bad.id]
    # ... any other block replaces it.
    click(h, h.point(one), RIGHT)
    assert opened[2:] == [(one, view.mapToGlobal(h.point(one)))]
    assert view.selected == [one.id]
    # Where there is no block there is no menu.
    click(h, beside(h), RIGHT)
    click(h, input_point(h, 0, 3), RIGHT)
    assert len(opened) == 3 and view.selected == [one.id]
    assert view._press is None
    # Nor is there one while a program has the whole view.
    row = h.point(ls)
    full_screen(h)
    click(h, row, RIGHT)
    assert len(opened) == 3 and view.selected == [one.id]


def test_other_buttons_do_nothing_and_never_start_a_double_click(ui):
    h = ui()
    block = h.run("cat f", "alpha beta\r\n")
    view = h.view
    spot = h.point(block, 0, 2)
    click(h, spot, BACK)
    assert view._press is None and view.selected == []
    click(h, spot)                       # the first click on this spot
    assert view.selected == [block.id] and view._sel is None


# -- clicks on blocks --------------------------------------------------------

def test_a_click_selects_a_block_and_modifiers_extend_the_selection(
        ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    click(h, h.point(ls, 1, 2))
    assert view.selected == [ls.id] and view._press is None
    click(h, h.point(bad), mods=CTRL)
    assert view.selected == [ls.id, bad.id]
    click(h, h.point(one), mods=CTRL | SHIFT)    # Ctrl decides
    assert view.selected == [one.id, ls.id, bad.id]
    click(h, h.point(ls), mods=CTRL)
    assert view.selected == [one.id, bad.id]
    # What was held when the button went down counts, not when it came up.
    clock.value += 1
    press(h, h.point(ls), mods=CTRL)
    release(h, h.point(ls), mods=NONE)
    assert view.selected == [one.id, ls.id, bad.id]
    clock.value += 1
    click(h, h.point(ls))
    assert view.selected == [ls.id]
    click(h, h.point(bad), mods=SHIFT)
    assert view.selected == [ls.id, bad.id]
    click(h, h.point(one), mods=SHIFT)   # the range still starts at "ls"
    assert view.selected == [one.id, ls.id]
    assert view._sel is None and h.sent == []


def test_clicks_count_up_to_a_word_and_then_a_line(ui):
    h = ui()
    block = h.run("cat f", "alpha beta-gamma delta\r\nsecond line\r\n")
    view = h.view
    spot = h.point(block, 0, 8)
    click(h, spot)
    assert view.selected == [block.id] and view._sel is None
    second_click(h, spot)
    assert view._sel == (out(block, 0, 6), out(block, 0, 16))
    assert view.selection_text() == "beta-gamma"
    assert view.selected == []           # text is selected, not the block
    updates = count_updates(h)
    press(h, spot)                       # the third: the whole line
    assert view._sel == (out(block, 0, 0), out(block, 0, 22))
    assert len(updates) == 1
    release(h, spot)
    assert view.selection_text() == "alpha beta-gamma delta"
    click(h, spot)                       # a fourth click changes nothing
    assert view._sel == (out(block, 0, 0), out(block, 0, 22))
    assert view.selected == []


def test_two_clicks_are_a_double_click_only_within_the_interval(ui, clock):
    h = ui()
    block = h.run("cat f", "alpha beta\r\n")
    view = h.view
    spot = h.point(block, 0, 2)
    interval = QGuiApplication.styleHints().mouseDoubleClickInterval() / 1000
    click(h, spot)
    clock.value = interval               # as late as it may be
    click(h, spot)
    assert view.selection_text() == "alpha"
    clock.value = 3 * interval           # too late: the first click again
    click(h, spot)
    assert view._sel is None and view.selected == [block.id]
    clock.value = 3.5 * interval
    click(h, spot)
    assert view.selection_text() == "alpha"


def test_a_double_click_needs_both_clicks_on_the_same_spot(ui):
    h = ui(rows=12)
    line = "x" * 30
    block = h.run("cat big", (line + "\r\n") * 100)
    view = h.view
    # Clicks that are six pixels apart are never on one spot, and pixels
    # 70 and 74 are on one spot however a spot is rounded off.
    for first, second in ((QPoint(70, 72), QPoint(74, 72)),
                          (QPoint(72, 70), QPoint(72, 74))):
        view._sel = None
        click(h, first)
        click(h, second)
        assert view.selection_text() == line, (first, second)
        click(h, second + QPoint(6, 0))  # the count starts again
        assert view._sel is None and view.selected == [block.id]
        click(h, second + QPoint(6, 6))
        assert view._sel is None and view.selected == [block.id]
        click(h, second + QPoint(0, 6))
        assert view._sel is None and view.selected == [block.id]


def test_a_double_click_needs_the_same_text_under_both_clicks(ui):
    h, block = tall(ui)
    view = h.view
    spot = h.point(block, 97, 0)
    click(h, spot)
    view._set_scroll(view.scroll - 2 * view.r.ch)    # between the clicks
    click(h, spot)
    assert view._sel is None and view.selected == [block.id]
    click(h, spot)                       # now it is the same text again
    assert view.selection_text() == "95"


def test_a_double_click_needs_the_same_block_under_both_clicks(ui):
    h = ui()
    first = h.run("true", "")
    view = h.view
    spot = h.point(first)
    click(h, spot)
    second = h.run("true", "")           # moves in under the pointer
    assert view._block_under(spot.y())[1] is second
    click(h, spot)
    assert view._sel is None and view.selected == [second.id]


def test_a_click_where_no_block_was_is_not_half_a_double_click(ui):
    h = ui()
    view = h.view
    spot = QPoint(PAD_LEFT + 2 * view.r.cw, view.frame().view_h - 8)
    click(h, spot)                       # nothing there yet
    block = h.run("true", "")
    assert view._block_under(spot.y())[1] is block
    click(h, spot)
    assert view._sel is None and view.selected == [block.id]


def test_a_click_in_another_zone_is_not_half_a_double_click(ui):
    h = ui()
    h.run("echo one", "one two\r\n")
    # A clear: the block is above the view, the list below it is empty.
    h.session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    h.session.feed(b"x" * 50)            # the input wraps into a second row
    h.pump()
    view = h.view
    scroll = view.scroll
    spot = QPoint(PAD_LEFT + 2 * view.r.cw, view.frame().view_h + 2)
    assert view._zone(spot.y()) == "input"
    click(h, spot)
    h.session.feed(b"\x1b[A\r\x1b[J% short")     # ... and is cut back to one
    h.pump()
    assert view._zone(spot.y()) == "blocks" and view.scroll == scroll
    assert view._block_under(spot.y())[1] is None
    click(h, spot)
    assert view._sel is None


def test_control_and_shift_clicks_never_select_words(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    spot = h.point(ls, 0, 2)
    click(h, spot, mods=CTRL)
    click(h, spot, mods=CTRL)            # twice on one spot: toggled back
    assert view.selected == [] and view._sel is None
    click(h, h.point(one), mods=CTRL)
    click(h, h.point(bad), mods=SHIFT)
    click(h, h.point(bad), mods=SHIFT)
    assert view.selected == [one.id, ls.id, bad.id] and view._sel is None
    # Alt is no such modifier.
    word = h.point(ls, 1, 1)
    click(h, word, mods=ALT)
    click(h, word, mods=ALT)
    assert view.selection_text() == "c.txt" and view.selected == []


def test_a_double_click_on_an_empty_line_is_just_a_click(ui):
    h = ui()
    block = h.run("printf", "top\r\n\r\nbottom\r\n")
    view = h.view
    spot = h.point(block, 1, 3)
    click(h, spot)
    second_click(h, spot)
    assert view._sel is None
    assert view.selected == [block.id]


def test_dragging_after_a_double_click_grows_the_selection_by_the_word(ui):
    h = ui()
    block = h.run("cat f", "alpha beta-gamma delta\r\nsecond line\r\n")
    view = h.view
    spot = h.point(block, 0, 8)
    word = (out(block, 0, 6), out(block, 0, 16))
    click(h, spot)
    press(h, spot)                       # the second press: a word
    assert view._sel == word and view.selected == []
    move(h, h.point(block, 0, 19))       # past its end
    assert view._sel == (out(block, 0, 6), out(block, 0, 19))
    move(h, h.point(block, 0, 2))        # before its start
    assert view._sel == (out(block, 0, 16), out(block, 0, 2))
    assert view.selection_text() == "pha beta-gamma"
    move(h, h.point(block, 0, 10))       # back inside it
    assert view._sel == word
    move(h, h.point(block, 0, 6))        # on its first character
    assert view._sel == word
    move(h, h.point(block, 0, 16))       # just behind its last
    assert view._sel == word
    move(h, h.point(block, 1, 6))        # into the next line
    release(h, h.point(block, 1, 6))
    assert view.selection_text() == "beta-gamma delta\nsecond"


def test_a_click_beside_the_blocks_drops_every_selection(ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    nowhere = beside(h)
    drag(h, h.point(ls, 0, 0), h.point(ls, 0, 5))
    assert view.selection_text() == "a.txt"
    click(h, nowhere)
    assert view._sel is None
    click(h, h.point(ls))
    assert view.selected == [ls.id]
    updates = count_updates(h)
    clock.value += 1
    click(h, nowhere)
    assert view.selected == [] and view._cursor_block is None
    assert len(updates) == 2             # the deselection, and the click


def test_presses_where_there_are_no_blocks_select_nothing(ui):
    h = ui()
    view = h.view
    spot = QPoint(PAD_LEFT + 5 * view.r.cw, 2 * view.r.ch)
    assert view._zone(spot.y()) == "blocks"
    click(h, spot)
    click(h, spot)                       # there is no word to double-click
    assert view._sel is None and view.selected == []
    press(h, spot)
    assert view._press["pos"] is None
    move(h, spot + QPoint(40, 0))
    assert view._press["dragging"] and view._sel is None
    block = h.run("echo late", "late\r\n")
    move(h, h.point(block, 0, 2))        # a drag that started on nothing
    assert view._sel is None
    release(h, h.point(block, 0, 2))
    assert view._sel is None and view.selected == []
    assert view._press is None


def test_a_drag_stops_extending_once_the_blocks_are_gone(ui):
    h = ui()
    block = h.run("cat f", "alpha beta\r\n")
    view = h.view
    start = h.point(block, 0, 2)
    press(h, start)
    assert view._press["pos"] == out(block, 0, 2)
    h.session.clear_blocks()
    move(h, start + QPoint(40, 0))
    assert view._press["dragging"] and view._sel is None


# -- dragging ----------------------------------------------------------------

def test_a_press_becomes_a_drag_after_four_pixels(ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    start = h.point(ls, 0, 3)
    for step in (QPoint(3, 0), QPoint(0, -3), QPoint(-2, 1)):
        clock.value += 1
        press(h, start)
        move(h, start + step)
        assert not view._press["dragging"], step
        release(h, start + step)
        assert view.selected == [ls.id]  # it was a click
    for step in (QPoint(4, 0), QPoint(0, 4), QPoint(2, -2)):
        clock.value += 1
        view.select_block(ls)
        press(h, start)
        move(h, start + step)
        assert view._press["dragging"], step
        assert view.selected == []       # a drag is never about blocks
        release(h, start + step)
        assert view.selected == []


def test_a_drag_selects_text_from_the_press_to_the_pointer(ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    published = []
    view._publish_selection = lambda: published.append(view.selection_text())
    view.select_block(one)
    assert view.follow
    press(h, h.point(ls, 0, 2))
    assert view.selected == [one.id] and published == []
    updates = count_updates(h)
    move(h, h.point(ls, 1, 3))
    assert view._sel == (out(ls, 0, 2), out(ls, 1, 3))
    assert view.selected == [] and not view.follow
    assert len(updates) == 2             # the deselection, the selection
    release(h, h.point(ls, 1, 3))
    assert view._sel == (out(ls, 0, 2), out(ls, 1, 3))
    assert view.selection_text() == "txt  b.txt\nc.t"
    assert published == ["txt  b.txt\nc.t"]
    assert len(updates) == 3 and view._press is None
    # Backwards it is the same text.
    clock.value += 1
    drag(h, h.point(ls, 1, 3), h.point(ls, 0, 2))
    assert view._sel == (out(ls, 1, 3), out(ls, 0, 2))
    assert published == ["txt  b.txt\nc.t"] * 2
    # A plain click publishes nothing.
    clock.value += 1
    click(h, h.point(bad))
    assert len(published) == 2 and view._sel is None


def test_a_drag_that_ends_where_it_began_selects_nothing(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    start = h.point(ls, 0, 3)
    press(h, start)
    move(h, h.point(ls, 1, 5))
    assert view._sel == (out(ls, 0, 3), out(ls, 1, 5))
    move(h, start)
    release(h, start)
    assert view._sel is None and not view.has_text_selection()
    assert view.selected == []           # and it was no click either


def test_a_move_without_the_left_button_down_does_not_drag(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    start = h.point(ls, 0, 3)
    press(h, start)
    move(h, h.point(ls, 1, 5), NO_BUTTON)
    move(h, h.point(ls, 1, 5), RIGHT)
    assert not view._press["dragging"] and view._sel is None
    release(h, start)
    assert view.selected == [ls.id]


def test_another_button_does_not_disturb_a_press(ui):
    """Only the button that began a press ends it."""
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    opened = record_menus(h)
    pasted = []
    view.paste = pasted.append
    press(h, h.point(ls))
    held = view._press
    press(h, h.point(ls), RIGHT)         # no menu in the middle of it
    press(h, h.point(ls), MIDDLE)        # and nothing pasted
    assert view._press is held and opened == [] and pasted == []
    release(h, h.point(ls), RIGHT)
    release(h, h.point(ls), MIDDLE)
    assert view._press is held and view.selected == []
    release(h, h.point(ls))              # the click it always was
    assert view._press is None and view.selected == [ls.id]


def test_a_drag_goes_on_when_another_button_is_let_go(ui):
    h, block = tall(ui)
    view = h.view
    published = []
    view._publish_selection = lambda: published.append(view.selection_text())
    view._set_scroll(10 * view.r.ch)
    view_h = view.frame().view_h
    x = PAD_LEFT + 3 * view.r.cw
    press(h, QPoint(x, view_h // 2))
    move(h, QPoint(x, view_h + 5))       # past the edge: scrolling
    assert view._drag_timer.isActive()
    chosen = view._sel
    release(h, QPoint(x, view_h + 5), RIGHT)
    assert view._drag_timer.isActive() and view._press["dragging"]
    assert view._sel == chosen and published == []
    release(h, QPoint(x, view_h + 5))
    assert not view._drag_timer.isActive() and view._press is None
    assert view._sel == chosen and published == [view.selection_text()]
    assert published[0] != ""


def test_dragging_past_an_edge_keeps_the_view_scrolling(ui):
    h, block = tall(ui)
    view = h.view
    view._set_scroll(10 * view.r.ch)
    view_h = view.frame().view_h
    x = PAD_LEFT + 3 * view.r.cw
    timer = view._drag_timer
    starts = []
    start = timer.start
    timer.start = lambda: starts.append(start())
    press(h, QPoint(x, view_h // 2))
    move(h, QPoint(x, view_h // 2 + 10))
    assert not timer.isActive()
    move(h, QPoint(x, -1))
    assert timer.isActive() and len(starts) == 1
    # Still outside: it runs on.  Starting it again with every move would
    # keep it from ever firing while the pointer moves.
    move(h, QPoint(x, -30))
    assert timer.isActive() and len(starts) == 1
    move(h, QPoint(x, 0))                # the top edge is inside
    assert not timer.isActive()
    move(h, QPoint(x, view_h + 1))
    assert timer.isActive() and len(starts) == 2
    move(h, QPoint(x, view_h))           # and so is the bottom edge
    assert not timer.isActive()
    move(h, QPoint(x, view_h + 20))
    assert timer.isActive() and len(starts) == 3
    release(h, QPoint(x, view_h + 20))
    assert not timer.isActive()


def test_the_view_scrolls_towards_a_pointer_held_outside_it(ui, pointer):
    h, block = tall(ui)
    view = h.view
    ch = view.r.ch
    view._set_scroll(40 * ch)
    view_h = view.frame().view_h
    x = PAD_LEFT + 2 * view.r.cw + 2
    timer = view._drag_timer

    def hold(y: int) -> int:
        """Keep the pointer there for one turn of the timer; how far the
        view scrolled."""
        pointer(view.mapToGlobal(QPoint(x, y)))
        before = view.scroll
        view._drag_scroll()
        return view.scroll - before

    def line_at(y: int) -> int:
        return (view.scroll + y - block.view.header_h) // ch

    press(h, QPoint(x, view_h // 2))
    anchor = view._press["pos"]
    assert anchor == out(block, line_at(view_h // 2), 2)
    move(h, QPoint(x, -5))
    assert timer.isActive()
    assert hold(-5) == -5
    assert view._sel == (anchor, out(block, line_at(0), 2))
    assert hold(-1) == -1
    assert hold(-60) == -60
    assert hold(-61) == -60              # never more than this at once
    assert hold(-400) == -60
    assert view._sel == (anchor, out(block, line_at(0), 2))
    assert timer.isActive() and not view.follow
    assert hold(view_h + 1) == 1
    assert view._sel == (anchor, out(block, line_at(view_h - 1), 2))
    assert hold(view_h + 7) == 7
    assert hold(view_h + 60) == 60
    assert hold(view_h + 61) == 60
    assert hold(view_h + 400) == 60
    assert view._sel == (anchor, out(block, line_at(view_h - 1), 2))
    assert timer.isActive()
    # Back inside, on either edge, the scrolling stops.
    chosen = view._sel
    for y in (0, view_h, view_h // 2):
        timer.start()
        assert hold(y) == 0
        assert not timer.isActive() and view._sel == chosen
    # Without a drag there is nothing to scroll for.
    release(h, QPoint(x, view_h // 2))
    timer.start()
    assert hold(-30) == 0 and not timer.isActive()
    press(h, QPoint(x, view_h // 2))     # a press alone is no drag
    timer.start()
    assert hold(-30) == 0 and not timer.isActive()
    assert view._sel == chosen


# -- the buttons of a block header -------------------------------------------

def test_the_menu_button_selects_its_block_and_opens_the_menu(ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    opened = record_menus(h)
    spot = button(h, ls, "menu")
    press(h, spot)
    assert opened == [] and view.selected == []  # not before the release
    release(h, spot + QPoint(1, 1))
    assert opened == [(ls, view.mapToGlobal(spot + QPoint(1, 1)))]
    assert view.selected == [ls.id] and view._press is None
    view.select_block(one, "toggle")
    clock.value += 1
    click(h, button(h, one, "menu"))     # one of several selected blocks
    assert opened[1][0] is one and view.selected == [one.id, ls.id]
    clock.value += 1
    click(h, button(h, bad, "menu"))     # one that is not
    assert opened[2][0] is bad and view.selected == [bad.id]
    assert view._sel is None


def test_the_jump_button_scrolls_to_the_end_of_its_block(ui):
    h, block = tall(ui)
    h.run("true", "")                    # the block's end is not the bottom
    view = h.view
    view._set_scroll(5 * view.r.ch)
    click(h, button(h, block, "jump"))
    frame = view.frame()
    assert view.scroll == frame.heights[0] - frame.view_h
    assert 0 < view.scroll < frame.max_scroll
    assert view.selected == []


def test_the_cross_on_a_filter_chip_clears_that_filter(ui, clock):
    h = ui()
    one = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\n")
    two = h.run("make again", "ok 3\r\nerror: b\r\n")
    view = h.view
    view.set_filter([one, two], FilterSpec("error"))
    view.open_filter([two])              # the bar is about the other block
    h.pump()
    assert (one.view.out_rows, two.view.out_rows) == (1, 1)
    click(h, button(h, one, "unfilter"))
    view.frame()
    assert one.filter is None and one.view.out_rows == 3
    assert two.filter is not None and two.view.out_rows == 1
    assert view.search.isVisible() and view._bar_targets == [two]
    clock.value += 1
    click(h, button(h, two, "unfilter"))
    view.frame()
    assert two.filter is None and two.view.out_rows == 2
    assert not view.search.isVisible() and view._bar_targets == []
    assert view.selected == []


def test_a_button_acts_only_when_the_release_is_on_it_too(ui, clock):
    h, block = tall(ui)
    view = h.view
    opened = record_menus(h)
    view._set_scroll(5 * view.r.ch)
    scroll = view.scroll
    jump, menu = button(h, block, "jump"), button(h, block, "menu")
    text = QPoint(PAD_LEFT + 2 * view.r.cw, view.frame().view_h // 2)
    for down, up in ((jump, menu), (menu, jump), (jump, text),
                     (menu, text)):
        clock.value += 1
        press(h, down)
        assert view._press["zone"] == "button"
        release(h, up)
        assert opened == [] and view.scroll == scroll
        assert view.selected == [] and view._press is None
    # The same button of another block is another button.
    h2 = ui()
    one, ls, bad = three_blocks(h2)
    opened = record_menus(h2)
    press(h2, button(h2, one, "menu"))
    release(h2, button(h2, ls, "menu"))
    assert opened == [] and h2.view.selected == []


def test_a_press_on_a_button_never_becomes_a_drag(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    view.select_block(ls)
    press(h, button(h, ls, "menu"))
    move(h, h.point(one, 0, 1))
    assert not view._press["dragging"]
    assert view.selected == [ls.id] and view._sel is None
    release(h, h.point(one, 0, 1))
    assert view.selected == [ls.id] and view._sel is None


def test_a_click_on_a_pinned_command_scrolls_to_the_start_of_its_block(ui):
    h = ui(rows=8)
    h.run("echo first", "first\r\n")
    block = h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    view = h.view
    view.repaint()
    spot = QPoint(PAD_LEFT + 20 * view.r.cw, HEAD_TOP + view.r.ch // 2)
    assert view._button_at(spot.x(), spot.y()) == (block, "sticky")
    bottom = view.scroll
    click(h, spot)
    assert view.scroll == view.frame().tops[1]
    assert 0 < view.scroll < bottom
    assert view.selected == []           # it is no click on the block


def test_a_drag_from_a_pinned_command_starts_in_that_command(ui):
    """The pinned header lies over output of its block.  A press on it is
    a press on the command it shows, not on the lines underneath."""
    h, block = tall(ui)
    view = h.view
    view.repaint()
    assert view.scroll > block.view.header_h
    cw, ch = view.r.cw, view.r.ch
    # Between the third and the fourth character of "seq 100".
    spot = QPoint(PAD_LEFT + 3 * cw, HEAD_TOP + ch // 2)
    assert view._button_at(spot.x(), spot.y()) == (block, "sticky")
    assert view._pinned_header_at(spot.x(), spot.y()) == (
        block, HEAD_TOP + ch // 2)
    hidden = view._position_at(spot.x(), spot.y())
    assert hidden[:2] == (block.id, 1) and hidden[2] > 0   # under it
    press(h, spot)
    assert view._press["pos"] == (block.id, 0, 0, 3)
    below = QPoint(PAD_LEFT + 2 * cw,
                   block.view.header_h + 2 * ch + ch // 2)
    move(h, below)
    release(h, below)
    head = view._position_at(below.x(), below.y())
    assert view._sel == ((block.id, 0, 0, 3), head)
    text = view.selection_text()
    assert text.startswith(" 100\n0\n1\n") and text.endswith(
        "\n" + block.lines()[head[2]][:2])


def test_the_pinned_header_is_found_only_where_it_is(ui):
    h, block = tall(ui)
    view = h.view
    view.repaint()
    header_h = block.view.header_h
    x = PAD_LEFT + 20 * view.r.cw
    assert view._pinned_header_at(x, 0) == (block, 0)
    # The line drawn under it is its last row of pixels.
    assert view._pinned_header_at(x, header_h) == (block, header_h)
    assert view._pinned_header_at(x, header_h + 1) == (None, 0)
    assert view._pinned_header_at(x, -1) == (None, 0)
    # A header that is where it belongs is not pinned.
    view._set_scroll(0)
    view.repaint()
    assert view._pinned_header_at(x, HEAD_TOP) == (None, 0)


def test_a_pinned_header_on_its_way_out_is_still_found(ui):
    """With little of its block left in view the pinned header is drawn
    partly above the view; a point on it is measured from its real top."""
    h = ui(rows=8)
    first = h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    h.run("seq 50", "".join(f"{i}\r\n" for i in range(50)))
    view = h.view
    header_h = first.view.header_h
    view._set_scroll(view.frame().tops[1] - 10)  # ten pixels of it are left
    view.repaint()
    top = 10 - header_h - 1                      # where its header is drawn
    assert top < 0
    x = PAD_LEFT + 20 * view.r.cw
    assert view._pinned_header_at(x, 0) == (first, -top)
    assert view._pinned_header_at(x, 5) == (first, 5 - top)
    assert view._pinned_header_at(x, 9) == (first, header_h)
    assert view._pinned_header_at(x, 10) == (None, 0)


# -- the input area and the full-screen terminal -----------------------------

def test_a_press_in_the_input_area_gives_the_keyboard_to_the_shell(
        ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.feed(b"echo typed text")
    h.pump()
    view = h.view
    drag(h, h.point(ls, 0, 0), h.point(ls, 0, 5))
    assert view.selection_text() == "a.txt"
    updates = count_updates(h)
    press(h, input_point(h, 0, 2))
    assert view._sel is None and view._gsel is None
    assert view._press["zone"] == "input"
    assert view._press["cell"] == (0, 2)
    assert view._press["screen"] is h.session.input
    assert len(updates) == 1
    release(h, input_point(h, 0, 2))
    assert not view.has_text_selection() and view._press is None
    assert len(updates) == 2
    clock.value += 1
    view.select_block(ls)
    press(h, input_point(h, 0, 2))
    assert view.selected == []
    assert h.sent == []


def test_dragging_in_the_input_area_selects_whole_cells(ui):
    h = ui()
    h.session.feed(b"echo typed text")
    h.pump()
    view = h.view
    screen = h.session.input
    published = []
    view._publish_selection = lambda: published.append(view.selection_text())
    press(h, input_point(h, 0, 2))
    updates = count_updates(h)
    move(h, input_point(h, 0, 5))
    assert view._gsel == (screen, (0, 2), (0, 6))
    assert view.selection_text() == "echo" and len(updates) == 1
    move(h, input_point(h, 0, 2))        # back on the first cell: just it
    assert view._gsel == (screen, (0, 2), (0, 3))
    assert view.selection_text() == "e" and len(updates) == 2
    move(h, input_point(h, 0, 0))        # backwards, the first cell stays
    assert view._gsel == (screen, (0, 3), (0, 0))
    assert view.selection_text() == "% e" and len(updates) == 3
    release(h, input_point(h, 0, 0))
    assert published == ["% e"] and len(updates) == 4
    assert view._gsel == (screen, (0, 3), (0, 0)) and view._sel is None


def test_a_small_move_in_the_input_area_is_not_a_drag(ui):
    h = ui()
    h.session.feed(b"echo typed text")
    h.pump()
    view = h.view
    start = input_point(h, 0, 2)
    press(h, start)
    move(h, start + QPoint(3, 0))
    assert view._gsel is None and not view._press["dragging"]
    release(h, start + QPoint(3, 0))
    assert not view.has_text_selection() and view._press is None


def test_a_press_anywhere_drops_a_selection_in_the_input_area(ui, clock):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.feed(b"echo typed text")
    h.pump()
    view = h.view
    drag(h, input_point(h, 0, 2), input_point(h, 0, 5))
    assert view.selection_text() == "echo"
    press(h, input_point(h, 0, 9))       # in the input area again
    assert view._gsel is None
    release(h, input_point(h, 0, 9))
    clock.value += 1
    drag(h, input_point(h, 0, 2), input_point(h, 0, 5))
    assert view.selection_text() == "echo"
    press(h, h.point(ls, 0, 1))          # on a block
    assert view._gsel is None
    release(h, h.point(ls, 0, 1))
    assert view.selected == [ls.id] and not view.has_text_selection()


def test_dragging_in_a_full_screen_program_selects_its_cells(ui):
    h = ui()
    full_screen(h, b"first row of text\r\nsecond row")
    view = h.view
    drag(h, cell_point(h, 0, 6), cell_point(h, 1, 5))
    assert view._gsel == (h.session.term.screen, (0, 6), (1, 6))
    assert view.selection_text() == "row of text\nsecond"
    assert h.sent == []


def test_a_drag_does_not_reach_from_one_screen_into_another(ui):
    h = ui()
    h.session.feed(b"echo typed")
    h.pump()
    view = h.view
    press(h, input_point(h, 0, 2))
    full_screen(h, b"page")              # a program takes the view over
    move(h, cell_point(h, 0, 3))
    assert view._press["dragging"] and view._gsel is None
    assert not view.has_text_selection()


# -- reporting the mouse to a program ----------------------------------------

def test_a_program_that_asked_for_the_mouse_gets_presses_and_releases(ui):
    h = ui()
    full_screen(h)
    view = h.view
    spot = cell_point(h, 1, 3)           # column 4, row 2 for the program

    def clicked(button_=LEFT, mods=NONE) -> list[bytes]:
        h.sent.clear()
        click(h, spot, button_, mods)
        return list(h.sent)

    assert clicked() == []               # it has not asked yet
    h.session.feed(b"\x1b[?1000h")
    assert clicked() == [b'\x1b[M $"', b'\x1b[M#$"']
    assert view._press is None and not view.has_text_selection()
    assert clicked(MIDDLE) == [b'\x1b[M!$"', b'\x1b[M#$"']
    assert clicked(RIGHT) == [b'\x1b[M"$"', b'\x1b[M#$"']
    assert clicked(BACK) == []           # no such button in the protocol
    assert clicked(mods=ALT) == [b'\x1b[M($"', b'\x1b[M+$"']
    assert clicked(mods=CTRL) == [b'\x1b[M0$"', b'\x1b[M3$"']
    assert clicked(mods=CTRL | ALT) == [b'\x1b[M8$"', b'\x1b[M;$"']
    h.session.feed(b"\x1b[?1006h")
    assert clicked() == [b"\x1b[<0;4;2M", b"\x1b[<0;4;2m"]
    assert clicked(MIDDLE) == [b"\x1b[<1;4;2M", b"\x1b[<1;4;2m"]
    assert clicked(RIGHT) == [b"\x1b[<2;4;2M", b"\x1b[<2;4;2m"]
    assert clicked(mods=CTRL | ALT) == [b"\x1b[<24;4;2M", b"\x1b[<24;4;2m"]
    h.session.feed(b"\x1b[?9h")          # presses only
    assert clicked() == [b"\x1b[<0;4;2M"]
    h.session.feed(b"\x1b[?9l")
    assert clicked() == []
    assert h.session.term.mouse_mode == 0


def test_the_view_knows_which_mouse_events_were_the_programs(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    kinds = {"press": QEvent.Type.MouseButtonPress,
             "move": QEvent.Type.MouseMove,
             "release": QEvent.Type.MouseButtonRelease}
    inside = input_point(h, 0, 7)

    def taken(kind: str, point: QPoint = inside, button=LEFT, mods=NONE):
        h.sent.clear()
        answer = view._mouse_report(mouse_event(
            h, kinds[kind], point, button, NO_BUTTON, mods), kind)
        return answer, list(h.sent)

    assert taken("press") == (False, [])                 # nobody asked
    h.session.feed(b"\x1b[?1000h\x1b[?1006h")
    assert taken("press") == (True, [b"\x1b[<0;8;1M"])
    assert taken("release") == (True, [b"\x1b[<0;8;1m"])
    assert taken("move") == (True, [])           # clicks only, but taken
    assert taken("press", button=BACK) == (True, [])
    assert taken("press", mods=SHIFT) == (False, [])
    assert taken("press", h.point(ls)) == (False, [])    # on a block
    h.session.feed(b"\x1b[?9h")
    assert taken("press") == (True, [b"\x1b[<0;8;1M"])
    assert taken("release") == (True, [])


def test_motion_is_reported_only_in_the_modes_that_ask_for_it(ui):
    h = ui()
    full_screen(h)
    h.session.feed(b"\x1b[?1006h")
    spot, other = cell_point(h, 1, 3), cell_point(h, 2, 5)

    def moved(buttons=NO_BUTTON, mods=NONE) -> list[bytes]:
        h.sent.clear()
        move(h, other, buttons, mods)
        return list(h.sent)

    h.session.feed(b"\x1b[?1000h")       # clicks only
    press(h, spot)
    assert moved(LEFT) == [] and moved() == []
    h.session.feed(b"\x1b[?1002h")       # and motion with a button down
    assert moved() == []
    assert moved(LEFT) == [b"\x1b[<32;6;3M"]
    assert moved(MIDDLE) == [b"\x1b[<33;6;3M"]
    assert moved(RIGHT) == [b"\x1b[<34;6;3M"]
    assert moved(LEFT | RIGHT) == [b"\x1b[<32;6;3M"]
    assert moved(LEFT, CTRL | ALT) == [b"\x1b[<56;6;3M"]
    h.session.feed(b"\x1b[?1003h")       # and any motion at all
    assert moved() == [b"\x1b[<35;6;3M"]
    assert moved(RIGHT) == [b"\x1b[<34;6;3M"]
    h.session.feed(b"\x1b[?1006l")
    assert moved() == [b"\x1b[MC&#"]
    assert h.view._gsel is None and h.view._press is None


def test_shift_keeps_the_mouse_for_selecting_text(ui):
    h = ui()
    full_screen(h, b"first row of text\r\nsecond row")
    h.session.feed(b"\x1b[?1003h\x1b[?1006h")
    view = h.view
    drag(h, cell_point(h, 0, 6), cell_point(h, 0, 8), mods=SHIFT)
    assert view.selection_text() == "row"
    assert hover(h, cell_point(h, 1, 1), SHIFT) == (None, IBEAM)
    assert h.sent == []


def test_at_the_prompt_only_the_input_area_reports_the_mouse(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.feed(b"\x1b[?1000h\x1b[?1006h")
    view = h.view
    click(h, h.point(ls))
    assert h.sent == [] and view.selected == [ls.id]
    assert hover(h, h.point(ls, 1, 2)) == (ls.id, IBEAM)
    click(h, input_point(h, 0, 7))
    assert h.sent == [b"\x1b[<0;8;1M", b"\x1b[<0;8;1m"]
    assert view.selected == [ls.id] and view._press is None


def test_cells_too_far_out_for_the_old_encoding_are_not_reported(ui):
    h = ui()
    full_screen(h)
    widen(h, 230)
    view = h.view
    h.session.feed(b"\x1b[?1000h")
    click(h, cell_point(h, 1, 120))
    assert h.sent == [b'\x1b[M \x99"', b'\x1b[M#\x99"']
    h.sent.clear()
    click(h, cell_point(h, 1, 223))      # column 224 does not fit a byte
    wheel(h, 120, at=cell_point(h, 1, 223))
    assert h.sent == []
    assert view._press is None and view._gsel is None
    h.session.feed(b"\x1b[?1005h")       # the wider encoding reaches it
    click(h, cell_point(h, 1, 120))
    assert h.sent == [b'\x1b[M \xc2\x99"', b'\x1b[M#\xc2\x99"']
    h.sent.clear()
    click(h, cell_point(h, 1, 223))
    wheel(h, -120, at=cell_point(h, 1, 223))
    assert h.sent == [b'\x1b[M \xc4\x80"', b'\x1b[M#\xc4\x80"',
                      b'\x1b[Ma\xc4\x80"' * 3]


# -- hovering ----------------------------------------------------------------

def test_hovering_marks_the_block_and_shapes_the_pointer(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    menu = button(h, ls, "menu")
    updates = count_updates(h)
    assert hover(h, h.point(ls)) == (ls.id, ARROW)       # its header
    assert len(updates) == 1
    assert hover(h, h.point(ls, 1, 2)) == (ls.id, IBEAM)  # its output
    assert hover(h, menu) == (ls.id, HAND)
    assert len(updates) == 1             # still the same block
    assert hover(h, h.point(one, 0, 1)) == (one.id, IBEAM)
    assert hover(h, beside(h)) == (None, ARROW)
    assert len(updates) == 3
    assert hover(h, input_point(h, 0, 4)) == (None, IBEAM)
    assert len(updates) == 3
    # The first pixel row of the output is text, the one above it is not.
    top = view.frame().tops[1] - view.scroll + ls.view.header_h
    x = h.point(ls).x()
    assert hover(h, QPoint(x, top)) == (ls.id, IBEAM)
    assert hover(h, QPoint(x, top - 1)) == (ls.id, ARROW)


def test_every_header_button_shows_a_pointing_hand(ui):
    h, block = tall(ui)
    view = h.view
    view.set_filter([block], FilterSpec("1"))
    h.pump()
    assert view.frame().max_scroll > 0
    for name in ("menu", "jump", "unfilter"):
        assert hover(h, button(h, block, name)) == (block.id, HAND), name
    # The rest of the pinned header belongs to the block as well.
    spot = QPoint(PAD_LEFT + 20 * view.r.cw, HEAD_TOP + view.r.ch // 2)
    assert view._button_at(spot.x(), spot.y()) == (block, "sticky")
    view._hover = None
    move(h, spot, NO_BUTTON)
    assert view._hover == block.id
    # It is a header, although output of the block lies under it: no
    # pointer for text there.  Right below it the output begins.
    assert hover(h, spot) == (block.id, ARROW)
    below = QPoint(spot.x(), block.view.header_h + view.r.ch)
    assert view._button_at(below.x(), below.y()) == (None, None)
    assert hover(h, below) == (block.id, IBEAM)


def test_leaving_the_view_unmarks_the_block(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    assert hover(h, h.point(ls)) == (ls.id, ARROW)
    updates = count_updates(h)
    view.leaveEvent(QEvent(QEvent.Type.Leave))
    assert view._hover is None and len(updates) == 1
    view.leaveEvent(QEvent(QEvent.Type.Leave))
    assert view._hover is None and len(updates) == 1


def test_a_full_screen_program_is_text_all_over(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    spot = h.point(ls)
    assert hover(h, spot) == (ls.id, ARROW)
    full_screen(h)
    assert hover(h, spot) == (None, IBEAM)
    # Once the program takes the mouse the pointer is left as it is.
    view.setCursor(ARROW)
    h.session.feed(b"\x1b[?1000h")
    assert hover(h, spot) == (None, ARROW)
    assert h.sent == []


# -- the wheel ---------------------------------------------------------------

def test_the_wheel_scrolls_the_block_list_three_rows_a_notch(ui):
    h, block = tall(ui)
    view = h.view
    ch = view.r.ch
    bottom = view.scroll
    wheel(h, 120)
    assert view.scroll == bottom - 3 * ch and not view.follow
    wheel(h, 240)
    assert view.scroll == bottom - 9 * ch
    wheel(h, -120)
    assert view.scroll == bottom - 6 * ch
    wheel(h, 120, pixels=25)             # a touchpad says exactly how far
    assert view.scroll == bottom - 6 * ch - 25
    wheel(h, -120, pixels=-10)
    assert view.scroll == bottom - 6 * ch - 15
    updates = count_updates(h)
    wheel(h, 0)
    assert view.scroll == bottom - 6 * ch - 15 and updates == []
    wheel(h, -120 * 40)
    assert view.scroll == bottom and view.follow
    assert h.sent == []


def test_the_wheel_becomes_arrow_keys_for_a_full_screen_program(ui):
    h, block = tall(ui)
    view = h.view
    full_screen(h)
    scroll = view.scroll

    def turned(angle: int, pixels: int = 0, mods=NONE) -> list[bytes]:
        h.sent.clear()
        wheel(h, angle, pixels, mods, at=cell_point(h, 1, 3))
        return list(h.sent)

    assert turned(120) == [b"\x1b[A" * 3]
    assert turned(-120) == [b"\x1b[B" * 3]
    assert turned(240) == [b"\x1b[A" * 6]
    assert turned(12) == [b"\x1b[A"]     # the smallest turn is one key
    assert turned(-12) == [b"\x1b[B"]
    assert turned(0) == [] and turned(0, pixels=30) == []
    h.session.feed(b"\x1b[?1h")
    assert turned(120) == [b"\x1bOA" * 3]
    h.session.feed(b"\x1b[?1l\x1b[?1000h")       # the program wants it
    assert turned(120) == [b'\x1b[M`$"' * 3]
    assert turned(-120) == [b'\x1b[Ma$"' * 3]
    assert turned(-12) == [b'\x1b[Ma$"']
    assert turned(120, mods=SHIFT) == [b"\x1b[A" * 3]
    h.session.feed(b"\x1b[?1006h")
    assert turned(240) == [b"\x1b[<64;4;2M" * 6]
    assert turned(-120) == [b"\x1b[<65;4;2M" * 3]
    h.session.feed(b"\x1b[?1007l")       # no arrow keys for the wheel
    assert turned(120, mods=SHIFT) == []
    assert turned(120) == [b"\x1b[<64;4;2M" * 3]
    h.session.feed(b"\x1b[?1000l")
    assert turned(120) == [] and turned(-120) == []
    assert view.scroll == scroll         # the block list stays where it was


# -- the context menu and the tooltip ----------------------------------------

def test_the_context_menu_event_is_taken(ui):
    h = ui()
    spot = QPoint(PAD_LEFT, h.view.r.ch)
    event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, spot,
                              h.view.mapToGlobal(spot))
    event.ignore()
    h.view.contextMenuEvent(event)
    assert event.isAccepted()


@pytest.fixture
def tips(monkeypatch):
    """Note the tooltips shown and hidden instead of showing them."""
    seen = SimpleNamespace(shown=[], hidden=0)

    def hide() -> None:
        seen.hidden += 1

    monkeypatch.setattr(view_module.QToolTip, "showText",
                        lambda *args: seen.shown.append(args))
    monkeypatch.setattr(view_module.QToolTip, "hideText", hide)
    return seen


def tooltip(h: Harness, point: QPoint) -> None:
    h.view._tooltip(QHelpEvent(QEvent.Type.ToolTip, point,
                               h.view.mapToGlobal(point)))


def started(block) -> str:
    return "Started " + datetime.fromtimestamp(block.started).strftime(
        "%Y-%m-%d %H:%M:%S")


def test_a_header_tooltip_tells_how_the_command_went(ui, tips):
    h = ui()
    block = h.run("make", "", status=2)
    view = h.view
    block.started = 1_700_000_000.0
    block.duration = 75.0
    block.branch = "main"
    point = h.point(block)
    tooltip(h, point)
    assert tips.shown == [(
        view.mapToGlobal(point),
        started(block) + "\nTook 1m 15s\nExit code 2\n/work  (main)", view)]
    assert started(block).startswith("Started 2023-11-1")
    block.branch = ""
    block.duration = None
    tooltip(h, point)
    assert tips.shown[-1][1] == started(block) + "\nExit code 2\n/work"
    block.exit_code = None
    block.cwd = ""
    tooltip(h, point)
    assert tips.shown[-1][1] == started(block)
    block.not_run = True
    block.exit_code = 1
    tooltip(h, point)
    assert tips.shown[-1][1] == \
        started(block) + "\nNot run: the shell rejected it"
    assert tips.hidden == 0


def test_a_header_tooltip_tells_how_long_a_command_has_been_running(
        ui, tips):
    h = ui()
    h.session.feed(submit("sleep 100"))
    h.pump()
    block = h.session.blocks[-1]
    # Half a minute of slack either way: the test may be slow, not wrong.
    block._t0 = time.monotonic() - (2 * 3600 + 30 * 60 + 30)
    tooltip(h, h.point(block))
    assert [args[1] for args in tips.shown] == [
        started(block) + "\nRunning for 2h 30m\n/work"]


def test_background_output_only_tells_when_it_started(ui, tips):
    h = ui()
    h.session.feed(b"\r\r\n" + mark("T") + b"[1]  + done  sleep\r\n\x1b[J"
                   + mark("A") + b"% \x1b[K")
    h.pump()
    block = h.session.blocks[-1]
    assert block.kind == BACKGROUND
    tooltip(h, h.point(block))
    assert [args[1] for args in tips.shown] == [started(block)]


def test_a_full_screen_program_has_no_block_tooltips(ui, tips):
    h = ui()
    h.run("echo one", "one\r\n")
    full_screen(h)
    view = h.view
    frame = view.frame()                 # the list is laid out behind it
    y = int(frame.tops[0] - view.scroll) + HEAD_TOP
    _, block, inside = view._block_under(y)
    assert block is not None and inside < block.view.header_h
    tooltip(h, QPoint(PAD_LEFT + 2 * view.r.cw, y))
    assert tips.shown == [] and tips.hidden == 1


def test_there_is_no_tooltip_off_the_block_headers(ui, tips):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    top = view.frame().tops[1] - view.scroll
    x = h.point(ls).x()
    tooltip(h, QPoint(x, top + ls.view.header_h))        # its output
    tooltip(h, beside(h))
    tooltip(h, input_point(h, 0, 2))
    assert tips.shown == [] and tips.hidden == 3
    tooltip(h, QPoint(x, top + ls.view.header_h - 1))    # the header's end
    tooltip(h, QPoint(x, top))                           # and its start
    assert len(tips.shown) == 2 and tips.hidden == 3
