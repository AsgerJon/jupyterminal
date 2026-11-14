"""Keys and mouse actions as the bytes a program reads (xterm's forms)."""

from __future__ import annotations

import pytest

from jupyterminal.term.keys import (ALT, CTRL, SHIFT, encode_char,
                                    encode_key, encode_mouse, encode_paste)


def test_modifier_bits():
    assert (SHIFT, ALT, CTRL) == (1, 2, 4)


# -- named keys ---------------------------------------------------------------

@pytest.mark.parametrize("name,final", [
    ("Up", "A"), ("Down", "B"), ("Right", "C"), ("Left", "D"),
    ("Home", "H"), ("End", "F"),
])
def test_cursor_keys(name, final):
    assert encode_key(name) == f"\x1b[{final}".encode()
    assert encode_key(name, app_cursor=True) == f"\x1bO{final}".encode()
    assert encode_key(name, SHIFT) == f"\x1b[1;2{final}".encode()
    assert encode_key(name, ALT) == f"\x1b[1;3{final}".encode()
    assert encode_key(name, CTRL) == f"\x1b[1;5{final}".encode()
    assert encode_key(name, CTRL | SHIFT) == f"\x1b[1;6{final}".encode()
    assert encode_key(name, CTRL | ALT | SHIFT) == \
        f"\x1b[1;8{final}".encode()
    # A modified key has one form, whatever the cursor key mode.
    assert encode_key(name, CTRL, app_cursor=True) == \
        f"\x1b[1;5{final}".encode()


@pytest.mark.parametrize("name,number", [
    ("Insert", 2), ("Delete", 3), ("PageUp", 5), ("PageDown", 6),
    ("F5", 15), ("F6", 17), ("F7", 18), ("F8", 19), ("F9", 20),
    ("F10", 21), ("F11", 23), ("F12", 24),
])
def test_keys_with_a_number(name, number):
    assert encode_key(name) == f"\x1b[{number}~".encode()
    assert encode_key(name, app_cursor=True) == f"\x1b[{number}~".encode()
    assert encode_key(name, SHIFT) == f"\x1b[{number};2~".encode()
    assert encode_key(name, CTRL | ALT) == f"\x1b[{number};7~".encode()


@pytest.mark.parametrize("name,final", [
    ("F1", "P"), ("F2", "Q"), ("F3", "R"), ("F4", "S"),
])
def test_the_first_four_function_keys(name, final):
    assert encode_key(name) == f"\x1bO{final}".encode()
    assert encode_key(name, SHIFT) == f"\x1b[1;2{final}".encode()
    assert encode_key(name, ALT | CTRL) == f"\x1b[1;7{final}".encode()


@pytest.mark.parametrize("name,mods,data", [
    ("Enter", 0, b"\r"),
    ("Enter", SHIFT, b"\r"),
    ("Enter", CTRL, b"\r"),
    ("Enter", ALT, b"\x1b\r"),
    ("Enter", ALT | CTRL, b"\x1b\r"),
    ("Tab", 0, b"\t"),
    ("Tab", CTRL, b"\t"),
    ("Tab", SHIFT, b"\x1b[Z"),
    ("Tab", SHIFT | ALT, b"\x1b[Z"),
    ("Tab", ALT, b"\x1b\t"),
    ("Backspace", 0, b"\x7f"),
    ("Backspace", SHIFT, b"\x7f"),
    ("Backspace", CTRL, b"\x08"),
    ("Backspace", ALT, b"\x1b\x7f"),
    ("Backspace", CTRL | ALT, b"\x1b\x08"),
    ("Escape", 0, b"\x1b"),
    ("Escape", SHIFT | CTRL, b"\x1b"),
    ("Escape", ALT, b"\x1b\x1b"),
])
def test_editing_keys(name, mods, data):
    assert encode_key(name, mods) == data


@pytest.mark.parametrize("name", ["", "F13", "up", "CapsLock", "a"])
def test_a_key_without_an_encoding_gives_none(name):
    assert encode_key(name) is None
    assert encode_key(name, CTRL | ALT | SHIFT, app_cursor=True) is None


# -- typed characters ---------------------------------------------------------

def test_plain_characters_are_their_utf8():
    assert encode_char("a") == b"a"
    assert encode_char("A", SHIFT) == b"A"
    assert encode_char("é") == "é".encode()
    assert encode_char("日") == "日".encode()
    assert encode_char("\ud800") == b"?"           # not encodable


def test_alt_prefixes_escape():
    assert encode_char("a", ALT) == b"\x1ba"
    assert encode_char("é", ALT) == b"\x1b" + "é".encode()
    assert encode_char("ab", ALT) == b"\x1bab"


def test_ctrl_with_a_letter_is_its_control_code():
    assert [encode_char(c, CTRL) for c in "azAZ"] == \
        [b"\x01", b"\x1a", b"\x01", b"\x1a"]
    assert encode_char("c", CTRL) == b"\x03"
    assert encode_char("m", CTRL | SHIFT) == b"\r"
    assert encode_char("c", CTRL | ALT) == b"\x1b\x03"


@pytest.mark.parametrize("char,code", [
    ("@", 0), (" ", 0), ("2", 0),
    ("[", 27), ("3", 27),
    ("\\", 28), ("4", 28),
    ("]", 29), ("5", 29),
    ("^", 30), ("6", 30), ("~", 30),
    ("_", 31), ("7", 31), ("/", 31), ("-", 31),
    ("?", 127), ("8", 127),
])
def test_ctrl_with_punctuation(char, code):
    assert encode_char(char, CTRL) == bytes((code,))
    assert encode_char(char, CTRL | ALT) == bytes((27, code))


@pytest.mark.parametrize("char", ["`", "{", "1", "9", "0", "!", "é", "日"])
def test_ctrl_with_a_character_that_has_no_control_code(char):
    assert encode_char(char, CTRL) == char.encode()
    assert encode_char(char, CTRL | ALT) == b"\x1b" + char.encode()


def test_ctrl_applies_to_single_characters_only():
    assert encode_char("ab", CTRL) == b"ab"
    assert encode_char("", CTRL) == b""


# -- pasting ------------------------------------------------------------------

def test_a_paste_arrives_as_typed_lines():
    assert encode_paste("one\r\ntwo\nthree\rfour", False) == \
        b"one\rtwo\rthree\rfour"
    assert encode_paste("", False) == b""
    assert encode_paste("é→\ud800", False) == "é→".encode() + b"?"


def test_a_bracketed_paste_is_wrapped_and_cannot_end_itself():
    assert encode_paste("a\nb", True) == b"\x1b[200~a\rb\x1b[201~"
    assert encode_paste("", True) == b"\x1b[200~\x1b[201~"
    assert encode_paste("x\x1b[201~rm -rf\n", True) == \
        b"\x1b[200~xrm -rf\r\x1b[201~"
    # Without the bracket there is nothing to close.
    assert encode_paste("x\x1b[201~y", False) == b"x\x1b[201~y"
    assert encode_paste("é\ud800", True) == \
        b"\x1b[200~" + "é".encode() + b"?\x1b[201~"


# -- the mouse ----------------------------------------------------------------

def test_mouse_press_in_the_original_form():
    assert encode_mouse(0, 0, 0, 0) == b"\x1b[M" + bytes((32, 33, 33))
    assert encode_mouse(1, 9, 4, 0) == b"\x1b[M" + bytes((33, 42, 37))
    assert encode_mouse(2, 0, 0, 0) == b"\x1b[M" + bytes((34, 33, 33))
    assert encode_mouse(64, 2, 3, 0) == b"\x1b[M" + bytes((96, 35, 36))
    assert encode_mouse(65, 2, 3, 0) == b"\x1b[M" + bytes((97, 35, 36))


def test_mouse_modifiers_and_motion_add_to_the_button():
    assert encode_mouse(0, 0, 0, SHIFT) == b"\x1b[M" + bytes((36, 33, 33))
    assert encode_mouse(0, 0, 0, ALT) == b"\x1b[M" + bytes((40, 33, 33))
    assert encode_mouse(0, 0, 0, CTRL) == b"\x1b[M" + bytes((48, 33, 33))
    assert encode_mouse(1, 0, 0, SHIFT | ALT | CTRL) == \
        b"\x1b[M" + bytes((61, 33, 33))
    assert encode_mouse(0, 0, 0, 0, motion=True) == \
        b"\x1b[M" + bytes((64, 33, 33))
    assert encode_mouse(3, 5, 6, CTRL, motion=True) == \
        b"\x1b[M" + bytes((83, 38, 39))


def test_mouse_release_in_the_original_form_names_no_button():
    assert encode_mouse(0, 1, 2, 0, release=True) == \
        b"\x1b[M" + bytes((35, 34, 35))
    assert encode_mouse(2, 1, 2, 0, release=True) == \
        b"\x1b[M" + bytes((35, 34, 35))
    # The modifiers stay.
    assert encode_mouse(1, 1, 2, SHIFT | CTRL, release=True) == \
        b"\x1b[M" + bytes((55, 34, 35))


def test_mouse_in_the_sgr_form():
    assert encode_mouse(0, 0, 0, 0, sgr=True) == b"\x1b[<0;1;1M"
    assert encode_mouse(2, 9, 4, 0, sgr=True) == b"\x1b[<2;10;5M"
    assert encode_mouse(2, 9, 4, 0, sgr=True, release=True) == \
        b"\x1b[<2;10;5m"
    assert encode_mouse(0, 300, 200, SHIFT | ALT | CTRL, sgr=True,
                        motion=True) == b"\x1b[<60;301;201M"
    assert encode_mouse(65, 0, 0, 0, sgr=True) == b"\x1b[<65;1;1M"
    # SGR wins over the UTF-8 form when a program asked for both.
    assert encode_mouse(0, 0, 0, 0, sgr=True, utf8=True) == b"\x1b[<0;1;1M"


def test_the_original_form_ends_at_column_and_row_223():
    assert encode_mouse(0, 222, 0, 0) == b"\x1b[M" + bytes((32, 255, 33))
    assert encode_mouse(0, 0, 222, 0) == b"\x1b[M" + bytes((32, 33, 255))
    assert encode_mouse(0, 223, 0, 0) is None
    assert encode_mouse(0, 0, 223, 0) is None


def test_the_utf8_form_reaches_further():
    assert encode_mouse(0, 0, 0, 0, utf8=True) == b"\x1b[M !!"
    assert encode_mouse(0, 223, 300, 0, utf8=True) == \
        ("\x1b[M " + chr(256) + chr(333)).encode()
    assert encode_mouse(0, 2014, 2014, 0, utf8=True) == \
        ("\x1b[M " + chr(0x7FF) + chr(0x7FF)).encode()
    assert encode_mouse(0, 2015, 0, 0, utf8=True) is None
    assert encode_mouse(0, 0, 2015, 0, utf8=True) is None
    assert encode_mouse(1, 3, 4, CTRL, utf8=True, release=True) == \
        ("\x1b[M" + chr(32 + 19) + chr(36) + chr(37)).encode()
