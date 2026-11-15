"""The view's wiring: what a new view sets up, how it reads the shell's
output, and what it does when the session speaks to it."""

from __future__ import annotations

import os
import types

import pytest
from PySide6.QtCore import QEvent, QSize, QSocketNotifier, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent

from conftest import COLS, CTRL, NONCE, NONE, SHIFT, prompt, pump_qt, submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec, Session
from jupyterminal.ui import view as view_module
from jupyterminal.ui.view import (FindState, Geo, TerminalView, _duration,
                                  short_path)


class FakePty:
    """Stands in for the shell's pseudo-terminal."""

    def __init__(self, backlog: bool = False, status: int | None = None):
        self.backlog = backlog
        self.status = status

    def flush(self) -> bool:
        return self.backlog

    def poll(self) -> int | None:
        return self.status


def clock(monkeypatch, now: float) -> types.SimpleNamespace:
    """Replace the view's clock by one that shows ``now`` until told
    otherwise."""
    fake = types.SimpleNamespace(now=now)
    fake.monotonic = lambda: fake.now
    monkeypatch.setattr(view_module, "time", fake)
    return fake


def spy(target, name: str) -> list:
    """Replace a method of one object by a recorder of its calls."""
    calls: list = []
    setattr(target, name, lambda *args: calls.append(args))
    return calls


@pytest.fixture
def bare(qapp):
    """A view that was never shown, over a session without a shell."""
    return TerminalView(Session(COLS, 12, nonce=NONCE), Config())


def three_blocks(h):
    return [h.run("echo one", "one\r\n"),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"),
            h.run("false", "", status=1)]


# -- the small helpers --------------------------------------------------------

@pytest.mark.parametrize("seconds,text", [
    (0.0, "0.0s"), (0.94, "0.9s"), (9.5, "9.5s"), (9.94, "9.9s"),
    (10, "10s"), (59.4, "59s"), (60, "1m 00s"), (61.9, "1m 01s"),
    (3599, "59m 59s"), (3600, "1h 00m"), (3725, "1h 02m"),
    (90000, "25h 00m"),
])
def test_a_duration_reads_like_a_stopwatch(seconds, text):
    assert _duration(seconds) == text


def test_a_path_under_the_home_directory_is_shortened(monkeypatch):
    monkeypatch.setenv("HOME", "/home/u")
    assert short_path("/home/u") == "~"
    assert short_path("/home/u/src/x") == "~/src/x"
    assert short_path("/home/user2") == "/home/user2"   # only a prefix
    assert short_path("/etc") == "/etc"


def test_a_home_at_the_root_shortens_no_other_path(monkeypatch):
    monkeypatch.setenv("HOME", "/")
    assert short_path("/") == "~"
    assert short_path("/etc") == "/etc"
    assert short_path("//net/share") == "//net/share"


def test_a_fresh_layout_record_is_empty():
    geo = Geo()
    assert geo.hkey is None and geo.skey is None
    assert geo.cmd_rows == [(0, 0, 0)] and geo.cmd_lines == [""]
    assert geo.cmd_truncated is False and geo.items == []
    assert [geo.header_h, geo.frozen_rows, geo.open_rows, geo.live_rows,
            geo.out_rows, geo.height] == [0] * 6
    assert geo.live_shown is None and geo.live_lines is None
    assert geo.fspec is None and geo.flive is False
    assert [geo.fbase, geo.fupto, geo.ftime] == [0, 0, 0.0]
    assert geo.lay.rows == 0


def test_a_search_starts_without_matches():
    spec = FilterSpec("needle")
    state = FindState(spec)
    assert state.spec is spec
    assert state.matches == [] and state.lines == []
    assert state.current == -1 and state.stamp == -1


# -- a new view ---------------------------------------------------------------

def test_a_new_view_has_the_size_of_its_session_and_nothing_chosen(bare):
    view = bare
    assert (view.cols, view.rows) == (COLS, 12)
    assert view.scroll == 0 and view.follow is True
    assert view.selected == []
    assert view._cursor_block is None and view._range_anchor is None
    assert view._sel is None and view._gsel is None and view._press is None
    assert view._last_click == (0.0, None) and view._click_count == 0
    assert view._hover is None and view._buttons == []
    assert view._frame is None and view._frame_key is None
    assert [view._ui, view._spin, view._flash, view._paint_failed] == [0] * 4
    assert view._find_block is None and view._bar_targets == []
    assert view._seen_running is None and view._notifier is None
    assert view._base_size == view.r.size == 11.0
    assert not view.search.isVisible()


def test_a_new_view_takes_the_keyboard_and_follows_the_mouse(bare):
    view = bare
    assert view.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert view.testAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
    assert view.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    assert view.hasMouseTracking()
    assert view.minimumSize() == QSize(240, 120)
    assert view.bar.orientation() == Qt.Orientation.Vertical
    assert view.bar.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert view.bar.parent() is view and view.search.parent() is view


def test_the_timers_of_a_new_view_are_set_and_idle(bare):
    expected = {"_spinner": (110, False), "_drag_timer": (30, False),
                "_resize_timer": (40, True), "_io_timer": (120, False),
                "_find_timer": (250, True)}
    for name, (interval, single_shot) in expected.items():
        timer = getattr(bare, name)
        assert timer.interval() == interval, name
        assert timer.isSingleShot() is single_shot, name
        assert not timer.isActive(), name
        assert timer.parent() is bare, name


def test_a_new_view_listens_to_its_session(bare):
    view = bare
    session = view.session
    assert session.on_change == view._session_changed
    assert session.on_exit == view._session_exited
    assert session.on_bell == view._bell
    assert session.on_clipboard == view._program_clipboard
    assert session.on_drop == view._blocks_dropped
    assert session.term.color_query == view.theme.rgb
    assert session.term.cell_px == (view.r.cw, view.r.ch)
    titles = []
    view.titleChanged.connect(titles.append)
    session.on_title("vim notes.txt")
    assert titles == ["vim notes.txt"]


def test_every_signal_of_a_new_view_reaches_its_handler(qapp, monkeypatch):
    calls = []
    for name in ("_bar_moved", "_bar_changed", "_bar_step", "_bar_closed",
                 "_tick", "_drag_scroll", "_apply_grid", "_io_tick",
                 "_refresh_find"):
        def handler(self, *args, name=name):
            calls.append((name,) + args)
        monkeypatch.setattr(TerminalView, name, handler)
    view = TerminalView(Session(COLS, 12, nonce=NONCE), Config())
    view.bar.setRange(0, 50)
    view.bar.setValue(7)
    view.search.changed.emit()
    view.search.step.emit(-1)
    view.search.closed.emit()
    for timer in (view._spinner, view._drag_timer, view._resize_timer,
                  view._io_timer, view._find_timer):
        timer.timeout.emit()
    assert calls == [("_bar_moved", 7), ("_bar_changed",), ("_bar_step", -1),
                     ("_bar_closed",), ("_tick",), ("_drag_scroll",),
                     ("_apply_grid",), ("_io_tick",), ("_refresh_find",)]


def test_the_scrollbar_is_drawn_in_the_colour_of_the_theme(qapp):
    config = Config()
    config.colors = {"scrollbar": "#123456"}
    view = TerminalView(Session(COLS, 12, nonce=NONCE), config)
    sheet = view.bar.styleSheet()
    assert "QScrollBar::handle:vertical { background: #123456;" in sheet
    assert "width: 12px;" in sheet


# -- reading the shell's output -----------------------------------------------

def test_without_a_shell_there_is_nothing_to_read(ui):
    h = ui()
    h.view.attach_io()
    assert h.view._notifier is None
    assert not h.view._io_timer.isActive()


def test_output_waiting_on_the_shell_s_descriptor_is_read(ui):
    h = ui()
    view = h.view
    read_end, write_end = os.pipe()
    seen = []

    def read_ready() -> bool:
        seen.append(os.read(read_end, 100))
        return True

    h.session.fileno = lambda: read_end
    h.session.read_ready = read_ready
    view.attach_io()
    notifier = view._notifier
    assert isinstance(notifier, QSocketNotifier)
    assert notifier.socket() == read_end
    assert notifier.type() == QSocketNotifier.Type.Read
    assert notifier.isEnabled() and notifier.parent() is view
    assert view._io_timer.isActive()
    os.write(write_end, b"hello")
    assert pump_qt(h.app, lambda: seen, 5)
    assert seen == [b"hello"]
    view._stop_io()
    assert not notifier.isEnabled()
    assert view._notifier is None and not view._io_timer.isActive()
    view._stop_io()                              # a second time is harmless
    assert view._notifier is None and not view._io_timer.isActive()
    os.close(read_end)
    os.close(write_end)


def test_descriptor_zero_is_a_descriptor_like_any_other(ui):
    h = ui()
    view = h.view
    h.session.fileno = lambda: 0
    view.attach_io()
    notifier = view._notifier
    view._stop_io()                              # before it can fire
    assert isinstance(notifier, QSocketNotifier) and notifier.socket() == 0


def test_reading_stops_when_the_shell_is_gone(ui):
    h = ui()
    view = h.view
    view._io_timer.start()
    h.session.read_ready = lambda: False
    view._readable()
    assert not view._io_timer.isActive()


def test_reading_goes_on_while_the_shell_lives(ui):
    h = ui()
    view = h.view
    view._io_timer.start()
    h.session.read_ready = lambda: True
    view._readable(7)                            # no shell process at all
    assert view._io_timer.isActive() and view._io_timer.interval() == 120
    h.session.pty = FakePty(backlog=False)
    view._readable(7)
    assert view._io_timer.isActive() and view._io_timer.interval() == 120
    h.session.pty = FakePty(backlog=True)        # input it has not taken
    view._readable(7)
    assert view._io_timer.isActive() and view._io_timer.interval() == 4


def test_the_io_timer_does_nothing_without_a_shell(ui):
    h = ui()
    ticks = spy(h.session, "tick")
    h.view._io_tick()
    assert ticks == []


def test_the_io_timer_hurries_while_input_is_held_back(ui):
    h = ui()
    view = h.view
    ticks = spy(h.session, "tick")
    closed = spy(h.session, "close")
    view._io_timer.start()
    h.session.pty = FakePty(backlog=True)
    view._io_tick()
    assert view._io_timer.interval() == 4
    h.session.pty = FakePty(backlog=False)
    view._io_tick()
    assert view._io_timer.interval() == 120
    assert ticks == [(), ()]
    assert closed == [] and view._io_timer.isActive()


def test_the_io_timer_takes_what_a_dead_shell_left_and_stops(ui):
    h = ui()
    view = h.view
    ticks = spy(h.session, "tick")
    closed = spy(h.session, "close")
    answers = [True, True, False, True]
    reads = []

    def read_ready() -> bool:
        reads.append(len(reads))
        return answers[len(reads) - 1]

    h.session.read_ready = read_ready
    h.session.pty = FakePty(status=0)
    view._io_timer.start()
    view._io_tick()
    assert reads == [0, 1, 2]                    # until nothing was left
    assert ticks == [()] and closed == [()]
    assert not view._io_timer.isActive()


def test_the_io_timer_gives_up_on_a_terminal_held_open(ui):
    h = ui()
    view = h.view
    closed = spy(h.session, "close")
    reads = []

    def read_ready() -> bool:
        reads.append(1)
        return True                              # a background job writes on

    h.session.read_ready = read_ready
    h.session.pty = FakePty(status=1)
    view._io_tick()
    assert len(reads) == 64
    assert closed == [()]


# -- when the session changes -------------------------------------------------

def test_a_new_command_makes_the_view_follow_its_output_again(ui):
    h = ui()
    view = h.view
    view.follow = False
    h.session.feed(submit("make"))
    block = h.session.running
    assert view.follow is True and view._seen_running is block
    view.follow = False                          # the user scrolls back
    h.session.feed(b"more output\r\n")
    assert view.follow is False                  # the same command: stay
    h.session.feed(prompt())
    assert view.follow is False                  # its end does not jump
    assert view._seen_running is None


def test_the_spinner_turns_only_while_a_command_runs(ui):
    h = ui()
    view = h.view
    assert not view._spinner.isActive()          # a prompt alone: no spinner
    h.session.feed(submit("make"))
    assert view._spinner.isActive()
    updates = spy(view, "update")
    view._tick()
    assert view._spin == 1 and view._spinner.isActive()
    assert updates == [()]
    h.session.feed(prompt())
    updates.clear()
    view._tick()
    assert view._spin == 2 and not view._spinner.isActive()
    assert updates == [()]


def test_a_change_of_the_session_schedules_a_search_refresh(ui):
    h = ui()
    view = h.view
    block = h.run("make", "error\r\n")
    updates = spy(view, "update")
    view._session_changed()
    assert not view._find_timer.isActive()       # nothing is searched
    assert updates == [()]
    view._find_block = block
    view._session_changed()
    assert view._find_timer.isActive()
    assert updates == [(), ()]


def test_a_change_of_the_session_drops_a_selection_on_a_gone_screen(ui):
    h = ui()
    view = h.view
    gone = Session(COLS, 4, nonce=NONCE).input   # not a screen of this view
    view._gsel = (gone, (0, 0), (0, 3))
    view._session_changed()
    assert view._gsel is None
    shown = (h.session.input, (0, 0), (0, 1))
    view._gsel = shown
    view._session_changed()
    assert view._gsel is shown


def test_the_end_of_the_session_is_announced(ui):
    h = ui()
    view = h.view
    ended = []
    view.sessionEnded.connect(lambda: ended.append(True))
    updates = spy(view, "update")
    h.session.on_exit(0)
    assert ended == [True] and updates == [()]


def test_dropped_blocks_leave_the_selection(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    view.selected = [one.id, ls.id, bad.id]
    view._cursor_block = ls.id
    before = view._ui
    view._blocks_dropped([ls])
    assert view.selected == [one.id, bad.id]
    assert view._cursor_block == bad.id          # the last one still chosen
    assert view._ui == before + 1
    view._blocks_dropped([one])
    assert view.selected == [bad.id] and view._cursor_block == bad.id
    view._blocks_dropped([bad])
    assert view.selected == [] and view._cursor_block is None
    view.selected = [one.id, ls.id, bad.id]
    view._cursor_block = one.id                  # not the last one chosen
    view._blocks_dropped([bad])
    assert view.selected == [one.id, ls.id] and view._cursor_block == one.id


def test_dropped_blocks_take_a_text_selection_that_touched_them(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    assert [one.id, ls.id, bad.id] == [1, 2, 3]
    chosen = ((ls.id, 1, 1, 1), (bad.id, 1, 1, 1))
    view._sel = chosen
    view._blocks_dropped([one])                  # block 1 is not line 1
    assert view._sel == chosen
    view._blocks_dropped([bad])                  # where the selection ends
    assert view._sel is None
    view._sel = chosen
    view._blocks_dropped([ls])                   # where it starts
    assert view._sel is None


def test_dropped_blocks_end_a_search_in_them(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    view.open_find(ls)
    assert view._find_block is ls and view.search.isVisible()
    view._blocks_dropped([one])
    assert view._find_block is ls and view.search.isVisible()
    view._blocks_dropped([ls])
    assert view._find_block is None and not view.search.isVisible()


def test_dropped_blocks_are_no_longer_filtered_by_the_bar(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    view.open_filter([one, ls])
    view.search.edit.setText("txt")
    assert view.search.status.text() == "2 of 3"
    view._blocks_dropped([bad])                  # not one of the bar's
    assert view._bar_targets == [one, ls] and view.search.isVisible()
    view._blocks_dropped([one])
    assert view._bar_targets == [ls] and view.search.isVisible()
    view.search.edit.setText("c.txt")            # counts what is left
    assert view.search.status.text() == "1 of 2"
    before = view._ui
    view._blocks_dropped([ls])                   # nothing left to filter
    assert view._bar_targets == [] and not view.search.isVisible()
    assert view._ui == before + 2                # the bar closing, and this
    view._blocks_dropped([ls])                   # no bar: nothing to close
    assert view._ui == before + 3


def test_clearing_the_blocks_tells_the_view(ui):
    h = ui()
    view = h.view
    one, ls, bad = three_blocks(h)
    view.selected = [ls.id]
    view._cursor_block = ls.id
    h.session.clear_blocks()
    assert view.selected == [] and view._cursor_block is None


def test_a_bell_is_ignored_unless_it_is_to_be_shown(ui, monkeypatch):
    h = ui()
    view = h.view
    clock(monkeypatch, 41.5)
    updates = spy(view, "update")
    h.session.feed(b"\x07")
    assert view._flash == 0.0
    assert updates == [()]                       # the feed alone


def test_a_visible_bell_flashes_the_view(ui, monkeypatch):
    config = Config()
    config.bell = "visual"
    h = ui(config=config)
    view = h.view
    clock(monkeypatch, 41.5)
    shots = []
    timer = types.SimpleNamespace(
        singleShot=lambda delay, call: shots.append((delay, call)))
    monkeypatch.setattr(view_module, "QTimer", timer)
    updates = spy(view, "update")
    view._bell()
    assert view._flash == 41.5
    assert updates == [()]
    assert shots == [(120, view.update)]         # and once more to end it


def test_a_program_may_write_the_clipboard_only_if_allowed(ui):
    h = ui()
    QGuiApplication.clipboard().setText("mine")
    h.config.allow_clipboard_write = False
    h.session.feed(b"\x1b]52;c;dGhlaXJz\x07")    # "theirs", base64
    assert QGuiApplication.clipboard().text() == "mine"
    h.config.allow_clipboard_write = True
    h.session.feed(b"\x1b]52;c;dGhlaXJz\x07")
    assert QGuiApplication.clipboard().text() == "theirs"


# -- the key table ------------------------------------------------------------

def test_shortcuts_are_split_into_those_of_the_view_and_of_the_blocks(ui):
    view = ui().view
    assert view._keys_global["Ctrl+Shift+C"] == "copy"
    assert view._keys_global["Ctrl+Alt+Shift+C"] == "copy_command_and_output"
    assert view._keys_global["Shift+Ins"] == "paste_selection"
    assert view._keys_global["Ctrl+Up"] == "select_newest_block"
    assert view._keys_blocks["Up"] == "blocks.previous"
    assert view._keys_blocks["Esc"] == "blocks.focus_input"
    assert not any(name.startswith("blocks.")
                   for name in view._keys_global.values())
    assert all(name.startswith("blocks.")
               for name in view._keys_blocks.values())


def test_rebuilding_the_key_table_forgets_the_old_shortcuts(ui):
    view = ui().view
    view.config.keys = {"copy": ["", "Ctrl+Y"],      # an empty one is skipped
                        "blocks.next": ["J", "Down"]}
    view._build_keymap()
    assert view._keys_global == {"Ctrl+Y": "copy"}
    assert view._keys_blocks == {"J": "blocks.next", "Down": "blocks.next"}


@pytest.mark.parametrize("key,mods,names", [
    (Qt.Key.Key_C, CTRL | SHIFT, ["Ctrl+Shift+C"]),
    (Qt.Key.Key_Plus, CTRL | SHIFT, ["Ctrl+Shift++", "Ctrl++"]),
    (Qt.Key.Key_Plus, CTRL, ["Ctrl++"]),
    (Qt.Key.Key_Backtab, SHIFT, ["Shift+Tab"]),
    (Qt.Key.Key_Backtab, NONE, ["Shift+Tab"]),
    (Qt.Key.Key_Space, SHIFT, ["Shift+Space"]),
    (Qt.Key.Key_Exclam, SHIFT, ["Shift+!", "!"]),
    (Qt.Key.Key_AsciiTilde, SHIFT, ["Shift+~", "~"]),
    (Qt.Key.Key_At, SHIFT, ["Shift+@", "@"]),
    (Qt.Key.Key_A, SHIFT, ["Shift+A"]),
    (Qt.Key.Key_Z, SHIFT, ["Shift+Z"]),
    (Qt.Key.Key_BracketLeft, SHIFT, ["Shift+[", "["]),
    (Qt.Key.Key_5, Qt.KeyboardModifier.KeypadModifier, ["5"]),
    (Qt.Key.Key_5, CTRL | Qt.KeyboardModifier.GroupSwitchModifier,
     ["Ctrl+5"]),
])
def test_a_key_press_is_named_the_way_shortcuts_are_written(key, mods, names):
    event = QKeyEvent(QEvent.Type.KeyPress, key, mods)
    assert TerminalView._combos(event) == names


def test_the_key_after_the_last_symbol_is_not_a_shifted_symbol():
    event = QKeyEvent(QEvent.Type.KeyPress, 0x7F, SHIFT)
    assert len(TerminalView._combos(event)) == 1
