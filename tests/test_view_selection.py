"""Selecting text, and selecting and walking through blocks, in the view.

The sessions are fed by hand, as in test_ui.py.  Every position comes from
the view's own metrics, so nothing here depends on the font.
"""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import QPoint
from PySide6.QtGui import QClipboard, QGuiApplication
from PySide6.QtTest import QTest

from conftest import COLS, LEFT, NONE, Harness, mark, prompt, submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec
from jupyterminal.ui import view as view_module
from jupyterminal.ui.view import PAD_LEFT, TerminalView

CLIPBOARD = QClipboard.Mode.Clipboard
SELECTION = QClipboard.Mode.Selection


# -- helpers, shared with the other test_view_* files ------------------------

def repaints(view) -> list:
    """Record every repaint the view asks for from now on."""
    asked: list = []
    update = view.update

    def recording(*args) -> None:
        asked.append(args)
        update(*args)

    view.update = recording
    return asked


class Board:
    """A clipboard with a primary selection, as a desktop has one; the
    off-screen platform the tests run on has none."""

    def __init__(self, selection: bool) -> None:
        self.selection = selection
        self.texts: dict = {}

    def supportsSelection(self) -> bool:
        return self.selection

    def setText(self, text: str, mode=CLIPBOARD) -> None:
        self.texts[mode] = text

    def text(self, mode=CLIPBOARD) -> str:
        return self.texts.get(mode, "")


def use_board(monkeypatch, selection: bool = True) -> Board:
    """Give the view a ``Board`` for a clipboard."""
    board = Board(selection)
    monkeypatch.setattr(view_module, "QGuiApplication", SimpleNamespace(
        clipboard=lambda: board, styleHints=QGuiApplication.styleHints))
    return board


def input_point(h: Harness, row: int, col: int) -> QPoint:
    """A point on a cell of the input area."""
    view = h.view
    top = view.frame().view_h + view_module.IN_TOP
    return QPoint(PAD_LEFT + col * view.r.cw + 2,
                  int(top + row * view.r.ch + view.r.ch // 2))


def screen_point(h: Harness, row: int, col: int) -> QPoint:
    """A point on a cell of the full-screen terminal."""
    view = h.view
    return QPoint(PAD_LEFT + col * view.r.cw + 2,
                  2 + row * view.r.ch + view.r.ch // 2)


def run_lines(h: Harness, lines: list[str], output: str):
    """Run a command of several lines, the way zsh reports one."""
    h.session.feed(submit("%0A".join(lines), typed=lines[0])
                   + output.encode() + prompt())
    h.pump()
    return h.session.blocks[-1]


def notice(h: Harness, text: str):
    """A job notice at the prompt: the line editor steps below its
    display, zsh prints, and the prompt is drawn again."""
    h.session.feed(b"\r\r\n" + mark("T") + text.encode() + b"\r\n\x1b[J"
                   + mark("A") + b"% \x1b[K")
    h.pump()
    return h.session.blocks[-1]


class Listener:
    """Stands in for the shell integration: notes the idle checks."""

    def __init__(self) -> None:
        self.asked: list[int] = []

    def request_sync(self, number: int) -> bool:
        self.asked.append(number)
        return True


def rest(h: Harness) -> None:
    """Let the line editor report itself idle, as it does once a prompt
    has been quiet for a moment.  From then on the session keeps a
    stand-in between the terminal and the input screen."""
    session = h.session
    session.runtime = listener = Listener()
    session._last_input = session._last_output = session._sync_sent = 0.0
    session.tick()
    session.feed(mark("S", str(listener.asked[-1]), "1"))
    assert session.term.screen is not session.input


def numbered(h: Harness, count: int, command: str = "seq"):
    """A finished block of the lines ``n0`` ... ``n<count - 1>``."""
    return h.run(command, "".join(f"n{i}\r\n" for i in range(count)))


def capped(h: Harness):
    """A finished block whose oldest lines the history cap has dropped;
    line ``i`` of it, counted from the first it ever had, reads ``n<i>``."""
    session = h.session
    session.max_lines = 50
    session.feed(submit("seq 1200")
                 + "".join(f"n{i}\r\n" for i in range(1200)).encode())
    session.max_lines = 2_000_000
    session.feed(prompt())
    h.pump()
    return session.blocks[-1]


def scene(h: Harness):
    """Four blocks: one line, a command of three lines, no output, two
    lines."""
    return (h.run("echo one", "one\r\n"),
            run_lines(h, ["for i in a b", "do echo $i", "done"],
                      "a\r\nb\r\n"),
            h.run("false", "", status=1),
            h.run("ls", "a.txt  b.txt\r\nc.txt\r\n"))


# -- is there a text selection? ----------------------------------------------

def test_nothing_selected_gives_no_text(ui):
    h = ui()
    scene(h)
    view = h.view
    assert view.has_text_selection() is False
    assert view._check_grid_selection() is False
    assert view.selection_text() == ""


def test_a_drag_in_a_block_is_a_text_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    h.drag(h.point(ls, 0, 2), h.point(ls, 1, 3))
    assert view._sel == ((ls.id, 1, 0, 2), (ls.id, 1, 1, 3))
    assert view.has_text_selection() is True
    assert view.selection_text() == "txt  b.txt\nc.t"
    h.drag(h.point(ls, 1, 3), h.point(ls, 0, 2))      # backwards
    assert view._sel == ((ls.id, 1, 1, 3), (ls.id, 1, 0, 2))
    assert view.selection_text() == "txt  b.txt\nc.t"


def test_a_drag_that_stays_on_one_character_selects_nothing(ui, monkeypatch):
    board = use_board(monkeypatch)
    h = ui()
    block = h.run("echo one", "one\r\n")
    view = h.view
    middle = h.point(block, 0, 1)
    top = middle.y() - view.r.ch // 2
    press = QPoint(middle.x(), top + 1)
    moved = QPoint(middle.x(), top + 1 + view_module.DRAG_THRESHOLD)
    QTest.mousePress(view, LEFT, NONE, press)
    QTest.mouseMove(view, moved)
    spot = (block.id, 1, 0, 1)
    assert view._sel == (spot, spot)
    assert view.has_text_selection() is True          # a drag is under way
    assert view.selection_text() == ""                # ... over nothing yet
    QTest.mouseRelease(view, LEFT, NONE, moved)
    h.pump()
    assert view._sel is None
    assert view.has_text_selection() is False
    assert board.texts == {}                          # nothing to publish


def test_selecting_text_fills_the_primary_selection(ui, monkeypatch):
    board = use_board(monkeypatch)
    h = ui()
    one, loop, bad, ls = scene(h)
    h.drag(h.point(ls, 0, 2), h.point(ls, 1, 3))
    assert board.texts == {SELECTION: "txt  b.txt\nc.t"}


def test_no_primary_selection_is_filled_where_there_is_none(ui, monkeypatch):
    board = use_board(monkeypatch, selection=False)
    h = ui()
    one, loop, bad, ls = scene(h)
    h.drag(h.point(ls, 0, 2), h.point(ls, 1, 3))
    assert h.view.selection_text() == "txt  b.txt\nc.t"
    assert board.texts == {}


# -- selections on a screen: the input area, a full-screen program -----------

def test_a_selection_in_the_input_area_lasts_as_long_as_its_prompt(ui):
    h = ui()
    h.session.feed(b"echo typed text")
    h.pump()
    view = h.view
    h.drag(input_point(h, 0, 2), input_point(h, 0, 5))
    chosen = view._gsel
    assert chosen[0] is h.session.input
    assert chosen[1:] == ((0, 2), (0, 6))
    assert view._check_grid_selection() is True
    assert view.has_text_selection() is True
    assert view._gsel is chosen                       # kept while shown
    assert view.selection_text() == "echo"
    h.session.feed(submit("echo typed text", typed=""))
    h.pump()
    assert view._gsel is None                         # its line is gone
    # Whoever still held it would find it worthless.
    view._gsel = chosen
    assert view._check_grid_selection() is False
    assert view._gsel is None
    view._gsel = chosen
    assert view.has_text_selection() is False
    assert view._gsel is None
    view._gsel = chosen
    assert view.selection_text() == ""
    assert view._gsel is None


def test_a_selection_in_the_input_area_holds_at_a_prompt_at_rest(ui):
    h = ui()
    h.session.feed(b"echo typed text")
    h.pump()
    rest(h)
    view = h.view
    h.drag(input_point(h, 0, 7), input_point(h, 0, 11))
    assert view._gsel[0] is h.session.input
    assert view.has_text_selection() is True
    assert view.selection_text() == "typed"
    assert view._gsel is not None


def test_a_full_screen_selection_ends_with_its_program(ui):
    h = ui()
    h.session.feed(submit("less f") + b"\x1b[?1049h\x1b[H\x1b[2J"
                   b"first row of text\r\nsecond row")
    h.pump()
    view = h.view
    h.drag(screen_point(h, 0, 6), screen_point(h, 1, 5))
    chosen = view._gsel
    assert chosen[0] is h.session.term.screen
    assert chosen[1:] == ((0, 6), (1, 6))
    assert view.has_text_selection() is True
    assert view._gsel is chosen
    assert view.selection_text() == "row of text\nsecond"
    # The input area is not what is shown now.
    view._gsel = (h.session.input, (0, 0), (0, 2))
    assert view.has_text_selection() is False
    assert view._gsel is None
    h.session.feed(b"\x1b[?1049l" + prompt())
    h.pump()
    assert not h.session.fullscreen and h.session.prompt_active
    view._gsel = chosen                               # the pager's screen
    assert view.has_text_selection() is False
    assert view._gsel is None


def test_selecting_backwards_in_the_input_area_gives_the_same_text(ui):
    h = ui()
    h.session.feed(b"echo typed text")
    h.pump()
    h.drag(input_point(h, 0, 5), input_point(h, 0, 2))
    assert h.view._gsel[1:] == ((0, 6), (0, 2))
    assert h.view.selection_text() == "echo"


def test_a_wrapped_line_of_the_input_area_is_selected_as_one_line(ui):
    h = ui()
    typed = "".join(chr(ord("a") + i % 26) for i in range(60))
    h.session.feed(typed.encode())
    h.pump()
    line = "% " + typed
    h.drag(input_point(h, 0, 36), input_point(h, 1, 3))
    assert h.view._gsel[1:] == ((0, 36), (1, 4))
    assert h.view.selection_text() == line[36:COLS + 4]
    assert "\n" not in h.view.selection_text()


def test_rows_of_a_full_screen_program_are_selected_as_lines(ui):
    h = ui()
    h.session.feed(submit("less f") + b"\x1b[?1049h\x1b[H\x1b[2J"
                   b"ab   \r\ncd  ef\r\nlast")
    h.pump()
    h.drag(screen_point(h, 2, 1), screen_point(h, 0, 0))    # backwards
    assert h.view._gsel[1:] == ((2, 2), (0, 0))
    # Whole rows in between, and no spaces at the ends of lines.
    assert h.view.selection_text() == "ab\ncd  ef\nla"


def test_a_selection_reaching_below_a_shrunken_screen_keeps_the_rest(ui):
    h = ui()
    typed = "".join(chr(ord("a") + i % 26) for i in range(330))
    h.session.feed(typed.encode() + b"\x1b[8A")       # the cursor goes back up
    h.pump()
    view = h.view
    line = "% " + typed
    assert len(line) > 8 * COLS                       # nine rows of it
    h.drag(input_point(h, 0, 5), input_point(h, 8, 3))
    assert view._gsel[1:] == ((0, 5), (8, 4))
    assert view.selection_text() == line[5:8 * COLS + 4]
    view.resize(*view.size_for(COLS, 8))              # the last row is cut
    h.pump()
    grid = h.session.input.grid
    assert len(grid) == 8 and not grid[-1].wrapped
    assert view.selection_text() == line[5:8 * COLS]


# -- the text of a selection in blocks ---------------------------------------

def test_a_selection_across_blocks_takes_commands_and_output(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    expected = ("ne\n"
                "for i in a b\ndo echo $i\ndone\na\nb\n"
                "false\n"
                "ls\na.txt  b.txt\nc.t")
    view._sel = ((one.id, 1, 0, 1), (ls.id, 1, 1, 3))
    assert view.selection_text() == expected
    view._sel = ((ls.id, 1, 1, 3), (one.id, 1, 0, 1))
    assert view.selection_text() == expected


def test_a_selection_inside_one_block_leaves_the_others_out(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view._sel = ((loop.id, 1, 0, 0), (loop.id, 1, 1, 1))
    assert view.selection_text() == "a\nb"
    view._sel = ((loop.id, 1, 1, 0), (loop.id, 1, 1, 1))
    assert view.selection_text() == "b"
    view._sel = ((ls.id, 1, 0, 7), (ls.id, 1, 0, 9))
    assert view.selection_text() == "b."


def test_a_selection_may_start_and_end_in_commands(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view._sel = ((loop.id, 0, 0, 4), (loop.id, 0, 1, 2))
    assert view.selection_text() == "i in a b\ndo"
    view._sel = ((loop.id, 0, 2, 1), (loop.id, 0, 2, 3))
    assert view.selection_text() == "on"
    # From the middle of a command through its output into the next
    # command: the output of the block it ends in is not part of it.
    view._sel = ((loop.id, 0, 1, 3), (ls.id, 0, 0, 1))
    assert view.selection_text() == "echo $i\ndone\na\nb\nfalse\nl"
    # From a command into the same block's output.
    view._sel = ((ls.id, 0, 0, 1), (ls.id, 1, 0, 5))
    assert view.selection_text() == "s\na.txt"


def test_a_selection_runs_to_the_end_of_each_last_line(ui):
    # The last line of a command or of an output is taken whole, however
    # short the first one is.
    h = ui()
    block = run_lines(h, ["if true", "then echo yes", "fi # and a comment"],
                      "no\r\nyes, indeed\r\n")
    after = h.run("true", "")
    view = h.view
    view._sel = ((block.id, 0, 0, 3), (block.id, 1, 0, 1))
    assert view.selection_text() == \
        "true\nthen echo yes\nfi # and a comment\nn"
    view._sel = ((block.id, 1, 0, 1), (after.id, 0, 0, 2))
    assert view.selection_text() == "o\nyes, indeed\ntr"


def test_collapsed_output_is_not_part_of_a_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.set_collapsed([loop], True)
    view._sel = ((one.id, 1, 0, 0), (ls.id, 1, 0, 5))
    assert view.selection_text() == \
        "one\nfor i in a b\ndo echo $i\ndone\nfalse\nls\na.txt"
    # A selection that ends inside what is hidden takes the command only.
    view._sel = ((one.id, 1, 0, 0), (loop.id, 1, 1, 1))
    assert view.selection_text() == "one\nfor i in a b\ndo echo $i\ndone"


def test_a_selection_takes_only_the_lines_a_filter_shows(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.set_filter([ls], FilterSpec("c."))
    view._sel = ((bad.id, 0, 0, 2), (ls.id, 1, 1, 4))
    assert view.selection_text() == "lse\nls\nc.tx"
    view.set_filter([ls], FilterSpec("b.txt"))
    view._sel = ((bad.id, 0, 0, 2), (ls.id, 1, 1, 4))
    assert view.selection_text() == "lse\nls\na.txt  b.txt"


def test_background_output_has_no_command_to_select(ui):
    h = ui()
    one = h.run("echo one", "one\r\n")
    job = notice(h, "[1]  + done  sleep 1")
    ls = h.run("ls", "a.txt\r\n")
    assert not job.is_command and job.lines() == ["[1]  + done  sleep 1"]
    view = h.view
    view._sel = ((one.id, 0, 0, 5), (ls.id, 1, 0, 3))
    assert view.selection_text() == \
        "one\none\n[1]  + done  sleep 1\nls\na.t"
    view._sel = ((job.id, 0, 0, 0), (job.id, 1, 0, 3))
    assert view.selection_text() == "[1]"


def test_a_selection_in_a_running_block_reads_its_screen_too(ui):
    h = ui(rows=6)
    h.session.feed(submit("seq 30")
                   + "".join(f"n{i}\r\n" for i in range(30)).encode())
    h.pump()
    block = h.session.blocks[-1]
    view = h.view
    assert block.running and 0 < block.store.end < 30
    view._sel = ((block.id, 1, 2, 1), (block.id, 1, 28, 2))
    assert view.selection_text() == "\n".join(
        ["2"] + [f"n{i}" for i in range(3, 28)] + ["n2"])
    assert view._block_lines(block) == ([f"n{i}" for i in range(30)], 0)


def test_lines_are_counted_from_the_first_a_block_ever_had(ui):
    h = ui()
    block = capped(h)
    view = h.view
    base = block.store.base
    assert base > 0 and block.store.dropped == base
    assert view._block_lines(block) == (block.store.texts, base)
    assert block.lines()[0] == f"n{base}" and block.lines()[-1] == "n1199"
    view._sel = ((block.id, 1, base + 1, 1), (block.id, 1, base + 3, 2))
    assert view.selection_text() == \
        f"{base + 1}\nn{base + 2}\nn{str(base + 3)[:1]}"
    # A selection that began in lines since dropped starts at what is left.
    view._sel = ((block.id, 1, base - 5, 3), (block.id, 1, base + 1, 2))
    assert view.selection_text() == \
        f"n{base}\nn{str(base + 1)[:1]}"


def test_the_lines_a_filter_shows(ui):
    h = ui(rows=6)
    done = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\n")
    view = h.view
    shown = TerminalView._shown_set
    assert shown(done) is None                        # no filter
    view.set_filter([done], FilterSpec("error"))
    assert shown(done) is None                        # not decided yet
    view.frame()
    assert shown(done) == [1] and shown(done) is done.visible
    view.set_filter([done], FilterSpec("(", regex=True))
    view.frame()
    assert done.filter is not None and shown(done) is None    # no pattern
    # A block that still runs: the lines on its screen count as well.
    h.session.feed(submit("build") + b"error: early\r\n")
    for i in range(10):
        h.session.feed(f"line {i}\r\n".encode())
    h.session.feed(b"error: late\r\nafter\r\n")
    h.pump()
    block = h.session.blocks[-1]
    view.set_filter([block], FilterSpec("error"))
    view.frame()
    assert block.visible == [0] and block.view.live_lines == [11]
    assert shown(block) == [0, 11]
    view.set_filter([block], FilterSpec("line 2"))    # a new filter:
    assert shown(block) is None                       # undecided again
    view.frame()
    assert block.view.live_lines == [] and shown(block) is block.visible
    assert block.visible == [3]
    view.set_filter([block], FilterSpec("error"))
    view.frame()
    block.view = None                                 # never laid out
    assert shown(block) == [0]


def test_slicing_lines_between_two_positions():
    cut = TerminalView._slice_lines
    lines = ["ab", "cd  ", "ef"]
    # From an offset in one line to an offset in another.
    assert cut(lines, 10, None, 10, 1, 12, 1) == ["b", "cd", "e"]
    # Within one line.
    assert cut(lines, 10, None, 11, 0, 11, 1) == ["c"]
    assert cut(lines, 10, None, 11, 1, 11, 1) == [""]
    # Spaces are dropped where the line ends, and only there.
    assert cut(lines, 10, None, 11, 0, 11, 4) == ["cd"]
    assert cut(lines, 10, None, 11, 0, 11, 3) == ["cd "]
    assert cut(lines, 10, None, 11, 2, 12, 0) == ["", ""]
    # Positions outside the lines are brought to their ends.
    assert cut(lines, 10, None, 7, 1, 11, 1) == ["ab", "c"]
    assert cut(lines, 10, None, 11, 1, 99, 1) == ["d", "ef"]
    assert cut(lines, 10, None, 13, 0, 99, 0) == []
    assert cut([], 10, None, 10, 0, 9, 0) == []
    # With a filter, only the lines it shows.
    assert cut(lines, 10, [10, 12], 10, 1, 12, 1) == ["b", "e"]
    assert cut(lines, 10, [10, 12], 11, 1, 12, 2) == ["ef"]
    assert cut(lines, 10, [10, 12], 10, 0, 11, 1) == ["ab"]
    assert cut(lines, 10, [11], 10, 1, 12, 1) == ["cd"]
    # Lines a stale filter names but the block no longer has are skipped.
    assert cut(lines, 10, [8, 9, 10, 12, 13, 40], 0, 0, 99, 0) == \
        ["ab", "ef"]


# -- words and lines (double and triple clicks) ------------------------------

def word(view, pos) -> tuple:
    """The offsets of the word at a position, which must stay in its
    line."""
    start, end = view._word_at(pos)
    assert start[:3] == end[:3] == pos[:3]
    return start[3], end[3]


def test_a_word_is_letters_digits_and_the_configured_characters(ui):
    h = ui()
    block = h.run("cat f", "alpha beta-gamma delta\r\n"
                           "a_b-c.d e  f\r\n"
                           "x,,y (z)\r\n"
                           "naïve café\r\n"
                           "\r\nlast\r\n")
    view = h.view
    i = block.id
    assert word(view, (i, 1, 0, 8)) == (6, 16)        # beta-gamma
    assert word(view, (i, 1, 0, 6)) == (6, 16)
    assert word(view, (i, 1, 0, 15)) == (6, 16)
    assert word(view, (i, 1, 0, 0)) == (0, 5)         # at the line's start
    assert word(view, (i, 1, 0, 21)) == (17, 22)      # ... and at its end
    assert word(view, (i, 1, 0, 22)) == (17, 22)      # just past the end
    assert word(view, (i, 1, 0, 99)) == (17, 22)
    assert word(view, (i, 1, 0, 5)) == (5, 6)         # the space between
    assert word(view, (i, 1, 1, 2)) == (0, 7)         # a_b-c.d
    assert word(view, (i, 1, 1, 8)) == (8, 9)         # one letter
    assert word(view, (i, 1, 1, 9)) == (9, 11)        # a run of spaces
    assert word(view, (i, 1, 1, 10)) == (9, 11)
    assert word(view, (i, 1, 1, 11)) == (11, 12)      # the last letter
    assert word(view, (i, 1, 2, 1)) == (1, 2)         # one comma of two
    assert word(view, (i, 1, 2, 2)) == (2, 3)
    assert word(view, (i, 1, 2, 5)) == (5, 6)         # a bracket
    assert word(view, (i, 1, 2, 6)) == (6, 7)
    assert word(view, (i, 1, 2, 7)) == (7, 8)
    assert word(view, (i, 1, 3, 1)) == (0, 5)         # letters of any kind
    assert word(view, (i, 1, 3, 7)) == (6, 10)
    assert word(view, (i, 1, 5, 2)) == (0, 4)
    # Nothing to take: an empty line, a line the block does not have.
    for nothing in [(i, 1, 4, 0), (i, 1, 6, 0), (i, 1, 60, 3), (i, 1, -1, 0)]:
        assert view._word_at(nothing) == (nothing, nothing)


def test_which_characters_join_a_word_is_configurable(ui):
    h = ui(config=Config(word_characters=""))
    block = h.run("cat f", "alpha beta-gamma a_b\r\n")
    view = h.view
    i = block.id
    assert word(view, (i, 1, 0, 8)) == (6, 10)        # beta
    assert word(view, (i, 1, 0, 10)) == (10, 11)      # the hyphen alone
    assert word(view, (i, 1, 0, 12)) == (11, 16)      # gamma
    assert word(view, (i, 1, 0, 18)) == (17, 20)      # a_b: always a word
    h = ui(config=Config(word_characters=","))
    block = h.run("cat f", "x,,y (z)\r\n")
    assert word(h.view, (block.id, 1, 0, 1)) == (0, 4)


def test_words_and_lines_of_a_command(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    i = loop.id
    assert word(view, (i, 0, 0, 4)) == (4, 5)         # i
    assert word(view, (i, 0, 0, 1)) == (0, 3)         # for
    assert word(view, (i, 0, 1, 8)) == (8, 9)         # the dollar sign
    assert word(view, (i, 0, 1, 9)) == (9, 10)
    assert word(view, (i, 0, 1, 10)) == (9, 10)       # past the end
    assert word(view, (i, 0, 2, 4)) == (0, 4)         # done
    assert view._line_at((i, 0, 0, 5)) == ((i, 0, 0, 0), (i, 0, 0, 12))
    assert view._line_at((i, 0, 1, 0)) == ((i, 0, 1, 0), (i, 0, 1, 10))
    assert view._line_at((i, 0, 2, 4)) == ((i, 0, 2, 0), (i, 0, 2, 4))
    # A line the command does not have is empty.
    assert view._word_at((i, 0, 3, 0)) == ((i, 0, 3, 0), (i, 0, 3, 0))
    assert view._line_at((i, 0, 3, 2)) == ((i, 0, 3, 0), (i, 0, 3, 0))
    # What a click on the command points at is a position in it.
    spot = h.point(loop, col=5)
    assert view._position_at(spot.x(), spot.y()) == (i, 0, 0, 5)


def test_a_line_is_taken_whole(ui):
    h = ui()
    block = h.run("cat f", "alpha beta\r\n\r\nlast one  \r\n")
    view = h.view
    i = block.id
    assert view._line_at((i, 1, 0, 4)) == ((i, 1, 0, 0), (i, 1, 0, 10))
    assert view._line_at((i, 1, 2, 0)) == ((i, 1, 2, 0), (i, 1, 2, 8))
    for empty in (1, 3, -1):
        assert view._line_at((i, 1, empty, 0)) == \
            ((i, 1, empty, 0), (i, 1, empty, 0))
    view._sel = view._line_at((i, 1, 2, 3))
    assert view.selection_text() == "last one"


def test_words_and_lines_of_a_block_that_lost_its_oldest_lines(ui):
    h = ui()
    block = capped(h)
    view = h.view
    i = block.id
    base = block.store.base
    width = len(f"n{base + 2}")
    assert word(view, (i, 1, base + 2, 1)) == (0, width)
    assert view._line_at((i, 1, base + 2, 1)) == \
        ((i, 1, base + 2, 0), (i, 1, base + 2, width))
    gone = (i, 1, base - 1, 0)                        # dropped since
    assert view._word_at(gone) == (gone, gone)
    assert view._line_at(gone) == (gone, gone)


def test_words_and_lines_of_a_block_that_is_gone(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    lost = (ls.id + 7, 1, 0, 2)
    assert view._word_at(lost) == (lost, lost)
    assert view._line_at(lost) == (lost, lost)


def test_the_row_a_running_command_rests_on_holds_no_word(ui):
    h = ui(rows=6)
    h.session.feed(submit("cat") + b"typed\r\n")
    h.pump()
    block = h.session.blocks[-1]
    view = h.view
    assert block.running and block.lines() == ["typed"]
    spot = h.point(block, 1, 3)                       # the cursor's row
    rest = view._position_at(spot.x(), spot.y())
    assert rest == (block.id, 1, 1, 0)
    assert view._word_at(rest) == (rest, rest)
    assert view._line_at(rest) == (rest, rest)


# -- selecting blocks --------------------------------------------------------

def state(view) -> tuple:
    return view.selected, view._cursor_block, view._range_anchor


def test_selecting_one_block_replaces_the_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    asked = repaints(view)
    laid_out = view._ui
    view.select_block(loop)
    assert state(view) == ([loop.id], loop.id, loop.id)
    assert len(asked) == 1 and view._ui == laid_out + 1
    view.select_block(ls)
    assert state(view) == ([ls.id], ls.id, ls.id)
    view.select_block(one, "anything else")           # means the same
    assert state(view) == ([one.id], one.id, one.id)
    assert view.selected_blocks() == [one]
    assert len(asked) == 3 and view._ui == laid_out + 3


def test_toggling_blocks_in_and_out_of_the_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(bad)
    view.select_block(one, "toggle")
    assert state(view) == ([one.id, bad.id], one.id, one.id)   # in order
    view.select_block(ls, "toggle")
    assert state(view) == ([one.id, bad.id, ls.id], ls.id, ls.id)
    assert view.selected_blocks() == [one, bad, ls]
    asked = repaints(view)
    laid_out = view._ui
    view.select_block(bad, "toggle")                  # out again
    assert state(view) == ([one.id, ls.id], ls.id, ls.id)
    assert len(asked) == 1 and view._ui == laid_out + 1
    view.select_block(ls, "toggle")
    assert state(view) == ([one.id], one.id, one.id)
    view.select_block(one, "toggle")                  # the last one
    assert state(view) == ([], None, None)
    assert view.selected_blocks() == []


def test_a_range_runs_from_the_anchor_to_the_block(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    laid_out = view._ui
    view.select_block(bad, "range")
    assert state(view) == ([loop.id, bad.id], bad.id, loop.id)
    assert view._ui == laid_out + 1
    view.select_block(one, "range")                   # turns round the anchor
    assert state(view) == ([one.id, loop.id], one.id, loop.id)
    view.select_block(loop, "range")
    assert state(view) == ([loop.id], loop.id, loop.id)
    view.select_block(ls, "range")
    assert state(view) == ([loop.id, bad.id, ls.id], ls.id, loop.id)
    assert view.selected_blocks() == [loop, bad, ls]
    # A toggled block becomes the anchor of the next range.
    view.select_block(one, "toggle")
    view.select_block(bad, "range")
    assert state(view) == ([one.id, loop.id, bad.id], bad.id, one.id)


def test_a_range_without_an_anchor_is_one_block(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(bad, "range")
    assert state(view) == ([bad.id], bad.id, bad.id)
    # The anchor's block falls to the history cap.
    view.select_block(one)
    h.session.max_lines = 7
    newest = h.run("seq 3", "1\r\n2\r\n3\r\n")
    assert h.session.blocks == [loop, bad, ls, newest]
    assert view._range_anchor == one.id               # names nothing now
    view.select_block(newest, "range")
    assert state(view) == ([newest.id], newest.id, newest.id)


def test_selecting_a_block_ends_any_text_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    h.drag(h.point(ls, 0, 2), h.point(ls, 1, 3))
    assert view.has_text_selection()
    view.select_block(one)
    assert view._sel is None and not view.has_text_selection()
    h.session.feed(b"typed")
    h.pump()
    h.drag(input_point(h, 0, 2), input_point(h, 0, 5))
    assert view._gsel is not None and view.has_text_selection()
    view.select_block(one, "toggle")
    assert view._gsel is None and not view.has_text_selection()


def test_clearing_the_block_selection(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    asked = repaints(view)
    laid_out = view._ui
    view.clear_block_selection()                      # nothing to clear
    assert state(view) == ([], None, None) and asked == []
    assert view._ui == laid_out
    view.select_block(loop)
    view.select_block(ls, "range")
    asked.clear()
    laid_out = view._ui
    view.clear_block_selection()
    assert state(view) == ([], None, None)
    assert len(asked) == 1 and view._ui == laid_out + 1


def test_letting_go_of_everything_that_is_selected(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    h.session.feed(b"echo typed")
    h.pump()
    view = h.view
    asked = repaints(view)
    laid_out = view._ui
    assert view.drop_selection() is False             # nothing is selected
    assert asked == [] and view._ui == laid_out
    view.select_block(loop)
    view.select_block(ls, "toggle")
    asked.clear()
    assert view.drop_selection() is True
    assert state(view) == ([], None, None)
    assert len(asked) == 2                            # the blocks, the rest
    view._sel = ((ls.id, 1, 0, 0), (ls.id, 1, 0, 5))
    asked.clear()
    assert view.drop_selection() is True
    assert view._sel is None and len(asked) == 1
    view._gsel = (h.session.input, (0, 2), (0, 6))
    assert view.selection_text() == "echo"
    assert view.drop_selection() is True
    assert view._gsel is None and not view.has_text_selection()
    assert view.drop_selection() is False


# -- walking through blocks --------------------------------------------------

def test_moving_the_selection_stops_at_the_first_and_last_block(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(loop)
    view._move_selection(-1, False)
    assert state(view) == ([one.id], one.id, one.id)
    view._move_selection(-1, False)                   # no further
    assert state(view) == ([one.id], one.id, one.id)
    view._move_selection(2, False)
    assert state(view) == ([bad.id], bad.id, bad.id)
    view._move_selection(1, False)
    view._move_selection(1, False)                    # no further
    assert state(view) == ([ls.id], ls.id, ls.id)


def test_extending_the_selection_moves_its_far_end(ui):
    h = ui()
    one, loop, bad, ls = scene(h)
    view = h.view
    view.select_block(bad)
    view._move_selection(-1, True)
    assert state(view) == ([loop.id, bad.id], loop.id, bad.id)
    view._move_selection(-1, True)
    assert state(view) == ([one.id, loop.id, bad.id], one.id, bad.id)
    view._move_selection(1, True)
    view._move_selection(1, True)
    view._move_selection(1, True)                     # past the anchor
    assert state(view) == ([bad.id, ls.id], ls.id, bad.id)


def test_moving_the_selection_brings_the_block_into_view(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    frame = view.frame()
    view.select_block(blocks[5])
    assert view.scroll == frame.max_scroll > 0        # selecting stays put
    for _ in range(5):
        view._move_selection(-1, False)
    assert view.selected == [blocks[0].id] and view.scroll == 0
    view._move_selection(3, True)
    assert view.selected == [b.id for b in blocks[:4]]
    assert view.scroll == frame.tops[3]


def test_moving_without_a_place_to_start_from(ui):
    h = ui()
    view = h.view
    view._move_selection(-1, False)                   # no blocks at all
    assert state(view) == ([], None, None)
    one, loop, bad, ls = scene(h)
    view._move_selection(-1, False)                   # no block selected:
    assert state(view) == ([ls.id], ls.id, ls.id)     # the newest one
    view._cursor_block = ls.id + 5                    # a block long gone
    view._move_selection(1, True)
    assert state(view) == ([ls.id], ls.id, ls.id)


# -- bringing a block into view ----------------------------------------------

def tall_scene(h: Harness):
    """Six blocks of two lines each in a view that shows about two."""
    return [numbered(h, 2, f"seq {k}") for k in range(6)]


def test_a_block_above_the_view_is_brought_to_its_top(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    frame = view.frame()
    view._set_scroll(frame.tops[2] + 1)               # just above the view
    view._reveal_block_start(blocks[2])
    assert view.scroll == frame.tops[2]
    asked = repaints(view)
    view._reveal_block_start(blocks[2])               # at the top: stays
    assert view.scroll == frame.tops[2] and asked == []
    view._reveal_block_start(blocks[0])
    assert view.scroll == 0 and len(asked) == 1


def test_a_block_whose_start_is_below_the_view_is_brought_to_its_top(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    frame = view.frame()
    top, height = frame.tops[2], frame.heights[2]
    three_rows = 3 * view.r.ch
    assert three_rows < height < frame.view_h
    # Three rows of the block show, to the pixel: its start counts as in
    # view, so the whole block is brought in from below.
    view._set_scroll(top + three_rows - frame.view_h)
    view._reveal_block_start(blocks[2])
    assert view.scroll == top + height - frame.view_h
    asked = repaints(view)
    view._reveal_block_start(blocks[2])               # all in view: stays
    assert view.scroll == top + height - frame.view_h and asked == []
    # One pixel less of it: the block moves to the top of the view.
    view._set_scroll(top + three_rows - frame.view_h - 1)
    view._reveal_block_start(blocks[2])
    assert view.scroll == top
    view._set_scroll(0)
    view._reveal_block_start(blocks[1])               # in view already
    assert view.scroll == 0


def test_a_short_block_needs_less_than_three_rows_to_count_as_in_view(ui):
    h = ui(rows=8)
    numbered(h, 12)
    short = h.run("false", "", status=1)
    numbered(h, 12, "seq again")
    view = h.view
    frame = view.frame()
    top, height = frame.tops[1], frame.heights[1]
    assert height < 3 * view.r.ch
    view._set_scroll(top + height - frame.view_h)     # all of it shows
    view._reveal_block_start(short)
    assert view.scroll == top + height - frame.view_h
    view._set_scroll(top + height - frame.view_h - 1)  # but for a pixel
    view._reveal_block_start(short)
    assert view.scroll == top


def test_a_block_as_tall_as_the_view_is_shown_whole(ui):
    h = ui(rows=8)
    numbered(h, 12)
    block = numbered(h, 6, "seq 6")
    numbered(h, 12, "seq again")
    view = h.view
    frame = view.frame()
    view.resize(view.width(), frame.heights[1] + frame.input_h)
    h.pump()
    frame = view.frame()
    top, height = frame.tops[1], frame.heights[1]
    assert height == frame.view_h
    view._set_scroll(top - view.r.ch)
    view._reveal_block_start(block)
    assert view.scroll == top


def test_a_block_taller_than_the_view_stays_where_its_start_shows(ui):
    h = ui(rows=8)
    numbered(h, 12)
    block = numbered(h, 30, "seq 30")
    view = h.view
    frame = view.frame()
    top = frame.tops[1]
    assert frame.heights[1] > frame.view_h
    view._set_scroll(top - view.r.ch)
    view._reveal_block_start(block)
    assert view.scroll == top - view.r.ch
    view._set_scroll(top)
    view._reveal_block_start(block)
    assert view.scroll == top


def test_scrolling_to_the_start_and_the_end_of_a_block(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    frame = view.frame()
    view._scroll_block(blocks[3], False)
    assert view.scroll == frame.tops[3]
    view._scroll_block(blocks[3], True)
    assert view.scroll == \
        frame.tops[3] + frame.heights[3] - frame.view_h
    view._scroll_block(blocks[0], True)               # as far as it goes
    assert view.scroll == 0
    view._scroll_block(blocks[5], False)
    assert view.scroll == frame.max_scroll < frame.tops[5]


def test_a_block_that_is_gone_moves_nothing(ui):
    h = ui(rows=8)
    blocks = tall_scene(h)
    view = h.view
    h.session.max_lines = 6                           # the cap drops three
    numbered(h, 2, "seq more")
    assert blocks[0] not in h.session.blocks
    assert blocks[4] in h.session.blocks
    view._set_scroll(7)
    view._reveal_block_start(blocks[0])
    view._scroll_block(blocks[0], False)
    view._scroll_block(blocks[0], True)
    view._reveal_text(blocks[0], 1, 0)
    assert view.scroll == 7
