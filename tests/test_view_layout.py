"""How the view lays the blocks out, scrolls them, and finds the text
under a point of the window."""

from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QPoint, QRect

from conftest import COLS, NONCE, mark, prompt, pump_qt, submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec, Session
from jupyterminal.session.block import Block
from jupyterminal.ui import view as view_module
from jupyterminal.ui.view import PAD_LEFT, Geo, TerminalView

MENU = ("menu", "", 2)
JUMP = ("jump", "", 3)


class FakePty:
    """Stands in for the shell's pseudo-terminal: it notes its sizes."""

    def __init__(self) -> None:
        self.resized: list[tuple] = []

    def resize(self, cols: int, rows: int, width: int, height: int) -> None:
        self.resized.append((cols, rows, width, height))


def spy(target, name: str) -> list:
    """Replace a method of one object by a recorder of its calls."""
    calls: list = []
    setattr(target, name, lambda *args: calls.append(args))
    return calls


def clock(monkeypatch, now: float) -> types.SimpleNamespace:
    """Replace the view's clock by one that shows ``now`` until told
    otherwise."""
    fake = types.SimpleNamespace(now=now)
    fake.monotonic = lambda: fake.now
    monkeypatch.setattr(view_module, "time", fake)
    return fake


def running(h, command: str, output: str = ""):
    """Start a command and let it print; it does not finish."""
    h.session.feed(submit(command) + output.encode())
    return h.session.blocks[-1]


def lines(*texts: str) -> str:
    return "".join(text + "\r\n" for text in texts)


def numbered(count: int) -> str:
    return "".join(f"line {i}\r\n" for i in range(count))


def rows_of(geo: Geo) -> tuple[int, int, int, int]:
    return geo.frozen_rows, geo.open_rows, geo.live_rows, geo.out_rows


def three_blocks(h):
    return [h.run("echo one", "one\r\n"),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"),
            h.run("false", "", status=1)]


def clear_display(h) -> None:
    """What ``clear`` at the prompt sends."""
    h.session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")


@pytest.fixture
def boot(qapp):
    """A view whose session has not reached its first prompt: what the
    shell prints now lands in a block without a command."""
    session = Session(COLS, 4, nonce=NONCE)
    view = TerminalView(session, Config())
    view.resize(*view.size_for(COLS, 4))
    view.show()
    yield view
    view.close()


# -- the window and the grid --------------------------------------------------

def test_the_spacing_the_layout_is_built_on():
    assert [view_module.PAD_LEFT, view_module.BAR_W, view_module.PAD_RIGHT,
            view_module.SCROLL_W] == [14, 3, 8, 12]
    assert [view_module.HEAD_TOP, view_module.HEAD_BOTTOM,
            view_module.BLOCK_BOTTOM] == [5, 5, 6]
    assert [view_module.IN_TOP, view_module.IN_BOTTOM] == [8, 8]
    assert view_module.MAX_CMD_ROWS == 6
    assert view_module.DRAG_THRESHOLD == 4


def test_a_grid_and_the_size_of_its_window_convert_both_ways(ui):
    view = ui().view
    cw, ch = view.r.cw, view.r.ch
    width, height = view.size_for(40, 12)
    assert (width, height) == (40 * cw + 34, 13 * ch + 18)
    assert view.grid_for(width, height) == (40, 12)
    assert view.grid_for(width - 1, height - 1) == (39, 11)
    assert view.grid_for(width + cw - 1, height + ch - 1) == (40, 12)
    assert view.grid_for(width + cw, height + ch) == (41, 13)
    assert [type(n) for n in view.grid_for(width + 1, height + 1)] == \
        [int, int]


def test_a_window_is_never_taken_for_less_than_ten_by_three(ui):
    view = ui().view
    assert view.grid_for(0, 0) == (10, 3)
    assert view.grid_for(*view.size_for(9, 2)) == (10, 3)
    assert view.grid_for(*view.size_for(10, 3)) == (10, 3)
    assert view.grid_for(*view.size_for(11, 4)) == (11, 4)


def test_the_scrollbar_keeps_to_the_right_edge(ui):
    h = ui()
    view = h.view
    assert view.bar.geometry() == QRect(view.width() - 12, 0, 12,
                                        view.height())
    view.resize(view.width() + 50, view.height() + 30)
    assert view.bar.geometry() == QRect(view.width() - 12, 0, 12,
                                        view.height())


def test_a_resize_within_one_cell_keeps_the_grid(ui):
    h = ui()
    view = h.view
    before = view._ui
    view.resize(view.width() + 1, view.height() + 1)
    assert (view.cols, view.rows) == (COLS, 12)
    assert (h.session.cols, h.session.rows) == (COLS, 12)
    assert view._ui == before + 1                # the frame is laid out anew
    assert not view._resize_timer.isActive()


def test_with_a_shell_a_resize_within_one_cell_starts_nothing(ui):
    h = ui()
    view = h.view
    pty = h.session.pty = FakePty()
    view.resize(view.width() + 1, view.height() + 1)
    assert not view._resize_timer.isActive() and pty.resized == []


def test_without_a_shell_a_new_grid_is_applied_at_once(ui):
    h = ui()
    view = h.view
    before = view._ui
    view.resize(*view.size_for(60, 15))
    assert (view.cols, view.rows) == (60, 15)
    assert (h.session.cols, h.session.rows) == (60, 15)
    assert not view._resize_timer.isActive()
    assert view._ui == before + 2                # the grid, then the frame


def test_with_a_shell_a_new_grid_waits_until_the_resize_settles(ui):
    h = ui()
    view = h.view
    cw, ch = view.r.cw, view.r.ch
    pty = h.session.pty = FakePty()
    view.resize(*view.size_for(60, 15))
    assert view._resize_timer.isActive()
    assert (view.cols, view.rows) == (COLS, 12) and pty.resized == []
    assert pump_qt(h.app, lambda: pty.resized, 5)
    assert pty.resized == [(60, 15, 60 * cw, 15 * ch)]
    assert (view.cols, view.rows) == (60, 15)
    assert (h.session.cols, h.session.rows) == (60, 15)


def test_applying_the_grid_tells_the_terminal_once(ui):
    h = ui()
    view = h.view
    cw, ch = view.r.cw, view.r.ch
    pty = h.session.pty = FakePty()
    view.resize(*view.size_for(50, 9))           # pending, not applied
    h.session.term.cell_px = (1, 1)
    updates = spy(view, "update")
    before = view._ui
    view._apply_grid()
    assert (view.cols, view.rows) == (50, 9)
    assert h.session.term.cell_px == (cw, ch)
    assert pty.resized == [(50, 9, 50 * cw, 9 * ch)]
    assert view._ui == before + 1 and updates == [()]
    view._apply_grid()                           # nothing has changed since
    assert pty.resized == [(50, 9, 50 * cw, 9 * ch)]
    assert view._ui == before + 1 and updates == [()]


def test_a_hidden_view_applies_a_new_grid_at_once_even_with_a_shell(ui):
    h = ui()
    view = h.view
    pty = h.session.pty = FakePty()
    view.hide()
    view.resize(*view.size_for(60, 15))          # not announced while hidden
    assert pty.resized == []
    view.resizeEvent(None)
    assert [size[:2] for size in pty.resized] == [(60, 15)]
    assert not view._resize_timer.isActive()


def test_the_search_bar_sits_in_the_top_right_corner(ui):
    h = ui()
    view = h.view
    block = h.run("make", "error\r\n")
    bar = view.search
    bar.move(77, 66)
    view._place_bar()                            # hidden: left where it is
    assert bar.pos() == QPoint(77, 66)
    view.resize(1000, view.height())
    view.open_find(block)
    assert bar.pos() == QPoint(1000 - 12 - bar.width() - 10, 8)
    view.resize(1100, view.height())
    assert bar.pos() == QPoint(1100 - 12 - bar.width() - 10, 8)
    hint = bar.sizeHint()
    bar.resize(hint.width() + 31, hint.height() + 7)
    view._place_bar()
    assert bar.size() == hint                    # back to its own size
    view.resize(bar.width(), view.height())      # too narrow for it
    assert bar.pos() == QPoint(4, 8)


# -- what a header shows ------------------------------------------------------

def test_a_plain_finished_command_shows_only_its_buttons(ui):
    h = ui()
    block = h.run("true", "")
    assert h.view._header_items(block) == [MENU, JUMP]


def test_a_running_command_shows_a_spinner_and_then_its_time(ui, monkeypatch):
    h = ui()
    block = running(h, "make")
    monkeypatch.setattr(Block, "elapsed", lambda self: 0.99)
    assert h.view._header_items(block) == [MENU, JUMP, ("spinner", "", 3)]
    monkeypatch.setattr(Block, "elapsed", lambda self: 1.0)
    assert h.view._header_items(block) == [MENU, JUMP,
                                           ("spinner", "1.0s", 7)]
    monkeypatch.setattr(Block, "elapsed", lambda self: 75.0)
    assert h.view._header_items(block) == [MENU, JUMP,
                                           ("spinner", "1m 15s", 9)]


def test_a_finished_command_shows_how_it_ended_and_how_long_it_took(ui):
    h = ui()
    view = h.view
    block = h.run("make", "", status=2)
    assert view._header_items(block) == [MENU, JUMP, ("fail", "exit 2", 7)]
    block.duration = 1.0                         # not worth mentioning
    assert view._header_items(block) == [MENU, JUMP, ("fail", "exit 2", 7)]
    block.duration = 1.5
    assert view._header_items(block) == [MENU, JUMP, ("fail", "exit 2", 7),
                                         ("dim", "1.5s", 5)]
    block.duration = 75
    block.exit_code = 130
    assert view._header_items(block) == [MENU, JUMP, ("fail", "exit 130", 9),
                                         ("dim", "1m 15s", 7)]
    block.exit_code = 0
    assert view._header_items(block) == [MENU, JUMP, ("dim", "1m 15s", 7)]
    block.exit_code = None                       # the session ended first
    assert view._header_items(block) == [MENU, JUMP, ("dim", "1m 15s", 7)]


def test_a_command_the_shell_turned_down_says_so(ui):
    h = ui()
    block = h.run("fi", "", status=1)
    block.not_run = True
    assert h.view._header_items(block) == [MENU, JUMP, ("dim", "not run", 8)]
    block.duration = 2.0
    assert h.view._header_items(block) == [MENU, JUMP, ("dim", "not run", 8),
                                           ("dim", "2.0s", 5)]


def test_output_without_a_command_shows_neither_time_nor_status(boot):
    view = boot
    view.session.feed(b"Last login\r\n")
    block, = view.session.blocks
    assert block.running and not block.is_command
    view.cols = 56
    assert view._header_items(block) == [MENU, JUMP]
    view.session.feed(prompt())
    block.exit_code = 1
    block.duration = 9.0
    block.cwd = "/work"
    assert not block.running
    assert view._header_items(block) == [MENU, JUMP]


def test_chips_tell_what_is_special_about_a_block(ui):
    h = ui()
    view = h.view
    block = h.run("seq 4", lines("1", "2", "3", "4"))
    block.fullscreen = True
    assert view._header_items(block)[2:] == [("chip", "full-screen", 14)]
    block.fullscreen = False
    block.filter = FilterSpec("3")
    assert view._header_items(block)[2:] == [("filter", "filter 4/4", 15)]
    block.visible = [2]
    assert view._header_items(block)[2:] == [("filter", "filter 1/4", 15)]
    block.filter = None
    block.collapsed = True
    assert view._header_items(block)[2:] == [("chip", "4 lines hidden", 17)]
    block.collapsed = False
    block.store.dropped = 7
    assert view._header_items(block)[2:] == \
        [("chip", "7 earlier lines dropped", 26)]


def test_one_hidden_line_is_not_called_lines(ui):
    h = ui()
    view = h.view
    block = h.run("echo", "one\r\n")
    block.collapsed = True
    assert view._header_items(block)[2:] == [("chip", "1 line hidden", 16)]
    empty = h.run("true", "")
    empty.collapsed = True
    assert view._header_items(empty)[2:] == [("chip", "0 lines hidden", 17)]


def test_everything_a_header_can_show_comes_in_one_order(ui):
    h = ui()
    view = h.view
    block = h.run("less log", lines("a", "b"), status=1)
    block.duration = 4.0
    block.fullscreen = True
    block.filter = FilterSpec("a")
    block.visible = [0]
    block.collapsed = True
    block.store.dropped = 3
    view.cols = 60
    assert view._header_items(block) == [
        MENU, JUMP, ("fail", "exit 1", 7), ("dim", "4.0s", 5),
        ("chip", "full-screen", 14), ("filter", "filter 1/2", 15),
        ("chip", "2 lines hidden", 17),
        ("chip", "3 earlier lines dropped", 26), ("context", "/work", 6)]


def test_a_wide_view_shows_where_a_command_ran(ui, monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    h = ui()
    view = h.view
    block = h.run("true", "")
    assert block.cwd == "/work"
    view.cols = 55
    assert view._header_items(block) == [MENU, JUMP]
    view.cols = 56
    assert view._header_items(block) == [MENU, JUMP, ("context", "/work", 6)]
    block.branch = "main"
    assert view._header_items(block)[2:] == [("context", "/work (main)", 13)]
    block.branch = ""
    block.cwd = "/home/u/src"
    assert view._header_items(block)[2:] == [("context", "~/src", 6)]
    block.cwd = ""
    assert view._header_items(block) == [MENU, JUMP]


def test_a_long_directory_keeps_its_end(ui):
    h = ui()
    view = h.view
    block = h.run("true", "")
    view.cols = 56                               # room for 56 // 3 cells
    block.cwd = "/a/b/c/d/e/f/g/h/i"
    assert len(block.cwd) == 18
    assert view._header_items(block)[2:] == [("context", block.cwd, 19)]
    block.cwd = "/a/b/c/d/e/f/g/h/ij"
    assert view._header_items(block)[2:] == \
        [("context", "…/b/c/d/e/f/g/h/ij", 19)]
    view.cols = 60                               # one more cell is enough
    assert view._header_items(block)[2:] == [("context", block.cwd, 20)]


# -- measuring a block --------------------------------------------------------

def test_a_block_is_as_tall_as_its_header_and_its_rows(ui):
    h = ui()
    view = h.view
    ch = view.r.ch
    block = h.run("ls", lines("a", "b"))
    geo = block.view
    assert isinstance(geo, Geo)
    assert geo.cmd_lines == ["ls"] and geo.cmd_rows == [(0, 0, 2)]
    assert geo.cmd_truncated is False and geo.items == [MENU, JUMP]
    assert geo.header_h == 5 + ch + 5
    assert rows_of(geo) == (2, 0, 0, 2) and geo.live_shown is None
    assert geo.height == geo.header_h + 2 * ch + 6 + 1
    quiet = h.run("true", "")
    assert rows_of(quiet.view) == (0, 0, 0, 0)
    assert quiet.view.height == quiet.view.header_h + 1


def test_a_long_command_wraps_around_what_its_header_shows(ui):
    h = ui()
    ch = h.view.r.ch
    command = "echo " + "x" * 55                 # 60 characters
    block = h.run(command, "")
    assert block.view.cmd_rows == [(0, 0, 34), (0, 34, 60)]    # 40 - 6
    assert block.view.header_h == 10 + 2 * ch
    failed = h.run(command, "", status=1)        # "exit 1" takes 7 cells
    assert failed.view.cmd_rows == [(0, 0, 27), (0, 27, 60)]


def test_a_command_of_several_lines_keeps_them(ui):
    h = ui()
    ch = h.view.r.ch
    h.session.feed(submit("for i in 1 2%0Ado echo $i%0Adone", typed="x")
                   + prompt())
    block = h.session.blocks[-1]
    h.view.frame()
    geo = block.view
    assert geo.cmd_lines == ["for i in 1 2", "do echo $i", "done"]
    assert geo.cmd_rows == [(0, 0, 12), (1, 0, 10), (2, 0, 4)]
    assert geo.cmd_truncated is False
    assert geo.header_h == 10 + 3 * ch


def test_a_header_shows_at_most_six_rows_of_a_command(ui):
    h = ui()
    ch = h.view.r.ch
    command = "%0A".join(f"line{i}" for i in range(1, 9))
    h.session.feed(submit(command, typed="x") + prompt())
    block = h.session.blocks[-1]
    h.view.frame()
    geo = block.view
    assert len(geo.cmd_lines) == 8
    assert geo.cmd_rows == [(i, 0, 5) for i in range(6)]
    assert geo.cmd_truncated is True
    assert geo.header_h == 10 + 6 * ch


def test_output_without_a_command_has_a_one_row_header(boot):
    view = boot
    ch = view.r.ch
    view.session.feed(b"Last login\r\n")
    block, = view.session.blocks
    view.frame()
    geo = block.view
    assert geo.cmd_lines == [""] and geo.cmd_rows == [(0, 0, 0)]
    assert geo.cmd_truncated is False
    assert geo.header_h == 10 + ch
    geo.cmd_lines, geo.cmd_rows, geo.cmd_truncated = ["x"], [(0, 0, 1)], True
    view.cols = 39                               # laid out anew: the same
    view._measure(block)
    assert geo.cmd_lines == [""] and geo.cmd_rows == [(0, 0, 0)]
    assert geo.cmd_truncated is False


def test_a_finished_block_is_measured_again_only_when_it_can_differ(ui):
    h = ui()
    view = h.view
    block = h.run("ls", lines("a", "b"))
    geo = block.view

    def measured_again() -> bool:
        geo.height = -1                          # a mark on the kept value
        return view._measure(block) != -1

    assert not measured_again()
    view.cols = 39
    assert measured_again() and not measured_again()
    block.collapsed = True
    assert measured_again() and not measured_again()
    block.collapsed = False
    assert measured_again() and not measured_again()
    block.filter = FilterSpec("a")
    assert measured_again() and not measured_again()
    block.fullscreen = True
    assert measured_again() and not measured_again()
    block.exit_code = 3
    assert measured_again() and not measured_again()
    assert geo.items == [MENU, JUMP, ("fail", "exit 3", 7),
                         ("chip", "full-screen", 14),
                         ("filter", "filter 1/2", 15)]


def test_a_running_block_is_measured_every_time(ui):
    h = ui()
    view = h.view
    block = running(h, "make", lines("a", "b"))
    view.frame()
    geo = block.view
    height = geo.height
    geo.height = -1
    assert view._measure(block) == height
    block.command = "pwd"                        # the header follows it
    view._measure(block)
    assert geo.cmd_lines == ["pwd"] and geo.cmd_rows == [(0, 0, 3)]


def test_the_command_is_wrapped_again_when_its_room_changes(ui):
    h = ui()
    view = h.view
    block = h.run("echo " + "x" * 45, "one\r\n")   # 50 characters
    geo = block.view
    assert geo.cmd_rows == [(0, 0, 34), (0, 34, 50)]
    view.cols = 45                               # five cells more
    view._measure(block)
    assert geo.cmd_rows == [(0, 0, 39), (0, 39, 50)]
    block.collapsed = True                       # a chip of 16 cells
    view._measure(block)
    assert geo.items[-1] == ("chip", "1 line hidden", 16)
    assert geo.cmd_rows == [(0, 0, 23), (0, 23, 50)]


def test_the_command_is_wrapped_again_only_when_its_room_changes(
        ui, monkeypatch):
    h = ui()
    view = h.view
    block = running(h, "make")
    monkeypatch.setattr(Block, "elapsed", lambda self: 1.0)
    view._measure(block)
    geo = block.view
    rows = geo.cmd_rows
    monkeypatch.setattr(Block, "elapsed", lambda self: 2.0)
    view._measure(block)
    assert geo.items[-1] == ("spinner", "2.0s", 7)
    assert geo.cmd_rows is rows                  # as wide as before: kept
    monkeypatch.setattr(Block, "elapsed", lambda self: 75.0)
    view._measure(block)
    assert geo.items[-1] == ("spinner", "1m 15s", 9)
    assert geo.cmd_rows is not rows and geo.cmd_rows == rows


def test_a_collapsed_block_is_only_its_header(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "build", numbered(6))
    block.filter = FilterSpec("line 4")
    view._measure(block)
    geo = block.view
    assert geo.live_shown == [1] and rows_of(geo) == (0, 0, 1, 1)
    block.collapsed = True
    assert view._measure(block) == geo.header_h + 1
    assert rows_of(geo) == (0, 0, 0, 0) and geo.live_shown is None
    assert geo.items[-1] == ("chip", "6 lines hidden", 17)


def test_zooming_measures_every_block_anew(ui):
    h = ui()
    view = h.view
    block = h.run("ls", lines("a", "b"))
    old = block.view
    view.set_font_size(view.r.size + 6)
    view.frame()
    ch = view.r.ch
    assert block.view is not old
    assert block.view.header_h == 10 + ch
    assert block.view.height == 10 + ch + 2 * ch + 7


def test_output_rows_follow_the_width(ui):
    h = ui()
    view = h.view
    block = h.run("cat", lines("x" * 100, "short"))
    assert rows_of(block.view) == (4, 0, 0, 4)   # 3 rows and 1
    view.cols = 60
    view._measure(block)
    assert rows_of(block.view) == (3, 0, 0, 3)


# -- blocks that still run ----------------------------------------------------

def test_a_running_command_shows_its_screen_down_to_the_cursor(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "seq")
    view.frame()
    assert rows_of(block.view) == (0, 0, 1, 1)   # the cursor's row alone
    h.session.feed(numbered(3).encode())
    view.frame()
    assert rows_of(block.view) == (0, 0, 4, 4)
    h.session.feed(numbered(6).encode())         # six lines scroll off
    view.frame()
    assert rows_of(block.view) == (6, 0, 4, 10)


def test_live_output_without_a_command_ends_at_its_last_line(boot):
    view = boot
    view.session.feed(b"Last login\r\n")
    block, = view.session.blocks
    view.frame()
    assert block.screen.cy == 1                  # the cursor is a row below
    assert rows_of(block.view) == (0, 0, 1, 1)
    assert view._live_texts(block) == ["Last login"]


def test_a_line_half_scrolled_off_is_counted_the_way_it_is_drawn(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "line\r\n" + "w" * 95 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert block.store.open_parts == ["w" * 40]  # its first row is gone
    assert rows_of(geo) == (1, 1, 4, 6)
    assert view._live_texts(block) == ["w" * 95, "next", "more"]
    assert view._row_map(block) == [(1, 40, 40), (1, 80, 15), (2, 0, 4),
                                    (3, 0, 4)]
    h.session.feed(prompt())                     # finished: all of it kept
    view.frame()
    assert rows_of(geo) == (6, 0, 0, 6)


def test_two_rows_of_an_open_line_count_as_two(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "w" * 135 + "\r\nnext\r\nmore")
    view.frame()
    assert block.store.open_parts == ["w" * 40, "w" * 40]
    assert rows_of(block.view) == (0, 2, 4, 6)


def test_an_open_line_whose_end_was_erased_is_still_a_line(boot):
    view = boot
    view.session.feed(b"x" * 210 + b"\x1b[H\x1b[J")
    block, = view.session.blocks
    view.frame()
    assert block.store.open_parts == ["x" * 40, "x" * 40]
    assert view._live_count(block) == 0          # nothing left on screen
    assert view._live_texts(block) == ["x" * 80]
    assert rows_of(block.view) == (0, 2, 0, 2)


def test_a_screen_wiped_blank_has_no_lines_left(boot):
    view = boot
    view.session.feed(b"Last login\r\n")
    view.session.feed(b"\x1b[H\x1b[J")
    block, = view.session.blocks
    assert block.store.open_parts is None
    assert view._live_count(block) == 0 and view._live_texts(block) == []


def test_a_program_that_cleared_the_display_owns_a_whole_screen(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "ssh host", numbered(30) + "\x1b[H\x1b[2Jremote$ ")
    view.frame()
    geo = block.view
    assert block.cleared and view._live_count(block) == 1
    assert geo.live_rows == 8
    view.rows = 5                                # the view is the smaller
    view._measure(block)
    assert geo.live_rows == 5
    h.session.feed(("\r\n" + numbered(6)).encode())    # more than that
    view._measure(block)
    assert geo.live_rows == 8
    view.rows = 8


def test_a_running_command_that_cleared_nothing_owns_only_its_rows(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "make", "one\r\n")
    view.frame()
    assert not block.cleared and block.view.live_rows == 2


# -- filters ------------------------------------------------------------------

def test_a_filter_on_a_finished_block_is_decided_once(ui):
    h = ui()
    view = h.view
    block = h.run("make", lines("ok 1", "error: a", "ok 2", "Error: b"))
    spec = FilterSpec("error")
    block.filter = spec
    view._measure(block)
    geo = block.view
    shown = block.visible
    assert shown == [1, 3] and rows_of(geo) == (2, 0, 0, 2)
    assert geo.fspec is spec and geo.fbase == 0 and geo.fupto == 4
    assert geo.flive is False and geo.live_lines is None
    view._refresh_filter(block, geo)
    assert block.visible is shown                # kept, not decided again
    block.visible = None                         # thrown away: decided again
    view._refresh_filter(block, geo)
    assert block.visible == [1, 3]
    shown = block.visible
    block.store.append("error: late")            # the block has grown
    view._refresh_filter(block, geo)
    assert block.visible == [1, 3, 4] and geo.fupto == 5


def test_another_filter_on_a_finished_block_replaces_the_first(ui):
    h = ui()
    view = h.view
    block = h.run("make", lines("ok 1", "error: a", "ok 2", "Error: b"))
    block.filter = FilterSpec("error")
    view._measure(block)
    assert block.visible == [1, 3]
    block.filter = FilterSpec("ok")
    view._measure(block)
    assert block.visible == [0, 2]


def test_a_filter_counts_lines_from_the_first_one_the_block_ever_had(ui):
    h = ui()
    view = h.view
    block = h.run("make", lines("ok 1", "error: a", "ok 2", "Error: b"))
    block.store.drop_front(2)
    block.filter = FilterSpec("error")
    view._measure(block)
    assert block.visible == [3] and block.view.fbase == 2


def test_a_filter_that_is_no_pattern_shows_every_line(ui):
    h = ui()
    view = h.view
    block = h.run("make", lines("ok 1", "error: a", "ok 2", "Error: b"))
    block.filter = FilterSpec("error")
    view._measure(block)
    geo = block.view
    assert rows_of(geo) == (2, 0, 0, 2)
    block.filter = FilterSpec("(", regex=True)
    view._measure(block)
    assert block.visible is None and geo.fspec is None
    assert rows_of(geo) == (4, 0, 0, 4)


def test_a_filter_on_a_collapsed_block_still_counts_its_lines(ui):
    h = ui()
    view = h.view
    block = h.run("seq 4", lines("1", "2", "3", "4"))
    block.collapsed = True
    view._measure(block)
    block.filter = FilterSpec("3")
    view._measure(block)
    geo = block.view
    assert block.visible == [2] and rows_of(geo) == (0, 0, 0, 0)
    assert geo.items[2:] == [("filter", "filter 1/4", 15),
                             ("chip", "4 lines hidden", 17)]
    view.open_filter([block])                    # and so does the filter bar
    view.search.edit.setText("[24]")
    view.search.regex.setChecked(True)
    h.pump()
    assert view.search.status.text() == "2 of 4"
    assert geo.items[2] == ("filter", "filter 2/4", 15)


def test_a_filter_follows_the_output_of_a_running_command(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "build", lines("ok 0", "error 1", "ok 2", "error 3",
                                      "ok 4", "error 5"))
    spec = FilterSpec("error")
    block.filter = spec
    view._measure(block)
    geo = block.view
    shown = block.visible
    assert shown == [1]                          # of the lines 0, 1 and 2
    assert geo.live_lines == [3, 5]              # of those still on screen
    assert geo.live_shown == [0, 2]              # as rows of that screen
    assert rows_of(geo) == (1, 0, 2, 3)
    assert geo.fspec is spec and geo.fbase == 0 and geo.fupto == 3
    assert geo.flive is True
    view._measure(block)                         # nothing new: nothing redone
    assert block.visible is shown and shown == [1]
    h.session.feed(lines("ok 6", "error 7").encode())
    view._measure(block)
    assert block.visible is shown and shown == [1, 3]    # extended in place
    assert geo.live_lines == [5, 7] and geo.fupto == 5
    assert rows_of(geo) == (2, 0, 2, 4)
    block.store.drop_front(2)                    # the oldest two lines go
    view._measure(block)
    assert block.visible == [3] and block.visible is not shown
    assert geo.fbase == 2 and geo.live_lines == [5, 7]
    shown = block.visible
    h.session.feed(lines("error 8", "ok 9").encode())
    view._measure(block)
    assert block.visible is shown and shown == [3, 5]
    assert geo.live_lines == [7, 8] and geo.fupto == 7
    h.session.feed(prompt())                     # finished: decided exactly
    view._measure(block)
    assert block.visible == [3, 5, 7, 8]
    assert geo.flive is False and geo.live_lines is None
    assert geo.fupto == 10 and rows_of(geo) == (4, 0, 0, 4)


def test_taking_the_filter_off_a_running_command_shows_all_of_it(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "build", lines("ok 0", "error 1", "ok 2", "error 3",
                                      "ok 4", "error 5"))
    block.filter = FilterSpec("error")
    view._measure(block)
    geo = block.view
    assert rows_of(geo) == (1, 0, 2, 3)
    block.filter = None
    view._measure(block)
    assert block.visible is None and geo.fspec is None
    assert geo.live_lines is None and geo.live_shown is None
    assert rows_of(geo) == (3, 0, 4, 7)


def test_a_filter_with_context_reaches_back_into_what_scrolled_off(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "build", lines("a 0", "b 1", "c 2", "error 3", "e 4",
                                      "f 5"))
    spec = FilterSpec("error", context=1)
    block.filter = spec
    view._measure(block)
    geo = block.view
    assert block.visible == [2]                  # the line before the match
    assert geo.live_lines == [3, 4] and geo.live_shown == [0, 1]
    assert rows_of(geo) == (1, 0, 2, 3)
    assert geo.fspec is spec and geo.fupto == 3 and geo.flive is True
    block.store.drop_front(1)
    view._measure(block)
    assert block.visible == [2] and geo.live_lines == [3, 4]
    assert geo.fbase == 1


def test_a_filter_with_context_on_a_huge_block_is_not_redone_every_frame(
        ui, monkeypatch):
    h = ui(rows=4)
    view = h.view
    fake = clock(monkeypatch, 10.0)
    block = running(h, "build", "x\r\n" * 20002)
    block.filter = FilterSpec("error", context=1)
    view._measure(block)
    geo = block.view
    assert len(block.store.texts) == 19999
    assert geo.ftime == 10.0 and geo.live_lines == []
    fake.now = 10.1                              # small enough: every time
    view._measure(block)
    assert geo.ftime == 10.1
    h.session.feed(b"error\r\n")
    assert len(block.store.texts) == 20000
    fake.now = 10.2                              # now only now and then
    view._measure(block)
    assert geo.ftime == 10.1 and geo.live_lines == []
    assert block.visible == [] and geo.flive is True
    geo.ftime = 0.0
    fake.now = 0.3                               # not yet ...
    view._measure(block)
    assert geo.ftime == 0.0 and geo.live_lines == []
    fake.now = 0.31                              # ... but now
    view._measure(block)
    assert geo.ftime == 0.31
    assert block.visible == [] and geo.live_lines == [20001, 20002, 20003]
    assert geo.fupto == 20000


def test_a_filter_decides_whether_a_half_scrolled_line_is_shown(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "line\r\n" + "w" * 95 + "\r\nnext\r\nmore")
    block.filter = FilterSpec("w")
    view._measure(block)
    geo = block.view
    assert block.visible == [] and geo.live_lines == [1]
    assert geo.live_shown == [0, 1] and rows_of(geo) == (0, 1, 2, 3)
    block.filter = FilterSpec("next")
    view._measure(block)
    assert geo.live_lines == [2]
    assert geo.live_shown == [2] and rows_of(geo) == (0, 0, 1, 1)


def test_a_filter_on_a_cleared_screen_looks_only_at_rows_that_exist(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "ssh host", numbered(30) + "\x1b[H\x1b[2Jremote$ ")
    block.filter = FilterSpec("remote")
    view._measure(block)
    geo = block.view
    assert len(block.screen.grid) == 1           # of the eight it may show
    assert geo.live_shown == [0] and geo.live_rows == 1


# -- the input area -----------------------------------------------------------

def test_the_input_area_is_as_tall_as_what_the_shell_draws(ui):
    h = ui()
    view = h.view
    assert view._input_rows() == 1
    h.session.feed(b"echo one \\\r\n> echo two \\\r\n> echo three")
    assert view._input_rows() == 3
    view.rows = 2                                # but no taller than the view
    assert view._input_rows() == 2


def test_there_is_no_input_area_while_a_program_has_the_terminal(ui):
    h = ui()
    view = h.view
    h.session.feed(b"\x1b[?1049h")               # full screen at the prompt
    assert h.session.prompt_active and h.session.fullscreen
    assert view._input_rows() == 0
    h.session.feed(b"\x1b[?1049l")
    assert view._input_rows() == 1
    h.session.feed(submit("sleep 9"))
    assert view._input_rows() == 0


# -- the frame ----------------------------------------------------------------

def test_blocks_shorter_than_the_view_rest_on_the_input_area(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    frame = view.frame()
    assert frame.blocks == [one, ls, bad]
    assert frame.blocks is not h.session.blocks
    assert frame.ids == [1, 2, 3]
    assert frame.heights == [one.view.height, ls.view.height,
                             bad.view.height]
    assert frame.input_rows == 1 and frame.input_h == view.r.ch + 16
    assert frame.view_h == view.height() - frame.input_h
    spacer = frame.view_h - sum(frame.heights)
    assert spacer > 0
    assert frame.tops == [spacer, spacer + frame.heights[0],
                          spacer + frame.heights[0] + frame.heights[1]]
    assert frame.content_h == frame.view_h and frame.max_scroll == 0
    assert view.scroll == 0


def test_blocks_taller_than_the_view_start_at_the_top_and_scroll(ui):
    h = ui(rows=8)
    view = h.view
    h.run("seq 40", numbered(40))
    h.run("seq 30", numbered(30))
    frame = view.frame()
    assert frame.tops == [0, frame.heights[0]]
    assert frame.content_h == sum(frame.heights)
    assert frame.max_scroll == frame.content_h - frame.view_h > 0
    assert view.scroll == frame.max_scroll


def test_a_running_command_leaves_no_room_for_the_input_area(ui):
    h = ui()
    view = h.view
    running(h, "sleep 9")
    frame = view.frame()
    assert frame.input_rows == 0 and frame.input_h == 0
    assert frame.view_h == view.height()


def test_a_clear_leaves_a_screenful_of_room_below_the_old_blocks(ui):
    h = ui()
    view = h.view
    three_blocks(h)
    clear_display(h)
    frame = view.frame()
    heights = frame.heights
    total = sum(heights)
    assert frame.tops == [0, heights[0], heights[0] + heights[1]]
    assert frame.content_h == total + frame.view_h
    assert frame.max_scroll == total and view.scroll == total
    new = h.run("echo new", "new\r\n")
    frame = view.frame()
    spacer = frame.view_h - new.view.height
    assert frame.tops == [0, heights[0], heights[0] + heights[1],
                          total + spacer]
    assert frame.content_h == total + frame.view_h
    assert view.scroll == total                  # the old ones end at the top


def test_a_view_too_short_for_its_input_area_shows_no_blocks(ui):
    h = ui()
    view = h.view
    three_blocks(h)
    view.setMinimumSize(0, 0)
    view.resize(view.width(), 10)
    frame = view.frame()
    assert frame.input_h == 10 and frame.view_h == 0
    assert view.bar.pageStep() == 1


def test_the_frame_is_kept_until_something_it_shows_changes(ui, monkeypatch):
    h = ui()
    view = h.view
    session = h.session
    three_blocks(h)
    frame = view.frame()
    assert view.frame() is frame
    assert view._frame is frame
    assert view._frame_key == (session.version, view._ui, view.width(),
                               view.height(), view.cols, 3, -1)
    view._ui += 1
    assert view.frame() is not frame
    frame = view.frame()
    session.blocks.pop()                         # behind the session's back
    assert view.frame().ids == [1, 2]
    running(h, "sleep 9")
    monkeypatch.setattr(Block, "elapsed", lambda self: 7.9)
    view.frame()
    assert view._frame_key[-1] == 7              # its seconds so far


def test_the_view_follows_new_output_until_the_user_scrolls_away(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "seq", numbered(60))
    frame = view.frame()
    assert view.follow and view.scroll == frame.max_scroll > 0
    view._set_scroll(50)
    assert not view.follow
    h.session.feed(numbered(20).encode())
    assert view.frame().max_scroll > frame.max_scroll
    assert view.scroll == 50                     # stays where it was put
    view.follow = True
    assert view.scroll == 50                     # nothing changed yet ...
    h.session.feed(b"more\r\n")
    assert view.frame().max_scroll == view.scroll    # ... now it has
    assert block.running


def test_a_view_scrolled_past_the_new_end_is_brought_back(ui):
    h = ui(rows=8)
    view = h.view
    block = h.run("seq 60", numbered(60))
    h.run("true", "")
    view._set_scroll(300)
    assert view.scroll == 300 and not view.follow
    view.set_collapsed([block], True)
    frame = view.frame()
    assert frame.max_scroll == 0
    assert view.scroll == 0
    assert not view.follow                       # it was not asked to follow


# -- scrolling ----------------------------------------------------------------

def test_the_scrollbar_mirrors_the_frame(ui):
    h = ui(rows=8)
    view = h.view
    h.run("seq 60", numbered(60))
    frame = view.frame()
    bar = view.bar
    assert (bar.minimum(), bar.maximum()) == (0, frame.max_scroll)
    assert bar.pageStep() == frame.view_h
    assert bar.singleStep() == 3 * view.r.ch
    assert bar.value() == view.scroll == frame.max_scroll
    assert bar.isVisible()
    view._set_scroll(70)
    assert bar.value() == 70


def test_the_scrollbar_shows_only_when_there_is_something_to_scroll(ui):
    h = ui(rows=8)
    view = h.view
    h.run("echo one", "one\r\n")
    view.frame()
    assert not view.bar.isVisible()
    h.run("seq 60", numbered(60))
    view.frame()
    assert view.bar.isVisible()
    h.session.feed(submit("vim") + b"\x1b[?1049h")    # a full-screen program
    view.frame()
    assert not view.bar.isVisible()


def test_one_pixel_too_many_is_enough_for_a_scrollbar(ui):
    h = ui()
    view = h.view
    block = h.run("seq 9", numbered(9))
    height = block.view.height + view.frame().input_h
    view.setMinimumSize(0, 0)
    view.resize(view.width(), height)            # the block just fits
    assert view.frame().max_scroll == 0 and not view.bar.isVisible()
    view.resize(view.width(), height - 1)
    assert view.frame().max_scroll == 1 and view.bar.isVisible()


def test_moving_the_scrollbar_scrolls_the_view(ui):
    h = ui(rows=8)
    view = h.view
    h.run("seq 60", numbered(60))
    view.frame()
    view.bar.setValue(40)
    assert view.scroll == 40 and not view.follow
    view.bar.setValue(view.bar.maximum())
    assert view.scroll == view.frame().max_scroll and view.follow


def test_scrolling_stays_within_the_content(ui):
    h = ui(rows=8)
    view = h.view
    h.run("seq 60", numbered(60))
    limit = view.frame().max_scroll
    updates = spy(view, "update")
    view._set_scroll(-5)
    assert view.scroll == 0 and not view.follow
    view._set_scroll(10.9)
    assert view.scroll == 10 and type(view.scroll) is int
    view._set_scroll(limit - 1)
    assert view.scroll == limit - 1 and not view.follow
    view._set_scroll(limit)
    assert view.scroll == limit and view.follow
    view._set_scroll(limit + 100)
    assert view.scroll == limit and view.follow
    assert updates == [()] * 5


def test_scrolling_to_the_bottom_follows_again(ui):
    h = ui(rows=8)
    view = h.view
    h.run("seq 60", numbered(60))
    view._set_scroll(10)
    updates = spy(view, "update")
    before = view._ui
    view.scroll_to_bottom()
    assert view.follow and view._ui == before + 1 and updates == [()]
    assert view.scroll == 10                     # until the next frame
    assert view.frame().max_scroll == view.scroll


def test_a_block_is_found_by_its_number(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    assert view._block_at_index(ls.id) == (1, ls)
    assert view._block_at_index(bad.id) == (2, bad)
    assert view._block_at_index(0) == (-1, None)
    assert view._block_at_index(4) == (-1, None)
    h.session.blocks.remove(ls)
    view._ui += 1
    assert view._block_at_index(ls.id) == (-1, None)
    assert view._block_at_index(bad.id) == (1, bad)


# -- from a point of the window to the text -----------------------------------

def top_of(view, block) -> int:
    """The window y of the first pixel of a block."""
    frame = view.frame()
    return frame.tops[frame.ids.index(block.id)] - view.scroll


def row_y(view, block, row: int) -> int:
    """The window y of the first pixel of an output row of a block."""
    return top_of(view, block) + block.view.header_h + row * view.r.ch


def x_of(view, col: float) -> float:
    """The window x of a column, counted in cells."""
    return PAD_LEFT + col * view.r.cw


def test_a_point_is_in_the_cell_it_falls_into(ui):
    view = ui().view
    cw, ch = view.r.cw, view.r.ch
    assert view._cell(14, 100, 100) == (0, 0)
    assert view._cell(14 + cw - 1, 100 + ch - 1, 100) == (0, 0)
    assert view._cell(14 + cw, 100 + ch, 100) == (1, 1)
    assert view._cell(14 + 2.5 * cw, 100 + 3.5 * ch, 100) == (3, 2)
    assert view._cell(0, 99, 100) == (-1, 0)     # left of the text, a row up
    assert view._cell(10 ** 6, 100, 100) == (0, COLS - 1)
    assert [type(n) for n in view._cell(20.5, 110.5, 100)] == [int, int]


def test_the_button_under_a_point_is_the_one_drawn_last(ui):
    h = ui(rows=8)
    view = h.view
    block = h.run("seq 100", numbered(100))
    view._set_scroll(200)
    h.pump()
    rects = {name: rect for rect, _, name in view._buttons}
    assert set(rects) == {"menu", "jump", "sticky"}
    menu = rects["menu"].center()                # on the pinned header
    assert rects["sticky"].contains(menu)
    assert view._button_at(menu.x(), menu.y()) == (block, "menu")
    jump = rects["jump"].center()
    assert view._button_at(jump.x(), jump.y()) == (block, "jump")
    assert view._button_at(20, 6) == (block, "sticky")
    assert view._button_at(20, view.height() - 3) == (None, None)


def test_the_block_under_a_point(ui):
    h = ui()
    view = h.view
    assert view._block_under(50) == (-1, None, 0)    # there are no blocks
    one, ls, bad = three_blocks(h)
    frame = view.frame()
    tops, heights = frame.tops, frame.heights
    assert tops[0] > 0                           # room above the first one
    assert view._block_under(0) == (-1, None, 0)
    assert view._block_under(tops[0] - 1) == (-1, None, 0)
    assert view._block_under(tops[0]) == (0, one, 0)
    assert view._block_under(tops[1] - 1) == (0, one, heights[0] - 1)
    assert view._block_under(tops[1]) == (1, ls, 0)
    assert view._block_under(tops[1] + 7) == (1, ls, 7)
    assert view._block_under(frame.view_h - 1) == (2, bad, heights[2] - 1)
    assert view._block_under(frame.view_h) == (-1, None, 0)    # the input
    assert view._block_under(-1) == (-1, None, 0)


def test_the_block_under_a_point_of_a_scrolled_view(ui):
    h = ui(rows=8)
    view = h.view
    first = h.run("seq 40", numbered(40))
    second = h.run("seq 30", numbered(30))
    frame = view.frame()
    view._set_scroll(frame.tops[1] - 50)         # the second starts 50 down
    assert view._block_under(0) == (0, first, frame.tops[1] - 50)
    assert view._block_under(-1) == (-1, None, 0)
    assert view._block_under(49) == (0, first, frame.heights[0] - 1)
    assert view._block_under(50) == (1, second, 0)
    assert view._block_under(frame.view_h - 1) == \
        (1, second, frame.view_h - 51)
    assert view._block_under(frame.view_h) == (-1, None, 0)


def test_no_block_is_under_the_room_a_clear_left(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    clear_display(h)
    new = h.run("echo new", "new\r\n")
    frame = view.frame()
    end_of_old = frame.tops[2] + frame.heights[2]
    view._set_scroll(end_of_old - 30)            # the old ones end 30 down
    start_of_new = frame.tops[3] - view.scroll
    assert 30 < start_of_new < frame.view_h
    assert view._block_under(29) == (2, bad, frame.heights[2] - 1)
    assert view._block_under(30) == (-1, None, 0)
    assert view._block_under(start_of_new - 1) == (-1, None, 0)
    assert view._block_under(start_of_new) == (3, new, 0)


def test_there_is_no_text_position_without_blocks(ui):
    assert ui().view._position_at(50, 50) is None


def test_a_point_on_output_gives_the_line_and_the_nearest_gap(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    ch = view.r.ch
    y = row_y(view, ls, 0)
    assert view._position_at(x_of(view, 0), y) == (ls.id, 1, 0, 0)
    assert view._position_at(x_of(view, 3.4), y) == (ls.id, 1, 0, 3)
    assert view._position_at(x_of(view, 3.6), y + ch - 1) == (ls.id, 1, 0, 4)
    assert view._position_at(x_of(view, 3), y + ch) == (ls.id, 1, 1, 3)
    assert view._position_at(x_of(view, 30), y + ch) == (ls.id, 1, 1, 5)
    assert view._position_at(0, y) == (ls.id, 1, 0, 0)
    assert view._position_at(10 ** 6, y) == (ls.id, 1, 0, 12)
    # One pixel up is the header; below the last row, the end of the block.
    assert view._position_at(x_of(view, 1), y - 1) == (ls.id, 0, 0, 1)
    assert view._position_at(x_of(view, 1), y + 2 * ch) == (ls.id, 1, 1, 5)
    assert view._position_at(x_of(view, 1), top_of(view, bad) - 1) == \
        (ls.id, 1, 1, 5)
    # Above the first block: the very start.
    assert view._position_at(x_of(view, 9), 0) == (one.id, 0, 0, 0)


def test_a_point_on_a_header_gives_a_place_in_the_command(ui):
    h = ui()
    view = h.view
    block = h.run("echo " + "x" * 55, "out\r\n")     # rows of 34 and 26
    ch = view.r.ch
    top = top_of(view, block)
    two = x_of(view, 2)
    assert view._position_at(two, top) == (block.id, 0, 0, 2)
    assert view._position_at(two, top + 5) == (block.id, 0, 0, 2)
    assert view._position_at(two, top + 5 + ch - 1) == (block.id, 0, 0, 2)
    assert view._position_at(two, top + 5 + ch) == (block.id, 0, 0, 36)
    assert view._position_at(two, top + 5 + 2 * ch + 4) == \
        (block.id, 0, 0, 36)                     # the padding below
    far = x_of(view, 39)
    assert view._position_at(far, top + 5) == (block.id, 0, 0, 34)
    assert view._position_at(far, top + 5 + ch) == (block.id, 0, 0, 60)


def test_a_point_on_a_command_of_several_lines(ui):
    h = ui()
    view = h.view
    h.session.feed(submit("first%0Asecond one", typed="x") + prompt())
    block = h.session.blocks[-1]
    ch = view.r.ch
    top = top_of(view, block)
    assert view._position_at(x_of(view, 3), top + 5) == (block.id, 0, 0, 3)
    assert view._position_at(x_of(view, 3), top + 5 + ch) == \
        (block.id, 0, 1, 3)
    assert view._position_at(x_of(view, 30), top + 5) == (block.id, 0, 0, 5)
    # Under its header a block without output has only its dividing line.
    assert view._position_at(x_of(view, 3), top + block.view.header_h) == \
        (block.id, 0, 1, 10)
    assert view._block_end(block, block.view) == (block.id, 0, 1, 10)


def test_a_point_on_a_command_with_wide_characters(ui):
    h = ui()
    view = h.view
    block = h.run("echo 日本", "")
    assert block.view.cmd_lines == ["echo 日本"]
    y = top_of(view, block) + 5
    assert view._position_at(x_of(view, 5), y) == (block.id, 0, 0, 5)
    assert view._position_at(x_of(view, 6), y) == (block.id, 0, 0, 6)
    assert view._position_at(x_of(view, 7), y) == (block.id, 0, 0, 6)
    assert view._position_at(x_of(view, 9), y) == (block.id, 0, 0, 7)


def test_a_point_on_the_label_of_output_without_a_command(boot):
    view = boot
    view.session.feed(b"Last login\r\n")
    block, = view.session.blocks
    top = top_of(view, block)
    assert view._position_at(x_of(view, 7), top + 9) == (block.id, 0, 0, 0)
    assert view._position_at(x_of(view, 7), row_y(view, block, 0)) == \
        (block.id, 1, 0, 7)
    block.collapsed = True
    view._measure(block)
    assert view._block_end(block, block.view) == (block.id, 0, 0, 0)


def test_the_end_of_a_block_is_the_end_of_its_last_row(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    assert view._block_end(one, one.view) == (one.id, 1, 0, 3)
    assert view._block_end(ls, ls.view) == (ls.id, 1, 1, 5)
    assert view._block_end(bad, bad.view) == (bad.id, 0, 0, 5)
    y = top_of(view, bad) + bad.view.header_h    # its dividing line
    assert view._position_at(x_of(view, 1), y) == (bad.id, 0, 0, 5)


def test_a_point_outside_the_block_list_counts_as_its_nearest_edge(ui):
    h = ui(rows=8)
    view = h.view
    block = h.run("seq 100", numbered(100))
    ch = view.r.ch
    header = block.view.header_h
    view_h = view.frame().view_h
    view._set_scroll(header + 10 * ch)           # row 10 starts at the top
    assert view._position_at(14, 0) == (block.id, 1, 10, 0)
    assert view._position_at(14, -5) == (block.id, 1, 10, 0)
    view._set_scroll(header + 10 * ch - 1)       # ... one pixel further down
    assert view._position_at(14, 1) == (block.id, 1, 10, 0)
    assert view._position_at(14, -5) == (block.id, 1, 9, 0)
    view._set_scroll(header + 30 * ch - view_h)  # row 30 starts right below
    assert view._position_at(14, view_h - 1) == (block.id, 1, 29, 0)
    assert view._position_at(14, 10 ** 6) == (block.id, 1, 29, 0)
    view._set_scroll(header + 30 * ch - view_h + 1)    # ... one pixel up
    assert view._position_at(14, view_h - 2) == (block.id, 1, 29, 0)
    assert view._position_at(14, 10 ** 6) == (block.id, 1, 30, 0)


def test_an_output_row_and_a_column_give_a_line_and_an_offset(ui):
    h = ui()
    view = h.view
    block = h.run("cat", lines("plain", "a日b\tc", "x" * 100))
    geo = block.view
    assert block.store.texts[1] == "a日b\tc"
    assert rows_of(geo) == (5, 0, 0, 5)
    assert view._row_to_text(block, geo, 0, 3) == (0, 3)
    assert view._row_to_text(block, geo, 0, 30) == (0, 5)    # the line's end
    assert view._row_to_text(block, geo, 1, 2) == (1, 1)     # the wide one
    assert view._row_to_text(block, geo, 1, 5) == (1, 3)     # in the tab
    assert view._row_to_text(block, geo, 1, 8) == (1, 4)
    assert view._row_to_text(block, geo, 1, 20) == (1, 5)
    assert view._row_to_text(block, geo, 3, 5) == (2, 45)    # a wrapped row
    assert view._row_to_text(block, geo, 4, 39) == (2, 100)
    block.filter = FilterSpec("x")               # only the long line shows
    view._measure(block)
    assert view._row_to_text(block, geo, 0, 5) == (2, 5)
    assert view._row_to_text(block, geo, 1, 5) == (2, 45)


def test_rows_of_a_running_block_are_found_wherever_they_are_kept(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "line\r\n" + "w" * 95 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert rows_of(geo) == (1, 1, 4, 6)
    assert view._row_to_text(block, geo, 0, 2) == (0, 2)     # scrolled off
    assert view._row_to_text(block, geo, 1, 5) == (1, 5)     # half scrolled
    assert view._row_to_text(block, geo, 2, 3) == (1, 43)    # on the screen
    assert view._row_to_text(block, geo, 3, 20) == (1, 95)
    assert view._row_to_text(block, geo, 4, 2) == (2, 2)
    assert view._row_to_text(block, geo, 5, 0) == (3, 0)
    assert view._block_end(block, geo) == (block.id, 1, 3, 4)
    block.filter = FilterSpec("next|more", regex=True)
    view._measure(block)
    assert geo.live_shown == [2, 3] and rows_of(geo) == (0, 0, 2, 2)
    assert view._row_to_text(block, geo, 0, 1) == (2, 1)
    assert view._row_to_text(block, geo, 1, 9) == (3, 4)


def test_a_half_scrolled_line_with_wide_characters(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "日" * 50 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert block.store.open_parts == ["日" * 20]
    assert rows_of(geo) == (0, 1, 4, 5)
    assert view._row_to_text(block, geo, 0, 6) == (0, 3)
    assert view._row_to_text(block, geo, 1, 6) == (0, 23)


def test_rows_below_what_a_cleared_screen_holds_count_as_its_last(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "ssh host",
                    numbered(30) + "\x1b[H\x1b[2Jremote$ ls\r\nfile")
    view.frame()
    geo = block.view
    first = geo.frozen_rows
    assert geo.live_rows == 8 and len(block.screen.grid) == 2
    line = block.store.end
    assert view._row_to_text(block, geo, first, 3) == (line, 3)
    assert view._row_to_text(block, geo, first + 1, 3) == (line + 1, 3)
    assert view._row_to_text(block, geo, first + 2, 3) == (line + 1, 3)
    assert view._row_to_text(block, geo, first + 7, 30) == (line + 1, 4)


def test_a_line_and_an_offset_give_the_output_row(ui):
    h = ui()
    view = h.view
    block = h.run("cat", lines("plain", "日" * 30, "x" * 100, "end"))
    geo = block.view
    assert rows_of(geo) == (7, 0, 0, 7)
    assert view._text_to_row(block, geo, 0, 3) == 0
    assert view._text_to_row(block, geo, 1, 4) == 1
    assert view._text_to_row(block, geo, 1, 25) == 2     # its second row
    assert view._text_to_row(block, geo, 2, 0) == 3
    assert view._text_to_row(block, geo, 2, 45) == 4
    assert view._text_to_row(block, geo, 2, 100) == 5
    assert view._text_to_row(block, geo, 3, 0) == 6
    assert view._text_to_row(block, geo, 9, 0) == 6      # past the end
    block.filter = FilterSpec("plain|x", regex=True)
    view._measure(block)
    assert block.visible == [0, 2]
    assert view._text_to_row(block, geo, 1, 0) == 1      # hidden: the next
    assert view._text_to_row(block, geo, 2, 45) == 2
    assert view._text_to_row(block, geo, 3, 0) == 1      # hidden: the last
    block.filter = FilterSpec("nothing")
    view._measure(block)
    assert block.visible == [] and view._text_to_row(block, geo, 2, 45) == 0
    quiet = h.run("true", "")
    assert view._text_to_row(quiet, quiet.view, 5, 0) == 0


def test_lines_of_a_running_block_are_found_on_its_screen(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "line\r\n" + "w" * 95 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert view._text_to_row(block, geo, 0, 2) == 0
    assert view._text_to_row(block, geo, 1, 40) == 2     # start of the row
    assert view._text_to_row(block, geo, 1, 80) == 2     # end of that row
    assert view._text_to_row(block, geo, 1, 81) == 3
    assert view._text_to_row(block, geo, 2, 2) == 4
    assert view._text_to_row(block, geo, 3, 4) == 5      # the line's end
    assert view._text_to_row(block, geo, 7, 0) == 5      # not there: last
    block.filter = FilterSpec("next|more", regex=True)
    view._measure(block)
    assert view._text_to_row(block, geo, 2, 0) == 0
    assert view._text_to_row(block, geo, 3, 0) == 1
    assert view._text_to_row(block, geo, 1, 45) == 0     # hidden: the first
    block.collapsed = True
    view._measure(block)
    assert view._text_to_row(block, geo, 9, 0) == 0


def test_the_part_of_a_line_that_scrolled_off_has_rows_of_its_own(ui):
    """A line that is still going on, its first rows gone from the screen:
    a position in them is found where they are drawn, above the screen's
    rows, and not at the end of the block."""
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "w" * 135 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert block.store.open_parts == ["w" * 40, "w" * 40]
    assert block.store.end == 0 and rows_of(geo) == (0, 2, 4, 6)
    assert view._text_to_row(block, geo, 0, 0) == 0
    assert view._text_to_row(block, geo, 0, 39) == 0
    assert view._text_to_row(block, geo, 0, 45) == 1
    assert view._text_to_row(block, geo, 0, 79) == 1
    assert view._text_to_row(block, geo, 0, 80) == 2     # on the screen
    assert view._text_to_row(block, geo, 0, 125) == 3
    assert view._text_to_row(block, geo, 1, 2) == 4
    # Hidden by a filter, the line has no rows: the last one, as before.
    block.filter = FilterSpec("next")
    view._measure(block)
    assert rows_of(geo) == (0, 0, 1, 1)
    assert view._text_to_row(block, geo, 0, 45) == 0


def test_wide_characters_in_the_part_that_scrolled_off_take_two_cells(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "\u4f60" * 70 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert block.store.open_parts == ["\u4f60" * 20, "\u4f60" * 20]
    assert rows_of(geo) == (0, 2, 4, 6)
    assert view._text_to_row(block, geo, 0, 19) == 0
    assert view._text_to_row(block, geo, 0, 20) == 1
    assert view._text_to_row(block, geo, 0, 39) == 1
    assert view._text_to_row(block, geo, 0, 40) == 2     # on the screen


def test_a_line_that_was_dropped_counts_as_the_first_row(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "seq", numbered(9))
    block.store.drop_front(2)
    view.frame()
    geo = block.view
    assert rows_of(geo) == (4, 0, 4, 8)
    assert view._row_to_text(block, geo, 0, 0) == (2, 0)
    assert view._text_to_row(block, geo, 0, 0) == 0
    assert view._text_to_row(block, geo, 3, 0) == 1
