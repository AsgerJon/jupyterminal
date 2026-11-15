"""The view, driven by synthetic sessions (no shell involved)."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from conftest import (ALT, COLS, CTRL, LEFT, NONE, SHIFT, Harness, mark,
                      prompt, submit)
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec
from jupyterminal.ui import view as view_module
from jupyterminal.ui.view import PAD_LEFT


def three_blocks(h: Harness):
    return [h.run("echo one", "one\r\n"),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"),
            h.run("false", "", status=1)]


# -- selecting blocks --------------------------------------------------------

def test_a_click_selects_the_whole_block_and_copy_takes_its_output(ui):
    h = ui()
    one, ls, _ = three_blocks(h)
    QGuiApplication.clipboard().setText("stale")
    h.click(ls, row=0, col=3)
    assert h.view.selected == [ls.id]
    assert not h.view.has_text_selection()
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == "a.txt  b.txt\nc.txt"
    assert h.sent == []                      # nothing went to the shell


def test_ctrl_click_toggles_and_shift_click_takes_the_range(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.click(one)
    h.click(bad, CTRL)
    assert h.view.selected == [one.id, bad.id]
    h.click(bad, CTRL)
    assert h.view.selected == [one.id]
    h.click(bad, SHIFT)
    assert h.view.selected == [one.id, ls.id, bad.id]
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == "one\n\na.txt  b.txt\nc.txt\n\n"
    h.key(Qt.Key.Key_Escape)
    assert h.view.selected == []
    assert h.sent == []                      # Escape was not typed


def test_copy_command_and_output_shortcut(ui):
    h = ui()
    one, ls, _ = three_blocks(h)
    h.click(ls)
    h.key(Qt.Key.Key_C, CTRL | ALT | SHIFT)
    assert h.clipboard() == "$ ls\na.txt  b.txt\nc.txt"


# -- keyboard navigation -----------------------------------------------------

def test_keyboard_navigation_between_blocks(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.key(Qt.Key.Key_Up, CTRL)
    assert h.view.selected == [bad.id]       # the newest block
    h.key(Qt.Key.Key_Up)
    assert h.view.selected == [ls.id]
    h.key(Qt.Key.Key_Up)
    h.key(Qt.Key.Key_Up)                     # stops at the first
    assert h.view.selected == [one.id]
    h.key(Qt.Key.Key_Down)
    assert h.view.selected == [ls.id]
    h.key(Qt.Key.Key_Down, SHIFT)
    assert h.view.selected == [ls.id, bad.id]
    assert h.sent == []
    h.key(Qt.Key.Key_Escape)
    assert h.view.selected == []
    h.key(Qt.Key.Key_Up)                     # now it is the shell's key
    assert h.sent == [b"\x1b[A"]


def test_typing_returns_the_keyboard_to_the_shell(ui):
    h = ui()
    three_blocks(h)
    h.key(Qt.Key.Key_Up, CTRL)
    assert h.view.selected
    h.key(Qt.Key.Key_L)
    assert h.view.selected == []
    assert h.sent == [b"l"]


def test_moving_the_selection_scrolls_the_block_into_view(ui):
    h = ui(rows=8)
    first = h.run("seq 40", "".join(f"{i}\r\n" for i in range(40)))
    h.run("seq 30", "".join(f"{i}\r\n" for i in range(30)))
    view = h.view
    assert view.scroll == view.frame().max_scroll > 0
    h.key(Qt.Key.Key_Up, CTRL)
    h.key(Qt.Key.Key_Up)
    assert view.selected == [first.id]
    assert view.scroll == view.frame().tops[0] == 0
    h.key(Qt.Key.Key_Down, CTRL | SHIFT)     # bottom of the selected block
    frame = view.frame()
    assert view.scroll == frame.tops[0] + frame.heights[0] - frame.view_h
    h.key(Qt.Key.Key_Up, CTRL | SHIFT)
    assert view.scroll == 0


def test_shortcuts_can_be_remapped(ui):
    config = Config()
    config.keys["copy"] = ["Ctrl+Y"]
    config.keys["blocks.previous"] = ["K"]
    h = ui(config=config)
    one, ls, _ = three_blocks(h)
    h.click(ls)
    QGuiApplication.clipboard().setText("stale")
    h.key(Qt.Key.Key_Y, CTRL)
    assert h.clipboard() == "a.txt  b.txt\nc.txt"
    h.key(Qt.Key.Key_K)
    assert h.view.selected == [one.id]
    QGuiApplication.clipboard().setText("stale")
    h.key(Qt.Key.Key_C, CTRL | SHIFT)        # no longer bound: it is typed
    assert h.clipboard() == "stale"
    assert h.sent == [b"\x03"]


# -- selecting text ----------------------------------------------------------

def test_dragging_selects_text_and_never_a_block(ui):
    h = ui()
    one, ls, _ = three_blocks(h)
    h.drag(h.point(ls, 0, 2), h.point(ls, 1, 3))
    assert h.view.selected == []
    assert h.view.selection_text() == "txt  b.txt\nc.t"
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == "txt  b.txt\nc.t"


def test_double_click_selects_a_word_and_triple_click_a_line(ui):
    h = ui()
    block = h.run("cat f", "alpha beta-gamma delta\r\nsecond line\r\n")
    spot = h.point(block, 0, 8)
    QTest.mouseClick(h.view, LEFT, NONE, spot)
    QTest.mouseDClick(h.view, LEFT, NONE, spot)
    h.pump()
    assert h.view.selection_text() == "beta-gamma"
    assert h.view.selected == []
    QTest.mouseClick(h.view, LEFT, NONE, spot)
    h.pump()
    assert h.view.selection_text() == "alpha beta-gamma delta"


def test_a_drag_extends_across_blocks_but_not_into_the_input(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    below = QPoint(200, h.view.height() - 4)     # inside the input area
    h.drag(h.point(one, 0, 1), below)
    assert h.view.selection_text() == \
        "ne\nls\na.txt  b.txt\nc.txt\nfalse"


def test_selection_stays_put_while_output_arrives(ui):
    h = ui(rows=6)
    h.session.feed(submit("ping host"))
    for i in range(4):
        h.session.feed(f"reply {i}\r\n".encode())
    h.pump()
    block = h.session.blocks[-1]
    h.drag(h.point(block, 1, 0), h.point(block, 2, 7))
    chosen = h.view._sel
    assert h.view.selection_text() == "reply 1\nreply 2"
    for i in range(4, 60):                       # scrolls off the screen
        h.session.feed(f"reply {i}\r\n".encode())
    h.pump()
    assert h.view._sel == chosen
    assert h.view.selection_text() == "reply 1\nreply 2"
    assert block.running


def test_wrapped_line_is_selected_and_copied_as_one(ui):
    h = ui()
    text = "".join(chr(ord("a") + i % 26) for i in range(100))
    block = h.run("cat wide", text + "\r\n")
    assert block.view.out_rows == 3              # 100 characters, 40 columns
    h.drag(h.point(block, 0, 38), h.point(block, 1, 2))
    assert h.view.selection_text() == text[38:42]
    h.click(block)                               # lets go of the text
    assert not h.view.has_text_selection() and h.view.selected == []
    h.click(block, row=2)                        # selects the block
    assert h.view.selected == [block.id]
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == text


# -- block actions -----------------------------------------------------------

def test_filter_hides_lines_and_clearing_restores_them(ui):
    h = ui()
    block = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nError: b\r\n")
    assert block.view.out_rows == 4
    h.view.set_filter([block], FilterSpec("error"))
    h.pump()
    assert block.view.out_rows == 2
    assert h.view.copy_blocks([block], "output") == "error: a\nError: b"
    h.view.set_filter([block], None)
    h.pump()
    assert block.view.out_rows == 4
    assert h.view.copy_blocks([block], "output").count("\n") == 3


def test_filter_bar_reports_and_applies(ui):
    h = ui()
    block = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nError: b\r\n")
    h.view.open_filter([block])
    h.view.search.edit.setText("error")
    h.pump()
    assert h.view.search.status.text() == "2 of 4"
    h.view.search.case.setChecked(True)
    h.pump()
    assert block.visible == [1]
    h.view.search.invert.setChecked(True)
    h.pump()
    assert block.visible == [0, 2, 3]
    h.view.search.edit.setText("")
    h.pump()
    assert block.filter is None and block.view.out_rows == 4


def test_find_highlights_and_steps_through_matches(ui):
    h = ui()
    block = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nError: b error\r\n")
    h.view.open_find(block)
    h.view.search.edit.setText("error")
    h.pump()
    find = block.find
    assert find.matches == [(1, 0, 5), (3, 0, 5), (3, 9, 14)]
    assert h.view.search.status.text() == "1 / 3"
    QTest.keyClick(h.view.search.edit, Qt.Key.Key_Return)
    h.pump()
    assert find.current == 1 and h.view.search.status.text() == "2 / 3"
    QTest.keyClick(h.view.search.edit, Qt.Key.Key_Return, SHIFT)
    QTest.keyClick(h.view.search.edit, Qt.Key.Key_Return, SHIFT)
    h.pump()
    assert find.current == 2                     # wrapped around
    QTest.keyClick(h.view.search.edit, Qt.Key.Key_Escape)
    h.pump()
    assert block.find is None and not h.view.search.isVisible()
    assert h.view.hasFocus()


def test_find_scrolls_to_the_match(ui):
    h = ui(rows=8)
    lines = "".join(f"line {i}\r\n" for i in range(200))
    block = h.run("cat big", lines.replace("line 17\r\n", "needle 17\r\n"))
    view = h.view
    view.find(block, FilterSpec("needle"))
    h.pump()
    frame = view.frame()
    y = frame.tops[0] + block.view.header_h + 17 * view.r.ch - view.scroll
    assert 0 <= y < frame.view_h


def test_collapse_and_expand(ui):
    h = ui()
    block = h.run("seq 5", "".join(f"{i}\r\n" for i in range(5)))
    tall = block.view.height
    h.view.set_collapsed([block], True)
    h.pump()
    assert block.view.out_rows == 0 and block.view.height < tall
    assert h.view.copy_blocks([block], "output").count("\n") == 4
    h.view.set_collapsed([block], None)          # toggles
    h.pump()
    assert block.view.height == tall


def test_menu_offers_the_block_actions(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    labels = [e[0] for e in h.view.menu_actions(ls) if e]
    assert labels == ["Copy output", "Copy command",
                      "Copy command and output", "Find in block…",
                      "Filter output…", "Collapse", "Re-run command",
                      "Save output to file…"]
    h.click(one)
    h.click(ls, SHIFT)
    many = dict(e for e in h.view.menu_actions(ls) if e)
    assert "Find in block…" not in many and "Re-run command" not in many
    many["Copy command"]()
    assert h.clipboard() == "echo one\n\nls"
    many["Collapse"]()
    assert one.collapsed and ls.collapsed and not bad.collapsed


def test_rerun_hands_the_command_to_the_shell_unsubmitted(ui):
    h = ui()
    one, ls, _ = three_blocks(h)
    asked = []
    h.session.set_command_buffer = asked.append
    dict(e for e in h.view.menu_actions(ls) if e)["Re-run command"]()
    assert asked == ["ls"]
    assert h.sent == []                          # nothing was submitted


def test_save_output_writes_what_copy_would(ui, tmp_path):
    h = ui()
    block = h.run("ls", "a   \r\n\tb\r\n")
    path = tmp_path / "out.txt"
    h.view.write_output([block], str(path))
    assert path.read_text() == "a\n\tb\n"


def test_multi_line_paste_asks_first(ui, monkeypatch):
    h = ui()
    pasted = []
    h.session.paste = pasted.append
    answers = iter([False, True])
    asked = []

    def fake_confirm(parent, text, font):
        asked.append(text)
        return next(answers)

    monkeypatch.setattr(view_module, "confirm_paste", fake_confirm)
    h.view.paste_text("echo one")
    assert pasted == ["echo one"] and asked == []
    h.view.paste_text("echo one\necho two")
    assert pasted == ["echo one"] and len(asked) == 1     # declined
    h.view.paste_text("echo one\necho two")
    assert pasted[-1] == "echo one\necho two"
    h.view.paste_text("single line with newline\n")
    assert len(asked) == 2                       # one line: no question
    assert pasted[-1] == "single line with newline"    # and it will not run


# -- keys for the program ----------------------------------------------------

def test_keys_are_encoded_for_the_program(ui):
    h = ui()
    h.key(Qt.Key.Key_A)
    h.key(Qt.Key.Key_C, CTRL)
    h.key(Qt.Key.Key_Left)
    h.key(Qt.Key.Key_Right, CTRL)
    h.key(Qt.Key.Key_Tab)
    h.key(Qt.Key.Key_Backtab, SHIFT)
    h.key(Qt.Key.Key_Return)
    h.key(Qt.Key.Key_Backspace)
    h.key(Qt.Key.Key_F5)
    h.key(Qt.Key.Key_X, ALT)
    assert h.sent == [b"a", b"\x03", b"\x1b[D", b"\x1b[1;5C", b"\t",
                      b"\x1b[Z", b"\r", b"\x7f", b"\x1b[15~", b"\x1bx"]
    h.sent.clear()
    h.session.feed(b"\x1b[?1h")                  # application cursor keys
    h.key(Qt.Key.Key_Up)
    assert h.sent == [b"\x1bOA"]


def test_full_screen_program_takes_the_view_and_the_keys(ui):
    h = ui()
    three_blocks(h)
    h.session.feed(submit("vim") + b"\x1b[?1049h\x1b[2J\x1b[Hfile text")
    h.pump()
    assert h.session.fullscreen
    assert not h.view.bar.isVisible()
    h.key(Qt.Key.Key_Up, CTRL)                   # not block navigation now
    assert h.view.selected == []
    assert h.sent == [b"\x1b[1;5A"]
    h.sent.clear()
    h.session.feed(b"\x1b[?1000h\x1b[?1006h")    # mouse reporting, SGR
    QTest.mouseClick(h.view, LEFT, NONE,
                     QPoint(PAD_LEFT + 3 * h.view.r.cw + 2,
                            2 + 1 * h.view.r.ch + 4))
    assert h.sent == [b"\x1b[<0;4;2M", b"\x1b[<0;4;2m"]
    h.session.feed(b"\x1b[?1049l" + prompt())
    h.pump()
    assert not h.session.fullscreen
    assert len(h.view.frame().blocks) == 4       # the block list is back


# -- layout ------------------------------------------------------------------

def test_input_area_is_pinned_below_the_newest_block(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    frame = view.frame()
    assert frame.input_h > 0
    bottom_of_newest = frame.tops[-1] + frame.heights[-1] - view.scroll
    assert bottom_of_newest == frame.view_h      # right above the input
    assert frame.view_h + frame.input_h == view.height()


def test_running_command_hides_the_input_area(ui):
    h = ui()
    h.session.feed(submit("sleep 9"))
    h.pump()
    assert h.view.frame().input_h == 0
    h.session.feed(prompt())
    h.pump()
    assert h.view.frame().input_h > 0


def test_clear_scrolls_blocks_out_of_view_but_keeps_them(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    h.pump()
    view = h.view
    frame = view.frame()
    last_bottom = frame.tops[-1] + frame.heights[-1]
    assert last_bottom - view.scroll <= 0        # all above the view
    assert frame.max_scroll > 0
    view._set_scroll(0)                          # still reachable
    assert view.frame().tops[0] - view.scroll == 0
    assert len(h.session.blocks) == 3
    h.key(Qt.Key.Key_K, CTRL | SHIFT)            # remove all blocks
    assert h.session.blocks == []


def test_sticky_header_and_jump_to_end(ui):
    h = ui(rows=8)
    block = h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    view = h.view
    view._set_scroll(200)
    h.pump()
    names = [name for _, b, name in view._buttons if b is block]
    assert "sticky" in names and "jump" in names
    rect = [r for r, b, name in view._buttons if name == "jump"][-1]
    QTest.mouseClick(view, LEFT, NONE, rect.center().toPoint())
    h.pump()
    frame = view.frame()
    assert view.scroll == frame.tops[0] + frame.heights[0] - frame.view_h
    view._set_scroll(200)
    h.pump()
    QTest.mouseClick(view, LEFT, NONE, QPoint(150, 6))    # the pinned command
    h.pump()
    assert view.scroll == 0


def test_resizing_rewraps_and_resizes_the_terminal(ui):
    h = ui()
    text = "x" * 100
    block = h.run("cat wide", text + "\r\n")
    assert block.view.out_rows == 3
    view = h.view
    view.resize(*view.size_for(60, 12))
    h.pump(0.15)
    assert (h.session.cols, h.session.rows) == (60, 12)
    assert block.view.out_rows == 2
    assert view.copy_blocks([block], "output") == text


def test_wheel_scrolling_stops_following(ui):
    h = ui(rows=8)
    h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    view = h.view
    assert view.follow
    view._set_scroll(view.scroll - 90)
    assert not view.follow
    before = view.scroll
    h.session.feed(submit("echo more") + b"more\r\n")
    h.pump()
    assert view.follow                           # a new command follows again
    assert view.scroll != before


def test_a_million_lines_stay_responsive(ui):
    h = ui(rows=12)
    h.session.feed(submit("seq 1000000"))
    data = "".join(f"{i}\r\n" for i in range(1, 1_000_001)).encode()
    start = time.perf_counter()
    for pos in range(0, len(data), 65536):
        h.session.feed(data[pos:pos + 65536])
    fed = time.perf_counter() - start
    h.session.feed(prompt())
    block = h.session.blocks[-1]
    view = h.view
    start = time.perf_counter()
    for fraction in (0.0, 0.31, 0.77, 1.0):
        view._set_scroll(view.frame().max_scroll * fraction)
        view.grab()
    painted = (time.perf_counter() - start) / 4
    start = time.perf_counter()
    h.drag(h.point(block, 2, 0), h.point(block, 5, 3))
    text = view.copy_blocks([block], "output")
    copied = time.perf_counter() - start
    assert text.count("\n") == 999_999 and text.endswith("1000000")
    assert view.frame().content_h > 1_000_000 * view.r.ch
    assert fed < 5.0 and painted < 0.25 and copied < 2.0, \
        (fed, painted, copied)


# -- the input area and the full-screen terminal as plain screens -------------

def input_point(h: Harness, row: int, col: int) -> QPoint:
    view = h.view
    top = view.frame().view_h + view_module.IN_TOP
    return QPoint(PAD_LEFT + col * view.r.cw + 2,
                  int(top + row * view.r.ch + view.r.ch // 2))


def test_text_in_the_input_area_can_be_selected_and_copied(ui):
    h = ui()
    one, ls, _ = three_blocks(h)
    h.session.feed(b"echo typed text")
    h.pump()
    h.click(ls)
    assert h.view.selected == [ls.id]
    h.drag(input_point(h, 0, 2), input_point(h, 0, 5))
    assert h.view.selected == []          # the keyboard is with the shell
    assert h.view.selection_text() == "echo"
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == "echo"
    QTest.mouseClick(h.view, LEFT, NONE, input_point(h, 0, 20))
    h.pump()
    assert not h.view.has_text_selection()


def test_text_in_a_full_screen_program_can_be_selected(ui):
    h = ui()
    h.session.feed(submit("less f") + b"\x1b[?1049h\x1b[H\x1b[2J"
                   b"first row of text\r\nsecond row")
    h.pump()
    cw, ch = h.view.r.cw, h.view.r.ch
    start = QPoint(PAD_LEFT + 6 * cw + 2, 2 + ch // 2)
    end = QPoint(PAD_LEFT + 5 * cw + 2, 2 + ch + ch // 2)
    h.drag(start, end)
    assert h.view.selection_text() == "row of text\nsecond"
    h.key(Qt.Key.Key_C, CTRL | SHIFT)
    assert h.clipboard() == "row of text\nsecond"
    assert h.sent == []


def test_header_tooltip_tells_when_the_command_started(ui, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QHelpEvent
    h = ui()
    block = h.run("false", "", status=1)
    shown = []
    monkeypatch.setattr(view_module.QToolTip, "showText",
                        lambda pos, text, *rest: shown.append(text))
    point = h.point(block)
    h.view._tooltip(QHelpEvent(QEvent.Type.ToolTip, point,
                               h.view.mapToGlobal(point)))
    assert shown and shown[0].startswith("Started 20")
    assert "Exit code 1" in shown[0] and "/work" in shown[0]


def test_wheel_in_a_full_screen_pager_scrolls_the_pager(ui):
    from PySide6.QtCore import QPoint as P, QPointF
    from PySide6.QtGui import QWheelEvent
    h = ui()
    h.session.feed(submit("less f") + b"\x1b[?1049h\x1b[?1h")
    h.pump()

    def wheel(delta: int) -> None:
        position = QPointF(60, 40)
        event = QWheelEvent(position, h.view.mapToGlobal(position), P(0, 0),
                            P(0, delta), Qt.MouseButton.NoButton, NONE,
                            Qt.ScrollPhase.NoScrollPhase, False)
        h.view.wheelEvent(event)

    wheel(-120)
    assert h.sent == [b"\x1bOB" * 3]      # arrow keys, in application mode
    h.sent.clear()
    h.session.feed(b"\x1b[?1000h\x1b[?1006h")
    wheel(120)
    assert h.sent and h.sent[0].startswith(b"\x1b[<64;")


# -- found in review ----------------------------------------------------------

def test_copy_takes_the_selection_that_is_highlighted(ui):
    h = ui()
    block = h.run("cat f", "alpha beta gamma\r\n")
    h.session.feed(submit("less f") + b"\x1b[?1049h\x1b[H\x1b[2J"
                   b"SECRET PAGER TEXT")
    h.pump()
    cw, ch = h.view.r.cw, h.view.r.ch
    h.drag(QPoint(PAD_LEFT + 2, 2 + ch // 2),
           QPoint(PAD_LEFT + 5 * cw + 2, 2 + ch // 2))
    assert h.view.selection_text() == "SECRET"
    h.session.feed(b"\x1b[?1049l" + prompt())
    h.pump()
    assert not h.view.has_text_selection()       # its screen is gone
    h.drag(h.point(block, 0, 11), h.point(block, 0, 16))
    assert h.view.selection_text() == "gamma"
    # A selection in the input area gives way to one in a block as well.
    h.session.feed(b"typed-a")
    h.pump()
    h.drag(input_point(h, 0, 2), input_point(h, 0, 8))
    assert h.view.selection_text() == "typed-a"
    h.drag(h.point(block, 0, 0), h.point(block, 0, 5))
    assert h.view.selection_text() == "alpha"


def test_keys_in_the_search_field_never_reach_the_shell(ui):
    h = ui()
    block = h.run("make", "ok\r\nerror\r\n")
    h.view.open_find(block)
    edit = h.view.search.edit
    for key, mods in [(Qt.Key.Key_D, CTRL), (Qt.Key.Key_U, CTRL),
                      (Qt.Key.Key_L, CTRL), (Qt.Key.Key_J, CTRL),
                      (Qt.Key.Key_Tab, NONE), (Qt.Key.Key_PageUp, NONE),
                      (Qt.Key.Key_F5, NONE), (Qt.Key.Key_Space, CTRL)]:
        QTest.keyClick(edit, key, mods)
    QTest.keyClicks(edit, "err")
    h.pump()
    assert h.sent == []
    assert edit.text() == "err" and len(block.find.matches) == 1


def test_reopening_the_filter_bar_keeps_the_filter(ui):
    h = ui()
    block = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nerror: b\r\n"
                          "ok 3\r\n")
    h.view.open_filter([block])
    h.view.search.edit.setText("error")
    h.pump()
    assert h.view.search.status.text() == "2 of 5"
    h.view._bar_closed()
    h.view.open_filter([block])
    h.pump()
    assert h.view.search.edit.text() == "error"
    assert h.view.search.status.text() == "2 of 5"
    assert block.visible == [1, 3] and block.view.out_rows == 2
    h.view._bar_closed()
    h.drag(h.point(block, 0, 0), h.point(block, 1, 8))
    assert h.view.selection_text() == "error: a\nerror: b"
    h.view.find(block, FilterSpec("ok"))
    assert block.find.matches == []              # hidden lines are not found


def test_filter_and_find_follow_a_running_block(ui):
    h = ui(rows=6)
    h.session.feed(submit("build"))
    for i in range(12):
        h.session.feed(f"line {i}\r\n".encode())
    h.session.feed(b"error: late\r\nafter\r\n")
    h.pump()
    block = h.session.blocks[-1]
    view = h.view
    view.set_filter([block], FilterSpec("error", context=1))
    h.pump()
    shown = view._shown_set(block)
    lines = block.lines()
    assert [lines[i] for i in shown] == ["line 11", "error: late", "after"]
    assert block.view.out_rows == 3              # what is shown ...
    assert view.copy_blocks([block], "output") == \
        "line 11\nerror: late\nafter"            # ... is what is copied
    h.drag(h.point(block, 0, 0), h.point(block, 2, 5))
    assert view.selection_text() == "line 11\nerror: late\nafter"
    view.find(block, FilterSpec("late"))
    assert len(block.find.matches) == 1
    # More output: the search keeps up, and so does the filter at the end.
    h.session.feed(b"error: late again\r\n")
    h.pump()
    view._refresh_find()
    assert len(block.find.matches) == 2
    h.session.feed(prompt())
    h.pump()
    view.frame()
    lines = block.lines()
    assert [lines[i] for i in block.visible] == \
        ["line 11", "error: late", "after", "error: late again"]


def test_painting_survives_whatever_the_screen_holds(ui):
    h = ui(rows=6)
    h.session.feed(submit("odd"))
    wide = "\u65e5" * (COLS // 2)
    h.session.feed(("x" * COLS + "\x1b[2K" + wide + "\u672c"
                    + "\n" * 5).encode())
    h.pump()
    block = h.session.blocks[-1]
    image = h.view.grab()
    assert not image.isNull()
    assert block.view.out_rows >= 5
    assert isinstance(h.view.copy_blocks([block], "output"), str)


def test_a_clear_inside_a_long_lived_block_clears_the_view(ui):
    h = ui(rows=8)
    h.session.feed(submit("ssh host"))
    for i in range(30):
        h.session.feed(f"remote line {i}\r\n".encode())
    h.session.feed(b"\x1b[H\x1b[2Jremote$ ")
    h.pump()
    block = h.session.blocks[-1]
    view = h.view
    frame = view.frame()
    geo = block.view
    assert geo.live_rows == 8                    # it owns a whole screen
    top = frame.tops[-1] - view.scroll + geo.header_h
    last_old_line = top + (geo.frozen_rows - 1) * view.r.ch
    # Everything older ends under the pinned command (and its border).
    assert last_old_line + view.r.ch <= geo.header_h + 1
    assert block.lines()[-1] == "remote$"
    assert len(block.lines()) > 20               # ... and still there


def test_partial_selection_keeps_its_spaces(ui):
    h = ui()
    block = h.run("cat f", "key:   value   \r\n")
    h.drag(h.point(block, 0, 0), h.point(block, 0, 7))
    assert h.view.selection_text() == "key:   "
    h.drag(h.point(block, 0, 7), h.point(block, 0, 30))
    assert h.view.selection_text() == "value"     # the line's end is trimmed


def test_a_quiet_command_still_counts_its_seconds(ui, monkeypatch):
    from jupyterminal.session import block as block_module
    h = ui()
    h.session.feed(submit("sleep 100"))
    h.pump()
    block = h.session.blocks[-1]
    view = h.view

    class Clock:
        """The time of the blocks, standing still: a busy machine must not
        add its own delay to what the test counts."""

        now = block._t0 + 0.25

        def monotonic(self) -> float:
            return self.now

    clock = Clock()
    monkeypatch.setattr(block_module, "time", clock)
    view.frame()
    assert ("spinner", "", 3) in block.view.items
    clock.now += 2.38                    # ... 2.6 seconds after it began
    view.frame()
    assert ("spinner", "2.6s", 7) in block.view.items


def test_highlight_covers_whole_wide_characters_and_tabs(ui):
    from jupyterminal.ui.layout import wrap_complex
    h = ui()
    view = h.view
    text = "a\u65e5b\tc"                     # a, a wide character, b, tab, c
    layout = wrap_complex(text, COLS)
    assert view._columns(text, layout, 0, 5, 1, 2) == (1, 3)    # two cells
    assert view._columns(text, layout, 0, 5, 3, 4) == (4, 8)    # to the stop
    assert view._columns(text, layout, 0, 5, 0, 99) == (0, 10)  # + line end
    assert view._columns("plain", None, 0, 5, 1, 3) == (1, 3)
