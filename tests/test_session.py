"""The session router, driven by hand-written output streams."""

from __future__ import annotations

import os
import random
import select

import pytest

import jupyterminal.session.session as session_module
from conftest import COLS, NONCE, mark, prompt, submit
from jupyterminal.session import (BACKGROUND, BOTH, CLOSED, COMMAND, PROMPT,
                                  RUNNING, STARTUP, FilterSpec, Session,
                                  copy_text, filter_indices, find_matches,
                                  output_text)
from jupyterminal.shell import ShellRuntime
from jupyterminal.shell.integration import SET_BUFFER_KEY


def make(**kwargs) -> Session:
    return Session(COLS, 10, nonce=NONCE, **kwargs)


def run(session: Session, command: str, output: str, status: int = 0):
    session.feed(submit(command) + output.encode() + prompt(status))
    return session.blocks[-1]


def input_rows(session: Session) -> list[str]:
    screen = session.input
    return [row.text().rstrip() for row in screen.grid[:screen.extent()]]


# -- the block lifecycle -----------------------------------------------------

def test_nothing_at_all_before_the_first_prompt():
    session = make()
    assert session.state == STARTUP and session.blocks == []
    session.feed(prompt())
    assert session.state == PROMPT and session.blocks == []
    assert input_rows(session) == ["%"]
    assert session.line_ready


def test_startup_output_becomes_a_background_block_at_the_top():
    session = make()
    session.feed(b"Last login: yesterday\r\n")
    assert len(session.blocks) == 1 and session.blocks[0].running
    session.feed(b"zshrc:3: command not found: foo\r\n" + prompt())
    block, = session.blocks
    assert block.kind == BACKGROUND and "startup" in block.label
    assert not block.running
    assert block.lines() == ["Last login: yesterday",
                             "zshrc:3: command not found: foo"]


def test_a_command_becomes_a_block():
    session = make()
    session.feed(prompt())
    session.feed(submit("echo hi"))
    block, = session.blocks
    assert session.state == RUNNING and block.running
    assert block.command == "echo hi" and block.cwd == "/work"
    session.feed(b"hi\r\n")
    assert block.lines() == ["hi"]
    session.feed(prompt())
    assert not block.running and block.exit_code == 0 and not block.failed
    assert block.lines() == ["hi"]
    assert session.state == PROMPT
    assert input_rows(session) == ["%"]


def test_failure_is_recorded():
    session = make()
    session.feed(prompt())
    block = run(session, "false", "", status=1)
    assert block.exit_code == 1 and block.failed
    block = run(session, "true", "")
    assert block.exit_code == 0 and not block.failed


def test_empty_submission_creates_no_block():
    session = make()
    session.feed(prompt())
    session.feed(mark("F", "") + b"\x1b[?2004l\r\r\n" + mark("T") + prompt())
    assert session.blocks == []
    assert input_rows(session) == ["%"]


def test_output_without_trailing_newline_is_exact():
    session = make()
    session.feed(prompt())
    block = run(session, "printf abc", "abc")
    assert output_text(block) == "abc"


def test_typed_text_never_reaches_a_block():
    session = make()
    session.feed(prompt())
    session.feed(b"ec\x08\x08echo secret-typing")
    assert session.blocks == []
    assert "secret-typing" in input_rows(session)[0]


def test_interrupted_command_finishes_with_its_status():
    session = make()
    session.feed(prompt())
    block = run(session, "sleep 5", "^C\r\n", status=130)
    assert block.exit_code == 130 and block.failed
    assert block.lines() == ["^C"]


def test_command_text_is_decoded():
    session = make()
    session.feed(prompt())
    session.feed(mark("F", "x") + mark("T")
                 + mark("C", "/a%3Bb", "echo 'a%3Bb%25c'%0Aecho two"))
    block, = session.blocks
    assert block.command == "echo 'a;b%c'\necho two"
    assert block.cwd == "/a;b"


def test_history_expansion_echo_is_not_output():
    session = make()
    session.feed(prompt())
    session.feed(b"!!" + mark("F", "!!") + b"\r\r\n" + mark("T")
                 + b"echo one\r\n" + mark("C", "/work", "echo one")
                 + b"one\r\n" + prompt())
    block, = session.blocks
    assert block.command == "echo one"
    assert block.lines() == ["one"]


def test_a_rejected_line_keeps_the_shells_message():
    session = make()
    session.feed(prompt())
    session.feed(b"echo )" + mark("F", "echo )") + b"\r\r\n" + mark("T")
                 + b"zsh: parse error near `)'\r\n" + prompt(1))
    block, = session.blocks
    assert block.kind == COMMAND and block.command == "echo )"
    assert block.not_run and block.exit_code is None
    assert block.lines() == ["zsh: parse error near `)'"]
    assert input_rows(session) == ["%"]


def test_end_without_the_early_mark():
    """PROMPT_SP off: only the prompt-hook mark closes the command."""
    session = make()
    session.feed(mark("P", "0", "on", "/work") + mark("A") + b"% ")
    session.feed(submit("false") + mark("P", "1", "on", "/work")
                 + mark("A") + b"% ")
    block, = session.blocks
    assert block.exit_code == 1 and session.state == PROMPT


def test_shell_exit_closes_the_running_block():
    session = make()
    session.feed(prompt())
    session.feed(submit("exit"))
    seen = []
    session.on_exit = seen.append
    session.close()
    assert not session.blocks[0].running
    assert seen == [None]


# -- background output -------------------------------------------------------

def test_job_notice_at_the_prompt_goes_to_a_background_block():
    session = make()
    session.feed(prompt())
    run(session, "sleep 1 &", "[1] 4242\r\n")
    session.feed(b"ec")
    # The editor steps below its display, zsh prints, the prompt is redrawn.
    session.feed(b"\r\r\n" + mark("T") + b"[1]  + done       sleep 1\r\n"
                 + b"\x1b[0m\x1b[J" + mark("A") + b"% ec\x1b[K")
    assert len(session.blocks) == 2
    notice = session.blocks[1]
    assert notice.kind == BACKGROUND and notice.command == ""
    assert notice.lines() == ["[1]  + done       sleep 1"]
    assert input_rows(session) == ["% ec"]
    assert session.input.cy == 0


def test_editor_messages_stay_in_the_input_area():
    session = make()
    session.feed(prompt())
    session.feed(b"ls")
    # A completion list: shown below, then the cursor returns to the line.
    session.feed(b"\r\r\n" + mark("T") + b"alpha  beta\x1b[A\r\x1b[2Cls\x1b[K")
    assert session.blocks == []
    assert input_rows(session) == ["% ls", "alpha  beta"]
    # A later in-place redraw of the prompt is not background output.
    session.feed(b"\r\x1b[J" + mark("A") + b"% ls\x1b[K")
    assert session.blocks == []
    assert input_rows(session) == ["% ls"]


def test_consecutive_background_output_shares_a_block():
    session = make()
    session.feed(prompt())
    for name in ("one", "two"):
        notice = f"[1]  + done  {name}\r\n".encode()
        session.feed(b"\r\r\n" + mark("T") + notice + b"\x1b[J"
                     + mark("A") + b"% \x1b[K")
    block, = session.blocks
    assert block.lines() == ["[1]  + done  one", "[1]  + done  two"]
    run(session, "true", "")
    session.feed(b"\r\r\n" + mark("T") + b"[2]  + done  three\r\n\x1b[J"
                 + mark("A") + b"% \x1b[K")
    assert [b.kind for b in session.blocks] == [BACKGROUND, COMMAND,
                                                BACKGROUND]


def test_notices_printed_before_the_prompt_with_notify_off():
    session = make()
    session.feed(prompt())
    session.feed(submit("sleep 9") + mark("E", "0") + b" " * COLS + b"\r\r"
                 + b"[1]  + done       sleep 1\r\n"
                 + mark("P", "0", "off", "/work") + mark("A") + b"% ")
    command, notice = session.blocks
    assert command.lines() == []
    assert notice.kind == BACKGROUND
    assert notice.lines() == ["[1]  + done       sleep 1"]
    assert input_rows(session) == ["%"]


def test_prompt_decoration_is_not_background_output():
    session = make()
    session.feed(mark("E", "0") + b" " * COLS + b"\r\rdecoration\r\n"
                 + mark("P", "0", "on", "/work") + mark("A") + b"% ")
    assert session.blocks == []
    assert input_rows(session) == ["decoration", "%"]


class FakeRuntime:
    """Stands in for the shell's private directory: notes what the session
    asks of it."""

    nonce = NONCE

    def __init__(self, listening: bool = True) -> None:
        self.requests: list[int] = []
        self.listening = listening
        self.buffers: list[str] = []
        self.cleaned = 0

    def environment(self, base: dict | None = None) -> dict:
        return dict(base or {}, ZDOTDIR="/shims")

    def request_sync(self, seq: int) -> bool:
        self.requests.append(seq)
        return self.listening

    def write_buffer(self, text: str) -> None:
        self.buffers.append(text)

    def cleanup(self) -> None:
        self.cleaned += 1


class FakePty:
    """Stands in for the shell process: output to hand out piece by piece,
    and a note of everything the session does to it."""

    def __init__(self, *chunks: bytes | None, status: int | None = 0) -> None:
        self.chunks = list(chunks)
        self.status = status
        self.fd = 7
        self.written: list[bytes] = []
        self.sizes: list[tuple] = []
        self.closed = 0

    def read(self) -> bytes | None:
        return self.chunks.pop(0) if self.chunks else None

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, cols: int, rows: int, width: int, height: int) -> None:
        self.sizes.append((cols, rows, width, height))

    def close(self) -> None:
        self.closed += 1

    def poll(self) -> int | None:
        return self.status


class Clock:
    """Stands in for the session's ``time`` module: time passes only when
    a test says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    made = Clock()
    monkeypatch.setattr(session_module, "time", made)
    return made


def settle(session: Session) -> None:
    """Let enough time pass for an idle check to be due."""
    session._last_input = 0.0
    session._last_output = 0.0
    session._sync_sent = 0.0


def idle(session: Session, runtime: FakeRuntime, count: str = "1") -> None:
    """Let the line editor report itself idle."""
    settle(session)
    session.tick()
    session.feed(mark("S", str(runtime.requests[-1]), count))


def test_output_at_an_idle_prompt_is_diverted():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"ec")
    assert runtime.requests == []            # never asked while it draws
    idle(session, runtime)
    # A background job prints while the editor waits for a key.
    session.feed(b"\x1b[31mRAWBG\x1b[0m line one\r\nline two\r\n")
    block, = session.blocks
    assert block.kind == BACKGROUND
    assert block.lines() == ["RAWBG line one", "line two"]
    assert input_rows(session) == ["% ec"]
    assert (session.input.cy, session.input.cx) == (0, 4)
    # The next key ends the diversion; the editor's echo goes to the input.
    session.write(b"h")
    session.feed(b"h")
    assert input_rows(session) == ["% ech"]
    assert block.lines() == ["RAWBG line one", "line two"]


def test_a_redraw_at_an_idle_prompt_is_not_diverted():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    idle(session, runtime)
    # An asynchronous prompt update: cursor movement, then the mark.
    session.feed(b"\r\x1b[J" + mark("A") + b"new% \x1b[K")
    assert session.blocks == []
    assert input_rows(session) == ["new%"]


def test_no_diversion_while_other_handlers_may_redraw():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"gi")
    idle(session, runtime, "2")
    session.feed(b"t status")  # an asynchronous suggestion being drawn
    assert session.blocks == []
    assert input_rows(session) == ["% git status"]


def test_stale_idle_reply_is_ignored():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    settle(session)
    session.tick()
    asked = runtime.requests[-1]
    session.write(b"x")                      # input goes out after asking
    session.feed(mark("S", str(asked), "1") + b"x")
    assert session.blocks == []
    assert input_rows(session) == ["% x"]
    assert session._hold is None             # not trusted: no diversion
    session.tick()
    assert runtime.requests[-1] == asked     # too soon after the key
    settle(session)                          # ... the key has settled
    session.tick()
    assert runtime.requests[-1] == asked + 1


def test_no_idle_check_rides_along_with_input():
    """An interrupt must find the line editor with nothing else to do."""
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"echo abc")
    idle(session, runtime)
    asked = len(runtime.requests)
    session.write(b"\x03")
    session.tick()
    assert len(runtime.requests) == asked    # nothing sent with the key
    session.feed(b"\r\r\n" + mark("T") + prompt(130))
    session.tick()
    assert len(runtime.requests) == asked    # still within the key's delay
    session._last_input = 0.0                # the key is long past, but the
    session.tick()                           # prompt has only just appeared
    assert len(runtime.requests) == asked
    session._last_output = 0.0               # ... and now it has been quiet
    session.tick()
    assert len(runtime.requests) == asked + 1


def test_idle_checks_back_off_when_another_plugin_may_redraw():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    versions = session.version
    idle(session, runtime, "2")
    assert session._hold is None
    assert session.version == versions       # a bare reply repaints nothing
    asked = len(runtime.requests)
    session._last_output = 0.0
    for _ in range(50):
        session.tick()
    assert len(runtime.requests) == asked    # not again right away
    assert session._sync_gap == 0.5
    session._sync_sent = 0.0                 # ... half a second later
    session.tick()
    session.feed(mark("S", str(runtime.requests[-1]), "2"))
    assert len(runtime.requests) == asked + 1 and session._sync_gap == 1.0


def test_editor_moves_before_a_mark_are_not_foreign_output():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"ec")
    idle(session, runtime)
    session.feed(b"RAW-RESULT\r\n")          # a background job prints
    # ... then finishes: the editor steps down, zsh reports, prompt again.
    session.feed(b"\r\r\n" + mark("T") + b"[1]  + done  job\r\n\x1b[J"
                 + mark("A") + b"% ec\x1b[K")
    block, = session.blocks
    assert block.lines() == ["RAW-RESULT", "[1]  + done  job"]
    assert input_rows(session) == ["% ec"]
    assert (session.input.cy, session.input.cx) == (0, 4)


def test_prompt_redrawn_in_place_after_foreign_output():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    two_line = b"top line\r\n% "
    session.feed(prompt(text="x")[:-len(b"x\x1b[K" + mark("I")
                                        + b"\x1b[?2004h")]
                 + two_line + b"\x1b[K" + mark("I"))
    assert input_rows(session) == ["top line", "%"]
    idle(session, runtime)
    session.feed(b"tick 1\r\n")
    # The prompt's clock ticks: up to its first row, erase, draw again.
    session.feed(b"\r\x1b[A\x1b[J" + mark("A") + two_line + b"\x1b[K")
    idle(session, runtime)
    session.feed(b"tick 2\r\n")
    session.feed(b"\r\x1b[A\x1b[J" + mark("A") + two_line + b"\x1b[K")
    block, = session.blocks
    assert block.lines() == ["tick 1", "tick 2"]
    assert input_rows(session) == ["top line", "%"]


def test_unclaimed_cursor_updates_are_not_held_for_long():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"hello")
    idle(session, runtime)
    session.feed(b"\x1b[2 q\x08")            # cursor shape, one step left
    assert session.input.cx == 7              # held: could be anyone's
    session._hold.__dict__["_since"] = 0.0    # ... but nothing follows
    session.tick()
    assert (session.input.cx, session.input.cursor_shape) == (6, 2)
    assert session.blocks == []


def test_background_output_after_a_clear_shows_below_the_fold():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    idle(session, runtime)
    session.feed(b"tick 1\r\n")
    first, = session.blocks
    session.write(b"\x0c")                    # Ctrl+L
    session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    assert session.clear_before > first.id
    idle(session, runtime)
    session.feed(b"tick 2\r\n")
    old, new = session.blocks
    assert old.lines() == ["tick 1"] and new.lines() == ["tick 2"]
    assert new.id >= session.clear_before


# -- clearing, full screen, history cap --------------------------------------

def test_clear_at_the_prompt_scrolls_blocks_out_of_view():
    session = make()
    session.feed(prompt())
    first = run(session, "ls", "a\r\n")
    assert session.clear_before <= first.id
    session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    assert session.clear_before > first.id
    assert len(session.blocks) == 1          # still there, just out of view
    second = run(session, "pwd", "/work\r\n")
    assert second.id >= session.clear_before


def test_the_clear_command_scrolls_itself_out_too():
    session = make()
    session.feed(prompt())
    run(session, "ls", "a\r\n")
    block = run(session, "clear", "\x1b[H\x1b[2J\x1b[3J")
    assert session.clear_before == block.id + 1
    kept = run(session, "clear; ls", "\x1b[H\x1b[2J\x1b[3Ja\r\n")
    assert session.clear_before == kept.id
    assert kept.lines() == ["a"]


def test_full_screen_program_leaves_a_marker_and_no_content():
    session = make()
    session.feed(prompt())
    session.feed(submit("vim notes") + b"before\r\n")
    block = session.blocks[0]
    session.feed(b"\x1b[?1049h\x1b[H\x1b[2Jfile contents\r\n~\r\n~")
    assert session.fullscreen and block.fullscreen
    session.feed(b"\x1b[?1049l" + b"after\r\n" + prompt())
    assert not session.fullscreen
    assert block.fullscreen and block.lines() == ["before", "after"]


def test_dying_in_full_screen_mode_restores_the_block_list():
    session = make()
    session.feed(prompt())
    session.feed(submit("htop") + b"\x1b[?1049h\x1b[?1000hstuff")
    assert session.fullscreen and session.term.mouse_mode == 1000
    session.feed(prompt(137))
    assert not session.fullscreen and session.term.mouse_mode == 0
    assert session.blocks[0].exit_code == 137


def test_history_cap_drops_whole_blocks():
    session = make(max_lines=25)
    dropped = []
    session.on_drop = dropped.extend
    session.feed(prompt())
    for n in range(4):
        run(session, f"seq {n}", "".join(f"{i}\r\n" for i in range(10)))
    assert [b.command for b in session.blocks] == ["seq 2", "seq 3"]
    assert [b.command for b in dropped] == ["seq 0", "seq 1"]
    assert all(len(b.lines()) == 10 for b in session.blocks)


def test_one_endless_block_is_bounded():
    session = make(max_lines=2000)
    session.feed(prompt())
    session.feed(submit("yes"))
    chunk = b"y\r\n" * 3000
    for _ in range(4):
        session.feed(chunk)
    block = session.blocks[0]
    assert 1900 <= len(block.store.texts) <= 3200
    assert block.store.dropped > 0


def test_clear_blocks_removes_everything_finished():
    session = make()
    session.feed(prompt())
    run(session, "a", "1\r\n")
    run(session, "b", "2\r\n")
    session.feed(submit("sleep 9"))
    removed = session.clear_blocks()
    assert [b.command for b in removed] == ["a", "b"]
    assert [b.command for b in session.blocks] == ["sleep 9"]


def test_clear_blocks_takes_background_output_along():
    session = make()
    session.feed(prompt())
    run(session, "a", "1\r\n")
    session.feed(b"\r\r\n" + mark("T") + b"[1]  + done  job\r\n\x1b[J"
                 + mark("A") + b"% \x1b[K")
    assert len(session.blocks) == 2
    session.clear_blocks()
    assert session.blocks == []
    session.feed(b"\r\r\n" + mark("T") + b"[2]  + done  more\r\n\x1b[J"
                 + mark("A") + b"% \x1b[K")
    assert [b.lines() for b in session.blocks] == [["[2]  + done  more"]]


def test_resize_reaches_every_live_screen():
    session = make()
    session.feed(prompt())
    session.feed(submit("cat") + b"x" * 70)
    session.resize(20, 6)
    block = session.blocks[0]
    assert block.screen.cols == 20 and session.input.cols == 20
    assert block.lines() == ["x" * 70]


# -- what copying yields -----------------------------------------------------

def test_copy_rules():
    session = make()
    session.feed(prompt())
    wide = "w" * 100
    first = run(session, "ls -la", "total 0   \r\n\tindented\r\n\r\nend\r\n")
    second = run(session, "cat wide", wide + "\r\n")
    third = run(session, "pip", "\r10%\r50%\r100%\r\n")
    assert output_text(first) == "total 0\n\tindented\n\nend"
    assert output_text(second) == wide
    assert output_text(third) == "100%"
    assert copy_text([first], BOTH) == "$ ls -la\ntotal 0\n\tindented\n\nend"
    assert copy_text([first, second, third]) == \
        "total 0\n\tindented\n\nend\n\n" + wide + "\n\n100%"
    assert copy_text([first, second], "command") == "ls -la\n\ncat wide"


def test_copy_while_running_takes_the_current_state():
    session = make()
    session.feed(prompt())
    session.feed(submit("ping") + b"reply 1\r\nreply 2\r\n")
    assert output_text(session.blocks[0]) == "reply 1\nreply 2"


def test_filter_shows_matching_lines_and_copy_follows():
    session = make()
    session.feed(prompt())
    text = "ok 1\r\nerror: a\r\nok 2\r\nok 3\r\nERROR: b\r\nok 4\r\n"
    block = run(session, "make", text)
    lines = block.lines()
    assert filter_indices(lines, FilterSpec("error")) == [1, 4]
    assert filter_indices(lines, FilterSpec("error", case_sensitive=True)) \
        == [1]
    assert filter_indices(lines, FilterSpec("error", invert=True)) == \
        [0, 2, 3, 5]
    assert filter_indices(lines, FilterSpec("error", context=1)) == \
        [0, 1, 2, 3, 4, 5]
    assert filter_indices(lines, FilterSpec(r"ok [13]$", regex=True)) == \
        [0, 3]
    assert filter_indices(lines, FilterSpec("(", regex=True)) == \
        list(range(6))                       # invalid pattern: show all
    block.filter = FilterSpec("error")
    assert output_text(block) == "error: a\nERROR: b"
    block.filter = None
    assert output_text(block).count("\n") == 5   # nothing was deleted


def test_find_reports_every_match():
    lines = ["an error here", "fine", "Error and error"]
    assert find_matches(lines, "error") == [(0, 3, 8), (2, 0, 5), (2, 10, 15)]
    assert find_matches(lines, "error", case_sensitive=True) == \
        [(0, 3, 8), (2, 10, 15)]
    assert find_matches(lines, "e.r", regex=True)[0] == (0, 3, 6)
    assert find_matches(lines, "error", indices=[2]) == \
        [(2, 0, 5), (2, 10, 15)]


# -- the stand-in screen of an idle prompt -----------------------------------

def held(text: bytes = b"hello", **kwargs):
    """A session whose line editor has just reported itself idle."""
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime, **kwargs)
    session.feed(prompt() + text)
    idle(session, runtime)
    assert session._hold is not None
    return session, runtime


def test_plain_output_in_bulk_at_an_idle_prompt_is_diverted_whole():
    session, _ = held()
    session.feed(b"abc")                     # the cursor is left mid-line
    lines = [f"line {n:04d}" for n in range(300)]
    session.feed(b"def\r\n" + "".join(f"{s}\r\n" for s in lines).encode())
    block, = session.blocks
    assert block.kind == BACKGROUND
    assert block.lines() == ["abcdef"] + lines
    assert input_rows(session) == ["% hello"]


def test_a_mode_set_inside_foreign_output_stays_with_it():
    session, _ = held()
    session.feed(b"RAW\x1b[?25l more\r\n")
    block, = session.blocks
    assert block.lines() == ["RAW more"]
    assert block.screen.cursor_visible is False
    assert session.input.cursor_visible is True


def test_a_question_about_the_cursor_is_answered_from_the_input_screen():
    session, _ = held()
    session.pty = FakePty()
    session.feed(b"\x1b[6n")
    assert session.pty.written == [b"\x1b[1;8R"]     # row 1, after "% hello"
    assert session._hold is None                     # an answer is input


def test_too_many_unclaimed_operations_are_handed_to_the_input():
    session, _ = held()
    session.feed(b"\x08" * 2048)
    assert session._hold is not None and session.input.cx == 7
    session.feed(b"\x08")                            # one too many to hold
    assert session._hold is None and session.input.cx == 0
    assert session.blocks == []


def test_unclaimed_operations_wait_from_the_first_of_them(clock):
    session, _ = held()
    hold = session._hold
    clock.now = 4.0
    session.feed(b"\x1b[2 q")                # a mode, then two movements,
    assert hold.pending and hold.since == 4.0
    clock.now = 4.0625
    session.feed(b"\x08\x1b[?25l\x08")       # ... and another mode
    assert hold.since == 4.0
    clock.now = 4.125
    session.tick()
    assert session._hold is hold             # nobody has claimed them yet
    assert (session.input.cx, session.input.cursor_shape) == (7, 0)
    clock.now = 4.25
    session.tick()
    assert session._hold is None             # then they are nobody's
    assert (session.input.cx, session.input.cursor_shape,
            session.input.cursor_visible) == (5, 2, False)


def test_a_movement_waits_from_the_moment_it_arrived(clock):
    session, _ = held()
    clock.now = 2000.0
    session.feed(b"\x08")
    assert session._hold.since == 2000.0
    session.tick()
    assert session._hold is not None and session.input.cx == 7


def test_unclaimed_operations_are_held_for_no_more_than_150_ms(clock):
    session, _ = held()
    session._hold.__dict__["_since"] = -1.0  # not from an earlier time
    clock.now = 0.0
    session.feed(b"\x08")
    clock.now = 0.15
    session.tick()
    assert session._hold is not None and session.input.cx == 7
    clock.now = 0.16
    session.tick()
    assert session._hold is None and session.input.cx == 6


def test_an_idle_prompt_with_nothing_held_is_left_alone():
    session, runtime = held()
    asked = list(runtime.requests)
    hold = session._hold
    assert not hold.pending and hold.target is None
    session.tick()
    assert session._hold is hold and runtime.requests == asked


def test_a_mark_of_another_kind_claims_nothing():
    session, _ = held()
    session.feed(b"\x08" + mark("Z", "1") + mark("S", "999", "1"))
    assert session._hold is not None and session._hold.pending
    assert session.input.cx == 7


def test_a_repeated_idle_reply_keeps_what_is_held():
    session, runtime = held()
    hold = session._hold
    session.feed(b"\x08" + mark("S", str(runtime.requests[-1]), "1"))
    assert session._hold is hold and hold.pending
    session.feed(mark("I"))                  # the editor claims the movement
    assert session._hold is None and session.input.cx == 6


def test_a_full_screen_program_at_an_idle_prompt_gets_the_real_screen_back():
    session, _ = held()
    session.feed(b"\x1b[?1049h")
    assert session.fullscreen
    session.write(b"x")                      # a key ends the idle state
    session.feed(b"\x1b[?1049l" + b"Z")
    assert not session.fullscreen
    assert input_rows(session) == ["% helloZ"]
    assert session.blocks == []              # nothing was taken for foreign


def test_clearing_the_blocks_ends_a_diversion_in_progress():
    session, _ = held()
    session.feed(b"foreign\r\n")
    assert [b.lines() for b in session.blocks] == [["foreign"]]
    removed = session.clear_blocks()
    assert [b.lines() for b in removed] == [["foreign"]]
    assert session._hold is None and session.blocks == []
    session.feed(b" later")                  # no longer anyone else's
    assert input_rows(session) == ["% hello later"]
    assert session.blocks == []


def test_no_diversion_behind_a_full_screen_program_at_the_prompt():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt() + b"\x1b[?1049h")
    idle(session, runtime)                   # one handler, but not our screen
    assert session._hold is None and session._sync_gap == 0.5


def test_an_idle_reply_after_the_command_started_changes_nothing():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    settle(session)
    session.tick()
    session.feed(mark("C", "/work", "started meanwhile"))
    block, = session.blocks
    session.feed(mark("S", str(runtime.requests[-1]), "1"))
    assert session._hold is None and session.term.screen is block.screen
    session.feed(b"output\r\n")
    assert block.lines() == ["output"]


@pytest.mark.parametrize("reply", [
    ("1",),                                  # no count of handlers
    ("x", "1"),                              # no number
    ("²", "1"),                         # a digit, but no number
    ("-1", "1"),
    ("7", "1"),                              # an answer to another question
])
def test_a_malformed_or_stale_idle_reply_is_ignored(reply):
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    settle(session)
    session.tick()
    assert runtime.requests == [1]
    session.feed(mark("S", *reply))
    assert session._hold is None and session._sync_waiting
    session.feed(mark("S", "1", "1"))        # the real answer still counts
    assert session._hold is not None and not session._sync_waiting


# -- when the line editor is asked -------------------------------------------

def quiet(clock, **kwargs):
    """A session at a prompt that came up long ago; the time is now 0."""
    runtime = kwargs.pop("runtime", None) or FakeRuntime()
    session = Session(COLS, 10, runtime=runtime, **kwargs)
    clock.now = -100.0
    session.write(b"earlier")
    session.feed(prompt())
    clock.now = 0.0
    return session, runtime


def test_the_editor_is_asked_once_the_prompt_is_quiet(clock):
    session, runtime = quiet(clock)
    session.tick()
    assert runtime.requests == [1]
    session.tick()
    assert runtime.requests == [1]           # one question at a time


def test_nothing_is_asked_before_the_first_prompt_or_while_a_command_runs(
        clock):
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    clock.now = 0.0
    session.tick()
    assert session.state == STARTUP and runtime.requests == []
    session.feed(prompt() + submit("sleep 9"))
    clock.now = 100.0
    session.tick()
    assert session.state == RUNNING and runtime.requests == []
    session.close()
    session.tick()
    assert session.state == CLOSED and runtime.requests == []


def test_nothing_is_asked_while_the_prompt_is_still_being_drawn(clock):
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    clock.now = -100.0
    session.feed(prompt()[:-len(mark("I") + b"\x1b[?2004h")])
    clock.now = 0.0
    session.tick()
    assert not session.line_ready and runtime.requests == []
    clock.now = 100.0
    session.feed(mark("I"))
    clock.now = 200.0
    session.tick()
    assert session.line_ready and runtime.requests == [1]


def test_nothing_is_asked_when_diverting_is_switched_off(clock):
    session, runtime = quiet(clock, divert=False)
    session.tick()
    assert runtime.requests == []


def test_nothing_is_asked_without_a_shell_directory(clock):
    session = make()
    clock.now = -100.0
    session.feed(prompt())
    clock.now = 0.0
    session.tick()                           # nobody to ask: no error
    assert session._sync_seq == 0 and session._hold is None


def test_the_editor_is_not_asked_until_output_has_been_quiet(clock):
    session, runtime = quiet(clock)
    session.feed(b"x")                       # output at time 0
    clock.now = 0.14
    session.tick()
    assert runtime.requests == []
    clock.now = 0.15
    session.tick()
    assert runtime.requests == [1]


def test_the_editor_is_not_asked_while_a_key_may_still_be_at_work(clock):
    session, runtime = quiet(clock)
    session.write(b"\x1b")                   # a key at time 0
    clock.now = 0.49
    session.tick()
    assert runtime.requests == []
    clock.now = 0.5
    session.tick()
    assert runtime.requests == [1]


def test_the_shells_own_key_timeout_sets_that_wait(clock):
    session, runtime = quiet(clock)
    assert session._key_delay == 0.5
    clock.now = -50.0
    session.feed(mark("P", "0", "on", "/work", "100") + mark("I"))
    assert session._key_delay == pytest.approx(1.1)
    clock.now = 0.0
    session.write(b"\x1b")
    clock.now = 1.09
    session.tick()
    assert runtime.requests == []
    clock.now = 1.1
    session.tick()
    assert runtime.requests == [1]


@pytest.mark.parametrize("field,delay", [
    ("0", 0.1), ("40", 0.5), ("500", 5.1), ("100000", 5.1),
    ("soon", 0.5), ("²", 0.5), ("-5", 0.5), ("", 0.5),
])
def test_the_key_timeout_is_taken_from_the_prompt_mark(field, delay):
    session = make()
    session.feed(mark("P", "0", "on", "/work", field))
    assert session._key_delay == pytest.approx(delay)


def test_an_unanswered_question_is_asked_again_after_two_seconds(clock):
    session, runtime = quiet(clock)
    session.tick()
    clock.now = 1.99
    session.tick()
    assert runtime.requests == [1]
    clock.now = 2.0
    session.tick()
    assert runtime.requests == [1, 2]
    clock.now = 3.99
    session.tick()
    assert runtime.requests == [1, 2]


def test_a_question_the_shell_cannot_take_yet_is_asked_again_at_once(clock):
    session, runtime = quiet(clock, runtime=FakeRuntime(listening=False))
    for _ in range(3):
        session.tick()
    assert runtime.requests == [1, 2, 3]
    assert not session._sync_waiting
    runtime.listening = True
    session.tick()
    session.tick()
    assert runtime.requests == [1, 2, 3, 4] and session._sync_waiting


def test_questions_come_ever_less_often_while_another_plugin_listens(clock):
    session, runtime = quiet(clock)
    asked = 0.0
    for gap in (0.5, 1.0, 2.0, 4.0, 4.0):
        clock.now = asked
        session.tick()
        session.feed(mark("S", str(runtime.requests[-1]), "2"))
        assert session._sync_gap == gap and session._hold is None
        count = len(runtime.requests)
        clock.now = asked + gap - 0.25
        session.tick()
        assert len(runtime.requests) == count        # too soon
        asked += gap
    assert runtime.requests == [1, 2, 3, 4, 5]


def backed_off(clock):
    """A session that has learnt to ask only every four seconds; the last
    question went out at time 7.5."""
    session, runtime = quiet(clock)
    for asked in (0.0, 0.5, 1.5, 3.5, 7.5):
        clock.now = asked
        session.tick()
        session.feed(mark("S", str(runtime.requests[-1]), "2"))
    assert session._sync_gap == 4.0 and runtime.requests == [1, 2, 3, 4, 5]
    return session, runtime


def test_a_key_makes_the_next_question_due_again(clock):
    session, runtime = backed_off(clock)
    clock.now = 7.75
    session.write(b"x")
    clock.now = 8.0
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5]       # the key is too fresh
    clock.now = 8.25
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5, 6]


def test_a_redraw_by_the_editor_makes_the_next_question_due_again(clock):
    session, runtime = backed_off(clock)
    clock.now = 7.75
    session.feed(b"\r\x1b[J" + mark("A") + b"% \x1b[K")
    clock.now = 8.0
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5, 6]


def test_without_either_the_next_question_waits_its_four_seconds(clock):
    session, runtime = backed_off(clock)
    clock.now = 11.25
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5]
    clock.now = 11.5
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("timeout,diverts", [("90", True), ("91", False)])
def test_an_answer_within_a_grown_key_timeout_is_not_trusted(
        clock, timeout, diverts):
    """The key went out a second ago, and the question after it; then the
    shell reports a key timeout of 1.0 or of 1.01 seconds."""
    session, runtime = quiet(clock)
    clock.now = -1.0
    session.write(b"\x1b")
    clock.now = 0.0
    session.tick()
    assert runtime.requests == [1]
    session.feed(mark("P", "0", "on", "/work", timeout) + mark("A") + b"% ")
    session.feed(mark("S", "1", "1"))
    assert (session._hold is not None) is diverts


# -- marks at the wrong moment, or malformed ---------------------------------

@pytest.mark.parametrize("field,status", [
    ("0", 0), ("1", 1), ("130", 130), ("255", 255),
    ("", None), ("abc", None), ("²", None), ("-1", None), ("1.5", None),
])
def test_the_exit_status_is_a_plain_number_or_unknown(field, status):
    session = make()
    session.feed(prompt())
    session.feed(submit("something") + mark("E", field))
    block, = session.blocks
    assert not block.running and block.exit_code == status
    assert block.failed is bool(status)
    assert session.state == PROMPT


def test_a_prompt_mark_without_a_directory_keeps_the_last_one():
    session = make()
    session.feed(mark("P", "0", "on", "/a%3Bb") + mark("A") + b"% ")
    assert session.cwd == "/a;b"
    session.feed(mark("P", "0", "on") + mark("A") + b"% ")
    assert session.cwd == "/a;b" and session.state == PROMPT
    session.feed(mark("P", "0") + mark("A") + b"% ")
    assert session.cwd == "/a;b"
    session.feed(mark("P") + mark("A") + b"% ")
    assert session.cwd == "/a;b" and session.blocks == []


def test_a_command_mark_without_the_text_takes_what_the_editor_handed_over():
    session = make()
    session.feed(prompt())
    session.feed(b"typed" + mark("F", "for x in 1 2%3B do") + mark("F", "done")
                 + mark("T") + mark("C", "/elsewhere"))
    block, = session.blocks
    assert block.command == "for x in 1 2; do\ndone"
    assert block.cwd == "/elsewhere"


def test_a_bare_command_mark_starts_a_nameless_command_where_the_shell_is():
    session = make()
    session.feed(prompt())
    session.feed(mark("C"))
    block, = session.blocks
    assert block.is_command and block.running
    assert (block.command, block.cwd) == ("", "/work")


def test_a_command_starting_while_another_runs_ends_that_one_unknown():
    session = make()
    session.feed(prompt())
    session.feed(submit("first") + b"out\r\n")
    session.feed(mark("C", "/work", "second") + b"more\r\n")
    first, second = session.blocks
    assert not first.running and first.exit_code is None
    assert first.lines() == ["out"]
    assert second.running and second.lines() == ["more"]
    assert session.running is second and session.state == RUNNING


def test_what_the_editor_hands_over_before_the_first_prompt_is_not_a_command():
    session = make()
    session.feed(mark("F", "not yet") + mark("C", "/w"))
    block, = session.blocks
    assert block.command == "" and block.cwd == "/w"


def test_a_prompt_cycle_without_a_submitted_line_leaves_nothing_behind():
    session = make()
    session.feed(prompt())
    session.feed(b"redrawn away\r\n" + prompt())
    assert session.blocks == [] and input_rows(session) == ["%"]


def test_output_after_an_empty_line_is_background_output():
    session = make()
    session.feed(prompt())
    session.feed(mark("F", "") + b"\r\r\n" + mark("T")
                 + b"a hook says hello\r\n" + prompt())
    block, = session.blocks
    assert block.kind == BACKGROUND and block.command == ""
    assert block.lines() == ["a hook says hello"]
    assert input_rows(session) == ["%"]


def test_blank_lines_around_background_output_are_dropped():
    session = make()
    session.feed(prompt())
    session.feed(submit("sleep 9") + mark("E", "0") + b" " * COLS + b"\r\r"
                 + b"\r\n\r\n[1] one\r\n\r\n[2] two\r\n\r\n\r\n"
                 + mark("P", "0", "off", "/work") + mark("A") + b"% ")
    command, notice = session.blocks
    assert notice.kind == BACKGROUND
    assert notice.lines() == ["[1] one", "", "[2] two"]


def test_nothing_but_blank_lines_is_no_background_output():
    session = make()
    session.feed(prompt())
    session.feed(submit("sleep 9") + mark("E", "0") + b" " * COLS + b"\r\r"
                 + b"\r\n \r\n" + mark("P", "0", "off", "/work")
                 + mark("A") + b"% ")
    command, = session.blocks
    assert command.is_command and input_rows(session) == ["%"]


def test_a_full_screen_program_at_the_prompt_leaves_no_trace_of_its_own():
    """The line editor steps aside for a program it runs itself; what is on
    the input screen afterwards was not printed by somebody else."""
    notice = b"\r\nfoo\r\n" + mark("A") + b"% "
    session = make()
    session.feed(prompt())
    session.feed(b"\x1b[?1049h" + mark("T") + b"\x1b[?1049l" + notice)
    assert session.blocks == []
    assert input_rows(session) == ["%", "foo", "%"]
    # Without the program in between, the same bytes are foreign output.
    session = make()
    session.feed(prompt())
    session.feed(mark("T") + notice)
    assert [b.lines() for b in session.blocks] == [["%", "foo"]]
    assert input_rows(session) == ["%"]


def test_marks_after_the_end_change_nothing():
    session = make()
    session.feed(prompt())
    run(session, "ls", "a\r\n")
    session.feed(b"\r\r\n" + mark("T") + b"notice\r\n")
    session.close()
    blocks = list(session.blocks)
    rows = input_rows(session)
    session.feed(mark("A") + mark("E", "0") + mark("P", "0", "on", "/late")
                 + mark("A") + mark("I") + mark("F", "too late") + mark("T")
                 + mark("C", "/work", "too late") + mark("S", "0", "1"))
    assert session.state == CLOSED and session.blocks == blocks
    assert session.running is None and not session.line_ready
    assert not session.prompt_active and input_rows(session) == rows
    assert session.cwd == "/work"
    # Not even what would have been a notice between two prompts.
    session.feed(b"\r\n[1] done\r\n" + mark("E", "0")
                 + mark("P", "0", "off", "/work"))
    assert session.blocks == blocks and session.state == CLOSED


# -- the shell process -------------------------------------------------------

class Started:
    """Stands in for the process class: notes how it was started."""

    made: list = []

    def __init__(self, argv, env, cwd, cols, rows) -> None:
        self.fd = 11
        self.made.append((argv, env, cwd, cols, rows))


@pytest.fixture
def started(monkeypatch) -> list:
    monkeypatch.setattr(Started, "made", [])
    monkeypatch.setattr(session_module, "PtyProcess", Started)
    return Started.made


def test_the_shell_starts_on_a_terminal_of_the_sessions_size(started):
    session = Session(132, 43, runtime=FakeRuntime())
    assert session.fileno() == -1 and session.pty is None
    session.start("/bin/zsh", cwd="/somewhere", env={"A": "1"})
    assert started == [(["/bin/zsh"],
                        {"A": "1", "ZDOTDIR": "/shims", "SHELL": "/bin/zsh"},
                        "/somewhere", 132, 43)]
    assert isinstance(session.pty, Started) and session.fileno() == 11


def test_a_login_shell_is_asked_for_with_its_flag(started):
    session = Session(COLS, 10, runtime=FakeRuntime())
    session.start("/bin/zsh", login=True)
    assert started == [(["/bin/zsh", "-l"],
                        {"ZDOTDIR": "/shims", "SHELL": "/bin/zsh"},
                        None, COLS, 10)]


def test_a_session_makes_its_own_shell_directory_when_given_none(started):
    session = Session(COLS, 10)
    assert session.runtime is None and session.term.nonce is None
    session.start("/bin/sh", env={})
    runtime = session.runtime
    try:
        assert isinstance(runtime, ShellRuntime)
        assert session.term.nonce == runtime.nonce
        (argv, env, cwd, cols, rows), = started
        assert argv == ["/bin/sh"] and cwd is None
        assert env["ZDOTDIR"] == runtime.path and env["SHELL"] == "/bin/sh"
        assert env["JUPYTERMINAL_NONCE"] == runtime.nonce
        assert os.path.isdir(runtime.path)
    finally:
        runtime.cleanup()


def test_marks_count_only_with_the_sessions_own_nonce():
    for stranger in (Session(COLS, 10, nonce="another"), Session(COLS, 10)):
        stranger.feed(prompt())
        assert stranger.state == STARTUP             # not its marks
    # The shell directory's nonce is the one that counts when there is one.
    session = Session(COLS, 10, runtime=FakeRuntime(), nonce="another")
    session.feed(prompt())
    assert session.state == PROMPT


def test_reading_takes_everything_that_is_waiting(clock):
    session = make()
    assert session.read_ready() is False             # no shell at all
    session.pty = FakePty(prompt(), submit("echo hi"), b"hi\r\n", None,
                          b"later\r\n")
    changes = []
    session.on_change = lambda: changes.append(session.version)
    before = session.version
    assert session.read_ready() is True
    block, = session.blocks
    assert block.lines() == ["hi"] and block.running
    assert changes == [before + 1]                   # one change for it all
    assert session.pty.chunks == [b"later\r\n"]
    assert session.read_ready() is True
    assert block.lines() == ["hi", "later"]
    assert session.read_ready() is True              # nothing waiting
    assert changes == [before + 1, before + 2]


def test_reading_stops_when_its_time_is_up(clock):
    session = make()
    session.pty = FakePty(b"one\r\n", b"two\r\n", b"three\r\n")
    session.feed(prompt() + submit("cat"))
    block, = session.blocks
    assert session.read_ready(budget=0) is True
    assert block.lines() == ["one"]                  # the rest comes later
    assert session.read_ready(budget=0) is True
    assert block.lines() == ["one", "two"]
    assert session.read_ready() is True
    assert block.lines() == ["one", "two", "three"]


def test_the_end_of_output_closes_the_session(clock):
    session = make()
    session.pty = FakePty(prompt(), submit("exit 3"), b"bye\r\n", b"",
                          b"never read", status=3)
    statuses = []
    session.on_exit = statuses.append
    assert session.read_ready() is False
    block, = session.blocks
    assert block.lines() == ["bye"] and not block.running
    assert session.state == CLOSED and session.exit_status == 3
    assert statuses == [3] and session.pty.closed == 1
    assert session.pty.chunks == [b"never read"]


def test_a_reply_to_an_idle_check_alone_is_no_change(clock):
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    settle(session)
    session.tick()
    changes = []
    session.on_change = lambda: changes.append(session.version)
    session.pty = FakePty(mark("S", "1", "1"))
    assert session.read_ready() is True
    assert changes == [] and session._hold is not None
    session.pty = FakePty(mark("S", "1", "1"), b"x")
    assert session.read_ready() is True
    assert len(changes) == 1                         # with output, it is one


def test_what_counts_as_a_bare_idle_reply():
    reply = mark("S", "12", "1")
    assert Session._is_sync_reply(reply)
    assert not Session._is_sync_reply(reply + b"x")
    assert not Session._is_sync_reply(b"x" + reply)
    assert not Session._is_sync_reply(reply + reply)
    assert not Session._is_sync_reply(mark("I"))
    # Nothing as long as 96 bytes is taken for one.
    longest = mark("S", "1" * (95 - len(mark("S", "", "1"))), "1")
    assert len(longest) == 95 and Session._is_sync_reply(longest)
    assert not Session._is_sync_reply(mark("S", "1" + longest[15:-3].decode(),
                                           "1"))


def test_output_this_terminal_cannot_handle_does_not_end_the_session(
        capsys, monkeypatch, clock):
    session = make()
    session.feed(prompt() + submit("cat"))
    feed = session.term.feed

    def choke(data: bytes) -> None:
        if data == b"poison":
            raise ValueError("cannot digest this")
        feed(data)

    monkeypatch.setattr(session.term, "feed", choke)
    session.pty = FakePty(b"before\r\n", b"poison", b"after\r\n")
    assert session.read_ready() is True
    assert session.blocks[0].lines() == ["before", "after"]
    assert session.state == RUNNING
    report = capsys.readouterr().err
    assert "Traceback" in report and "cannot digest this" in report


def test_a_change_is_announced_for_every_feed():
    session = make()
    session.feed(prompt())                           # nobody listens: fine
    changes = []
    session.on_change = lambda: changes.append(session.version)
    session.feed(b"a")
    session.feed(b"b")
    assert changes == [session.version - 1, session.version]


def test_input_goes_to_the_shell():
    session = make()
    session.write(b"lost")                           # no shell: no error
    session.pty = FakePty()
    session.feed(prompt())
    session.write(b"ls\r")
    session.write(b"")
    assert session.pty.written == [b"ls\r"]


def test_no_input_at_all_does_not_end_the_idle_state():
    session, _ = held()
    session.pty = FakePty()
    session.write(b"")
    assert session._hold is not None and session.pty.written == []
    assert not session._sync_dirty
    session.write(b"x")
    assert session._hold is None and session.pty.written == [b"x"]
    assert session._sync_dirty


def test_the_terminal_answers_programs_through_the_shells_input():
    session = make()
    session.pty = FakePty()
    session.feed(prompt())
    session.feed(submit("probe") + b"ab\x1b[6n")
    assert session.pty.written == [b"\x1b[1;3R"]


def test_a_paste_is_bracketed_when_the_program_asked_for_it():
    session = make()
    session.pty = FakePty()
    session.feed(prompt())                           # zsh asks for brackets
    session.paste("echo a\necho b")
    session.feed(submit("cat"))                      # ... and withdraws them
    session.paste("x\r\ny")
    assert session.pty.written == [b"\x1b[200~echo a\recho b\x1b[201~",
                                   b"x\ry"]


def test_a_command_is_handed_to_the_line_editor_through_its_file():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.pty = FakePty()
    assert session.set_command_buffer("too early") is False
    session.feed(prompt())
    assert session.set_command_buffer("echo again") is True
    assert runtime.buffers == ["echo again"]
    assert session.pty.written == [SET_BUFFER_KEY]
    session.feed(submit("sleep 9"))
    assert session.set_command_buffer("not now") is False
    assert runtime.buffers == ["echo again"]
    assert session.pty.written == [SET_BUFFER_KEY]


def test_no_command_can_be_handed_over_without_a_shell_directory():
    session = make()
    session.pty = FakePty()
    session.feed(prompt())
    assert session.set_command_buffer("echo again") is False
    assert session.pty.written == []


# -- resizing ----------------------------------------------------------------

def test_a_new_size_reaches_the_shell_and_the_same_size_does_not():
    session = make()
    session.pty = FakePty()
    session.feed(prompt())
    version = session.version
    session.resize(COLS, 10, 400, 180)
    assert session.pty.sizes == [] and session.version == version
    session.resize(60, 10, 540, 180)
    assert session.pty.sizes == [(60, 10, 540, 180)]
    assert session.version == version + 1
    session.resize(60, 12)
    assert session.pty.sizes[-1] == (60, 12, 0, 0)
    assert (session.cols, session.rows) == (60, 12)
    assert (session.input.cols, session.input.rows) == (60, 12)


def test_no_size_is_smaller_than_two_by_two():
    session = make()
    session.pty = FakePty()
    session.resize(1, 0)
    assert (session.cols, session.rows) == (2, 2)
    assert session.pty.sizes == [(2, 2, 0, 0)]
    assert (Session(1, 1).cols, Session(1, 1).rows) == (2, 2)
    assert (Session(3, 2).cols, Session(3, 2).rows) == (3, 2)


def test_resizing_before_the_first_prompt_reaches_the_startup_output():
    session = make()
    session.feed(b"m" * 70)
    block, = session.blocks
    session.resize(20, 6)                            # no shell: no error
    assert (block.screen.cols, block.screen.rows) == (20, 6)
    assert block.lines() == ["m" * 70]


def test_resizing_reaches_a_full_screen_program_and_what_is_behind_it():
    session = make()
    session.feed(prompt())
    session.feed(submit("vim") + b"before\r\n\x1b[?1049hfull")
    block, = session.blocks
    screen = session.term.screen
    assert session.cursor_screen() is screen and screen is not block.screen
    session.resize(30, 8)
    assert (screen.cols, screen.rows) == (30, 8)
    assert (block.screen.cols, block.screen.rows) == (30, 8)
    assert (session.input.cols, session.input.rows) == (30, 8)


def test_resizing_ends_the_idle_state():
    session, _ = held()
    session.feed(b"\x08")
    session.resize(30, 8)
    assert session._hold is None and session.input.cx == 6


# -- which screen has the cursor ---------------------------------------------

def test_the_cursor_is_where_output_goes():
    session = make()
    assert not session.prompt_active
    assert session.cursor_screen() is session.term.screen
    assert session.cursor_screen() is not session.input
    session.feed(prompt())
    assert session.prompt_active
    assert session.cursor_screen() is session.input
    session.feed(submit("cat"))
    assert not session.prompt_active
    assert session.cursor_screen() is session.blocks[0].screen


def test_the_cursor_stays_in_the_input_while_the_prompt_is_idle():
    session, _ = held()
    assert session.term.screen is not session.input  # the stand-in
    assert session.cursor_screen() is session.input


# -- the end -----------------------------------------------------------------

def test_closing_settles_everything_once():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.pty = FakePty(status=7)
    session.feed(prompt())
    events = []
    session.on_change = lambda: events.append("change")
    session.on_exit = lambda status: events.append(("exit", status))
    version = session.version
    session.close()
    assert session.state == CLOSED and session.exit_status == 7
    assert events == ["change", ("exit", 7)]
    assert session.version == version + 1
    assert (session.pty.closed, runtime.cleaned) == (1, 1)
    session.close()                                  # already closed
    assert events == ["change", ("exit", 7)]
    assert (session.pty.closed, runtime.cleaned) == (1, 1)
    assert session.version == version + 1


def test_closing_without_a_shell_or_anyone_listening():
    session = make()
    session.feed(b"startup output\r\n")
    session.close()
    block, = session.blocks
    assert session.state == CLOSED and session.exit_status is None
    assert not block.running and block.lines() == ["startup output"]


def test_closing_while_idle_gives_held_movements_to_the_input():
    session, runtime = held()
    session.feed(b"\x08")
    session.close()
    assert session._hold is None and session.input.cx == 6
    assert runtime.cleaned == 1


# -- clearing and the history cap, continued ---------------------------------

def test_clearing_before_the_first_prompt_keeps_the_startup_output_open():
    session = make()
    session.feed(b"motd\r\n")
    dropped = []
    session.on_drop = dropped.append
    assert session.clear_blocks() == []
    assert dropped == []                             # nothing went
    block, = session.blocks
    assert block.running
    session.feed(b"more\r\n" + prompt())
    assert block.lines() == ["motd", "more"] and not block.running


def test_clearing_tells_whoever_listens():
    session = make()
    session.feed(prompt())
    first = run(session, "a", "1\r\n")
    second = run(session, "b", "2\r\n")
    session.clear_before = second.id
    events = []
    session.on_drop = lambda blocks: events.append(("drop", list(blocks)))
    session.on_change = lambda: events.append("change")
    version = session.version
    assert session.clear_blocks() == [first, second]
    assert events == [("drop", [first, second]), "change"]
    assert session.clear_before == 0 and session.version == version + 1
    assert session.clear_blocks() == []
    assert events == [("drop", [first, second]), "change", "change"]


def test_no_cap_means_nothing_is_ever_dropped():
    session = make(max_lines=0)
    session.feed(prompt())
    for n in range(3):
        run(session, f"seq {n}", "x\r\n" * 50)
    session.feed(submit("yes") + b"y\r\n" * 5000)
    assert [len(b.lines()) for b in session.blocks] == [50, 50, 50, 5000]
    assert session.blocks[-1].store.dropped == 0


def test_the_cap_drops_blocks_with_nobody_listening_too():
    session = make(max_lines=25)
    session.feed(prompt())
    for n in range(4):
        run(session, f"seq {n}", "x\r\n" * 10)
    assert [b.command for b in session.blocks] == ["seq 2", "seq 3"]


def test_the_cap_is_met_exactly_and_the_last_block_always_stays():
    session = make(max_lines=20)
    session.feed(prompt())
    run(session, "a", "x\r\n" * 10)
    run(session, "b", "x\r\n" * 10)
    assert [b.command for b in session.blocks] == ["a", "b"]   # 20 fit
    run(session, "c", "x\r\n" * 1)
    assert [b.command for b in session.blocks] == ["b", "c"]
    run(session, "d", "x\r\n" * 500)                 # more than the cap
    assert [b.command for b in session.blocks] == ["d"]
    assert len(session.blocks[0].lines()) == 500


def test_a_rejected_line_counts_towards_the_cap():
    session = make(max_lines=5)
    dropped = []
    session.on_drop = dropped.extend
    session.feed(prompt())
    first = run(session, "a", "x\r\n" * 5)
    session.feed(b"echo )" + mark("F", "echo )") + b"\r\r\n" + mark("T")
                 + b"zsh: parse error near `)'\r\n" + prompt(1))
    assert dropped == [first]
    assert [b.command for b in session.blocks] == ["echo )"]


def test_an_endless_block_keeps_the_cap_and_some_slack():
    session = make(max_lines=2000)
    session.feed(prompt())
    session.feed(submit("yes"))
    block, = session.blocks
    # Nine lines are still on the screen; the slack is 1000 lines.
    session.feed(b"y\r\n" * 3009)
    assert len(block.store.texts) == 3000 and block.store.dropped == 0
    session.feed(b"y\r\n")
    assert len(block.store.texts) == 2000
    assert block.store.dropped == 1001


def test_endless_startup_output_is_bounded_as_well():
    session = make(max_lines=40000)
    session.feed(b"y\r\n" * 42009)                   # the slack is 2000
    block, = session.blocks
    assert block.kind == BACKGROUND
    assert len(block.store.texts) == 42000 and block.store.dropped == 0
    session.feed(b"y\r\n")
    assert len(block.store.texts) == 40000 and block.store.dropped == 2001


# -- what the terminal reports -----------------------------------------------

def test_title_bell_and_clipboard_reach_whoever_listens():
    session = make()
    stream = (b"\x1b]2;my title\x07" + b"\x07"
              + b"\x1b]52;c;aGVsbG8=\x07" + b"\x1b]0;\x07")
    session.feed(stream)                             # nobody listens: fine
    events = []
    session.on_title = lambda title: events.append(("title", title))
    session.on_bell = lambda: events.append(("bell",))
    session.on_clipboard = lambda target, text: events.append(
        ("clipboard", target, text))
    session.feed(stream)
    assert events == [("title", "my title"), ("bell",),
                      ("clipboard", "c", "hello"), ("title", "")]


# -- a real process, without the integration ---------------------------------

PROGRAM = """#!/bin/sh
printf '%s\\n' "args:$*" "shell:$SHELL" "term:$TERM" "$(pwd -P)" \\
    "pid:$JUPYTERMINAL_SHELL_PID:$$" "$(stty size)"
exit 3
"""


def play(tmp_path, monkeypatch, **kwargs) -> Session:
    """Run the little program above as the session's shell, to its end."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    program = tmp_path / "program"
    program.write_text(PROGRAM, encoding="utf-8")
    program.chmod(0o755)
    session = Session(132, 43)
    session.start(str(program), cwd=str(tmp_path),
                  env={"PATH": os.environ["PATH"]}, **kwargs)
    alive = True
    while alive:
        select.select([session.fileno()], [], [], 0.05)
        alive = session.read_ready()
    return session


def test_a_program_without_the_integration_is_one_background_block(
        tmp_path, monkeypatch):
    session = play(tmp_path, monkeypatch)
    block, = session.blocks
    assert block.kind == BACKGROUND and not block.running
    program = str(tmp_path / "program")
    pid = str(session.pty.pid)
    assert block.lines() == [
        "args:", f"shell:{program}", "term:xterm-256color",
        os.path.realpath(tmp_path), f"pid:{pid}:{pid}", "43 132"]
    assert session.state == CLOSED and session.exit_status == 3
    assert session.fileno() == -1
    assert os.listdir(tmp_path) == ["program"]       # its directory is gone


def test_a_login_start_of_a_real_program(tmp_path, monkeypatch):
    session = play(tmp_path, monkeypatch, login=True)
    assert session.blocks[0].lines()[0] == "args:-l"
    assert session.exit_status == 3



def test_the_states_have_the_names_the_interface_shows():
    assert (STARTUP, PROMPT, RUNNING, CLOSED) \
        == ("startup", "prompt", "command", "closed")


# -- every operation, at an idle prompt --------------------------------------
#
# The input screen is put into a state in which each operation would show:
# three rows of text, the cursor in the middle, the cursor hidden.

BUSY_PROMPT = prompt(text="aaaaaa\r\nbbbbbb\r\ncccccc") + b"\x1b[?25l\x1b[2;4H"

#: Every operation the line editor may be doing as well as somebody else,
#: with a sequence that asks for it.
OPERATIONS = {
    "linefeed": b"\n",
    "carriage_return": b"\r",
    "backspace": b"\x08",
    "tab": b"\t",
    "back_tab": b"\x1b[Z",
    "reverse_index": b"\x1bM",
    "index": b"\x1bD",
    "next_line": b"\x1bE",
    "cursor_up": b"\x1b[A",
    "cursor_down": b"\x1b[B",
    "cursor_forward": b"\x1b[C",
    "cursor_back": b"\x1b[D",
    "cursor_to": b"\x1b[3;5H",
    "cursor_col": b"\x1b[5G",
    "cursor_row": b"\x1b[3d",
    "save_cursor": b"\x1b7",
    "restore_cursor": b"\x1b8",
    "erase_line": b"\x1b[2K",
    "erase_display": b"\x1b[2J",
    "erase_chars": b"\x1b[2X",
    "insert_chars": b"\x1b[2@",
    "delete_chars": b"\x1b[2P",
    "insert_lines": b"\x1b[L",
    "delete_lines": b"\x1b[M",
    "scroll_up": b"\x1b[S",
    "scroll_down": b"\x1b[T",
    "set_sgr": b"\x1b[31m",
    "set_margins": b"\x1b[2;3r",
    "set_origin": b"\x1b[?6h",
    "tab_set": b"\x1bH",
    "tab_clear": b"\x1b[3g",
    "designate_charset": b"\x1b(0",
    "shift_charset": b"\x0e",
    "soft_reset": b"\x1b[!p",
    "reset": b"\x1bc",
    # Modes, which the terminal sets on the screen directly.
    "insert mode": b"\x1b[4h",
    "newline mode": b"\x1b[20h",
    "no wrapping": b"\x1b[?7l",
    "cursor shown": b"\x1b[?25h",
    "cursor shape": b"\x1b[4 q",
}

FIELDS = ("cols", "rows", "cx", "cy", "wrap_pending", "fg", "bg", "attrs",
          "sid", "bce", "top", "bot", "autowrap", "insert", "origin",
          "newline_mode", "cursor_visible", "cursor_shape", "_gl", "_saved",
          "last_char", "clear_count")


def state_of(screen) -> dict:
    """Everything about a screen that an operation can change."""
    state = {name: getattr(screen, name) for name in FIELDS}
    state["grid"] = [(row.text(), row.styles and list(row.styles),
                      row.wrapped) for row in screen.grid]
    state["tabs"] = screen.tabs and sorted(screen.tabs)
    state["charsets"] = list(screen._charsets)
    state["translating"] = screen._xlat is not None
    state["stored"] = list(screen.store.texts)
    return state


def busy(idle_first: bool):
    """A session at the busy prompt, idle (output is held) or not."""
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(BUSY_PROMPT)
    assert (session.input.cy, session.input.cx) == (1, 3)
    for _ in range(idle_first):
        idle(session, runtime)
    assert (session._hold is not None) is idle_first
    return session


@pytest.mark.parametrize("name", OPERATIONS)
def test_an_operation_and_then_a_mark_was_the_line_editors(name):
    """Held back at first, it reaches the input screen exactly as if the
    prompt had not been idle."""
    direct = busy(idle_first=False)
    before = state_of(direct.input)
    direct.feed(OPERATIONS[name] + mark("I"))
    expected = state_of(direct.input)
    assert expected != before                # the operation does show
    session = busy(idle_first=True)
    session.feed(OPERATIONS[name])
    assert state_of(session.input) == before     # nothing yet
    session.feed(mark("I"))
    assert state_of(session.input) == expected
    assert session._hold is None and session.blocks == []


@pytest.mark.parametrize("name", OPERATIONS)
def test_an_operation_and_then_output_was_somebody_elses(name):
    """It goes with the output it introduces, and the input screen is not
    touched at all."""
    session = busy(idle_first=True)
    before = state_of(session.input)
    session.feed(OPERATIONS[name] + b"X")
    block, = session.blocks
    assert block.kind == BACKGROUND
    assert "X" in "".join(row.text() for row in block.screen.grid)
    assert state_of(session.input) == before
    # ... and on a screen of its own it is what the operation does there.
    alone = Session(COLS, 10).input
    twin = Session(COLS, 10)
    twin.term.set_screen(alone)
    twin.term.feed(OPERATIONS[name] + b"X")
    assert state_of(block.screen) == state_of(alone)


def test_text_repeated_by_the_program_stays_with_its_output():
    session, _ = held()
    session.feed(b"ab\x1b[3b\r\n")           # "repeat the last character"
    block, = session.blocks
    assert block.lines() == ["abbbb"]
    assert input_rows(session) == ["% hello"]


def test_a_screen_test_pattern_at_an_idle_prompt_is_not_the_editors():
    session, _ = held()
    session.feed(b"\x1b#8")                  # fill the screen with E
    block, = session.blocks
    assert block.lines() == ["E" * COLS] * 10
    assert input_rows(session) == ["% hello"]


@pytest.mark.parametrize("ending,lines", [
    (b"\n", ["X", ""]),                      # the cursor stays in its column
    (b"\x1bD", ["X", ""]),
    (b"\x1bE", ["X"]),                       # ... or goes to the first
])
def test_a_line_of_foreign_output_ends_with_any_kind_of_line_feed(ending,
                                                                 lines):
    session, _ = held()
    session.feed(b"X" + ending + b"\x08" + mark("I"))
    block, = session.blocks
    assert block.lines() == lines
    # The step back came after the line: it was the line editor's.
    assert session.input.cx == 6 and session._hold is None


def test_within_a_line_of_foreign_output_everything_is_its_own():
    session, _ = held()
    session.feed(b"X\x08Y\x1b[?25l" + mark("I"))
    block, = session.blocks
    assert block.lines() == ["Y"]            # the step back was the output's
    assert block.screen.cursor_visible is False
    assert session.input.cx == 7 and session.input.cursor_visible is True


def test_what_was_held_before_foreign_output_goes_with_it_once():
    session, _ = held()
    session.feed(b"\x1b[3CA\x1b[31mB\r\n")
    block, = session.blocks
    (text, runs), = block.screen.all_lines()
    assert text == "   AB" and runs is not None      # moved, and in color
    assert session.input.cx == 7 and session.input.sid == 0
    assert not session._hold.pending


def test_a_fresh_stand_in_holds_nothing():
    session, _ = held()
    hold = session._hold
    assert hold.bulk_ok() is False
    assert hold.pending is False and hold.since == 0.0
    assert hold.target is None
    assert hold.cols == COLS and hold.cx == 7        # read from the input


# -- a fresh session, and what its numbers mean ------------------------------

def test_a_fresh_session_has_nothing_and_knows_nothing():
    session = Session(COLS, 10)
    assert session.blocks == [] and session.running is None
    assert (session.cwd, session.clear_before, session.version) == ("", 0, 0)
    assert session.exit_status is None and session.pty is None
    assert not session.line_ready and session.state == STARTUP
    assert (session.max_lines, session.divert) == (2_000_000, True)
    assert (Session().cols, Session().rows) == (80, 24)


def test_blocks_are_numbered_from_one_in_the_order_they_appear():
    session = make()
    session.feed(b"motd\r\n" + prompt())
    run(session, "a", "1\r\n")
    session.feed(b"\r\r\n" + mark("T") + b"[1]  + done  job\r\n\x1b[J"
                 + mark("A") + b"% \x1b[K")
    run(session, "b", "")
    assert [b.id for b in session.blocks] == [1, 2, 3, 4]
    assert [b.kind for b in session.blocks] == [BACKGROUND, COMMAND,
                                                BACKGROUND, COMMAND]


def test_the_input_area_is_cut_to_a_new_width_not_rewrapped():
    """The shell draws its line again after a change of size; what is
    there until then is not rearranged behind its back."""
    session = make()
    session.feed(prompt() + b"x" * 70)
    assert input_rows(session) == ["% " + "x" * 38, "x" * 32]
    session.resize(20, 10)
    assert input_rows(session) == ["% " + "x" * 18, "x" * 20]


@pytest.mark.parametrize("blank", [1, 2, 3])
def test_any_number_of_blank_lines_around_background_output_goes(blank):
    session = make()
    session.feed(prompt())
    session.feed(submit("sleep 9") + mark("E", "0") + b"\r\n" * blank
                 + b"[1] one\r\n\r\n[2] two\r\n" + b" \r\n" * blank
                 + mark("P", "0", "off", "/work") + mark("A") + b"% ")
    command, notice = session.blocks
    assert notice.lines() == ["[1] one", "", "[2] two"]


# -- the prompt cycle, mark by mark ------------------------------------------

REJECTED = (b"echo )" + mark("F", "echo )") + b"\r\r\n" + mark("T")
            + b"zsh: parse error near `)'\r\n")
NOTICE = (b"\r\r\n" + mark("T") + b"[1]  + done  job\r\n\x1b[J"
          + mark("A") + b"% \x1b[K")


def test_the_line_editor_is_not_ready_while_a_prompt_is_drawn_again():
    session = make()
    session.feed(prompt())
    assert session.line_ready
    session.feed(mark("E", "130") + b" " * COLS + b"\r\r"
                 + mark("P", "130", "on", "/work") + mark("A") + b"% ")
    assert session.state == PROMPT and not session.line_ready
    session.feed(mark("I"))
    assert session.line_ready


def test_the_line_editor_is_not_ready_once_it_has_handed_over_its_line():
    session = make()
    session.feed(prompt())
    session.feed(b"ls" + mark("F", "ls", "not a second field"))
    assert session.state == PROMPT and not session.line_ready
    session.feed(mark("T") + mark("C", "/work"))
    assert session.blocks[0].command == "ls"


def test_a_command_starting_without_a_line_handed_over_ends_readiness():
    session = make()
    session.feed(prompt())
    session.feed(mark("C", "/work", "started by a widget"))
    assert session.state == RUNNING and not session.line_ready


def test_a_new_prompt_starts_from_an_empty_input_area():
    session = make()
    session.feed(prompt())
    session.feed(b"".join(b"a tall buffer, line %d\r\n" % n
                          for n in range(15)))
    assert len(session.input.store.texts) == 6       # scrolled off its top
    session.feed(prompt())
    assert session.input.store.texts == [] and session.blocks == []
    assert input_rows(session) == ["%"]


def test_a_prompt_mark_alone_ends_a_cycle_that_had_its_early_mark_before():
    """The early mark of one cycle says nothing about the next."""
    session = make()
    session.feed(prompt())                           # with the early mark
    session.feed(REJECTED + mark("P", "1", "on", "/work") + mark("A")
                 + b"% ")                            # ... and without
    block, = session.blocks
    assert block.command == "echo )" and block.not_run
    assert not block.running
    assert block.lines() == ["zsh: parse error near `)'"]


def test_a_short_prompt_mark_after_the_early_one_is_taken_as_it_is():
    session = make()
    session.feed(prompt())
    session.feed(submit("x") + mark("E", "0") + mark("P", "0") + mark("A")
                 + b"% ")
    block, = session.blocks
    assert session.state == PROMPT and not block.running
    assert session.cwd == "/work"


def test_notices_in_color_do_not_color_the_prompt_after_them():
    session = make()
    session.feed(prompt())
    session.feed(submit("sleep 9") + mark("E", "0") + b" " * COLS + b"\r\r"
                 + b"\x1b[31m[1] done\r\n" + mark("P", "0", "off")
                 + mark("A") + b"% ")
    command, notice = session.blocks
    assert notice.kind == BACKGROUND and notice.lines() == ["[1] done"]
    assert input_rows(session) == ["%"]
    assert session.input.sid == 0 and session.input.grid[0].styles is None


def test_a_mark_left_from_one_cycle_means_nothing_in_the_next():
    session = make()
    session.feed(prompt())
    session.feed(mark("F", "") + b"\r\r\n" + mark("T")
                 + prompt(text="top\r\n% "))
    assert input_rows(session) == ["top", "%"]
    # Interrupted: a new prompt, and the line editor never stepped aside.
    session.feed(mark("E", "130") + b" " * COLS + b"\r\r"
                 + mark("P", "130", "on", "/work") + mark("A") + b"% ")
    assert session.blocks == [] and input_rows(session) == ["%"]


def test_a_line_handed_over_in_one_cycle_does_not_name_a_block_in_the_next():
    session = make()
    session.feed(prompt())
    session.feed(REJECTED + prompt(1))
    session.feed(mark("F", "") + b"\r\r\n" + mark("T")
                 + b"a hook says hello\r\n" + prompt())
    rejected, hook = session.blocks
    assert rejected.is_command and rejected.command == "echo )"
    assert hook.kind == BACKGROUND and hook.command == ""
    assert hook.lines() == ["a hook says hello"]


def test_one_rejected_line_after_another():
    session = make()
    session.feed(prompt())
    session.feed(REJECTED + prompt(1))
    session.feed(REJECTED.replace(b")", b"(") + prompt(1))
    first, second = session.blocks
    assert (first.command, second.command) == ("echo )", "echo (")
    assert second.lines() == ["zsh: parse error near `('"]
    assert not first.running and not second.running


def test_a_line_that_runs_nothing_and_prints_nothing_leaves_no_block():
    session = make()
    session.feed(prompt())
    session.feed(b"# just a comment" + mark("F", "# just a comment")
                 + b"\r\r\n" + mark("T") + prompt())
    assert session.blocks == [] and input_rows(session) == ["%"]


def test_background_output_before_and_after_a_rejected_line_stays_apart():
    session = make()
    session.feed(prompt())
    session.feed(NOTICE)
    session.feed(REJECTED + prompt(1))
    session.feed(NOTICE.replace(b"job", b"other"))
    before, rejected, after = session.blocks
    assert [b.kind for b in session.blocks] == [BACKGROUND, COMMAND,
                                                BACKGROUND]
    assert before.lines() == ["[1]  + done  job"]
    assert after.lines() == ["[1]  + done  other"]
    assert not before.running and before.duration is None


def test_a_cleared_prompt_and_a_command_in_one_piece_of_output():
    session = make()
    session.feed(prompt())
    first = run(session, "a", "1\r\n")
    assert session.clear_before == 0                 # nothing is out of view
    session.feed(b"\x1b[H\x1b[2J" + submit("ls"))
    second = session.blocks[-1]
    assert session.clear_before == second.id > first.id


def test_a_silent_command_after_a_cleared_prompt_stays_in_view():
    session = make()
    session.feed(prompt())
    run(session, "a", "1\r\n")
    session.feed(b"\x1b[H\x1b[2J\x1b[J" + mark("A") + b"% \x1b[K")
    block = run(session, "true", "")
    assert not block.cleared and session.clear_before == block.id


def test_ordinary_output_scrolls_nothing_out_of_view():
    session = make()
    session.feed(prompt())
    run(session, "a", "1\r\n")
    session.feed(submit("b") + b"2\r\n")
    session.feed(b"3\r\n")
    assert session.clear_before == 0
    assert not session.blocks[-1].cleared


def test_a_clear_inside_a_command_scrolls_what_came_before_it_away():
    session = make()
    session.feed(prompt())
    run(session, "ls", "a\r\n")
    cleared = run(session, "clear", "\x1b[H\x1b[2J\x1b[3J")
    run(session, "echo x", "x\r\n")
    assert session.clear_before == cleared.id + 1
    session.feed(submit("clear; ls") + b"\x1b[H\x1b[2J\x1b[3Ja\r\n")
    kept = session.blocks[-1]
    assert kept.cleared and session.clear_before == kept.id
    # Bringing everything back is not undone by the next piece of output.
    session.clear_blocks()
    session.feed(b"b\r\n")
    assert session.clear_before == 0 and kept.lines() == ["a", "b"]


def test_a_redraw_without_the_editor_stepping_aside_is_not_foreign():
    session = make()
    session.feed(prompt())
    session.feed(NOTICE)
    session.feed(b"\r\x1b[Jdecoration\r\n" + mark("A") + b"% ")
    block, = session.blocks
    assert block.lines() == ["[1]  + done  job"]
    assert input_rows(session) == ["decoration", "%"]


def test_a_list_below_a_decorated_prompt_leaves_the_decoration_alone():
    session = make()
    session.feed(mark("E", "0") + b" " * COLS + b"\r\rdecoration\r\n"
                 + mark("P", "0", "on", "/work") + mark("A") + b"% "
                 + mark("I"))
    session.feed(b"ls\r\r\n" + mark("T") + b"alpha  beta\x1b[A\r"
                 + mark("A") + b"% ls\x1b[K")
    assert session.blocks == []
    assert input_rows(session) == ["decoration", "% ls", "alpha  beta"]


def test_a_prompt_drawn_again_just_below_starts_at_the_top():
    session = make()
    session.feed(prompt())
    session.feed(b"ec\r\r\n" + mark("T") + mark("A") + b"% ec\x1b[K")
    assert session.blocks == []
    assert input_rows(session) == ["% ec"] and session.input.cy == 0


def test_a_line_shown_again_for_approval_is_not_foreign_output():
    """History expansion that is only shown: the line editor gives the line
    up, steps aside, and takes the expanded line up again."""
    session = make()
    session.feed(prompt())
    session.feed(b"!!" + mark("F", "!!") + b"\r\r\n" + mark("T")
                 + b"echo one\r\n" + mark("A") + b"% echo one" + mark("I"))
    assert session.blocks == [] and session.line_ready
    assert input_rows(session) == ["% !!", "echo one", "% echo one"]
    # From then on, what comes between such marks is foreign again.
    session.feed(b"\r\r\n" + mark("T") + b"[1] done\r\n\x1b[J"
                 + mark("A") + b"% echo one\x1b[K")
    block, = session.blocks
    assert block.lines() == ["[1] done"]
    assert input_rows(session) == ["% echo one"]


def test_a_new_prompt_forgets_the_line_given_up_before_it():
    """Even when the line editor has not yet said it is ready again."""
    session = make()
    session.feed(prompt())
    session.feed(submit("cmd") + b"out\r\n" + mark("E", "0") + b" " * COLS
                 + b"\r\r" + mark("P", "0", "on", "/work") + mark("A")
                 + b"% ")
    session.feed(NOTICE)
    command, notice = session.blocks
    assert notice.kind == BACKGROUND
    assert notice.lines() == ["[1]  + done  job"]


def test_blank_lines_alone_do_not_end_a_line_of_background_output():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    idle(session, runtime)
    session.feed(b"abc")                     # foreign, its line not ended
    # An empty line is entered; with NOTIFY off the shell looks for
    # notices and has none.
    session.feed(mark("F", "") + b"\r\r\n" + mark("T") + mark("E", "0")
                 + b" " * COLS + b"\r\r" + mark("P", "0", "off", "/work")
                 + mark("A") + b"% " + mark("I"))
    idle(session, runtime)
    session.feed(b"def\r\n")
    block, = session.blocks
    assert block.lines() == ["abcdef"]


# -- screens, continued ------------------------------------------------------

def test_a_full_screen_program_at_the_prompt_has_the_cursor():
    session = make()
    session.feed(prompt() + b"\x1b[?1049h")
    assert session.prompt_active and session.fullscreen
    assert session.cursor_screen() is session.term.screen
    assert session.cursor_screen() is not session.input


def test_the_end_leaves_full_screen_mode():
    session = make()
    session.feed(prompt())
    session.feed(submit("vim") + b"\x1b[?1049hfull")
    assert session.fullscreen
    session.close()
    assert not session.fullscreen
    assert session.term.screen is session.input


def test_the_end_before_the_first_prompt_leaves_the_input_screen_in_use():
    session = make()
    session.feed(b"startup output\r\n")
    assert session.term.screen is not session.input
    session.close()
    assert session.term.screen is session.input
    assert session.cursor_screen() is session.input


# -- the idle state, continued -----------------------------------------------

def test_an_answer_nobody_asked_for_is_ignored():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    session._last_input = 0.0                # nothing typed for a long time
    session.feed(mark("S", "0", "1"))
    assert session._hold is None and runtime.requests == []


def test_an_answer_after_the_command_started_does_not_slow_the_checks():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    settle(session)
    session.tick()
    session.feed(mark("C", "/work", "started meanwhile"))
    session.feed(mark("S", "1", "2"))
    assert session._sync_gap == 0.0 and not session._sync_waiting


def test_an_answer_is_not_trusted_once_a_key_has_gone_out(clock):
    """However long ago: the answer may be older than the key."""
    session, runtime = quiet(clock)
    session.tick()
    assert runtime.requests == [1]
    clock.now = 1.0
    session.write(b"x")
    clock.now = 10.0
    session.feed(mark("S", "1", "1"))
    assert session._hold is None
    session.tick()                           # ... but the next one is
    session.feed(mark("S", "2", "1"))
    assert runtime.requests == [1, 2] and session._hold is not None


def test_an_idle_prompt_is_asked_about_again_at_once_when_it_ends(clock):
    """Even after a time in which the checks had become rare."""
    session, runtime = backed_off(clock)
    clock.now = 11.5
    session.tick()
    session.feed(mark("S", "6", "1"))        # the other plugin has gone
    assert session._hold is not None
    session.feed(b"\x08")                    # a movement nobody claims
    clock.now = 11.75
    session.tick()
    assert session._hold is None and session.input.cx == 1
    session.tick()
    assert runtime.requests == [1, 2, 3, 4, 5, 6, 7]


def test_clearing_the_blocks_leaves_an_idle_prompt_idle():
    runtime = FakeRuntime()
    session = Session(COLS, 10, runtime=runtime)
    session.feed(prompt())
    session.feed(NOTICE)                     # a background block, still open
    idle(session, runtime)
    hold = session._hold
    assert [b.lines() for b in session.clear_blocks()] \
        == [["[1]  + done  job"]]
    assert session._hold is hold and session.blocks == []
    session.feed(b"later\r\n")               # still taken for foreign
    assert [b.lines() for b in session.blocks] == [["later"]]


def test_what_follows_foreign_output_unclaimed_stays_with_it():
    session, _ = held()
    session.feed(b"X\r\n\x1b[2 q")           # a line, then a cursor shape
    block, = session.blocks
    assert session._hold.pending
    session.write(b"k")                      # a key ends the idle state
    assert session._hold is None
    assert block.screen.cursor_shape == 2 and session.input.cursor_shape == 0


# -- the history cap, continued ----------------------------------------------

def test_nobody_is_told_of_dropped_blocks_when_none_was_dropped():
    session = make(max_lines=20)
    told = []
    session.on_drop = told.append
    session.feed(prompt())
    run(session, "a", "x\r\n" * 10)
    run(session, "b", "x\r\n" * 10)                  # exactly at the cap
    assert told == []
    session = make(max_lines=20)
    session.on_drop = told.append
    session.feed(prompt())
    run(session, "huge", "x\r\n" * 100)              # alone, it stays
    assert told == [] and len(session.blocks) == 1


def test_dropping_stops_as_soon_as_the_rest_fits():
    session = make(max_lines=20)
    session.feed(prompt())
    run(session, "a", "x\r\n" * 5)
    run(session, "b", "x\r\n" * 10)
    run(session, "c", "x\r\n" * 10)                  # 25: a goes, 20 fit
    assert [b.command for b in session.blocks] == ["b", "c"]


def test_a_cap_of_one_line_is_a_cap():
    session = make(max_lines=1)
    session.feed(prompt())
    run(session, "a", "1\r\n")
    run(session, "b", "2\r\n")
    assert [b.command for b in session.blocks] == ["b"]
    session.feed(submit("yes") + b"y\r\n" * 1010)    # 1001 lines are stored
    block = session.blocks[-1]
    assert len(block.store.texts) == 1001 and block.store.dropped == 0
    session.feed(b"y\r\n")
    assert len(block.store.texts) == 1 and block.store.dropped == 1001


def test_resizing_reaches_background_output_that_is_still_open():
    session = make()
    session.feed(prompt())
    session.feed(NOTICE)
    block, = session.blocks
    session.resize(20, 6)
    assert (block.screen.cols, block.screen.rows) == (20, 6)
    assert (session.input.cols, session.input.rows) == (20, 6)


# -- anything, in any order --------------------------------------------------

FUZZ_FIELDS = ["", "0", "1", "130", "on", "off", "/work", "x", "40", "²"]
FUZZ_OUTPUT = [b"abc", b"\r\n", b"\r", b"\x08", b"\x1b[A", b"\x1b[2J",
               b"\x1b[?1049h", b"\x1b[?1049l", b"\x1bc", b"\x1b[31m",
               b"x" * 50, b"\x1b[6n", b"\t", b"\x1b[H", b"\x1b[J", b"\x1b7",
               b"\x1b8", b"\x1b[2 q"]


def any_order(seed: int) -> Session:
    """A session that has seen marks, output, keys, ticks, changes of size
    and clearings in an order drawn from ``seed``."""
    rng = random.Random(seed)
    runtime = FakeRuntime(listening=rng.random() < 0.8)
    session = Session(COLS, rng.choice([3, 10]), runtime=runtime,
                      max_lines=rng.choice([0, 5, 2_000_000]))
    session.pty = FakePty()
    events = [
        lambda: session.feed(mark(rng.choice("EPACTIFSZ"), *rng.choices(
            FUZZ_FIELDS, k=rng.randint(0, 4)))),
        lambda: session.feed(mark("S", str(session._sync_seq),
                                  rng.choice("12"))),
        lambda: session.feed(b"".join(rng.choices(FUZZ_OUTPUT,
                                                  k=rng.randint(1, 4)))),
        lambda: (settle(session), session.tick()),
        lambda: session.write(rng.choice([b"x", b"\r", b"\x03", b""])),
        lambda: session.resize(rng.randint(1, 60), rng.randint(1, 12)),
        lambda: session.clear_blocks(),
        lambda: session.close(),
    ]
    weights = [40, 10, 30, 10, 6, 4, 2, 1]
    for event in rng.choices(events, weights, k=rng.randint(5, 80)):
        event()
    session.close()
    return session


def test_no_order_of_marks_and_events_breaks_the_session():
    for seed in range(400):
        session = any_order(seed)
        assert session.state == CLOSED and session._hold is None, seed
        assert not any(block.running for block in session.blocks), seed
        ids = [block.id for block in session.blocks]
        assert ids == sorted(set(ids)) and 0 not in ids, seed
