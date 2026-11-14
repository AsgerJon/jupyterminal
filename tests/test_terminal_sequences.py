"""The parser of the terminal, sequence by sequence: what a program may
send - also in pieces, and with controls in the middle - and what the
terminal makes of it."""

from __future__ import annotations

import gc
import weakref

import pytest

from jupyterminal.term import LineStore, Screen, Terminal
from jupyterminal.term import style


def make(cols: int = 20, rows: int = 5):
    replies: list[bytes] = []
    store = LineStore()
    screen = Screen(cols, rows, store)
    term = Terminal(screen, replies.append)
    term.replies = replies
    return term, screen, store


def feed(term: Terminal, *reads: str) -> None:
    for read in reads:
        term.feed(read.encode())


def texts(screen: Screen) -> list[str]:
    return [row.text() for row in screen.grid]


# -- parameters ---------------------------------------------------------------

def test_an_empty_parameter_is_zero():
    term, screen, _ = make()
    feed(term, "abcdef\x1b[3G\x1b[;K")          # erasing of kind 0: to the end
    assert texts(screen) == ["ab"]
    feed(term, "\x1b[?;2004h")                  # and no private mode 1
    assert (term.app_cursor, term.bracketed_paste) == (False, True)


def test_a_parameter_too_many_is_ignored():
    term, screen, _ = make()
    feed(term, "abcdef\x1b[1G\x1b[2;4Cx")       # two to the right, not four
    assert texts(screen) == ["abxdef"]
    feed(term, "\x1b[?1;0K")                    # erased up to the cursor
    assert texts(screen) == ["    ef"]
    feed(term, "\r\nab\x1b[?1;0J")              # and everything above it
    assert texts(screen) == ["", "  "]
    feed(term, "\x1b[?6;1n\x1b[14;2t")         # where it is, and how large
    assert term.replies == [b"\x1b[?2;3R", b"\x1b[4;90;180t"]
    del term.replies[:]
    feed(term, "\x1b[?2004h\x1b[20h\x1b[?1;2004$p\x1b[4;20$p")
    assert term.replies == [b"\x1b[?1;2$y", b"\x1b[4;2$y"]


def test_a_number_left_out_counts_as_one():
    term, screen, _ = make()
    feed(term, "\x1b[Ca\x1b[ab")                # one to the right, twice
    assert texts(screen) == [" a b"]
    feed(term, "\x1b[Dc")                       # one to the left
    assert texts(screen) == [" a c"]
    feed(term, "\x1b[Gd\x1b[3C\x1b[`e")         # to column one, twice
    assert texts(screen) == ["ea c"]
    feed(term, "\r\n\r\nf\x1b[dg")              # to row one
    assert texts(screen) == ["eg c", "", "f"]
    feed(term, "\x1b[3;3H\x1b[Fh")              # one row up, to its start
    assert texts(screen) == ["eg c", "h", "f"]
    feed(term, "\x1b[1;1H\x1b[X")               # one character erased
    assert texts(screen) == [" g c", "h", "f"]


def test_cursor_next_line_goes_down_as_many_lines_as_asked():
    term, screen, _ = make()
    feed(term, "ab\x1b[3Ec")
    assert texts(screen) == ["ab", "", "", "c"]


def test_scrolling_down_takes_one_number_at_most():
    term, screen, _ = make()
    feed(term, "a\r\nb\x1b[1;2T")
    assert texts(screen) == ["a", "b"]
    feed(term, "\x1b[1;2;3;4;5T")               # this is highlight tracking
    assert texts(screen) == ["a", "b"]


def test_ansi_modes_the_terminal_does_not_have_change_nothing():
    term, screen, _ = make()
    feed(term, "\x1b[2h\x1b[12h")
    assert (screen.insert, screen.newline_mode) == (False, False)
    feed(term, "\x1b[4;20h\x1b[2l\x1b[12l")
    assert (screen.insert, screen.newline_mode) == (True, True)


def test_a_sequence_the_terminal_does_not_know_is_no_window_request():
    term, _, _ = make()
    feed(term, "\x1b[18x\x1b[14y\x1b[16~\x1b[11q")
    assert term.replies == []
    feed(term, "\x1b[8;24;18t")                 # asks for a size, not about it
    assert term.replies == []


@pytest.mark.parametrize("private", [">4;2", "?1", "<1", "=1"])
def test_a_private_sequence_ending_in_m_leaves_the_style_alone(private):
    term, screen, _ = make()
    bold = style.intern(None, None, style.BOLD)
    feed(term, f"\x1b[1m\x1b[{private}mx")
    assert (screen.attrs, screen.sid) == (style.BOLD, bold)
    assert screen.grid[0].styles == [bold]


# -- sequences with bytes before the final one --------------------------------

def test_only_a_blank_before_q_sets_the_shape_of_the_cursor():
    term, screen, _ = make()
    feed(term, "\x1b[4 q\x1b[1\"q\x1b[2 @\x1b[3$q")
    assert screen.cursor_shape == 4


def test_two_intermediate_bytes_are_not_the_second_one_alone():
    term, screen, _ = make()
    feed(term, "\x1b[4h\x1b[2 !p\x1b[4 $p")     # no soft reset, no request
    assert screen.insert is True and term.replies == []
    feed(term, "\x1b[2\x00 !p\x1b[4\x00 $p")    # nor with a control inside
    assert screen.insert is True and term.replies == []


# -- private sequences --------------------------------------------------------

def test_the_private_forms_of_erasing_take_the_same_kinds():
    term, screen, _ = make()
    feed(term, "abcdef\x1b[3G\x1b[?1K")
    assert texts(screen) == ["   def"]
    feed(term, "\x1b[?2K")
    assert texts(screen) == [""]
    feed(term, "ab\r\ncd\x1b[?1J")
    assert texts(screen) == ["", "  "]
    feed(term, "ef\x1b[?2J")
    assert texts(screen) == ["", ""] and screen.clear_count == 1


def test_the_private_cursor_report_is_asked_for_with_6_n_only():
    term, _, _ = make()
    feed(term, "ab\x1b[?5n\x1b[?n\x1b[?6c\x1b[?6s\x1b[?6r")
    assert term.replies == []
    feed(term, "\x1b[?6n")
    assert term.replies == [b"\x1b[?1;3R"]


def test_private_modes_the_terminal_does_not_have_change_nothing():
    term, _, _ = make()
    feed(term, "\x1b[?12h\x1b[?2027h")
    assert term.sync_output is False
    feed(term, "\x1b[?2026h\x1b[?12l\x1b[?2027l")
    assert term.sync_output is True


def test_switching_autowrap_on_again_keeps_a_pending_wrap():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\x1b[?7hf")
    assert texts(screen) == ["abcde", "f"]
    assert screen.grid[0].wrapped is True


def test_a_wrap_pending_when_autowrap_goes_off_is_forgotten():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\x1b[?7l\x1b[?7hf")
    assert texts(screen) == ["abcdf"]


# -- the alternate screen -----------------------------------------------------

@pytest.mark.parametrize("mode", [47, 1047])
def test_the_plain_alternate_screen_does_not_save_the_cursor(mode):
    term, screen, _ = make()
    feed(term, f"abc\x1b7\r\nxyz\x1b[?{mode}h\x1b[?{mode}l\x1b8!")
    assert texts(screen) == ["abc!", "xyz"]


def test_leaving_through_1049_restores_the_cursor_however_it_was_entered():
    term, screen, _ = make()
    feed(term, "abc\x1b7\r\nxyz\x1b[?47hfull\x1b[?1049l!")
    assert texts(screen) == ["abc!", "xyz"]


# A screen holds every line of its block.  The terminal must not keep one
# alive that it no longer draws on.

def test_the_screen_behind_the_alternate_one_is_let_go_with_it():
    term, screen, _ = make()
    held = weakref.ref(screen)
    feed(term, "\x1b[?1049h")
    term.set_screen(Screen(20, 5, LineStore()))
    del screen
    gc.collect()
    assert held() is None


def test_a_screen_that_was_behind_the_alternate_one_is_let_go_as_well():
    term, screen, _ = make()
    held = weakref.ref(screen)
    feed(term, "\x1b[?1049hfull\x1b[?1049l")
    term.set_screen(Screen(20, 5, LineStore()))
    del screen
    gc.collect()
    assert held() is None


# -- controls -----------------------------------------------------------------

def test_controls_that_mean_nothing_leave_the_character_set_alone():
    term, screen, _ = make()
    feed(term, "\x1b)0\x0eq\x00q\x7fq\x01q\x0fq")
    assert texts(screen) == ["────q"]


# -- escape sequences with controls in them -----------------------------------

def test_a_control_after_an_escape_acts_and_the_sequence_goes_on():
    term, screen, _ = make()
    feed(term, "ab\x1b\nDcd")                   # a linefeed, then ESC D
    assert texts(screen) == ["ab", "", "  cd"]
    feed(term, "\x1b\x00[2Ce")                  # then a control sequence
    assert texts(screen) == ["ab", "", "  cd  e"]
    feed(term, "\x1b\r\x00]2;title\x07f")       # then a string
    assert term.title == "title"
    assert texts(screen) == ["ab", "", "f cd  e"]


def test_an_escape_cut_off_after_a_control_is_not_forgotten():
    term, screen, _ = make()
    feed(term, "a\x1b\x00", "Db")
    assert texts(screen) == ["a", " b"]


def test_a_bell_inside_a_sequence_that_is_cut_short_rings_once():
    term, screen, _ = make()
    rings = ["once"]
    term.on_bell = rings.pop                    # a second ring is an error
    feed(term, "a\x1b\x07[3", "Cb")
    assert rings == []
    assert texts(screen) == ["a   b"]


def test_a_control_does_not_part_an_escape_from_its_intermediate_byte():
    term, screen, _ = make()
    feed(term, "a\r\nb\x1b\x00 Mc")             # ESC SP M, not ESC M
    assert texts(screen) == ["a", "bc"]
    feed(term, "\x1b\x00 ", "Md")               # also when cut after the blank
    assert texts(screen) == ["a", "bcd"]


def test_a_blank_is_taken_as_the_third_character_of_an_escape():
    term, screen, _ = make()
    feed(term, "a\x1b( ")
    assert (texts(screen), screen.cx) == (["a"], 1)


def test_only_number_8_of_the_escapes_with_a_hash_fills_the_screen():
    term, screen, _ = make()
    feed(term, "ab\x1b#3\x1b#4\x1b#5\x1b#6c")
    assert texts(screen) == ["abc"]


def test_escapes_the_terminal_does_not_know_are_not_answered():
    term, screen, _ = make()
    feed(term, "a\x1b\\b\x1bnc\x1b~d\x1b6e")
    assert term.replies == [] and texts(screen) == ["abcde"]


# -- control sequences with controls in them ----------------------------------

def test_a_control_inside_a_control_sequence_leaves_its_final_byte_alone():
    term, screen, _ = make()
    feed(term, "abc\x1b[1G\x1b[2\x00@")         # the lowest final byte
    assert texts(screen) == ["  abc"]
    feed(term, "\x1b[2\x00~x")                  # and the highest
    assert texts(screen) == ["x abc"]
    feed(term, "\x1b[2\x7fCy")                  # a delete is not one
    assert texts(screen) == ["x ayc"]


def test_a_control_inside_a_control_sequence_leaves_its_parts_alone():
    term, screen, _ = make()
    feed(term, "\x1b[4\x00 q")                  # the blank is an intermediate
    assert screen.cursor_shape == 4
    feed(term, "\x1b[10\x00Cx")                 # the zero is a digit
    assert texts(screen) == [" " * 10 + "x"]
    feed(term, "\x1b[2\x1fDy")                  # the last control is neither
    assert texts(screen) == [" " * 9 + "yx"]


def test_an_escape_inside_a_control_sequence_starts_a_new_sequence():
    term, screen, _ = make()
    feed(term, "a\x1b[3\x1b[2Cb")
    assert texts(screen) == ["a  b"]
    feed(term, "\x1b[5", "\x1b[2Cc")            # also when cut in between
    assert texts(screen) == ["a  b  c"]


# -- strings ------------------------------------------------------------------

def test_a_string_is_measured_from_where_it_starts():
    term, _, _ = make()
    half = 2 * 1024 * 1024                      # of what a string may hold
    feed(term, "\x1b]0;" + "t" * half + "\x07\x1b]2;kept", "\x07")
    assert term.title == "kept"


def test_a_substitute_cancels_a_string_without_using_it():
    term, screen, _ = make()
    feed(term, "\x1b]2;lost\x1aa\x1bP$qm\x1ab\x1bP$qm\x18c")
    assert term.title == "" and term.replies == []
    assert texts(screen) == ["abc"]


def test_a_bell_right_before_the_end_of_a_string_belongs_to_it():
    term, screen, _ = make()
    feed(term, "\x1bPx\x07\x1b\\a\x1b_y\x07", "\x1b\\b")
    assert texts(screen) == ["ab"] and term.replies == []


def test_only_a_device_control_string_is_answered():
    term, screen, _ = make()
    feed(term, "\x1b_$qm\x1b\\a\x1b^+q544e\x1b\\b\x1bX$q q\x1b\\c")
    assert term.replies == [] and texts(screen) == ["abc"]


# -- operating system commands ------------------------------------------------

def test_a_mark_nobody_listens_for_is_dropped():
    term, screen, _ = make()
    term.nonce = "secret"
    feed(term, "\x1b]7717;secret;C;x\x07ok")
    assert texts(screen) == ["ok"]


def test_palette_colors_come_in_pairs_of_number_and_color():
    term, _, _ = make()
    asked = []
    term.color_query = lambda kind, index: asked.append((kind, index))
    feed(term, "\x1b]4;1;2;?\x07")              # color 1 is set, to "2"
    assert asked == []
    feed(term, "\x1b]4;1;2;3;?\x07")
    assert asked == [("palette", 3)] and term.replies == []


def test_only_command_52_reaches_the_clipboard():
    term, _, _ = make()
    seen = []
    term.on_clipboard = lambda target, text: seen.append((target, text))
    feed(term, "\x1b]5;c;aGk=\x07\x1b]520;c;aGk=\x07\x1b]8;;aGk=\x07")
    assert seen == []
    feed(term, "\x1b]52;c;aGk=\x07")
    assert seen == [("c", "hi")]


def test_clipboard_data_may_be_wrapped_over_several_lines():
    term, _, _ = make()
    seen = []
    term.on_clipboard = lambda target, text: seen.append((target, text))
    feed(term, "\x1b]52;c;aGVsbG8g\r\nd29ybGQ=\x07")
    assert seen == [("c", "hello world")]


# -- many plain lines at once -------------------------------------------------

def test_a_few_long_lines_leave_the_rows_above_them_on_the_screen():
    term, screen, store = make(cols=100, rows=40)
    feed(term, "one\r\ntwo\r\n", ("x" * 700 + "\r\n") * 3)
    assert store.texts == []
    assert texts(screen)[:3] == ["one", "two", "x" * 100]
    assert (len(screen.grid), screen.cy, screen.cx) == (24, 23, 0)


# Reads at the edges of the short cut for many plain lines, and one that
# takes it: what was printed last is known either way.

@pytest.mark.parametrize("read", [
    pytest.param("ab\r\ncd\r\n", id="short lines"),
    pytest.param("abcd\r\n" * 600, id="through the short cut"),
    pytest.param("x" * 1100 + "\r\nab\r\ncd\r\n", id="after a long line"),
    pytest.param("\r" + "a" * 1100 + "\r\n" + "d" * 1100 + "\r\n",
                 id="long lines after a carriage return"),
    pytest.param("a" * 1000 + "\r\n" + "d" * 1043 + "\r\n",
                 id="2047 characters"),
])
def test_the_last_character_is_repeated_after_lines_that_fill_the_screen(
        read):
    term, screen, _ = make(cols=20, rows=2)
    feed(term, read, "\x1b[2b")
    assert texts(screen)[-1] == "dd"
    assert (screen.cy, screen.cx) == (1, 2)


def test_a_slash_is_an_intermediate_character_like_the_others():
    term, screen, _ = make()
    feed(term, "a\x1b(/Xb")                     # the X ends it, not the slash
    assert texts(screen) == ["ab"]


def test_empty_lines_leave_the_last_character_as_it_was():
    term, screen, _ = make(cols=20, rows=3)
    feed(term, "x\r\n", "\r\n" * 2000, "\x1b[2b")
    assert texts(screen) == ["", "", "xx"]


# -- what the two screens share -----------------------------------------------

def test_the_alternate_screen_takes_over_what_belongs_to_the_terminal():
    term, screen, _ = make()
    feed(term, "\x1b[?1049h")
    alt = term.screen                           # nothing was set: nothing is
    assert (alt.cursor_visible, alt.autowrap, alt.insert,
            alt.newline_mode, alt.cursor_shape) == (True, True, False,
                                                    False, 0)
    assert alt.tabs is None and alt.charsets() == ("B", "B", 0)
    feed(term, "\x1b[?1049l")
    # Hidden cursor, no wrapping, insert and new line mode, a cursor
    # shape, a tab stop in column 5, line drawing in the second set and
    # that one shifted in.
    feed(term, "\x1b[?25l\x1b[?7l\x1b[4h\x1b[20h\x1b[3 q\x1b[5G\x1bH"
               "\x1b)0\x0e")
    feed(term, "\x1b[?1049h")
    alt = term.screen
    assert alt is not screen
    assert (alt.cursor_visible, alt.autowrap, alt.insert,
            alt.newline_mode, alt.cursor_shape) == (False, False, True,
                                                    True, 3)
    assert alt.tabs == {4, 8, 16} and alt.charsets() == ("B", "0", 1)
    feed(term, "q")
    assert texts(alt) == ["─"]


def test_what_a_program_sets_on_the_alternate_screen_stays_set():
    term, screen, _ = make()
    feed(term, "\x1b[?47h")
    feed(term, "\x1b[?25l\x1b[?7l\x1b[4h\x1b[20h\x1b[5 q\x1b[3g"
               "\x1b(0\x0e")
    feed(term, "\x1b[?47l")
    assert term.screen is screen
    assert (screen.cursor_visible, screen.autowrap, screen.insert,
            screen.newline_mode, screen.cursor_shape) == (False, False,
                                                          True, True, 5)
    assert screen.tabs == set() and screen.charsets() == ("0", "B", 1)


def test_a_soft_reset_puts_the_character_sets_back():
    term, screen, _ = make()
    feed(term, "\x1b(0\x1b)0\x0e")
    assert screen.charsets() == ("0", "0", 1)
    feed(term, "\x1b[!p")
    assert screen.charsets() == ("B", "B", 0)


def test_a_cursor_hidden_before_the_alternate_screen_can_be_shown_on_it():
    term, screen, _ = make()
    feed(term, "\x1b[?25l\x1b[?1049h")
    assert not term.screen.cursor_visible
    feed(term, "\x1b[?25h\x1b[?1049l")
    assert term.screen is screen and screen.cursor_visible


# -- where a stream is cut makes no difference --------------------------------

STREAM = "".join((
    "ab\x1b\nDcd",                  # a linefeed between ESC and D
    "\x1b\x00[2Ce",                 # a null before a control sequence
    "\x1b\r\x00]2;title\x07f",      # a return and a null before a string
    "\x1b\x07\x00 Mg",              # a bell and a null before ESC SP M
    "\r\n\x1b\x00\x00(0q\x1b(Bq",   # two nulls before a character set
    "\x1b[3\x1b[2Ch",               # an escape inside a control sequence
    "\x1b[2\x00 q",                 # a null before an intermediate byte
    "\x1b[1\x1f\x7f0\x00Ci",        # controls and a delete between digits
    "\x1bPx\x07\x1b\\j",            # a bell right before the terminator
    "\x1b]0;lost\x1ak",             # a string that is cancelled
    "\x1b\x18l\x1b[2\x1am",         # an escape and a sequence, cancelled
    "\x1b[?6\x07n",                 # a bell inside a request
))


def outcome(reads: list[bytes]) -> tuple:
    term, screen, _ = make(cols=20, rows=6)
    rings: list[str] = []
    term.on_bell = lambda: rings.append("ring")
    for read in reads:
        term.feed(read)
    return (texts(screen), screen.cy, screen.cx, screen.cursor_shape,
            term.title, term.replies, rings)


def test_a_stream_of_sequences_with_controls_in_them_may_be_cut_anywhere():
    data = STREAM.encode()
    whole = outcome([data])
    assert whole == (
        ["ab", "", "fgcd  e", "─q  h" + " " * 10 + "ijklm"],
        3, 19, 2, "title", [b"\x1b[?4;20R"], ["ring", "ring"])
    for cut in range(1, len(data)):
        assert outcome([data[:cut], data[cut:]]) == whole, cut
    assert outcome([bytes((byte,)) for byte in data]) == whole
