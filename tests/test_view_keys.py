"""Keys, input-method text and focus in the view (no shell involved)."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import (QFocusEvent, QGuiApplication, QHelpEvent,
                           QInputMethodEvent, QKeyEvent)
from PySide6.QtWidgets import QApplication, QLineEdit

from conftest import ALT, CTRL, NONE, SHIFT, Harness, prompt, submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec
from jupyterminal.ui import view as view_module

Key = Qt.Key
META = Qt.KeyboardModifier.MetaModifier
KEYPAD = Qt.KeyboardModifier.KeypadModifier


def key_event(key, mods=NONE, text: str = "") -> QKeyEvent:
    return QKeyEvent(QEvent.Type.KeyPress, key, mods, text)


def press(h: Harness, key, mods=NONE, text: str = "") -> list[bytes]:
    """Deliver one key press the way Qt does and return what it sent to
    the shell.  (QTest would also press the modifier keys themselves.)"""
    h.sent.clear()
    assert QApplication.sendEvent(h.view, key_event(key, mods, text))
    return list(h.sent)


def record_actions(h: Harness) -> list[str]:
    """Note the actions the keyboard asks for instead of performing them."""
    actions: list[str] = []
    h.view._do = actions.append
    return actions


def count_updates(h: Harness) -> list[tuple]:
    """Note every repaint the view asks for."""
    calls: list[tuple] = []
    h.view.update = lambda *args: calls.append(args)
    return calls


def three_blocks(h: Harness):
    return [h.run("echo one", "one\r\n"),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"),
            h.run("false", "", status=1)]


def full_screen(h: Harness, text: bytes = b"") -> None:
    h.session.feed(submit("vim") + b"\x1b[?1049h\x1b[2J\x1b[H" + text)
    h.pump()
    assert h.session.fullscreen


# -- keys with a name --------------------------------------------------------

NAMED_KEYS = [
    (Key.Key_Up, b"\x1b[A"), (Key.Key_Down, b"\x1b[B"),
    (Key.Key_Right, b"\x1b[C"), (Key.Key_Left, b"\x1b[D"),
    (Key.Key_Home, b"\x1b[H"), (Key.Key_End, b"\x1b[F"),
    (Key.Key_Insert, b"\x1b[2~"), (Key.Key_Delete, b"\x1b[3~"),
    (Key.Key_PageUp, b"\x1b[5~"), (Key.Key_PageDown, b"\x1b[6~"),
    (Key.Key_Return, b"\r"), (Key.Key_Enter, b"\r"),
    (Key.Key_Tab, b"\t"), (Key.Key_Backtab, b"\x1b[Z"),
    (Key.Key_Backspace, b"\x7f"), (Key.Key_Escape, b"\x1b"),
    (Key.Key_F1, b"\x1bOP"), (Key.Key_F2, b"\x1bOQ"),
    (Key.Key_F3, b"\x1bOR"), (Key.Key_F4, b"\x1bOS"),
    (Key.Key_F5, b"\x1b[15~"), (Key.Key_F6, b"\x1b[17~"),
    (Key.Key_F7, b"\x1b[18~"), (Key.Key_F8, b"\x1b[19~"),
    (Key.Key_F9, b"\x1b[20~"), (Key.Key_F10, b"\x1b[21~"),
    (Key.Key_F11, b"\x1b[23~"), (Key.Key_F12, b"\x1b[24~"),
]


def test_every_named_key_sends_its_sequence(ui):
    h = ui()
    for key, expected in NAMED_KEYS:
        assert press(h, key) == [expected], key


def test_modifiers_are_encoded_into_named_keys(ui):
    h = ui()
    left = Key.Key_Left
    assert press(h, left, SHIFT) == [b"\x1b[1;2D"]
    assert press(h, left, ALT) == [b"\x1b[1;3D"]
    assert press(h, left, ALT | SHIFT) == [b"\x1b[1;4D"]
    assert press(h, left, CTRL) == [b"\x1b[1;5D"]
    assert press(h, left, CTRL | SHIFT) == [b"\x1b[1;6D"]
    assert press(h, left, CTRL | ALT) == [b"\x1b[1;7D"]
    assert press(h, left, CTRL | ALT | SHIFT) == [b"\x1b[1;8D"]
    assert press(h, Key.Key_Delete, SHIFT) == [b"\x1b[3;2~"]
    assert press(h, Key.Key_F5, SHIFT) == [b"\x1b[15;2~"]
    assert press(h, Key.Key_F1, CTRL) == [b"\x1b[1;5P"]
    assert press(h, Key.Key_Return, ALT) == [b"\x1b\r"]
    assert press(h, Key.Key_Tab, ALT) == [b"\x1b\t"]
    assert press(h, Key.Key_Tab, SHIFT) == [b"\x1b[Z"]
    assert press(h, Key.Key_Backspace, CTRL) == [b"\x08"]
    assert press(h, Key.Key_Backspace, ALT) == [b"\x1b\x7f"]
    assert press(h, Key.Key_Escape, ALT) == [b"\x1b\x1b"]


def test_modifiers_the_terminal_has_no_code_for_are_left_out(ui):
    h = ui()
    assert press(h, Key.Key_Left, META) == [b"\x1b[D"]
    assert press(h, Key.Key_Enter, KEYPAD) == [b"\r"]
    assert press(h, Key.Key_A, META, "a") == [b"a"]


def test_backtab_is_shift_tab_whatever_the_modifiers_say(ui):
    h = ui()
    assert press(h, Key.Key_Backtab, NONE) == [b"\x1b[Z"]
    assert press(h, Key.Key_Backtab, SHIFT) == [b"\x1b[Z"]
    assert press(h, Key.Key_Tab, NONE, "\t") == [b"\t"]


def test_application_cursor_mode_changes_the_plain_cursor_keys(ui):
    h = ui()
    h.session.feed(b"\x1b[?1h")
    assert press(h, Key.Key_Up) == [b"\x1bOA"]
    assert press(h, Key.Key_Home) == [b"\x1bOH"]
    assert press(h, Key.Key_Up, SHIFT) == [b"\x1b[1;2A"]
    assert press(h, Key.Key_Delete) == [b"\x1b[3~"]
    h.session.feed(b"\x1b[?1l")
    assert press(h, Key.Key_Up) == [b"\x1b[A"]


# -- control characters ------------------------------------------------------

def test_control_with_a_letter_sends_its_control_character(ui):
    h = ui()
    for code in range(1, 27):
        key = Key(0x40 + code)                   # Key_A .. Key_Z
        assert press(h, key, CTRL) == [bytes((code,))], key
    # The text Qt adds is not what is sent: the key decides.
    assert press(h, Key.Key_C, CTRL, "c") == [b"\x03"]
    assert press(h, Key.Key_A, CTRL | SHIFT, "A") == [b"\x01"]
    assert press(h, Key.Key_C, CTRL | ALT) == [b"\x1b\x03"]
    assert press(h, Key.Key_A, CTRL | ALT | SHIFT) == [b"\x1b\x01"]


def test_control_with_punctuation_follows_the_terminal_conventions(ui):
    h = ui()
    expected = {
        Key.Key_Space: b"\x00", Key.Key_At: b"\x00", Key.Key_2: b"\x00",
        Key.Key_BracketLeft: b"\x1b", Key.Key_3: b"\x1b",
        Key.Key_Backslash: b"\x1c", Key.Key_4: b"\x1c",
        Key.Key_BracketRight: b"\x1d", Key.Key_5: b"\x1d",
        Key.Key_AsciiCircum: b"\x1e", Key.Key_6: b"\x1e",
        Key.Key_AsciiTilde: b"\x1e", Key.Key_Underscore: b"\x1f",
        Key.Key_7: b"\x1f", Key.Key_Slash: b"\x1f",
        Key.Key_Question: b"\x7f", Key.Key_8: b"\x7f",
        # No control character of its own: the character itself.
        Key.Key_1: b"1", Key.Key_9: b"9", Key.Key_Comma: b",",
    }
    for key, data in expected.items():
        assert press(h, key, CTRL) == [data], key
    assert press(h, Key.Key_Question, CTRL | SHIFT, "?") == [b"\x7f"]
    assert press(h, Key.Key_1, CTRL | ALT) == [b"\x1b1"]


def test_control_only_rewrites_printable_ascii(ui):
    h = ui()
    # Just outside the printable range on both sides, and far outside.
    assert press(h, 0x1F, CTRL) == []
    assert press(h, 0x7F, CTRL) == []
    assert press(h, Key.Key_Eacute, CTRL, "\xe9") == ["\xe9".encode()]
    assert press(h, Key.Key_Eacute, CTRL | ALT, "\xe9") == \
        [b"\x1b" + "\xe9".encode()]


# -- text --------------------------------------------------------------------

def test_a_text_key_sends_its_text(ui):
    h = ui()
    assert press(h, Key.Key_A, NONE, "a") == [b"a"]
    assert press(h, Key.Key_A, SHIFT, "A") == [b"A"]
    assert press(h, Key.Key_Exclam, SHIFT, "!") == [b"!"]
    assert press(h, Key.Key_Eacute, NONE, "\xe9") == [b"\xc3\xa9"]
    assert press(h, 0x416, NONE, "ж") == [b"\xd0\xb6"]
    assert press(h, 0x1F600, NONE, "\U0001f600") == [b"\xf0\x9f\x98\x80"]
    assert press(h, Key.Key_X, ALT, "x") == [b"\x1bx"]
    assert press(h, Key.Key_X, ALT | SHIFT, "X") == [b"\x1bX"]


def test_a_key_with_neither_name_nor_text_sends_nothing(ui):
    h = ui(rows=8)
    block = h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    view = h.view
    view.select_block(block)
    view._set_scroll(44)
    updates = count_updates(h)
    assert press(h, Key.Key_F13) == []
    # Nothing was typed, so nothing changes hands either.
    assert view.selected == [block.id]
    assert view.scroll == 44 and not view.follow
    assert updates == []


def test_a_modifier_key_alone_does_nothing_at_all(ui):
    config = Config()
    config.keys["zoom_in"] = ["Shift"]           # a shortcut cannot be this
    config.keys["zoom_out"] = ["Ctrl+Shift"]
    h = ui(config=config)
    size = h.view.r.size
    actions = record_actions(h)
    assert press(h, Key.Key_Shift, SHIFT) == []
    assert press(h, Key.Key_Shift, CTRL | SHIFT) == []
    for key in (Key.Key_Control, Key.Key_Alt, Key.Key_Meta, Key.Key_AltGr,
                Key.Key_CapsLock, Key.Key_NumLock, Key.Key_ScrollLock,
                Key.Key_Super_L, Key.Key_Super_R, Key.Key_Hyper_L,
                Key.Key_Hyper_R, Key.Key_unknown):
        assert press(h, key) == [], key
    assert actions == [] and h.view.r.size == size


# -- what typing does to the view --------------------------------------------

def test_typing_takes_the_keyboard_back_from_the_block_list(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.view.select_block(ls)
    assert press(h, Key.Key_L, NONE, "l") == [b"l"]
    assert h.view.selected == []
    assert h.view._cursor_block is None


def test_typing_scrolls_back_to_the_newest_output(ui):
    h = ui(rows=8)
    h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    view = h.view
    view._set_scroll(0)
    assert not view.follow
    assert press(h, Key.Key_A, NONE, "a") == [b"a"]
    assert view.follow
    frame = view.frame()
    assert view.scroll == frame.max_scroll > 0


def test_typing_while_the_view_follows_lays_nothing_out_again(ui):
    h = ui()
    three_blocks(h)
    frame = h.view.frame()
    assert h.view.follow
    assert press(h, Key.Key_A, NONE, "a") == [b"a"]
    assert h.view.frame() is frame


def test_typing_drops_a_selection_in_the_line_being_edited(ui):
    h = ui()
    h.session.feed(b"echo typed")
    h.pump()
    view = h.view
    view._gsel = (h.session.input, (0, 2), (0, 6))
    assert view.selection_text() == "echo"
    assert press(h, Key.Key_X, NONE, "x") == [b"x"]
    assert view._gsel is None
    assert not view.has_text_selection()


def test_typing_lets_go_of_text_selected_in_the_output(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    view._sel = ((ls.id, 1, 0, 0), (ls.id, 1, 0, 5))
    assert view.selection_text() == "a.txt"
    updates = count_updates(h)
    assert press(h, Key.Key_X, NONE, "x") == [b"x"]
    assert view._sel is None and len(updates) == 1


def test_typing_keeps_a_selection_in_a_full_screen_program(ui):
    h = ui()
    full_screen(h, b"page text")
    view = h.view
    view._gsel = (h.session.term.screen, (0, 0), (0, 4))
    assert press(h, Key.Key_J, NONE, "j") == [b"j"]
    assert view._gsel == (h.session.term.screen, (0, 0), (0, 4))
    assert view.selection_text() == "page"


def test_keys_go_to_a_running_program(ui):
    h = ui()
    h.session.feed(submit("cat"))
    h.pump()
    assert h.session.running is not None
    assert press(h, Key.Key_A, NONE, "a") == [b"a"]
    assert press(h, Key.Key_D, CTRL) == [b"\x04"]


def test_keys_after_the_shell_has_gone_are_harmless(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.close()
    del h.session.write                  # the real thing: there is no shell
    h.view.keyPressEvent(key_event(Key.Key_A, NONE, "a"))
    h.view.keyPressEvent(key_event(Key.Key_Return))
    assert [b.command for b in h.session.blocks] == ["echo one", "ls",
                                                    "false"]
    h.view.keyPressEvent(key_event(Key.Key_Up, CTRL))
    assert h.view.selected == [bad.id]   # the blocks can still be used


# -- shortcuts ---------------------------------------------------------------

GLOBAL_SHORTCUTS = [
    (Key.Key_C, CTRL | SHIFT, "copy"),
    (Key.Key_C, CTRL | ALT | SHIFT, "copy_command_and_output"),
    (Key.Key_V, CTRL | SHIFT, "paste"),
    (Key.Key_Insert, SHIFT, "paste_selection"),
    (Key.Key_Up, CTRL, "select_newest_block"),
    (Key.Key_K, CTRL | SHIFT, "clear_blocks"),
    (Key.Key_F, CTRL | SHIFT, "find_in_block"),
    (Key.Key_L, CTRL | SHIFT, "filter_output"),
    (Key.Key_PageUp, SHIFT, "scroll_page_up"),
    (Key.Key_PageDown, SHIFT, "scroll_page_down"),
    (Key.Key_Home, SHIFT, "scroll_to_top"),
    (Key.Key_End, SHIFT, "scroll_to_bottom"),
    (Key.Key_Equal, CTRL, "zoom_in"),
    (Key.Key_Plus, CTRL, "zoom_in"),
    (Key.Key_Minus, CTRL, "zoom_out"),
    (Key.Key_0, CTRL, "zoom_reset"),
]

BLOCK_SHORTCUTS = [
    (Key.Key_Up, NONE, "blocks.previous"),
    (Key.Key_Down, NONE, "blocks.next"),
    (Key.Key_Up, SHIFT, "blocks.extend_previous"),
    (Key.Key_Down, SHIFT, "blocks.extend_next"),
    (Key.Key_Up, CTRL | SHIFT, "blocks.scroll_to_top"),
    (Key.Key_Down, CTRL | SHIFT, "blocks.scroll_to_bottom"),
    (Key.Key_Home, NONE, "blocks.first"),
    (Key.Key_End, NONE, "blocks.last"),
    (Key.Key_PageUp, NONE, "blocks.page_up"),
    (Key.Key_PageDown, NONE, "blocks.page_down"),
    (Key.Key_Escape, NONE, "blocks.focus_input"),
    (Key.Key_Menu, NONE, "blocks.menu"),
]


def test_every_default_shortcut_asks_for_its_action_and_types_nothing(ui):
    h = ui()
    actions = record_actions(h)
    for key, mods, action in GLOBAL_SHORTCUTS:
        assert press(h, key, mods) == [], action
        assert actions == [action]
        actions.clear()


def test_block_shortcuts_work_only_while_a_block_is_selected(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    actions = record_actions(h)
    typed = [press(h, key, mods) for key, mods, _ in BLOCK_SHORTCUTS]
    # Without a selection each of them is a key for the shell.
    assert typed == [[b"\x1b[A"], [b"\x1b[B"], [b"\x1b[1;2A"],
                     [b"\x1b[1;2B"], [b"\x1b[1;6A"], [b"\x1b[1;6B"],
                     [b"\x1b[H"], [b"\x1b[F"], [b"\x1b[5~"], [b"\x1b[6~"],
                     [b"\x1b"], []]
    assert actions == []
    h.view.select_block(ls)
    for key, mods, action in BLOCK_SHORTCUTS:
        assert press(h, key, mods) == [], action
        assert actions == [action]
        actions.clear()
    # The shortcuts that work everywhere still do.
    assert press(h, Key.Key_C, CTRL | SHIFT) == []
    assert actions == ["copy"]
    assert h.view.selected == [ls.id]


def test_escape_lets_go_of_selected_text_and_is_no_key_for_the_shell(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    view = h.view
    view._sel = ((ls.id, 1, 0, 0), (ls.id, 1, 0, 5))
    assert press(h, Key.Key_Escape) == []
    assert not view.has_text_selection() and view.hasFocus()
    assert press(h, Key.Key_Escape) == [b"\x1b"]    # nothing is selected now
    # In the line that is being edited as well.
    h.session.feed(b"echo typed")
    h.pump()
    view._gsel = (h.session.input, (0, 2), (0, 6))
    assert view.selection_text() == "echo"
    assert press(h, Key.Key_Escape) == []
    assert view._gsel is None
    # The other keys of the blocks need a block: with text selected they
    # are keys for the shell.
    view._sel = ((ls.id, 1, 0, 0), (ls.id, 1, 0, 5))
    assert press(h, Key.Key_Up) == [b"\x1b[A"]
    assert view.selected == []


def test_a_full_screen_program_gets_escape_whatever_is_selected(ui):
    h = ui()
    full_screen(h, b"page text")
    view = h.view
    view._gsel = (h.session.term.screen, (0, 0), (0, 4))
    assert press(h, Key.Key_Escape) == [b"\x1b"]
    assert view.selection_text() == "page"


def test_a_shifted_symbol_matches_a_shortcut_that_does_not_name_shift(ui):
    h = ui()
    size = h.view.r.size
    # "+" needs Shift on most keyboards; the shortcut is just "Ctrl++".
    assert press(h, Key.Key_Plus, CTRL | SHIFT, "+") == []
    assert h.view.r.size == size + 1


def test_the_first_matching_spelling_of_a_block_shortcut_wins(ui):
    config = Config()
    config.keys["blocks.previous"] = ["Shift+!"]
    h = ui(config=config)
    one, ls, bad = three_blocks(h)
    h.view.select_block(ls)
    assert press(h, Key.Key_Exclam, SHIFT, "!") == []
    assert h.view.selected == [one.id]


def test_in_a_full_screen_program_no_key_is_a_key_of_the_blocks(ui):
    config = Config()
    config.keys["blocks.previous"] = ["Ctrl+Shift+C"]    # the key of "copy"
    h = ui(config=config)
    one, ls, bad = three_blocks(h)
    h.view.select_block(ls)
    actions = record_actions(h)
    assert press(h, Key.Key_C, CTRL | SHIFT) == []
    assert actions == ["blocks.previous"]     # with a block selected
    actions.clear()
    h.session.feed(submit("sleep 1; vim") + b"\x1b[?1049h\x1b[2J\x1b[H")
    h.view.select_block(ls)
    h.pump()
    assert h.session.fullscreen and h.view.selected == [ls.id]
    assert press(h, Key.Key_C, CTRL | SHIFT) == []
    assert actions == ["copy"]                # the program's screen is copied


def test_a_full_screen_program_gets_every_key_but_copy_paste_and_zoom(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.session.feed(submit("sleep 1; vim"))
    h.view.select_block(ls)              # ... while the command starts up
    h.session.feed(b"\x1b[?1049h\x1b[2J\x1b[H")
    h.pump()
    assert h.session.fullscreen and h.view.selected == [ls.id]
    # A selected block does not make Up a block key now.
    assert press(h, Key.Key_Up) == [b"\x1b[A"]
    assert h.view.selected == []
    actions = record_actions(h)
    allowed = {"copy", "paste", "paste_selection", "zoom_in", "zoom_out",
               "zoom_reset"}
    typed = []
    for key, mods, action in GLOBAL_SHORTCUTS:
        data = press(h, key, mods)
        if action in allowed:
            assert data == [] and actions == [action], action
            actions.clear()
        else:
            typed.append(data)
    assert actions == []
    assert typed == [[b"\x1b\x03"], [b"\x1b[1;5A"], [b"\x0b"], [b"\x06"],
                     [b"\x0c"], [b"\x1b[5;2~"], [b"\x1b[6;2~"],
                     [b"\x1b[1;2H"], [b"\x1b[1;2F"]]


# -- the keyboard elsewhere --------------------------------------------------

def focus(h: Harness, widget) -> None:
    widget.setFocus()
    h.pump()
    assert QApplication.focusWidget() is widget


def test_keys_the_filter_bar_has_no_use_for_stop_at_the_view(ui):
    h = ui()
    block = h.run("make", "ok\r\nerror\r\n")
    h.view.open_filter([block])
    spin = h.view.search.context
    focus(h, spin)
    h.sent.clear()
    for key in (Key.Key_F5, Key.Key_Tab):
        assert QApplication.sendEvent(spin, key_event(key))
    assert h.sent == []
    assert QApplication.focusWidget() is spin
    assert h.view.search.isVisible()
    # The view takes such a key off Qt's hands.
    event = key_event(Key.Key_A, NONE, "a")
    event.ignore()
    h.view.keyPressEvent(event)
    assert event.isAccepted() and h.sent == []
    focus(h, h.view.search.edit)
    h.view.keyPressEvent(key_event(Key.Key_B, NONE, "b"))
    assert h.sent == []


def test_escape_closes_the_filter_bar_from_any_of_its_fields(ui):
    h = ui()
    block = h.run("make", "ok\r\nerror\r\n")
    h.view.open_filter([block])
    h.view.search.edit.setText("error")
    spin = h.view.search.context
    focus(h, spin)
    h.sent.clear()
    assert QApplication.sendEvent(spin, key_event(Key.Key_Escape))
    assert not h.view.search.isVisible()
    assert QApplication.focusWidget() is h.view      # back to typing
    assert h.sent == []                              # Escape was not typed
    assert block.filter == FilterSpec("error")       # the filter stays


def test_keys_reach_the_shell_unless_the_search_bar_has_the_keyboard(ui):
    h = ui()
    view = h.view
    focus(h, view)
    assert press(h, Key.Key_A, NONE, "a") == [b"a"]
    other = QLineEdit(view)              # not part of the search bar
    other.show()
    focus(h, other)
    h.sent.clear()
    view.keyPressEvent(key_event(Key.Key_B, NONE, "b"))
    assert h.sent == [b"b"]
    other.clearFocus()
    h.pump()
    assert QApplication.focusWidget() is None
    view.keyPressEvent(key_event(Key.Key_C, NONE, "c"))
    assert h.sent == [b"b", b"c"]


def test_tab_is_typed_and_never_moves_the_keyboard_focus(ui):
    h = ui()
    block = h.run("make", "ok\r\nerror\r\n")
    view = h.view
    view.open_find(block)                # something else could take focus
    focus(h, view)
    assert press(h, Key.Key_Tab, NONE, "\t") == [b"\t"]
    assert press(h, Key.Key_Backtab, SHIFT) == [b"\x1b[Z"]
    assert QApplication.focusWidget() is view
    assert view.focusNextPrevChild(True) is False
    assert view.focusNextPrevChild(False) is False


def test_a_tooltip_request_is_answered_by_the_view(ui, monkeypatch):
    h = ui()
    block = h.run("true", "")
    shown = []
    monkeypatch.setattr(view_module.QToolTip, "showText",
                        lambda pos, text, widget: shown.append(widget))
    point = h.point(block)
    event = QHelpEvent(QEvent.Type.ToolTip, point, h.view.mapToGlobal(point))
    assert QApplication.sendEvent(h.view, event) is True
    assert shown == [h.view]


# -- input methods -----------------------------------------------------------

def test_committed_input_method_text_is_typed(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.view.select_block(ls)
    event = QInputMethodEvent()
    event.setCommitString("日本")
    event.ignore()
    h.view.inputMethodEvent(event)
    assert h.sent == ["日本".encode()]
    assert h.view.selected == []         # typing: the shell has the keyboard
    assert event.isAccepted()


def test_input_method_text_does_to_the_view_what_a_key_does(ui):
    h = ui(rows=8)
    h.run("seq 100", "".join(f"{i}\r\n" for i in range(100)))
    h.session.feed(b"echo typed")
    h.pump()
    view = h.view
    view._set_scroll(0)
    view._gsel = (h.session.input, (0, 2), (0, 6))
    assert view.selection_text() == "echo" and not view.follow
    event = QInputMethodEvent()
    event.setCommitString("\u65e5")
    view.inputMethodEvent(event)
    assert h.sent == ["\u65e5".encode()]
    # Back at the newest output, and the edited line's selection is gone.
    assert view.follow and view._gsel is None
    frame = view.frame()
    assert view.scroll == frame.max_scroll > 0


def test_text_still_being_composed_is_not_typed(ui):
    h = ui()
    one, ls, bad = three_blocks(h)
    h.view.select_block(ls)
    event = QInputMethodEvent("に", [])      # pre-edit text only
    event.ignore()
    h.view.inputMethodEvent(event)
    assert h.sent == []
    assert h.view.selected == [ls.id]
    assert event.isAccepted()


class _Commit:
    """Just enough of an input-method event to carry any string."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.accepted = False

    def commitString(self) -> str:
        return self.text

    def accept(self) -> None:
        self.accepted = True


def test_input_method_text_that_cannot_be_encoded_is_replaced(ui):
    h = ui()
    event = _Commit("a\udc80b")          # half a surrogate pair
    h.view.inputMethodEvent(event)
    assert h.sent == [b"a?b"]
    assert event.accepted


# -- focus -------------------------------------------------------------------

def test_focus_changes_are_reported_to_a_program_that_asked(ui):
    h = ui()
    view = h.view
    updates = count_updates(h)
    view.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    view.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert h.sent == []                  # nobody asked
    assert updates == [(), ()]           # the cursor is drawn differently
    h.session.feed(b"\x1b[?1004h")
    updates.clear()
    view.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert h.sent == [b"\x1b[I"] and updates == [()]
    view.focusOutEvent(QFocusEvent(QEvent.Type.FocusOut))
    assert h.sent == [b"\x1b[I", b"\x1b[O"] and updates == [(), ()]
    h.session.feed(b"\x1b[?1004l")
    view.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
    assert h.sent == [b"\x1b[I", b"\x1b[O"]


def test_losing_and_gaining_the_real_focus_is_reported(ui):
    h = ui()
    view = h.view
    focus(h, view)
    h.session.feed(b"\x1b[?1004h")
    view.clearFocus()
    h.pump()
    assert h.sent == [b"\x1b[O"]
    focus(h, view)
    assert h.sent == [b"\x1b[O", b"\x1b[I"]


def test_copy_still_works_in_a_full_screen_program(ui):
    h = ui()
    full_screen(h, b"page text")
    view = h.view
    QGuiApplication.clipboard().setText("stale")
    view._gsel = (h.session.term.screen, (0, 5), (0, 9))
    assert press(h, Key.Key_C, CTRL | SHIFT) == []
    assert QGuiApplication.clipboard().text() == "text"
    # ... and the block list's own keys are the program's.
    assert press(h, Key.Key_Escape) == [b"\x1b"]
    h.session.feed(b"\x1b[?1049l" + prompt())
    h.pump()
    assert not h.session.fullscreen
    assert press(h, Key.Key_Escape) == [b"\x1b"]
