"""The session: an ordered list of blocks, followed by one input area.

The session owns the shell process and decides, from the marks the shell
integration sends, where each piece of the output stream belongs:

* while a command runs, to that command's block;
* while the shell is at its prompt, to the input screen, where the shell's
  own line editor draws the prompt, the text being typed and its
  completion lists;
* otherwise - shell startup, a job-finished notice, a background job
  printing - to a background-output block.

Nothing here depends on the user interface.
"""

from __future__ import annotations

import time
import traceback
from typing import Callable

from ..shell import PtyProcess, ShellRuntime, decode_field
from ..shell.integration import EDIT_KEY, LOOK_KEY, SET_BUFFER_KEY
from ..term import LineStore, Screen, Terminal
from ..term.charwidth import str_width
from ..term.keys import encode_paste
from .block import BACKGROUND, COMMAND, Block, git_branch

STARTUP = "startup"
PROMPT = "prompt"
RUNNING = "command"
CLOSED = "closed"

#: A command handed to the shell normally starts within milliseconds.
#: One that has not started after this many seconds is being asked about
#: (whether to correct a misspelt name, say) and the question is shown.
ASK_AFTER = 0.3
#: The line editor answers what the text box asks of it within
#: milliseconds, too.  If it has put something on the screen and is still
#: at it after this long, it is showing a menu or asking a question, and
#: the keyboard is its own until it answers.
EDIT_SHOWS_AFTER = 0.15
#: One that shows nothing gets this long: a completion can be slow.
EDIT_ASK_AFTER = 3.0

_PRINTS = frozenset(("put_text", "repeat_last", "align_test"))
_MUTATORS = frozenset((
    "linefeed", "carriage_return", "backspace", "tab", "back_tab",
    "reverse_index", "index", "next_line", "cursor_up", "cursor_down",
    "cursor_forward", "cursor_back", "cursor_to", "cursor_col",
    "cursor_row", "save_cursor", "restore_cursor", "erase_line",
    "erase_display", "erase_chars", "insert_chars", "delete_chars",
    "insert_lines", "delete_lines", "scroll_up", "scroll_down", "set_sgr",
    "set_margins", "set_origin", "tab_set", "tab_clear",
    "designate_charset", "shift_charset", "soft_reset", "reset",
))


_LINE_ENDS = frozenset(("linefeed", "index", "next_line"))


class HoldProxy:
    """Stands in for the screen while it is undecided whose output this is.

    Used while the shell's line editor is known to be idle.  Whatever
    arrives then is either the line editor redrawing after all - which
    always starts with a mark - or output from somewhere else, which gives
    itself away by printing.

    Printing goes to the screen for foreign output, and so does everything
    up to the end of that line: the two belong together.  Drawing
    operations at the start of a line are only recorded, because they may
    just as well be the line editor moving its cursor before it sends a
    mark.  They are replayed onto whichever screen turns out to own them.
    """

    def __init__(self, fallback: Screen, on_print: Callable[[], Screen],
                 on_overflow: Callable[[], None]) -> None:
        state = self.__dict__
        state["_fallback"] = fallback
        state["_on_print"] = on_print
        state["_on_overflow"] = on_overflow
        state["_calls"] = []
        state["_target"] = None
        state["_in_line"] = False
        state["_since"] = 0.0

    def bulk_ok(self) -> bool:
        return False

    @property
    def pending(self) -> bool:
        """Recorded operations are waiting for an owner."""
        return bool(self._calls)

    @property
    def since(self) -> float:
        return self._since

    @property
    def target(self):
        """The screen foreign output went to, once some has arrived."""
        return self._target

    def __getattr__(self, name: str):
        state = self.__dict__
        if name in _PRINTS:
            def forward(*args):
                target = state["_target"] = state["_on_print"]()
                self.replay(target)
                state["_in_line"] = True
                return getattr(target, name)(*args)
            return forward
        if name in _MUTATORS:
            if state["_in_line"]:
                if name in _LINE_ENDS:
                    state["_in_line"] = False
                return getattr(state["_target"], name)
            calls = state["_calls"]

            def record(*args):
                if not calls:
                    state["_since"] = time.monotonic()
                calls.append((name, args))
                if len(calls) > 2048:
                    state["_on_overflow"]()
            return record
        return getattr(state["_fallback"], name)

    def __setattr__(self, name: str, value) -> None:
        state = self.__dict__
        if state["_in_line"]:
            setattr(state["_target"], name, value)
            return
        if not state["_calls"]:
            state["_since"] = time.monotonic()
        state["_calls"].append(("=", (name, value)))

    def replay(self, target: Screen) -> None:
        calls = self._calls
        for name, args in calls:
            if name == "=":
                setattr(target, *args)
            else:
                getattr(target, name)(*args)
        calls.clear()


def _spans(text: str) -> list[tuple[int, int, str]]:
    """The colors of a command line as the shell sends them: a "start end
    style" to the line."""
    spans = []
    for line in text.split("\n"):
        start, _, rest = line.partition(" ")
        end, _, style = rest.partition(" ")
        if start.isdecimal() and end.isdecimal() and style:
            spans.append((int(start), int(end), style))
    return spans


def _trim_blank(lines: list[tuple]) -> list[tuple]:
    """Drop blank lines from both ends."""
    start = 0
    end = len(lines)
    while start < end and not lines[start][0].strip():
        start += 1
    while end > start and not lines[end - 1][0].strip():
        end -= 1
    return lines[start:end]


class Session:
    """One shell and its blocks."""

    def __init__(self, cols: int = 80, rows: int = 24, *,
                 runtime: ShellRuntime | None = None,
                 nonce: str | None = None, max_lines: int = 2_000_000,
                 divert: bool = True) -> None:
        self.cols = max(2, cols)
        self.rows = max(2, rows)
        self.runtime = runtime
        self.pty: PtyProcess | None = None
        #: History cap, in output lines (0 = unlimited).
        self.max_lines = max_lines
        #: Route output that arrives at an idle prompt into a background
        #: block instead of letting it land on the prompt.
        self.divert = divert
        self.blocks: list[Block] = []
        self._next_id = 1
        self.state = STARTUP
        self.cwd = ""
        #: Blocks with an id below this have been scrolled out of view by
        #: a clear.
        self.clear_before = 0
        self.exit_status: int | None = None
        self.version = 0
        #: Where the shell draws its prompt and line editor.
        self.input = Screen(self.cols, self.rows, LineStore(), reflow=False)
        self.running: Block | None = None
        self._bg: Block | None = None
        #: The count of clears last seen on the screen that is watched
        #: for them; set whenever a prompt or a command begins.
        self._clear_seen: int
        # Prompt-phase bookkeeping, set anew when a prompt begins.
        self._e_seen = False
        self._t_line: int | None
        self._f_seen: bool
        self._f_bufs: list[str] = []
        #: The shell's line editor is up and taking input.
        self.line_ready = False
        # Idle detection.
        self._hold: HoldProxy | None = None
        self._sync_seq = 0
        self._sync_waiting = False
        #: Input went out since the question was asked.
        self._sync_dirty: bool
        self._sync_sent = 0.0
        #: Wait this long between idle checks that found the line editor
        #: not safely idle (another plugin may redraw it at any time).
        #: Every mark and every key sets it back to nothing.
        self._sync_gap: float
        self._last_input = 0.0
        self._last_output: float
        #: How long after a key the line editor may still act on it by
        #: itself (zsh's KEYTIMEOUT, plus a margin).
        self._key_delay = 0.5
        # Callbacks for the user interface.
        self.on_change: Callable[[], None] | None = None
        self.on_exit: Callable[[int | None], None] | None = None
        self.on_title: Callable[[str], None] | None = None
        self.on_bell: Callable[[], None] | None = None
        self.on_clipboard: Callable[[str, str], None] | None = None
        self.on_drop: Callable[[list[Block]], None] | None = None
        #: Called with the text of a command the shell did not run
        #: because it was not complete.
        self.on_incomplete: Callable[[str], None] | None = None
        #: A command handed over by run_command() that the shell has
        #: neither started nor turned down yet, and when it was handed
        #: over.
        self._submitted: str | None = None
        self._submitted_at = 0.0
        #: How many prompts the shell has begun: whoever shows where the
        #: shell is looks again when this has grown.
        self.prompts = 0
        #: Called with text and cursor once the line editor has done what
        #: ask_editor() asked for.
        self.on_edited: Callable[[str, int], None] | None = None
        #: What ask_editor() asked the line editor and it has not
        #: answered yet: when, the text and the cursor it was handed, and
        #: the version of the session at that moment.  A later version
        #: means that something has come from the shell since.
        self._asked: tuple[float, str, int, int] | None = None
        #: After an answer, the text that follows the line editor's
        #: cursor: what shows under all of its text is there for the text
        #: box (a list of completions).  None when nothing is listed.
        self._listing: str | None = None
        #: Called with a text, the line the shell's plugins would go on
        #: to from it, and the colors they give it: ``(start, end,
        #: style)``, in characters, the style as zsh writes one.
        self.on_hints: Callable[[str, str, list], None] | None = None
        #: Whether the text was edited (see look()) since the line editor
        #: last answered ask_editor(): if so, the next key it is asked
        #: about does not follow the one before.
        self._touched = False
        #: Whether the shell's configuration has plugins that have
        #: something to say about a command line; every prompt tells.
        self.hinting: bool
        #: Looking at a text (see look()): how many times it was asked
        #: for, the number and text of the request that is not answered
        #: yet, and a newer text that waits for that answer.
        self._looks = 0
        self._look: tuple[int, str] | None = None
        self._wanted: tuple[str, int] | None = None

        startup = self._open_bg()
        startup.label = "background output · shell startup"
        self.term = Terminal(startup.screen, self._respond)
        self.term.nonce = runtime.nonce if runtime is not None else nonce
        self.term.on_mark = self._on_mark
        self.term.on_alt = self._on_alt
        self.term.on_title = self._on_title
        self.term.on_bell = self._on_bell
        self.term.on_clipboard = self._on_clipboard
        self._marks = {
            "E": self._mark_end, "P": self._mark_prompt,
            "C": self._mark_command, "A": self._mark_prompt_start,
            "T": self._mark_trash, "I": self._mark_line_init,
            "F": self._mark_line_finish, "S": self._mark_sync,
            "B": self._mark_buffer, "V": self._mark_view,
        }

    # ------------------------------------------------------------------
    # the shell process
    # ------------------------------------------------------------------

    def start(self, shell: str, cwd: str | None = None,
              login: bool = False, env: dict[str, str] | None = None
              ) -> None:
        """Start the shell."""
        if self.runtime is None:
            self.runtime = ShellRuntime()
            self.term.nonce = self.runtime.nonce
        environment = self.runtime.environment(env)
        environment["SHELL"] = shell
        argv = [shell, "-l"] if login else [shell]
        self.pty = PtyProcess(argv, environment, cwd, self.cols, self.rows)

    def fileno(self) -> int:
        return self.pty.fd if self.pty is not None else -1

    def read_ready(self, budget: float = 0.012) -> bool:
        """Process output that is waiting; False once the shell is gone."""
        pty = self.pty
        if pty is None:
            return False
        deadline = time.monotonic() + budget
        alive = True
        quiet = True                 # nothing so far that changes anything
        while True:
            data = pty.read()
            if data is None:
                break
            if data == b"":
                alive = False
                break
            quiet = quiet and self._is_sync_reply(data)
            try:
                self.term.feed(data)
            except Exception:  # noqa: BLE001 - never let output kill us
                # A sequence this terminal mishandles must not take the
                # session down with it; report it and carry on.
                traceback.print_exc()
            if time.monotonic() >= deadline:
                break
        self._after_feed(quiet)
        if not alive:
            self.close()
        return alive

    def feed(self, data: bytes) -> None:
        """Process output bytes directly (used by tests)."""
        quiet = self._is_sync_reply(data)
        self.term.feed(data)
        self._after_feed(quiet)

    @staticmethod
    def _is_sync_reply(data: bytes) -> bool:
        """True for a chunk that is nothing but a reply to an idle check."""
        return (len(data) < 96 and data.startswith(b"\x1b]7717;")
                and data.endswith(b"\x07") and data.count(b"\x1b") == 1
                and b";S;" in data)

    def tick(self) -> None:
        """Housekeeping; the owner calls this a few times a second."""
        hold = self._hold
        if hold is None:
            self._request_sync()
        elif hold.pending and time.monotonic() - hold.since > 0.15:
            # Nothing claimed these operations; they are not the start of
            # anything, so stop sitting on them.
            self._leave_quiescent()

    def write(self, data: bytes) -> None:
        """Send input to the shell or the program running in it."""
        if not data:
            return
        self._leave_quiescent()
        self._sync_dirty = True
        self._sync_gap = 0.0
        self._last_input = time.monotonic()
        if self.pty is not None:
            self.pty.write(data)

    def _respond(self, data: bytes) -> None:
        self.write(data)

    def paste(self, text: str) -> None:
        self.write(encode_paste(text, self.term.bracketed_paste))

    def set_command_buffer(self, text: str) -> bool:
        """Put ``text`` into the shell's input, unsubmitted."""
        if self.state != PROMPT or self.runtime is None:
            return False
        self.runtime.write_buffer(text)
        self.write(SET_BUFFER_KEY)
        return True

    def run_command(self, text: str) -> bool:
        """Hand ``text`` to the shell as a command and submit it: what
        pressing Enter does for an input that is edited outside the
        shell.  False when there is nothing to run or nobody to take it.
        """
        if not text.strip() or not self.input_ready or self.editing:
            return False
        if not self.set_command_buffer(text):
            return False
        self._submitted = text
        self._submitted_at = time.monotonic()
        # Until the line editor says it is back, it is busy with this.
        self.line_ready = False
        self.write(b"\r")
        return True

    def ask_editor(self, key: bytes, fallback: str, text: str,
                   cursor: int) -> bool:
        """Have the shell's line editor do on ``text`` what ``key`` does
        in the shell: complete the word at the cursor, fetch a line of
        the history.  Whoever edits commands outside the shell gets at
        what only the shell knows this way.  ``cursor`` counts characters;
        ``fallback`` names the widget to run where the key runs nothing
        of use.  The answer comes through ``on_edited``.  False when the
        line editor cannot be asked now.
        """
        if not self.input_ready or self.editing or self.runtime is None:
            return False
        self.runtime.write_edit(text, cursor, key, fallback, self._touched)
        self.drop_list()
        self._asked = (time.monotonic(), text, cursor, self.version)
        self.write(EDIT_KEY)
        return True

    def look(self, text: str, cursor: int) -> None:
        """The text was edited and is ``text`` now.  Ask what the shell's
        plugins make of it: the line they would go on to, the colors they
        give it.  The answer comes through ``on_hints``, unless a newer
        text has been handed in by then: of texts that follow each other
        quickly only the last is looked at.
        """
        self._touched = True
        self._wanted = (text, cursor)
        self._look_now()

    def _look_now(self) -> None:
        """Send the text that waits to be looked at, if the line editor
        is free to do so."""
        wanted = self._wanted
        if wanted is None or self._look is not None \
                or not self.input_ready or self.editing \
                or not self.hinting:
            return
        self._wanted = None
        text, cursor = wanted
        if not text:
            # Nothing to say about no text, and no need to ask.
            if self.on_hints:
                self.on_hints("", "", [])
            return
        self._looks += 1
        self.runtime.write_look(text, cursor, self._looks)
        self._look = (self._looks, text)
        self.write(LOOK_KEY)

    def drop_list(self) -> None:
        """What the line editor lists under its text is of no use any
        more: the text it was for has changed."""
        if self._listing is not None:
            self._listing = None
            self.version += 1
            if self.on_change:
                self.on_change()

    def resize(self, cols: int, rows: int, width: int = 0,
               height: int = 0) -> None:
        cols = max(2, cols)
        rows = max(2, rows)
        if cols == self.cols and rows == self.rows:
            return
        self.cols = cols
        self.rows = rows
        self._leave_quiescent()
        # The screen in use, which may be a full-screen program's; then
        # the ones behind it or waiting their turn.
        self.term.screen.resize(cols, rows)
        self.input.resize(cols, rows)
        if self.running is not None and self.running.screen is not None:
            self.running.screen.resize(cols, rows)
        if self._bg is not None and self._bg.screen is not None:
            self._bg.screen.resize(cols, rows)
        if self.pty is not None:
            self.pty.resize(cols, rows, width, height)
        self.version += 1

    def close(self) -> None:
        """The shell is gone (or must go): settle everything."""
        if self.state == CLOSED:
            return
        self._leave_quiescent()
        if self.running is not None:
            self._finish_running(None)
        self._close_bg()
        self.term.set_screen(self.input)
        self.state = CLOSED
        self.line_ready = False
        self._asked = None
        if self.pty is not None:
            self.pty.close()
            self.exit_status = self.pty.poll()
        if self.runtime is not None:
            self.runtime.cleanup()
        self.version += 1
        if self.on_change:
            self.on_change()
        if self.on_exit:
            self.on_exit(self.exit_status)

    # ------------------------------------------------------------------
    # views for the user interface
    # ------------------------------------------------------------------

    @property
    def prompt_active(self) -> bool:
        return self.state == PROMPT

    @property
    def fullscreen(self) -> bool:
        """A full-screen program has taken over the view."""
        return self.term.alt_active

    @property
    def input_ready(self) -> bool:
        """The shell's line editor waits for a command and none is on
        its way to it: one can be handed over."""
        return (self.line_ready and self._submitted is None
                and not self.term.alt_active and not self._editor_shows())

    @property
    def editing(self) -> bool:
        """The line editor was asked something by ask_editor() and has
        not answered yet."""
        return self._asked is not None

    def _editor_shows(self) -> bool:
        """The line editor was asked something and needs the keyboard for
        it: it has put a menu or a question on the screen, or it has been
        at it for very long."""
        asked = self._asked
        return asked is not None and self._overdue(asked[0], asked[3])

    def _overdue(self, since: float, version: int) -> bool:
        waited = time.monotonic() - since
        return waited > EDIT_ASK_AFTER or (
            self.version != version and waited > EDIT_SHOWS_AFTER)

    @property
    def shell_asks(self) -> bool:
        """The shell wants an answer before it goes on, and what it asks
        is on its own input line.  Either a command was handed over a
        while ago and has neither started nor been turned down, or the
        line editor shows something for what ask_editor() asked."""
        if self._editor_shows():
            return True
        return (self._submitted is not None
                and time.monotonic() - self._submitted_at > ASK_AFTER)

    def listed(self) -> tuple[int, int]:
        """What the line editor shows for the text box after an answer to
        ask_editor(), a list of completions as a rule: the first row of
        the input screen under the editor's own text, and how many rows
        there are from there on."""
        tail = self._listing
        if tail is None or self.state != PROMPT:
            return 0, 0
        screen = self.input
        # The text after the cursor takes the rest of the cursor's row,
        # and a row more for every line it has and every time one wraps.
        first = screen.cy + 1
        width = screen.cx
        for index, line in enumerate(tail.split("\n")):
            if index:
                first += 1
                width = 0
            width += str_width(line)
            first += (width - 1) // self.cols if width else 0
        return first, max(0, screen.extent() - first)

    def cursor_screen(self) -> Screen:
        """The screen whose cursor is the terminal's cursor."""
        if self.term.alt_active:
            return self.term.screen
        if self.state == PROMPT:
            return self.input
        return self.term.screen

    def clear_blocks(self) -> list[Block]:
        """Remove every block (a command that still runs stays)."""
        if self.state != STARTUP:
            self._close_bg()
        keep = [b for b in self.blocks if b.running]
        removed = [b for b in self.blocks if not b.running]
        self.blocks = keep
        self.clear_before = 0
        if removed and self.on_drop:
            self.on_drop(removed)
        self.version += 1
        if self.on_change:
            self.on_change()
        return removed

    # ------------------------------------------------------------------
    # blocks
    # ------------------------------------------------------------------

    def _list(self, block: Block) -> None:
        block.id = self._next_id
        self._next_id += 1
        self.blocks.append(block)

    def _open_bg(self) -> Block:
        """The background block that takes output right now."""
        bg = self._bg
        if bg is None:
            # Nothing is listed after a background block without closing
            # it first, so an open one is always the last block.
            bg = self._bg = Block(BACKGROUND, self.cols, self.rows)
            self._bg_listed = False
        return bg

    def _materialize_bg(self) -> None:
        """List the background block once it has something to show."""
        bg = self._bg
        if bg is not None and not self._bg_listed and bg.has_output():
            self._list(bg)
            self._bg_listed = True

    def _close_bg(self) -> None:
        bg = self._bg
        if bg is None:
            return
        if self._hold is not None and self._hold.target is bg.screen:
            self._leave_quiescent()
        self._materialize_bg()
        bg.finish(None)
        bg.duration = None
        self._bg = None

    def _append_bg(self, lines: list[tuple]) -> None:
        lines = _trim_blank(lines)
        if not lines:
            return
        bg = self._open_bg()
        bg.screen.push_lines(lines)

    def _finish_running(self, status: int | None) -> None:
        block = self.running
        block.finish(status)
        if block.cleared and not block.store.texts:
            # "clear" itself leaves nothing to look at.
            self.clear_before = max(self.clear_before, block.id + 1)
        self.running = None
        self._enforce_cap()

    def _enforce_cap(self) -> None:
        """Drop the oldest blocks, whole, once history exceeds the cap."""
        cap = self.max_lines
        blocks = self.blocks
        total = sum(len(b.store.texts) for b in blocks)
        # Called when a block has just finished: none is running now.
        dropped = []
        while cap > 0 and total > cap and len(blocks) > 1:
            oldest = blocks[0]
            total -= len(oldest.store.texts)
            dropped.append(oldest)
            del blocks[0]
        if dropped and self.on_drop:
            self.on_drop(dropped)

    def _limit_live(self) -> None:
        """Keep one endless block from growing without bound.

        The cap normally drops whole blocks only.  A single block that is
        larger than the entire cap has nothing older to drop, so its own
        oldest lines go.
        """
        cap = self.max_lines
        if cap <= 0:
            return
        slack = max(1000, cap // 20)
        for block in (self.running, self._bg):
            if block is not None:
                count = len(block.store.texts)
                if count > cap + slack:
                    block.store.drop_front(count - cap)

    # ------------------------------------------------------------------
    # after each chunk of output
    # ------------------------------------------------------------------

    def _after_feed(self, quiet: bool = False) -> None:
        if quiet:
            return
        self._last_output = time.monotonic()
        self._materialize_bg()
        self._check_clear()
        self._limit_live()
        self.version += 1
        if self.on_change:
            self.on_change()

    def _check_clear(self) -> None:
        """Notice that the program (or the shell) cleared the display."""
        if self.state == PROMPT:
            count = self.input.clear_count
            if count != self._clear_seen:
                self._clear_seen = count
                # Whatever arrives from now on must show below the fold,
                # not join a block that was just scrolled away.
                self._close_bg()
                self.clear_before = self._next_id
        elif self.state == RUNNING:
            screen = self.running.screen
            if screen.clear_count != self._clear_seen:
                self._clear_seen = screen.clear_count
                self.running.cleared = True
                self.clear_before = self.running.id

    # ------------------------------------------------------------------
    # marks
    # ------------------------------------------------------------------

    def _on_mark(self, kind: str, fields: list[str]) -> None:
        handler = self._marks.get(kind)
        if handler is None or self.state == CLOSED:
            return                   # not a mark, or the shell has gone
        if kind == "S":
            handler(fields)
            return
        # A mark is the line editor (or the shell) speaking: what was held
        # back before it was its cursor getting into position.
        self._leave_quiescent(by_mark=True)
        self._sync_gap = 0.0
        handler(fields)

    @staticmethod
    def _status(fields: list[str]) -> int | None:
        if fields and fields[0].isdecimal():
            return int(fields[0])
        return None

    def _mark_end(self, fields: list[str]) -> None:
        """E: a command has ended, or a prompt cycle with no command."""
        self._cycle(self._status(fields))
        self._e_seen = True

    def _mark_prompt(self, fields: list[str]) -> None:
        """P: the prompt hooks are running."""
        if not self._e_seen:
            self._cycle(self._status(fields))
        elif len(fields) > 1 and fields[1] == "off":
            # With NOTIFY off, zsh reports finished jobs right here,
            # between the end of the command and the prompt hooks.
            screen = self.input
            screen.finish()
            lines = screen.store.take_all()
            screen.reset()
            self._append_bg(lines)
        self._e_seen = False
        if len(fields) > 2:
            self.cwd = decode_field(fields[2])
        if len(fields) > 3 and fields[3].isdecimal():
            # KEYTIMEOUT is in hundredths of a second.
            self._key_delay = min(5.0, int(fields[3]) / 100.0) + 0.1
        self.hinting = self.runtime is not None and fields[4:5] == ["1"]

    def _cycle(self, status: int | None) -> None:
        self._check_clear()
        if self.state == RUNNING:
            self._finish_running(status)
        elif self.state == PROMPT:
            self._prompt_leftovers()
        else:
            self._close_bg()             # the shell has finished starting
        self._begin_prompt()

    def _begin_prompt(self) -> None:
        screen = self.input
        screen.reset()
        screen.store.clear()
        self.term.set_screen(screen)
        self.term.reset_input_modes()
        self.state = PROMPT
        self.line_ready = False
        self._submitted = None
        asked, self._asked = self._asked, None
        self._listing = None
        # A new line: no look is under way, and what the shell has to
        # say about plugins is yet to come.
        self._look = None
        self.hinting = False
        self.prompts += 1
        self._clear_seen = screen.clear_count
        self._t_line = None
        self._f_seen = False
        self._f_bufs = []
        if asked is not None and self.on_edited:
            # The line editor gave up what it was asked for (it was
            # interrupted, as a rule): whoever asked has the text as it
            # was.
            self.on_edited(asked[1], asked[2])

    def _prompt_leftovers(self) -> None:
        """A prompt cycle ended and no command ran.

        Whatever the shell printed after taking the line - a parse error,
        most often - would vanish with the input screen.  Keep it: as a
        block of its own when a command was submitted, as background
        output otherwise.
        """
        mark = self._t_line
        if mark is None:
            return
        screen = self.input
        base = screen.store.base
        screen.finish()
        lines = screen.store.take_all()
        tail = _trim_blank(lines[mark - base:])
        if not tail:
            return
        if self._f_bufs:
            self._close_bg()
            block = Block(COMMAND, self.cols, self.rows,
                          "\n".join(self._f_bufs), self.cwd,
                          git_branch(self.cwd))
            block.not_run = True
            self._list(block)
            block.screen.push_lines(tail)
            block.finish(None)
            self._enforce_cap()
        else:
            self._append_bg(tail)

    def _mark_command(self, fields: list[str]) -> None:
        """C: a command is about to run."""
        cwd = decode_field(fields[0]) if fields else self.cwd
        command = decode_field(fields[1]) if len(fields) > 1 else ""
        if not command and self._f_bufs:
            command = "\n".join(self._f_bufs)
        self._check_clear()
        if self.state == RUNNING:
            self._finish_running(None)
        self._close_bg()
        block = Block(COMMAND, self.cols, self.rows, command, cwd,
                      git_branch(cwd))
        self._list(block)
        self.running = block
        self.state = RUNNING
        self.line_ready = False
        self._submitted = None
        if self._asked is not None:
            # What the line editor was asked to work on has been run
            # instead: whoever asked has nothing left of it.
            self._asked = None
            if self.on_edited:
                self.on_edited("", 0)
        self.term.set_screen(block.screen)
        self._clear_seen = 0

    def _mark_prompt_start(self, fields: list[str]) -> None:
        """A: the prompt is being drawn from its first character.

        If the line editor had stepped below its display before (T) and
        the prompt now starts down there, whatever lies in between was
        printed by something else: move it into a background block and
        let the prompt start at the top of the input screen again.
        """
        if self.state != PROMPT:
            return
        mark = self._t_line
        self._t_line = None
        if mark is None or self._f_seen or self.term.alt_active:
            return
        screen = self.input
        if screen.lines_above_cursor() < mark:
            return  # redrawn in place: the editor had shown a message
        base = screen.store.base
        screen.flush_above_cursor()
        lines = screen.store.take_all()
        self._append_bg(lines[mark - base:])

    def _mark_trash(self, fields: list[str]) -> None:
        """T: the line editor moved below its display."""
        if self.state == PROMPT and not self.term.alt_active:
            self._t_line = self.input.lines_above_cursor()

    def _mark_line_init(self, fields: list[str]) -> None:
        """I: the line editor starts on a line."""
        text = self._submitted
        called_off = text is not None and self._f_seen
        self._f_seen = False
        if called_off:
            # The shell took the line and its editor is back, with no
            # command started and no new prompt: it wants the rest of a
            # command that is not complete.  The command is called off
            # and whoever handed it over gets it back; the editor counts
            # as ready again with the prompt that follows.
            self._submitted = None
            self.write(b"\x03")
            if self.on_incomplete:
                self.on_incomplete(text)
            return
        self.line_ready = self.state == PROMPT
        self._look_now()                # a text may have waited for this

    def _mark_buffer(self, fields: list[str]) -> None:
        """B: the line editor's text after what ask_editor() asked for,
        and what the plugins make of that text."""
        if self._asked is None or len(fields) < 4:
            return
        self._asked = None
        self._touched = False
        text = decode_field(fields[1])
        at = fields[0]
        cursor = min(int(at), len(text)) if at.isdecimal() else len(text)
        self._listing = text[cursor:]
        # The answer says what the plugins make of this text; a look at
        # the one before it is of no use any more.
        self._wanted = None
        if self.on_edited:
            self.on_edited(text, cursor)
        if self.on_hints:
            self.on_hints(text, decode_field(fields[2]),
                          _spans(decode_field(fields[3])))

    def _mark_view(self, fields: list[str]) -> None:
        """V: what the plugins make of a text look() asked about."""
        look = self._look
        if look is None or len(fields) < 3 or fields[0] != str(look[0]):
            return
        self._look = None
        if self._wanted is None and self.on_hints:
            self.on_hints(look[1], decode_field(fields[1]),
                          _spans(decode_field(fields[2])))
        self._look_now()                # a newer text may be waiting

    def _mark_line_finish(self, fields: list[str]) -> None:
        """F: the line editor hands over a line."""
        if self.state != PROMPT:
            return
        self._f_seen = True
        self.line_ready = False
        text = decode_field(fields[0]) if fields else ""
        if text.strip():
            self._f_bufs.append(text)

    # ------------------------------------------------------------------
    # idle detection
    # ------------------------------------------------------------------

    def _request_sync(self) -> None:
        """Ask the line editor to say when it is idle.

        Only once it has been quiet for a moment, so that the question is
        answered at once.  zsh must never find it waiting while it is
        busy - starting a prompt, or with a key still working its way
        through (an Escape that may begin a longer sequence): an interrupt
        arriving then would not take effect until the next key.
        """
        if (not self.divert or self.runtime is None or self.state != PROMPT
                or not self.line_ready):
            return
        now = time.monotonic()
        if now - self._last_input < self._key_delay \
                or now - self._last_output < 0.15:
            return
        since = now - self._sync_sent
        if since < self._sync_gap or (self._sync_waiting and since < 2.0):
            return
        self._sync_seq += 1
        if self.runtime.request_sync(self._sync_seq):
            self._sync_waiting = True
            self._sync_dirty = False
            self._sync_sent = now

    def _mark_sync(self, fields: list[str]) -> None:
        """S: the line editor is idle, waiting for a key."""
        if len(fields) < 2 or not fields[0].isdecimal() \
                or int(fields[0]) != self._sync_seq or not self._sync_waiting:
            return  # no answer to the question that is open
        self._sync_waiting = False
        if self.state != PROMPT or self._sync_dirty:
            return  # input went out after we asked; the next tick asks again
        if time.monotonic() - self._last_input < self._key_delay:
            return
        if fields[1] == "1" and self.term.screen is self.input:
            # Exactly one handler - ours - means nothing else is in a
            # position to redraw the line behind our back.
            self._sync_gap = 0.0
            self._hold = HoldProxy(self.input, self._hold_print,
                                   self._leave_quiescent)
            self.term.screen = self._hold
        else:
            # Another plugin watches for events and may redraw the line
            # at any moment.  Look again later, and ever less often.
            self._sync_gap = min(4.0, max(0.5, self._sync_gap * 2))

    def _hold_print(self) -> Screen:
        """Something printed while the line editor was idle."""
        return self._open_bg().screen

    def _leave_quiescent(self, by_mark: bool = False) -> None:
        """Stop holding output back.

        Operations still unclaimed go to the input screen when a mark
        follows them (they were the line editor's) or when nothing foreign
        has printed; otherwise they trail the foreign output.
        """
        hold = self._hold
        if hold is None:
            return
        self._hold = None
        self._swap(self.input)
        target = hold.target
        if by_mark or target is None:
            hold.replay(self.input)
        else:
            hold.replay(target)

    def _swap(self, new: Screen) -> None:
        """Put ``new`` where the stand-in was: it is the terminal's main
        screen for as long as it is in use."""
        term = self.term
        if term.alt_active:
            term._main = new
        else:
            term.screen = new

    # ------------------------------------------------------------------
    # terminal events
    # ------------------------------------------------------------------

    def _on_alt(self, active: bool) -> None:
        if active and self.running is not None:
            self.running.fullscreen = True

    def _on_title(self, title: str) -> None:
        if self.on_title:
            self.on_title(title)

    def _on_bell(self) -> None:
        if self.on_bell:
            self.on_bell()

    def _on_clipboard(self, target: str, text: str) -> None:
        if self.on_clipboard:
            self.on_clipboard(target, text)
