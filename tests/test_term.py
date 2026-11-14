"""Terminal emulation core: parser and screen model."""

from __future__ import annotations

import random

import pytest

from jupyterminal.term import LineStore, Screen, Terminal, terminal as tmod
from jupyterminal.term import style
from jupyterminal.term.screen import row_text_runs


def make(cols: int = 20, rows: int = 5):
    replies: list[bytes] = []
    store = LineStore()
    screen = Screen(cols, rows, store)
    term = Terminal(screen, replies.append)
    term.replies = replies
    return term, screen, store


def lines(screen: Screen) -> list[str]:
    return [text for text, _ in screen.all_lines()]


def feed(term: Terminal, text: str) -> None:
    term.feed(text.encode())


# -- printing and wrapping --------------------------------------------------

def test_plain_lines():
    term, screen, _ = make()
    feed(term, "one\r\ntwo\r\n")
    assert lines(screen) == ["one", "two"]
    assert (screen.cy, screen.cx) == (2, 0)


def test_no_trailing_newline_keeps_last_line():
    term, screen, _ = make()
    feed(term, "abc")
    assert lines(screen) == ["abc"]


def test_blank_lines_inside_output_are_kept():
    term, screen, _ = make()
    feed(term, "a\r\n\r\n\r\nb\r\n")
    assert lines(screen) == ["a", "", "", "b"]


def test_only_the_final_newline_is_dropped():
    term, screen, _ = make(rows=10)
    feed(term, "a\r\n\r\n\r\n")
    assert lines(screen) == ["a", "", ""]


def test_long_line_wraps_on_screen_but_is_one_logical_line():
    term, screen, _ = make(cols=10, rows=8)
    text = "".join(chr(ord("a") + i % 26) for i in range(35))
    feed(term, text + "\r\nnext\r\n")
    assert len(screen.grid) == 6  # 4 rows + "next" + cursor row
    assert [r.wrapped for r in screen.grid[:4]] == [True, True, True, False]
    assert lines(screen) == [text, "next"]


def test_exactly_full_row_does_not_wrap():
    term, screen, _ = make(cols=10)
    feed(term, "0123456789\r\nx\r\n")
    assert lines(screen) == ["0123456789", "x"]
    assert screen.grid[0].wrapped is False


def test_wrapped_line_survives_scrolling_into_the_store():
    term, screen, store = make(cols=10, rows=3)
    text = "x" * 47
    feed(term, text + "\r\nend\r\n")
    assert lines(screen) == [text, "end"]
    # Its last row is still on the grid, so the line is not complete yet.
    assert store.texts == [] and len(store.open_parts) == 4
    feed(term, "a\r\nb\r\n")
    assert store.texts[0] == text
    assert lines(screen) == [text, "end", "a", "b"]


def counted(screen: Screen) -> tuple[int, list[str]]:
    """The count of lines, and the lines it has to be the count of."""
    return screen.line_count(), lines(screen)


def test_the_lines_are_counted_without_being_built():
    term, screen, store = make(cols=5, rows=2)
    assert counted(screen) == (0, [])
    feed(term, "ab")
    assert counted(screen) == (1, ["ab"])            # not over, but a line
    feed(term, "cdefg")
    assert counted(screen) == (1, ["abcdefg"])       # two rows, one line
    feed(term, "\r\nx")
    # The first row left the screen with its line still going on.
    assert store.texts == [] and store.open_parts == ["abcde"]
    assert counted(screen) == (2, ["abcdefg", "x"])
    feed(term, "\r\ny")
    assert store.texts == ["abcdefg"] and store.open_parts is None
    assert counted(screen) == (3, ["abcdefg", "x", "y"])
    feed(term, "\r\n")
    assert counted(screen) == (3, ["abcdefg", "x", "y"])


def test_a_line_that_ends_in_a_full_row_is_counted_once():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcdefghij")                 # two full rows, the wrap pending
    assert [row.wrapped for row in screen.grid] == [True, False]
    assert counted(screen) == (1, ["abcdefghij"])
    feed(term, "k")
    assert [row.wrapped for row in screen.grid] == [True, True, False]
    assert counted(screen) == (1, ["abcdefghijk"])


def test_a_line_of_which_nothing_is_left_on_the_screen_still_counts():
    term, screen, store = make(cols=5, rows=2)
    feed(term, "abcdefghijkl\x1b[2J\x1b[H")
    assert store.texts == [] and store.open_parts == ["abcde"]
    assert screen.content_rows() == 0
    assert counted(screen) == (1, ["abcde"])


def test_a_screen_without_a_store_counts_what_is_on_it():
    screen = Screen(5, 3)
    term = Terminal(screen, lambda data: None)
    feed(term, "abcdefg\r\nx")
    assert screen.store is None
    assert counted(screen) == (2, ["abcdefg", "x"])


def test_trailing_spaces_are_not_stored():
    term, screen, store = make(cols=20, rows=2)
    feed(term, "abc   \r\ndef\r\nghi\r\n")
    assert store.texts == ["abc", "def"]


# -- carriage returns, cursor movement and erasing --------------------------

def test_carriage_return_redraw_keeps_only_final_state():
    term, screen, _ = make(cols=40)
    for pct in (10, 50, 100):
        feed(term, f"\rprogress {pct:3d}%")
    feed(term, "\r\n")
    assert lines(screen) == ["progress 100%"]


def test_shorter_redraw_with_erase():
    term, screen, _ = make(cols=40)
    feed(term, "downloading 123456\r\x1b[Kdone\r\n")
    assert lines(screen) == ["done"]


def test_cursor_up_rewrites_an_earlier_line():
    term, screen, _ = make(cols=40, rows=10)
    feed(term, "a: 0%\r\nb: 0%\r\n")
    feed(term, "\x1b[2A\x1b[2Ka: 100%\r\n\x1b[2Kb: 100%\r\n")
    assert lines(screen) == ["a: 100%", "b: 100%"]


def test_cursor_addressing_and_erase_display():
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "one\r\ntwo\r\nthree")
    feed(term, "\x1b[2;1H\x1b[J")
    assert lines(screen) == ["one"]
    feed(term, "\x1b[H\x1b[2J")
    assert lines(screen) == []
    assert screen.clear_count == 1


def test_insert_and_delete_characters():
    term, screen, _ = make()
    feed(term, "abcdef\x1b[1;3H\x1b[2P")
    assert lines(screen) == ["abef"]
    feed(term, "\x1b[2@")
    assert lines(screen) == ["ab  ef"]
    feed(term, "\x1b[1;1H\x1b[3X")
    assert lines(screen) == ["    ef"]


def test_insert_and_delete_lines():
    term, screen, _ = make(rows=6)
    feed(term, "a\r\nb\r\nc\r\nd")
    feed(term, "\x1b[2;1H\x1b[L")
    assert lines(screen) == ["a", "", "b", "c", "d"]
    feed(term, "\x1b[M\x1b[M")
    assert lines(screen) == ["a", "c", "d"]


def test_scroll_region_keeps_the_status_row():
    term, screen, store = make(cols=20, rows=5)
    feed(term, "\x1b[1;4r")            # scroll rows 1-4, row 5 is fixed
    feed(term, "\x1b[5;1Hstatus")
    feed(term, "\x1b[1;1H")
    for i in range(6):
        feed(term, f"line{i}\r\n")
    assert screen.grid[4].text() == "status"
    assert store.texts == ["line0", "line1", "line2"]
    assert screen.grid[0].text() == "line3"


def test_reverse_index_scrolls_down():
    term, screen, _ = make(rows=4)
    feed(term, "a\r\nb\r\nc\x1b[H\x1bM")
    assert [r.text() for r in screen.grid[:4]] == ["", "a", "b", "c"]


def test_save_and_restore_cursor():
    term, screen, _ = make()
    feed(term, "abc\x1b7\r\nxyz\x1b8!")
    assert lines(screen) == ["abc!", "xyz"]


# -- styles ------------------------------------------------------------------

def test_sgr_runs_are_recorded():
    term, screen, _ = make(cols=40)
    feed(term, "plain \x1b[31mred\x1b[0m \x1b[1;38;2;1;2;3mrgb\x1b[m\r\n")
    (text, runs), = screen.all_lines()
    assert text == "plain red rgb"
    assert runs is not None
    spans = []
    start = 0
    for end, sid in runs:
        spans.append((text[start:end], style.lookup(sid)))
        start = end
    assert spans[0] == ("plain ", (None, None, 0))
    assert spans[1] == ("red", (1, None, 0))
    assert spans[3] == ("rgb", (style.RGB | 0x010203, None, style.BOLD))


def test_256_color_and_colon_forms():
    assert style.apply_sgr("38;5;200", None, None, 0)[0] == 200
    assert style.apply_sgr("48:5:17", None, None, 0)[1] == 17
    assert style.apply_sgr("38:2::10:20:30", None, None, 0)[0] == \
        style.RGB | 0x0A141E
    assert style.apply_sgr("1;4;7", None, None, 0)[2] == \
        style.BOLD | style.UNDERLINE | style.INVERSE
    assert style.apply_sgr("0", 3, 4, 5) == (None, None, 0)


def test_styled_line_keeps_runs_after_scrolling():
    term, screen, store = make(cols=30, rows=2)
    feed(term, "\x1b[32mgreen\x1b[0m tail\r\nb\r\nc\r\n")
    assert store.texts[0] == "green tail"
    runs = store.runs(0)
    assert runs[0][0] == 5 and style.lookup(runs[0][1])[0] == 2


def test_background_erase_fills_the_row():
    term, screen, _ = make(cols=10)
    feed(term, "\x1b[44mab\x1b[K\x1b[0m")
    row = screen.grid[0]
    assert len(row.cells) == 10
    assert not row.is_blank()
    # The colored blanks are kept for display; copying strips them later.
    assert lines(screen) == ["ab" + " " * 8]


# -- tabs, wide and combining characters ------------------------------------

def test_tab_is_kept_as_a_tab():
    term, screen, _ = make(cols=40)
    feed(term, "a\tb\tc\r\n")
    assert lines(screen) == ["a\tb\tc"]
    assert screen.grid[0].cells[8] == "b"


def test_overwritten_tab_becomes_spaces():
    term, screen, _ = make(cols=40)
    feed(term, "a\tb\r\x1b[3Cx\r\n")
    assert lines(screen) == ["a  x    b"]


def test_wide_characters_take_two_cells():
    term, screen, _ = make(cols=10)
    feed(term, "日本語ab\r\n")
    assert screen.grid[0].cells[:8] == ["日", "", "本", "", "語", "", "a", "b"]
    assert lines(screen) == ["日本語ab"]


def test_wide_character_wraps_whole():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcd日x\r\n")
    assert screen.grid[0].wrapped is True
    assert screen.grid[1].cells[:3] == ["日", "", "x"]
    assert lines(screen) == ["abcd日x"]


def test_overwriting_half_a_wide_character_clears_it():
    term, screen, _ = make(cols=10)
    feed(term, "日本\x1b[1;2Hx\r\n")
    assert lines(screen) == [" x本"]


def test_a_wide_character_and_a_marked_one_keep_their_own_styles():
    """A wide character is one character in two cells, a marked one two
    characters in one cell.  Together they are as many characters as
    cells, and the styles still belong to cells, not to characters."""
    term, screen, _ = make(cols=10)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    feed(term, "\x1b[31m\u4f60\x1b[32me\u0301\x1b[0m")
    row = screen.grid[0]
    assert row.cells == ["\u4f60", "", "e\u0301"]
    assert row.styles == [red, red, green]
    assert row_text_runs(row, False) == (
        "\u4f60e\u0301", ((1, red), (3, green)))


def test_combining_marks_join_their_base():
    term, screen, _ = make(cols=10)
    feed(term, "e\u0301x\r\n")
    assert screen.grid[0].cells[:2] == ["e\u0301", "x"]
    assert lines(screen) == ["e\u0301x"]


def test_line_drawing_charset():
    term, screen, _ = make(cols=10)
    feed(term, "\x1b(0lqk\x1b(Bx\r\n")
    assert lines(screen) == ["┌─┐x"]


# -- parser robustness -------------------------------------------------------

def test_escape_sequences_split_across_reads():
    term, screen, _ = make(cols=40)
    data = "a\x1b[31mb\x1b[0mc\x1b]0;title\x07d\r\n".encode()
    for byte in data:
        term.feed(bytes((byte,)))
    assert lines(screen) == ["abcd"]
    assert term.title == "title"


def test_utf8_split_across_reads():
    term, screen, _ = make()
    data = "héllo→\r\n".encode()
    for byte in data:
        term.feed(bytes((byte,)))
    assert lines(screen) == ["héllo→"]


def test_control_inside_csi_is_executed():
    term, screen, _ = make()
    feed(term, "ab\x1b[\r2Cx\r\n")
    assert lines(screen) == ["abx"]


def test_unknown_sequences_are_swallowed():
    term, screen, _ = make(cols=40)
    feed(term, "a\x1bP+q544e\x1b\\b\x1b_apc\x1b\\c\x1b[?2026hd\x1b[>4;2me\r\n")
    assert lines(screen) == ["abcde"]


def test_marks_require_the_nonce():
    term, screen, _ = make()
    seen = []
    term.nonce = "secret"
    term.on_mark = lambda kind, fields: seen.append((kind, fields))
    feed(term, "\x1b]7717;wrong;C;x\x07\x1b]7717;secret;C;a;b\x07ok")
    assert seen == [("C", ["a", "b"])]
    assert lines(screen) == ["ok"]


def test_queries_are_answered():
    term, screen, _ = make(cols=30, rows=7)
    feed(term, "ab\r\ncd\x1b[6n\x1b[5n\x1b[18t\x1b[c")
    assert term.replies == [b"\x1b[2;3R", b"\x1b[0n", b"\x1b[8;7;30t",
                            b"\x1b[?62;22c"]


def test_mode_reports():
    term, _, _ = make()
    feed(term, "\x1b[?2004h\x1b[?2004$p\x1b[?1$p\x1b[?9999$p")
    assert term.bracketed_paste is True
    assert term.replies == [b"\x1b[?2004;1$y", b"\x1b[?1;2$y",
                            b"\x1b[?9999;0$y"]


# -- alternate screen --------------------------------------------------------

def test_alternate_screen_leaves_the_main_screen_alone():
    term, screen, _ = make()
    events = []
    term.on_alt = events.append
    feed(term, "before\r\n\x1b[?1049h\x1b[2J\x1b[Hfull screen")
    assert term.alt_active and term.screen is not screen
    assert term.screen.grid[0].text() == "full screen"
    feed(term, "\x1b[?1049lafter\r\n")
    assert term.screen is screen and not term.alt_active
    assert lines(screen) == ["before", "after"]
    assert events == [True, False]


# -- large output ------------------------------------------------------------

def _plain_stream(rng: random.Random, count: int, cols: int,
                  ragged: float = 0.2) -> str:
    out = []
    for _ in range(count):
        kind = rng.random()
        if kind < 0.05:
            length = 0
        elif kind < 0.15:
            length = rng.randint(cols - 2, cols * 3)
        elif kind < 0.25:
            length = cols * rng.randint(1, 3)   # rows filled to the last cell
        else:
            length = rng.randint(1, cols)
        text = "".join(rng.choice("abc xyzé0") for _ in range(length))
        if rng.random() < ragged:
            # Blanks at the end, of any length: to the end of its last
            # row, or over a whole row and more.
            blanks = rng.randint(1, cols + 3)
            text = text[:max(0, length - blanks)] + " " * blanks
        out.append(text + "\r\n")
    return "".join(out)


def _everything(screen: Screen) -> tuple:
    """All there is to a screen and its store, to compare two of them."""
    store = screen.store
    rows = [(row.cells, row.wrapped, row_text_runs(row, True)[1])
            for row in screen.grid]
    return (rows, screen.cy, screen.cx, screen.wrap_pending,
            screen.last_char, store.texts, store.styles, store.complex,
            store.max_width, store.base, store.open_parts, store.open_runs,
            store.open_len)


#: What may be on the screen when the plain lines come.
_BEFORE = (
    "",
    "one\r\ntwo\r\n",
    "\x1b[31mred\x1b[0m\r\n",
    "a line that is longer than the screen is wide by far, so that it "
    "takes several rows of it, and then some more of them\r\n",
    # The same line, wiped off the screen while its first rows are in
    # the store already: the lines that come go on with it.
    "a line that is longer than the screen is wide by far, so that it "
    "takes several rows of it, and then some more of them\x1b[H\x1b[J",
    "gone\r\n\x1b[H\x1b[2J",
    "x\r\n\x1b[3b\r\n",
)


@pytest.mark.parametrize("seed", range(60))
def test_bulk_path_matches_the_careful_path(seed, monkeypatch):
    rng = random.Random(seed)
    cols, rows = rng.choice([(20, 6), (37, 11), (80, 24), (7, 1), (5, 2),
                             (12, 60), (1, 3)])
    before = rng.choice(_BEFORE)
    data = _plain_stream(rng, rng.choice([3, 40, 800]), cols,
                         rng.choice([0.0, 0.2]))
    tail = rng.choice(["", "partial", "\x1b[31mred\x1b[0m\r\n", "\x1b[2b"])
    pieces = [before.encode()]
    raw = (data + tail).encode()
    longest = rng.choice([60, 900, 9000])
    pos = 0
    while pos < len(raw):
        step = rng.randint(1, longest)
        pieces.append(raw[pos:pos + step])
        pos += step

    # A short run of lines takes the short cut as well, here: it has to
    # be right for any number of them.
    monkeypatch.setattr(tmod, "_BULK_MIN", rng.choice([1, 30, 300]))
    fast, fast_screen, _ = make(cols, rows)
    for piece in pieces:
        fast.feed(piece)

    monkeypatch.setattr(tmod, "_BULK_MIN", 10 ** 9)
    slow, slow_screen, _ = make(cols, rows)
    for piece in pieces:
        slow.feed(piece)

    assert _everything(fast_screen) == _everything(slow_screen)
    assert fast_screen.all_lines() == slow_screen.all_lines()


def test_a_million_lines():
    term, screen, store = make(cols=80, rows=24)
    data = "".join(f"{i}\r\n" for i in range(1, 200_001)).encode()
    for pos in range(0, len(data), 65536):
        term.feed(data[pos:pos + 65536])
    all_lines = lines(screen)
    assert len(all_lines) == 200_000
    assert all_lines[0] == "1" and all_lines[-1] == "200000"
    assert len(screen.grid) == 24


# -- resizing ----------------------------------------------------------------

def test_rewrap_on_resize_keeps_logical_lines():
    term, screen, _ = make(cols=20, rows=10)
    text = "".join(chr(ord("a") + i % 26) for i in range(50))
    feed(term, text + "\r\nshort\r\n$ ")
    before = lines(screen)
    screen.resize(8, 10)
    assert lines(screen) == before
    assert all(len(r.cells) <= 8 for r in screen.grid)
    screen.resize(60, 10)
    assert lines(screen) == before
    assert screen.grid[screen.cy].text() == "$ "
    assert screen.cx == 2


def test_rewrap_joins_the_part_that_already_scrolled_off():
    term, screen, store = make(cols=10, rows=3)
    text = "y" * 45
    feed(term, text)
    assert store.open_parts is not None
    screen.resize(15, 3)
    assert lines(screen) == [text]
    feed(term, "\r\nz\r\n")
    assert lines(screen) == [text, "z"]


def test_clip_mode_does_not_rewrap():
    screen = Screen(20, 5, LineStore(), reflow=False)
    term = Terminal(screen)
    feed(term, "0123456789abcdef\r\nxy")
    screen.resize(8, 5)
    assert [r.text() for r in screen.grid] == ["01234567", "xy"]


def test_shrinking_rows_freezes_the_top():
    term, screen, store = make(cols=20, rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne")
    screen.resize(20, 3)
    assert store.texts == ["a", "b"]
    assert lines(screen) == ["a", "b", "c", "d", "e"]
    assert screen.cy == 2


# -- freezing ----------------------------------------------------------------

def test_finish_moves_everything_into_the_store():
    term, screen, store = make(cols=10, rows=4)
    feed(term, "one\r\n" + "w" * 25 + "\r\nlast")
    screen.finish()
    assert store.texts == ["one", "w" * 25, "last"]
    assert store.max_width == 25


def test_store_drop_front_keeps_absolute_indices():
    store = LineStore()
    for i in range(10):
        store.append(f"line{i}", ((2, 5),) if i % 2 else None)
    store.drop_front(4)
    assert store.base == 4 and store.end == 10
    assert store.text(4) == "line4"
    assert store.runs(5) == ((2, 5),)
    assert 3 not in store.styles


# -- found in review ----------------------------------------------------------

def test_tab_at_the_right_margin_stays_in_the_text():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "a" * 10 + "\tb\r\n")
    assert lines(screen) == ["a" * 10 + "\tb"]
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "abcdefg\t\t\tx\r\n")             # tabs running into the margin
    assert lines(screen) == ["abcdefg\t\t\tx"]


@pytest.mark.parametrize("cols", [20, 37, 80, 100, 120])
def test_tab_separated_lines_keep_every_tab(cols):
    rng = random.Random(cols)
    term, screen, store = make(cols=cols, rows=8)
    expected = []
    for _ in range(300):
        fields = ["".join(rng.choice("abcdefgh0123")
                          for _ in range(rng.randint(0, 14)))
                  for _ in range(rng.randint(1, 30))]
        while fields and not fields[-1]:
            fields.pop()                         # no trailing tab-only tail
        line = "\t".join(fields) or "x"
        expected.append(line)
        feed(term, line + "\r\n")
    screen.finish()
    assert store.texts == expected


def test_a_stray_escape_does_not_swallow_what_follows():
    term, screen, _ = make(cols=40)
    seen = []
    term.nonce = "n"
    term.on_mark = lambda kind, fields: seen.append(kind)
    feed(term, "data\x1b\x1b]7717;n;E;0\x07tail\r\n")
    assert seen == ["E"]
    assert lines(screen) == ["datatail"]
    feed(term, "one\x1b\r\ntwo\r\n")              # ESC, then a newline
    assert lines(screen)[1] == "one"
    assert len(lines(screen)) == 3               # the newline was not lost


def test_resize_during_a_full_screen_program_keeps_the_main_cursor():
    for first, second in [((12, 6), (30, 4)), ((60, 6), (20, 6)),
                          ((20, 3), (20, 9))]:
        term, screen, _ = make(cols=20, rows=6)
        long = "=" * 50
        feed(term, f"1:{long}\r\n2:{long}\r\n3:{long}\r\n")
        feed(term, "\x1b[?1049h\x1b[2J\x1b[Hpager text")
        term.resize(*first)
        feed(term, "\x1b[H\x1b[2Jredrawn")
        term.resize(*second)
        feed(term, "\x1b[?1049lAFTER\r\n")
        assert lines(screen) == [f"1:{long}", f"2:{long}", f"3:{long}",
                                 "AFTER"], (first, second)


def test_erasing_cancels_a_pending_wrap_and_wrapped_rows_are_full():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "x" * 10 + "\x1b[2K" + "y")
    assert [r.wrapped for r in screen.grid] == [False]
    assert lines(screen) == [" " * 9 + "y"]
    # Whatever leads up to a wrap, the row that wraps is a full row.
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "x" * 10 + "\x1b[1K" + "日本語日本語" + "z")
    for row in screen.grid:
        assert not row.wrapped or len(row.cells) == 10


def test_a_pending_wrap_survives_resizing():
    term, screen, _ = make(cols=10, rows=5)
    feed(term, "0123456789")
    screen.resize(10, 8)                         # rows only
    feed(term, "X")
    assert lines(screen) == ["0123456789X"]
    assert [r.text() for r in screen.grid[:2]] == ["0123456789", "X"]
    term, screen, _ = make(cols=10, rows=5)
    feed(term, "0123456789")
    screen.resize(5, 5)                          # now exactly two full rows
    feed(term, "X")
    assert [r.text() for r in screen.grid[:3]] == ["01234", "56789", "X"]
    term, screen, _ = make(cols=10, rows=5)
    feed(term, "0123456789")
    screen.resize(14, 5)                         # room again
    feed(term, "X")
    assert [r.text() for r in screen.grid] == ["0123456789X"]


def test_without_autowrap_the_last_column_takes_the_last_character():
    term, screen, _ = make(cols=4, rows=3)
    feed(term, "\x1b[?7labcdef")
    assert screen.grid[0].text() == "abcf"
    assert len(screen.grid) == 1


# -- the line store -----------------------------------------------------------

def test_the_store_counts_and_indexes_its_lines():
    store = LineStore()
    assert len(store) == 0 and store.end == 0
    store.append("one")
    store.append("two", ((3, 7),))
    store.append("three", None)
    assert len(store) == 3 and store.end == 3
    assert [store.text(i) for i in range(3)] == ["one", "two", "three"]
    assert [store.runs(i) for i in range(3)] == [None, ((3, 7),), None]
    assert store.styles == {1: ((3, 7),)}
    assert store.max_width == 5


def test_the_store_knows_the_width_of_lines_that_need_measuring():
    store = LineStore()
    store.append("abc")
    store.append("café→")                # one cell a character all the same
    assert store.complex == {} and store.max_width == 5
    store.append("日本")
    store.append("a\tb")
    store.append("e\u0301")
    assert store.complex == {2: 4, 3: 9, 4: 1}
    assert store.max_width == 9
    store.append("ab")
    assert store.max_width == 9


def test_plain_lines_can_be_appended_in_one_go():
    store = LineStore()
    store.append("12345678")
    store.extend_plain(["ab", "abcd"])
    assert store.texts == ["12345678", "ab", "abcd"]
    assert store.max_width == 8
    store.extend_plain(["x" * 12, ""])
    assert (store.max_width, store.end) == (12, 5)
    assert store.styles == {} and store.complex == {}


def test_dropping_nothing_from_the_front_changes_nothing():
    store = LineStore()
    store.drop_front(3)                  # there is nothing to drop
    assert (store.base, store.dropped, store.texts) == (0, 0, [])
    store.append("a", ((1, 5),))
    store.drop_front(0)
    store.drop_front(-2)
    assert (store.base, store.dropped, store.texts) == (0, 0, ["a"])
    assert store.styles == {0: ((1, 5),)}


@pytest.mark.parametrize("styled", [(1, 7), (0, 1, 2, 3, 4, 5, 6, 7)])
def test_dropping_from_the_front_forgets_what_those_lines_had(styled):
    store = LineStore()
    for i in range(10):
        text = "日本" if i in (2, 8) else f"line{i}"
        store.append(text, ((2, 5),) if i in styled else None)
    store.drop_front(5)
    assert (store.base, store.end, store.dropped, len(store)) == \
        (5, 10, 5, 5)
    assert store.texts == ["line5", "line6", "line7", "日本", "line9"]
    assert store.styles == {i: ((2, 5),) for i in styled if i >= 5}
    assert store.complex == {8: 4}
    assert store.text(7) == "line7" and store.runs(7) == ((2, 5),)
    store.drop_front(100)                # more than there is
    assert (store.base, store.end, store.dropped, len(store)) == \
        (10, 10, 10, 0)
    assert store.styles == {} and store.complex == {}


def test_taking_every_line_empties_the_store_but_not_its_count():
    store = LineStore()
    store.append("zero")
    store.append("one", ((1, 3),))
    store.append("日", None)
    store.drop_front(1)
    assert store.take_all() == [("one", ((1, 3),)), ("日", None)]
    assert (store.base, store.end, len(store)) == (3, 3, 0)
    assert store.styles == {} and store.complex == {}
    assert store.max_width == 0
    assert store.take_all() == []
    store.append("next")
    assert store.text(3) == "next" and store.end == 4


def test_a_line_scrolling_off_in_parts_stays_open_until_its_last_row():
    term, screen, store = make(cols=5, rows=2)
    assert store.take_open() is None
    assert store.open_text() == "" and store.open_line_runs() is None
    feed(term, "\x1b[31mabcde\x1b[0mfghijkl")
    # "abcde" has scrolled off; "fghij" and "kl" are still on the grid.
    red = style.intern(1, None, 0)
    assert store.texts == [] and store.open_parts == ["abcde"]
    assert store.open_len == 5
    assert store.open_text() == "abcde"
    assert store.open_line_runs() == ((5, red),)
    assert lines(screen) == ["abcdefghijkl"]
    assert screen.all_lines() == [("abcdefghijkl", ((5, red),))]
    assert store.take_open() == ("abcde", ((5, red),))
    assert store.open_parts is None and store.open_runs == []
    assert store.open_len == 0
    assert store.open_text() == ""
    assert store.take_open() is None


def test_the_parts_of_an_open_line_keep_their_styles_apart():
    term, screen, store = make(cols=4, rows=2)
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    # Four rows: default, red, half default and half red, green.
    feed(term, "abcd\x1b[31mefgh\x1b[0mij\x1b[31mkl\x1b[32mmnop\x1b[0mq")
    assert store.open_parts == ["abcd", "efgh", "ijkl"]
    assert store.open_line_runs() == ((4, 0), (8, red), (10, 0), (12, red))
    whole = ("abcdefghijklmnopq", ((4, 0), (8, red), (10, 0), (12, red),
                                   (16, green)))
    assert screen.all_lines() == [whole]
    feed(term, "\r\n")                          # "mnop" goes; "q" is left
    assert store.texts == []
    assert store.open_parts == ["abcd", "efgh", "ijkl", "mnop"]
    assert screen.all_lines() == [whole]
    feed(term, "\r\n")
    assert store.texts == ["abcdefghijklmnopq"] and store.open_parts is None
    assert store.runs(0) == whole[1]
    assert screen.all_lines() == [whole, ("", None)]


def test_clearing_the_store_drops_an_open_line_too():
    term, screen, store = make(cols=5, rows=2)
    feed(term, "abcdefghijkl\x1b[3J")
    assert store.open_parts is None and store.texts == []
    assert (store.open_len, store.open_runs) == (0, [])
    assert lines(screen) == ["fghijkl"]


# -- rows ---------------------------------------------------------------------

def test_style_runs_count_characters_not_cells():
    term, screen, _ = make(cols=20)
    feed(term, "\x1b[31m日本\x1b[0mab\x1b[32m\tc\x1b[0m  \r\n")
    red = style.intern(1, None, 0)
    green = style.intern(2, None, 0)
    assert screen.all_lines() == [("日本ab\tc", ((2, red), (5, 0),
                                                (6, green)))]


def test_a_row_of_default_blanks_has_no_text_and_no_runs():
    term, screen, _ = make(cols=20)
    feed(term, "\x1b[31mab\x1b[0m\r  \r\n")
    assert screen.grid[0].styles == [0, 0]
    assert screen.all_lines() == [("", None)]


def test_blanks_that_show_are_not_blank():
    for params, blank in [("31", True), ("1", True), ("2", True),
                          ("3", True), ("5", True), ("8", True),
                          ("4", False), ("7", False), ("9", False),
                          ("41", False), ("48;5;0", False)]:
        term, screen, _ = make()
        assert screen.grid[0].is_blank()
        feed(term, f"\x1b[{params}m   ")
        assert screen.grid[0].is_blank() is blank, params
        feed(term, "x")
        assert not screen.grid[0].is_blank()


def test_a_screen_is_at_least_one_cell():
    screen = Screen(0, -3)
    assert (screen.cols, screen.rows, screen.bot) == (1, 1, 0)
    screen.resize(0, 0)
    assert (screen.cols, screen.rows) == (1, 1)
    screen.resize(5, 0)
    assert (screen.cols, screen.rows, screen.bot) == (5, 1, 0)


def test_the_unused_cell_before_a_wrapped_wide_character():
    term, screen, _ = make(cols=5, rows=3)
    feed(term, "abcd日")
    assert screen.grid[0].cells == ["a", "b", "c", "d", ""]
    assert screen.grid[0].wrapped
    assert lines(screen) == ["abcd日"]
    feed(term, "\x1b[1;4H\x1b[P")              # delete the "d"
    assert screen.grid[0].cells == ["a", "b", "c", " "]
    assert lines(screen) == ["abc", "日"]


def test_a_wide_character_needs_two_columns():
    screen = Screen(1, 3, LineStore())
    term = Terminal(screen)
    feed(term, "日a")
    assert lines(screen) == ["a"]


# -- insert mode --------------------------------------------------------------

def test_insert_mode_pushes_the_rest_of_the_row_to_the_right():
    term, screen, _ = make(cols=10)
    feed(term, "abc\r\x1b[4hXY")
    assert screen.insert is True
    assert screen.grid[0].text() == "XYabc" and screen.cx == 2
    feed(term, "\x1b[4lZ")
    assert screen.insert is False
    assert screen.grid[0].text() == "XYZbc"


def test_insert_mode_drops_what_is_pushed_past_the_right_margin():
    term, screen, _ = make(cols=5)
    feed(term, "abcde\r\x1b[4hXY")
    assert screen.grid[0].cells == ["X", "Y", "a", "b", "c"]
    assert len(screen.grid) == 1


def test_insert_mode_with_wide_characters():
    term, screen, _ = make(cols=10)
    feed(term, "abc\r\x1b[4h日")
    assert screen.grid[0].cells == ["日", "", "a", "b", "c"]
    assert screen.cx == 2


def test_a_wide_character_pushed_half_off_the_row_is_blanked():
    term, screen, _ = make(cols=6)
    feed(term, "ab日本\r\x1b[4hX")
    assert screen.grid[0].cells == ["X", "a", "b", "日", "", " "]
    term, screen, _ = make(cols=6)
    feed(term, "a日本\r\x1b[4hX")              # this one still fits whole
    assert screen.grid[0].cells == ["X", "a", "日", "", "本", ""]


def test_a_tab_pushed_partly_off_the_row_is_still_a_tab():
    term, screen, _ = make(cols=10)
    feed(term, "\tZ\r\x1b[3@")
    assert screen.grid[0].cells == [" ", " ", " ", "\t", "", "", "", "",
                                    "", ""]
    assert lines(screen) == ["   \t"]


def test_inserting_blanks_beyond_the_text_changes_nothing():
    term, screen, _ = make(cols=10)
    feed(term, "ab\x1b[6G\x1b[2@")
    assert screen.grid[0].cells == ["a", "b"]
    blue = style.intern(None, 4, 0)
    feed(term, "\x1b[44m\x1b[2@")               # unless they have a color
    assert screen.grid[0].cells == ["a", "b", " ", " ", " ", " ", " "]
    assert screen.grid[0].styles == [0, 0, 0, 0, 0, blue, blue]


def test_inserting_blanks_into_a_styled_row_and_up_to_the_margin():
    term, screen, _ = make(cols=5)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31mab\x1b[0m\r\x1b[@")
    assert screen.grid[0].cells == [" ", "a", "b"]
    assert screen.grid[0].styles == [0, red, red]
    feed(term, "\x1b[99@")
    assert screen.grid[0].cells == [" "] * 5
    assert screen.grid[0].styles == [0] * 5
    feed(term, "abcde\x1b[@")                   # cancels the pending wrap
    assert not screen.wrap_pending
    assert screen.grid[0].cells == ["a", "b", "c", "d", " "]


# -- deleting and erasing -----------------------------------------------------

def test_deleting_characters():
    term, screen, _ = make(cols=10)
    feed(term, "ab\x1b[6G\x1b[P")               # beyond the text: nothing
    assert screen.grid[0].cells == ["a", "b"]
    feed(term, "\rabcdef\x1b[2G\x1b[99P")       # more than there is
    assert screen.grid[0].cells == ["a"]
    blue = style.intern(None, 4, 0)
    feed(term, "\rabcd\x1b[2G\x1b[44m\x1b[2P")  # the gap takes the color
    assert screen.grid[0].cells == ["a", "d"] + [" "] * 8
    assert screen.grid[0].styles == [0, 0] + [blue] * 8


def test_deleting_half_a_wide_character_leaves_a_blank_for_the_other():
    term, screen, _ = make(cols=10)
    feed(term, "日本\x1b[2G\x1b[P")             # on the second half
    assert screen.grid[0].cells == [" ", "本", ""]
    term, screen, _ = make(cols=10)
    feed(term, "a日b\x1b[2G\x1b[P")             # the first half
    assert screen.grid[0].cells == ["a", " ", "b"]


def test_erasing_to_the_cursor():
    term, screen, _ = make(cols=20)
    feed(term, "abcdef\x1b[3G\x1b[1K")
    assert screen.grid[0].text() == "   def"
    feed(term, "\x1b[10G\x1b[1K")               # beyond the text
    assert screen.grid[0].cells == [" "] * 6
    blue = style.intern(None, 4, 0)
    feed(term, "\x1b[44m\x1b[1K")               # a color reaches the cursor
    assert screen.grid[0].cells == [" "] * 10
    assert screen.grid[0].styles == [blue] * 10
    feed(term, "\x1b[20G\x1b[1K")               # and stops at the margin
    assert screen.grid[0].cells == [" "] * 20


def test_erasing_a_number_of_characters():
    term, screen, _ = make(cols=10)
    feed(term, "abcdef\x1b[2G\x1b[3X")
    assert screen.grid[0].cells == ["a", " ", " ", " ", "e", "f"]
    assert screen.cx == 1
    feed(term, "\x1b[5G\x1b[99X")               # no further than the text
    assert screen.grid[0].cells == ["a", " ", " ", " ", " ", " "]
    blue = style.intern(None, 4, 0)
    feed(term, "\x1b[44m\x1b[99X")              # a color goes to the margin
    assert screen.grid[0].cells == ["a"] + [" "] * 9
    assert screen.grid[0].styles == [0] * 4 + [blue] * 6


def test_erasing_a_line_from_inside_a_wide_character():
    term, screen, _ = make(cols=10)
    feed(term, "日本\x1b[2G\x1b[K")
    assert screen.grid[0].cells == [" "]


def test_erasing_a_whole_line_with_a_color():
    term, screen, _ = make(cols=4)
    blue = style.intern(None, 4, 0)
    feed(term, "abcd\x1b[44m\x1b[2K")
    assert screen.grid[0].cells == [" "] * 4
    assert screen.grid[0].styles == [blue] * 4
    assert not screen.wrap_pending


def test_erasing_below_with_a_color_paints_the_rest_of_the_screen():
    term, screen, _ = make(cols=4, rows=3)
    blue = style.intern(None, 4, 0)
    feed(term, "ab\r\ncd\x1b[1;2H\x1b[44m\x1b[J")
    assert [r.text() for r in screen.grid] == ["a   ", "    ", "    "]
    assert [r.styles for r in screen.grid] == [
        [0, blue, blue, blue], [blue] * 4, [blue] * 4]


def test_erasing_above_the_cursor():
    term, screen, _ = make(cols=6, rows=4)
    feed(term, "one\r\ntwo\r\nthree\x1b[2;2H\x1b[1J")
    assert [r.text() for r in screen.grid] == ["", "  o", "three"]
    assert (screen.cy, screen.cx) == (1, 1)


def test_erasing_the_whole_screen_with_a_color():
    term, screen, _ = make(cols=3, rows=3)
    blue = style.intern(None, 4, 0)
    feed(term, "a\r\nb\x1b[44m\x1b[2J")
    assert [r.cells for r in screen.grid] == [[" "] * 3] * 3
    assert [r.styles for r in screen.grid] == [[blue] * 3] * 3
    assert (screen.cy, screen.clear_count) == (1, 1)


def test_an_unknown_kind_of_erasing_erases_nothing():
    term, screen, _ = make()
    feed(term, "abc\x1b[2G\x1b[7J\x1b[7K")
    assert screen.grid[0].text() == "abc"
    assert screen.clear_count == 0


def test_erasing_the_scrollback_of_a_screen_that_keeps_none():
    term, screen, _ = make()
    feed(term, "main\r\n\x1b[?1049hfull\x1b[3J")
    alt = term.screen
    assert alt.store is None
    assert alt.clear_count == 1
    assert alt.grid[0].text() == "full"         # the screen itself stays


def test_erasing_the_scrollback_keeps_what_is_on_the_screen():
    term, screen, store = make(rows=2)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[3J")
    assert store.texts == [] and store.base == 2
    assert [r.text() for r in screen.grid] == ["c", "d"]
    assert screen.clear_count == 1
    assert lines(screen) == ["c", "d"]


# -- lines, scrolling and the scroll region -----------------------------------

def test_a_linefeed_keeps_the_column_unless_newline_mode_is_on():
    term, screen, _ = make()
    feed(term, "ab\ncd\x0bef\x0cgh")
    assert [r.text() for r in screen.grid] == ["ab", "  cd", "    ef",
                                               "      gh"]
    term, screen, _ = make()
    feed(term, "\x1b[20hab\ncd")
    assert screen.newline_mode is True
    assert [r.text() for r in screen.grid] == ["ab", "cd"]
    feed(term, "\x1b[20l\nef")
    assert screen.newline_mode is False
    assert [r.text() for r in screen.grid] == ["ab", "cd", "  ef"]


def test_index_and_next_line():
    term, screen, _ = make(cols=10, rows=3)
    feed(term, "0123456789\x1bDa")              # down, same column, no wrap
    assert [r.text() for r in screen.grid] == ["0123456789", "         a"]
    assert not screen.grid[0].wrapped
    feed(term, "\x1bEb")                        # down, to the first column
    assert screen.grid[2].text() == "b"
    feed(term, "\x1bDc")                        # at the bottom: scroll
    assert [r.text() for r in screen.grid] == ["         a", "b", " c"]


def test_reverse_index_above_the_scroll_region_stops_at_the_top_row():
    term, screen, _ = make(rows=5)
    feed(term, "a\r\nb\r\nc\x1b[2;4r\x1bM\x1bM")
    assert screen.cy == 0
    assert [r.text() for r in screen.grid] == ["a", "b", "c"]


def test_reverse_index_inside_the_scroll_region():
    term, screen, _ = make(rows=5)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[2;4r\x1b[4;1H\x1bM")
    assert screen.cy == 2                       # just up
    feed(term, "\x1bM\x1bM")                    # at its top: it scrolls
    assert screen.cy == 1
    assert [r.text() for r in screen.grid[:4]] == ["a", "", "b", "c"]


def test_cursor_up_and_down_stop_at_the_scroll_region():
    term, screen, _ = make(rows=8)
    feed(term, "\x1b[3;6r")                     # rows 3 to 6
    feed(term, "\x1b[5;1H\x1b[9A")
    assert screen.cy == 2
    feed(term, "\x1b[9B")
    assert screen.cy == 5
    feed(term, "\x1b[2;1H\x1b[9A")              # above the region
    assert screen.cy == 0
    feed(term, "\x1b[9B")                       # coming from above
    assert screen.cy == 5
    feed(term, "\x1b[7;1H\x1b[9B")              # below the region
    assert screen.cy == 7 and len(screen.grid) == 8
    feed(term, "\x1b[9A")                       # coming from below
    assert screen.cy == 2
    feed(term, "\x1b[4;1H\x1b[A\x1b[B\x1b[B")   # one row is the default
    assert screen.cy == 4


def test_cursor_movements_by_column_and_row():
    term, screen, _ = make(cols=10, rows=6)
    feed(term, "\x1b[3C")
    assert screen.cx == 3
    feed(term, "\x1b[99C")
    assert screen.cx == 9
    feed(term, "\x1b[2D")
    assert screen.cx == 7
    feed(term, "\x1b[99D")
    assert screen.cx == 0
    feed(term, "\x1b[4a\x1b[3e")                # HPR and VPR
    assert (screen.cy, screen.cx) == (3, 4)
    feed(term, "\x1b[2`\x1b[2d")                # HPA and VPA
    assert (screen.cy, screen.cx) == (1, 1)
    feed(term, "\x1b[99G\x1b[99d")
    assert (screen.cy, screen.cx) == (5, 9)
    feed(term, "\x1b[2F")                       # up, to the first column
    assert (screen.cy, screen.cx) == (3, 0)
    feed(term, "\x1b[5G\x1b[E")                 # down, to the first column
    assert (screen.cy, screen.cx) == (4, 0)
    feed(term, "\x1b[3;4f")
    assert (screen.cy, screen.cx) == (2, 3)
    feed(term, "\x1b[H")
    assert (screen.cy, screen.cx) == (0, 0)
    feed(term, "\x1b[99;99H")
    assert (screen.cy, screen.cx) == (5, 9)


def test_moving_the_cursor_cancels_a_pending_wrap():
    for move in ["\x1b[D", "\x1b[C", "\x1b[A", "\x1b[B", "\x1b[10G",
                 "\x1b[1d", "\x08", "\r", "\x1b[Z"]:
        term, screen, _ = make(cols=10, rows=3)
        feed(term, "\r\n0123456789")
        assert screen.wrap_pending
        feed(term, move)
        assert not screen.wrap_pending, repr(move)
        feed(term, "X")
        assert len(screen.grid) <= 3 and not screen.grid[1].wrapped


def test_backspace_stops_at_the_first_column():
    term, screen, _ = make()
    feed(term, "ab\x08\x08\x08X")
    assert screen.grid[0].text() == "Xb" and screen.cx == 1


def test_origin_mode_counts_rows_from_the_scroll_region():
    term, screen, _ = make(cols=20, rows=8)
    feed(term, "\x1b[3;6r\x1b[?6h")
    assert screen.origin is True
    assert (screen.cy, screen.cx) == (2, 0)
    feed(term, "\x1b[2;5H\x1b[6n")
    assert (screen.cy, screen.cx) == (3, 4)
    assert term.replies == [b"\x1b[2;5R"]
    feed(term, "\x1b[99;1H")
    assert screen.cy == 5                       # held inside the region
    feed(term, "\x1b[?6l")
    assert screen.origin is False
    assert (screen.cy, screen.cx) == (0, 0)
    feed(term, "\x1b[4;2H\x1b[6n")
    assert term.replies[-1] == b"\x1b[4;2R"


def test_margins_that_make_no_region_mean_the_whole_screen():
    term, screen, _ = make(rows=6)
    feed(term, "\x1b[2;4r")
    assert (screen.top, screen.bot) == (1, 3)
    feed(term, "x\x1b[4;4r")                    # one row is no region
    assert (screen.top, screen.bot) == (0, 5)
    assert (screen.cy, screen.cx) == (0, 0)     # and the cursor goes home
    feed(term, "\x1b[2;4r\x1b[5;3r")            # upside down
    assert (screen.top, screen.bot) == (0, 5)
    feed(term, "\x1b[3r")                       # to the bottom by default
    assert (screen.top, screen.bot) == (2, 5)
    feed(term, "\x1b[;3r")                      # from the top by default
    assert (screen.top, screen.bot) == (0, 2)
    feed(term, "\x1b[2;99r")                    # no further than the screen
    assert (screen.top, screen.bot) == (1, 5)
    feed(term, "\x1b[r")
    assert (screen.top, screen.bot) == (0, 5)


def test_inserting_and_deleting_lines_outside_the_region_does_nothing():
    term, screen, _ = make(rows=5)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\x1b[2;3r\x1b[5;2H\x1b[L\x1b[M")
    assert [r.text() for r in screen.grid] == ["a", "b", "c", "d", "e"]
    assert screen.cx == 1
    feed(term, "\x1b[1;2H\x1b[L\x1b[M")
    assert [r.text() for r in screen.grid] == ["a", "b", "c", "d", "e"]


def test_inserting_and_deleting_lines_stays_inside_the_region():
    term, screen, _ = make(rows=5)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\x1b[2;4r\x1b[2;3H\x1b[99L")
    assert [r.text() for r in screen.grid] == ["a", "", "", "", "e"]
    assert screen.cx == 0
    feed(term, "\x1b[2;1Hb\x1b[3;1Hc\x1b[4;1Hd\x1b[3;3H\x1b[M")
    assert [r.text() for r in screen.grid] == ["a", "b", "d", "", "e"]
    assert screen.cx == 0
    feed(term, "\x1b[2;1H\x1b[99M")
    assert [r.text() for r in screen.grid] == ["a", "", "", "", "e"]


def test_scrolling_by_command():
    term, screen, store = make(rows=3)
    feed(term, "a\r\nb\r\nc\x1b[S")
    assert store.texts == ["a"]
    assert [r.text() for r in screen.grid] == ["b", "c", ""]
    feed(term, "\x1b[T")
    assert [r.text() for r in screen.grid] == ["", "b", "c"]
    feed(term, "\x1b[2T")
    assert [r.text() for r in screen.grid] == ["", "", ""]
    feed(term, "\x1b[1;2;3;4;5T")               # this one is no scrolling
    assert [r.text() for r in screen.grid] == ["", "", ""]
    assert screen.cy == 2


def test_scrolling_more_than_the_screen_holds_scrolls_it_all_out():
    term, screen, store = make(rows=3)
    feed(term, "a\r\nb\r\nc\x1b[99S")
    assert store.texts == ["a", "b", "c"]
    assert [r.text() for r in screen.grid] == ["", "", ""]


def test_scrolling_a_region_keeps_its_rows_out_of_the_store():
    term, screen, store = make(rows=4)
    blue = style.intern(None, 4, 0)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[2;3r\x1b[44m\x1b[S")
    assert store.texts == []
    assert [r.text() for r in screen.grid] == ["a", "c", " " * 20, "d"]
    assert screen.grid[2].styles == [blue] * 20
    feed(term, "\x1b[2T")
    assert [r.text() for r in screen.grid] == ["a", " " * 20, " " * 20, "d"]


def test_the_alignment_test_fills_the_screen():
    term, screen, _ = make(cols=4, rows=3)
    feed(term, "ab\r\ncd\x1b#8")
    assert [r.text() for r in screen.grid] == ["EEEE"] * 3
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 0, False)
    feed(term, "\x1b#3x")                       # another one: nothing
    assert screen.grid[0].text() == "xEEE"


# -- tabs ---------------------------------------------------------------------

def test_a_tab_over_text_only_moves_the_cursor():
    term, screen, _ = make(cols=20)
    feed(term, "abcdefgh\r\tX")
    assert screen.grid[0].cells == list("abcdefghX")


def test_several_tabs_at_once():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[2Ix\x1b[Iy")
    assert lines(screen) == ["\t\tx\ty"]
    assert (screen.grid[0].cells[16], screen.grid[0].cells[24]) == \
        ("x", "y")


def test_a_tab_keeps_the_styles_of_the_row_in_step():
    term, screen, _ = make(cols=20)
    red = style.intern(1, None, 0)
    feed(term, "\x1b[31ma\x1b[0m\tb")
    assert screen.grid[0].cells == ["a", "\t", "", "", "", "", "", "", "b"]
    assert screen.grid[0].styles == [red, 0, 0, 0, 0, 0, 0, 0, 0]


def test_a_tab_in_a_single_column():
    screen = Screen(1, 2, LineStore())
    term = Terminal(screen)
    feed(term, "\t")
    assert screen.grid[0].cells == []
    feed(term, "a\t")
    assert lines(screen) == ["a\t"]


def test_a_tab_at_the_margin_joins_what_is_under_the_cursor():
    term, screen, _ = make(cols=4)
    feed(term, "ab日\t")
    assert screen.grid[0].cells == ["a", "b", "日\t", ""]
    assert lines(screen) == ["ab日\t"]
    term, screen, _ = make(cols=4)
    feed(term, "\x1b[4G\t\tx")                  # nothing written there yet
    assert screen.grid[0].cells == [" ", " ", " \t\t", "x"]


def test_a_tab_ending_on_the_unused_cell_of_a_wrapped_row():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "\x1b[10G日")                    # too wide for the last column
    assert screen.grid[0].cells == [" "] * 9 + [""]
    assert screen.grid[0].wrapped
    feed(term, "\x1b[1;9H\t")
    assert screen.grid[0].cells == [" "] * 8 + ["\t", ""]    # still unused
    assert screen.cx == 9
    assert lines(screen) == [" " * 8 + "\t日"]


def test_a_tab_starting_inside_a_tab_leaves_the_first_one_alone():
    term, screen, _ = make(cols=20)
    feed(term, "\tb\x1b[3G\tc")
    assert screen.grid[0].cells == ["\t", "", "", "", "", "", "", "", "c"]
    assert lines(screen) == ["\tc"]


def test_back_tab():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[20G\x1b[Z")
    assert screen.cx == 16
    feed(term, "\x1b[Z")
    assert screen.cx == 8
    feed(term, "\x1b[3Z")
    assert screen.cx == 0
    feed(term, "\x1b[30G\x1b[2Z")
    assert screen.cx == 16
    feed(term, "\x1b[18G\x1b[Z")
    assert screen.cx == 16


def test_tab_stops_can_be_set_and_cleared():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[5G\x1bH\r\tA")             # a stop at column 5
    assert screen.cx == 5 and screen.grid[0].cells[4] == "A"
    feed(term, "\tB")                           # the usual stops remain
    assert screen.grid[0].cells[8] == "B"
    feed(term, "\x1b[Z\x1b[Z")                  # and back over them
    assert screen.cx == 4
    feed(term, "\x1b[9G\x1b[g\r\x1b[2K\t\tC")   # column 9 is none any more
    assert screen.grid[0].cells[16] == "C"
    feed(term, "\x1b[20G\x1b[2Z")
    assert screen.cx == 4
    feed(term, "\x1b[Z")
    assert screen.cx == 0
    feed(term, "\x1b[3g\r\x1b[2K\tD")           # no stops at all
    assert screen.grid[0].cells[39] == "D"
    assert lines(screen) == ["\tD"]
    feed(term, "\x1b[Z")
    assert screen.cx == 0


def test_setting_a_second_tab_stop_keeps_the_first():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[3G\x1bH\x1b[6G\x1bH\r\ta\tb\tc")
    assert [screen.grid[0].cells[i] for i in (2, 5, 8)] == ["a", "b", "c"]


def test_clearing_a_tab_stop_before_any_was_set():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[9G\x1b[g\r\tX")
    assert screen.grid[0].cells[16] == "X"
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[9G\x1b[5g\r\tX")           # not a way to clear
    assert screen.grid[0].cells[8] == "X"


def test_tab_stops_beyond_a_narrower_screen_are_forgotten():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[5G\x1bH\x1b[31G\x1bH")
    assert screen.tabs == {4, 8, 16, 24, 30, 32}
    screen.resize(20, 5)
    assert screen.tabs == {4, 8, 16}
    screen.resize(17, 5)
    assert screen.tabs == {4, 8, 16}
    screen.resize(16, 5)
    assert screen.tabs == {4, 8}


# -- characters at the margin -------------------------------------------------

def test_a_wrap_left_pending_when_autowrap_goes_off_never_happens():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "0123456789\x1b7\x1b[?7l\x1b8")
    assert screen.wrap_pending and not screen.autowrap
    feed(term, "XY")
    assert [r.text() for r in screen.grid] == ["012345678Y"]
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 9, False)
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "0123456789\x1b7\x1b[?7l\x1b8日")
    assert screen.grid[0].cells == list("01234567") + ["日", ""]
    assert len(screen.grid) == 1
    assert (screen.cy, screen.cx, screen.wrap_pending) == (0, 9, False)


def test_switching_autowrap_off_cancels_a_pending_wrap():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "0123456789\x1b[?7lX")
    assert [r.text() for r in screen.grid] == ["012345678X"]
    feed(term, "\x1b[?7hYZ")
    assert screen.autowrap is True
    assert [r.text() for r in screen.grid] == ["012345678Y", "Z"]


def test_without_autowrap_wide_characters_pile_up_in_the_last_two_columns():
    term, screen, _ = make(cols=6, rows=3)
    feed(term, "\x1b[?7labcd日本語")
    assert screen.grid[0].cells == ["a", "b", "c", "d", "語", ""]
    assert len(screen.grid) == 1


def test_a_combining_mark_without_a_character_to_join_is_dropped():
    term, screen, _ = make(cols=10)
    feed(term, "\u0301")
    assert screen.grid[0].cells == [] and screen.cx == 0
    feed(term, "\t\u0301x")
    assert lines(screen) == ["\tx"]


def test_a_combining_mark_joins_the_character_before_the_cursor():
    term, screen, _ = make(cols=10)
    feed(term, "日\u0301")
    assert screen.grid[0].cells == ["日\u0301", ""]
    feed(term, "\r\nab\x1b[5C\u0301")           # the last one there is
    assert screen.grid[1].cells == ["a", "b\u0301"]
    feed(term, "\r\n0123456789\u0301")           # with a wrap pending
    assert screen.grid[2].cells[8:] == ["8", "9\u0301"]


def test_repeating_the_last_character():
    term, screen, _ = make(cols=20)
    feed(term, "\x1b[3b")                       # nothing to repeat yet
    assert screen.grid[0].cells == []
    feed(term, "ab\x1b[3b\x1b[b")
    assert screen.grid[0].text() == "abbbbb"
    feed(term, "日\x1b[2b")
    assert screen.grid[0].text() == "abbbbb日日日"
    term, screen, _ = make(cols=4, rows=2)
    feed(term, "a\x1b[999b")                    # no more than a screenful
    assert lines(screen) == ["a" * 9]


def test_the_second_character_set():
    term, screen, _ = make()
    feed(term, "\x1b)0q\x0eq\x0fq\x1b(0q\x1b(Bq")
    assert screen.grid[0].text() == "q─q─q"
    screen.designate_charset(2, "0")            # there are two sets only
    feed(term, "q")
    assert screen.grid[0].text() == "q─q─qq"


def test_escapes_that_take_one_more_character():
    term, screen, _ = make()
    feed(term, "a\x1b%Gb\x1b Fc\x1b*0d\x1b+0e\x1b-Af\x1b.Ag\x1b/Ah")
    assert screen.grid[0].text() == "abcdefgh"
    feed(term, "\x1b(\r\x1b)\nX")               # cut short by a control
    assert [r.text() for r in screen.grid] == ["abcdefgh", "X"]


# -- attributes ---------------------------------------------------------------

def test_a_style_is_remembered_per_sequence_and_starting_style(monkeypatch):
    from jupyterminal.term import screen as screen_module
    cache: dict = {}
    monkeypatch.setattr(screen_module, "_sgr_cache", cache)
    term, screen, _ = make()
    feed(term, "\x1b[1m")
    bold = style.intern(None, None, style.BOLD)
    assert cache == {("1", 0): (None, None, style.BOLD, bold, 0)}
    feed(term, "\x1b[44m")
    both = style.intern(None, 4, style.BOLD)
    blue = style.intern(None, 4, 0)
    assert cache[("44", bold)] == (None, 4, style.BOLD, both, blue)
    assert (screen.sid, screen.bce) == (both, blue)
    feed(term, "\x1b[m")
    assert cache[("", both)] == (None, None, 0, 0, 0)
    assert (screen.fg, screen.bg, screen.attrs, screen.sid, screen.bce) == \
        (None, None, 0, 0, 0)
    assert len(cache) == 3
    feed(term, "\x1b[1m\x1b[44m\x1b[m")         # the same again: all known
    assert len(cache) == 3


def test_the_memory_of_styles_is_emptied_when_it_grows_too_large(
        monkeypatch):
    from jupyterminal.term import screen as screen_module
    cache = {(str(i), -1): None for i in range(20000)}
    monkeypatch.setattr(screen_module, "_sgr_cache", cache)
    term, screen, _ = make()
    feed(term, "\x1b[1m")                       # 20000 entries are fine
    assert len(cache) == 20001
    feed(term, "\x1b[3m")                       # one more is too many
    assert list(cache) == [("3", style.intern(None, None, style.BOLD))]
    assert screen.attrs == style.BOLD | style.ITALIC


def test_private_sequences_ending_in_m_are_not_styles():
    term, screen, _ = make()
    feed(term, "\x1b[>4;2m\x1b[?1m\x1b[<1m\x1b[=1mx")
    assert (screen.sid, screen.attrs) == (0, 0)
    assert screen.grid[0].styles is None


def test_save_and_restore_bring_back_style_and_character_set():
    term, screen, _ = make()
    red = style.intern(1, None, 0)
    feed(term, "ab\x1b[31m\x1b(0\x1b7\x1b[0m\x1b(B\r\nq\x1b8q")
    assert screen.grid[0].text() == "ab─" and screen.grid[1].text() == "q"
    assert screen.grid[0].styles == [0, 0, red]
    assert (screen.fg, screen.sid) == (1, red)
    feed(term, "\x1b[s\x1b[H\x1b[uq")           # the other way to say it
    assert screen.grid[0].text() == "ab──"


def test_restoring_a_cursor_that_was_never_saved_goes_home():
    term, screen, _ = make(rows=6)
    feed(term, "\x1b[2;4r\x1b[?6h\x1b[2;3Hx\x1b8")
    assert (screen.cy, screen.cx, screen.origin) == (0, 0, False)
    term, screen, _ = make(cols=10)
    feed(term, "0123456789\x1b8X")              # and cancels a pending wrap
    assert [r.text() for r in screen.grid] == ["X123456789"]


def test_a_saved_cursor_is_held_inside_a_smaller_screen():
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "\x1b[5;15H\x1b7\x1b[H")
    screen.resize(10, 3)
    feed(term, "\x1b8")
    assert (screen.cy, screen.cx) == (2, 9)


def test_a_soft_reset_puts_modes_back_but_leaves_the_screen():
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "text\x1b[31;44;1m\x1b[2;4r\x1b[4h\x1b[?6h\x1b[?7l\x1b[?25l"
               "\x1b(0\x1b7\x1b[3;3H")
    assert (screen.cy, screen.cx) == (3, 2)
    assert (screen.autowrap, screen.insert, screen.origin,
            screen.cursor_visible) == (False, True, True, False)
    feed(term, "\x1b[!p")
    assert (screen.fg, screen.bg, screen.attrs, screen.sid, screen.bce) == \
        (None, None, 0, 0, 0)
    assert (screen.top, screen.bot) == (0, 5)
    assert (screen.autowrap, screen.insert, screen.origin,
            screen.cursor_visible) == (True, False, False, True)
    assert screen.grid[0].text() == "text"
    assert (screen.cy, screen.cx) == (3, 2)
    feed(term, "q\x1b8")                        # plain letters, nothing saved
    assert screen.grid[3].cells == [" ", " ", "q"]
    assert (screen.cy, screen.cx) == (0, 0)
    feed(term, "\x1b[?25h")
    assert screen.cursor_visible is True


def test_a_full_reset():
    term, screen, store = make(cols=20, rows=3)
    events = []
    term.on_alt = events.append
    feed(term, "a\r\nb\r\nc\r\nd")              # "a" has scrolled off
    feed(term, "\x1b[?1h\x1b=\x1b[?1000h\x1b[?1006h\x1b[?1005h\x1b[?1004h"
               "\x1b[?2026h\x1b[?2004h\x1b[31m\x1b[4h\x1b[?1049h")
    assert term.alt_active
    feed(term, "\x1bc")
    assert not term.alt_active and term.screen is screen
    assert events == [True, False]
    assert (term.app_cursor, term.app_keypad, term.mouse_mode,
            term.mouse_sgr, term.mouse_utf8, term.focus_events,
            term.sync_output, term.bracketed_paste) == \
        (False, False, 0, False, False, False, False, False)
    assert [r.cells for r in screen.grid] == [[]]
    assert (screen.cx, screen.cy, screen.sid, screen.insert) == \
        (0, 0, 0, False)
    assert store.texts == [] and store.base == 1
    assert screen.clear_count == 1


def test_a_full_reset_of_a_screen_that_keeps_no_lines():
    screen = Screen(10, 3)
    term = Terminal(screen)
    feed(term, "abcdefghij\x1b[5 q\x1b[20h\x1b[3g\x1bc")
    assert [r.cells for r in screen.grid] == [[]]
    assert (screen.clear_count, screen.cursor_shape, screen.newline_mode,
            screen.tabs, screen.last_char, screen.wrap_pending) == \
        (1, 0, False, None, "", False)


# -- resizing -----------------------------------------------------------------

def test_resizing_to_the_same_size_changes_nothing():
    term, screen, _ = make(cols=10, rows=4)
    feed(term, "\x1b[2;3r0123456789\x1b7")
    grid = screen.grid
    saved = screen._saved
    screen.resize(10, 4)
    assert screen.grid is grid and screen._saved is saved
    assert (screen.top, screen.bot, screen.wrap_pending) == (1, 2, True)
    screen.resize(10, 5)                        # any change resets the region
    assert (screen.top, screen.bot, screen.wrap_pending) == (0, 4, True)


def test_a_saved_cursor_elsewhere_keeps_its_place_through_a_resize():
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\r\ne\r\nf\x1b[5;4H\x1b7\x1b[6;1H")
    screen.resize(20, 4)                        # two rows go off the top
    assert screen._saved[:3] == (3, 2, False)
    feed(term, "\x1b8X")
    assert [r.text() for r in screen.grid] == ["c", "d", "e  X", "f"]
    term, screen, _ = make(cols=20, rows=6)
    feed(term, "a\r\nb\r\nc\r\nd\x1b[2;15H\x1b7\x1b[4;1H")
    screen.resize(10, 2)                        # its own row goes as well
    assert screen._saved[:3] == (9, 0, False)
    feed(term, "\x1b8X")
    assert [r.text() for r in screen.grid] == ["c" + " " * 8 + "X", "d"]


def test_rewrapping_tabs_wide_characters_and_styles():
    term, screen, _ = make(cols=12, rows=6)
    red = style.intern(1, None, 0)
    feed(term, "ab\t\x1b[31m日本語\x1b[0mxyz")
    assert lines(screen) == ["ab\t日本語xyz"]
    screen.resize(11, 6)                        # 本 no longer fits on the row
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "\t", "", "", "", "", "", "日", "", ""],
        ["本", "", "語", "", "x", "y", "z"]]
    assert [r.wrapped for r in screen.grid] == [True, False]
    assert (screen.cy, screen.cx, screen.wrap_pending) == (1, 7, False)
    assert screen.all_lines() == [("ab\t日本語xyz", ((3, 0), (6, red)))]
    screen.resize(5, 6)                         # the tab ends at the margin
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "\t", "", ""],
        ["日", "", "本", "", ""],
        ["語", "", "x", "y", "z"]]
    assert [r.wrapped for r in screen.grid] == [True, True, False]
    assert (screen.cy, screen.cx, screen.wrap_pending) == (2, 4, True)
    assert screen.all_lines() == [("ab\t日本語xyz", ((3, 0), (6, red)))]
    screen.resize(40, 6)
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "\t", "", "", "", "", "", "日", "", "本", "", "語", "",
         "x", "y", "z"]]
    assert screen.grid[0].styles == [0] * 8 + [red] * 6 + [0] * 3
    assert (screen.cy, screen.cx) == (0, 17)


def test_rewrapping_keeps_the_cursor_where_it_was_in_the_text():
    term, screen, _ = make(cols=10, rows=5)
    feed(term, "0123456789abcde\x1b[1;4H")      # on the "3"
    screen.resize(4, 5)
    assert (screen.cy, screen.cx) == (0, 3)
    feed(term, "X")
    assert lines(screen) == ["012X456789abcde"]
    term, screen, _ = make(cols=10, rows=5)
    feed(term, "0123456789abcde\x1b[2;3H")      # on the "c"
    screen.resize(4, 5)
    assert (screen.cy, screen.cx) == (3, 0)
    feed(term, "X")
    assert lines(screen) == ["0123456789abXde"]


def test_rewrapping_keeps_a_cursor_beyond_the_text_beyond_it():
    term, screen, _ = make(cols=20, rows=5)
    feed(term, "abc\x1b[10G")
    screen.resize(30, 5)
    assert (screen.cy, screen.cx) == (0, 9)
    screen.resize(6, 5)
    assert (screen.cy, screen.cx) == (0, 5)


def test_rewrapping_a_line_whose_start_scrolled_off_keeps_its_styles():
    term, screen, store = make(cols=5, rows=2)
    red = style.intern(1, None, 0)
    feed(term, "ab\x1b[31mcde\u0301fgh\x1b[0mijkl")
    assert store.open_parts == ["abcde\u0301"]
    screen.resize(8, 2)
    assert store.open_parts is None             # it is on the grid again
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "c", "d", "e\u0301", "f", "g", "h"],
        ["i", "j", "k", "l"]]
    assert screen.grid[0].styles == [0, 0] + [red] * 6
    assert (screen.cy, screen.cx) == (1, 4)
    assert screen.all_lines() == [
        ("abcde\u0301fghijkl", ((2, 0), (9, red)))]
    term, screen, store = make(cols=5, rows=2)
    feed(term, "\x1b[31mab\x1b[0mcdefghijk")     # styled at its start only
    assert store.open_line_runs() == ((2, red),)
    screen.resize(8, 2)
    assert screen.grid[0].styles == [red, red, 0, 0, 0, 0, 0, 0]
    assert screen.all_lines() == [("abcdefghijk", ((2, red),))]


def test_clipping_a_row_cuts_wide_characters_and_tabs_cleanly():
    screen = Screen(12, 3, LineStore(), reflow=False)
    term = Terminal(screen)
    feed(term, "ab日本\r\na\tb\r\n日本語")
    screen.resize(5, 3)
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "日", "", " "],
        ["a", "\t", "", "", ""],
        ["日", "", "本", "", " "]]
    screen.resize(4, 3)
    assert [r.cells for r in screen.grid] == [
        ["a", "b", "日", ""],
        ["a", "\t", "", ""],
        ["日", "", "本", ""]]


# -- reading the screen -------------------------------------------------------

def test_how_much_of_the_grid_is_output():
    term, screen, _ = make(cols=10, rows=6)
    assert (screen.extent(), screen.content_rows()) == (1, 0)
    feed(term, "a")
    assert (screen.extent(), screen.content_rows()) == (1, 1)
    feed(term, "\r\n")                          # after the final newline
    assert (screen.extent(), screen.content_rows()) == (2, 1)
    feed(term, "\r\n\r\n")                      # blank lines printed count
    assert (screen.extent(), screen.content_rows()) == (4, 3)
    feed(term, "\x1b[2;1H")                     # rows the cursor left do not
    assert (screen.extent(), screen.content_rows()) == (2, 1)
    feed(term, "\x1b[5C")                       # away from the first column
    assert (screen.extent(), screen.content_rows()) == (2, 2)
    term, screen, _ = make(cols=10, rows=6)
    feed(term, "0123456789")                    # a full row, wrap pending
    assert (screen.extent(), screen.content_rows()) == (1, 1)
    feed(term, "\r\x1b[2K")
    assert screen.wrap_pending is False
    assert (screen.extent(), screen.content_rows()) == (1, 0)


def test_counting_the_complete_lines_above_the_cursor():
    term, screen, _ = make(cols=5, rows=3)
    assert screen.lines_above_cursor() == 0
    feed(term, "one\r\nabcdefgh\r\n")           # two lines on three rows
    assert screen.lines_above_cursor() == 2
    feed(term, "x\r\ny")                        # "one" is in the store now
    assert screen.lines_above_cursor() == 3
    bare = Screen(5, 3)
    Terminal(bare).feed(b"a\r\nb")
    assert bare.lines_above_cursor() == 1


def test_the_lines_of_the_first_rows_only():
    term, screen, _ = make(cols=5, rows=4)
    feed(term, "abcdefgh\r\nxy\r\nz")
    assert screen.live_lines() == [("abcdefgh", None), ("xy", None),
                                   ("z", None)]
    assert screen.live_lines(1) == [("abcde", None)]
    assert screen.live_lines(2) == [("abcdefgh", None)]
    assert screen.live_lines(0) == []
    assert screen.all_lines(2) == [("abcdefgh", None)]


def test_freezing_what_is_above_the_cursor():
    term, screen, store = make(cols=5, rows=4)
    feed(term, "\x1b[4;1H\x1b7\x1b[H")
    screen.flush_above_cursor()                 # nothing is above it
    assert store.texts == [] and screen._saved[1] == 3
    feed(term, "one\r\nabcdefgh\r\ntail")
    screen.flush_above_cursor()
    assert store.texts == ["one", "abcdefgh"]
    assert [r.text() for r in screen.grid] == ["tail"]
    assert (screen.cy, screen.cx) == (0, 4)
    assert screen._saved[1] == 0                # it moved up along
    bare = Screen(5, 4)
    Terminal(bare).feed(b"a\r\nb\r\nc")
    bare.flush_above_cursor()                   # with nowhere to keep them
    assert [r.text() for r in bare.grid] == ["c"]
    assert bare.cy == 0 and bare._saved is None


def test_finishing_a_screen_that_keeps_no_lines():
    bare = Screen(5, 4)
    Terminal(bare).feed(b"abcdefgh\r\nxy")
    bare.finish()
    assert [r.cells for r in bare.grid] == [[]]
    assert (bare.cy, bare.cx, bare.wrap_pending) == (0, 0, False)


def test_finishing_closes_a_line_that_was_still_wrapping():
    term, screen, store = make(cols=5, rows=4)
    feed(term, "abcde")                         # full row, wrap pending
    screen.finish()
    assert store.texts == ["abcde"] and store.open_parts is None
    assert not screen.wrap_pending


def test_appending_lines_that_are_frozen_already():
    term, screen, store = make(cols=10, rows=4)
    feed(term, "live")
    screen.push_lines([("one", None), ("two", ((1, 9),))])
    assert store.texts == ["live", "one", "two"]
    assert store.styles == {2: ((1, 9),)}
    assert [r.cells for r in screen.grid] == [[]]


def test_a_few_plain_lines_through_the_fast_path():
    for count in (1, 2, 3, 4, 9):
        text = [f"line {i}" * (1 + i % 3) for i in range(count)]
        data = "".join(t + "\r\n" for t in text)
        fast = Screen(8, 4, LineStore())
        assert fast.bulk_ok()
        fast.bulk_lines(data)
        slow = Screen(8, 4, LineStore())
        Terminal(slow).feed(data.encode())
        assert [(r.cells, r.wrapped) for r in fast.grid] == \
            [(r.cells, r.wrapped) for r in slow.grid], count
        assert (fast.cy, fast.cx) == (slow.cy, slow.cx)
        assert fast.store.texts == slow.store.texts
        assert fast.store.open_parts == slow.store.open_parts
        assert fast.all_lines() == slow.all_lines() == \
            [(t, None) for t in text]


def test_when_the_fast_path_may_be_used():
    def fresh(setup: str = "") -> Screen:
        screen = Screen(10, 4, LineStore())
        Terminal(screen).feed(setup.encode())
        return screen

    assert fresh().bulk_ok()
    assert fresh("one\r\ntwo\r\n").bulk_ok()
    assert not Screen(10, 4).bulk_ok()               # nowhere to put lines
    assert not fresh("ab").bulk_ok()                 # in the middle of a row
    assert not fresh("ab\r").bulk_ok()               # on a row with text
    assert not fresh("0123456789").bulk_ok()         # a wrap is pending
    assert not fresh("\x1b[31m").bulk_ok()           # a style is on
    assert fresh("\x1b[44m\x1b[0m").bulk_ok()        # and off again
    assert not fresh("\x1b[2;4r").bulk_ok()          # a region is set
    assert not fresh("\x1b[1;3r").bulk_ok()
    assert not fresh("\x1b[?7l").bulk_ok()           # no wrapping
    assert not fresh("\x1b[4h").bulk_ok()            # insert mode
    assert not fresh("\x1b(0").bulk_ok()             # line drawing
    assert not fresh("a\r\nb\r\n\x1b[A").bulk_ok()   # rows below the cursor
    assert not fresh("0123456789a\r").bulk_ok()
    assert not fresh("0123456789a\x1b[2K\r").bulk_ok()   # the row above wraps


# -- the parser: parameters and broken sequences ------------------------------

def test_parameters_with_colons_empty_ones_and_huge_ones():
    term, screen, _ = make(cols=40, rows=10)
    feed(term, "\x1b[3:9Cx")                    # after a colon: dropped
    assert screen.grid[0].cells[3] == "x"
    feed(term, "\x1b[;7Hy")                     # an empty one is the default
    assert (screen.cy, screen.grid[0].cells[6]) == (0, "y")
    feed(term, "\x1b[3;Hz")
    assert (screen.cy, screen.cx, screen.grid[2].cells) == (2, 1, ["z"])
    feed(term, "\x1b[99999 q")                  # no number is beyond 65535
    assert screen.cursor_shape == 65535
    feed(term, "\x1b[2 q")
    assert screen.cursor_shape == 2
    feed(term, "\x1b[70000;2 q")
    assert screen.cursor_shape == 65535
    feed(term, "\x1b[65534;2 q")
    assert screen.cursor_shape == 65534
    feed(term, "\x1b[ q")
    assert screen.cursor_shape == 0


def test_cancel_and_substitute_end_a_sequence_at_once():
    term, screen, _ = make(cols=40)
    feed(term, "a\x1b\x18b\x1b\x1ac")           # right after the escape
    feed(term, "\x1b[3\x18d\x1b[3\x1ae")        # inside a control sequence
    feed(term, "\x1b]0;title\x18f\x1bPdata\x1ag\r\n")   # inside a string
    assert lines(screen) == ["abcdefg"]
    assert term.title == ""


def test_a_new_escape_ends_a_string_that_was_not_finished():
    term, screen, _ = make(cols=40)
    red = style.intern(1, None, 0)
    feed(term, "\x1b]0;lost\x1b[31mX\x1b[0m\r\n")
    assert term.title == ""
    assert screen.all_lines() == [("X", ((1, red),))]


def test_only_an_operating_system_command_ends_at_a_bell():
    term, screen, _ = make(cols=40)
    rings = []
    term.on_bell = lambda: rings.append(1)
    feed(term, "\x1b]2;one\x07a\x1bPx\x07y\x1b\\b\x1b^p\x07m\x1b\\c"
               "\x1b_a\x07p\x1b\\d\x1bXs\x07s\x1b\\e\x07\r\n")
    assert term.title == "one"
    assert lines(screen) == ["abcde"]
    assert rings == [1]


def test_a_control_sequence_that_never_ends_is_given_up():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[" + "1" * 256)             # still waiting for its end
    feed(term, "mok\r\n")
    assert lines(screen) == ["ok"]
    feed(term, "\x1b[" + "1" * 257)             # too long: dropped
    feed(term, "mok\r\n")
    assert lines(screen) == ["ok", "mok"]


def test_a_string_that_never_ends_is_given_up_after_four_megabytes():
    term, screen, _ = make(cols=40)
    limit = 4 * 1024 * 1024
    feed(term, "\x1b]0;" + "t" * (limit - 4))   # exactly that long: kept
    feed(term, "\x07")
    assert len(term.title) == limit - 4
    feed(term, "\x1b]0;" + "u" * (limit - 3))   # one more: dropped
    feed(term, "\x07x\r\n")
    assert (len(term.title), term.title[0]) == (limit - 4, "t")
    assert lines(screen) == ["x"]


class Searches:
    """Stands in for the expression that finds the end of a string, and
    counts the characters it is given to look at."""

    def __init__(self, real) -> None:
        self.real = real
        self.looked_at = 0

    def search(self, text: str, pos: int = 0):
        self.looked_at += len(text) - pos
        return self.real.search(text, pos)


def test_a_string_in_many_pieces_is_looked_at_once(monkeypatch):
    """A title of 50,000 characters that arrives ten at a time: every
    character is searched for the end once, not once in every read."""
    term, screen, _ = make(cols=40)
    counted = Searches(tmod._STRING_END)
    monkeypatch.setattr(tmod, "_STRING_END", counted)
    body = "t" * 50_000
    feed(term, "\x1b]0;")
    for k in range(0, len(body), 10):
        feed(term, body[k:k + 10])
        assert term._pending == ""
    assert term.title == ""                     # not over yet
    feed(term, "\x07ok\r\n")
    assert term.title == body and lines(screen) == ["ok"]
    # "0;", the body, and the five characters of the last read.
    assert counted.looked_at == 2 + len(body) + 5


def test_the_end_of_a_string_may_be_cut_in_two():
    term, screen, _ = make(cols=40)
    bold = style.intern(None, None, style.BOLD)
    feed(term, "\x1b]0;one\x1b")              # the end, or not?
    assert term.title == ""
    feed(term, "\\a\r\n")
    assert term.title == "one" and lines(screen) == ["a"]
    # It was not: the ESC begins something else, and the string is lost.
    feed(term, "\x1b]0;two\x1b")
    feed(term, "[1mb\r\n")
    assert term.title == "one"
    assert screen.all_lines()[1] == ("b", ((1, bold),))
    # Body, ESC and backslash each in a read of their own.
    feed(term, "\x1b[0m\x1b]0;th")
    feed(term, "ree")
    feed(term, "\x1b")
    feed(term, "\\")
    assert term.title == "three"


def test_a_string_cut_up_is_cancelled_like_one_in_a_piece():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b]0;gone")
    assert term._string_kind == "]" and term._string_size == 6
    feed(term, "more\x18a\r\n")              # CAN
    assert term.title == "" and lines(screen) == ["a"]
    assert (term._string_kind, term._string_parts,
            term._string_size) == ("", [], 0)
    feed(term, "\x1bPx")
    feed(term, "\x07y\x1b\\b\r\n")         # a bell does not end this one
    assert lines(screen) == ["a", "b"]
    assert (term._string_kind, term._string_parts,
            term._string_size) == ("", [], 0)


def test_the_size_of_a_string_counts_every_piece_once(monkeypatch):
    monkeypatch.setattr(tmod, "_MAX_STRING", 12)
    term, screen, _ = make(cols=40)
    feed(term, "\x1b]0;abc")
    feed(term, "defgh")                         # twelve with the ESC ]
    feed(term, "\x07")
    assert term.title == "abcdefgh"
    feed(term, "\x1b]0;abc")
    feed(term, "defghi")                        # thirteen: let go
    assert term._string_kind == ""
    feed(term, "X\r\n")
    assert term.title == "abcdefgh" and lines(screen) == ["X"]
    # An ESC that waits for its backslash is counted once, however many
    # reads bring nothing.
    feed(term, "\x1b]0;abcdefg\x1b")
    for _ in range(5):
        term.feed(b"")
    assert term._string_size == 10
    feed(term, "\\")
    assert term.title == "abcdefg"


def test_an_escape_sequence_may_have_any_intermediate_characters():
    term, screen, _ = make(cols=40)
    # Character sets of several bytes, announcements and the like: not
    # supported, and not printed either.
    feed(term, "a\x1b$Bb\x1b!@c\x1b\"Fd\x1b&4e\x1b'Af\x1b,Ag\x1b$(Dh\r\n")
    assert lines(screen) == ["abcdefgh"]
    # Only "(" or ")" alone names a character set.
    feed(term, "\x1b( 0q\x1b(0q\x1b(B\r\n")
    assert lines(screen)[1] == "q\u2500"
    # Cut in two, and with a control inside.
    feed(term, "\x1b$")
    assert term._pending == "\x1b$"
    feed(term, "Bx\x1b$\rBy\r\n")             # x, back to the margin, By
    assert term._pending == "" and lines(screen)[2] == "By"


def test_an_escape_sequence_without_an_end_is_let_go():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b" + "!" * 15 + "Az\r\n")   # as long as one may be
    feed(term, "\x1b" + "!" * 16 + "Az\r\n")   # longer: A is text again
    assert lines(screen) == ["z", "Az"]
    feed(term, "\x1b" + " " * 100)             # nothing is kept waiting
    assert term._pending == ""
    feed(term, "x\r\n")
    assert lines(screen) == ["z", "Az", "x"]
    feed(term, "\x1b" + "!" * 15)              # this one may still end
    assert term._pending == "\x1b" + "!" * 15
    feed(term, "Ay\r\n")
    assert lines(screen) == ["z", "Az", "x", "y"]


def test_the_oldest_mouse_mode_can_be_asked_about():
    term, _, _ = make()
    feed(term, "\x1b[?9$p\x1b[?9h\x1b[?9$p\x1b[?1000h\x1b[?9$p")
    assert term.replies == [b"\x1b[?9;2$y", b"\x1b[?9;1$y",
                            b"\x1b[?9;2$y"]


def test_the_style_in_force_can_be_asked_for():
    term, _, _ = make()
    feed(term, "\x1bP$qm\x1b\\")
    feed(term, "\x1b[1;4;38;5;208;48;2;1;2;3m\x1bP$qm\x1b\\")
    feed(term, "\x1b[0;7;92;103m\x1bP$qm\x1b\\")
    assert term.replies == [
        b"\x1bP1$r0m\x1b\\",
        b"\x1bP1$r0;1;4;38;5;208;48;2;1;2;3m\x1b\\",
        b"\x1bP1$r0;7;92;103m\x1b\\"]


def test_a_full_reset_gives_the_wheel_back_to_full_screen_programs():
    term, _, _ = make()
    feed(term, "\x1b[?1007l")
    assert term.alt_scroll is False
    feed(term, "\x1bc")
    assert term.alt_scroll is True
def test_a_control_inside_a_control_sequence_does_not_end_it():
    term, screen, _ = make(cols=40)
    feed(term, "ab\x1b[\x082Cx")                # a backspace, then 2 right
    assert screen.grid[0].text() == "ab x"
    feed(term, "\x1b[1\n;1Hy\x1b[2;\t5Hz")       # a linefeed, and a tab
    assert [r.text() for r in screen.grid] == ["yb x", "    z"]


def test_a_control_sequence_with_bytes_before_its_final_one():
    term, screen, _ = make(cols=40)
    feed(term, "\x1b[5 qa\x1b[1$}b\x1b[1'}c\x1b[?5$pd")
    assert screen.cursor_shape == 5
    assert screen.grid[0].text() == "abcd"
    assert term.replies == [b"\x1b[?5;0$y"]


def test_one_very_long_plain_line_takes_the_careful_path():
    term, screen, _ = make(cols=80, rows=24)
    feed(term, "x" * 3000 + "\r\nend\r\n")
    assert lines(screen) == ["x" * 3000, "end"]
    # 38 rows of it, "end" and the cursor's row: 16 rows too many.
    assert (screen.store.texts, len(screen.store.open_parts)) == ([], 16)
    assert len(screen.grid) == 24


def test_null_delete_and_unknown_controls_are_ignored():
    term, screen, _ = make()
    feed(term, "a\x00b\x7fc\x01d" + chr(0x85) + "e" + chr(0x9b) + "f")
    assert screen.grid[0].text() == "abcdef"


def test_the_bell_rings():
    term, screen, _ = make()
    feed(term, "\x07")                          # nobody is listening: fine
    rings = []
    term.on_bell = lambda: rings.append(1)
    feed(term, "a\x07b\x07")
    assert rings == [1, 1] and lines(screen) == ["ab"]


def test_a_terminal_nobody_listens_to_swallows_its_answers():
    screen = Screen(20, 5, LineStore())
    term = Terminal(screen)
    feed(term, "a\x1b[6n\x1b[c\x1bZb")
    assert screen.grid[0].text() == "ab"


# -- the parser: reports ------------------------------------------------------

def test_device_and_position_reports():
    term, screen, _ = make(cols=30, rows=7)
    feed(term, "ab\r\ncd")
    feed(term, "\x1b[?6n\x1b[?5n\x1b[>c\x1b[>q\x1b[=c\x1bZ\x1b[0c\x1b[1c"
               "\x1b[7n\x1b[>5n\x1b[<q")
    assert term.replies == [b"\x1b[?2;3R", b"\x1b[>0;10;1c",
                            b"\x1bP>|jupyterminal\x1b\\", b"\x1b[?62;22c",
                            b"\x1b[?62;22c"]


def test_window_size_reports():
    term, _, _ = make(cols=30, rows=7)
    feed(term, "\x1b[14t\x1b[16t\x1b[18t\x1b[19t\x1b[11t")
    assert term.replies == [b"\x1b[4;126;270t", b"\x1b[6;18;9t",
                            b"\x1b[8;7;30t", b"\x1b[9;7;30t", b"\x1b[1t"]
    del term.replies[:]
    term.cell_px = (10, 21)
    feed(term, "\x1b[14t\x1b[16t\x1b[t\x1b[13t\x1b[15t\x1b[17t\x1b[20t"
               "\x1b[22;0t")
    assert term.replies == [b"\x1b[4;147;300t", b"\x1b[6;21;10t"]


def test_reports_of_the_two_ansi_modes():
    term, _, _ = make()
    feed(term, "\x1b[4$p\x1b[20$p\x1b[4h\x1b[20h\x1b[4$p\x1b[20$p\x1b[7$p"
               "\x1b[$p\x1b[4;20l\x1b[4$p\x1b[20$p")
    assert term.replies == [b"\x1b[4;2$y", b"\x1b[20;2$y", b"\x1b[4;1$y",
                            b"\x1b[20;1$y", b"\x1b[7;0$y", b"\x1b[0;0$y",
                            b"\x1b[4;2$y", b"\x1b[20;2$y"]


@pytest.mark.parametrize("mode", [1, 6, 7, 25, 47, 66, 1000, 1002, 1003,
                                  1004, 1005, 1006, 1007, 1047, 1049, 2004,
                                  2026])
def test_every_private_mode_reports_its_state(mode):
    term, _, _ = make()
    feed(term, f"\x1b[?{mode}h\x1b[?{mode}$p\x1b[?{mode}l\x1b[?{mode}$p")
    assert term.replies == [f"\x1b[?{mode};1$y".encode(),
                            f"\x1b[?{mode};2$y".encode()]


def test_a_mode_the_terminal_does_not_know_is_reported_as_such():
    term, _, _ = make()
    feed(term, "\x1b[?12h\x1b[?12$p\x1b[?$p\x1b[?1001$p\x1b[?2027$p")
    assert term.replies == [b"\x1b[?12;0$y", b"\x1b[?0;0$y",
                            b"\x1b[?1001;0$y", b"\x1b[?2027;0$y"]


def test_requests_for_settings():
    term, screen, _ = make(rows=6)
    feed(term, "\x1bP$qm\x1b\\\x1bP$q q\x1b\\\x1bP$qr\x1b\\\x1bP$qx\x1b\\"
               "\x1bP+q544e\x1b\\\x1bPother\x1b\\\x1bP$\x1b\\")
    assert term.replies == [b"\x1bP1$r0m\x1b\\", b"\x1bP1$r0 q\x1b\\",
                            b"\x1bP1$r1;6r\x1b\\", b"\x1bP0$r\x1b\\",
                            b"\x1bP0+r\x1b\\"]
    del term.replies[:]
    feed(term, "\x1b[4 q\x1b[2;5r\x1bP$q q\x1b\\\x1bP$qr\x1b\\")
    assert term.replies == [b"\x1bP1$r4 q\x1b\\", b"\x1bP1$r2;5r\x1b\\"]


# -- the parser: modes --------------------------------------------------------

def test_input_modes_are_switched_by_their_numbers():
    term, _, _ = make()

    def modes() -> tuple:
        return (term.app_cursor, term.app_keypad, term.focus_events,
                term.mouse_utf8, term.mouse_sgr, term.alt_scroll,
                term.bracketed_paste, term.sync_output)

    assert modes() == (False, False, False, False, False, True, False,
                       False)
    feed(term, "\x1b[?1h\x1b[?66h\x1b[?1004h\x1b[?1005h\x1b[?1006h"
               "\x1b[?1007l\x1b[?2004h\x1b[?2026h")
    assert modes() == (True, True, True, True, True, False, True, True)
    feed(term, "\x1b[?1;66;1004;1005;1006;2004;2026l\x1b[?1007h")
    assert modes() == (False, False, False, False, False, True, False,
                       False)
    feed(term, "\x1b=")
    assert term.app_keypad is True
    feed(term, "\x1b>")
    assert term.app_keypad is False


def test_mouse_reporting_modes_replace_each_other():
    term, _, _ = make()
    assert term.mouse_mode == 0
    for mode in (9, 1000, 1002, 1003):
        feed(term, f"\x1b[?{mode}h")
        assert term.mouse_mode == mode
    feed(term, "\x1b[?1000l")                   # not the one that is on
    assert term.mouse_mode == 1003
    feed(term, "\x1b[?1003l")
    assert term.mouse_mode == 0


def test_dropping_the_modes_of_a_program_that_crashed():
    term, _, _ = make()
    feed(term, "\x1b[?1h\x1b=\x1b[?1003h\x1b[?1006h\x1b[?1005h\x1b[?1004h"
               "\x1b[?2026h\x1b[?2004h\x1b[?1007l")
    term.reset_input_modes()
    assert (term.app_cursor, term.app_keypad, term.mouse_mode,
            term.mouse_sgr, term.mouse_utf8, term.focus_events,
            term.sync_output) == (False, False, 0, False, False, False,
                                  False)
    # The wheel scrolls a full-screen program again, as when the terminal
    # started.  Bracketed paste is the shell's own business: it switches
    # it on at every prompt.
    assert (term.bracketed_paste, term.alt_scroll) == (True, True)


def test_mode_1048_saves_and_restores_the_cursor():
    term, screen, _ = make()
    feed(term, "abc\x1b[?1048h\r\nxyz\x1b[?1048l!")
    assert lines(screen) == ["abc!", "xyz"]


def test_the_cursor_can_be_hidden_and_erasing_has_a_private_form():
    term, screen, _ = make()
    assert screen.cursor_visible is True
    feed(term, "\x1b[?25labcdef\x1b[3G\x1b[?K")
    assert screen.cursor_visible is False
    assert screen.grid[0].text() == "ab"
    feed(term, "\r\nxyz\x1b[H\x1b[?1K\x1b[?J")
    assert [r.text() for r in screen.grid] == [""]
    feed(term, "\x1b[?25h")
    assert screen.cursor_visible is True


def test_the_alternate_screen_is_entered_once_and_left_once():
    term, screen, _ = make()
    events = []
    term.on_alt = events.append
    feed(term, "abc\x1b[?1049l")                # there is none to leave
    assert events == [] and term.screen is screen
    feed(term, "\x1b[?1049h")
    alt = term.screen
    assert (alt.cols, alt.rows, alt.store, alt.reflow) == \
        (20, 5, None, False)
    feed(term, "\x1b[?47h\x1b[?1047h")          # already there
    assert term.screen is alt and events == [True]
    feed(term, "\x1b[?47l\x1b[?1047l")          # already back
    assert term.screen is screen and events == [True, False]
    assert term.main_screen is screen


def test_the_plain_alternate_screen_leaves_the_cursor_alone():
    term, screen, _ = make()
    feed(term, "abc\x1b7\r\n\x1b[?47hfull\x1b[?47lX")
    assert lines(screen) == ["abc", "X"]        # no restoring to the save
    term, screen, _ = make()
    feed(term, "abc\r\n\x1b[?1049hfull\x1b[?1049lX")
    assert lines(screen) == ["abc", "X"]


def test_handing_the_terminal_another_screen_leaves_the_alternate_one():
    term, screen, _ = make()
    events = []
    feed(term, "\x1b[?1049hfull")
    assert term.main_screen is screen
    term.on_alt = events.append
    other = Screen(20, 5, LineStore())
    term.set_screen(other)
    assert term.screen is other and term.main_screen is other
    assert not term.alt_active and events == [False]
    term.set_screen(screen)                     # nothing to leave this time
    assert term.screen is screen and events == [False]
    term.on_alt = None
    feed(term, "\x1b[?1049h")
    term.set_screen(other)                      # and nobody to tell
    assert term.screen is other and not term.alt_active


def test_resizing_reaches_both_screens():
    term, screen, _ = make(cols=20, rows=5)
    term.resize(30, 6)
    assert (screen.cols, screen.rows) == (30, 6)
    feed(term, "\x1b[?1049h")
    term.resize(12, 4)
    assert (term.screen.cols, term.screen.rows) == (12, 4)
    assert (screen.cols, screen.rows) == (12, 4)


# -- the parser: operating system commands ------------------------------------

def test_title_directory_and_clipboard_reports():
    term, screen, _ = make(cols=40)
    seen = []
    feed(term, "\x1b]0;quiet\x07\x1b]7;file:///a\x07\x1b]52;c;aGk=\x07")
    assert term.title == "quiet"                # nobody listens yet
    term.on_title = lambda title: seen.append(("title", title))
    term.on_cwd = lambda url: seen.append(("cwd", url))
    term.on_clipboard = lambda target, text: seen.append(
        ("clip", target, text))
    feed(term, "\x1b]0;one;two\x07\x1b]2;three\x1b\\\x1b]1;icon\x07")
    feed(term, "\x1b]7;file://host/tmp/x\x07")
    feed(term, "\x1b]52;c;aMOpIQ==\x07\x1b]52;p;?\x07\x1b]52;c;\x07"
               "\x1b]52;s0;abc\x07\x1b]52;;aGk=\x1b\\\x1b]52;c;/w==\x07")
    feed(term, "\x1b]999;x\x07\x1b]\x07\x1b]0\x07ok")
    assert seen == [("title", "one;two"), ("title", "three"),
                    ("cwd", "file://host/tmp/x"), ("clip", "c", "hé!"),
                    ("clip", "", "hi"), ("clip", "c", "\ufffd"),
                    ("title", "")]
    assert term.title == ""
    assert lines(screen) == ["ok"]


def test_color_queries_are_answered_in_the_form_they_were_asked():
    term, _, _ = make()
    asked = []
    known = {"fg": (255, 0, 18), "bg": (1, 2, 3), "palette": (16, 32, 48)}

    def colors(kind, index):
        asked.append((kind, index))
        return known.get(kind)

    feed(term, "\x1b]10;?\x07\x1b]4;1;?\x07")   # nobody to ask yet
    assert term.replies == []
    term.color_query = colors
    feed(term, "\x1b]10;?\x07\x1b]11;?\x1b\\\x1b]12;?\x07")
    assert asked == [("fg", 0), ("bg", 0), ("cursor", 0)]
    assert term.replies == [b"\x1b]10;rgb:ffff/0000/1212\x07",
                            b"\x1b]11;rgb:0101/0202/0303\x1b\\"]
    del term.replies[:], asked[:]
    feed(term, "\x1b]4;1;?;17;?\x07\x1b]4;5;?\x1b\\")
    assert asked == [("palette", 1), ("palette", 17), ("palette", 5)]
    assert term.replies == [b"\x1b]4;1;rgb:1010/2020/3030\x07",
                            b"\x1b]4;17;rgb:1010/2020/3030\x07",
                            b"\x1b]4;5;rgb:1010/2020/3030\x1b\\"]
    del term.replies[:], asked[:]
    # Setting a color is not asking for one, and neither is half a pair.
    feed(term, "\x1b]10;#ffffff\x07\x1b]11;red\x07\x1b]4;x;?;2;#fff;3\x07"
               "\x1b]4;7\x07\x1b]4;\x07")
    assert asked == [] and term.replies == []
    del known["palette"]
    feed(term, "\x1b]4;1;?\x07")                # asked, but not known
    assert asked == [("palette", 1)] and term.replies == []


# -- the parser: where a stream is cut makes no difference --------------------

STREAM = ("plain \x1b[1;31mred\x1b[0m\r\n"
          "\x1b]0;the title\x07\x1b]2;other\x1b\\"
          "\x1b(0lqk\x1b(B \x1b)0\x0eq\x0f\r\n"
          "tab\there\x1b[5Gx\x1b[2K\x1b[6n"
          "\x1b[?1049h\x1b[2J\x1b[Hfull\x1b[?1049l"
          "\x1bP$qm\x1b\\\x1b[?2004h\x1b[?25l"
          "wide 日本語 é\x1b[4 q\x1b#8\x1b[3;3Hend\x1b[!p")


def _outcome(parts: list[bytes]) -> tuple:
    term, screen, store = make(cols=20, rows=6)
    events: list[bool] = []
    term.on_alt = events.append
    for part in parts:
        term.feed(part)
    rows = [(row.cells, row.styles, row.wrapped) for row in screen.grid]
    return (rows, screen.cy, screen.cx, term.replies, term.title,
            term.bracketed_paste, screen.cursor_visible,
            screen.cursor_shape, events, store.texts)


def test_a_stream_may_be_cut_anywhere():
    data = STREAM.encode()
    whole = _outcome([data])
    rows, cy, cx, replies, title, paste, visible, shape, events, kept = whole
    assert [("".join(cells), wrapped) for cells, _, wrapped in rows] == [
        ("E" * 20, False), ("E" * 20, False), ("EEend" + "E" * 15, False),
        ("E" * 20, False), ("E" * 20, False), ("E" * 20, False)]
    assert (cy, cx) == (2, 5)
    assert replies == [b"\x1b[3;6R", b"\x1bP1$r0m\x1b\\"]
    assert (title, paste, visible, shape) == ("other", True, True, 4)
    assert events == [True, False] and kept == []
    for cut in range(1, len(data)):
        assert _outcome([data[:cut], data[cut:]]) == whole, cut
    assert _outcome([bytes((byte,)) for byte in data]) == whole


def test_bytes_that_are_not_utf8_show_as_the_replacement_character():
    term, screen, _ = make()
    term.feed(b"a\xffb\xc3")                    # the last one may go on
    assert screen.grid[0].text() == "a" + chr(0xFFFD) + "b"
    term.feed(b"\xa9c")
    assert screen.grid[0].text() == "a" + chr(0xFFFD) + "béc"
