"""What the view draws.

Most tests read the list of drawing operations the view issues: a stand-in
for the painter and for the text renderer writes them down, so a test can
say where each rectangle, text and row has to be.  A few look at the
pixels of the real thing.
"""

from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen

from conftest import COLS, NONCE, mark, prompt, submit
from jupyterminal.config import Config
from jupyterminal.session import FilterSpec, Session
from jupyterminal.session.block import Block
from jupyterminal.term.screen import Row
from jupyterminal.ui import view as view_module
from jupyterminal.ui.layout import wrap_complex
from jupyterminal.ui.view import FindState, TerminalView

NO_PEN = Qt.PenStyle.NoPen
NO_BRUSH = Qt.BrushStyle.NoBrush
SMOOTH = QPainter.RenderHint.Antialiasing


def plain(value):
    """A drawing argument as something that compares and prints well."""
    if isinstance(value, (list, tuple)):
        return type(value)(plain(item) for item in value)
    if isinstance(value, QColor):
        return value.name()
    if isinstance(value, (QRect, QRectF)):
        return (value.x(), value.y(), value.width(), value.height())
    if isinstance(value, QPointF):
        return (value.x(), value.y())
    if isinstance(value, QPen):
        return (value.color().name(), value.widthF())
    if isinstance(value, QPainterPath):
        return [(value.elementAt(i).x, value.elementAt(i).y)
                for i in range(value.elementCount())]
    return value


class Canvas:
    """Stands in for the painter and for the text renderer, and writes
    down what the view draws."""

    RenderHint = QPainter.RenderHint

    def __init__(self, *_) -> None:
        self.ops: list[tuple] = []

    def __getattr__(self, name: str):
        def record(*args) -> None:
            self.ops.append((name,) + plain(args))
        return record

    def text(self, p, x, y, text, color, bold=False, italic=False) -> None:
        self.ops.append(("text", x, y, text, color.name(), bold, italic))

    def row(self, p, x, y, segments, highlights=None, overlays=None,
            fg=None, bold=False) -> None:
        self.ops.append(("row", x, y, list(segments), plain(highlights),
                         plain(overlays), plain(fg), bold))


def draw(view, focus: bool = True) -> list[tuple]:
    """Everything the view draws in its present state, with or without
    the keyboard focus."""
    canvas = Canvas()
    renderer = view.r
    renderer.paint_text, renderer.paint_row = canvas.text, canvas.row
    view.hasFocus = lambda: focus
    try:
        view._paint(canvas)
    finally:
        del renderer.paint_text, renderer.paint_row, view.hasFocus
    return canvas.ops


def of(ops: list[tuple], kind: str) -> list[tuple]:
    """The operations of one kind, without their name."""
    return [op[1:] for op in ops if op[0] == kind]


def between(ops: list[tuple], first: tuple, last: tuple) -> list[tuple]:
    """The operations after ``first`` and before ``last``."""
    return ops[ops.index(first) + 1:ops.index(last)]


def colors(view) -> types.SimpleNamespace:
    """The colours of the view's theme, by name, as text."""
    theme = view.theme
    return types.SimpleNamespace(**{
        name: color.name() for name, color in vars(theme).items()
        if isinstance(color, QColor)})


def top_of(view, block) -> int:
    """The window y of the first pixel of a block."""
    frame = view.frame()
    return frame.tops[frame.ids.index(block.id)] - view.scroll


def row_y(view, block, row: int) -> int:
    """The window y of the first pixel of an output row of a block."""
    return top_of(view, block) + block.view.header_h + row * view.r.ch


def pixel(image, x: float, y: float) -> str:
    return image.pixelColor(int(x), int(y)).name()


def running(h, command: str, output: str = ""):
    """Start a command and let it print; it does not finish."""
    h.session.feed(submit(command) + output.encode())
    return h.session.blocks[-1]


def numbered(count: int) -> str:
    return "".join(f"line {i}\r\n" for i in range(count))


def clock(monkeypatch, now: float) -> types.SimpleNamespace:
    """Replace the view's clock by one that shows ``now`` until told
    otherwise."""
    fake = types.SimpleNamespace(now=now)
    fake.monotonic = lambda: fake.now
    monkeypatch.setattr(view_module, "time", fake)
    return fake


# -- the whole picture --------------------------------------------------------

def test_an_empty_session_is_drawn_as_background_and_input_area(ui):
    h = ui()
    view = h.view
    c = colors(view)
    frame = view.frame()
    width, height, view_h = view.width(), view.height(), frame.view_h
    view._buttons = ["stale"]
    assert draw(view) == [
        ("fillRect", (0, 0, width, height), c.background),
        ("save",),
        ("setClipRect", (0, 0, width, view_h)),
        ("restore",),
        ("fillRect", (0, view_h, width, frame.input_h), c.input_background),
        ("fillRect", (0, view_h, width, 1), c.input_border),
        ("row", 14, view_h + 8, [(0, "% ", 0, 2)], None,
         [(2, 3, c.cursor, c.cursor_text)], None, False),
    ]
    assert view._buttons == []


def test_a_block_is_drawn_as_header_rows_and_dividing_line(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("ls", "a.txt  b.txt\r\nc.txt\r\n")
    geo = block.view
    cw, ch, width = view.r.cw, view.r.ch, view.width()
    top = top_of(view, block)
    ops = draw(view)
    assert between(ops, ("setClipRect", (0, 0, width, view.frame().view_h)),
                   ("restore",)) == [
        ("fillRect", (0, top, width, geo.header_h), c.header_background),
        ("row", 14, top + 5, [(0, "ls", 0, 2)], None, None,
         c.header_foreground, True),
        ("row", 14, top + geo.header_h, [(0, "a.txt  b.txt", 0, 12)],
         None, None, None, False),
        ("row", 14, top + geo.header_h + ch, [(0, "c.txt", 0, 5)],
         None, None, None, False),
        ("fillRect", (0, top + geo.height - 1, width, 1), c.divider),
    ]
    assert view._buttons == [
        (QRectF(width - 8 - 2 * cw, top + 5, 2 * cw, ch), block, "menu")]


def test_a_failed_block_is_tinted_and_gets_a_bar(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("false", "", status=1)
    geo = block.view
    cw, width = view.r.cw, view.r.cw * COLS + 34
    top = top_of(view, block)
    ops = draw(view)
    assert between(ops, ("setClipRect", (0, 0, width, view.frame().view_h)),
                   ("restore",)) == [
        ("fillRect", (0, top, width, geo.height - 1), c.failed_background),
        ("fillRect", (0, top, width, geo.header_h), c.failed_header),
        ("text", width - 8 - 12 * cw, top + 5, "exit 1",
         c.failed_foreground, True, False),
        ("row", 14, top + 5, [(0, "false", 0, 5)], None, None,
         c.header_foreground, True),
        ("fillRect", (0, top, 3, geo.height - 1), c.failed_bar),
        ("fillRect", (0, top + geo.height - 1, width, 1), c.divider),
    ]


def test_the_colours_of_a_block_follow_its_state(ui):
    h = ui()
    view = h.view
    theme = view.theme
    fine = h.run("true", "")
    bad = h.run("false", "", status=1)
    assert view._block_colors(fine) == (None, theme.header_background, None)
    assert view._block_colors(bad) == (
        theme.failed_background, theme.failed_header, theme.failed_bar)
    view.selected = [fine.id, bad.id]            # being chosen wins
    for block in (fine, bad):
        assert view._block_colors(block) == (
            theme.selected_background, theme.selected_header,
            theme.selected_bar)


def test_a_selected_block_is_tinted_and_shows_its_menu_dots(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("true", "")
    geo = block.view
    cw, ch, width = view.r.cw, view.r.ch, view.width()
    top = top_of(view, block)
    view.selected = [block.id]
    x = width - 8 - cw                           # the middle of the button
    y = top + 5 + ch / 2
    ops = draw(view)
    assert between(ops, ("setClipRect", (0, 0, width, view.frame().view_h)),
                   ("restore",)) == [
        ("fillRect", (0, top, width, geo.height - 1), c.selected_background),
        ("fillRect", (0, top, width, geo.header_h), c.selected_header),
        ("setPen", NO_PEN),
        ("setBrush", c.foreground),
        ("drawEllipse", (x, y - 5), 1.4, 1.4),
        ("drawEllipse", (x, y), 1.4, 1.4),
        ("drawEllipse", (x, y + 5), 1.4, 1.4),
        ("setBrush", NO_BRUSH),
        ("row", 14, top + 5, [(0, "true", 0, 4)], None, None,
         c.header_foreground, True),
        ("fillRect", (0, top, 3, geo.height - 1), c.selected_bar),
        ("fillRect", (0, top + geo.height - 1, width, 1), c.divider),
    ]


def test_the_menu_dots_also_show_under_the_mouse(ui):
    h = ui()
    view = h.view
    one = h.run("true", "")
    two = h.run("true", "")
    assert of(draw(view), "drawEllipse") == []
    view._hover = two.id
    dots = of(draw(view), "drawEllipse")
    assert len(dots) == 3
    assert dots[1][0][1] == top_of(view, two) + 5 + view.r.ch / 2
    view._hover = None
    view.selected = [one.id]
    dots = of(draw(view), "drawEllipse")
    assert len(dots) == 3
    assert dots[1][0][1] == top_of(view, one) + 5 + view.r.ch / 2


def test_a_failed_block_on_the_screen(ui):
    h = ui()
    view = h.view
    c = colors(view)
    fine = h.run("true", "fine\r\n")
    bad = h.run("false", "oops\r\n", status=1)
    image = view.grab().toImage()
    top = top_of(view, bad)
    geo = bad.view
    bottom = top + geo.height
    for y in (top, top + geo.header_h, bottom - 2):
        assert [pixel(image, x, y) for x in (0, 2)] == [c.failed_bar] * 2
    assert pixel(image, 3, top + 1) == c.failed_header
    assert pixel(image, 3, top + geo.header_h - 1) == c.failed_header
    assert pixel(image, 3, top + geo.header_h) == c.failed_background
    assert pixel(image, view.width() - 3, bottom - 2) == c.failed_background
    assert [pixel(image, x, bottom - 1) for x in (0, 200)] == [c.divider] * 2
    top = top_of(view, fine)                     # its neighbour is not
    assert pixel(image, 0, top + 1) == c.header_background
    assert pixel(image, 3, top + fine.view.header_h) == c.background
    assert pixel(image, 0, top + fine.view.height - 1) == c.divider


# -- what a header shows ------------------------------------------------------

def header_ops(view, block, ops: list[tuple]) -> list[tuple]:
    """What is drawn between a header's background and its command."""
    geo = block.view
    start = [op for op in ops if op[0] == "fillRect"
             and op[1] == (0, top_of(view, block), view.width(),
                           geo.header_h)]
    rest = ops[ops.index(start[0]) + 1:]
    end = [op for op in rest if op[0] == "row"]
    return rest[:rest.index(end[0])]


def test_a_running_command_shows_a_turning_arc_and_its_time(ui, monkeypatch):
    h = ui()
    view = h.view
    c = colors(view)
    block = running(h, "make")
    monkeypatch.setattr(Block, "elapsed", lambda self: 75.0)
    view._spin = 3
    cw, ch = view.r.cw, view.r.ch
    top = top_of(view, block)
    right = view.width() - 8 - 5 * cw            # left of the two buttons
    size = min(2 * cw, ch) - 6
    box = (right - cw - size / 2, top + 5 + ch / 2 - size / 2, size, size)
    assert header_ops(view, block, draw(view)) == [
        ("setRenderHint", SMOOTH, True),
        ("setPen", (c.accent, 2.0)),
        ("drawArc", box, 240 * 16, 270 * 16),
        ("setRenderHint", SMOOTH, False),
        ("text", right - 9 * cw, top + 5, "1m 15s", c.accent, False, False),
    ]
    monkeypatch.setattr(Block, "elapsed", lambda self: 0.5)
    view._spin = 0
    assert header_ops(view, block, draw(view)) == [
        ("setRenderHint", SMOOTH, True),
        ("setPen", (c.accent, 2.0)),
        ("drawArc", box, 0, 270 * 16),
        ("setRenderHint", SMOOTH, False),
    ]


def test_a_chip_is_a_rounded_label(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("less log", "a\r\nb\r\n")
    block.fullscreen = True
    view._ui += 1
    cw, ch = view.r.cw, view.r.ch
    y = top_of(view, block) + 5
    left = view.width() - 8 - 5 * cw - 14 * cw   # the buttons, then 14 cells
    assert header_ops(view, block, draw(view)) == [
        ("setPen", NO_PEN),
        ("setBrush", c.chip_background),
        ("setRenderHint", SMOOTH, True),
        ("drawRoundedRect", (left, y + 1, 13 * cw, ch - 2), 4, 4),
        ("setRenderHint", SMOOTH, False),
        ("setBrush", NO_BRUSH),
        ("text", left + cw, y, "full-screen", c.chip_foreground, False,
         False),
    ]
    assert [name for _, _, name in view._buttons] == ["menu"]


def test_the_filter_chip_has_a_cross_that_takes_the_filter_off(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("make", "ok\r\nerror\r\n")
    block.filter = FilterSpec("error")
    view._ui += 1
    cw, ch = view.r.cw, view.r.ch
    y = top_of(view, block) + 5
    left = view.width() - 8 - 5 * cw - 15 * cw
    end = left + 14 * cw                         # where the chip ends
    assert header_ops(view, block, draw(view)) == [
        ("setPen", NO_PEN),
        ("setBrush", c.chip_background),
        ("setRenderHint", SMOOTH, True),
        ("drawRoundedRect", (left, y + 1, 14 * cw, ch - 2), 4, 4),
        ("setRenderHint", SMOOTH, False),
        ("setBrush", NO_BRUSH),
        ("text", left + cw, y, "filter 1/2", c.chip_foreground, False,
         False),
        ("text", end - 2 * cw + cw / 2, y, "×", c.chip_foreground, True,
         False),
    ]
    assert view._buttons[1:] == [
        (QRectF(end - 2 * cw, y, 2 * cw, ch), block, "unfilter")]


def test_a_collapsed_block_shows_a_triangle_and_what_it_hides(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("seq 3", "1\r\n2\r\n3\r\n")
    block.collapsed = True
    view._ui += 1
    cw, ch = view.r.cw, view.r.ch
    y = top_of(view, block) + 5
    left = view.width() - 8 - 5 * cw - 17 * cw
    ops = draw(view)
    assert header_ops(view, block, ops)[0] == (
        "fillPath", [(5, y + ch * 0.25), (10, y + ch * 0.5),
                     (5, y + ch * 0.75)], c.dim)
    assert of(ops, "text") == [
        (left + cw, y, "3 lines hidden", c.chip_foreground, False, False)]
    assert [row[2] for row in of(ops, "row")] == [[(0, "seq 3", 0, 5)],
                                                  [(0, "% ", 0, 2)]]


def test_time_and_place_are_spelled_out_in_grey(ui):
    h = ui()
    view = h.view
    c = colors(view)
    view.resize(*view.size_for(60, 12))          # wide enough for the place
    block = h.run("make", "")
    block.duration = 75.0
    block.view = None                            # as if it had taken that long
    view._ui += 1
    cw = view.r.cw
    y = top_of(view, block) + 5
    right = view.width() - 8 - 5 * cw
    assert header_ops(view, block, draw(view)) == [
        ("text", right - 7 * cw, y, "1m 15s", c.dim, False, False),
        ("text", right - 13 * cw, y, "/work", c.dim, False, False),
    ]
    block.not_run = True
    block.view = None
    view._ui += 1
    assert header_ops(view, block, draw(view))[0] == (
        "text", right - 8 * cw, y, "not run", c.dim, False, False)


def test_a_visible_scrollbar_pushes_the_header_items_left(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    block = h.run("seq 60", numbered(60), status=1)
    view._set_scroll(0)
    assert view.bar.isVisible()
    cw = view.r.cw
    ops = draw(view)
    assert of(ops, "text") == [
        (view.width() - 12 - 8 - 12 * cw, 5, "exit 1", c.failed_foreground,
         True, False)]
    assert view._buttons[0] == (
        QRectF(view.width() - 12 - 8 - 2 * cw, 5, 2 * cw, view.r.ch), block,
        "menu")


def test_a_block_taller_than_the_view_offers_a_jump_to_its_end(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    block = h.run("seq 60", numbered(60))
    view._set_scroll(0)
    cw, ch = view.r.cw, view.r.ch
    left = view.width() - 12 - 8 - 5 * cw
    middle = left + 1.5 * cw
    tip = 5 + ch - 4 - 2                         # where the arrow points
    assert header_ops(view, block, draw(view)) == [
        ("setPen", (c.dim, 1.5)),
        ("drawLine", (middle, 5 + 3), (middle, tip)),
        ("drawLine", (middle - 4, tip - 4), (middle, tip)),
        ("drawLine", (middle + 4, tip - 4), (middle, tip)),
        ("drawLine", (middle - 5, tip + 3), (middle + 5, tip + 3)),
    ]
    assert view._buttons[1] == (QRectF(left, 5, 3 * cw, ch), block, "jump")


def test_a_block_exactly_as_tall_as_the_view_needs_no_jump(ui):
    h = ui()
    view = h.view
    block = h.run("seq 9", numbered(9))
    height = block.view.height + view.frame().input_h
    view.setMinimumSize(0, 0)
    view.resize(view.width(), height)
    assert view.frame().view_h == block.view.height
    draw(view)
    assert [name for _, _, name in view._buttons] == ["menu"]
    view.resize(view.width(), height - 1)        # one pixel too short
    assert view.frame().view_h == block.view.height - 1
    draw(view)
    assert "jump" in [name for _, _, name in view._buttons]


def test_the_command_stays_pinned_while_its_output_scrolls(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    block = h.run("seq 60", numbered(60), status=1)
    geo = block.view
    view._set_scroll(200)
    width, cw, ch = view.width(), view.r.cw, view.r.ch
    ops = draw(view)
    end = ("fillRect", (0, geo.height - 201, width, 1), c.divider)
    pinned = between(ops, end, ("restore",))
    assert pinned[:3] == [
        ("fillRect", (0, 0, width, geo.header_h), c.failed_header),
        ("fillRect", (0, geo.header_h, width, 1), c.input_border),
        ("fillRect", (0, 0, 3, geo.header_h), c.failed_bar),
    ]
    assert pinned[-2:] == [
        ("text", width - 12 - 8 - 12 * cw, 5, "exit 1", c.failed_foreground,
         True, False),
        ("row", 14, 5, [(0, "seq 60", 0, 6)], None, None,
         c.header_foreground, True),
    ]
    assert [op[0] for op in pinned[3:-2]] == ["setPen"] + ["drawLine"] * 4
    assert [name for _, _, name in view._buttons] == [
        "menu", "jump", "sticky", "menu", "jump"]
    assert view._buttons[2][0] == QRectF(0, 0, width, geo.header_h)
    assert view._buttons[3][0] == QRectF(width - 12 - 8 - 2 * cw, 5, 2 * cw,
                                         ch)


def test_a_pinned_command_of_a_plain_block_has_no_bar(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    block = h.run("seq 60", numbered(60))
    geo = block.view
    view._set_scroll(200)
    width = view.width()
    ops = draw(view)
    end = ("fillRect", (0, geo.height - 201, width, 1), c.divider)
    assert between(ops, end, ("restore",))[:3] == [
        ("fillRect", (0, 0, width, geo.header_h), c.header_background),
        ("fillRect", (0, geo.header_h, width, 1), c.input_border),
        ("setPen", (c.dim, 1.5)),
    ]


def test_the_pinned_command_leaves_with_the_end_of_its_block(ui):
    h = ui(rows=8)
    view = h.view
    first = h.run("seq 60", numbered(60))
    h.run("seq 60", numbered(60))
    geo = first.view
    width = view.width()

    def pinned_at(scroll: int) -> list:
        view._set_scroll(scroll)
        draw(view)
        return [rect for rect, _, name in view._buttons if name == "sticky"]

    assert pinned_at(0) == []                    # the header itself shows
    assert pinned_at(1) == [QRectF(0, 0, width, geo.header_h)]
    last = geo.height - geo.header_h - 1         # the header still fits
    assert pinned_at(last) == [QRectF(0, 0, width, geo.header_h)]
    assert pinned_at(last + 10) == [QRectF(0, -10, width, geo.header_h)]


def test_a_block_without_output_is_never_pinned(ui):
    h = ui(rows=8)
    view = h.view
    h.run("true", "")
    h.run("seq 60", numbered(60))
    view._set_scroll(10)
    draw(view)
    assert [name for _, _, name in view._buttons] == ["menu", "menu", "jump"]


def test_a_long_command_is_drawn_row_by_row(ui):
    h = ui()
    view = h.view
    c = colors(view)
    command = "echo " + "x" * 55
    block = h.run(command, "")
    top = top_of(view, block)
    assert of(draw(view), "row")[:2] == [
        (14, top + 5, [(0, command[:34], 0, 34)], None, None,
         c.header_foreground, True),
        (14, top + 5 + view.r.ch, [(0, command[34:], 0, 26)], None, None,
         c.header_foreground, True),
    ]


def test_a_command_cut_short_ends_in_an_ellipsis(ui):
    h = ui()
    view = h.view
    command = "%0A".join(f"line{i}" for i in range(1, 9))
    h.session.feed(submit(command, typed="x") + prompt())
    rows = of(draw(view), "row")
    assert [row[2] for row in rows[:6]] == [
        [(0, "line1", 0, 5)], [(0, "line2", 0, 5)], [(0, "line3", 0, 5)],
        [(0, "line4", 0, 5)], [(0, "line5", 0, 5)],
        [(0, "line6", 0, 5), (6, "…", 0, 1)]]


def test_the_ellipsis_follows_the_last_cell_of_a_wide_command(ui):
    h = ui()
    view = h.view
    command = "%0A".join(["a", "b", "c", "d", "e", "日本x", "g", "h"])
    h.session.feed(submit(command, typed="x") + prompt())
    rows = of(draw(view), "row")
    assert rows[5][2] == [(0, "日", 0, 2), (2, "本", 0, 2), (4, "x", 0, 1),
                          (6, "…", 0, 1)]


def test_a_command_cut_short_at_an_empty_line_shows_no_ellipsis(ui):
    h = ui()
    view = h.view
    command = "%0A".join(["a", "b", "c", "d", "e", "", "g", "h"])
    h.session.feed(submit(command, typed="x") + prompt())
    block = h.session.blocks[-1]
    rows = of(draw(view), "row")
    assert block.view.cmd_truncated and block.view.cmd_rows[5] == (5, 0, 0)
    assert rows[5][2] == []


def test_a_command_with_wide_characters_keeps_to_the_cell_grid(ui):
    h = ui()
    view = h.view
    c = colors(view)
    block = h.run("echo 日本", "")
    assert of(draw(view), "row")[0][2] == [
        (0, "echo ", 0, 5), (5, "日", 0, 2), (7, "本", 0, 2)]
    view._sel = ((block.id, 0, 0, 5), (block.id, 0, 0, 6))    # the first one
    assert of(draw(view), "row")[0][3] == [(5, 7, c.selection)]
    plain_one = h.run("echo ab", "")
    view._sel = ((plain_one.id, 0, 0, 5), (plain_one.id, 0, 0, 6))
    assert of(draw(view), "row")[1][3] == [(5, 6, c.selection)]


def test_output_without_a_command_is_headed_by_its_label(qapp):
    session = Session(COLS, 4, nonce=NONCE)
    view = TerminalView(session, Config())
    view.resize(*view.size_for(COLS, 4))
    session.feed(b"Last login\r\n")
    c = colors(view)
    ops = draw(view)
    assert of(ops, "text") == [
        (14, top_of(view, session.blocks[0]) + 5,
         "background output · shell startup", c.dim, False, True)]
    assert [row[2] for row in of(ops, "row")] == [[(0, "Last login", 0, 10)]]


# -- which blocks and which rows ----------------------------------------------

def commands_drawn(view) -> list[str]:
    """The commands whose headers the view draws, in drawing order."""
    return [row[2][0][1] for row in of(draw(view), "row") if row[6]]


def blocks_in_view(view) -> list[str]:
    """The commands of the blocks that reach into the view."""
    frame = view.frame()
    return [block.command for block, top, height
            in zip(frame.blocks, frame.tops, frame.heights, strict=True)
            if top < view.scroll + frame.view_h and top + height > view.scroll]


def test_only_the_blocks_in_view_are_drawn(ui):
    h = ui(rows=8)
    view = h.view
    for i in range(12):
        h.run(f"echo {i}", f"{i}\r\n")
    frame = view.frame()
    tops = frame.tops
    assert tops[0] == 0 and frame.max_scroll > tops[6]
    view._set_scroll(tops[3] + 10)               # the fourth is cut off ...
    seen = blocks_in_view(view)
    assert seen[0] == "echo 3" and 3 <= len(seen) < 9
    assert commands_drawn(view) == seen + ["echo 3"]    # ... and pinned
    view._set_scroll(tops[4])                    # the fifth starts on top
    seen = blocks_in_view(view)
    assert seen[0] == "echo 4"
    assert commands_drawn(view) == seen
    view._set_scroll(tops[6] - frame.view_h)     # the seventh: right below
    seen = blocks_in_view(view)
    assert seen[-1] == "echo 5"
    assert commands_drawn(view)[:len(seen)] == seen
    assert "echo 6" not in commands_drawn(view)


def test_blocks_scrolled_away_by_a_clear_are_not_drawn(ui):
    h = ui()
    view = h.view
    h.run("echo one", "one\r\n")
    h.run("echo two", "two\r\n")
    h.session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    frame = view.frame()
    assert view.scroll == frame.tops[1] + frame.heights[1]    # their end
    ops = draw(view)
    assert between(ops, ("setClipRect", (0, 0, view.width(), frame.view_h)),
                   ("restore",)) == []


def test_a_view_without_room_for_blocks_draws_none(ui):
    h = ui()
    view = h.view
    c = colors(view)
    h.run("echo one", "one\r\n")
    view.setMinimumSize(0, 0)
    view.resize(view.width(), 10)
    assert view.frame().view_h == 0
    ops = draw(view)
    assert [op[0] for op in ops] == ["fillRect", "fillRect", "fillRect",
                                     "row"]
    assert ops[1] == ("fillRect", (0, 0, view.width(), 10),
                      c.input_background)
    view.resize(view.width(), view.r.ch + 17)    # one pixel for the blocks
    assert view.frame().view_h == 1
    assert ("setClipRect", (0, 0, view.width(), 1)) in draw(view)


def test_a_block_with_one_pixel_in_view_is_still_drawn(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    for i in range(12):
        h.run(f"echo {i}", f"{i}\r\n")
    frame = view.frame()
    view._set_scroll(frame.tops[4] - 1)          # the last line of the fourth
    ops = draw(view)
    assert ("fillRect", (0, 0, view.width(), 1), c.divider) in ops
    assert commands_drawn(view)[0] == "echo 3"


def output_rows(view, block, top: float, view_h: float) -> list[tuple]:
    """The rows of a block's output that are drawn when the output starts
    at window y ``top``: ``(line, row of the line, y)`` each, and
    ``("live", first, end)`` for the rows left to its screen."""
    calls: list[tuple] = []
    view._paint_line_row = \
        lambda p, block, index, text, runs, sub, layout, y: calls.append(
            (index, sub, y))
    view._paint_live = lambda p, block, geo, top, row, last: calls.append(
        ("live", row, last))
    try:
        view._paint_output(None, block, block.view, top, view_h)
    finally:
        del view._paint_line_row, view._paint_live
    return calls


def test_every_row_of_a_short_block_is_drawn(ui):
    h = ui()
    view = h.view
    ch = view.r.ch
    block = h.run("cat", "one\r\n" + "x" * 100 + "\r\nthree\r\n")
    assert output_rows(view, block, 100, 1000) == [
        (0, 0, 100), (1, 0, 100 + ch), (1, 1, 100 + 2 * ch),
        (1, 2, 100 + 3 * ch), (2, 0, 100 + 4 * ch)]
    assert output_rows(view, block, -2 * ch, 1000) == [
        (1, 1, 0), (1, 2, ch), (2, 0, 2 * ch)]   # from inside the long line
    assert output_rows(view, block, 0, ch + 5) == [
        (0, 0, 0), (1, 0, ch)]                   # ... and up to inside it


def layouts_given(view, block) -> list[tuple]:
    """The cell layout that each drawn row of a block's kept output is
    given, as ``(line, layout)``: ``None`` for a line of plain cells."""
    seen: list[tuple] = []
    view._paint_line_row = \
        lambda p, block, index, text, runs, sub, layout, y: seen.append(
            (index, layout))
    view._paint_live = lambda *args: None
    try:
        view._paint_output(None, block, block.view, 0, 10 ** 6)
    finally:
        del view._paint_line_row, view._paint_live
    return seen


def test_only_lines_that_need_it_are_laid_out_cell_by_cell(ui):
    h = ui(rows=4)
    view = h.view
    wide = "日" * 50
    block = running(h, "cat", "plain\r\n" + wide + "\r\nxy\r\n")
    view.frame()
    assert block.store.texts == ["plain"] and block.store.open_parts
    assert layouts_given(view, block) == [
        (0, None), (1, wrap_complex("日" * 20, COLS))]
    h.session.feed(prompt())
    view.frame()
    assert layouts_given(view, block) == [(0, None)] + [
        (1, wrap_complex(wide, COLS))] * 3 + [(2, None)]
    plain_open = running(h, "cat", "w" * 135 + "\r\nnext\r\nmore")
    view.frame()
    assert layouts_given(view, plain_open) == [(0, None), (0, None)]


def test_rows_above_and_below_the_view_are_left_out(ui):
    h = ui()
    view = h.view
    ch = view.r.ch
    block = h.run("seq 30", numbered(30))

    def drawn(top: float, view_h: float) -> list[int]:
        return [line for line, _, _ in output_rows(view, block, top, view_h)]

    assert drawn(-3 * ch, 4 * ch) == [3, 4, 5, 6, 7]
    assert drawn(-3 * ch + 1, 4 * ch) == [2, 3, 4, 5, 6]
    assert drawn(-3 * ch - 1, 4 * ch) == [3, 4, 5, 6, 7]
    assert drawn(10, 10 + ch // 2) == [0]        # half a row shows
    assert drawn(10, 10 + 2 * ch + ch // 2) == [0, 1, 2]
    assert drawn(0, 10 ** 6) == list(range(30))
    assert drawn(-30 * ch - 2, 500) == []        # all of it is above
    assert drawn(505, 500) == []                 # all of it is below


def test_a_filter_and_dropped_lines_do_not_confuse_the_rows(ui):
    h = ui(rows=4)
    view = h.view
    ch = view.r.ch
    block = running(h, "seq", numbered(9))
    block.store.drop_front(2)
    view.frame()
    assert output_rows(view, block, 0, 1000) == [
        (2, 0, 0), (3, 0, ch), (4, 0, 2 * ch), (5, 0, 3 * ch),
        ("live", 4, 8)]
    block.filter = FilterSpec("line [35]", regex=True)
    view._measure(block)
    assert output_rows(view, block, 0, 1000) == [(3, 0, 0), (5, 0, ch)]


def test_the_part_of_a_line_that_scrolled_off_is_drawn_from_what_was_kept(ui):
    h = ui(rows=4)
    view = h.view
    ch = view.r.ch
    block = running(h, "cat", "line\r\n" + "w" * 135 + "\r\nnext\r\nmore")
    view.frame()
    geo = block.view
    assert (geo.frozen_rows, geo.open_rows, geo.live_rows) == (1, 2, 4)
    assert output_rows(view, block, 0, 1000) == [
        (0, 0, 0), (1, 0, ch), (1, 1, 2 * ch), ("live", 3, 7)]
    assert output_rows(view, block, -2 * ch, 1000) == [
        (1, 1, 0), ("live", 3, 7)]
    assert output_rows(view, block, 0, 2 * ch) == [
        (0, 0, 0), (1, 0, ch), (1, 1, 2 * ch)]   # the view ends before
    assert output_rows(view, block, 0, ch) == [(0, 0, 0), (1, 0, ch)]
    assert output_rows(view, block, -5 * ch, 1000) == [("live", 5, 7)]


def test_output_is_drawn_with_its_styles_and_on_the_cell_grid(ui):
    h = ui()
    view = h.view
    block = h.run("cat", "plain \x1b[31mred\x1b[0m\r\na日b\tc\r\n")
    runs = block.store.styles[0]
    red = runs[1][1]
    assert runs == ((6, 0), (9, red))
    rows = of(draw(view), "row")
    assert rows[1][2] == [(0, "plain ", 0, 6), (6, "red", red, 3)]
    assert rows[2][2] == [(0, "a", 0, 1), (1, "日", 0, 2), (3, "b", 0, 1),
                          (4, "", 0, 4), (8, "c", 0, 1)]


def test_the_scrolled_off_part_of_a_wide_line_keeps_to_the_cell_grid(ui):
    h = ui(rows=4)
    view = h.view
    block = running(h, "cat", "日" * 50 + "\r\nnext\r\nmore")
    view.frame()
    assert block.view.open_rows == 1
    rows = of(draw(view), "row")
    assert rows[1][2] == [(2 * i, "日", 0, 2) for i in range(20)]
    top, ch = row_y(view, block, 0), view.r.ch   # the screen's rows follow
    assert [(row[1], row[2]) for row in rows[4:6]] == [
        (top + 3 * ch, [(0, "next", 0, 4)]),
        (top + 4 * ch, [(0, "more", 0, 4)])]


# -- bands behind and over the text -------------------------------------------

def searching(matches: list[tuple], current: int) -> FindState:
    """A search that has found these matches."""
    state = FindState(FilterSpec("error"))
    state.matches = matches
    state.lines = [match[0] for match in matches]
    state.current = current
    return state


def test_rows_of_output_carry_the_selection_and_the_search(ui):
    h = ui()
    view = h.view
    c = colors(view)
    ch = view.r.ch
    block = h.run("make", "ok 1\r\nerror: a\r\nok 2\r\nError: b error\r\n")
    block.find = searching([(1, 0, 5), (3, 0, 5), (3, 9, 14)], 0)
    view._sel = ((block.id, 1, 3, 2), (block.id, 1, 3, 4))
    y = row_y(view, block, 0)
    assert of(draw(view), "row")[1:5] == [
        (14, y, [(0, "ok 1", 0, 4)], None, None, None, False),
        (14, y + ch, [(0, "error: a", 0, 8)], None,
         [(0, 5, c.find_current, c.find_current_text)], None, False),
        (14, y + 2 * ch, [(0, "ok 2", 0, 4)], None, None, None, False),
        (14, y + 3 * ch, [(0, "Error: b error", 0, 14)],
         [(2, 4, c.selection), (0, 5, c.find), (9, 14, c.find)], None, None,
         False),
    ]
    block.find = searching([], -1)               # a search that found nothing
    assert of(draw(view), "row")[4][3:5] == ([(2, 4, c.selection)], None)


def test_the_columns_of_characters_on_a_plain_row(ui):
    view = ui().view
    assert view._columns("plain", None, 0, 5, 1, 3) == (1, 3)
    wrapped = "x" * 100                          # its second row: 40 to 80
    assert view._columns(wrapped, None, 40, 80, 45, 50) == (5, 10)
    assert view._columns(wrapped, None, 40, 80, 10, 45) == (0, 5)
    assert view._columns(wrapped, None, 40, 80, 70, 200) == (30, 41)
    assert view._columns(wrapped, None, 40, 80, 80, 81) == (40, 41)
    assert view._columns(wrapped, None, 40, 80, 0, 40) is None
    assert view._columns(wrapped, None, 40, 80, 50, 50) is None
    assert view._columns(wrapped, None, 40, 80, 81, 90) is None


def test_the_columns_of_wide_characters_tabs_and_marks(ui):
    view = ui().view
    text = "a日b\tc"                             # at columns 0, 1, 3, 4, 8
    layout = wrap_complex(text, COLS)
    assert view._columns(text, layout, 0, 5, 0, 1) == (0, 1)
    assert view._columns(text, layout, 0, 5, 1, 2) == (1, 3)
    assert view._columns(text, layout, 0, 5, 3, 4) == (4, 8)
    assert view._columns(text, layout, 0, 5, 0, 5) == (0, 9)
    assert view._columns(text, layout, 0, 5, 0, 99) == (0, 10)
    assert view._columns(text, layout, 0, 5, 5, 6) == (9, 10)
    assert view._columns(text, layout, 0, 5, 2, 2) is None
    tab_last = "ab\t"                            # a tab that ends its row
    assert view._columns(tab_last, wrap_complex(tab_last, COLS), 0, 3, 2,
                         3) == (2, 8)
    marked = "e\u0301x"                          # an accent upon the e
    layout = wrap_complex(marked, COLS)
    assert view._columns(marked, layout, 0, 3, 1, 2) == (0, 1)
    assert view._columns(marked, layout, 0, 3, 0, 3) == (0, 2)
    assert view._columns(marked, layout, 0, 2, 0, 99) == (0, 2)
    # A mark right after a tab sits in the tab's last cell.
    odd = "a\t\u0301b"                           # at columns 0, 1, 7, 8
    layout = wrap_complex(odd, COLS)
    assert view._columns(odd, layout, 0, 4, 1, 2) == (1, 7)
    assert view._columns(odd, layout, 0, 3, 1, 2) == (1, 7)
    narrow = "abcdefg\t\u0301"                   # a tab one cell wide
    assert view._columns(narrow, wrap_complex(narrow, COLS), 0, 9, 7,
                         8) == (7, 8)
    assert view._columns("", ([0], []), 0, 0, 0, 5) == (0, 1)


def test_the_selection_band_of_a_row(ui):
    h = ui()
    view = h.view
    sel = view.theme.selection
    block = h.run("cat", "first line\r\n" + "x" * 100 + "\r\nthird\r\n")
    other = h.run("true", "")

    def band(part: int, index: int, start: int, end: int):
        text = block.command if part == 0 else block.store.texts[index]
        return view._selection_band(block, part, index, text, start, end,
                                    None)

    assert band(1, 0, 0, 10) is None             # nothing is selected
    view._sel = ((block.id, 1, 0, 2), (block.id, 1, 0, 7))
    assert band(1, 0, 0, 10) == [(2, 7, sel)]
    view._sel = ((block.id, 1, 0, 7), (block.id, 1, 0, 2))    # backwards
    assert band(1, 0, 0, 10) == [(2, 7, sel)]
    assert band(1, 1, 0, 40) is None and band(0, 0, 0, 3) is None
    view._sel = ((block.id, 0, 0, 1), (block.id, 1, 2, 3))
    assert band(0, 0, 0, 3) == [(1, 4, sel)]     # and the end of the line
    assert band(1, 0, 0, 10) == [(0, 11, sel)]
    assert band(1, 1, 0, 40) == [(0, 40, sel)]   # no wider than the view
    assert band(1, 1, 80, 100) == [(0, 21, sel)]
    assert band(1, 2, 0, 5) == [(0, 3, sel)]
    assert view._selection_band(other, 0, 0, "true", 0, 4, None) is None
    view._sel = ((block.id, 1, 1, 45), (block.id, 1, 1, 50))
    assert band(1, 1, 0, 40) is None             # it is on the next row
    assert band(1, 1, 40, 80) == [(5, 10, sel)]


def test_the_bands_of_a_search_on_a_row(ui):
    view = ui().view
    theme = view.theme
    current = (theme.find_current, theme.find_current_text)
    state = searching([(1, 0, 5), (3, 0, 5), (3, 9, 14), (4, 35, 45),
                       (5, 40, 45)], 1)
    line = "error at error"
    assert view._find_bands(state, 3, line, 0, 14, None, None) == (
        [(9, 14, theme.find)], [(0, 5) + current])
    assert view._find_bands(state, 1, line, 0, 14, None, None) == (
        [(0, 5, theme.find)], None)
    kept = [(2, 4, theme.selection)]
    assert view._find_bands(state, 1, line, 0, 14, None, kept) == (
        kept + [(0, 5, theme.find)], None)
    assert view._find_bands(state, 2, line, 0, 14, None, kept) == (kept, None)
    long = "x" * 100                             # a match over two rows
    assert view._find_bands(state, 4, long, 0, 40, None, None) == (
        [(35, 40, theme.find)], None)
    assert view._find_bands(state, 4, long, 40, 80, None, None) == (
        [(0, 5, theme.find)], None)
    assert view._find_bands(state, 4, long, 80, 100, None, None) == (
        None, None)
    # A match that starts where a row ends belongs to the next row alone.
    assert view._find_bands(state, 5, long, 0, 40, None, None) == (None, None)
    assert view._find_bands(state, 5, long, 40, 80, None, None) == (
        [(0, 5, theme.find)], None)


def grid_row(cells: list[str], wrapped: bool = False) -> Row:
    row = Row()
    row.cells = cells
    row.wrapped = wrapped
    return row


def test_the_selection_band_on_a_row_of_a_live_screen(ui):
    h = ui()
    view = h.view
    sel = view.theme.selection
    block = h.run("true", "")

    def band(row: Row, line: int, first: int = 0):
        count = len("".join(row.cells))
        return view._live_bands(block, row, line, first, count, None)

    row = grid_row(list("hello world"))
    assert band(row, 5) == (None, None)
    view._sel = ((block.id, 1, 5, 2), (block.id, 1, 5, 7))
    assert band(row, 5) == ([(2, 7, sel)], None)
    assert band(row, 4) == (None, None) and band(row, 6) == (None, None)
    view._sel = ((block.id, 1, 6, 1), (block.id, 1, 4, 3))    # backwards
    assert band(row, 4) == ([(3, 12, sel)], None)    # and the line's end
    assert band(row, 5) == ([(0, 12, sel)], None)
    assert band(row, 6) == ([(0, 1, sel)], None)
    wrapped = grid_row(list("x" * 40), wrapped=True)     # the line goes on
    assert band(wrapped, 5) == ([(0, 40, sel)], None)
    full = grid_row(list("x" * 40))              # it ends at the edge
    assert band(full, 5) == ([(0, 40, sel)], None)
    rest = grid_row(list("y" * 10))              # a second row, from 40
    view._sel = ((block.id, 1, 5, 45), (block.id, 1, 5, 48))
    assert band(rest, 5, first=40) == ([(5, 8, sel)], None)
    assert band(wrapped, 5) == (None, None)      # that is the row after
    view._sel = ((block.id, 1, 5, 10), (block.id, 1, 5, 20))
    assert band(rest, 5, first=40) == (None, None)   # ... the row before
    view._sel = ((block.id, 1, 5, 30), (block.id, 1, 5, 45))
    assert band(rest, 5, first=40) == ([(0, 5, sel)], None)


def test_bands_on_a_live_row_cover_whole_cells(ui):
    h = ui()
    view = h.view
    sel = view.theme.selection
    block = h.run("true", "")
    view._sel = ((block.id, 1, 5, 1), (block.id, 1, 5, 2))
    wide = grid_row(["a", "日", "", "b"])
    assert view._live_bands(block, wide, 5, 0, 3, None) == (
        [(1, 3, sel)], None)
    marked = grid_row(["e\u0301", "x"])          # two characters, one cell
    # The mark alone: the cell of the letter it is on, not the next one.
    assert view._live_bands(block, marked, 5, 0, 3, None) == (
        [(0, 1, sel)], None)
    view._sel = ((block.id, 1, 5, 1), (block.id, 1, 5, 3))
    assert view._live_bands(block, marked, 5, 0, 3, None) == (
        [(0, 2, sel)], None)             # the mark and the x
    view._sel = ((block.id, 1, 5, 2), (block.id, 1, 5, 3))
    assert view._live_bands(block, marked, 5, 0, 3, None) == (
        [(1, 2, sel)], None)             # the x alone


def test_the_bands_of_a_search_on_a_row_of_a_live_screen(ui):
    h = ui()
    view = h.view
    theme = view.theme
    current = (theme.find_current, theme.find_current_text)
    block = h.run("true", "")
    row = grid_row(list("error at error"))
    state = searching([(5, 0, 5), (5, 9, 14)], 1)
    assert view._live_bands(block, row, 5, 0, 14, state) == (
        [(0, 5, theme.find)], [(9, 14) + current])
    assert view._live_bands(block, row, 4, 0, 14, state) == (None, None)
    view._sel = ((block.id, 1, 5, 2), (block.id, 1, 5, 4))
    assert view._live_bands(block, row, 5, 0, 14, state) == (
        [(2, 4, theme.selection), (0, 5, theme.find)], [(9, 14) + current])
    view._sel = None
    first = grid_row(list("x" * 40), wrapped=True)   # a line of two rows
    rest = grid_row(list("y" * 10))
    state = searching([(5, 38, 43)], -1)
    assert view._live_bands(block, first, 5, 0, 40, state) == (
        [(38, 40, theme.find)], None)
    assert view._live_bands(block, rest, 5, 40, 10, state) == (
        [(0, 3, theme.find)], None)
    state = searching([(5, 45, 48)], -1)         # all of it on the second
    assert view._live_bands(block, first, 5, 0, 40, state) == (None, None)
    state = searching([(5, 3, 3)], -1)           # nothing wide: nothing
    assert view._live_bands(block, first, 5, 0, 40, state) == (None, None)
    assert view._live_bands(block, first, 5, 0, 40, searching([], -1)) == (
        None, None)


# -- the cursor ---------------------------------------------------------------

def cursor(view, shape: int, focus: bool = True, cx: int = 3) -> tuple:
    """What the cursor of a screen adds to its row drawn at (14, 100):
    the bands it returns and what it draws itself."""
    screen = types.SimpleNamespace(cx=cx, cursor_shape=shape)
    canvas = Canvas()
    view.hasFocus = lambda: focus
    try:
        bands = view._cursor_overlay(canvas, screen, 14, 100)
    finally:
        del view.hasFocus
    return plain(bands), canvas.ops


@pytest.mark.parametrize("shape", [0, 1, 2, 7])
def test_the_cursor_is_a_block_that_shows_its_character(ui, shape):
    view = ui().view
    c = colors(view)
    assert cursor(view, shape) == ([(3, 4, c.cursor, c.cursor_text)], [])


@pytest.mark.parametrize("shape", [3, 4])
def test_the_cursor_can_be_an_underline(ui, shape):
    view = ui().view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    assert cursor(view, shape) == ([], [
        ("fillRect", (14 + 3 * cw, 100 + ch - 2, cw, 2), c.cursor)])


@pytest.mark.parametrize("shape", [5, 6])
def test_the_cursor_can_be_a_bar(ui, shape):
    view = ui().view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    assert cursor(view, shape) == ([], [
        ("fillRect", (14 + 3 * cw, 100, 2, ch), c.cursor)])


@pytest.mark.parametrize("shape", [0, 3, 5])
def test_the_cursor_of_a_view_without_focus_is_an_outline(ui, shape):
    view = ui().view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    assert cursor(view, shape, focus=False) == ([], [
        ("setPen", (c.cursor, 1.0)),
        ("drawRect", (14 + 3 * cw + 0.5, 100.5, cw - 1, ch - 1))])


def test_the_cursor_stays_inside_the_view(ui):
    view = ui().view
    c = colors(view)
    assert cursor(view, 0, cx=COLS - 1) == (
        [(COLS - 1, COLS, c.cursor, c.cursor_text)], [])
    assert cursor(view, 0, cx=COLS + 5) == (
        [(COLS - 1, COLS, c.cursor, c.cursor_text)], [])


# -- screens drawn as plain grids ---------------------------------------------

def screen_ops(view, screen, rows: int, with_cursor: bool,
               focus: bool = True) -> list[tuple]:
    """What drawing a screen at (14, 50) does."""
    canvas = Canvas()
    renderer = view.r
    renderer.paint_row = canvas.row
    view.hasFocus = lambda: focus
    try:
        view._paint_screen(canvas, screen, 14, 50, rows, with_cursor)
    finally:
        del renderer.paint_row, view.hasFocus
    return canvas.ops


def test_a_screen_is_drawn_row_by_row_with_its_cursor(ui):
    h = ui()
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    h.session.feed(b"one\r\ntwo\r\nthree")
    screen = h.session.input
    block_cursor = [(5, 6, c.cursor, c.cursor_text)]
    assert screen_ops(view, screen, 6, True) == [
        ("row", 14, 50, [(0, "% one", 0, 5)], None, None, None, False),
        ("row", 14, 50 + ch, [(0, "two", 0, 3)], None, None, None, False),
        ("row", 14, 50 + 2 * ch, [(0, "three", 0, 5)], None, block_cursor,
         None, False),
    ]
    assert len(screen_ops(view, screen, 2, True)) == 2    # as many as asked
    assert [op[5] for op in screen_ops(view, screen, 6, False)] == [None] * 3
    assert screen_ops(view, screen, 6, True, focus=False)[2:] == [
        ("setPen", (c.cursor, 1.0)),
        ("drawRect", (14 + 5 * cw + 0.5, 50.5 + 2 * ch, cw - 1, ch - 1)),
        ("row", 14, 50 + 2 * ch, [(0, "three", 0, 5)], None, [], None,
         False),
    ]
    screen.cursor_visible = False
    assert [op[5] for op in screen_ops(view, screen, 6, True)] == [None] * 3


def test_a_selection_on_a_screen_is_banded_row_by_row(ui):
    h = ui()
    view = h.view
    sel = colors(view).selection
    h.session.feed(b"one\r\ntwo\r\nthree")
    screen = h.session.input

    def bands(start: tuple, end: tuple) -> list:
        view._gsel = (screen, start, end)
        return [op[4] for op in screen_ops(view, screen, 6, False)]

    assert bands((0, 2), (0, 5)) == [[(2, 5, sel)], None, None]
    assert bands((2, 3), (0, 2)) == [[(2, COLS, sel)], [(0, COLS, sel)],
                                     [(0, 3, sel)]]
    assert bands((0, 5), (1, 0)) == [[(5, COLS, sel)], None, None]
    assert bands((1, 4), (1, 4)) == [None, None, None]
    assert bands((1, 0), (1, 1)) == [None, [(0, 1, sel)], None]
    other = Session(COLS, 4, nonce=NONCE).input  # a selection elsewhere
    view._gsel = (other, (0, 0), (2, 5))
    assert [op[4] for op in screen_ops(view, screen, 6, False)] == [None] * 3


def test_the_input_area_grows_with_what_the_shell_draws(ui):
    h = ui()
    view = h.view
    c = colors(view)
    ch = view.r.ch
    h.session.feed(b"echo one \\\r\n> echo two")
    frame = view.frame()
    top = frame.view_h
    assert frame.input_h == 2 * ch + 16
    ops = draw(view)
    assert ops[-4:] == [
        ("fillRect", (0, top, view.width(), frame.input_h),
         c.input_background),
        ("fillRect", (0, top, view.width(), 1), c.input_border),
        ("row", 14, top + 8, [(0, "% echo one \\", 0, 12)], None, None,
         None, False),
        ("row", 14, top + 8 + ch, [(0, "> echo two", 0, 10)], None,
         [(10, 11, c.cursor, c.cursor_text)], None, False),
    ]


# -- blocks that still run ----------------------------------------------------

def test_a_running_block_is_drawn_from_its_screen_with_the_cursor(ui):
    h = ui(rows=4)
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    block = running(h, "cat", "one\r\ntwo\r\nab")
    y = row_y(view, block, 0)
    last = (14, y + 2 * ch, [(0, "ab", 0, 2)], None)
    assert of(draw(view), "row")[1:] == [
        (14, y, [(0, "one", 0, 3)], None, None, None, False),
        (14, y + ch, [(0, "two", 0, 3)], None, None, None, False),
        last + ([(2, 3, c.cursor, c.cursor_text)], None, False),
    ]
    end = [("fillRect", (0, top_of(view, block) + block.view.height - 1,
                         view.width(), 1), c.divider), ("restore",)]
    ops = draw(view, focus=False)                # an outline, drawn first
    assert ops[-5:] == [
        ("setPen", (c.cursor, 1.0)),
        ("drawRect", (14 + 2 * cw + 0.5, y + 2 * ch + 0.5, cw - 1, ch - 1)),
        ("row",) + last + ([], None, False),
    ] + end
    h.session.feed(b"\x1b[5 q")                  # a bar for a cursor
    ops = draw(view)
    assert ops[-4:] == [
        ("fillRect", (14 + 2 * cw, y + 2 * ch, 2, ch), c.cursor),
        ("row",) + last + ([], None, False),
    ] + end
    h.session.feed(b"\x1b[?25l")                 # and no cursor at all
    assert of(draw(view), "row")[-1] == last + (None, None, False)


def test_a_selection_and_a_search_show_on_a_running_block(ui):
    h = ui(rows=4)
    view = h.view
    c = colors(view)
    block = running(h, "cat", "one\r\ntwo\r\nab")
    view._sel = ((block.id, 1, 1, 1), (block.id, 1, 2, 1))
    block_cursor = [(2, 3, c.cursor, c.cursor_text)]
    assert [row[3:5] for row in of(draw(view), "row")[1:]] == [
        (None, None), ([(1, 4, c.selection)], None),
        ([(0, 1, c.selection)], block_cursor)]
    view._sel = None
    block.find = searching([(0, 1, 3), (2, 0, 2)], 1)
    assert [row[3:5] for row in of(draw(view), "row")[1:]] == [
        ([(1, 3, c.find)], None), (None, None),
        (None, [(0, 2, c.find_current, c.find_current_text)] + block_cursor)]
    block.find = searching([], -1)
    assert [row[3:5] for row in of(draw(view), "row")[1:]] == [
        (None, None), (None, None), (None, block_cursor)]


def test_rows_of_the_screen_follow_those_that_scrolled_off(ui):
    h = ui(rows=4)
    view = h.view
    ch = view.r.ch
    block = running(h, "seq", numbered(6))       # three lines scrolled off
    top = row_y(view, block, 0)
    rows = {row[2][0][1]: row[1] for row in of(draw(view), "row")
            if row[2] and not row[6]}
    assert block.view.frozen_rows == 3
    assert rows["line 2"] == top + 2 * ch        # the last one kept
    assert rows["line 3"] == top + 3 * ch        # the first on the screen
    assert rows["line 5"] == top + 5 * ch
    block.filter = FilterSpec("line [35]", regex=True)
    view._ui += 1
    top = row_y(view, block, 0)
    rows = [(row[2][0][1], row[1]) for row in of(draw(view), "row")[1:]]
    assert rows == [("line 3", top), ("line 5", top + ch)]


def test_a_cleared_screen_is_drawn_as_far_as_it_has_rows(ui):
    h = ui(rows=8)
    view = h.view
    block = running(h, "ssh host", numbered(30) + "\x1b[H\x1b[2Jremote$ ")
    view.frame()
    assert block.view.live_rows == 8
    first = row_y(view, block, block.view.frozen_rows)
    live = [row for row in of(draw(view), "row") if row[1] >= first]
    assert [row[1:3] for row in live] == [(first, [(0, "remote$ ", 0, 8)])]


# -- a program that has the whole view ----------------------------------------

def test_a_full_screen_program_is_drawn_alone(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    h.run("seq 60", numbered(60))
    view.frame()
    assert view.bar.isVisible()
    h.session.feed(submit("vim") + b"\x1b[?1049h\x1b[2J\x1b[Hfile text\r\n"
                   b"second")
    view._buttons = ["stale"]
    assert draw(view) == [
        ("fillRect", (0, 0, view.width(), view.height()), c.background),
        ("row", 14, 2, [(0, "file text", 0, 9)], None, None, None, False),
        ("row", 14, 2 + view.r.ch, [(0, "second", 0, 6)], None,
         [(6, 7, c.cursor, c.cursor_text)], None, False),
    ]
    assert not view.bar.isVisible()
    assert view._buttons == []


def test_a_full_screen_program_is_drawn_no_taller_than_the_view(ui):
    h = ui(rows=8)
    view = h.view
    h.session.feed(submit("vim") + b"\x1b[?1049h" + numbered(5).encode())
    view.rows = 3
    assert [row[2][0][1] for row in of(draw(view), "row")] == [
        "line 0", "line 1", "line 2"]
    view.rows = 8


# -- the bell, and a paint that fails -----------------------------------------

def test_a_visible_bell_washes_the_view_for_a_tenth_of_a_second(
        ui, monkeypatch):
    h = ui()
    view = h.view
    fake = clock(monkeypatch, 0.05)
    wash = ("fillRect", (0, 0, view.width(), view.height()),
            colors(view).selection)
    assert wash not in draw(view)                # no bell has rung
    view._flash = 0.1
    fake.now = 0.19
    assert draw(view)[-1] == wash
    fake.now = 0.2                               # a tenth of a second on
    assert wash not in draw(view)


class Painter(Canvas):
    """A painter that remembers what it was to paint on."""

    made: list = []

    def __init__(self, device) -> None:
        super().__init__()
        self.device = device
        Painter.made.append(self)


def test_a_paint_event_draws_the_view_and_ends_its_painter(ui, monkeypatch):
    h = ui()
    view = h.view
    monkeypatch.setattr(view_module, "QPainter", Painter)
    monkeypatch.setattr(Painter, "made", [])
    view.paintEvent(None)
    painter, = Painter.made
    assert painter.device is view
    assert painter.ops[0] == ("fillRect", (0, 0, view.width(), view.height()),
                              colors(view).background)
    assert [op for op in painter.ops if op[0] == "end"] == [("end",)]
    assert painter.ops[-1] == ("end",)


def test_a_paint_that_fails_is_reported_now_and_then_and_survived(
        ui, monkeypatch, capsys):
    h = ui()
    view = h.view
    fake = clock(monkeypatch, 100.0)
    monkeypatch.setattr(view_module, "QPainter", Painter)
    monkeypatch.setattr(Painter, "made", [])

    def broken(p) -> None:
        raise RuntimeError("the paint went wrong")

    view._paint = broken
    view.paintEvent(None)
    assert "RuntimeError: the paint went wrong" in capsys.readouterr().err
    assert view._paint_failed == 100.0
    assert Painter.made[-1].ops == [("end",)]
    fake.now = 105.0                             # five seconds: still quiet
    view.paintEvent(None)
    assert capsys.readouterr().err == ""
    assert view._paint_failed == 100.0
    assert Painter.made[-1].ops == [("end",)]
    fake.now = 105.5
    view.paintEvent(None)
    assert "the paint went wrong" in capsys.readouterr().err
    assert view._paint_failed == 105.5


def test_a_real_paint_that_fails_leaves_the_window_standing(ui, capsys):
    h = ui()
    view = h.view

    def broken(p) -> None:
        raise RuntimeError("the paint went wrong")

    view._paint = broken
    assert not view.grab().isNull()
    assert "the paint went wrong" in capsys.readouterr().err


# -- the pixels ---------------------------------------------------------------

def test_a_selection_is_painted_behind_exactly_its_cells(ui):
    h = ui()
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    block = h.run("cat", "a      z\r\n")         # cells without ink
    view._sel = ((block.id, 1, 0, 2), (block.id, 1, 0, 5))
    image = view.grab().toImage()
    top = row_y(view, block, 0)
    middle = top + ch // 2
    assert [pixel(image, 14 + col * cw + cw // 2, middle)
            for col in range(1, 7)] == [
        c.background, c.selection, c.selection, c.selection, c.background,
        c.background]
    left, right = 14 + 2 * cw, 14 + 5 * cw
    assert pixel(image, left - 1, middle) == c.background
    assert pixel(image, left, middle) == c.selection
    assert pixel(image, right - 1, middle) == c.selection
    assert pixel(image, right, middle) == c.background
    assert pixel(image, left, top) == c.selection
    assert pixel(image, left, top - 1) == c.header_background
    assert pixel(image, left, top + ch - 1) == c.selection
    assert pixel(image, left, top + ch) == c.background


def test_search_matches_are_painted_in_their_two_colours(ui):
    h = ui()
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    block = h.run("cat", "a      z\r\n")
    block.find = searching([(0, 1, 3), (0, 4, 6)], 1)
    image = view.grab().toImage()
    middle = row_y(view, block, 0) + ch // 2
    assert [pixel(image, 14 + col * cw + cw // 2, middle)
            for col in range(1, 7)] == [
        c.find, c.find, c.background, c.find_current, c.find_current,
        c.background]


def test_the_cursor_and_the_input_area_on_the_screen(ui):
    h = ui()
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    h.run("echo one", "one\r\n")
    view.activateWindow()
    view.setFocus()
    h.pump()
    assert view.hasFocus()
    image = view.grab().toImage()
    top = view.frame().view_h
    assert pixel(image, 3, top) == c.input_border
    assert pixel(image, 3, top + 1) == c.input_background
    assert pixel(image, 3, view.height() - 1) == c.input_background
    middle = top + 8 + ch // 2
    assert [pixel(image, 14 + col * cw + cw // 2, middle)
            for col in (1, 2, 3)] == [c.input_background, c.cursor,
                                      c.input_background]


def test_the_pinned_command_covers_the_output_under_it(ui):
    h = ui(rows=8)
    view = h.view
    c = colors(view)
    block = h.run("seq 60", numbered(60))
    view._set_scroll(200)
    image = view.grab().toImage()
    header = block.view.header_h
    assert pixel(image, 3, 0) == c.header_background
    assert pixel(image, 3, header - 1) == c.header_background
    assert pixel(image, 3, header) == c.input_border
    assert pixel(image, 3, header + 1) == c.background


def test_a_collapsed_block_and_its_chip_on_the_screen(ui):
    h = ui()
    view = h.view
    c = colors(view)
    cw, ch = view.r.cw, view.r.ch
    block = h.run("seq 3", "1\r\n2\r\n3\r\n")
    block.collapsed = True
    view._ui += 1
    image = view.grab().toImage()
    y = top_of(view, block) + 5
    assert pixel(image, 6, y + ch // 2) == c.dim     # inside the triangle
    assert pixel(image, 12, y + ch // 2) == c.header_background
    left = view.width() - 8 - 5 * cw - 17 * cw       # where the chip starts
    assert pixel(image, left + cw // 2, y + ch // 2) == c.chip_background
    assert pixel(image, left - 2, y + ch // 2) == c.header_background
