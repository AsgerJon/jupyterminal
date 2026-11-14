"""The screen model.

A :class:`Screen` is the addressable part of a terminal: a grid of at most
``rows`` rows that a program draws on with cursor movement, erasing and
scrolling.  Rows that scroll off the top are *frozen* into a
:class:`LineStore` as logical lines: rows joined across soft wraps, so a
long line that the window displayed on several rows is stored - and later
copied - as one line.

Cells
-----
A row is a list of cell strings.  Most cells hold one character.  Three
special shapes exist so the text of a row is always ``"".join(cells)``:

* a wide character occupies its own cell followed by one ``""`` cell;
* a tab is kept as ``"\\t"`` followed by ``""`` cells up to the tab stop,
  for as long as nothing overwrites that span;
* a combining mark is appended to the cell of its base character, and so
  is a tab that arrives at the right margin, where it has no cell to take.

Rows are lazy: a row holds only the cells that were written, and the grid
holds only the rows that were reached.  Everything beyond is blank.
"""

from __future__ import annotations

from itertools import groupby

from . import style as _style
from .charwidth import TAB_SIZE, char_width, is_simple, str_width

#: DEC Special Graphics (the line-drawing character set).
DEC_GRAPHICS = str.maketrans(
    "`abcdefghijklmnopqrstuvwxyz{|}~",
    "◆▒␉␌␍␊°±␤␋┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·",
)

_visible_blank: dict[int, bool] = {}
_sgr_cache: dict[tuple[str, int], tuple] = {}


def _shows_on_blank(sid: int) -> bool:
    """True when a space drawn in style ``sid`` is visible."""
    known = _visible_blank.get(sid)
    if known is None:
        _, bg, attrs = _style.lookup(sid)
        known = bg is not None or bool(
            attrs & (_style.INVERSE | _style.UNDERLINE | _style.STRIKE))
        _visible_blank[sid] = known
    return known


class Row:
    """One grid row."""

    __slots__ = ("cells", "styles", "wrapped")

    def __init__(self) -> None:
        self.cells: list[str] = []
        #: One style id per cell, or ``None`` while every cell is default.
        self.styles: list[int] | None = None
        #: True when this row continues on the next one (soft wrap).
        self.wrapped = False

    def is_blank(self, start: int = 0, stop: int | None = None) -> bool:
        """True when the cells from ``start`` up to ``stop`` show nothing:
        no character and no colour."""
        cells = self.cells
        if not cells:
            return True
        if "".join(cells[start:stop]).strip(" "):
            return False
        styles = self.styles
        if styles is not None:
            for sid in set(styles[start:stop]):
                if sid and _shows_on_blank(sid):
                    return False
        return True

    def text(self) -> str:
        return "".join(self.cells)


Runs = tuple  # ((end_offset, style_id), ...) over character offsets


def row_text_runs(row: Row, keep_trailing: bool) -> tuple[str, Runs | None]:
    """The text of a row and its style runs in character offsets."""
    cells = row.cells
    styles = row.styles
    if styles is None:
        text = "".join(cells)
        return (text if keep_trailing else text.rstrip(" ")), None
    n = len(cells)
    if not keep_trailing:
        while n and not styles[n - 1] and cells[n - 1] in (" ", ""):
            n -= 1
    if n == 0:
        return "", None
    part = cells[:n] if n != len(cells) else cells
    text = "".join(part)
    runs: list[tuple[int, int]] = []
    current = styles[0]
    # One character in every cell: then cells are character offsets.  As
    # many characters as cells is not enough, since a wide character (one
    # in two cells) and a marked one (two in one cell) cancel out.
    if len(text) == n and "" not in part:
        end = 0
        for sid, group in groupby(styles if n == len(styles) else styles[:n]):
            end += len(list(group))
            runs.append((end, sid))
    else:
        offset = 0
        for i in range(n):
            cell = cells[i]
            sid = styles[i]
            if sid != current and cell != "":
                # Never at offset 0: the first cell of a row is not a
                # continuation cell, and it set ``current``.
                runs.append((offset, current))
                current = sid
            offset += len(cell)
        runs.append((offset, current))
    if runs[-1][1] == 0:
        runs.pop()
    return text, (tuple(runs) if runs else None)


class LineStore:
    """Frozen logical lines of one block.

    Lines are addressed by an absolute index that never changes, even when
    old lines are dropped from the front (``base`` moves instead).
    """

    __slots__ = ("texts", "styles", "base", "complex", "max_width",
                 "open_parts", "open_runs", "open_len", "_open_text",
                 "dropped")

    def __init__(self) -> None:
        self.texts: list[str] = []
        #: absolute index -> style runs, for lines that are not all-default.
        self.styles: dict[int, Runs] = {}
        self.base = 0
        #: absolute index -> display width, for lines that are not simple.
        self.complex: dict[int, int] = {}
        self.max_width = 0
        # A logical line whose first rows have scrolled off while its last
        # row is still on the grid.
        self.open_parts: list[str] | None = None
        self.open_runs: list[tuple[int, int]] = []
        self.open_len = 0
        self._open_text: str | None = None
        #: Lines dropped from the front (the safety valve for one endless
        #: block).
        self.dropped = 0

    @property
    def end(self) -> int:
        """Absolute index one past the last complete line."""
        return self.base + len(self.texts)

    def __len__(self) -> int:
        return len(self.texts)

    def text(self, index: int) -> str:
        return self.texts[index - self.base]

    def runs(self, index: int) -> Runs | None:
        return self.styles.get(index)

    # -- appending ---------------------------------------------------------

    def append(self, text: str, runs: Runs | None = None) -> None:
        index = self.base + len(self.texts)
        self.texts.append(text)
        if runs:
            self.styles[index] = runs
        if is_simple(text):
            width = len(text)
        else:
            width = str_width(text)
            self.complex[index] = width
        if width > self.max_width:
            self.max_width = width

    def extend_plain(self, lines: list[str]) -> None:
        """Append simple, unstyled lines, at least one, all at once."""
        self.texts.extend(lines)
        width = max(map(len, lines))
        if width > self.max_width:
            self.max_width = width

    def push_row(self, row: Row) -> None:
        """Freeze one grid row, joining it to its neighbours if wrapped."""
        text, runs = row_text_runs(row, row.wrapped)
        parts = self.open_parts
        if parts is None:
            if not row.wrapped:
                self.append(text, runs)
                return
            parts = self.open_parts = []
        if runs:
            offset = self.open_len
            merged = self.open_runs
            if merged and merged[-1][0] < offset:
                merged.append((offset, 0))
            elif not merged and offset:
                merged.append((offset, 0))
            merged.extend((end + offset, sid) for end, sid in runs)
        parts.append(text)
        self.open_len += len(text)
        self._open_text = None
        if not row.wrapped:
            self.close_open()

    def close_open(self) -> None:
        """Finish the open line, if any, and make it a complete line."""
        parts = self.open_parts
        if parts is None:
            return
        text = "".join(parts)
        runs = _normalize_runs(self.open_runs)
        self.open_parts = None
        self.open_runs = []
        self.open_len = 0
        self._open_text = None
        self.append(text, runs)

    def open_text(self) -> str:
        """Text of the open line so far (cached)."""
        text = self._open_text
        if text is None:
            text = self._open_text = "".join(self.open_parts or ())
        return text

    def open_line_runs(self) -> Runs | None:
        return _normalize_runs(self.open_runs)

    def take_open(self) -> tuple[str, Runs | None] | None:
        """Remove and return the open line (used when re-wrapping)."""
        if self.open_parts is None:
            return None
        result = (self.open_text(), _normalize_runs(self.open_runs))
        self.open_parts = None
        self.open_runs = []
        self.open_len = 0
        self._open_text = None
        return result

    # -- removing ----------------------------------------------------------

    def clear(self) -> None:
        self.base += len(self.texts)
        self.texts = []
        self.styles = {}
        self.complex = {}
        self.max_width = 0
        self.open_parts = None
        self.open_runs = []
        self.open_len = 0
        self._open_text = None

    def drop_front(self, count: int) -> None:
        """Drop the ``count`` oldest lines."""
        count = min(count, len(self.texts))
        if count <= 0:
            return
        old_base = self.base
        del self.texts[:count]
        self.base = new_base = old_base + count
        self.dropped += count
        for table in (self.styles, self.complex):
            if len(table) < count:
                for key in [k for k in table if k < new_base]:
                    del table[key]
            else:
                for key in range(old_base, new_base):
                    table.pop(key, None)

    def take_all(self) -> list[tuple[str, Runs | None]]:
        """Remove and return every line; one that is still open is over
        with that."""
        self.close_open()
        base = self.base
        styles = self.styles
        lines = [(text, styles.get(base + i))
                 for i, text in enumerate(self.texts)]
        self.clear()
        return lines


def _frozen(line: str, cols: int) -> str:
    """A plain line as the rows it would be laid out on freeze: without
    the blanks at the end of its last row."""
    start = (len(line) - 1) // cols * cols      # where that row begins
    return line[:start] + line[start:].rstrip(" ")


def _normalize_runs(runs: list[tuple[int, int]]) -> Runs | None:
    """Join neighbouring runs of one style.

    The runs come from :func:`row_text_runs`, shifted and put end to end:
    their ends only grow, and the last one is never the default style.
    """
    if not runs:
        return None
    out: list[tuple[int, int]] = []
    for end, sid in runs:
        if out and out[-1][1] == sid:
            out[-1] = (end, sid)
        else:
            out.append((end, sid))
    return tuple(out)


class Screen:
    """A terminal screen: grid, cursor, attributes and local modes."""

    def __init__(self, cols: int, rows: int, store: LineStore | None = None,
                 reflow: bool = True) -> None:
        self.cols = max(1, cols)
        self.rows = max(1, rows)
        #: Where rows go when they scroll off the top; ``None`` discards.
        self.store = store
        #: Re-wrap content when the width changes (else clip).
        self.reflow = reflow
        self.grid: list[Row] = [Row()]
        self.cx = 0
        self.cy = 0
        self.wrap_pending = False
        self.fg: int | None = None
        self.bg: int | None = None
        self.attrs = 0
        self.sid = 0
        #: Style of erased cells (background color erase).
        self.bce = 0
        self.top = 0
        self.bot = self.rows - 1
        self.autowrap = True
        self.insert = False
        self.origin = False
        self.newline_mode = False
        self.cursor_visible = True
        self.cursor_shape = 0
        self.tabs: set[int] | None = None
        self._charsets = ["B", "B"]
        self._gl = 0
        self._xlat: dict | None = None
        self._saved: tuple | None = None
        self.last_char = ""
        #: Incremented whenever the program erases the whole display.
        self.clear_count = 0

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _ensure_row(self, index: int) -> None:
        grid = self.grid
        while len(grid) <= index:
            grid.append(Row())

    def _blank_row(self) -> Row:
        row = Row()
        if self.bce:
            row.cells = [" "] * self.cols
            row.styles = [self.bce] * self.cols
        return row

    def _pad(self, row: Row, length: int) -> None:
        cells = row.cells
        missing = length - len(cells)
        if missing > 0:
            cells.extend(" " * missing)
            if row.styles is not None:
                row.styles.extend([0] * missing)

    @staticmethod
    def _break(row: Row, x: int) -> None:
        """Turn the wide character or tab that owns cell ``x`` into spaces.

        ``x`` is a continuation cell (``""``).  Its owner is somewhere to
        its left: no row begins with a continuation cell.
        """
        cells = row.cells
        owner = x - 1
        while cells[owner] == "":
            owner -= 1
        head = cells[owner]
        if head[0] == "\t":
            end = owner + 1
            n = len(cells)
            while end < n and cells[end] == "":
                end += 1
            for i in range(owner, end):
                cells[i] = " "
        elif x == owner + 1 and char_width(head[0]) == 2:
            cells[owner] = " "
            cells[x] = " "
        else:
            cells[x] = " "

    def _write(self, row: Row, x: int, part, count: int, sid: int) -> None:
        """Store ``count`` cells from ``part`` at column ``x``."""
        cells = row.cells
        length = len(cells)
        if x > length:
            # The row is ``x`` cells long now.  The old length still
            # serves below: nothing lies at ``x`` or beyond either way.
            self._pad(row, x)
        end = x + count
        if x < length and cells[x] == "":
            self._break(row, x)
        if end < length and cells[end] == "":
            self._break(row, end)
        cells[x:end] = part
        styles = row.styles
        if styles is None:
            if sid:
                styles = row.styles = [0] * len(cells)
                styles[x:end] = [sid] * count
        else:
            styles[x:end] = [sid] * count

    def _fill(self, row: Row, x0: int, x1: int, sid: int) -> None:
        """Blank the cells ``[x0, x1)`` in style ``sid``."""
        if x1 <= x0:
            return
        self._write(row, x0, " " * (x1 - x0), x1 - x0, sid)

    def _index(self) -> None:
        """Move down one row, scrolling at the bottom margin."""
        cy = self.cy
        if cy == self.bot:
            self._scroll_up(1)
        elif cy < self.rows - 1:
            cy += 1
            if cy >= len(self.grid):
                self.grid.append(Row())
            self.cy = cy

    def _wrap(self) -> None:
        row = self.grid[self.cy]
        self._pad(row, self.cols)
        row.wrapped = True
        self.cx = 0
        self.wrap_pending = False
        self._index()

    def _scroll_up(self, count: int) -> None:
        grid = self.grid
        top = self.top
        bot = self.bot
        store = self.store
        count = min(count, bot - top + 1)
        if self.bce:
            # The rows that come up have the erase colour: they are rows
            # of the grid, then, however little of it was reached so far.
            self._ensure_row(bot)
        for _ in range(count):
            if top < len(grid):
                row = grid.pop(top)
                if top == 0 and store is not None:
                    store.push_row(row)
            if len(grid) >= bot:
                grid.insert(bot, self._blank_row())
        self._ensure_row(self.cy)

    def _scroll_down(self, count: int) -> None:
        grid = self.grid
        top = self.top
        bot = self.bot
        count = min(count, bot - top + 1)
        # The rows above the region, so that the new ones land in it.
        self._ensure_row(top - 1)
        for _ in range(count):
            if bot < len(grid):
                del grid[bot]
            grid.insert(top, self._blank_row())

    def _next_tab(self, x: int) -> int:
        last = self.cols - 1
        tabs = self.tabs
        if tabs is None:
            stop = (x // TAB_SIZE + 1) * TAB_SIZE
        else:
            later = [t for t in tabs if t > x]
            stop = min(later) if later else last
        return stop if stop < last else last

    def _prev_tab(self, x: int) -> int:
        tabs = self.tabs
        if tabs is None:
            return ((x - 1) // TAB_SIZE) * TAB_SIZE if x > 0 else 0
        earlier = [t for t in tabs if t < x]
        return max(earlier) if earlier else 0

    # ------------------------------------------------------------------
    # printing
    # ------------------------------------------------------------------

    def put_text(self, text: str) -> None:
        """Print a run of printable characters at the cursor."""
        if self._xlat is not None:
            text = text.translate(self._xlat)
        if text.isascii() or is_simple(text):
            self.last_char = text[-1]
            self._put_simple(text)
        else:
            self._put_complex(text)

    def _put_simple(self, text: str) -> None:
        cols = self.cols
        sid = self.sid
        n = len(text)
        pos = 0
        while pos < n:
            if self.wrap_pending:
                if self.autowrap:
                    self._wrap()
                else:
                    self.wrap_pending = False
                    pos = n - 1
            cx = self.cx
            row = self.grid[self.cy]
            room = cols - cx
            take = n - pos
            if take > room:
                take = room
            part = text if take == n else text[pos:pos + take]
            if self.insert:
                self.insert_chars(take)
            self._write(row, cx, part, take, sid)
            pos += take
            cx += take
            if cx >= cols:
                cx = cols - 1
                self.wrap_pending = self.autowrap
                if not self.autowrap:
                    if pos < n:
                        self._write(row, cx, text[-1], 1, sid)
                    pos = n
            self.cx = cx

    def _put_complex(self, text: str) -> None:
        cols = self.cols
        sid = self.sid
        for ch in text:
            width = char_width(ch)
            if width == 0:
                row = self.grid[self.cy]
                cells = row.cells
                x = self.cx if self.wrap_pending else self.cx - 1
                if x >= len(cells):
                    x = len(cells) - 1
                while x >= 0 and cells[x] == "":
                    x -= 1
                # A tab is nothing a mark can stand on.
                if x >= 0 and not cells[x].endswith("\t"):
                    cells[x] += ch
                continue
            # What a repeat repeats: a mark alone would be no character.
            self.last_char = ch
            if self.wrap_pending:
                if self.autowrap:
                    self._wrap()
                else:
                    self.wrap_pending = False
            if width == 2 and self.cx >= cols - 1:
                if cols < 2:
                    continue
                if not self.autowrap:
                    self.cx = cols - 2
                else:
                    row = self.grid[self.cy]
                    self._pad(row, cols - 1)
                    if len(row.cells) == cols - 1:
                        row.cells.append("")
                        if row.styles is not None:
                            row.styles.append(0)
                    self._wrap()
            row = self.grid[self.cy]
            cx = self.cx
            if self.insert:
                self.insert_chars(width)
            if width == 1:
                self._write(row, cx, ch, 1, sid)
            else:
                self._write(row, cx, (ch, ""), 2, sid)
            cx += width
            if cx >= cols:
                cx = cols - 1
                self.wrap_pending = self.autowrap
            self.cx = cx

    def bulk_ok(self) -> bool:
        """True when :meth:`bulk_lines` may be used."""
        grid = self.grid
        cy = self.cy
        store = self.store
        # A line that is open in the store goes on with the first row of
        # the grid: the careful path knows how to join the two.
        return (store is not None and store.open_parts is None
                and self.cx == 0
                and not self.wrap_pending and self.sid == 0
                and self.bce == 0 and self.top == 0
                and self.bot == self.rows - 1 and self.autowrap
                and not self.insert and self._xlat is None
                and cy == len(grid) - 1 and not grid[cy].cells
                and (cy == 0 or not grid[cy - 1].wrapped))

    def bulk_lines(self, text: str) -> None:
        """Print plain lines, each of them ending in CR LF.

        The fast path for large outputs: lines that scroll straight
        through the grid are appended to the store without ever becoming
        rows.  The caller checks :meth:`bulk_ok` and passes nothing but
        whole lines of simple characters (one cell each).  The outcome is
        that of printing the same text piece by piece.
        """
        lines = text[:-2].split("\r\n")
        cols = self.cols
        store = self.store
        keep = self.rows - 1
        # From the end, the lines that stay on the screen.
        used = 0
        first = len(lines)
        while first > 0 and used < keep:
            first -= 1
            length = len(lines[first])
            used += 1 if length <= cols else (length + cols - 1) // cols
        fresh = self.grid[:-1]
        if first:
            # The new lines fill the screen: the rows on it scroll off,
            # and so do the first of the new lines.
            for row in fresh:
                store.push_row(row)
            fresh = []
            head = lines[:first]
            if " \r\n" in text:
                head = [_frozen(line, cols) for line in head]
            store.extend_plain(head)
        for line in lines[first:]:
            length = len(line)
            if length <= cols:
                row = Row()
                row.cells = list(line)
                fresh.append(row)
            else:
                for start in range(0, length, cols):
                    row = Row()
                    row.cells = list(line[start:start + cols])
                    row.wrapped = start + cols < length
                    fresh.append(row)
        fresh.append(Row())
        excess = max(0, len(fresh) - self.rows)
        for row in fresh[:excess]:
            store.push_row(row)
        del fresh[:excess]
        self.grid = fresh
        self.cy = len(fresh) - 1
        printed = text.rstrip("\r\n")
        if printed:
            self.last_char = printed[-1]

    def repeat_last(self, count: int) -> None:
        """Print the last character printed ``count`` times more."""
        char = self.last_char
        if char and count > 0:
            # As it was printed: not through the character set again.
            xlat = self._xlat
            self._xlat = None
            self.put_text(char * min(count, self.cols * self.rows))
            self._xlat = xlat

    # ------------------------------------------------------------------
    # C0 controls
    # ------------------------------------------------------------------

    def linefeed(self) -> None:
        self.wrap_pending = False
        self._index()
        if self.newline_mode:
            self.cx = 0

    def carriage_return(self) -> None:
        self.cx = 0
        self.wrap_pending = False

    def backspace(self) -> None:
        if self.cx > 0:
            self.cx -= 1
        self.wrap_pending = False

    def _attach_tab(self) -> None:
        """Keep a tab that has no cell to occupy.

        At the right margin a tab moves nothing, yet the program did
        print it.  It joins the character before it, so the text of the
        line - what copying takes - still has it.
        """
        # With a wrap pending the cursor sits on the last character
        # written; otherwise its own cell is still to be written.
        x = self.cx if self.wrap_pending else self.cx - 1
        if x < 0:
            return
        row = self.grid[self.cy]
        self._pad(row, x + 1)
        cells = row.cells
        while x > 0 and cells[x] == "":
            x -= 1
        cells[x] += "\t"

    def tab(self, count: int = 1) -> None:
        for _ in range(max(1, count)):
            cx = self.cx
            stop = self._next_tab(cx)
            if self.wrap_pending or stop <= cx:
                self._attach_tab()
                continue
            row = self.grid[self.cy]
            cells = row.cells
            length = len(cells)
            # Remember the tab itself when it crosses nothing but blanks,
            # so copying gives the tab back.  Anything else stays as it
            # is, and that includes blanks with a colour and the second
            # cell of a wide character or of an earlier tab.
            if length <= cx or (cells[cx] != "" and row.is_blank(cx, stop)):
                self._pad(row, cx)
                cells[cx:stop] = ["\t"] + [""] * (stop - cx - 1)
                styles = row.styles
                if styles is not None:
                    styles.extend([0] * (len(cells) - len(styles)))
            self.cx = stop

    def back_tab(self, count: int = 1) -> None:
        self.wrap_pending = False
        for _ in range(max(1, count)):
            self.cx = self._prev_tab(self.cx)

    def reverse_index(self) -> None:
        self.wrap_pending = False
        if self.cy == self.top:
            self._scroll_down(1)
        elif self.cy > 0:
            self.cy -= 1

    def index(self) -> None:
        self.wrap_pending = False
        self._index()

    def next_line(self) -> None:
        self.wrap_pending = False
        self._index()
        self.cx = 0

    # ------------------------------------------------------------------
    # cursor movement
    # ------------------------------------------------------------------

    def cursor_up(self, count: int = 1) -> None:
        self.wrap_pending = False
        limit = self.top if self.cy >= self.top else 0
        self.cy = max(limit, self.cy - max(1, count))

    def cursor_down(self, count: int = 1) -> None:
        self.wrap_pending = False
        limit = self.bot if self.cy <= self.bot else self.rows - 1
        self.cy = min(limit, self.cy + max(1, count))
        self._ensure_row(self.cy)

    def cursor_forward(self, count: int = 1) -> None:
        self.wrap_pending = False
        self.cx = min(self.cols - 1, self.cx + max(1, count))

    def cursor_back(self, count: int = 1) -> None:
        self.wrap_pending = False
        self.cx = max(0, self.cx - max(1, count))

    def cursor_to(self, row: int, col: int) -> None:
        """Move to a zero-based position (relative to the margin in origin
        mode)."""
        self.wrap_pending = False
        if self.origin:
            row = min(self.bot, max(self.top, row + self.top))
        else:
            row = min(self.rows - 1, max(0, row))
        self.cy = row
        self.cx = min(self.cols - 1, max(0, col))
        self._ensure_row(row)

    def cursor_col(self, col: int) -> None:
        self.wrap_pending = False
        self.cx = min(self.cols - 1, max(0, col))

    def cursor_row(self, row: int) -> None:
        self.cursor_to(row, self.cx)

    def save_cursor(self) -> None:
        self._saved = (self.cx, self.cy, self.wrap_pending, self.fg, self.bg,
                       self.attrs, self.origin, list(self._charsets),
                       self._gl)

    def restore_cursor(self) -> None:
        saved = self._saved
        if saved is None:
            self.cx = 0
            self.cy = 0
            self.wrap_pending = False
            self.origin = False
            return
        (cx, cy, pending, self.fg, self.bg, self.attrs, self.origin,
         charsets, self._gl) = saved
        self._charsets = list(charsets)
        self._update_charset()
        self._update_style()
        if self.origin:
            # The region may be another one by now: stay inside it.
            cy = min(self.bot, max(self.top, cy))
        # A resize keeps the saved position on the screen.
        self.cy = cy
        self.cx = cx
        self.wrap_pending = pending
        self._ensure_row(cy)

    # ------------------------------------------------------------------
    # erasing and editing
    # ------------------------------------------------------------------

    def erase_line(self, mode: int = 0) -> None:
        row = self.grid[self.cy]
        cx = self.cx
        bce = self.bce
        self.wrap_pending = False
        if mode == 0:
            cells = row.cells
            if cx < len(cells):
                if cells[cx] == "":
                    self._break(row, cx)
                del cells[cx:]
                if row.styles is not None:
                    del row.styles[cx:]
            if bce:
                self._fill(row, cx, self.cols, bce)
            row.wrapped = False
        elif mode == 1:
            end = min(cx + 1, self.cols)
            if not bce:
                end = min(end, len(row.cells))
            self._fill(row, 0, end, bce)
        elif mode == 2:
            row.cells = []
            row.styles = None
            row.wrapped = False
            if bce:
                self._fill(row, 0, self.cols, bce)

    def erase_display(self, mode: int = 0) -> None:
        grid = self.grid
        cy = self.cy
        self.wrap_pending = False
        if mode == 0:
            self.erase_line(0)
            del grid[cy + 1:]
            if self.bce:
                for _ in range(self.rows - len(grid)):
                    grid.append(self._blank_row())
        elif mode == 1:
            for i in range(cy):
                grid[i] = self._blank_row()
            self.erase_line(1)
        elif mode == 2:
            self.clear_count += 1
            del grid[cy + 1:]
            for i in range(len(grid)):
                grid[i] = self._blank_row()
            if self.bce:
                for _ in range(self.rows - len(grid)):
                    grid.append(self._blank_row())
        elif mode == 3:
            self.clear_count += 1
            if self.store is not None:
                self.store.clear()

    def erase_chars(self, count: int = 1) -> None:
        self.wrap_pending = False
        row = self.grid[self.cy]
        end = min(self.cx + max(1, count), self.cols)
        if not self.bce:
            end = min(end, len(row.cells))
        self._fill(row, self.cx, end, self.bce)

    def insert_chars(self, count: int = 1) -> None:
        self.wrap_pending = False
        row = self.grid[self.cy]
        cells = row.cells
        cx = self.cx
        if cx >= len(cells) and not self.bce:
            return
        count = min(max(1, count), self.cols - cx)
        self._pad(row, cx)
        if cx < len(cells) and cells[cx] == "":
            self._break(row, cx)
        cells[cx:cx] = " " * count
        if row.styles is not None:
            row.styles[cx:cx] = [self.bce] * count
        elif self.bce:
            row.styles = [0] * len(cells)
            row.styles[cx:cx + count] = [self.bce] * count
        self._clip(row, self.cols)
        row.wrapped = False

    def delete_chars(self, count: int = 1) -> None:
        self.wrap_pending = False
        row = self.grid[self.cy]
        cells = row.cells
        cx = self.cx
        length = len(cells)
        if cx >= length:
            return
        end = min(cx + max(1, count), length)
        if cells[cx] == "":
            self._break(row, cx)
        if end < length and cells[end] == "":
            self._break(row, end)
        del cells[cx:end]
        if row.styles is not None:
            del row.styles[cx:end]
        if self.bce:
            self._fill(row, len(cells), self.cols, self.bce)
        row.wrapped = False

    def insert_lines(self, count: int = 1) -> None:
        cy = self.cy
        if not self.top <= cy <= self.bot:
            return
        grid = self.grid
        count = min(max(1, count), self.bot - cy + 1)
        for _ in range(count):
            if self.bot < len(grid):
                del grid[self.bot]
            grid.insert(cy, self._blank_row())
        self.cx = 0
        self.wrap_pending = False

    def delete_lines(self, count: int = 1) -> None:
        cy = self.cy
        if not self.top <= cy <= self.bot:
            return
        grid = self.grid
        count = min(max(1, count), self.bot - cy + 1)
        if self.bce:
            self._ensure_row(self.bot)      # as in _scroll_up
        for _ in range(count):
            if cy < len(grid):
                del grid[cy]
            if len(grid) >= self.bot:
                grid.insert(self.bot, self._blank_row())
        self._ensure_row(cy)
        self.cx = 0
        self.wrap_pending = False

    def scroll_up(self, count: int = 1) -> None:
        self._scroll_up(max(1, count))

    def scroll_down(self, count: int = 1) -> None:
        self._scroll_down(max(1, count))

    def align_test(self) -> None:
        """DECALN: fill the screen with ``E``."""
        self.grid = []
        for _ in range(self.rows):
            row = Row()
            row.cells = ["E"] * self.cols
            self.grid.append(row)
        self.cx = self.cy = 0
        self.wrap_pending = False

    @staticmethod
    def _clip(row: Row, cols: int) -> None:
        """Cut a row down to ``cols`` cells."""
        cells = row.cells
        if len(cells) <= cols:
            return
        del cells[cols:]
        if row.styles is not None:
            del row.styles[cols:]
        # A wide character cut in half loses its meaning.  A tab cut short
        # is still a tab, and a continuation cell still has its owner.
        head = cells[cols - 1]
        if head and char_width(head[0]) == 2:
            cells[cols - 1] = " "

    # ------------------------------------------------------------------
    # modes and attributes
    # ------------------------------------------------------------------

    def set_sgr(self, params: str) -> None:
        # Programs repeat the same few sequences; remember where each one
        # leads from each style.
        key = (params, self.sid)
        hit = _sgr_cache.get(key)
        if hit is None:
            fg, bg, attrs = _style.apply_sgr(params, self.fg, self.bg,
                                             self.attrs)
            if fg is None and bg is None and not attrs:
                sid = bce = 0
            else:
                sid = _style.intern(fg, bg, attrs)
                bce = _style.intern(None, bg, 0) if bg is not None else 0
            if len(_sgr_cache) > 20000:
                _sgr_cache.clear()
            hit = _sgr_cache[key] = (fg, bg, attrs, sid, bce)
        self.fg, self.bg, self.attrs, self.sid, self.bce = hit

    def _update_style(self) -> None:
        if self.fg is None and self.bg is None and not self.attrs:
            self.sid = 0
            self.bce = 0
        else:
            self.sid = _style.intern(self.fg, self.bg, self.attrs)
            self.bce = _style.intern(None, self.bg, 0) if self.bg is not None \
                else 0

    def set_margins(self, top: int | None, bot: int | None) -> None:
        """DECSTBM with zero-based inclusive margins (``None`` = default)."""
        top = 0 if top is None else top
        bot = self.rows - 1 if bot is None else min(bot, self.rows - 1)
        if top < bot:
            self.top = top
            self.bot = bot
        else:
            self.top = 0
            self.bot = self.rows - 1
        self.cursor_to(0, 0)

    def set_origin(self, on: bool) -> None:
        self.origin = on
        self.cursor_to(0, 0)

    def tab_set(self) -> None:
        if self.tabs is None:
            self.tabs = set(range(TAB_SIZE, self.cols, TAB_SIZE))
        self.tabs.add(self.cx)

    def tab_clear(self, mode: int = 0) -> None:
        if self.tabs is None:
            self.tabs = set(range(TAB_SIZE, self.cols, TAB_SIZE))
        if mode == 0:
            self.tabs.discard(self.cx)
        elif mode == 3:
            self.tabs.clear()

    def designate_charset(self, slot: int, name: str) -> None:
        if slot in (0, 1):
            self._charsets[slot] = name
            self._update_charset()

    def shift_charset(self, slot: int) -> None:
        self._gl = slot
        self._update_charset()

    def charsets(self) -> tuple[str, str, int]:
        """The two designated character sets and the slot shifted in."""
        return self._charsets[0], self._charsets[1], self._gl

    def _update_charset(self) -> None:
        self._xlat = DEC_GRAPHICS if self._charsets[self._gl] == "0" else None

    def soft_reset(self) -> None:
        """DECSTR."""
        self.fg = self.bg = None
        self.attrs = 0
        self._update_style()
        self.top = 0
        self.bot = self.rows - 1
        self.autowrap = True
        self.insert = False
        self.origin = False
        self.cursor_visible = True
        self._charsets = ["B", "B"]
        self._gl = 0
        self._xlat = None
        self._saved = None

    def reset(self) -> None:
        """Full reset: an empty screen in its initial state."""
        self.soft_reset()
        self.grid = [Row()]
        self.cx = self.cy = 0
        self.wrap_pending = False
        self.newline_mode = False
        self.cursor_shape = 0
        self.tabs = None
        self.last_char = ""

    # ------------------------------------------------------------------
    # resizing
    # ------------------------------------------------------------------

    def resize(self, cols: int, rows: int) -> None:
        cols = max(1, cols)
        rows = max(1, rows)
        if cols == self.cols and rows == self.rows:
            return
        saved = self._saved
        # A cursor saved where the cursor still is - the main screen while
        # a full-screen program runs - has to move along with it.
        follows = saved is not None and saved[0] == self.cx \
            and saved[1] == self.cy
        if cols != self.cols:
            if self.reflow and self.store is not None:
                self._rewrap(cols)
            else:
                for row in self.grid:
                    self._clip(row, cols)
                    row.wrapped = False
                self.cx = min(self.cx, cols - 1)
                self.wrap_pending = False
            self.cols = cols
            if self.tabs is not None:
                self.tabs = {t for t in self.tabs if t < cols}
        self.rows = rows
        grid = self.grid
        excess = len(grid) - rows
        from_top = 0
        if excess > 0:
            # Keep the cursor row on screen; drop from the top first.
            from_top = min(excess, self.cy)
            store = self.store
            for row in grid[:from_top]:
                if store is not None:
                    store.push_row(row)
            del grid[:from_top]
            self.cy -= from_top
            del grid[rows:]             # those below the cursor go last
        # Whatever the last row went on with is not there (any more).
        grid[-1].wrapped = False
        self.top = 0
        self.bot = rows - 1
        if saved is not None:
            state = list(saved)
            if follows:
                state[0], state[1], state[2] = (self.cx, self.cy,
                                                self.wrap_pending)
            else:
                state[0] = min(state[0], cols - 1)
                state[1] = min(max(0, state[1] - from_top), rows - 1)
                state[2] = False
            self._saved = tuple(state)

    def _rewrap(self, cols: int) -> None:
        """Re-wrap the grid (and the open line) to a new width."""
        store = self.store
        # Collect logical lines as lists of (cell_text, style) clusters.
        lines: list[list[tuple[str, int]]] = []
        current: list[tuple[str, int]] = []
        opened = store.take_open()
        if opened is not None:
            text, runs = opened
            sids = _expand_runs(runs, len(text))
            for i, ch in enumerate(text):
                if char_width(ch) == 0 and ch != "\t" and current:
                    head, sid = current[-1]
                    current[-1] = (head + ch, sid)
                else:
                    current.append((ch, sids[i] if sids else 0))
        # For every row, its logical line and how many clusters of that
        # line come before it.
        starts: list[tuple[int, int]] = []
        for row in self.grid:
            starts.append((len(lines), len(current)))
            styles = row.styles
            for x, cell in enumerate(row.cells):
                if cell != "":
                    current.append((cell, styles[x] if styles else 0))
            if not row.wrapped:
                lines.append(current)
                current = []
        if current:
            lines.append(current)
        # The cursor: the cluster it is on, or how far it is beyond the
        # last one of its row.
        cells = self.grid[self.cy].cells
        after = 1 if self.wrap_pending else 0
        cursor_line, cursor_cluster = starts[self.cy]
        cursor_cluster += len([c for c in cells[:self.cx + after] if c != ""])
        cursor_extra = max(0, self.cx - len(cells))
        # Lay the lines out again.
        self.grid = grid = []
        self.wrap_pending = False
        for index, clusters in enumerate(lines):
            row = Row()
            x = 0
            cursor_here = index == cursor_line
            for k, (cell, sid) in enumerate(clusters):
                if cell[0] == "\t":
                    stop = min((x // TAB_SIZE + 1) * TAB_SIZE, cols)
                    width = max(1, stop - x)
                else:
                    width = min(char_width(cell[0]), cols)
                if x + width > cols:
                    # It does not fit: continue on the next row.  A wide
                    # character leaves the last cell of this one unused.
                    if x < cols:
                        row.cells.append("")
                        if row.styles is not None:
                            row.styles.append(0)
                    row.wrapped = True
                    grid.append(row)
                    row = Row()
                    x = 0
                    if cell[0] == "\t":
                        width = min(TAB_SIZE, cols)
                if cursor_here and k == cursor_cluster:
                    self.cy = len(grid)
                    self.cx = x
                row.cells.append(cell)
                row.cells.extend([""] * (width - 1))
                if sid or row.styles is not None:
                    if row.styles is None:
                        row.styles = [0] * (len(row.cells) - width)
                    row.styles.extend([sid] * width)
                x += width
            if cursor_here and cursor_cluster >= len(clusters):
                # After the last character: on a full row that is the
                # last column with a wrap pending.
                self.cy = len(grid)
                self.cx = min(cols - 1, x + cursor_extra)
                self.wrap_pending = x >= cols and cursor_extra == 0
            grid.append(row)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------

    def extent(self) -> int:
        """Grid rows worth showing: the last non-blank row or the cursor
        row, whichever is lower."""
        grid = self.grid
        n = len(grid)
        while n > 0 and grid[n - 1].is_blank():
            n -= 1
        return max(n, self.cy + 1)

    def content_rows(self) -> int:
        """Grid rows that are output.

        Trailing blank rows are not output, and neither is the row the
        cursor rests on when it sits at its very start: that is the
        position after the final newline, not a line of its own.
        """
        grid = self.grid
        n = len(grid)
        while n > 0 and grid[n - 1].is_blank():
            n -= 1
        cy = self.cy
        if cy >= n:
            n = cy if (self.cx == 0 and not self.wrap_pending) else cy + 1
        return n

    def live_lines(self, count: int | None = None
                   ) -> list[tuple[str, Runs | None]]:
        """Logical lines of the first ``count`` grid rows.

        When the store holds an open line, the first entry is its
        continuation only (the caller prefixes the stored part).
        """
        grid = self.grid
        if count is None:
            count = self.content_rows()
        out: list[tuple[str, Runs | None]] = []
        text_parts: list[str] = []
        run_parts: list[tuple[int, int]] = []
        offset = 0
        for row in grid[:count]:
            text, runs = row_text_runs(row, row.wrapped)
            if runs:
                if offset and (not run_parts or run_parts[-1][0] < offset):
                    run_parts.append((offset, 0))
                run_parts.extend((end + offset, sid) for end, sid in runs)
            text_parts.append(text)
            offset += len(text)
            if not row.wrapped:
                out.append(("".join(text_parts), _normalize_runs(run_parts)))
                text_parts = []
                run_parts = []
                offset = 0
        if text_parts:
            out.append(("".join(text_parts), _normalize_runs(run_parts)))
        return out

    def line_count(self) -> int:
        """How many lines :meth:`all_lines` gives, without building them:
        a block that is folded while it prints counts its lines for
        every frame, however many have scrolled off."""
        live = 0
        joined = False
        for row in self.grid[:self.content_rows()]:
            joined = row.wrapped
            if not joined:
                live += 1
        if joined:
            # The rows at the end belong to a line that is not over yet.
            live += 1
        store = self.store
        if store is None:
            return live
        if store.open_parts is not None and not live:
            # Of the line that is partly scrolled off, nothing is on the
            # screen any more: it counts all the same.
            live = 1
        return len(store.texts) + live

    def all_lines(self, count: int | None = None
                  ) -> list[tuple[str, Runs | None]]:
        """Every logical line of output so far: frozen, then live."""
        store = self.store
        out: list[tuple[str, Runs | None]] = []
        live = self.live_lines(count)
        if store is not None:
            styles = store.styles
            base = store.base
            out = [(text, styles.get(base + i))
                   for i, text in enumerate(store.texts)]
            if store.open_parts is not None:
                head = store.open_text()
                head_runs = store.open_line_runs()
                if live:
                    text, runs = live[0]
                    live[0] = (head + text,
                               join_runs(head_runs, len(head), runs))
                else:
                    live = [(head, head_runs)]
        out.extend(live)
        return out

    def lines_above_cursor(self) -> int:
        """Number of complete logical lines above the cursor row,
        counting the store."""
        count = self.store.end if self.store is not None else 0
        for row in self.grid[:self.cy]:
            if not row.wrapped:
                count += 1
        return count

    def flush_above_cursor(self) -> None:
        """Freeze every row above the cursor row into the store."""
        cy = self.cy
        if cy <= 0:
            return
        store = self.store
        if store is not None:
            for row in self.grid[:cy]:
                store.push_row(row)
            store.close_open()
        del self.grid[:cy]
        self.cy = 0
        if self._saved is not None:
            saved = list(self._saved)
            saved[1] = max(0, saved[1] - cy)
            self._saved = tuple(saved)

    def finish(self) -> None:
        """Freeze all output into the store and leave an empty grid."""
        store = self.store
        if store is not None:
            for row in self.grid[:self.content_rows()]:
                store.push_row(row)
            store.close_open()
        self.grid = [Row()]
        self.cx = self.cy = 0
        self.wrap_pending = False

    def push_lines(self, lines: list[tuple[str, Runs | None]]) -> None:
        """Append already-frozen lines after the current output."""
        self.finish()
        for text, runs in lines:
            self.store.append(text, runs)


def join_runs(head: Runs | None, head_len: int,
              tail: Runs | None) -> Runs | None:
    """Style runs of two pieces of text joined into one line."""
    if not tail:
        return head
    merged = list(head or ())
    if head_len and (not merged or merged[-1][0] < head_len):
        merged.append((head_len, 0))
    merged.extend((end + head_len, sid) for end, sid in tail)
    return _normalize_runs(merged)


def _expand_runs(runs: Runs | None, length: int) -> list[int] | None:
    """Per-character style ids for a run tuple."""
    if not runs:
        return None
    out: list[int] = []
    start = 0
    for end, sid in runs:
        out.extend([sid] * (end - start))
        start = end
    if start < length:
        out.extend([0] * (length - start))
    return out
