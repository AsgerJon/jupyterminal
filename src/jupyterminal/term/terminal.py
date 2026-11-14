"""The terminal: parses a program's output stream and drives a screen.

:class:`Terminal` owns everything that belongs to the terminal as a whole
(the parser, input-related modes, the alternate screen) and forwards
drawing operations to ``self.screen``, the screen currently receiving
output.  The session swaps that screen as commands start and finish.
"""

from __future__ import annotations

import base64
import codecs
import re
from typing import Callable

from . import style as _style
from .screen import Screen

#: Private OSC number used by the shell integration marks.
MARK_OSC = "7717"

_SPECIAL = re.compile("[\x00-\x1f\x7f-\x9f]")
_CSI = re.compile("\x1b\\[([\x30-\x3f]*)([\x20-\x2f]*)([\x40-\x7e])")
_STRING_END = re.compile("[\x07\x18\x1a\x1b\x9c]")
#: Complete plain lines: characters that are one cell wide, then CR LF.
_BULK = re.compile("(?:[\x20-\x7e\xa0-\xac\xae-˿]*\r\n)+")

_BULK_MIN = 2048
_MAX_STRING = 4 * 1024 * 1024
#: More characters than any escape sequence has between ESC and its end.
_MAX_INTERMEDIATES = 16
_MAX_PARAM = 65535


def _params(text: str) -> list[int]:
    if not text:
        return []
    if text.isdigit():
        return [min(int(text), _MAX_PARAM)]
    out = []
    for part in text.split(";"):
        if ":" in part:
            part = part.split(":", 1)[0]
        out.append(min(int(part), _MAX_PARAM) if part.isdigit() else 0)
    return out


class Terminal:
    """Parser and terminal-wide state."""

    def __init__(self, screen: Screen,
                 respond: Callable[[bytes], None] | None = None) -> None:
        self.screen = screen
        self.respond = respond or (lambda data: None)
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = ""
        # A string (OSC, DCS and the like) whose end has not arrived: its
        # kind, the pieces of its body so far, and their size.
        self._string_kind = ""
        self._string_parts: list[str] = []
        self._string_size = 0
        self._main: Screen | None = None
        self.alt_active = False
        # Input-related modes.
        self.app_cursor = False
        self.app_keypad = False
        self.bracketed_paste = False
        self.mouse_mode = 0
        self.mouse_sgr = False
        self.mouse_utf8 = False
        self.focus_events = False
        self.alt_scroll = True
        self.sync_output = False
        self.title = ""
        #: Cell size in pixels, for size reports.
        self.cell_px = (9, 18)
        #: Nonce that authenticates shell integration marks.
        self.nonce: str | None = None
        # Callbacks, all optional.
        self.on_mark: Callable[[str, list[str]], None] | None = None
        self.on_title: Callable[[str], None] | None = None
        self.on_bell: Callable[[], None] | None = None
        self.on_alt: Callable[[bool], None] | None = None
        self.on_clipboard: Callable[[str, str], None] | None = None
        self.on_cwd: Callable[[str], None] | None = None
        #: ``color_query(kind, index)`` -> ``(r, g, b)`` or ``None``.
        self.color_query: Callable[[str, int], tuple | None] | None = None

    # ------------------------------------------------------------------
    # screens
    # ------------------------------------------------------------------

    def set_screen(self, screen) -> None:
        """Direct output to another screen, leaving the alternate screen."""
        if self.alt_active:
            self.alt_active = False
            self._main = None
            if self.on_alt:
                self.on_alt(False)
        self.screen = screen

    @property
    def main_screen(self):
        """The screen behind the alternate screen (or the screen itself)."""
        return self._main if self.alt_active else self.screen

    def _enter_alt(self, save_cursor: bool) -> None:
        if self.alt_active:
            return
        main = self.screen
        if save_cursor:
            main.save_cursor()
        self._main = main
        self.screen = Screen(main.cols, main.rows, store=None, reflow=False)
        self._hand_over(main, self.screen)
        self.alt_active = True
        if self.on_alt:
            self.on_alt(True)

    def _leave_alt(self, restore_cursor: bool) -> None:
        if not self.alt_active:
            return
        alt = self.screen
        self.screen = self._main
        self._main = None
        self.alt_active = False
        self._hand_over(alt, self.screen)
        if restore_cursor:
            self.screen.restore_cursor()
        if self.on_alt:
            self.on_alt(False)

    @staticmethod
    def _hand_over(old, new) -> None:
        """Give ``new`` what belongs to the terminal and not to a screen.

        Modes, tab stops and character sets are the terminal's: a program
        that hides the cursor and then goes to the alternate screen finds
        it hidden there, and one that shows it again before coming back
        has it shown.  Colours, the scroll region and the saved cursor
        stay with each screen.
        """
        new.autowrap = old.autowrap
        new.insert = old.insert
        new.newline_mode = old.newline_mode
        new.cursor_visible = old.cursor_visible
        new.cursor_shape = old.cursor_shape
        new.tabs = None if old.tabs is None else set(old.tabs)
        g0, g1, shifted = old.charsets()
        new.designate_charset(0, g0)
        new.designate_charset(1, g1)
        new.shift_charset(shifted)

    def resize(self, cols: int, rows: int) -> None:
        if self.alt_active:
            self.screen.resize(cols, rows)
            self._main.resize(cols, rows)
        else:
            self.screen.resize(cols, rows)

    def reset_input_modes(self) -> None:
        """Drop modes a crashed program may have left behind."""
        self.app_cursor = False
        self.app_keypad = False
        self.mouse_mode = 0
        self.mouse_sgr = False
        self.mouse_utf8 = False
        self.focus_events = False
        self.alt_scroll = True
        self.sync_output = False

    # ------------------------------------------------------------------
    # parsing
    # ------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        """Process output bytes from the program."""
        text = self._decoder.decode(data)
        if self._string_kind:
            text = self._string_more(text)
        if self._pending:
            text = self._pending + text
            self._pending = ""
        if text:
            self._parse(text)

    def _parse(self, d: str) -> None:
        n = len(d)
        i = 0
        search = _SPECIAL.search
        while i < n:
            if n - i >= _BULK_MIN and (i == 0 or d[i - 1] == "\n"):
                scr = self.screen
                if scr.bulk_ok():
                    m = _BULK.match(d, i)
                    # Both ways of printing give the same; this one pays
                    # for a long run of plain lines only.
                    if m is not None and m.end() - i >= _BULK_MIN:
                        scr.bulk_lines(m.group())
                        i = m.end()
                        continue
            m = search(d, i)
            if m is None:
                self.screen.put_text(d[i:] if i else d)
                return
            j = m.start()
            if j > i:
                self.screen.put_text(d[i:j])
            c = d[j]
            if c == "\r":
                self.screen.carriage_return()
                i = j + 1
            elif c == "\n":
                self.screen.linefeed()
                i = j + 1
            elif c == "\x1b":
                i = self._escape(d, j, n)
                if i < 0:
                    return
            else:
                self._c0(c)
                i = j + 1

    def _c0(self, c: str) -> None:
        if c == "\t":
            self.screen.tab()
        elif c == "\b":
            self.screen.backspace()
        elif c == "\x07":
            if self.on_bell:
                self.on_bell()
        elif c == "\n" or c == "\x0b" or c == "\x0c":
            self.screen.linefeed()
        elif c == "\r":
            self.screen.carriage_return()
        elif c == "\x0e":
            self.screen.shift_charset(1)
        elif c == "\x0f":
            self.screen.shift_charset(0)

    def _escape(self, d: str, j: int, n: int) -> int:
        """Handle the escape sequence at ``d[j]``; return the next index
        or -1 when the sequence is incomplete."""
        if j + 1 >= n:
            self._pending = d[j:]
            return -1
        c = d[j + 1]
        if c < " ":
            # Controls inside an escape sequence: another ESC starts over,
            # CAN and SUB cancel, anything else acts and the sequence goes
            # on.  (A program that prints a stray ESC must not swallow the
            # start of whatever comes next.)
            k = j + 1
            while k < n and d[k] < " ":
                ch = d[k]
                if ch == "\x1b":
                    return k
                if ch == "\x18" or ch == "\x1a":
                    return k + 1
                self._c0(ch)
                k += 1
            if k >= n:
                self._pending = "\x1b"
                return -1
            rest = "\x1b" + d[k:]
            end = self._escape(rest, 0, len(rest))
            return -1 if end < 0 else k - 1 + end
        if c == "[":
            m = _CSI.match(d, j)
            if m is not None:
                self._csi(m.group(1), m.group(2), m.group(3))
                return m.end()
            return self._csi_slow(d, j, n)
        if c == "]" or c == "P" or c == "^" or c == "_" or c == "X":
            return self._string(d, j + 2, n, c)
        if c <= "/":
            # Intermediate characters (space to slash), then the character
            # that ends the sequence.
            k = j + 2
            while k < n and " " <= d[k] <= "/":
                k += 1
            if k - j > _MAX_INTERMEDIATES:
                return k  # no sequence is that long: let it go
            if k >= n:
                self._pending = d[j:]
                return -1
            final = d[k]
            if final < " ":
                return k  # cut short; the control is handled next
            if k == j + 2:
                if c == "(":
                    self.screen.designate_charset(0, final)
                elif c == ")":
                    self.screen.designate_charset(1, final)
                elif c == "#" and final == "8":
                    self.screen.align_test()
            return k + 1
        self._esc(c)
        return j + 2

    def _csi_slow(self, d: str, j: int, n: int) -> int:
        """A CSI that is split across reads or has controls inside it."""
        collected: list[str] = []
        k = j + 2
        while k < n:
            ch = d[k]
            code = ord(ch)
            if 0x40 <= code <= 0x7E:
                seq = "".join(collected)
                split = len(seq)
                for idx, sc in enumerate(seq):
                    if sc < "0":
                        split = idx
                        break
                self._csi(seq[:split], seq[split:], ch)
                return k + 1
            if 0x20 <= code <= 0x3F:
                collected.append(ch)
            elif ch == "\x1b":
                return k
            elif ch == "\x18" or ch == "\x1a":
                return k + 1
            elif code < 0x20:
                self._c0(ch)
            k += 1
        if len(collected) > 256:
            return n
        self._pending = "\x1b[" + "".join(collected)
        return -1

    def _string(self, d: str, start: int, n: int, kind: str) -> int:
        """OSC, DCS, PM, APC and SOS: text up to a terminator.

        ``d[start]`` is the first character of the string's body that has
        not been looked at; what came of the body in earlier reads waits
        in ``_string_parts``.  Returns the index after the string, which
        is ``n`` when its end has not arrived: the rest is then kept, and
        :meth:`_string_more` goes on with the next read.  Every character
        is looked at once, however the string is cut up.
        """
        pos = start
        while True:
            m = _STRING_END.search(d, pos)
            if m is None:
                return self._string_wait(d, start, n, kind)
            end = m.start()
            c = d[end]
            if c == "\x1b":
                if end + 1 >= n:
                    return self._string_wait(d, start, n, kind)
                if d[end + 1] != "\\":
                    self._string_drop()
                    return end  # aborted by a new escape sequence
                after = end + 2
                bel = False
            elif c == "\x18" or c == "\x1a":
                self._string_drop()
                return end + 1
            elif c == "\x07" and kind != "]":
                pos = end + 1  # BEL only terminates OSC
                continue
            else:
                after = end + 1
                bel = c == "\x07"
            body = "".join(self._string_parts) + d[start:end]
            self._string_drop()
            if kind == "]":
                self._osc(body, bel)
            elif kind == "P":
                self._dcs(body)
            return after

    def _string_wait(self, d: str, start: int, n: int, kind: str) -> int:
        """Keep the body from ``d[start]`` until the string's end comes;
        a string that has grown beyond all reason is let go."""
        size = self._string_size + n - start
        if size + 2 > _MAX_STRING:
            self._string_drop()
        else:
            self._string_kind = kind
            self._string_parts.append(d[start:])
            self._string_size = size
        return n

    def _string_drop(self) -> None:
        self._string_kind = ""
        self._string_parts = []
        self._string_size = 0

    def _string_more(self, text: str) -> str:
        """Go on with a string begun in an earlier read; return what
        follows it in ``text``."""
        kind = self._string_kind
        parts = self._string_parts
        if parts[-1].endswith("\x1b"):
            # Whether that ESC begins the terminator shows only now.
            parts[-1] = parts[-1][:-1]
            self._string_size -= 1
            text = "\x1b" + text
        return text[self._string(text, 0, len(text), kind):]

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def _esc(self, c: str) -> None:
        scr = self.screen
        if c == "7":
            scr.save_cursor()
        elif c == "8":
            scr.restore_cursor()
        elif c == "D":
            scr.index()
        elif c == "E":
            scr.next_line()
        elif c == "H":
            scr.tab_set()
        elif c == "M":
            scr.reverse_index()
        elif c == "=":
            self.app_keypad = True
        elif c == ">":
            self.app_keypad = False
        elif c == "c":
            self._leave_alt(False)
            self.screen.reset()
            self.screen.clear_count += 1
            if self.screen.store is not None:
                self.screen.store.clear()
            self.reset_input_modes()
            self.bracketed_paste = False
        elif c == "Z":
            self.respond(b"\x1b[?62;22c")

    def _csi(self, p: str, inter: str, f: str) -> None:
        scr = self.screen
        if inter:
            self._csi_inter(p, inter, f)
            return
        if f == "m":
            if not p or p[0] not in "<=>?":
                scr.set_sgr(p)
            return
        if p and p[0] in "<=>?":
            self._csi_private(p[0], p[1:], f)
            return
        ps = _params(p)
        a = ps[0] if ps else 0
        if f == "H" or f == "f":
            row = (ps[0] if ps and ps[0] else 1) - 1
            col = (ps[1] if len(ps) > 1 and ps[1] else 1) - 1
            scr.cursor_to(row, col)
        elif f == "K":
            scr.erase_line(a)
        elif f == "J":
            scr.erase_display(a)
        elif f == "A":
            scr.cursor_up(a or 1)
        elif f == "B" or f == "e":
            scr.cursor_down(a or 1)
        elif f == "C" or f == "a":
            scr.cursor_forward(a or 1)
        elif f == "D":
            scr.cursor_back(a or 1)
        elif f == "G" or f == "`":
            scr.cursor_col((a or 1) - 1)
        elif f == "d":
            scr.cursor_row((a or 1) - 1)
        elif f == "E":
            scr.cursor_down(a or 1)
            scr.carriage_return()
        elif f == "F":
            scr.cursor_up(a or 1)
            scr.carriage_return()
        elif f == "P":
            scr.delete_chars(a or 1)
        elif f == "@":
            scr.insert_chars(a or 1)
        elif f == "X":
            scr.erase_chars(a or 1)
        elif f == "L":
            scr.insert_lines(a or 1)
        elif f == "M":
            scr.delete_lines(a or 1)
        elif f == "S":
            scr.scroll_up(a or 1)
        elif f == "T":
            if len(ps) <= 1:
                scr.scroll_down(a or 1)
        elif f == "I":
            scr.tab(a or 1)
        elif f == "Z":
            scr.back_tab(a or 1)
        elif f == "b":
            scr.repeat_last(a or 1)
        elif f == "g":
            scr.tab_clear(a)
        elif f == "r":
            top = ps[0] - 1 if ps and ps[0] else None
            bot = ps[1] - 1 if len(ps) > 1 and ps[1] else None
            scr.set_margins(top, bot)
        elif f == "s":
            scr.save_cursor()
        elif f == "u":
            scr.restore_cursor()
        elif f == "h" or f == "l":
            on = f == "h"
            for mode in ps:
                if mode == 4:
                    scr.insert = on
                elif mode == 20:
                    scr.newline_mode = on
        elif f == "n":
            if a == 5:
                self.respond(b"\x1b[0n")
            elif a == 6:
                self.respond(self._cursor_report(""))
        elif f == "c":
            if not a:
                self.respond(b"\x1b[?62;22c")
        elif f == "t":
            self._window_op(ps)

    def _cursor_report(self, prefix: str) -> bytes:
        scr = self.screen
        row = scr.cy - (scr.top if scr.origin else 0) + 1
        return f"\x1b[{prefix}{row};{scr.cx + 1}R".encode()

    def _window_op(self, ps: list[int]) -> None:
        if not ps:
            return
        scr = self.screen
        op = ps[0]
        cw, ch = self.cell_px
        if op == 14:
            self.respond(f"\x1b[4;{scr.rows * ch};{scr.cols * cw}t".encode())
        elif op == 16:
            self.respond(f"\x1b[6;{ch};{cw}t".encode())
        elif op == 18 or op == 19:
            self.respond(f"\x1b[{op - 10};{scr.rows};{scr.cols}t".encode())
        elif op == 11:
            self.respond(b"\x1b[1t")

    def _csi_inter(self, p: str, inter: str, f: str) -> None:
        scr = self.screen
        if inter == " " and f == "q":
            ps = _params(p)
            scr.cursor_shape = ps[0] if ps else 0
        elif inter == "!" and f == "p":
            scr.soft_reset()
        elif inter == "$" and f == "p":
            if p.startswith("?"):
                ps = _params(p[1:])
                mode = ps[0] if ps else 0
                state = self._mode_state(mode)
                self.respond(f"\x1b[?{mode};{state}$y".encode())
            else:
                ps = _params(p)
                mode = ps[0] if ps else 0
                if mode == 4:
                    state = 1 if scr.insert else 2
                elif mode == 20:
                    state = 1 if scr.newline_mode else 2
                else:
                    state = 0
                self.respond(f"\x1b[{mode};{state}$y".encode())

    def _mode_state(self, mode: int) -> int:
        scr = self.screen
        table = {
            1: self.app_cursor, 6: scr.origin, 7: scr.autowrap,
            9: self.mouse_mode == 9,
            25: scr.cursor_visible, 47: self.alt_active,
            66: self.app_keypad, 1000: self.mouse_mode == 1000,
            1002: self.mouse_mode == 1002, 1003: self.mouse_mode == 1003,
            1004: self.focus_events, 1005: self.mouse_utf8,
            1006: self.mouse_sgr, 1007: self.alt_scroll,
            1047: self.alt_active, 1049: self.alt_active,
            2004: self.bracketed_paste, 2026: self.sync_output,
        }
        value = table.get(mode)
        if value is None:
            return 0
        return 1 if value else 2

    def _csi_private(self, marker: str, p: str, f: str) -> None:
        if marker == "?":
            if f == "h" or f == "l":
                on = f == "h"
                for mode in _params(p):
                    self._set_mode(mode, on)
            elif f == "J":
                ps = _params(p)
                self.screen.erase_display(ps[0] if ps else 0)
            elif f == "K":
                ps = _params(p)
                self.screen.erase_line(ps[0] if ps else 0)
            elif f == "n":
                ps = _params(p)
                if ps and ps[0] == 6:
                    self.respond(self._cursor_report("?"))
        elif marker == ">":
            if f == "c":
                self.respond(b"\x1b[>0;10;1c")
            elif f == "q":
                self.respond(b"\x1bP>|jupyterminal\x1b\\")

    def _set_mode(self, mode: int, on: bool) -> None:
        scr = self.screen
        if mode == 1:
            self.app_cursor = on
        elif mode == 6:
            scr.set_origin(on)
        elif mode == 7:
            scr.autowrap = on
            if not on:
                scr.wrap_pending = False
        elif mode == 25:
            scr.cursor_visible = on
        elif mode == 47 or mode == 1047:
            if on:
                self._enter_alt(False)
            else:
                self._leave_alt(False)
        elif mode == 1048:
            if on:
                scr.save_cursor()
            else:
                scr.restore_cursor()
        elif mode == 1049:
            if on:
                self._enter_alt(True)
            else:
                self._leave_alt(True)
        elif mode == 66:
            self.app_keypad = on
        elif mode == 9 or mode == 1000 or mode == 1002 or mode == 1003:
            if on:
                self.mouse_mode = mode
            elif self.mouse_mode == mode:
                self.mouse_mode = 0
        elif mode == 1004:
            self.focus_events = on
        elif mode == 1005:
            self.mouse_utf8 = on
        elif mode == 1006:
            self.mouse_sgr = on
        elif mode == 1007:
            self.alt_scroll = on
        elif mode == 2004:
            self.bracketed_paste = on
        elif mode == 2026:
            self.sync_output = on

    def _osc(self, payload: str, bel: bool) -> None:
        code, _, rest = payload.partition(";")
        if code == MARK_OSC:
            nonce, _, body = rest.partition(";")
            if self.nonce is not None and nonce == self.nonce and self.on_mark:
                kind, _, tail = body.partition(";")
                self.on_mark(kind, tail.split(";") if tail else [])
            return
        if code == "0" or code == "2":
            self.title = rest
            if self.on_title:
                self.on_title(rest)
        elif code == "7":
            if self.on_cwd:
                self.on_cwd(rest)
        elif code == "10" or code == "11" or code == "12":
            if rest == "?" and self.color_query:
                kind = {"10": "fg", "11": "bg", "12": "cursor"}[code]
                rgb = self.color_query(kind, 0)
                if rgb is not None:
                    self._color_reply(code, rgb, bel)
        elif code == "4":
            parts = rest.split(";")
            for k in range(0, len(parts) - 1, 2):
                if parts[k + 1] == "?" and parts[k].isdigit() \
                        and self.color_query:
                    rgb = self.color_query("palette", int(parts[k]))
                    if rgb is not None:
                        self._color_reply(f"4;{parts[k]}", rgb, bel)
        elif code == "52":
            target, _, data = rest.partition(";")
            if data and data != "?" and self.on_clipboard:
                try:
                    text = base64.b64decode(data, validate=False).decode(
                        "utf-8", "replace")
                except ValueError:
                    return
                self.on_clipboard(target, text)

    def _color_reply(self, code: str, rgb: tuple, bel: bool) -> None:
        r, g, b = rgb
        end = "\x07" if bel else "\x1b\\"
        self.respond(
            f"\x1b]{code};rgb:{r * 257:04x}/{g * 257:04x}/{b * 257:04x}{end}"
            .encode())

    def _dcs(self, payload: str) -> None:
        scr = self.screen
        if payload.startswith("$q"):
            what = payload[2:]
            if what == "m":
                params = _style.sgr_params(*_style.lookup(scr.sid))
                self.respond(f"\x1bP1$r{params}m\x1b\\".encode())
            elif what == " q":
                self.respond(f"\x1bP1$r{scr.cursor_shape} q\x1b\\".encode())
            elif what == "r":
                self.respond(
                    f"\x1bP1$r{scr.top + 1};{scr.bot + 1}r\x1b\\".encode())
            else:
                self.respond(b"\x1bP0$r\x1b\\")
        elif payload.startswith("+q"):
            self.respond(b"\x1bP0+r\x1b\\")
