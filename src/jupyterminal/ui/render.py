"""Drawing terminal text on a cell grid.

Text reaches the painter as *segments*: ``(column, text, style_id,
cells)``.  A segment's text is drawn starting at its column and covers
``cells`` cells.  Three builders make segments - from a slice of a simple
logical line, from a slice of a complex one, and from a grid row - and
:meth:`Renderer.paint_row` draws them.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from itertools import groupby

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QFont, QFontDatabase, QFontMetricsF, QPainter, QPen

from ..term import style as _style
from ..term.charwidth import char_width


def simple_segments(text: str, runs, start: int, end: int) -> list[tuple]:
    """Segments for ``text[start:end]`` of a one-cell-per-character line."""
    if end <= start:
        return []
    if not runs:
        return [(0, text[start:end], 0, end - start)]
    out = []
    # The first run that ends after ``start``: a one-item tuple sorts
    # just before every run with that end.
    i = bisect_left(runs, (start + 1,))
    pos = start
    count = len(runs)
    while i < count and pos < end:
        stop, sid = runs[i]
        stop = min(stop, end)
        out.append((pos - start, text[pos:stop], sid, stop - pos))
        pos = stop
        i += 1
    if pos < end:
        out.append((pos - start, text[pos:end], 0, end - pos))
    return out


def complex_segments(text: str, runs, start: int, end: int,
                     columns: list[int], cols: int) -> list[tuple]:
    """Segments for a slice of a line with tabs, wide or zero-width
    characters; ``columns`` comes from the line's layout."""
    out: list[tuple] = []
    runs = runs or ()
    count = len(runs)
    ri = bisect_left(runs, (start + 1,))
    i = start
    width_of = char_width
    while i < end:
        ch = text[i]
        while ri < count and runs[ri][0] <= i:
            ri += 1
        sid = runs[ri][1] if ri < count else 0
        col = columns[i]
        if ch == "\t":
            nxt = columns[i + 1] if i + 1 < end else cols
            out.append((col, "", sid, max(1, nxt - col)))
            i += 1
            continue
        width = width_of(ch) or 1
        j = i + 1
        while j < end and text[j] != "\t" and width_of(text[j]) == 0:
            j += 1
        if width == 1 and j == i + 1 and out:
            pcol, ptext, psid, pcells = out[-1]
            if psid == sid and ptext and pcells == len(ptext) \
                    and pcol + pcells == col:
                out[-1] = (pcol, ptext + ch, sid, pcells + 1)
                i = j
                continue
        out.append((col, text[i:j], sid, width))
        i = j
    return out


def grid_segments(row) -> list[tuple]:
    """Segments for a screen row."""
    cells = row.cells
    if not cells:
        return []
    styles = row.styles
    text = "".join(cells)
    if len(text) == len(cells) and "" not in cells and "\t" not in text:
        if styles is None:
            return [(0, text, 0, len(text))]
        out = []
        pos = 0
        for sid, group in groupby(styles):
            n = len(list(group))
            out.append((pos, text[pos:pos + n], sid, n))
            pos += n
        return out
    out = []
    count = len(cells)
    x = 0
    while x < count:
        cell = cells[x]
        sid = styles[x] if styles is not None else 0
        span = 1
        while x + span < count and cells[x + span] == "":
            span += 1
        cell = cell.replace("\t", "")
        if span == 1 and len(cell) == 1 and out:
            pcol, ptext, psid, pcells = out[-1]
            if psid == sid and ptext and pcells == len(ptext) \
                    and pcol + pcells == x:
                out[-1] = (pcol, ptext + cell, sid, pcells + 1)
            else:
                out.append((x, cell, sid, 1))
        else:
            out.append((x, cell, sid, span))
        x += span
    return out


def cells_to_offset(cells: list[str], col: int) -> int:
    """Characters stored in the first ``col`` cells of a row."""
    return len("".join(cells[:max(0, col)]))


def cell_of(cells: list[str], offset: int) -> int:
    """The cell that holds character ``offset`` of a row: for a mark the
    cell of the character it is on, and beyond the text the blank cell
    that many places after it."""
    total = 0
    for x, cell in enumerate(cells):
        total += len(cell)
        if total > offset:
            return x
    return len(cells) + max(0, offset - total)


def offset_to_cells(cells: list[str], offset: int) -> int:
    """The cell at which character ``offset`` of a row starts."""
    if offset <= 0:
        return 0
    total = 0
    for x, cell in enumerate(cells):
        if total >= offset and cell != "":
            return x
        total += len(cell)
    return len(cells) + max(0, offset - total)


class Renderer:
    """Fonts, cell metrics and the actual drawing."""

    def __init__(self, theme, family: str = "", size: float = 11.0) -> None:
        self.theme = theme
        self.set_font(family, size)

    def set_font(self, family: str, size: float) -> None:
        self.family = family
        self.size = max(4.0, min(72.0, size))
        if family:
            font = QFont(family)
        else:
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSizeF(self.size)
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setKerning(False)
        metrics = QFontMetricsF(font)
        advance = metrics.horizontalAdvance("M" * 16) / 16.0
        self.cw = max(1, math.ceil(advance - 0.05))
        self.fonts = []
        for bold, italic in ((False, False), (True, False), (False, True),
                             (True, True)):
            variant = QFont(font)
            variant.setBold(bold)
            variant.setItalic(italic)
            # Whatever the face, every glyph advances exactly one cell.
            own = QFontMetricsF(variant).horizontalAdvance("M" * 16) / 16.0
            gap = self.cw - own
            if abs(gap) > 0.001:
                variant.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing,
                                         gap)
            self.fonts.append(variant)
        self.font = self.fonts[0]
        self.metrics = QFontMetricsF(self.font)
        height = self.metrics.ascent() + self.metrics.descent()
        self.ch = max(1, math.ceil(height + 1.0))
        self.ascent = math.floor(self.metrics.ascent()
                                 + (self.ch - height) / 2.0)
        self._safe: set[str] = set()
        self._unsafe: set[str] = set()

    def _is_safe(self, text: str) -> bool:
        """True when ``text`` drawn in one call lands on the cell grid."""
        fresh = set(text) - self._safe
        # One character already known not to fit settles it.
        ok = not fresh & self._unsafe
        if ok:
            metrics = self.metrics
            cw = self.cw
            for ch in fresh:
                if ch.isascii() or (metrics.inFont(ch) and abs(
                        metrics.horizontalAdvance(ch) - cw) < 0.02):
                    self._safe.add(ch)
                else:
                    self._unsafe.add(ch)
                    ok = False
        return ok

    def _text(self, p: QPainter, x: float, base: float, text: str) -> None:
        if text.isascii() or self._is_safe(text):
            p.drawText(QPointF(x, base), text)
            return
        cw = self.cw
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            width = char_width(ch) or 1
            j = i + 1
            while j < n and char_width(text[j]) == 0:
                j += 1
            p.drawText(QPointF(x, base), text[i:j])
            x += width * cw
            i = j

    def paint_row(self, p: QPainter, x: float, y: float, segments,
                  highlights=None, overlays=None, fg=None,
                  bold: bool = False, italic: bool = False) -> None:
        """Draw one row.

        ``highlights`` are ``(col0, col1, color)`` bands painted behind
        the text; ``overlays`` are ``(col0, col1, background, foreground)``
        bands that repaint their text in another color (the cursor, the
        current search match).
        """
        if not segments and not highlights and not overlays:
            return
        cw = self.cw
        ch = self.ch
        resolve = self.theme.resolve
        for col, _, sid, cells in segments:
            if sid:
                background = resolve(sid)[1]
                if background is not None:
                    p.fillRect(QRectF(x + col * cw, y, cells * cw, ch),
                               background)
        if highlights:
            for c0, c1, color in highlights:
                if c1 > c0:
                    p.fillRect(QRectF(x + c0 * cw, y, (c1 - c0) * cw, ch),
                               color)
        base = y + self.ascent
        default = fg if fg is not None else self.theme.foreground
        current_font = None
        current_pen = None
        for col, text, sid, cells in segments:
            if sid:
                color, _, attrs = resolve(sid)
            else:
                color = default
                attrs = 0
            if text and not text.isspace():
                index = (1 if (bold or attrs & _style.BOLD) else 0) | (
                    2 if (italic or attrs & _style.ITALIC) else 0)
                if index != current_font:
                    p.setFont(self.fonts[index])
                    current_font = index
                if color is not current_pen:
                    p.setPen(color)
                    current_pen = color
                self._text(p, x + col * cw, base, text)
            if attrs & (_style.UNDERLINE | _style.STRIKE):
                p.setPen(QPen(color, 1))
                current_pen = None
                left = x + col * cw
                right = left + cells * cw
                if attrs & _style.UNDERLINE:
                    line_y = base + 2.5
                    p.drawLine(QPointF(left, line_y), QPointF(right, line_y))
                if attrs & _style.STRIKE:
                    line_y = y + ch * 0.55
                    p.drawLine(QPointF(left, line_y), QPointF(right, line_y))
        if overlays:
            for c0, c1, background, foreground in overlays:
                if c1 <= c0:
                    continue
                p.fillRect(QRectF(x + c0 * cw, y, (c1 - c0) * cw, ch),
                           background)
                p.setPen(foreground)
                for col, text, sid, cells in segments:
                    if col >= c1 or col + cells <= c0 or not text:
                        continue
                    attrs = resolve(sid)[2] if sid else 0
                    p.setFont(self.fonts[
                        (1 if (bold or attrs & _style.BOLD) else 0)
                        | (2 if (italic or attrs & _style.ITALIC) else 0)])
                    if cells == len(text):
                        a = max(c0, col)
                        b = min(c1, col + cells)
                        self._text(p, x + a * cw, base,
                                   text[a - col:b - col])
                    elif col >= c0:
                        self._text(p, x + col * cw, base, text)

    def paint_text(self, p: QPainter, x: float, y: float, text: str,
                   color, bold: bool = False, italic: bool = False) -> None:
        """Draw plain interface text on the cell grid."""
        if not text:
            return
        p.setFont(self.fonts[(1 if bold else 0) | (2 if italic else 0)])
        p.setPen(color)
        self._text(p, x, y + self.ascent, text)
