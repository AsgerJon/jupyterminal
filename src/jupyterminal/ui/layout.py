"""Laying out logical lines in rows of a given width.

Blocks store logical lines; how many rows a line takes depends on the
window.  This module answers, for a block and a width: how many rows is
the output tall, which line is at row *r*, and where on its row does each
character sit.  Nothing here touches Qt.

The cost model matters: a block may hold a million lines.  When every
line fits the width, rows and lines coincide and nothing is computed at
all.  Otherwise one cumulative array is built, and extended as lines
arrive.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left, bisect_right
from itertools import accumulate

from ..term.charwidth import TAB_SIZE, char_width, is_simple

_complex_cache: dict[tuple[str, int], tuple[list[int], list[int]]] = {}


def wrap_complex(text: str, cols: int) -> tuple[list[int], list[int]]:
    """Layout of a line that is not one cell per character.

    Returns ``(breaks, columns)``: the offset at which each row starts,
    and for every character the column it is drawn at.  Zero-width marks
    share the column of their base character.
    """
    key = (text, cols)
    hit = _complex_cache.get(key)
    if hit is not None:
        return hit
    breaks = [0]
    columns: list[int] = []
    x = 0
    for i, ch in enumerate(text):
        if ch == "\t":
            width = min((x // TAB_SIZE + 1) * TAB_SIZE, cols) - x
            if width <= 0:
                breaks.append(i)
                x = 0
                width = min(TAB_SIZE, cols)
            columns.append(x)
        else:
            width = char_width(ch)
            if width == 0:
                columns.append(x - 1 if x else 0)
                continue
            if x + width > cols and x:
                breaks.append(i)
                x = 0
            columns.append(x)
        x += width
    if len(_complex_cache) > 4096:
        _complex_cache.clear()
    hit = (breaks, columns)
    _complex_cache[key] = hit
    return hit


def simple_rows(length: int, cols: int) -> int:
    return (length + cols - 1) // cols or 1


def text_rows(text: str, cols: int) -> int:
    """Rows a line takes at this width."""
    if is_simple(text):
        return simple_rows(len(text), cols)
    return len(wrap_complex(text, cols)[0])


def row_span(text: str, cols: int, sub: int,
             complex_layout: tuple | None) -> tuple[int, int]:
    """Character range ``[start, end)`` of row ``sub`` of a line."""
    if complex_layout is None:
        start = sub * cols
        return start, min(len(text), start + cols)
    breaks = complex_layout[0]
    start = breaks[sub]
    end = breaks[sub + 1] if sub + 1 < len(breaks) else len(text)
    return start, end


def offset_at(text: str, cols: int, sub: int, col: int,
              complex_layout: tuple | None) -> int:
    """Character offset under column ``col`` of row ``sub`` (an offset
    one past the row's last character when the column is beyond it)."""
    start, end = row_span(text, cols, sub, complex_layout)
    if complex_layout is None:
        return min(end, start + max(0, col))
    columns = complex_layout[1]
    for i in range(start, end):
        ch = text[i]
        if ch == "\t":
            nxt = columns[i + 1] if i + 1 < end else cols
            width = max(1, nxt - columns[i])
        else:
            width = char_width(ch)
        if width and columns[i] <= col < columns[i] + width:
            return i
        if columns[i] > col:
            return i
    return end


def position_of(text: str, cols: int, offset: int,
                complex_layout: tuple | None) -> tuple[int, int]:
    """``(sub_row, column)`` of a character offset within a line."""
    if complex_layout is None:
        if offset >= len(text) and offset and offset % cols == 0:
            return offset // cols - 1, cols
        return offset // cols, offset % cols
    breaks, columns = complex_layout
    sub = bisect_right(breaks, offset) - 1
    if offset >= len(text):
        if not text:
            return 0, 0
        last = len(text) - 1
        return len(breaks) - 1, columns[last] + max(1, char_width(text[last]))
    return sub, columns[offset]


class OutputLayout:
    """The row index of a block's frozen lines at one width."""

    __slots__ = ("cols", "base", "count", "acc", "vis", "vis_count", "rows")

    def __init__(self) -> None:
        self.cols = 0
        self.base = 0
        #: Number of lines covered (in the shown sequence).
        self.count = 0
        #: Cumulative rows per shown line, or ``None`` when every line is
        #: exactly one row.
        self.acc: array | None = None
        #: Absolute indices shown by a filter, or ``None`` for all.
        self.vis: list[int] | None = None
        self.vis_count = 0
        self.rows = 0

    # -- building ----------------------------------------------------------

    def update(self, store, cols: int, vis: list[int] | None) -> None:
        """Bring the index up to date with the store."""
        if cols != self.cols or store.base != self.base or vis is not self.vis:
            self._build(store, cols, vis)
        elif vis is None:
            total = len(store.texts)
            if total < self.count:
                self._build(store, cols, vis)
            elif total > self.count:
                self._extend(store, total)
        elif len(vis) < self.vis_count:
            self._build(store, cols, vis)
        elif len(vis) > self.vis_count:
            self._extend_filtered(store, vis)

    @staticmethod
    def _rows_per_line(store, cols: int, start: int) -> list[int]:
        texts = store.texts
        base = store.base
        per = [(len(t) + cols - 1) // cols or 1 for t in texts[start:]]
        complex_lines = store.complex
        first = base + start
        # Whichever is shorter is walked: the complex lines of the whole
        # block, or the lines asked for.
        if len(complex_lines) < len(per):
            items = [(i, w) for i, w in complex_lines.items() if i >= first]
        else:
            items = [(i, complex_lines[i]) for i in
                     range(first, first + len(per)) if i in complex_lines]
        for index, width in items:
            per[index - first] = 1 if width <= cols else \
                len(wrap_complex(texts[index - base], cols)[0])
        return per

    def _build(self, store, cols: int, vis: list[int] | None) -> None:
        self.cols = cols
        self.base = store.base
        self.vis = vis
        if vis is None:
            self.vis_count = 0
            self.count = len(store.texts)
            if store.max_width <= cols:
                self.acc = None
                self.rows = self.count
            else:
                self.acc = array("q", accumulate(
                    self._rows_per_line(store, cols, 0)))
                self.rows = self.acc[-1] if self.acc else 0
            return
        self.vis_count = len(vis)
        self.count = len(vis)
        if store.max_width <= cols:
            self.acc = None
            self.rows = self.count
            return
        self.acc = array("q", accumulate(self._rows_of(store, cols, vis)))
        self.rows = self.acc[-1] if self.acc else 0

    @staticmethod
    def _rows_of(store, cols: int, indices) -> list[int]:
        texts = store.texts
        base = store.base
        complex_lines = store.complex
        per = []
        for index in indices:
            text = texts[index - base]
            if index in complex_lines:
                per.append(1 if complex_lines[index] <= cols
                           else len(wrap_complex(text, cols)[0]))
            else:
                per.append((len(text) + cols - 1) // cols or 1)
        return per

    def _extend_filtered(self, store, vis: list[int]) -> None:
        cols = self.cols
        old = self.vis_count
        self.vis_count = self.count = len(vis)
        if self.acc is None:
            if store.max_width <= cols:
                self.rows = self.count
                return
            self._build(store, cols, vis)
            return
        last = self.acc[-1] if self.acc else 0
        self.acc.extend(accumulate(self._rows_of(store, cols, vis[old:]),
                                   initial=last))
        # ``initial`` contributes one leading element that repeats the
        # previous total; drop it.
        del self.acc[old]
        self.rows = self.acc[-1]

    def _extend(self, store, total: int) -> None:
        cols = self.cols
        if self.acc is None:
            if store.max_width <= cols:
                self.count = total
                self.rows = total
                return
            self._build(store, cols, None)
            return
        per = self._rows_per_line(store, cols, self.count)
        last = self.acc[-1] if self.acc else 0
        self.acc.extend(accumulate(per, initial=last))
        # ``initial`` contributes one leading element that repeats the
        # previous total; drop it.
        del self.acc[self.count]
        self.count = total
        self.rows = self.acc[-1]

    # -- queries -----------------------------------------------------------

    def locate(self, row: int) -> tuple[int, int]:
        """``(ordinal, sub_row)`` of the shown line at ``row``."""
        acc = self.acc
        if acc is None:
            return row, 0
        k = bisect_right(acc, row)
        return k, row - (acc[k - 1] if k else 0)

    def first_row(self, ordinal: int) -> int:
        acc = self.acc
        if acc is None:
            return ordinal
        return acc[ordinal - 1] if ordinal else 0

    def line_rows(self, ordinal: int) -> int:
        acc = self.acc
        if acc is None:
            return 1
        return acc[ordinal] - (acc[ordinal - 1] if ordinal else 0)

    def index_of(self, ordinal: int) -> int:
        """Absolute line index of the shown line number ``ordinal``."""
        vis = self.vis
        return self.base + ordinal if vis is None else vis[ordinal]

    def ordinal_of(self, index: int) -> int:
        """Shown-line number of an absolute index (the nearest shown line
        at or after it when the line itself is hidden)."""
        vis = self.vis
        if vis is None:
            return index - self.base
        return bisect_left(vis, index)


def wrap_command(command: str, cols: int, first_cols: int,
                 max_rows: int) -> tuple[list[tuple[int, int, int]], bool]:
    """Rows of a command shown in a block header.

    Returns ``(rows, truncated)``; each row is ``(line, start, end)``.
    The first row is narrower: it shares its line with the block's
    status.
    """
    rows: list[tuple[int, int, int]] = []
    for number, line in enumerate(command.split("\n")):
        start = 0
        length = len(line)
        simple = is_simple(line)
        while True:
            width = max(4, first_cols if not rows else cols)
            if simple:
                end = min(length, start + width)
            else:
                end = start
                used = 0
                while end < length:
                    used += char_width(line[end]) if line[end] != "\t" \
                        else TAB_SIZE - used % TAB_SIZE
                    if used > width and end > start:
                        break
                    end += 1
            if len(rows) >= max_rows:
                return rows or [(0, 0, 0)], True
            rows.append((number, start, end))
            start = end
            if start >= length:
                break
    return rows, False


def slice_columns(text: str, start: int, end: int) -> list[int] | None:
    """Columns of the characters ``[start, end)`` drawn as one row.

    ``None`` for simple text, where the column is the offset into the
    slice.  Otherwise a list indexed by character offset (entries before
    ``start`` are unused).
    """
    if is_simple(text):
        return None
    columns = [0] * start
    x = 0
    for i in range(start, end):
        ch = text[i]
        if ch == "\t":
            columns.append(x)
            x += TAB_SIZE - x % TAB_SIZE
            continue
        width = char_width(ch)
        columns.append(x if width else max(0, x - 1))
        x += width
    return columns


def slice_offset(columns: list[int] | None, start: int, end: int,
                 col: int) -> int:
    """Character offset at column ``col`` of a row made by
    :func:`slice_columns`."""
    if columns is None:
        return min(end, start + max(0, col))
    for i in range(start, end):
        if columns[i] >= col:
            return i
    return end
