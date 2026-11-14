"""The terminal view.

One widget shows the three faces of a session: the block list with the
input area pinned beneath it, and - while a full-screen program runs - a
conventional terminal that takes over the whole view.  It also owns
everything the user does to blocks: selecting, copying, navigating,
finding, filtering.
"""

from __future__ import annotations

import os
import time
import traceback
from bisect import bisect_left, bisect_right

from PySide6.QtCore import (QEvent, QKeyCombination, QPointF, QRectF,
                            QSocketNotifier, Qt, QTimer, Signal)
from PySide6.QtGui import (QClipboard, QGuiApplication, QKeySequence,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QFileDialog, QMenu,
                               QMessageBox, QScrollBar, QToolTip, QWidget)

from ..session import BOTH, FilterSpec, copy_text, filter_indices
from ..session import find_matches
from ..session.text import COMMAND as COPY_COMMAND
from ..session.text import OUTPUT, clean_lines
from ..term import keys as K
from ..term.charwidth import TAB_SIZE, char_width, is_simple
from .bars import FILTER, FIND, SearchBar, confirm_paste
from .layout import (OutputLayout, offset_at, position_of, row_span,
                     slice_columns, slice_offset, text_rows, wrap_command,
                     wrap_complex)
from .render import (Renderer, cell_of, cells_to_offset, complex_segments,
                     grid_segments, offset_to_cells, simple_segments)
from .theme import Theme

PAD_LEFT = 14
BAR_W = 3
PAD_RIGHT = 8
SCROLL_W = 12
HEAD_TOP = 5
HEAD_BOTTOM = 5
BLOCK_BOTTOM = 6
IN_TOP = 8
IN_BOTTOM = 8
MAX_CMD_ROWS = 6
DRAG_THRESHOLD = 4

_Key = Qt.Key
_KEY_NAMES = {
    _Key.Key_Up: "Up", _Key.Key_Down: "Down", _Key.Key_Left: "Left",
    _Key.Key_Right: "Right", _Key.Key_Home: "Home", _Key.Key_End: "End",
    _Key.Key_Insert: "Insert", _Key.Key_Delete: "Delete",
    _Key.Key_PageUp: "PageUp", _Key.Key_PageDown: "PageDown",
    _Key.Key_Return: "Enter", _Key.Key_Enter: "Enter",
    _Key.Key_Tab: "Tab", _Key.Key_Backtab: "Tab",
    _Key.Key_Backspace: "Backspace", _Key.Key_Escape: "Escape",
    _Key.Key_F1: "F1", _Key.Key_F2: "F2", _Key.Key_F3: "F3",
    _Key.Key_F4: "F4", _Key.Key_F5: "F5", _Key.Key_F6: "F6",
    _Key.Key_F7: "F7", _Key.Key_F8: "F8", _Key.Key_F9: "F9",
    _Key.Key_F10: "F10", _Key.Key_F11: "F11", _Key.Key_F12: "F12",
}
_MODIFIER_KEYS = {
    _Key.Key_Shift, _Key.Key_Control, _Key.Key_Alt, _Key.Key_Meta,
    _Key.Key_AltGr, _Key.Key_CapsLock, _Key.Key_NumLock, _Key.Key_ScrollLock,
    _Key.Key_Super_L, _Key.Key_Super_R, _Key.Key_Hyper_L, _Key.Key_Hyper_R,
    _Key.Key_unknown,
}
#: Actions that stay available while a full-screen program has the view.
_FULLSCREEN_ACTIONS = {"copy", "paste", "paste_selection", "zoom_in",
                       "zoom_out", "zoom_reset"}

_PORTABLE = QKeySequence.SequenceFormat.PortableText


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s" if seconds < 10 else f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def _short_path(path: str) -> str:
    home = os.path.expanduser("~")
    if path == home:
        return "~"
    if home != "/" and path.startswith(home + "/"):
        return "~" + path[len(home):]
    return path


class Geo:
    """What the view knows about one block's layout."""

    __slots__ = ("hkey", "cmd_rows", "cmd_lines", "cmd_truncated",
                 "items", "header_h", "lay", "frozen_rows", "open_rows",
                 "live_rows", "live_shown", "out_rows", "height",
                 "fspec", "fbase", "fupto", "ftime", "flive",
                 "live_lines", "skey")

    def __init__(self) -> None:
        self.hkey = None
        self.skey = None
        self.cmd_rows: list[tuple[int, int, int]] = [(0, 0, 0)]
        self.cmd_lines: list[str] = [""]
        self.cmd_truncated = False
        self.items: list[tuple] = []
        self.header_h = 0
        self.lay = OutputLayout()
        self.frozen_rows = 0
        self.open_rows = 0
        self.live_rows = 0
        self.live_shown: list[int] | None = None
        self.out_rows = 0
        self.height = 0
        self.fspec = None
        self.fbase = 0
        self.fupto = 0
        self.ftime = 0.0
        #: The filter result was computed while the block still ran.
        self.flive = False
        #: Logical lines still on the screen that the filter shows.
        self.live_lines: list[int] | None = None


class Frame:
    """The vertical arrangement of all blocks at one moment."""

    __slots__ = ("blocks", "ids", "tops", "heights", "content_h", "view_h",
                 "input_h", "input_rows", "max_scroll")


class FindState:
    """An active "Find in block"."""

    __slots__ = ("spec", "matches", "lines", "current", "stamp")

    def __init__(self, spec: FilterSpec) -> None:
        self.spec = spec
        self.matches: list[tuple[int, int, int]] = []
        self.lines: list[int] = []
        self.current = -1
        self.stamp = -1


class TerminalView(QWidget):
    """Block list, input area and full-screen terminal."""

    titleChanged = Signal(str)
    sessionEnded = Signal()

    def __init__(self, session, config, parent: QWidget | None = None
                 ) -> None:
        super().__init__(parent)
        self.session = session
        self.config = config
        self.theme = Theme(config.colors)
        self.r = Renderer(self.theme, config.font_family, config.font_size)
        self._base_size = self.r.size
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 120)

        self.bar = QScrollBar(Qt.Orientation.Vertical, self)
        self.bar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bar.valueChanged.connect(self._bar_moved)
        self._style_scrollbar()

        self.cols = session.cols
        self.rows = session.rows
        self.scroll = 0
        self.follow = True
        # Block selection ("a block is selected" = keyboard is with the
        # block list).
        self.selected: list[int] = []
        self._cursor_block: int | None = None
        self._range_anchor: int | None = None
        # Text selection inside blocks: ((id, part, line, off) x 2).
        self._sel: tuple | None = None
        # Text selection on a screen (input area, full-screen terminal).
        self._gsel: tuple | None = None
        self._press: dict | None = None
        self._last_click = (0.0, None)
        self._click_count = 0
        self._hover: int | None = None
        self._buttons: list[tuple] = []
        self._frame: Frame | None = None
        self._frame_key = None
        self._ui = 0
        self._spin = 0
        self._find_block = None
        self._bar_targets: list = []
        self._seen_running = None
        self._flash = 0.0
        self._notifier = None

        self._keys_global: dict[str, str] = {}
        self._keys_blocks: dict[str, str] = {}
        self._build_keymap()

        self.search = SearchBar(self.theme, self)
        self.search.changed.connect(self._bar_changed)
        self.search.step.connect(self._bar_step)
        self.search.closed.connect(self._bar_closed)

        self._spinner = QTimer(self)
        self._spinner.setInterval(110)
        self._spinner.timeout.connect(self._tick)
        self._drag_timer = QTimer(self)
        self._drag_timer.setInterval(30)
        self._drag_timer.timeout.connect(self._drag_scroll)
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(40)
        self._resize_timer.timeout.connect(self._apply_grid)
        self._io_timer = QTimer(self)
        self._io_timer.setInterval(120)
        self._io_timer.timeout.connect(self._io_tick)
        self._find_timer = QTimer(self)
        self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(250)
        self._find_timer.timeout.connect(self._refresh_find)
        self._paint_failed = 0.0

        session.on_change = self._session_changed
        session.on_exit = self._session_exited
        session.on_title = self.titleChanged.emit
        session.on_bell = self._bell
        session.on_clipboard = self._program_clipboard
        session.on_drop = self._blocks_dropped
        session.term.color_query = self.theme.rgb
        session.term.cell_px = (self.r.cw, self.r.ch)

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------

    def attach_io(self) -> None:
        """Start reading the shell's output."""
        fd = self.session.fileno()
        if fd < 0:
            return
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._readable)
        self._io_timer.start()

    def _readable(self, *_) -> None:
        if not self.session.read_ready():
            self._stop_io()
            return
        pty = self.session.pty
        if pty is not None and pty.flush():
            self._io_timer.setInterval(4)

    def _io_tick(self) -> None:
        pty = self.session.pty
        if pty is None:
            return
        # Input the program has not taken yet (a large paste) is retried
        # quickly; otherwise this timer only watches for the shell's exit.
        backlog = pty.flush()
        self._io_timer.setInterval(4 if backlog else 120)
        self.session.tick()
        if pty.poll() is not None:
            # The shell is gone; take what it left and stop, even if a
            # background job still holds the terminal open.
            for _ in range(64):
                if not self.session.read_ready():
                    break
            self.session.close()
            self._stop_io()

    def _stop_io(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier = None
        self._io_timer.stop()

    def _session_changed(self) -> None:
        running = self.session.running
        if running is not self._seen_running:
            self._seen_running = running
            if running is not None:
                self.follow = True
        if running is not None and not self._spinner.isActive():
            self._spinner.start()
        if self._find_block is not None and not self._find_timer.isActive():
            self._find_timer.start()      # its output may have changed
        if self._gsel is not None:
            self._check_grid_selection()
        self.update()

    def _session_exited(self, status) -> None:
        self.update()
        self.sessionEnded.emit()

    def _blocks_dropped(self, blocks) -> None:
        gone = {b.id for b in blocks}
        self.selected = [i for i in self.selected if i not in gone]
        if self._cursor_block in gone:
            self._cursor_block = self.selected[-1] if self.selected else None
        if self._sel and (self._sel[0][0] in gone or self._sel[1][0] in gone):
            self._sel = None
        if self._find_block is not None and self._find_block.id in gone:
            self._bar_closed()
        if self._bar_targets:
            self._bar_targets = [b for b in self._bar_targets
                                 if b.id not in gone]
            if not self._bar_targets:
                self._bar_closed()      # nothing left for it to filter
        self._ui += 1

    def _bell(self) -> None:
        if self.config.bell == "visual":
            self._flash = time.monotonic()
            self.update()
            QTimer.singleShot(120, self.update)

    def _program_clipboard(self, target: str, text: str) -> None:
        if self.config.allow_clipboard_write:
            QGuiApplication.clipboard().setText(text)

    def _tick(self) -> None:
        if self.session.running is None:
            self._spinner.stop()
        self._spin += 1
        self.update()

    def _style_scrollbar(self) -> None:
        handle = self.theme.scrollbar.name()
        self.bar.setStyleSheet(f"""
            QScrollBar:vertical {{ background: transparent;
                                   width: {SCROLL_W}px; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {handle};
                min-height: 28px; border-radius: 3px; margin: 2px 3px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: transparent; }}
        """)

    # ------------------------------------------------------------------
    # keymap
    # ------------------------------------------------------------------

    def _build_keymap(self) -> None:
        self._keys_global.clear()
        self._keys_blocks.clear()
        for action, shortcuts in self.config.keys.items():
            table = self._keys_blocks if action.startswith("blocks.") \
                else self._keys_global
            for shortcut in shortcuts:
                portable = QKeySequence(shortcut).toString(_PORTABLE)
                if portable:
                    table[portable] = action

    @staticmethod
    def _combos(event) -> list[str]:
        key = event.key()
        mods = event.modifiers() & ~(Qt.KeyboardModifier.KeypadModifier
                                     | Qt.KeyboardModifier.GroupSwitchModifier)
        if key == _Key.Key_Backtab:
            key = _Key.Key_Tab
            mods |= Qt.KeyboardModifier.ShiftModifier
        out = [QKeySequence(QKeyCombination(mods, _Key(key)))
               .toString(_PORTABLE)]
        shift = Qt.KeyboardModifier.ShiftModifier
        if mods & shift and 0x20 < key < 0x7F and not (
                _Key.Key_A <= key <= _Key.Key_Z):
            # A shifted symbol: "Ctrl++" should match without naming Shift.
            out.append(QKeySequence(QKeyCombination(mods & ~shift,
                                                    _Key(key)))
                       .toString(_PORTABLE))
        return out

    # ------------------------------------------------------------------
    # geometry
    # ------------------------------------------------------------------

    def grid_for(self, width: int, height: int) -> tuple[int, int]:
        """The terminal size that fits a widget of this size."""
        cols = (width - PAD_LEFT - PAD_RIGHT - SCROLL_W) // self.r.cw
        rows = (height - (HEAD_TOP + self.r.ch + HEAD_BOTTOM)
                - BLOCK_BOTTOM - 2) // self.r.ch
        return max(10, cols), max(3, rows)

    def size_for(self, cols: int, rows: int) -> tuple[int, int]:
        """The widget size that shows a terminal of this size."""
        width = cols * self.r.cw + PAD_LEFT + PAD_RIGHT + SCROLL_W
        height = (rows * self.r.ch + HEAD_TOP + self.r.ch + HEAD_BOTTOM
                  + BLOCK_BOTTOM + 2)
        return width, height

    def resizeEvent(self, event) -> None:
        self.bar.setGeometry(self.width() - SCROLL_W, 0, SCROLL_W,
                             self.height())
        self._place_bar()
        cols, rows = self.grid_for(self.width(), self.height())
        if (cols, rows) != (self.cols, self.rows):
            if self.session.pty is None or not self.isVisible():
                self._apply_grid()
            else:
                self._resize_timer.start()
        self._ui += 1

    def _apply_grid(self) -> None:
        cols, rows = self.grid_for(self.width(), self.height())
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols = cols
        self.rows = rows
        self.session.term.cell_px = (self.r.cw, self.r.ch)
        self.session.resize(cols, rows, cols * self.r.cw, rows * self.r.ch)
        self._ui += 1
        self.update()

    def _place_bar(self) -> None:
        if self.search.isVisible():
            self.search.adjustSize()
            self.search.move(max(4, self.width() - SCROLL_W
                                 - self.search.width() - 10), 8)

    # -- per-block layout ----------------------------------------------------

    def _header_items(self, block) -> list[tuple]:
        """What the right end of a block header shows, right to left.

        Each item is ``(kind, text, cells)``.
        """
        items: list[tuple] = [("menu", "", 2), ("jump", "", 3)]
        if block.running and block.is_command:
            elapsed = block.elapsed()
            text = _duration(elapsed) if elapsed >= 1.0 else ""
            items.append(("spinner", text, len(text) + 3))
        elif block.is_command:
            if block.not_run:
                items.append(("dim", "not run", 8))
            elif block.exit_code not in (None, 0):
                text = f"exit {block.exit_code}"
                items.append(("fail", text, len(text) + 1))
            if block.duration is not None and block.duration > 1.0:
                text = _duration(block.duration)
                items.append(("dim", text, len(text) + 1))
        if block.fullscreen:
            items.append(("chip", "full-screen", 14))
        if block.filter is not None:
            shown, total = self._filter_counts(block)
            text = f"filter {shown}/{total}"
            items.append(("filter", text, len(text) + 5))
        if block.collapsed:
            count = block.line_count()
            text = f"{count} line{'s' if count != 1 else ''} hidden"
            items.append(("chip", text, len(text) + 3))
        if block.store.dropped:
            text = f"{block.store.dropped} earlier lines dropped"
            items.append(("chip", text, len(text) + 3))
        if block.is_command and self.cols >= 56 and block.cwd:
            context = _short_path(block.cwd)
            if block.branch:
                context += f" ({block.branch})"
            limit = max(12, self.cols // 3)
            if len(context) > limit:
                context = "…" + context[-(limit - 1):]
            items.append(("context", context, len(context) + 1))
        return items

    @staticmethod
    def _filter_counts(block) -> tuple[int, int]:
        """How many lines of a filtered block are shown, and how many it
        has: those that have left its screen and, while it still runs,
        those on it."""
        total = block.line_count()
        if block.visible is None:
            return total, total         # no filter that could be applied
        shown = len(block.visible)
        live = block.view.live_lines
        if live is not None:
            # Not the row the cursor waits on below the last line: the
            # filter may show it, but it is no line of output.
            shown += bisect_left(live, block.store.base + total)
        return shown, total

    def _measure(self, block) -> int:
        """Lay a block out for the current width; return its height."""
        geo = block.view
        if geo is None:
            geo = block.view = Geo()
        cols = self.cols
        ch = self.r.ch
        if block.screen is None:
            skey = (cols, ch, block.collapsed, block.filter,
                    block.fullscreen, block.exit_code)
            if geo.skey == skey:
                return geo.height
            geo.skey = skey
        # Also while the block is collapsed: its header counts the lines.
        self._refresh_filter(block, geo)
        items = self._header_items(block)
        hkey = (cols, block.command, tuple(i[2] for i in items))
        geo.items = items
        if geo.hkey != hkey:
            geo.hkey = hkey
            reserved = sum(i[2] for i in items) + 1
            if block.is_command:
                geo.cmd_lines = block.command.split("\n")
                geo.cmd_rows, geo.cmd_truncated = wrap_command(
                    block.command, cols, cols - reserved, MAX_CMD_ROWS)
            else:
                geo.cmd_lines = [""]
                geo.cmd_rows = [(0, 0, 0)]
                geo.cmd_truncated = False
            geo.header_h = HEAD_TOP + len(geo.cmd_rows) * ch + HEAD_BOTTOM
        if block.collapsed:
            geo.frozen_rows = geo.open_rows = geo.live_rows = 0
            geo.live_shown = None
            geo.out_rows = 0
        else:
            store = block.store
            geo.lay.update(store, cols, block.visible)
            geo.frozen_rows = geo.lay.rows
            screen = block.screen
            geo.live_shown = None
            if screen is None:
                geo.open_rows = 0
                geo.live_rows = 0
            else:
                # Rows of the line that is partly scrolled off, counted
                # the way they will be drawn.
                geo.open_rows = text_rows(store.open_text(), cols) \
                    if store.open_parts is not None else 0
                live = self._live_count(block)
                if block.cleared and block.running and block.is_command:
                    # After the program cleared the display it owns a
                    # whole screen, as on any terminal: what was there
                    # before stays above, out of view.
                    live = max(live, min(screen.rows, self.rows))
                if geo.live_lines is not None:
                    shown = set(geo.live_lines)
                    row_map = self._row_map(block)
                    geo.live_shown = [
                        r for r in range(min(live, len(row_map)))
                        if row_map[r][0] in shown]
                    if store.end not in shown:
                        geo.open_rows = 0
                    live = len(geo.live_shown)
                geo.live_rows = live
            geo.out_rows = geo.frozen_rows + geo.open_rows + geo.live_rows
        geo.height = geo.header_h + geo.out_rows * ch + (
            BLOCK_BOTTOM if geo.out_rows else 0) + 1
        return geo.height

    @staticmethod
    def _live_count(block) -> int:
        """Rows of a live block's screen that are shown."""
        screen = block.screen
        if block.is_command and block.running:
            return screen.extent()
        return screen.content_rows()

    def _live_texts(self, block) -> list[str]:
        """The logical lines still on a live block's screen; the first
        includes the part of it that has already scrolled off."""
        store = block.store
        tail = [text for text, _ in
                block.screen.live_lines(self._live_count(block))]
        if store.open_parts is not None:
            head = store.open_text()
            if tail:
                tail[0] = head + tail[0]
            else:
                tail = [head]
        return tail

    def _refresh_filter(self, block, geo: Geo) -> None:
        """Keep the filter's result in step with the block.

        ``block.visible`` lists the frozen lines shown, ``geo.live_lines``
        the ones still on a running block's screen.
        """
        spec = block.filter
        if spec is None or spec.compile() is None:
            block.visible = None
            geo.fspec = None
            geo.live_lines = None
            return
        store = block.store
        base = store.base
        fresh = geo.fspec is not spec or geo.fbase != base \
            or block.visible is None
        if block.screen is None:
            # Finished: decide once, exactly.
            if fresh or geo.flive or geo.fupto != store.end:
                block.visible = [i + base for i in
                                 filter_indices(store.texts, spec)]
                geo.fupto = store.end
            geo.fspec = spec
            geo.fbase = base
            geo.flive = False
            geo.live_lines = None
            return
        tail = self._live_texts(block)
        now = time.monotonic()
        if spec.context == 0:
            if fresh:
                block.visible = [i + base for i in
                                 filter_indices(store.texts, spec)]
            elif store.end > geo.fupto:
                start = geo.fupto - base
                block.visible.extend(
                    i + geo.fupto for i in
                    filter_indices(store.texts[start:], spec))
            geo.live_lines = [store.end + i
                              for i in filter_indices(tail, spec)]
            geo.fupto = store.end
        elif fresh or len(store.texts) < 20000 or now - geo.ftime > 0.3:
            # Context reaches across lines, and from the screen back into
            # what has scrolled off it: decide all of them together (not
            # on every frame once the block is large).
            count = len(store.texts)
            shown = filter_indices(store.texts + tail, spec)
            split = bisect_left(shown, count)
            block.visible = [i + base for i in shown[:split]]
            geo.live_lines = [i + base for i in shown[split:]]
            geo.fupto = store.end
            geo.ftime = now
        geo.fspec = spec
        geo.fbase = base
        geo.flive = True

    def _input_rows(self) -> int:
        session = self.session
        if not session.prompt_active or session.fullscreen:
            return 0
        return max(1, min(session.input.extent(), self.rows))

    def frame(self) -> Frame:
        """The current vertical layout (cached until something changes)."""
        session = self.session
        running = session.running
        key = (session.version, self._ui, self.width(), self.height(),
               self.cols, len(session.blocks),
               # A quiet command still counts its seconds.
               int(running.elapsed()) if running is not None else -1)
        frame = self._frame
        if frame is not None and self._frame_key == key:
            return frame
        frame = Frame()
        blocks = frame.blocks = list(session.blocks)
        frame.ids = [b.id for b in blocks]
        heights = frame.heights = [self._measure(b) for b in blocks]
        rows = frame.input_rows = self._input_rows()
        frame.input_h = rows * self.r.ch + IN_TOP + IN_BOTTOM if rows else 0
        frame.input_h = min(frame.input_h, self.height())
        view_h = frame.view_h = max(0, self.height() - frame.input_h)
        split = bisect_left(frame.ids, session.clear_before)
        after = sum(heights[split:])
        spacer = max(0, view_h - after)
        tops = []
        y = 0
        for index, height in enumerate(heights):
            if index == split:
                y += spacer
            tops.append(y)
            y += height
        if split == len(heights):
            y += spacer
        frame.tops = tops
        frame.content_h = y
        frame.max_scroll = max(0, y - view_h)
        if self.follow or self.scroll > frame.max_scroll:
            self.scroll = frame.max_scroll
        self._frame = frame
        self._frame_key = key
        self._sync_bar(frame)
        return frame

    def _sync_bar(self, frame: Frame) -> None:
        bar = self.bar
        blocked = bar.blockSignals(True)
        bar.setRange(0, frame.max_scroll)
        bar.setPageStep(max(1, frame.view_h))
        bar.setSingleStep(self.r.ch * 3)
        bar.setValue(int(self.scroll))
        bar.blockSignals(blocked)
        visible = frame.max_scroll > 0 and not self.session.fullscreen
        if bar.isVisible() != visible:
            bar.setVisible(visible)

    def _bar_moved(self, value: int) -> None:
        self._set_scroll(value)

    def _set_scroll(self, value: float) -> None:
        frame = self.frame()
        value = max(0, min(frame.max_scroll, int(value)))
        self.scroll = value
        self.follow = value >= frame.max_scroll
        self._sync_bar(frame)
        self.update()

    def scroll_to_bottom(self) -> None:
        self.follow = True
        self._ui += 1
        self.update()

    def _block_at_index(self, block_id: int):
        frame = self.frame()
        index = bisect_left(frame.ids, block_id)
        if index < len(frame.ids) and frame.ids[index] == block_id:
            return index, frame.blocks[index]
        return -1, None

    # ------------------------------------------------------------------
    # painting
    # ------------------------------------------------------------------

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception:  # noqa: BLE001 - a paint must never bring us down
            # An exception escaping a paint event aborts the application.
            now = time.monotonic()
            if now - self._paint_failed > 5.0:
                self._paint_failed = now
                traceback.print_exc()
        finally:
            p.end()

    def _paint(self, p: QPainter) -> None:
        theme = self.theme
        p.fillRect(self.rect(), theme.background)
        self._buttons = []
        session = self.session
        if session.fullscreen:
            if self.bar.isVisible():
                self.bar.setVisible(False)
            self._paint_screen(p, session.term.screen, PAD_LEFT, 2,
                               self.rows, True)
        else:
            frame = self.frame()
            self._paint_blocks(p, frame)
            if frame.input_h:
                self._paint_input(p, frame)
        if self._flash and time.monotonic() - self._flash < 0.1:
            p.fillRect(self.rect(), theme.selection)

    def _paint_blocks(self, p: QPainter, frame: Frame) -> None:
        view_h = frame.view_h
        if view_h <= 0:
            return
        p.save()
        p.setClipRect(QRectF(0, 0, self.width(), view_h))
        scroll = self.scroll
        tops = frame.tops
        blocks = frame.blocks
        index = max(0, bisect_right(tops, scroll) - 1)
        sticky = None
        while index < len(blocks):
            top = tops[index] - scroll
            if top >= view_h:
                break
            block = blocks[index]
            geo = block.view
            if top + geo.height > 0:
                self._paint_block(p, frame, block, geo, top, view_h)
                if top < 0 and geo.out_rows:
                    sticky = (block, geo, top)
            index += 1
        if sticky is not None:
            block, geo, top = sticky
            # The command stays pinned while its output is in view.
            y = min(0, top + geo.height - geo.header_h - 1)
            self._paint_header(p, frame, block, geo, y, True)
        p.restore()

    def _block_colors(self, block):
        theme = self.theme
        if block.id in self.selected:
            return (theme.selected_background, theme.selected_header,
                    theme.selected_bar)
        if block.failed:
            return (theme.failed_background, theme.failed_header,
                    theme.failed_bar)
        return None, theme.header_background, None

    def _paint_block(self, p, frame, block, geo, top, view_h) -> None:
        width = self.width()
        background, _, bar = self._block_colors(block)
        if background is not None:
            p.fillRect(QRectF(0, top, width, geo.height - 1), background)
        self._paint_header(p, frame, block, geo, top, False)
        if geo.out_rows:
            self._paint_output(p, block, geo, top + geo.header_h, view_h)
        if bar is not None:
            p.fillRect(QRectF(0, top, BAR_W, geo.height - 1), bar)
        p.fillRect(QRectF(0, top + geo.height - 1, width, 1),
                   self.theme.divider)

    def _paint_header(self, p, frame, block, geo, top, sticky) -> None:
        theme = self.theme
        r = self.r
        cw = r.cw
        ch = r.ch
        width = self.width() - (SCROLL_W if self.bar.isVisible() else 0)
        _, header_bg, bar = self._block_colors(block)
        p.fillRect(QRectF(0, top, self.width(), geo.header_h), header_bg)
        if sticky:
            p.fillRect(QRectF(0, top + geo.header_h, self.width(), 1),
                       theme.input_border)
            if bar is not None:
                p.fillRect(QRectF(0, top, BAR_W, geo.header_h), bar)
            self._buttons.append((QRectF(0, top, self.width(), geo.header_h),
                                  block, "sticky"))
        y = top + HEAD_TOP
        if block.collapsed:
            path = QPainterPath()
            path.moveTo(5, y + ch * 0.25)
            path.lineTo(10, y + ch * 0.5)
            path.lineTo(5, y + ch * 0.75)
            p.fillPath(path, theme.dim)
        # The right end, right to left.
        right = width - PAD_RIGHT
        hot = self._hover == block.id or block.id in self.selected
        for kind, text, cells in geo.items:
            span = cells * cw
            left = right - span
            rect = QRectF(left, y, span, ch)
            if kind == "menu":
                if hot:
                    self._paint_dots(p, rect)
                self._buttons.append((rect, block, "menu"))
            elif kind == "jump":
                if geo.height > frame.view_h:
                    self._paint_jump(p, rect)
                    self._buttons.append((rect, block, "jump"))
            elif kind == "spinner":
                self._paint_spinner(p, QRectF(right - 2 * cw, y, 2 * cw, ch))
                if text:
                    r.paint_text(p, left, y, text, theme.accent)
            elif kind == "fail":
                r.paint_text(p, left, y, text, theme.failed_foreground, True)
            elif kind == "chip" or kind == "filter":
                chip = QRectF(left, y + 1, span - cw, ch - 2)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(theme.chip_background)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                p.drawRoundedRect(chip, 4, 4)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                p.setBrush(Qt.BrushStyle.NoBrush)
                r.paint_text(p, left + cw, y, text, theme.chip_foreground)
                if kind == "filter":
                    cross = QRectF(chip.right() - 2 * cw, y, 2 * cw, ch)
                    r.paint_text(p, cross.left() + cw / 2, y, "×",
                                 theme.chip_foreground, True)
                    self._buttons.append((cross, block, "unfilter"))
            else:                        # "dim" and "context": plain grey
                r.paint_text(p, left, y, text, theme.dim)
            right = left
        # The command.
        if block.is_command:
            rows = geo.cmd_rows
            last = len(rows) - 1
            for number, (line, start, end) in enumerate(rows):
                text = geo.cmd_lines[line]
                row_y = y + number * ch
                columns = slice_columns(text, start, end)
                layout = None if columns is None else (None, columns)
                if columns is None:
                    segments = simple_segments(text, None, start, end)
                else:
                    segments = complex_segments(text, None, start, end,
                                                columns, self.cols)
                if number == last and geo.cmd_truncated and segments:
                    tail = segments[-1][0] + segments[-1][3] + 1
                    segments = segments + [(tail, "…", 0, 1)]
                highlights = self._selection_band(block, 0, line, text,
                                                  start, end, layout)
                r.paint_row(p, PAD_LEFT, row_y, segments, highlights,
                            fg=theme.header_foreground, bold=True)
        else:
            r.paint_text(p, PAD_LEFT, y, block.label, theme.dim, italic=True)

    def _paint_dots(self, p, rect: QRectF) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.theme.foreground)
        cx = rect.center().x()
        cy = rect.center().y()
        for dy in (-5, 0, 5):
            p.drawEllipse(QPointF(cx, cy + dy), 1.4, 1.4)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_jump(self, p, rect: QRectF) -> None:
        """An arrow down to a line: jump to the end of this block."""
        pen = QPen(self.theme.dim, 1.5)
        p.setPen(pen)
        cx = rect.center().x()
        top = rect.top() + 3
        bottom = rect.bottom() - 4
        p.drawLine(QPointF(cx, top), QPointF(cx, bottom - 2))
        p.drawLine(QPointF(cx - 4, bottom - 6), QPointF(cx, bottom - 2))
        p.drawLine(QPointF(cx + 4, bottom - 6), QPointF(cx, bottom - 2))
        p.drawLine(QPointF(cx - 5, bottom + 1), QPointF(cx + 5, bottom + 1))

    def _paint_spinner(self, p, rect: QRectF) -> None:
        size = min(rect.width(), rect.height()) - 6
        box = QRectF(rect.center().x() - size / 2,
                     rect.center().y() - size / 2, size, size)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(self.theme.accent, 2))
        p.drawArc(box, (-self._spin * 40 % 360) * 16, 270 * 16)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    # -- output --------------------------------------------------------------

    def _text_segments(self, text, runs, start, end, layout):
        if layout is None:
            return simple_segments(text, runs, start, end)
        return complex_segments(text, runs, start, end, layout[1], self.cols)

    def _paint_output(self, p, block, geo, top, view_h) -> None:
        ch = self.r.ch
        first = max(0, int(-top // ch))
        last = min(geo.out_rows, int((view_h - top) // ch) + 1)
        if first >= last:
            return
        cols = self.cols
        store = block.store
        texts = store.texts
        base = store.base
        styles = store.styles
        complex_lines = store.complex
        lay = geo.lay
        row = first
        frozen = geo.frozen_rows
        if row < frozen:
            ordinal, sub = lay.locate(row)
            stop = min(last, frozen)
            while row < stop:
                index = lay.index_of(ordinal)
                text = texts[index - base]
                layout = wrap_complex(text, cols) \
                    if index in complex_lines else None
                rows = lay.line_rows(ordinal)
                runs = styles.get(index)
                while sub < rows and row < stop:
                    self._paint_line_row(p, block, index, text, runs, sub,
                                         layout, top + row * ch)
                    sub += 1
                    row += 1
                ordinal += 1
                sub = 0
        open_end = frozen + geo.open_rows
        if row < last and row < open_end:
            text = store.open_text()
            runs = store.open_line_runs()
            layout = None if is_simple(text) else wrap_complex(text, cols)
            index = store.end
            while row < last and row < open_end:
                self._paint_line_row(p, block, index, text, runs,
                                     row - frozen, layout, top + row * ch)
                row += 1
        if row < last:
            self._paint_live(p, block, geo, top, row, last)

    def _paint_line_row(self, p, block, index, text, runs, sub, layout,
                        y) -> None:
        cols = self.cols
        start, end = row_span(text, cols, sub, layout)
        segments = self._text_segments(text, runs, start, end, layout)
        highlights = self._selection_band(block, 1, index, text, start, end,
                                          layout)
        overlays = None
        find = block.find
        if find is not None and find.matches:
            highlights, overlays = self._find_bands(
                find, index, text, start, end, layout, highlights)
        self.r.paint_row(p, PAD_LEFT, y, segments, highlights, overlays)

    def _columns(self, text, layout, start, end, a, b):
        """Columns covered by characters ``[a, b)`` on a row that shows
        ``[start, end)``; ``None`` when they do not meet.  ``b`` beyond
        the row's last character takes in the cell after it."""
        a = max(a, start)
        b = min(b, end + 1)
        if b <= a:
            return None
        if layout is None:
            return a - start, b - start
        columns = layout[1]

        def after(i: int) -> int:
            """The column just past character ``i``."""
            ch = text[i]
            if ch == "\t":
                if i + 1 < end:
                    return columns[i + 1]
                return columns[i] + TAB_SIZE - columns[i] % TAB_SIZE
            return columns[i] + (char_width(ch) or 1)

        row_end = after(end - 1) if end > start else 0
        c0 = columns[a] if a < end else row_end
        c1 = row_end + 1 if b > end else after(b - 1)
        return c0, max(c1, c0 + 1)

    def _selection_band(self, block, part, index, text, start, end, layout):
        sel = self._sel
        if sel is None:
            return None
        low, high = sel if sel[0] <= sel[1] else (sel[1], sel[0])
        key = (block.id, part, index)
        if key < low[:3] or key > high[:3]:
            return None
        a = low[3] if key == low[:3] else 0
        b = high[3] if key == high[:3] else len(text) + 1
        span = self._columns(text, layout, start, end, a, b)
        if span is None:
            return None
        return [(span[0], min(span[1], self.cols), self.theme.selection)]

    def _find_bands(self, find, index, text, start, end, layout, highlights):
        lines = find.lines
        k = bisect_left(lines, index)
        overlays = None
        theme = self.theme
        while k < len(lines) and lines[k] == index:
            _, a, b = find.matches[k]
            span = self._columns(text, layout, start, end, a, b)
            if span is not None and a < end and b > start:
                c1 = min(span[1], self.cols)
                if k == find.current:
                    overlays = (overlays or []) + [
                        (span[0], c1, theme.find_current,
                         theme.find_current_text)]
                else:
                    highlights = (highlights or []) + [
                        (span[0], c1, theme.find)]
            k += 1
        return highlights, overlays

    # -- live rows -----------------------------------------------------------

    @staticmethod
    def _row_map(block) -> list[tuple[int, int, int]]:
        """For each grid row of a live block: ``(line, first_offset,
        characters)`` in the block's logical lines."""
        screen = block.screen
        store = block.store
        line = store.end
        offset = store.open_len if store.open_parts is not None else 0
        out = []
        for row in screen.grid:
            count = len("".join(row.cells))
            out.append((line, offset, count))
            if row.wrapped:
                offset += count
            else:
                line += 1
                offset = 0
        return out

    def _paint_live(self, p, block, geo, top, row, last) -> None:
        screen = block.screen
        grid = screen.grid
        ch = self.r.ch
        offset = geo.frozen_rows + geo.open_rows
        shown = geo.live_shown
        row_map = None
        find = block.find
        if self._sel is not None or (find is not None and find.matches):
            row_map = self._row_map(block)
        session = self.session
        cursor_here = (screen is session.cursor_screen()
                       and not session.fullscreen and screen.cursor_visible)
        for display in range(row, last):
            k = display - offset
            index = shown[k] if shown is not None else k
            if index >= len(grid):
                continue
            grid_row = grid[index]
            segments = grid_segments(grid_row)
            highlights = None
            overlays = None
            if row_map is not None:
                line, first, count = row_map[index]
                highlights, overlays = self._live_bands(
                    block, grid_row, line, first, count, find)
            if cursor_here and index == screen.cy:
                overlays = (overlays or []) + self._cursor_overlay(
                    p, screen, PAD_LEFT, top + display * ch)
            self.r.paint_row(p, PAD_LEFT, top + display * ch, segments,
                             highlights, overlays)

    def _live_bands(self, block, grid_row, line, first, count, find):
        cells = grid_row.cells
        one_to_one = len(cells) == count
        highlights = None
        overlays = None

        def columns(a, b):
            a = max(a - first, 0)
            b = min(b - first, count + (0 if grid_row.wrapped else 1))
            if b <= a:
                return None
            if one_to_one:
                return a, b
            # From the cell that holds the first character, a mark's
            # being the cell of what it is on, to the cell at which the
            # first character after the last one starts.  That one lies
            # further on: b is past a.
            return cell_of(cells, a), offset_to_cells(cells, b)

        sel = self._sel
        if sel is not None:
            low, high = sel if sel[0] <= sel[1] else (sel[1], sel[0])
            key = (block.id, 1, line)
            if low[:3] <= key <= high[:3]:
                a = low[3] if key == low[:3] else 0
                b = high[3] if key == high[:3] else first + count + 1
                span = columns(a, b)
                if span is not None:
                    highlights = [(span[0], min(span[1], self.cols),
                                   self.theme.selection)]
        if find is not None and find.matches:
            lines = find.lines
            k = bisect_left(lines, line)
            while k < len(lines) and lines[k] == line:
                _, a, b = find.matches[k]
                if a < first + count and b > first:
                    span = columns(a, b)
                    if span is not None:
                        if k == find.current:
                            overlays = (overlays or []) + [
                                (span[0], span[1], self.theme.find_current,
                                 self.theme.find_current_text)]
                        else:
                            highlights = (highlights or []) + [
                                (span[0], span[1], self.theme.find)]
                k += 1
        return highlights, overlays

    def _cursor_overlay(self, p, screen, x, y) -> list[tuple]:
        """The cursor as an overlay band (or drawn directly when it is
        not a block)."""
        theme = self.theme
        cw = self.r.cw
        ch = self.r.ch
        cx = min(screen.cx, self.cols - 1)
        rect = QRectF(x + cx * cw, y, cw, ch)
        if not self.hasFocus():
            p.setPen(QPen(theme.cursor, 1))
            p.drawRect(rect.adjusted(0.5, 0.5, -0.5, -0.5))
            return []
        shape = screen.cursor_shape
        if shape in (3, 4):
            p.fillRect(QRectF(rect.left(), rect.bottom() - 2, cw, 2),
                       theme.cursor)
            return []
        if shape in (5, 6):
            p.fillRect(QRectF(rect.left(), rect.top(), 2, ch), theme.cursor)
            return []
        return [(cx, cx + 1, theme.cursor, theme.cursor_text)]

    def _paint_screen(self, p, screen, x, y, rows, with_cursor) -> None:
        """Draw a screen as a plain grid (input area, full-screen)."""
        grid = screen.grid
        ch = self.r.ch
        gsel = self._gsel if (self._gsel and self._gsel[0] is screen) \
            else None
        if gsel:
            low, high = sorted(gsel[1:3])
        for index in range(min(rows, len(grid))):
            k = index
            grid_row = grid[index]
            highlights = None
            if gsel and low[0] <= index <= high[0]:
                c0 = low[1] if index == low[0] else 0
                c1 = high[1] if index == high[0] else self.cols
                if c1 > c0:
                    highlights = [(c0, c1, self.theme.selection)]
            overlays = None
            row_y = y + k * ch
            if with_cursor and index == screen.cy and screen.cursor_visible:
                overlays = self._cursor_overlay(p, screen, x, row_y)
            self.r.paint_row(p, x, row_y, grid_segments(grid_row),
                             highlights, overlays)

    def _paint_input(self, p, frame: Frame) -> None:
        theme = self.theme
        top = frame.view_h
        p.fillRect(QRectF(0, top, self.width(), frame.input_h),
                   theme.input_background)
        p.fillRect(QRectF(0, top, self.width(), 1), theme.input_border)
        session = self.session
        owns_cursor = session.cursor_screen() is session.input
        self._paint_screen(p, session.input, PAD_LEFT, top + IN_TOP,
                           frame.input_rows, owns_cursor)

    # ------------------------------------------------------------------
    # hit testing
    # ------------------------------------------------------------------

    def _cell(self, x: float, y: float, top: float) -> tuple[int, int]:
        col = int((x - PAD_LEFT) // self.r.cw)
        row = int((y - top) // self.r.ch)
        return row, max(0, min(self.cols - 1, col))

    def _button_at(self, x: float, y: float):
        for rect, block, name in reversed(self._buttons):
            if rect.contains(QPointF(x, y)):
                return block, name
        return None, None

    def _pinned_header_at(self, x: float, y: float):
        """``(block, y inside its header)`` for a point on the header that
        is pinned to the top of the view, ``(None, 0)`` anywhere else."""
        for rect, block, name in self._buttons:
            if name == "sticky" and rect.contains(QPointF(x, y)):
                return block, y - rect.top()
        return None, 0

    def _block_under(self, y: float):
        """``(index, block, y inside the block)`` at a view position."""
        frame = self.frame()
        if y >= frame.view_h or y < 0 or not frame.blocks:
            return -1, None, 0
        content_y = y + self.scroll
        index = bisect_right(frame.tops, content_y) - 1
        if index < 0:
            return -1, None, 0
        inside = content_y - frame.tops[index]
        if inside >= frame.heights[index]:
            return -1, None, 0
        return index, frame.blocks[index], inside

    def _position_at(self, x: float, y: float):
        """The text position ``(id, part, line, offset)`` nearest to a
        view position, or ``None`` when there are no blocks."""
        frame = self.frame()
        if not frame.blocks:
            return None
        y = max(0, min(frame.view_h - 1, y))
        content_y = y + self.scroll
        index = bisect_right(frame.tops, content_y) - 1
        if index < 0:
            block = frame.blocks[0]
            return (block.id, 0, 0, 0)
        block = frame.blocks[index]
        geo = block.view
        inside = content_y - frame.tops[index]
        if inside < geo.header_h:
            return self._header_position(block, x, inside)
        col = self._column(x)
        if not geo.out_rows:
            return self._block_end(block, geo)
        row = int((inside - geo.header_h) // self.r.ch)
        if row >= geo.out_rows:
            return self._block_end(block, geo)
        line, offset = self._row_to_text(block, geo, row, col)
        return (block.id, 1, line, offset)

    def _column(self, x: float) -> int:
        """The cell boundary nearest to ``x``."""
        return max(0, min(self.cols, int(round((x - PAD_LEFT) / self.r.cw))))

    def _header_position(self, block, x: float, inside: float):
        """The position in a block's command at ``x``, ``inside`` pixels
        below the top of its header, wherever that header is drawn."""
        if not block.is_command:
            return (block.id, 0, 0, 0)
        geo = block.view
        row = int((inside - HEAD_TOP) // self.r.ch)
        row = max(0, min(len(geo.cmd_rows) - 1, row))
        line, start, end = geo.cmd_rows[row]
        columns = slice_columns(geo.cmd_lines[line], start, end)
        return (block.id, 0, line,
                slice_offset(columns, start, end, self._column(x)))

    def _block_end(self, block, geo):
        if not geo.out_rows:
            if block.is_command:
                lines = geo.cmd_lines
                return (block.id, 0, len(lines) - 1, len(lines[-1]))
            return (block.id, 0, 0, 0)
        line, offset = self._row_to_text(block, geo, geo.out_rows - 1,
                                         self.cols)
        return (block.id, 1, line, offset)

    def _row_to_text(self, block, geo, row, col) -> tuple[int, int]:
        """Logical ``(line, offset)`` at an output row and column."""
        cols = self.cols
        store = block.store
        if row < geo.frozen_rows:
            ordinal, sub = geo.lay.locate(row)
            index = geo.lay.index_of(ordinal)
            text = store.text(index)
            layout = wrap_complex(text, cols) \
                if index in store.complex else None
            return index, offset_at(text, cols, sub, col, layout)
        row -= geo.frozen_rows
        if row < geo.open_rows:
            text = store.open_text()
            layout = None if is_simple(text) else wrap_complex(text, cols)
            return store.end, offset_at(text, cols, row, col, layout)
        row -= geo.open_rows
        shown = geo.live_shown
        index = shown[row] if shown is not None else row
        row_map = self._row_map(block)
        if index >= len(row_map):
            index = len(row_map) - 1
        line, first, count = row_map[index]
        cells = block.screen.grid[index].cells
        return line, first + min(count, cells_to_offset(cells, col))

    def _text_to_row(self, block, geo, line, offset) -> int:
        """The output row showing a logical position (best effort)."""
        store = block.store
        cols = self.cols
        if line < store.end:
            ordinal = geo.lay.ordinal_of(line)
            ordinal = max(0, min(geo.lay.count - 1, ordinal))
            row = geo.lay.first_row(ordinal) if geo.lay.count else 0
            if geo.lay.count and geo.lay.index_of(ordinal) == line:
                text = store.text(line)
                layout = wrap_complex(text, cols) \
                    if line in store.complex else None
                row += position_of(text, cols, offset, layout)[0]
            return row
        if block.screen is None:
            return max(0, geo.out_rows - 1)
        if line == store.end and geo.open_rows and offset < store.open_len:
            # In the part of a line that has scrolled off the screen while
            # the line still goes on: it has rows of its own.
            text = store.open_text()
            layout = None if is_simple(text) else wrap_complex(text, cols)
            return geo.frozen_rows + position_of(text, cols, offset,
                                                 layout)[0]
        base = geo.frozen_rows + geo.open_rows
        for index, (row_line, first, count) in enumerate(
                self._row_map(block)):
            if row_line == line and first <= offset <= first + count:
                shown = geo.live_shown
                if shown is not None:
                    index = shown.index(index) if index in shown else 0
                return base + index
        return max(0, geo.out_rows - 1)

    # ------------------------------------------------------------------
    # text selection
    # ------------------------------------------------------------------

    def has_text_selection(self) -> bool:
        return self._sel is not None or (self._gsel is not None
                                         and self._check_grid_selection())

    def _check_grid_selection(self) -> bool:
        """Drop a selection on a screen that is no longer shown."""
        gsel = self._gsel
        if gsel is None:
            return False
        session = self.session
        screen = gsel[0]
        if session.fullscreen:
            shown = screen is session.term.screen
        else:
            shown = screen is session.input and session.prompt_active
        if not shown:
            self._gsel = None
        return shown

    def _block_lines(self, block) -> tuple[list[str], int]:
        """A block's logical lines and the absolute index of the first."""
        return block.lines(), block.store.base

    def selection_text(self) -> str:
        """The selected text, by the copy rules."""
        if self._gsel is not None and self._check_grid_selection():
            return self._grid_selection_text()
        sel = self._sel
        if sel is None:
            return ""
        low, high = sel if sel[0] <= sel[1] else (sel[1], sel[0])
        if low == high:
            return ""
        pieces: list[str] = []
        frame = self.frame()
        first = bisect_left(frame.ids, low[0])
        for block in frame.blocks[first:]:
            if block.id > high[0]:
                break
            start = low[1:] if block.id == low[0] else (0, 0, 0)
            end = high[1:] if block.id == high[0] else (2, 0, 0)
            if start[0] == 0 and block.is_command:
                lines = block.command.split("\n")
                to = end if end[0] == 0 else (0, len(lines) - 1,
                                              len(lines[-1]))
                pieces.extend(self._slice_lines(
                    lines, 0, None, start[1], start[2], to[1], to[2]))
            if end[0] >= 1 and not block.collapsed:
                lines, base = self._block_lines(block)
                frm = start if start[0] == 1 else (1, base, 0)
                if end[0] == 1:
                    to_line, to_off = end[1], end[2]
                else:
                    to_line = base + len(lines) - 1
                    to_off = len(lines[-1]) if lines else 0
                pieces.extend(self._slice_lines(
                    lines, base, self._shown_set(block), frm[1], frm[2],
                    to_line, to_off))
        return "\n".join(pieces)

    @staticmethod
    def _shown_set(block):
        """The lines a block's filter shows (``None``: no filter)."""
        if block.filter is None or block.visible is None:
            return None
        geo = block.view
        if block.screen is not None and geo is not None and geo.live_lines:
            return block.visible + geo.live_lines
        return block.visible

    @staticmethod
    def _slice_lines(lines, base, shown, line0, off0, line1, off1):
        """Lines ``line0..line1`` (absolute), cut at the two offsets."""
        out = []
        count = len(lines)
        if shown is not None:
            a = bisect_left(shown, line0)
            b = bisect_right(shown, line1)
            indices = shown[a:b]
        else:
            indices = range(max(line0, base), min(line1, base + count - 1)
                            + 1)
        for index in indices:
            k = index - base
            if not 0 <= k < count:
                continue
            text = lines[k]
            a = off0 if index == line0 else 0
            b = off1 if index == line1 else len(text)
            piece = text[a:b]
            # Trailing spaces go where the line ends, not where the
            # selection happens to.
            out.append(piece.rstrip(" ") if b >= len(text) else piece)
        return out

    def _grid_selection_text(self) -> str:
        screen, a, b = self._gsel[:3]
        low, high = sorted((a, b))
        grid = screen.grid
        parts: list[str] = []
        current = ""
        # The screen may have lost rows since the selection was made.
        last = min(high[0], len(grid) - 1)
        for index in range(low[0], last + 1):
            cells = grid[index].cells
            c0 = low[1] if index == low[0] else 0
            c1 = high[1] if index == high[0] else len(cells)
            current += "".join(cells[c0:c1])
            if not grid[index].wrapped or index == last:
                parts.append(current)
                current = ""
        return clean_lines(parts)

    def _publish_selection(self) -> None:
        """Selecting text fills the primary selection, as on any Linux
        terminal."""
        clipboard = QGuiApplication.clipboard()
        if clipboard.supportsSelection():
            text = self.selection_text()
            if text:
                clipboard.setText(text, QClipboard.Mode.Selection)

    def _word_at(self, pos):
        block_id, part, line, offset = pos
        _, block = self._block_at_index(block_id)
        if block is None:
            return pos, pos
        if part == 0:
            lines = block.command.split("\n")
            text = lines[line] if line < len(lines) else ""
        else:
            lines, base = self._block_lines(block)
            text = lines[line - base] if 0 <= line - base < len(lines) else ""
        if not text:
            return pos, pos
        offset = min(offset, len(text) - 1)
        extra = self.config.word_characters

        def kind(ch: str) -> int:
            if ch.isalnum() or ch == "_" or ch in extra:
                return 1
            return 2 if ch.isspace() else 3

        wanted = kind(text[offset])
        start = offset
        while start > 0 and kind(text[start - 1]) == wanted:
            start -= 1
        end = offset + 1
        while end < len(text) and kind(text[end]) == wanted:
            end += 1
        if wanted == 3:
            start, end = offset, offset + 1
        return (block_id, part, line, start), (block_id, part, line, end)

    def _line_at(self, pos):
        block_id, part, line, _ = pos
        _, block = self._block_at_index(block_id)
        if block is None:
            return pos, pos
        if part == 0:
            lines = block.command.split("\n")
            length = len(lines[line]) if line < len(lines) else 0
        else:
            lines, base = self._block_lines(block)
            length = len(lines[line - base]) \
                if 0 <= line - base < len(lines) else 0
        return (block_id, part, line, 0), (block_id, part, line, length)

    # ------------------------------------------------------------------
    # block selection and navigation
    # ------------------------------------------------------------------

    def selected_blocks(self) -> list:
        chosen = set(self.selected)
        return [b for b in self.session.blocks if b.id in chosen]

    def select_block(self, block, mode: str = "single") -> None:
        """Select a block: alone, toggled into the selection, or as the
        end of a range from the last selected one."""
        ids = [b.id for b in self.session.blocks]
        if mode == "toggle":
            if block.id in self.selected:
                self.selected.remove(block.id)
                self._cursor_block = self.selected[-1] \
                    if self.selected else None
            else:
                self.selected.append(block.id)
                self._cursor_block = block.id
            self._range_anchor = self._cursor_block
        elif mode == "range" and self._range_anchor in ids:
            a = ids.index(self._range_anchor)
            b = ids.index(block.id)
            low, high = (a, b) if a <= b else (b, a)
            self.selected = ids[low:high + 1]
            self._cursor_block = block.id
        else:
            self.selected = [block.id]
            self._cursor_block = block.id
            self._range_anchor = block.id
        self.selected.sort()
        self._sel = None
        self._gsel = None
        self._ui += 1
        self.update()

    def clear_block_selection(self) -> None:
        if self.selected:
            self.selected = []
            self._cursor_block = None
            self._range_anchor = None
            self._ui += 1
            self.update()

    def _move_selection(self, delta: int, extend: bool) -> None:
        blocks = self.session.blocks
        if not blocks:
            return
        ids = [b.id for b in blocks]
        if self._cursor_block not in ids:
            target = len(ids) - 1
        else:
            target = max(0, min(len(ids) - 1,
                                ids.index(self._cursor_block) + delta))
        block = blocks[target]
        if extend:
            self.select_block(block, "range")
        else:
            self.select_block(block)
        self._reveal_block_start(block)

    def _reveal_block_start(self, block) -> None:
        frame = self.frame()
        index, _ = self._block_at_index(block.id)
        if index < 0:
            return
        top = frame.tops[index]
        height = frame.heights[index]
        if top < self.scroll or top + min(height, self.r.ch * 3) > \
                self.scroll + frame.view_h:
            self._set_scroll(top)
        elif top + height > self.scroll + frame.view_h and \
                height <= frame.view_h:
            self._set_scroll(top + height - frame.view_h)

    def _scroll_block(self, block, to_end: bool) -> None:
        frame = self.frame()
        index, _ = self._block_at_index(block.id)
        if index < 0:
            return
        if to_end:
            self._set_scroll(frame.tops[index] + frame.heights[index]
                             - frame.view_h)
        else:
            self._set_scroll(frame.tops[index])

    def _reveal_text(self, block, line: int, offset: int) -> None:
        frame = self.frame()
        index, _ = self._block_at_index(block.id)
        if index < 0:
            return
        geo = block.view
        row = self._text_to_row(block, geo, line, offset)
        y = frame.tops[index] + geo.header_h + row * self.r.ch
        margin = geo.header_h + self.r.ch
        if y < self.scroll + margin or \
                y + self.r.ch > self.scroll + frame.view_h - self.r.ch:
            self._set_scroll(y - frame.view_h // 2)

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def copy(self) -> str:
        """Copy the selected text, or else the selected blocks' output."""
        if self.has_text_selection():
            text = self.selection_text()
        else:
            text = copy_text(self.selected_blocks(), OUTPUT)
        if text or self.selected:
            QGuiApplication.clipboard().setText(text)
        return text

    def copy_blocks(self, blocks, mode: str) -> str:
        text = copy_text(blocks, mode)
        QGuiApplication.clipboard().setText(text)
        return text

    def paste(self, selection: bool = False) -> None:
        clipboard = QGuiApplication.clipboard()
        mode = QClipboard.Mode.Selection if selection and \
            clipboard.supportsSelection() else QClipboard.Mode.Clipboard
        self.paste_text(clipboard.text(mode))

    def paste_text(self, text: str) -> None:
        if not text:
            return
        body = text.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in body.rstrip("\n"):
            # One line: leave the newline out, so that pasting never runs
            # anything by itself.
            body = body.rstrip("\n")
            if not body:
                return
        elif self.config.confirm_multiline_paste:
            if not confirm_paste(self, body, self.r.font):
                return
        self.clear_block_selection()
        self.scroll_to_bottom()
        self.session.paste(body)

    def _do(self, action: str) -> None:
        session = self.session
        if action == "copy":
            self.copy()
        elif action == "copy_command_and_output":
            blocks = self.selected_blocks()
            if blocks:
                self.copy_blocks(blocks, BOTH)
        elif action == "paste":
            self.paste()
        elif action == "paste_selection":
            self.paste(True)
        elif action == "select_newest_block":
            if session.blocks:
                block = session.blocks[-1]
                self.select_block(block)
                self._reveal_block_start(block)
        elif action == "clear_blocks":
            self.clear_block_selection()
            self._sel = None
            session.clear_blocks()
            self.scroll_to_bottom()
        elif action == "find_in_block":
            block = self._action_block()
            if block is not None:
                self.open_find(block)
        elif action == "filter_output":
            blocks = self.selected_blocks() or (
                [self._action_block()] if self._action_block() else [])
            if blocks:
                self.open_filter(blocks)
        elif action == "scroll_page_up":
            self._set_scroll(self.scroll - self.frame().view_h + self.r.ch)
        elif action == "scroll_page_down":
            self._set_scroll(self.scroll + self.frame().view_h - self.r.ch)
        elif action == "scroll_to_top":
            self._set_scroll(0)
        elif action == "scroll_to_bottom":
            self.scroll_to_bottom()
        elif action == "zoom_in":
            self.set_font_size(self.r.size + 1)
        elif action == "zoom_out":
            self.set_font_size(self.r.size - 1)
        elif action == "zoom_reset":
            self.set_font_size(self._base_size)
        elif action == "blocks.previous":
            self._move_selection(-1, False)
        elif action == "blocks.next":
            self._move_selection(1, False)
        elif action == "blocks.extend_previous":
            self._move_selection(-1, True)
        elif action == "blocks.extend_next":
            self._move_selection(1, True)
        elif action == "blocks.scroll_to_top":
            block = self._cursor()
            if block is not None:
                self._scroll_block(block, False)
        elif action == "blocks.scroll_to_bottom":
            block = self._cursor()
            if block is not None:
                self._scroll_block(block, True)
        elif action == "blocks.first" or action == "blocks.last":
            if session.blocks:
                block = session.blocks[0 if action == "blocks.first" else -1]
                self.select_block(block)
                self._reveal_block_start(block)
        elif action == "blocks.page_up":
            self._set_scroll(self.scroll - self.frame().view_h + self.r.ch)
        elif action == "blocks.page_down":
            self._set_scroll(self.scroll + self.frame().view_h - self.r.ch)
        elif action == "blocks.focus_input":
            self.focus_input()
        elif action == "blocks.menu":
            block = self._cursor()
            if block is not None:
                index, _ = self._block_at_index(block.id)
                frame = self.frame()
                y = max(0, frame.tops[index] - self.scroll) + self.r.ch
                self.open_menu(block, self.mapToGlobal(
                    QPointF(PAD_LEFT + 40, y).toPoint()))
        elif action == "blocks.toggle_collapse":
            self.set_collapsed(self.selected_blocks(), None)

    def _cursor(self):
        if self._cursor_block is None:
            return None
        return self._block_at_index(self._cursor_block)[1]

    def _action_block(self):
        """The block an unaddressed action means: the selected one, else
        the newest."""
        block = self._cursor()
        if block is None and self.session.blocks:
            block = self.session.blocks[-1]
        return block

    def focus_input(self) -> None:
        """Give the keyboard back to the shell."""
        self.clear_block_selection()
        self.setFocus()

    def set_font_size(self, size: float) -> None:
        self.r.set_font(self.r.family, size)
        self._ui += 1
        for block in self.session.blocks:
            block.view = None
        self._apply_grid_force()

    def _apply_grid_force(self) -> None:
        self.cols = -1
        self._apply_grid()
        self.update()

    def set_collapsed(self, blocks, collapsed: bool | None) -> None:
        if not blocks:
            return
        if collapsed is None:
            collapsed = not all(b.collapsed for b in blocks)
        for block in blocks:
            block.collapsed = collapsed
        self._ui += 1
        self.update()

    def rerun(self, block) -> None:
        """Put a block's command into the input, unsubmitted."""
        if not block.is_command:
            return
        self.clear_block_selection()
        self.scroll_to_bottom()
        self.session.set_command_buffer(block.command)
        self.setFocus()

    def save_output(self, blocks) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save output to file",
                                              os.path.expanduser("~"))
        if not path:
            return
        try:
            self.write_output(blocks, path)
        except OSError as error:
            self._warn("Save output to file",
                       f"Could not write {path}:\n{error.strerror}")

    def _warn(self, title: str, text: str) -> None:
        """Say that something went wrong, as plain text: a path may hold
        anything, also what Qt would take for markup."""
        box = QMessageBox(QMessageBox.Icon.Warning, title, text,
                          QMessageBox.StandardButton.Ok, self)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.exec()
        box.deleteLater()

    @staticmethod
    def write_output(blocks, path: str) -> None:
        text = copy_text(blocks, OUTPUT)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
            if text:
                handle.write("\n")

    # -- find and filter -----------------------------------------------------

    def open_find(self, block) -> None:
        previous = block.find.spec if block.find is not None else None
        self._bar_closed(False)
        self._find_block = block
        self.search.open(FIND, previous)
        self._place_bar()
        self._bar_changed()

    def open_filter(self, blocks) -> None:
        self._bar_closed(False)
        self._bar_targets = list(blocks)
        self.search.open(FILTER, blocks[0].filter if blocks else None)
        self._place_bar()
        # The bar opens on the filter of the first block.  Opening it
        # changes nothing: the other blocks keep theirs until it is edited.
        self._filter_status()

    def _filter_status(self) -> None:
        """Say in the bar how much the blocks it works on show."""
        spec = self.search.spec()
        if not spec.pattern:
            self.search.set_status("")
        elif spec.compile() is None:
            self.search.set_status("bad pattern")
        else:
            self.frame()
            counts = [self._filter_counts(b) for b in self._bar_targets]
            shown = sum(count[0] for count in counts)
            total = sum(count[1] for count in counts)
            self.search.set_status(f"{shown} of {total}")

    def _bar_changed(self) -> None:
        spec = self.search.spec()
        if self.search.mode == FILTER:
            self.set_filter(self._bar_targets,
                            spec if spec.pattern else None)
            self._filter_status()
            return
        block = self._find_block
        if block is None:
            return
        self.find(block, spec)
        find = block.find
        if not spec.pattern:
            self.search.set_status("")
        elif spec.compile() is None:
            self.search.set_status("bad pattern")
        elif find is None or not find.matches:
            self.search.set_status("no matches")
        else:
            self.search.set_status(f"{find.current + 1} / "
                                   f"{len(find.matches)}")

    def _bar_step(self, direction: int) -> None:
        if self.search.mode != FIND or self._find_block is None:
            return
        self.find_step(self._find_block, direction)
        find = self._find_block.find
        if find is not None and find.matches:
            self.search.set_status(f"{find.current + 1} / "
                                   f"{len(find.matches)}")

    def _bar_closed(self, refocus: bool = True) -> None:
        if self._find_block is not None:
            self._find_block.find = None
            self._find_block = None
        self._bar_targets = []
        if self.search.isVisible():
            self.search.hide()
        self._ui += 1
        self.update()
        if refocus:
            self.setFocus()

    def set_filter(self, blocks, spec: FilterSpec | None) -> None:
        """Show only matching lines of these blocks (``None`` clears)."""
        for block in blocks:
            if block.filter == spec and (spec is None
                                         or block.visible is not None):
                continue
            block.filter = spec
            block.visible = None
            if block.view is not None:
                block.view.fspec = None
                block.view.skey = None
        self._sel = None
        self._ui += 1
        self.update()
        if self._find_block in blocks:
            # A search covers the lines that are shown, and those changed.
            self._refresh_find(True)

    def _find_matches(self, block, spec: FilterSpec) -> list[tuple]:
        lines, base = self._block_lines(block)
        shown = self._shown_set(block)
        indices = None
        if shown is not None:
            indices = [i - base for i in shown if 0 <= i - base < len(lines)]
        found = find_matches(lines, spec.pattern, spec.regex,
                             spec.case_sensitive, indices)
        return [(line + base, a, b) for line, a, b in found]

    def find(self, block, spec: FilterSpec) -> None:
        """Search a block's output and highlight the matches."""
        if not spec.pattern or spec.compile() is None:
            block.find = None
            self._ui += 1
            self.update()
            return
        self.frame()                     # the filter decides what is shown
        if self._find_block is not None and self._find_block is not block:
            self._find_block.find = None  # one search at a time
        self._find_block = block
        state = FindState(spec)
        state.matches = self._find_matches(block, spec)
        state.lines = [m[0] for m in state.matches]
        state.stamp = self.session.version
        block.find = state
        if state.matches:
            state.current = 0
            self._goto_match(block, state)
        self._ui += 1
        self.update()

    def _refresh_find(self, anyway: bool = False) -> None:
        """Bring the matches up to date with a block that keeps printing
        or, ``anyway``, with a filter that changed what it shows."""
        block = self._find_block
        state = block.find if block is not None else None
        if state is None or (state.stamp == self.session.version
                             and not anyway):
            return
        self.frame()
        here = state.matches[state.current] \
            if 0 <= state.current < len(state.matches) else None
        state.matches = self._find_matches(block, state.spec)
        state.lines = [m[0] for m in state.matches]
        state.stamp = self.session.version
        if not state.matches:
            state.current = -1
        elif here is None:
            state.current = 0
        else:
            state.current = min(bisect_left(state.matches, here),
                                len(state.matches) - 1)
        if self.search.isVisible() and self.search.mode == FIND:
            self.search.set_status(
                f"{state.current + 1} / {len(state.matches)}"
                if state.matches else "no matches")
        self._ui += 1
        self.update()

    def find_step(self, block, direction: int) -> None:
        state = block.find
        if state is None or not state.matches:
            return
        state.current = (state.current + direction) % len(state.matches)
        self._goto_match(block, state)
        self._ui += 1
        self.update()

    def _goto_match(self, block, state: FindState) -> None:
        line, start, _ = state.matches[state.current]
        if block.collapsed:
            block.collapsed = False
            self._ui += 1
        self.follow = False
        self._reveal_text(block, line, start)

    # -- the block menu ------------------------------------------------------

    def menu_actions(self, block) -> list[tuple]:
        """The entries of a block's menu as ``(label, callable)``; a
        ``None`` entry is a separator.  With several blocks selected the
        actions that make sense for many apply to all of them."""
        blocks = self.selected_blocks()
        if block not in blocks:
            blocks = [block]
        many = len(blocks) > 1
        commands = [b for b in blocks if b.is_command]
        entries: list[tuple | None] = [
            ("Copy output", lambda: self.copy_blocks(blocks, OUTPUT)),
        ]
        if commands:
            entries.append(("Copy command", lambda: self.copy_blocks(
                blocks, COPY_COMMAND)))
            entries.append(("Copy command and output",
                            lambda: self.copy_blocks(blocks, BOTH)))
        entries.append(None)
        if not many:
            entries.append(("Find in block…", lambda: self.open_find(block)))
        entries.append(("Filter output…", lambda: self.open_filter(blocks)))
        if any(b.filter is not None for b in blocks):
            entries.append(("Clear filter",
                            lambda: self.set_filter(blocks, None)))
        if all(b.collapsed for b in blocks):
            entries.append(("Expand", lambda: self.set_collapsed(
                blocks, False)))
        else:
            entries.append(("Collapse", lambda: self.set_collapsed(
                blocks, True)))
        entries.append(None)
        if not many and block.is_command:
            entries.append(("Re-run command", lambda: self.rerun(block)))
        entries.append(("Save output to file…",
                        lambda: self.save_output(blocks)))
        return entries

    def open_menu(self, block, global_pos) -> None:
        menu = QMenu(self)
        for entry in self.menu_actions(block):
            if entry is None:
                menu.addSeparator()
            else:
                label, callback = entry
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda _=False, run=callback: run())
        menu.exec(global_pos)
        self.update()

    # ------------------------------------------------------------------
    # keyboard
    # ------------------------------------------------------------------

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ToolTip:
            self._tooltip(event)
            return True
        return super().event(event)

    def _typing_elsewhere(self) -> bool:
        """The keyboard is in the find / filter bar."""
        focus = QApplication.focusWidget()
        return focus is not None and focus is not self \
            and self.search.isAncestorOf(focus)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in _MODIFIER_KEYS:
            return
        if self._typing_elsewhere():
            # A key the search field has no use for is not the shell's.
            event.accept()
            return
        fullscreen = self.session.fullscreen
        action = None
        for combo in self._combos(event):
            if self.selected and not fullscreen:
                action = self._keys_blocks.get(combo)
            if action is None:
                action = self._keys_global.get(combo)
            if action is not None:
                break
        if action is not None and (not fullscreen
                                   or action in _FULLSCREEN_ACTIONS):
            self._do(action)
            return
        data = self._encode(event)
        if data is not None:
            self._type(data)

    def _type(self, data: bytes) -> None:
        """Send what was typed to the shell or the program in it."""
        # Typing gives the keyboard back to the shell.
        self.clear_block_selection()
        if self._gsel is not None and not self.session.fullscreen:
            self._gsel = None            # the line it was on is changing
        if not self.follow:
            self.scroll_to_bottom()
        self.session.write(data)

    def _encode(self, event) -> bytes | None:
        key = event.key()
        qt_mods = event.modifiers()
        mods = 0
        if qt_mods & Qt.KeyboardModifier.ShiftModifier:
            mods |= K.SHIFT
        if qt_mods & Qt.KeyboardModifier.AltModifier:
            mods |= K.ALT
        if qt_mods & Qt.KeyboardModifier.ControlModifier:
            mods |= K.CTRL
        name = _KEY_NAMES.get(key)
        if name is not None:
            if key == _Key.Key_Backtab:
                mods |= K.SHIFT
            return K.encode_key(name, mods, self.session.term.app_cursor)
        text = event.text()
        if mods & K.CTRL and 0x20 <= key < 0x7F:
            return K.encode_char(chr(key).lower(), mods & (K.CTRL | K.ALT))
        if text:
            return K.encode_char(text, mods & K.ALT)
        return None

    def inputMethodEvent(self, event) -> None:
        text = event.commitString()
        if text:
            self._type(text.encode("utf-8", "replace"))
        event.accept()

    def focusInEvent(self, event) -> None:
        if self.session.term.focus_events:
            self.session.write(b"\x1b[I")
        self.update()

    def focusOutEvent(self, event) -> None:
        if self.session.term.focus_events:
            self.session.write(b"\x1b[O")
        self.update()

    def focusNextPrevChild(self, forward: bool) -> bool:
        # Tab and Shift+Tab are keys for the shell.  Qt asks here before
        # it moves the focus, and hands the key to keyPressEvent on a no.
        return False

    # ------------------------------------------------------------------
    # mouse
    # ------------------------------------------------------------------

    def _zone(self, y: float) -> str:
        if self.session.fullscreen:
            return "screen"
        if y >= self.frame().view_h:
            return "input"
        return "blocks"

    def _screen_cell(self, x: float, y: float):
        """``(screen, row, col)`` for the input area or the full-screen
        terminal."""
        session = self.session
        if session.fullscreen:
            row, col = self._cell(x, y, 2)
            return session.term.screen, max(0, min(self.rows - 1, row)), col
        row, col = self._cell(x, y, self.frame().view_h + IN_TOP)
        return session.input, max(0, row), col

    def _mouse_report(self, event, kind: str) -> bool:
        """Forward a mouse event to the program, if it asked for them."""
        term = self.session.term
        if not term.mouse_mode or \
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            return False
        pos = event.position()
        if self._zone(pos.y()) == "blocks":
            return False
        _, row, col = self._screen_cell(pos.x(), pos.y())
        mods = 0
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            mods |= K.ALT
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            mods |= K.CTRL
        buttons = {Qt.MouseButton.LeftButton: 0,
                   Qt.MouseButton.MiddleButton: 1,
                   Qt.MouseButton.RightButton: 2}
        if kind == "move":
            held = [code for button, code in buttons.items()
                    if event.buttons() & button]
            if term.mouse_mode == 1003 or (term.mouse_mode == 1002 and held):
                data = K.encode_mouse(held[0] if held else 3, col, row, mods,
                                      motion=True, sgr=term.mouse_sgr,
                                      utf8=term.mouse_utf8)
            else:
                return True
        else:
            code = buttons.get(event.button())
            if code is None:
                return True
            if kind == "release" and term.mouse_mode == 9:
                return True
            data = K.encode_mouse(code, col, row, mods,
                                  release=kind == "release",
                                  sgr=term.mouse_sgr, utf8=term.mouse_utf8)
        if data:
            self.session.write(data)
        return True

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if self._mouse_report(event, "press"):
            return
        pos = event.position()
        x, y = pos.x(), pos.y()
        button = event.button()
        if button != Qt.MouseButton.LeftButton and self._press is not None:
            # Another button while the left one is held: the drag that is
            # under way goes on undisturbed.
            return
        zone = self._zone(y)
        if button == Qt.MouseButton.MiddleButton:
            self.paste(True)
            return
        if button == Qt.MouseButton.RightButton:
            if zone == "blocks":
                _, block, _ = self._block_under(y)
                if block is not None:
                    if block.id not in self.selected:
                        self.select_block(block)
                    self.open_menu(block, event.globalPosition().toPoint())
            return
        if button != Qt.MouseButton.LeftButton:
            return
        now = time.monotonic()
        interval = QGuiApplication.styleHints().mouseDoubleClickInterval() \
            / 1000.0
        # A second click counts as a double click only on the same spot
        # of the same content.
        under = self._block_under(y)[1] if zone == "blocks" else None
        where = (round(x / 6), round(y / 6), zone, under, int(self.scroll))
        if now - self._last_click[0] <= interval and \
                self._last_click[1] == where:
            self._click_count += 1
        else:
            self._click_count = 1
        self._last_click = (now, where)
        if zone != "blocks":
            # The input area and the full-screen terminal: plain grid
            # selection, and the keyboard goes to the shell.
            self.clear_block_selection()
            self._sel = None
            screen, row, col = self._screen_cell(x, y)
            self._gsel = None
            self._press = {"zone": zone, "x": x, "y": y, "screen": screen,
                           "cell": (row, col), "dragging": False}
            self.update()
            return
        block, name = self._button_at(x, y)
        if name is not None and name != "sticky":
            self._press = {"zone": "button", "block": block, "name": name,
                           "x": x, "y": y, "dragging": False}
            return
        # ``block`` is the one whose pinned header was pressed, if any.
        # That header lies over the output; the press is on the command
        # it shows, not on the lines hidden underneath.
        pinned, inside = self._pinned_header_at(x, y)
        position = self._position_at(x, y) if pinned is None \
            else self._header_position(pinned, x, inside)
        self._gsel = None
        self._press = {"zone": "blocks", "x": x, "y": y, "block": under,
                       "sticky": block, "pos": position, "dragging": False,
                       "mods": event.modifiers()}
        if position is None:
            return
        chord = event.modifiers() & (Qt.KeyboardModifier.ControlModifier
                                     | Qt.KeyboardModifier.ShiftModifier)
        if self._click_count >= 2 and not chord:
            unit = self._word_at(position) if self._click_count == 2 \
                else self._line_at(position)
            if unit[0] != unit[1]:
                self._sel = unit
                self._press["dragging"] = True
                self._press["fixed"] = unit
                self.clear_block_selection()
                self.update()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position()
        x, y = pos.x(), pos.y()
        press = self._press
        if press is None:
            if self._mouse_report(event, "move"):
                return
            self._update_hover(x, y)
            return
        if not event.buttons() & Qt.MouseButton.LeftButton:
            return
        if not press["dragging"]:
            if abs(x - press["x"]) + abs(y - press["y"]) < DRAG_THRESHOLD:
                return
            if press["zone"] == "button":
                return
            press["dragging"] = True
            # Dragging selects text; it never starts a block selection.
            self.clear_block_selection()
        if press["zone"] == "blocks":
            self._extend_selection(x, y)
            frame = self.frame()
            if y < 0 or y > frame.view_h:
                if not self._drag_timer.isActive():
                    self._drag_timer.start()
            else:
                self._drag_timer.stop()
        else:
            # The input area or the full-screen terminal: a press on a
            # button never gets this far.
            screen, row, col = self._screen_cell(x, y)
            if screen is press["screen"]:
                a = press["cell"]
                b = (row, col + 1 if (row, col) >= a else col)
                if (row, col) < a:
                    a = (a[0], a[1] + 1)
                self._gsel = (screen, a, b)
                self.update()

    def _extend_selection(self, x: float, y: float) -> None:
        press = self._press
        head = self._position_at(x, y)
        anchor = press.get("pos")
        if head is None or anchor is None:
            return
        fixed = press.get("fixed")
        if fixed is not None:
            # Started with a double or triple click: grow from that unit.
            if head < fixed[0]:
                self._sel = (fixed[1], head)
            elif head > fixed[1]:
                self._sel = (fixed[0], head)
            else:
                self._sel = fixed
        else:
            self._sel = (anchor, head)
        self.follow = False
        self.update()

    def _drag_scroll(self) -> None:
        press = self._press
        if press is None or not press["dragging"]:
            self._drag_timer.stop()
            return
        pos = self.mapFromGlobal(self.cursor().pos())
        frame = self.frame()
        y = pos.y()
        if y < 0:
            self._set_scroll(self.scroll + max(-60, y))
        elif y > frame.view_h:
            self._set_scroll(self.scroll + min(60, y - frame.view_h))
        else:
            self._drag_timer.stop()
            return
        self._extend_selection(pos.x(), y)

    def mouseReleaseEvent(self, event) -> None:
        press = self._press
        if press is None:
            self._mouse_report(event, "release")
            return
        if event.button() != Qt.MouseButton.LeftButton:
            # Only the button that began a press ends it.
            return
        self._drag_timer.stop()
        self._press = None
        pos = event.position()
        if press["zone"] == "button":
            block, name = self._button_at(pos.x(), pos.y())
            if block is press["block"] and name == press["name"]:
                self._press_button(block, name, event)
            return
        if press["dragging"]:
            if self._sel is not None and self._sel[0] == self._sel[1]:
                self._sel = None
            self._publish_selection()
            self.update()
            return
        # A click without a drag.
        sticky = press.get("sticky")
        if sticky is not None:
            self._scroll_block(sticky, False)
            return
        block = press.get("block")
        self._sel = None
        if block is None:
            # Beside the blocks, in the input area or on the full screen.
            self.clear_block_selection()
            self.update()
            return
        mods = press["mods"]
        if mods & Qt.KeyboardModifier.ControlModifier:
            self.select_block(block, "toggle")
        elif mods & Qt.KeyboardModifier.ShiftModifier:
            self.select_block(block, "range")
        else:
            self.select_block(block)

    def _press_button(self, block, name: str, event) -> None:
        if name == "menu":
            if block.id not in self.selected:
                self.select_block(block)
            self.open_menu(block, event.globalPosition().toPoint())
        elif name == "jump":
            self._scroll_block(block, True)
        else:
            # "unfilter", the only other button a press is kept for.
            self.set_filter([block], None)
            if block in self._bar_targets:
                self._bar_closed()

    def mouseDoubleClickEvent(self, event) -> None:
        # Multi-clicks are counted in mousePressEvent.
        self.mousePressEvent(event)

    def _update_hover(self, x: float, y: float) -> None:
        hover = None
        if self._zone(y) == "blocks":
            # A header button lies inside its own block, pinned or not.
            _, block, _ = self._block_under(y)
            hover = block.id if block is not None else None
            _, name = self._button_at(x, y)
            if name in ("menu", "jump", "unfilter"):
                shape = Qt.CursorShape.PointingHandCursor
            elif name != "sticky" and self._over_text(y):
                shape = Qt.CursorShape.IBeamCursor
            else:
                # A header, also the pinned one that lies over the output.
                shape = Qt.CursorShape.ArrowCursor
            self.setCursor(shape)
        else:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        if hover != self._hover:
            self._hover = hover
            self.update()

    def _over_text(self, y: float) -> bool:
        _, block, inside = self._block_under(y)
        return block is not None and block.view is not None \
            and inside >= block.view.header_h

    def leaveEvent(self, event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def wheelEvent(self, event) -> None:
        session = self.session
        pixels = event.pixelDelta().y()
        steps = event.angleDelta().y() / 120.0
        if session.fullscreen:
            term = session.term
            count = int(abs(steps) * 3) or (1 if steps else 0)
            if not count:
                return
            pos = event.position()
            if term.mouse_mode and not (event.modifiers()
                                        & Qt.KeyboardModifier.ShiftModifier):
                _, row, col = self._screen_cell(pos.x(), pos.y())
                data = K.encode_mouse(64 if steps > 0 else 65, col, row, 0,
                                      sgr=term.mouse_sgr,
                                      utf8=term.mouse_utf8)
                if data:
                    session.write(data * count)
            elif term.alt_scroll:
                key = K.encode_key("Up" if steps > 0 else "Down",
                                   app_cursor=term.app_cursor)
                session.write(key * count)
            return
        delta = pixels if pixels else steps * self.r.ch * 3
        if delta:
            self._set_scroll(self.scroll - delta)

    def contextMenuEvent(self, event) -> None:
        event.accept()

    def _tooltip(self, event) -> None:
        pos = event.pos()
        _, block, inside = self._block_under(pos.y())
        # Behind a full-screen program the blocks are laid out as well.
        if self.session.fullscreen or block is None or \
                block.view is None or inside >= block.view.header_h:
            QToolTip.hideText()
            return
        started = time.strftime("%Y-%m-%d %H:%M:%S",
                                time.localtime(block.started))
        lines = [f"Started {started}"]
        if block.is_command:
            if block.running:
                lines.append(f"Running for {_duration(block.elapsed())}")
            else:
                if block.duration is not None:
                    lines.append(f"Took {_duration(block.duration)}")
                if block.not_run:
                    lines.append("Not run: the shell rejected it")
                elif block.exit_code is not None:
                    lines.append(f"Exit code {block.exit_code}")
            if block.cwd:
                where = block.cwd
                if block.branch:
                    where += f"  ({block.branch})"
                lines.append(where)
        QToolTip.showText(event.globalPos(), "\n".join(lines), self)
