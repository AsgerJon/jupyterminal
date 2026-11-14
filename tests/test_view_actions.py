"""What the view does to blocks on request: copying, pasting, the named
actions that keys are bound to, zooming, collapsing, re-running, saving,
and the block menu."""

from __future__ import annotations

import errno
import os
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

from conftest import submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec
from jupyterminal.shell.integration import SET_BUFFER_KEY
from jupyterminal.ui import view as view_module
from jupyterminal.ui.bars import FILTER, FIND
from jupyterminal.ui.view import (BLOCK_BOTTOM, HEAD_BOTTOM, HEAD_TOP,
                                  PAD_LEFT)
from test_view_selection import (CLIPBOARD, SELECTION, notice, numbered,
                                 repaints, scene, state, tall_scene,
                                 use_board)


def pasted(text: str) -> bytes:
    """Text as the shell receives a paste (it asked for brackets)."""
    return b"\x1b[200~" + text.encode() + b"\x1b[201~"


def use_menu(monkeypatch, pick: str | None = None) -> list:
    """Menus that are not shown: each notes whose it is, what it offers
    and where it would open, and chooses the entry ``pick`` by itself."""
    opened: list = []

    class Menu(QMenu):
        def exec(self, pos) -> None:
            actions = self.actions()
            opened.append((self.parent(), [
                None if a.isSeparator() else a.text() for a in actions], pos))
            for action in actions:
                if action.text() == pick:
                    action.trigger()

    monkeypatch.setattr(view_module, "QMenu", Menu)
    return opened


class Buffer:
    """Stands in for the shell integration that takes a command for the
    input line."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def write_buffer(self, text: str) -> None:
        self.texts.append(text)


def labels(view, block) -> list:
    return [entry and entry[0] for entry in view.menu_actions(block)]


def choose(view, block, label: str) -> None:
    """Take one entry of a block's menu."""
    dict(entry for entry in view.menu_actions(block) if entry)[label]()


# -- copy --------------------------------------------------------------------

def test_copy_with_nothing_selected_leaves_the_clipboard_alone(ui):
    h = ui()
    scene(h)
    QGuiApplication.clipboard().setText("stale")
    assert h.view.copy() == ""
    assert h.clipboard() == "stale"


def test_copy_takes_the_output_of_the_selected_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(ls)
    assert view.copy() == "a.txt  b.txt\nc.txt"
    assert h.clipboard() == "a.txt  b.txt\nc.txt"
    view.select_block(one, "toggle")
    assert view.copy() == "one\n\na.txt  b.txt\nc.txt"
    assert h.clipboard() == "one\n\na.txt  b.txt\nc.txt"
    view.set_filter([ls], FilterSpec("c."))
    assert view.copy() == "one\n\nc.txt"
    # A selected block without output empties the clipboard: the copy
    # happened, and that is what it had.
    view.select_block(bad)
    assert view.copy() == ""
    assert h.clipboard() == ""


def test_copy_prefers_selected_text(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view._sel = ((ls.id, 1, 0, 0), (ls.id, 1, 0, 5))
    assert view.copy() == "a.txt"
    assert h.clipboard() == "a.txt"
    # A selection of no text, and no block either: nothing to copy.
    view._sel = ((ls.id, 1, 0, 2), (ls.id, 1, 0, 2))
    assert view.copy() == ""
    assert h.clipboard() == "a.txt"


def test_copying_blocks_as_output_commands_or_both(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    assert view.copy_blocks([one, bad, ls], "output") == \
        "one\n\n\n\na.txt  b.txt\nc.txt"
    assert h.clipboard() == "one\n\n\n\na.txt  b.txt\nc.txt"
    assert view.copy_blocks([loop, bad], "command") == \
        "for i in a b\ndo echo $i\ndone\n\nfalse"
    assert h.clipboard() == "for i in a b\ndo echo $i\ndone\n\nfalse"
    assert view.copy_blocks([one, bad], "both") == "$ echo one\none\n\n$ false"
    assert h.clipboard() == "$ echo one\none\n\n$ false"
    assert view.copy_blocks([], "output") == ""
    assert h.clipboard() == ""


# -- paste -------------------------------------------------------------------

def test_paste_takes_the_clipboard_or_the_primary_selection(ui, monkeypatch):
    board = use_board(monkeypatch)
    board.texts = {CLIPBOARD: "from clipboard", SELECTION: "from selection"}
    h = ui()
    view = h.view
    view.paste()
    assert h.sent == [pasted("from clipboard")]
    view.paste(True)
    assert h.sent[1:] == [pasted("from selection")]
    view.paste(False)
    assert h.sent[2:] == [pasted("from clipboard")]


def test_paste_without_a_primary_selection_takes_the_clipboard(
        ui, monkeypatch):
    board = use_board(monkeypatch, selection=False)
    board.texts = {CLIPBOARD: "from clipboard", SELECTION: "unreachable"}
    h = ui()
    h.view.paste(True)
    assert h.sent == [pasted("from clipboard")]


def test_pasting_nothing_does_nothing(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(one)
    view._set_scroll(0)
    for nothing in ("", "\n", "\r\n", "\r", "\n\n\n"):
        view.paste_text(nothing)
    assert h.sent == []
    assert view.selected == [one.id] and view.follow is False


def test_one_pasted_line_loses_its_newline_and_is_not_asked_about(
        ui, monkeypatch):
    asked = []
    monkeypatch.setattr(view_module, "confirm_paste",
                        lambda *args: asked.append(args))
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    view.select_block(blocks[0])
    view._set_scroll(0)
    assert view.follow is False
    view.paste_text("echo one\r\n")
    assert h.sent == [pasted("echo one")]
    assert asked == []
    # The keyboard is the shell's again, and its prompt is in view.
    assert view.selected == [] and view.follow is True
    view.paste_text("echo two\n\n")
    view.paste_text("echo three\r")
    assert h.sent[1:] == [pasted("echo two"), pasted("echo three")]
    assert asked == []


def test_several_pasted_lines_are_asked_about_first(ui, monkeypatch):
    asked = []
    answers = [False, True, True]

    def confirm(parent, text, font) -> bool:
        asked.append((parent, text, font))
        return answers.pop(0)

    monkeypatch.setattr(view_module, "confirm_paste", confirm)
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(one)
    view._set_scroll(0)
    view.paste_text("echo one\r\necho two\r\n")
    assert asked == [(view, "echo one\necho two\n", view.r.font)]
    # Declined: nothing is sent and nothing else changes.
    assert h.sent == []
    assert view.selected == [one.id] and view.follow is False
    view.paste_text("echo one\recho two")
    assert asked[1:] == [(view, "echo one\necho two", view.r.font)]
    assert h.sent == [pasted("echo one\recho two")]
    assert view.selected == [] and view.follow is True
    view.paste_text("a\nb\n")                         # its newlines stay
    assert h.sent[1:] == [pasted("a\rb\r")]
    assert answers == []


def test_several_pasted_lines_go_unasked_when_configured_so(ui, monkeypatch):
    asked = []
    monkeypatch.setattr(view_module, "confirm_paste",
                        lambda *args: asked.append(args))
    h = ui(config=Config(confirm_multiline_paste=False))
    h.view.paste_text("echo one\necho two")
    assert h.sent == [pasted("echo one\recho two")]
    assert asked == []


# -- the named actions -------------------------------------------------------

def test_the_copy_actions(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    QGuiApplication.clipboard().setText("stale")
    view._do("copy")
    view._do("copy_command_and_output")
    assert h.clipboard() == "stale"                   # nothing is selected
    view.select_block(ls)
    view._do("copy")
    assert h.clipboard() == "a.txt  b.txt\nc.txt"
    view.select_block(one, "toggle")
    view._do("copy_command_and_output")
    assert h.clipboard() == "$ echo one\none\n\n$ ls\na.txt  b.txt\nc.txt"
    assert h.sent == []


def test_the_paste_actions(ui, monkeypatch):
    board = use_board(monkeypatch)
    board.texts = {CLIPBOARD: "from clipboard", SELECTION: "from selection"}
    h = ui()
    h.view._do("paste")
    assert h.sent == [pasted("from clipboard")]
    h.view._do("paste_selection")
    assert h.sent[1:] == [pasted("from selection")]


def test_select_newest_block_selects_it_and_brings_it_into_view(ui):
    h = ui(rows=8)
    view = h.view
    view._do("select_newest_block")                   # there is none yet
    assert state(view) == ([], None, None)
    blocks = tall_scene(h)
    view._set_scroll(0)
    view._do("select_newest_block")
    newest = blocks[-1]
    assert state(view) == ([newest.id], newest.id, newest.id)
    assert view.scroll == view.frame().max_scroll > 0


def test_clear_blocks_removes_all_but_a_running_command(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    h.session.feed(submit("tail -f log") + b"first\r\nsecond\r\n")
    h.pump()
    running = h.session.blocks[-1]
    view = h.view
    view.select_block(running)
    view._sel = ((running.id, 1, 0, 0), (running.id, 1, 0, 3))
    view._set_scroll(0)
    assert view.follow is False
    view._do("clear_blocks")
    assert h.session.blocks == [running]
    assert view.selected == [] and view._cursor_block is None
    assert view._sel is None
    assert view.follow is True
    assert h.sent == []
    assert blocks[0].id not in view.frame().ids


def test_find_in_block_means_the_selected_block_or_else_the_newest(ui):
    h = ui()
    view = h.view
    view._do("find_in_block")                         # no block to search
    assert not view.search.isVisible() and view._find_block is None
    one, loop, bad, ls = scene(h)
    view._do("find_in_block")
    assert view.search.isVisible() and view.search.mode == FIND
    assert view._find_block is ls
    view.select_block(loop)
    view._do("find_in_block")
    assert view._find_block is loop
    assert view._action_block() is loop


def test_filter_output_means_the_selected_blocks_or_else_the_newest(ui):
    h = ui()
    view = h.view
    view._do("filter_output")                         # no block to filter
    assert not view.search.isVisible() and view._bar_targets == []
    assert view._action_block() is None
    one, loop, bad, ls = scene(h)
    view._do("filter_output")
    assert view.search.isVisible() and view.search.mode == FILTER
    assert view._bar_targets == [ls]
    assert view._action_block() is ls
    view.select_block(one)
    view.select_block(bad, "toggle")
    view._do("filter_output")
    assert view._bar_targets == [one, bad]


def test_scrolling_by_pages_and_to_the_ends(ui):
    h = ui(rows=8)
    numbered(h, 60)
    view = h.view
    frame = view.frame()
    page = frame.view_h - view.r.ch
    bottom = frame.max_scroll
    assert bottom > 2 * page > 0 and view.scroll == bottom
    for up, down in (("scroll_page_up", "scroll_page_down"),
                     ("blocks.page_up", "blocks.page_down")):
        view._do(up)
        assert view.scroll == bottom - page and view.follow is False
        view._do(up)
        assert view.scroll == bottom - 2 * page
        view._do(down)
        assert view.scroll == bottom - page
        view._do(down)
        view._do(down)                                # no further
        assert view.scroll == bottom and view.follow is True
    view._do("scroll_to_top")
    assert view.scroll == 0 and view.follow is False
    view._do("scroll_page_up")                        # no further
    assert view.scroll == 0
    view._do("scroll_page_down")
    assert view.scroll == page
    view._do("scroll_to_bottom")
    assert view.follow is True
    view.frame()
    assert view.scroll == bottom


def test_the_zoom_actions_change_the_font_size(ui):
    h = ui(config=Config(font_size=13.0))
    view = h.view
    assert view.r.size == 13.0
    view._do("zoom_in")
    assert view.r.size == 14.0
    view._do("zoom_in")
    assert view.r.size == 15.0
    view._do("zoom_reset")                            # as configured
    assert view.r.size == 13.0
    view._do("zoom_out")
    assert view.r.size == 12.0
    view._do("zoom_reset")
    assert view.r.size == 13.0


def test_the_actions_that_walk_through_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(bad)
    view._do("blocks.previous")
    assert state(view) == ([loop.id], loop.id, loop.id)
    view._do("blocks.next")
    assert state(view) == ([bad.id], bad.id, bad.id)
    view._do("blocks.next")
    assert state(view) == ([ls.id], ls.id, ls.id)
    view._do("blocks.extend_previous")
    view._do("blocks.extend_previous")
    assert state(view) == ([loop.id, bad.id, ls.id], loop.id, ls.id)
    view._do("blocks.extend_next")
    assert state(view) == ([bad.id, ls.id], bad.id, ls.id)
    view._do("blocks.first")
    assert state(view) == ([one.id], one.id, one.id)
    view._do("blocks.last")
    assert state(view) == ([ls.id], ls.id, ls.id)
    assert h.sent == []


def test_the_first_and_the_last_block_are_brought_into_view(ui):
    h = ui(rows=8)
    view = h.view
    view._do("blocks.first")                          # there is none yet
    view._do("blocks.last")
    assert state(view) == ([], None, None)
    blocks = tall_scene(h)
    bottom = view.frame().max_scroll
    view._do("blocks.first")
    assert view.selected == [blocks[0].id] and view.scroll == 0
    view._do("blocks.last")
    assert view.selected == [blocks[-1].id] and view.scroll == bottom


def test_scrolling_to_the_ends_of_the_block_the_cursor_is_on(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    frame = view.frame()
    view._set_scroll(3)
    view._do("blocks.scroll_to_top")                  # no cursor: no move
    view._do("blocks.scroll_to_bottom")
    assert view.scroll == 3
    assert view._cursor() is None
    view.select_block(blocks[3])
    assert view._cursor() is blocks[3]
    view._set_scroll(3)
    view._do("blocks.scroll_to_top")
    assert view.scroll == frame.tops[3]
    view._do("blocks.scroll_to_bottom")
    assert view.scroll == frame.tops[3] + frame.heights[3] - frame.view_h
    view._cursor_block = blocks[-1].id + 9            # a block long gone
    assert view._cursor() is None


def test_escape_from_the_blocks_gives_the_keyboard_to_the_shell(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    view.open_find(loop)
    assert QApplication.focusWidget() is view.search.edit
    view._do("blocks.focus_input")
    assert state(view) == ([], None, None)
    assert QApplication.focusWidget() is view and view.hasFocus()


def test_the_menu_key_opens_the_menu_at_the_block(ui, monkeypatch):
    opened = use_menu(monkeypatch)
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    view._do("blocks.menu")                           # no cursor: no menu
    assert opened == []
    view.select_block(blocks[2])
    frame = view.frame()
    view._set_scroll(frame.tops[2] - 2 * view.r.ch)
    assert view.scroll == frame.tops[2] - 2 * view.r.ch > 0
    view._do("blocks.menu")
    place = view.mapToGlobal(QPoint(PAD_LEFT + 40, 3 * view.r.ch))
    assert opened == [(view, labels(view, blocks[2]), place)]
    # The block starts above the view: the menu opens at the view's top.
    view._set_scroll(frame.tops[2] + view.r.ch)
    assert view.scroll == frame.tops[2] + view.r.ch
    view._do("blocks.menu")
    place = view.mapToGlobal(QPoint(PAD_LEFT + 40, view.r.ch))
    assert opened[1:] == [(view, labels(view, blocks[2]), place)]


def test_toggle_collapse_folds_the_selected_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view._do("blocks.toggle_collapse")                # none selected
    assert [b.collapsed for b in (one, loop, bad, ls)] == [False] * 4
    view.select_block(one)
    view.select_block(ls, "toggle")
    view._do("blocks.toggle_collapse")
    assert [b.collapsed for b in (one, loop, bad, ls)] == \
        [True, False, False, True]
    view._do("blocks.toggle_collapse")
    assert [b.collapsed for b in (one, loop, bad, ls)] == [False] * 4


def test_an_action_nobody_knows_does_nothing(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    QGuiApplication.clipboard().setText("stale")
    asked = repaints(view)
    before = view.frame()
    laid_out = view._ui
    view._do("blocks.no_such_action")
    view._do("")
    assert state(view) == ([loop.id], loop.id, loop.id)
    assert h.sent == [] and h.clipboard() == "stale" and asked == []
    assert view.frame() is before and view._ui == laid_out
    assert not view.search.isVisible()


# -- zoom --------------------------------------------------------------------

def test_a_new_font_size_lays_everything_out_again(ui):
    h = ui()
    block = numbered(h, 3)
    view = h.view
    old = block.view
    cell = (view.r.cw, view.r.ch)
    assert (view.cols, view.rows) == (h.session.cols, h.session.rows)
    asked = repaints(view)
    size = view.r.size
    laid_out = view._ui
    view.set_font_size(size + 6)
    assert view.r.size == size + 6
    assert view._ui == laid_out + 2                   # the font, the grid
    cw, ch = view.r.cw, view.r.ch
    assert cw > cell[0] and ch > cell[1]
    # Fewer cells fit now, and the shell is told so.
    grid = view.grid_for(view.width(), view.height())
    assert (view.cols, view.rows) == grid
    assert grid[0] < 40 and grid[1] < 12
    assert (h.session.cols, h.session.rows) == grid
    assert h.session.term.cell_px == (cw, ch)
    assert asked
    frame = view.frame()
    assert block.view is not old
    assert block.view.header_h == HEAD_TOP + ch + HEAD_BOTTOM
    assert frame.heights == [HEAD_TOP + ch + HEAD_BOTTOM + 3 * ch
                             + BLOCK_BOTTOM + 1]


def test_zooming_reports_the_cell_size_even_if_the_grid_stays(ui):
    h = ui()
    block = h.run("echo one", "one\r\n")
    view = h.view
    view.resize(view.minimumSize())                   # as small as it gets
    h.pump()
    view.set_font_size(40)
    view.frame()
    grid = (view.cols, view.rows)
    assert grid == (10, 3)                            # the least there is
    before = (view.r.cw, view.r.ch)
    view.set_font_size(41)
    cw, ch = view.r.cw, view.r.ch
    assert (cw, ch) != before
    assert (view.cols, view.rows) == grid == (h.session.cols, h.session.rows)
    assert h.session.term.cell_px == (cw, ch)
    view.frame()
    rows = len(block.view.cmd_rows)
    assert block.view.header_h == HEAD_TOP + rows * ch + HEAD_BOTTOM


# -- collapse ----------------------------------------------------------------

def test_collapsing_and_expanding_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    frame = view.frame()
    tall = list(frame.heights)
    asked = repaints(view)
    laid_out = view._ui
    view.set_collapsed([], True)                      # no blocks: nothing
    assert asked == [] and view.frame() is frame
    assert view._ui == laid_out
    view.set_collapsed([one, ls], True)
    assert [b.collapsed for b in (one, loop, bad, ls)] == \
        [True, False, False, True]
    assert len(asked) == 1 and view._ui == laid_out + 1
    folded = view.frame().heights
    assert folded == [one.view.header_h + 1, tall[1], tall[2],
                      ls.view.header_h + 1]
    assert folded[0] < tall[0] and folded[3] < tall[3]
    view.set_collapsed([one, loop], None)             # some are open: fold
    assert [b.collapsed for b in (one, loop, bad, ls)] == \
        [True, True, False, True]
    view.set_collapsed([one, loop], None)             # all folded: open
    assert [b.collapsed for b in (one, loop, bad, ls)] == \
        [False, False, False, True]
    view.set_collapsed([ls], False)
    assert view.frame().heights == tall
    assert len(asked) == 4
    # Asked for outright, the state does not depend on what it was.
    view.set_collapsed([one], False)
    assert [b.collapsed for b in (one, loop, bad, ls)] == [False] * 4
    view.set_collapsed([one, loop], True)
    view.set_collapsed([one, loop], True)
    assert [b.collapsed for b in (one, loop, bad, ls)] == \
        [True, True, False, False]


# -- re-run ------------------------------------------------------------------

def test_rerun_puts_the_command_on_the_input_line_unsubmitted(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    buffer = h.session.runtime = Buffer()
    view = h.view
    view.select_block(blocks[0])
    view.open_find(blocks[0])                         # the keyboard is there
    view._set_scroll(0)
    view.rerun(blocks[2])
    assert buffer.texts == ["seq 2"]
    assert h.sent == [SET_BUFFER_KEY]                 # and no Enter
    assert view.selected == [] and view.follow is True
    assert QApplication.focusWidget() is view


def test_background_output_has_no_command_to_rerun(ui):
    h = ui()
    one = h.run("echo one", "one\r\n")
    job = notice(h, "[1]  + done  sleep 1")
    buffer = h.session.runtime = Buffer()
    view = h.view
    view.select_block(one)
    view._set_scroll(0)
    view.rerun(job)
    assert buffer.texts == [] and h.sent == []
    assert view.selected == [one.id]


# -- save --------------------------------------------------------------------

def test_saving_output_asks_for_a_file_and_writes_it(
        ui, monkeypatch, tmp_path):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    target = tmp_path / "out.txt"
    asked = []

    def pick_file(parent, title, start):
        asked.append((parent, title, start))
        return str(target), "All Files (*)"

    monkeypatch.setattr(view_module, "QFileDialog",
                        SimpleNamespace(getSaveFileName=pick_file))
    view.save_output([one, ls])
    assert asked == [(view, "Save output to file", os.path.expanduser("~"))]
    assert target.read_text(encoding="utf-8") == \
        "one\n\na.txt  b.txt\nc.txt\n"


def test_saving_output_can_be_called_off(ui, monkeypatch, tmp_path):
    h = ui()
    one, loop, bad, ls = scene(h)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(view_module, "QFileDialog", SimpleNamespace(
        getSaveFileName=lambda *args: ("", "")))
    h.view.save_output([one])
    assert list(tmp_path.iterdir()) == []


def test_a_file_that_cannot_be_written_is_reported(ui, monkeypatch, tmp_path):
    """In plain text: the path is shown as it is, also where it looks
    like markup to Qt."""
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    shown = []

    class Box(QMessageBox):
        def exec(self) -> int:
            shown.append((self.parent(), self.icon(), self.windowTitle(),
                          self.text(), self.textFormat(),
                          self.standardButtons()))
            return 0

    folder = tmp_path / "<b>bold<"
    folder.mkdir()
    monkeypatch.setattr(view_module, "QFileDialog", SimpleNamespace(
        getSaveFileName=lambda *args: (str(folder), "")))
    monkeypatch.setattr(view_module, "QMessageBox", Box)
    view.save_output([one])
    assert shown == [(view, QMessageBox.Icon.Warning, "Save output to file",
                      f"Could not write {folder}:\n"
                      f"{os.strerror(errno.EISDIR)}",
                      Qt.TextFormat.PlainText,
                      QMessageBox.StandardButton.Ok)]
    # The box is gone again once it has been answered.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert view.findChildren(QMessageBox) == []


def test_written_output_is_what_copy_gives_plus_a_final_newline(ui, tmp_path):
    h = ui()
    one = h.run("echo", "héllo ✓  \r\n\tb\r\n")
    bad = h.run("false", "", status=1)
    path = tmp_path / "out.txt"
    h.view.write_output([one], str(path))
    assert path.read_bytes() == "héllo ✓\n\tb\n".encode("utf-8")
    h.view.write_output([one, bad, one], str(path))   # the file is replaced
    assert path.read_text(encoding="utf-8") == \
        "héllo ✓\n\tb\n\n\n\nhéllo ✓\n\tb\n"
    h.view.write_output([bad], str(path))             # nothing: no newline
    assert path.read_bytes() == b""
    h.view.write_output([], str(path))
    assert path.read_bytes() == b""


# -- the block menu ----------------------------------------------------------

def test_the_menu_of_one_command(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    assert labels(h.view, ls) == [
        "Copy output", "Copy command", "Copy command and output", None,
        "Find in block…", "Filter output…", "Collapse", None,
        "Re-run command", "Save output to file…"]


def test_the_menu_of_background_output(ui):
    h = ui()
    h.run("echo one", "one\r\n")
    job = notice(h, "[1]  + done  sleep 1")
    assert labels(h.view, job) == [
        "Copy output", None, "Find in block…", "Filter output…",
        "Collapse", None, "Save output to file…"]


def test_the_menu_of_several_selected_blocks(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    view.select_block(ls, "toggle")
    several = ["Copy output", "Copy command", "Copy command and output",
               None, "Filter output…", "Collapse", None,
               "Save output to file…"]
    assert labels(view, loop) == several
    assert labels(view, ls) == several
    # A block outside the selection gets a menu of its own.
    assert labels(view, one) == [
        "Copy output", "Copy command", "Copy command and output", None,
        "Find in block…", "Filter output…", "Collapse", None,
        "Re-run command", "Save output to file…"]
    choose(view, one, "Copy output")
    assert h.clipboard() == "one"
    choose(view, ls, "Copy output")
    assert h.clipboard() == "a\nb\n\na.txt  b.txt\nc.txt"


def test_the_menu_of_selected_background_output_has_no_commands(ui):
    h = ui()
    first = notice(h, "[1]  + done  sleep 1")
    h.run("echo one", "one\r\n")
    second = notice(h, "[2]  + done  sleep 2")
    view = h.view
    view.select_block(first)
    view.select_block(second, "toggle")
    assert labels(view, first) == [
        "Copy output", None, "Filter output…", "Collapse", None,
        "Save output to file…"]


def test_the_menu_offers_to_clear_a_filter_and_to_expand(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    view.select_block(ls, "toggle")
    view.set_filter([ls], FilterSpec("c."))
    view.set_collapsed([ls], True)
    # One of the two is filtered, one of the two is folded.
    assert labels(view, loop) == [
        "Copy output", "Copy command", "Copy command and output", None,
        "Filter output…", "Clear filter", "Collapse", None,
        "Save output to file…"]
    choose(view, loop, "Collapse")
    assert loop.collapsed and ls.collapsed and not one.collapsed
    assert "Expand" in labels(view, loop) and \
        "Collapse" not in labels(view, loop)
    choose(view, loop, "Expand")
    assert not loop.collapsed and not ls.collapsed
    choose(view, loop, "Clear filter")
    assert ls.filter is None and "Clear filter" not in labels(view, loop)
    # The same for one block.
    view.set_filter([one], FilterSpec("o"))
    view.set_collapsed([one], True)
    assert labels(view, one) == [
        "Copy output", "Copy command", "Copy command and output", None,
        "Find in block…", "Filter output…", "Clear filter",
        "Expand", None, "Re-run command", "Save output to file…"]
    choose(view, one, "Clear filter")
    choose(view, one, "Expand")
    assert one.filter is None and not one.collapsed


def test_the_entries_of_the_menu_do_what_they_say(ui, monkeypatch, tmp_path):
    h = ui()
    one, loop, bad, ls = scene(h)
    buffer = h.session.runtime = Buffer()
    view = h.view
    choose(view, loop, "Copy output")
    assert h.clipboard() == "a\nb"
    choose(view, loop, "Copy command")
    assert h.clipboard() == "for i in a b\ndo echo $i\ndone"
    choose(view, loop, "Copy command and output")
    assert h.clipboard() == "$ for i in a b\ndo echo $i\ndone\na\nb"
    choose(view, loop, "Find in block…")
    assert view.search.mode == FIND and view._find_block is loop
    choose(view, loop, "Filter output…")
    assert view.search.mode == FILTER and view._bar_targets == [loop]
    assert view._find_block is None
    choose(view, loop, "Collapse")
    assert loop.collapsed and not ls.collapsed
    choose(view, loop, "Re-run command")
    assert buffer.texts == ["for i in a b\ndo echo $i\ndone"]
    target = tmp_path / "saved.txt"
    monkeypatch.setattr(view_module, "QFileDialog", SimpleNamespace(
        getSaveFileName=lambda *args: (str(target), "")))
    choose(view, loop, "Save output to file…")
    assert target.read_text(encoding="utf-8") == "a\nb\n"
    # With several blocks selected, the entries mean all of them.
    view.select_block(one)
    view.select_block(ls, "toggle")
    choose(view, ls, "Copy command")
    assert h.clipboard() == "echo one\n\nls"
    choose(view, ls, "Copy command and output")
    assert h.clipboard() == "$ echo one\none\n\n$ ls\na.txt  b.txt\nc.txt"
    choose(view, ls, "Filter output…")
    assert view._bar_targets == [one, ls]
    choose(view, ls, "Save output to file…")
    assert target.read_text(encoding="utf-8") == \
        "one\n\na.txt  b.txt\nc.txt\n"


def test_opening_the_menu_shows_the_entries_and_runs_the_chosen_one(
        ui, monkeypatch):
    opened = use_menu(monkeypatch, pick="Copy command")
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    QGuiApplication.clipboard().setText("stale")
    asked = repaints(view)
    place = view.mapToGlobal(QPoint(31, 17))
    view.open_menu(ls, place)
    assert opened == [(view, labels(view, ls), place)]
    assert h.clipboard() == "ls"
    assert len(asked) == 1


def test_closing_the_menu_without_a_choice_changes_nothing(ui, monkeypatch):
    opened = use_menu(monkeypatch)
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    QGuiApplication.clipboard().setText("stale")
    asked = repaints(view)
    view.open_menu(loop, QPoint(5, 6))
    assert opened == [(view, labels(view, loop), QPoint(5, 6))]
    assert h.clipboard() == "stale" and not loop.collapsed
    assert len(asked) == 1
