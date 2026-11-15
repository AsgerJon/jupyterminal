"""The session against a real zsh: the non-visual acceptance checks."""

from __future__ import annotations

import os
import shlex
import subprocess
import time

import pytest

from conftest import SMI_COPIED, SMI_PROGRAMS, SMI_TEXT, install_smi
from test_zsh_configs import DISTRIBUTION_ZSHRC
from jupyterminal.session import (BACKGROUND, BOTH, CLOSED, FilterSpec,
                                  copy_text, output_text)


def commands(session):
    return [b for b in session.blocks if b.is_command]


def background(session):
    return [b for b in session.blocks if b.kind == BACKGROUND]


# -- lifecycle ---------------------------------------------------------------

def test_true_and_false(shell):
    live = shell()
    ok = live.run("true")
    bad = live.run("false")
    assert ok.exit_code == 0 and not ok.failed
    assert bad.exit_code == 1 and bad.failed
    assert ok.lines() == [] and bad.lines() == []
    assert ok.command == "true" and bad.command == "false"


def test_output_and_working_directory(shell):
    live = shell()
    os.mkdir(os.path.join(live.home, "sub dir"))
    first = live.run("echo hello; cd 'sub dir'")
    second = live.run("pwd")
    assert output_text(first) == "hello"
    assert first.cwd == live.home
    assert second.cwd == os.path.join(live.home, "sub dir")
    assert output_text(second) == second.cwd
    assert live.session.cwd == second.cwd


def test_empty_enter_creates_no_block(shell):
    live = shell()
    live.type("\r")
    assert live.pump(live.idle_prompt)
    live.type("   \r")
    assert live.pump(live.idle_prompt)
    assert live.session.blocks == []
    assert live.at_prompt()


def test_startup_output_is_a_background_block_at_the_top(shell):
    live = shell(rc="print 'motd: hello'\nnosuchcommand_xyz\n")
    block = live.session.blocks[0]
    assert block.kind == BACKGROUND and not block.running
    lines = block.lines()
    assert lines[0] == "motd: hello"
    assert "command not found: nosuchcommand_xyz" in lines[1]
    live.run("true")
    assert [b.kind for b in live.session.blocks] == [BACKGROUND, "command"]


def test_multi_line_command(shell):
    live = shell()
    block = live.run("for i in 1 2 3; do\recho n$i\rdone")
    assert block.command == "for i in 1 2 3; do\necho n$i\ndone"
    assert output_text(block) == "n1\nn2\nn3"


def test_interrupt_gives_130(shell):
    live = shell()
    block = live.start("sleep 30")
    assert live.pump(live.job_in_foreground)
    live.type(b"\x03")
    live.wait_prompt()
    assert block.exit_code == 130 and block.failed


def test_rejected_line_keeps_the_error(shell):
    live = shell()
    live.type("echo )\r")
    live.pump(lambda: live.session.blocks and live.at_prompt())
    block, = live.session.blocks
    assert block.command == "echo )" and block.not_run
    assert "parse error" in output_text(block)


def test_exit_closes_the_session(shell):
    live = shell()
    seen = []
    live.session.on_exit = seen.append
    live.type("exit 3\r")
    live.pump(lambda: live.session.state == CLOSED)
    assert live.session.state == CLOSED
    assert seen == [3]
    # With the shell gone there is nothing to wait for any more.
    began = time.monotonic()
    assert live.pump(lambda: False, 30.0) is False
    assert time.monotonic() - began < 5.0


def test_waiting_for_something_that_never_comes_says_so(shell):
    live = shell()
    began = time.monotonic()
    assert live.pump(lambda: False, 0.2) is False
    assert 0.2 <= time.monotonic() - began < 5.0
    assert live.at_prompt()


def test_exec_zsh_keeps_blocks_working(shell):
    live = shell()
    live.run("exec zsh")
    block = live.run("echo still here")
    assert output_text(block) == "still here"
    assert [b.command for b in commands(live.session)] == \
        ["exec zsh", "echo still here"]


def test_the_environment_is_left_alone(shell):
    live = shell()
    block = live.run("print -r -- ${ZDOTDIR-unset} $TERM; env | grep -c "
                     "JUPYTERMINAL; print -r -- $POSTEDIT | wc -c")
    lines = output_text(block).split("\n")
    assert lines[0] == "unset xterm-256color"
    assert lines[1] == "0"


def test_user_configuration_is_respected(shell):
    rc = ("PS1='custom> '\n"
          "alias hi='echo hi-from-alias'\n"
          "precmd() { : }\n"
          "preexec() { : }\n")
    live = shell(rc=rc)
    assert "custom>" in live.input_text()
    block = live.run("hi")
    assert output_text(block) == "hi-from-alias"
    assert block.command == "hi"


# -- the copy rules, on real output ------------------------------------------

def test_a_300_character_line_copies_as_one_line(shell):
    live = shell(cols=100)
    block = live.run("print -r -- ${(l:300::x:)}")
    assert output_text(block) == "x" * 300


def test_colors_do_not_reach_the_clipboard(shell):
    live = shell()
    for name in ("alpha", "beta", "gamma"):
        os.mkdir(os.path.join(live.home, name))
    colored = live.run("ls --color=always -1")
    plain = live.run("ls --color=never -1")
    assert "\x1b" not in output_text(colored)
    assert output_text(colored) == output_text(plain)
    assert colored.store.styles          # the colors were kept for display


def test_copy_command_and_output(shell):
    live = shell()
    open(os.path.join(live.home, "file.txt"), "w").close()
    block = live.run("ls -la")
    text = copy_text([block], BOTH)
    head, _, rest = text.partition("\n")
    assert head == "$ ls -la"
    expected = subprocess.run(["ls", "-la"], cwd=live.home,
                              capture_output=True, text=True).stdout
    assert rest == expected.rstrip("\n")


def test_three_blocks_copy_in_order_with_blank_lines(shell):
    live = shell()
    blocks = [live.run(f"echo out{i}") for i in range(3)]
    assert copy_text(blocks) == "out0\n\nout1\n\nout2"


def test_progress_bar_copies_its_final_state_once(shell):
    live = shell()
    block = live.run("for i in {1..25}; do printf '\\r[%-25s] %3d%%' "
                     "${(l:$i::#:)} $((i*4)); sleep 0.01; done; echo")
    assert output_text(block) == "[" + "#" * 25 + "] 100%"


def test_tabs_and_blank_lines_survive(shell):
    live = shell()
    block = live.run("printf 'a\\tb\\n\\n  indented  \\nlast\\n'")
    assert output_text(block) == "a\tb\n\n  indented\nlast"


def test_a_million_lines(shell):
    live = shell()
    block = live.run("seq 1000000", timeout=120)
    lines = block.lines()
    assert len(lines) == 1_000_000
    assert lines[0] == "1" and lines[-1] == "1000000"
    text = output_text(block)
    assert text.count("\n") == 999_999 and text.endswith("\n1000000")


def test_filter_on_real_output(shell):
    live = shell()
    block = live.run("print -l 'ok 1' 'error: disk' 'ok 2' 'Error: net'")
    block.filter = FilterSpec("error")
    assert output_text(block) == "error: disk\nError: net"
    block.filter = None
    assert len(output_text(block).split("\n")) == 4


@pytest.mark.parametrize("program", SMI_PROGRAMS)
def test_nvidia_smi_copies_as_plain_text(shell, program):
    live = shell(cols=120)
    install_smi(live.home)
    block = live.run(program, timeout=60)
    text = output_text(block)
    assert text and "\x1b" not in text
    assert all(line == line.rstrip(" ") for line in text.split("\n"))
    assert text == "\n".join(line.rstrip(" ") for line in block.lines())


def test_a_table_in_color_copies_as_its_text(shell):
    live = shell(cols=120)
    install_smi(live.home)
    block = live.run(SMI_PROGRAMS[0])
    assert block.store.styles                # the colors were kept for display
    assert output_text(block) == SMI_COPIED
    # Not to a terminal, the program prints the same text without colors.
    live.run(f"{SMI_PROGRAMS[0]} > table.txt")
    with open(os.path.join(live.home, "table.txt"),
              encoding="utf-8") as handle:
        assert handle.read() == SMI_TEXT + "\n"
    plain = live.run("cat table.txt")
    assert not plain.store.styles and output_text(plain) == SMI_COPIED


# -- interactive programs ----------------------------------------------------

def test_python_repl_lives_in_one_block(shell):
    live = shell()
    block = live.start("python3 -q")
    live.pump(lambda: any(">>>" in line for line in block.lines()))
    live.type("6 * 7\r")
    live.pump(lambda: "42" in block.lines())
    live.type("exit()\r")
    live.wait_prompt()
    assert len(commands(live.session)) == 1
    transcript = output_text(block)
    assert ">>> 6 * 7" in transcript and "\n42\n" in transcript
    assert "exit()" in transcript
    assert block.exit_code == 0


def test_yes_no_question_lives_in_the_block(shell):
    live = shell()
    block = live.start("read -q 'reply?Proceed? ' && echo && echo yes-path")
    live.pump(lambda: any("Proceed?" in line for line in block.lines()))
    live.type("y")
    live.wait_prompt()
    assert output_text(block) == "Proceed? y\nyes-path"


@pytest.mark.parametrize("program,quit_keys", [
    ("less -+F -+X .zshrc", "q"),
    ("vim -u NONE -N .zshrc", "\x1b:q!\r"),
    ("htop", "q"),
])
def test_full_screen_programs(shell, program, quit_keys):
    live = shell(rc="# a file to look at\n" * 80)
    before = live.run("echo before")
    block = live.start(program)
    assert live.pump(lambda: live.session.fullscreen), "never went full screen"
    screen = live.session.term.screen
    assert live.pump(lambda: any(not row.is_blank() for row in screen.grid))
    live.type(quit_keys)
    live.wait_prompt()
    assert not live.session.fullscreen
    assert block.fullscreen and block.exit_code == 0
    assert block.lines() == []           # the full-screen content is gone
    after = live.run("echo after")
    assert [output_text(b) for b in (before, after)] == ["before", "after"]


def test_clear_scrolls_blocks_out_of_view(shell):
    live = shell()
    first = live.run("echo one")
    cleared = live.run("clear")
    assert live.session.clear_before == cleared.id + 1
    assert first in live.session.blocks      # reachable by scrolling up
    live.run("echo two")
    live.type(b"\x0c")                        # Ctrl+L
    assert live.pump(lambda: live.session.clear_before
                     == live.session._next_id)
    assert len(commands(live.session)) == 3


def test_rerun_puts_the_command_in_the_input_unsubmitted(shell):
    live = shell()
    live.run("echo first")
    live.type("junk")
    assert live.pump(lambda: "junk" in live.input_text())
    assert live.session.set_command_buffer("echo 'again and again'")
    live.pump(lambda: "again and again" in live.input_text())
    assert "junk" not in live.input_text()
    assert len(commands(live.session)) == 1  # nothing ran
    block = live.run("")
    assert block.command == "echo 'again and again'"
    assert output_text(block) == "again and again"


def test_paste_arrives_as_typed_input(shell):
    live = shell()
    # zsh asks for bracketed paste right after its prompt is up.
    assert live.pump(lambda: live.session.term.bracketed_paste)
    live.session.paste("echo pasted\necho second")
    live.pump(lambda: "second" in live.input_text())
    assert commands(live.session) == []      # bracketed: nothing ran yet
    live.type("\r")
    live.pump(lambda: len(commands(live.session)) == 1 and live.at_prompt())
    assert output_text(commands(live.session)[0]) == "pasted\nsecond"


# -- background output -------------------------------------------------------

def test_job_notice_goes_to_a_background_block(shell):
    live = shell()
    live.run("sleep 0.4 &")
    live.type("ec")
    assert live.pump(lambda: any("done" in output_text(b)
                                 for b in background(live.session))
                     and live.input_text().endswith("ec"))
    notice, = background(live.session)
    assert "done" in output_text(notice) and "sleep 0.4" in output_text(notice)
    assert notice.command == ""
    # The half-typed line is intact and still the only thing in the input.
    assert live.input_text().endswith("ec")
    assert live.input_text().count("\n") == 0
    block = live.run("ho typed-through")
    assert output_text(block) == "typed-through"


def test_background_job_output_is_kept_off_the_prompt(shell):
    live = shell()
    live.run("{ until [[ -e go ]]; do sleep 0.05; done; "
             "echo RAW-ONE; echo RAW-TWO } &")
    assert live.pump(live.idle_prompt)
    prompt_before = live.input_text()
    open(os.path.join(live.home, "go"), "w").close()
    assert live.pump(lambda: any("done" in t for b in
                                 background(live.session) for t in b.lines())
                     and live.idle_prompt())
    text = "\n".join(output_text(b) for b in background(live.session))
    assert "RAW-ONE\nRAW-TWO" in text
    assert "RAW" not in live.input_text()
    assert live.input_text() == prompt_before
    block = live.run("echo after")
    assert output_text(block) == "after"


def test_a_large_paste_arrives_whole(shell):
    live = shell()
    assert live.pump(lambda: live.session.term.bracketed_paste)
    words = " ".join(f"w{i:05d}" for i in range(6000))   # about 42 kB
    live.session.paste("print -r -- " + words)
    assert live.pump(lambda: not live.session.pty._outbox
                     and "w05999" in live.input_text(), 30)
    live.type("\r")
    assert live.pump(lambda: live.session.blocks and live.at_prompt(), 30)
    block = live.session.blocks[-1]
    assert output_text(block) == words


# -- found in review ----------------------------------------------------------

def test_ctrl_c_at_an_idle_prompt_takes_effect_at_once(shell):
    live = shell()
    for attempt in range(3):
        live.type("echo abandoned")
        live.wait_idle()
        assert live.input_text().endswith("echo abandoned")
        live.type(b"\x03")
        # Nothing more is typed: the interrupt itself must clear the line.
        assert live.pump(lambda: "abandoned" not in live.input_text()
                         and live.at_prompt(), 10), attempt
        block = live.run("echo next")
        assert block.command == "echo next"
        assert output_text(block) == "next"
    live.wait_idle()
    live.type(b"\x03")                    # on an empty prompt too
    assert live.pump(live.idle_prompt)    # a new prompt, idle in its turn
    assert output_text(live.run("echo again")) == "again"
    assert all(b.command.startswith("echo") for b in commands(live.session))


def test_background_job_output_and_its_notice_land_in_one_clean_block(shell):
    live = shell(rc="PS1='myhost %~ %# '\n")
    live.run("(until [[ -e go ]]; do sleep 0.05; done; "
             "echo BACKGROUND-RESULT-42) &")
    assert live.pump(live.idle_prompt)
    prompt_before = live.input_text()
    open(os.path.join(live.home, "go"), "w").close()
    assert live.pump(lambda: any("done" in t for b in background(live.session)
                                 for t in b.lines())
                     and live.idle_prompt(), 15)
    block, = background(live.session)
    lines = block.lines()
    assert lines[0] == "BACKGROUND-RESULT-42"
    assert "done" in lines[-1] and "myhost" not in "\n".join(lines)
    assert len(lines) == 2
    assert live.input_text() == prompt_before


def test_background_output_after_ctrl_l_shows_in_a_new_block(shell):
    live = shell()

    def printed(text: str):
        """Whether that has arrived as background output."""
        return lambda: any(text in t for b in background(live.session)
                           for t in b.lines())

    def tick(number: int) -> None:
        """Let the job print its next line, at a prompt that is idle."""
        assert live.pump(live.idle_prompt)
        open(os.path.join(live.home, f"go{number}"), "w").close()
        assert live.pump(printed(f"tick {number}"), 15)

    live.run("(for i in 1 2 3; do until [[ -e go$i ]]; do sleep 0.05; done; "
             "echo tick $i; done) &")
    tick(1)
    live.type(b"\x0c")
    tick(2)
    tick(3)
    assert live.pump(printed("done"), 15)
    first, *later = background(live.session)
    assert first.id < live.session.clear_before
    assert later and all(b.id >= live.session.clear_before for b in later)
    assert "tick 3" in "\n".join(t for b in later for t in b.lines())


def test_resizing_during_a_pager_leaves_earlier_output_alone(shell):
    live = shell(rc="# a file to page through\n" * 120)
    block = live.start("for i in 1 2 3; do print -r -- $i:${(l:150::=:)}; "
                       "done; less -c -+F -+X .zshrc; echo AFTER")
    assert live.pump(lambda: live.session.fullscreen)
    live.idle(0.3)
    live.session.resize(60, 30)
    live.idle(0.3)
    live.session.resize(140, 22)
    live.idle(0.3)
    live.type("q")
    live.wait_prompt()
    bar = "=" * 150
    assert block.lines() == [f"1:{bar}", f"2:{bar}", f"3:{bar}", "AFTER"]


def test_mashing_ctrl_c_never_swallows_the_next_key(shell):
    """A second Ctrl+C lands just after the new prompt comes up: the worst
    moment for anything else to be asking the line editor a question.

    (Not at the very instant its line-init hook runs: zsh itself defers an
    interrupt that arrives right then until the next key, with or without
    this terminal.  A few milliseconds later is as fast as keys repeat.)
    """
    live = shell()
    live.session._key_delay = 0.0      # as if every key were long past
    for attempt in range(5):
        live.start("sleep 30")
        assert live.pump(live.job_in_foreground)
        live.type(b"\x03")
        assert live.pump(live.at_prompt, 10)
        live.idle(0.01 + 0.03 * attempt)
        live.type(b"\x03")
        # Never in the same instant as the Ctrl+C: the terminal driver
        # discards input typed along with an interrupt.
        live.idle(0.05 + 0.2 * attempt)
        block = live.run(f"echo ok{attempt}")
        assert block.command == f"echo ok{attempt}", attempt
        assert output_text(block) == f"ok{attempt}"


# -- the text box asks the line editor ----------------------------------------

TAB, UP, DOWN = b"\t", b"\x1b[A", b"\x1b[B"
FALLBACK = {TAB: "expand-or-complete", UP: "up-line-or-history",
            DOWN: "down-line-or-history"}
FILES = {"alpha.txt": "x\n", "alphabet.txt": "x\n", "beta.txt": "x\n"}


class Asking:
    """Hands text to the line editor of a live shell and takes what it
    answers, as the text box does."""

    def __init__(self, live) -> None:
        self.live = live
        self.session = live.session
        self.answers: list[tuple] = []
        self.session.on_edited = \
            lambda text, cursor: self.answers.append((text, cursor))
        #: What the plugins made of a text: the text, how it might go
        #: on, its colors.
        self.hints: list[tuple] = []
        self.session.on_hints = lambda *said: self.hints.append(said)
        live.wait_prompt()

    def send(self, key: bytes, text: str, cursor: int | None = None) -> None:
        cursor = len(text) if cursor is None else cursor
        assert self.session.ask_editor(key, FALLBACK.get(key, ""), text,
                                       cursor)

    def ask(self, key: bytes, text: str, cursor: int | None = None) -> tuple:
        before = len(self.answers)
        self.send(key, text, cursor)
        assert self.live.pump(lambda: len(self.answers) > before, 10.0), \
            "no answer"
        self.live.idle(0.1)
        return self.answers[-1]

    def again(self, key: bytes) -> tuple:
        """The same key once more, on what the last answer was."""
        return self.ask(key, *self.answers[-1])

    def look(self, text: str) -> tuple:
        """How the plugins would go on from ``text``, and its colors."""
        before = len(self.hints)
        self.session.look(text, len(text))
        assert self.live.pump(lambda: len(self.hints) > before, 10.0), \
            "no hints"
        assert self.hints[-1][0] == text
        return self.hints[-1][1:]

    def run(self, command: str):
        """Hand a command over as the text box does; its block."""
        session = self.session
        before = len(commands(session))
        assert session.run_command(command)
        assert self.live.pump(lambda: len(commands(session)) > before
                              and self.live.at_prompt()), command
        return commands(session)[-1]


def test_the_line_editor_completes_what_it_is_handed(shell):
    box = Asking(shell(files=FILES))
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    assert box.ask(TAB, "ls al") == ("ls alpha", 8)  # as far as they agree
    assert box.session.listed() == (1, 0)
    assert box.again(TAB) == ("ls alpha", 8)         # now it lists them
    assert box.session.listed() == (1, 1)
    assert box.live.input_text().split("\n")[1].split() == \
        ["alpha.txt", "alphabet.txt"]
    assert box.again(TAB) == ("ls alpha.txt", 12)    # and takes turns
    assert box.again(TAB) == ("ls alphabet.txt", 15)
    # In the middle of a line, and of several lines.
    assert box.ask(TAB, "ls be; echo done", 5) == \
        ("ls beta.txt ; echo done", 12)
    assert box.ask(TAB, "echo x\nls be\necho y", 12) == \
        ("echo x\nls beta.txt \necho y", 19)
    assert box.ask(TAB, "ls zz") == ("ls zz", 5)     # nothing to add
    # None of it was run, and the editor is as ready as before: a command
    # handed over takes the place of what it was last asked about.
    session = box.session
    assert commands(session) == [] and session.input_ready
    assert box.run("echo ok").lines() == ["ok"]
    assert [b.command for b in commands(session)] == ["echo ok"]


def test_the_line_editor_walks_through_its_history(shell):
    box = Asking(shell())
    for command in ("echo one", "echo two"):
        box.live.run(command)
    assert box.ask(UP, "dra") == ("echo two", 8)
    assert box.again(UP) == ("echo one", 8)
    assert box.again(UP) == ("echo one", 8)          # there is none older
    assert box.again(DOWN) == ("echo two", 8)
    assert box.again(DOWN) == ("dra", 3)             # what was being typed
    assert box.again(DOWN) == ("dra", 3)
    # Thrown away on purpose: the walk starts over, from an empty line.
    assert box.ask(UP, "dra") == ("echo two", 8)
    assert box.again(UP) == ("echo one", 8)
    assert box.ask(UP, "") == ("echo two", 8)
    assert box.again(DOWN) == ("", 0)


def test_a_key_does_in_the_text_box_what_it_is_bound_to_in_the_shell(shell):
    box = Asking(shell(
        "PS1='%% '\n"
        "autoload -Uz up-line-or-beginning-search "
        "down-line-or-beginning-search\n"
        "zle -N up-line-or-beginning-search\n"
        "zle -N down-line-or-beginning-search\n"
        "bindkey '^[[A' up-line-or-beginning-search\n"
        "bindkey '^[[B' down-line-or-beginning-search\n"
        "bindkey '^I' self-insert\n", files=FILES))
    for command in ("echo one", "true", "echo two", "false"):
        box.live.run(command)
    # Up looks for lines that begin as this one does, and goes on doing
    # so: the widget finds itself to have been the last one used.
    assert box.ask(UP, "ec")[0] == "echo two"
    assert box.again(UP)[0] == "echo one"
    assert box.again(DOWN)[0] == "echo two"
    assert box.again(DOWN) == ("ec", 2)
    # A key that only types itself in the shell falls back on the widget
    # the request names; with none named, nothing happens.
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    assert box.ask(b"\x1b[Z", "ls be") == ("ls be", 5)


#: A common way to search the history from the arrow keys: two widgets
#: that share a function.  It tells by its name which way to go, and by
#: the name of the widget used before whether to go on from there.
SEARCHING = (
    "PS1='%% '\n"
    "autoload -Uz history-search-end\n"
    "zle -N history-beginning-search-backward-end history-search-end\n"
    "zle -N history-beginning-search-forward-end history-search-end\n"
    "bindkey '^[[A' history-beginning-search-backward-end\n"
    "bindkey '^[[B' history-beginning-search-forward-end\n")
LEFT, BACKSPACE = b"\x1b[D", b"\x7f"


class Typing:
    """Types at the prompt of a live shell, the way one does in any
    terminal, and reads text and cursor off the line editor's line (one
    line, behind a prompt of two characters)."""

    def __init__(self, live) -> None:
        self.live = live

    def type(self, keys: str | bytes) -> tuple:
        self.live.type(keys)
        self.live.idle(0.25)
        line = self.live.input_text().split("\n")[0]
        return line[2:], self.live.session.input.cx - 2


def both(shell, rc: str, ran: tuple = (), **kwargs) -> tuple:
    """Two shells of the same configuration and history: one to type in,
    one for the text box to ask."""
    typing, box = Typing(shell(rc, **kwargs)), Asking(shell(rc, **kwargs))
    for live in (typing.live, box.live):
        for command in ran:
            live.run(command)
    return typing, box


RAN = ("echo three", "echo one", "true", "echo two", "false")


def test_a_key_follows_the_one_before_it_as_at_the_prompt(shell):
    typing, box = both(shell, SEARCHING, RAN)
    typing.type("ec")
    typed = [typing.type(key) for key in (UP, UP, UP, UP, DOWN, DOWN, DOWN)]
    assert typed == [("echo two", 8), ("echo one", 8), ("echo three", 10),
                     ("echo three", 10), ("echo one", 8), ("echo two", 8),
                     ("ec", 2)]
    asked = [box.ask(UP, "ec")] + [
        box.again(key) for key in (UP, UP, UP, DOWN, DOWN, DOWN)]
    assert asked == typed


def test_what_was_done_in_between_counts_as_at_the_prompt(shell):
    typing, box = both(shell, SEARCHING, RAN, files=FILES)
    # Another key in between: the search starts anew from the whole line.
    typing.type("ec")
    typed = [typing.type(key) for key in (UP, TAB, UP)]
    assert typed == [("echo two", 8)] * 3
    assert [box.ask(UP, "ec"), box.again(TAB), box.again(UP)] == typed
    # The cursor was moved: what stands before it is looked for.
    typed = [typing.type(key) for key in (LEFT * 3, UP)]
    assert typed == [("echo two", 5), ("echo one", 8)]
    assert box.ask(UP, "echo two", 5) == typed[-1]
    # Something was typed and taken away again: the text is the same, and
    # still the key before it is not followed.  (Following it would have
    # found the next line that begins with "echo ".)
    typed = [typing.type(key) for key in ("x", BACKSPACE, UP)]
    assert typed[1:] == [("echo one", 8), ("echo one", 8)]
    box.session.look("echo onex", 9)
    box.session.look("echo one", 8)
    assert box.ask(UP, "echo one", 8) == typed[-1]
    # Left alone now, it is searched on from where the search before the
    # typing had got to.
    typed = typing.type(UP)
    assert typed == ("echo three", 10) and box.again(UP) == typed


def test_a_menu_of_completions_is_walked_as_at_the_prompt(shell):
    typing, box = both(shell, "PS1='%% '\n", files=FILES)
    typing.type("ls al")
    typed = [typing.type(key) for key in (TAB, TAB, TAB, TAB)]
    assert typed == [("ls alpha", 8), ("ls alpha", 8),
                     ("ls alpha.txt", 12), ("ls alphabet.txt", 15)]
    asked = [box.ask(TAB, "ls al")] + [box.again(TAB) for _ in range(3)]
    assert asked == typed
    # Typing ends the walk, even if it leaves the text as it was: the
    # next Tab completes the word that is there.
    typed = [typing.type(key) for key in ("x", BACKSPACE, TAB)]
    assert typed[-1] == ("ls alphabet.txt", 16)     # a blank behind it
    box.session.look("ls alphabet.txtx", 16)
    box.session.look("ls alphabet.txt", 15)
    assert box.again(TAB) == ("ls alphabet.txt ", 16)


def test_the_line_editor_asks_before_it_lists_very_many(shell):
    box = Asking(shell(files={f"file{i:03}.txt": "" for i in range(150)}))
    assert box.ask(TAB, "ls fi") == ("ls file", 7)
    box.send(TAB, "ls file")
    session = box.session
    assert box.live.pump(lambda: session.shell_asks, 10.0)
    assert session.editing and not session.input_ready
    assert "do you wish to see all 150 possibilities" in box.live.input_text()
    box.live.type("n")                               # the answer is its own
    assert box.live.pump(lambda: not session.editing, 10.0)
    assert box.answers[-1] == ("ls file", 7)
    assert session.input_ready and not session.shell_asks


def test_a_menu_of_the_line_editor_is_worked_with_its_own_keys(shell):
    box = Asking(shell(
        "PS1='%% '\nzmodload zsh/complist\n"
        "autoload -Uz compinit && compinit -u -d $HOME/.zcompdump\n"
        "zstyle ':completion:*' menu select\n", files=FILES))
    assert box.ask(TAB, "ls al") == ("ls alpha", 8)
    assert box.again(TAB) == ("ls alpha", 8)         # the list
    box.send(TAB, "ls alpha")                        # the menu: no answer
    session = box.session
    assert box.live.pump(lambda: session.shell_asks, 10.0)
    assert "ls alpha.txt" in box.live.input_text()   # its first entry
    box.live.type(b"\x1b[C")                         # to the next one
    assert box.live.pump(
        lambda: "ls alphabet.txt" in box.live.input_text(), 10.0)
    box.live.type("\r")                              # take it
    assert box.live.pump(lambda: not session.editing, 10.0)
    assert box.answers[-1] == ("ls alphabet.txt ", 16)
    assert commands(session) == []                   # nothing was run


def test_an_interrupt_ends_a_request_that_takes_too_long(shell):
    box = Asking(shell("PS1='%% '\nslow() { sleep 30 }\nzle -N slow\n"
                       "bindkey '^I' slow\n"))
    session = box.session
    box.send(TAB, "ls al", 3)
    box.live.idle(0.3)
    assert session.editing and box.answers == []     # it is still at it
    began = time.monotonic()
    box.live.type(b"\x03")
    assert box.live.pump(lambda: not session.editing, 10.0)
    assert time.monotonic() - began < 5.0
    assert box.answers == [("ls al", 3)]             # the text, as it was
    box.live.wait_prompt()
    assert commands(session) == []
    assert box.ask(UP, "") == ("", 0)                # and it works again


# -- what the plugins of the configuration make of a command line -------------

#: Stand-ins for the two plugins the text box asks, with the interface of
#: the real ones.  zsh-autosuggestions goes on with a line the way the
#: newest line of the history that begins like it does ...
SUGGESTING = r"""
_zsh_autosuggest_fetch_suggestion() {
  emulate -L zsh
  typeset -g suggestion=${history[(r)${(b)1}*]}
}
"""
#: ... and zsh-syntax-highlighting gives the name of a command its color:
#: green if there is such a command, red if there is none.  (What begins
#: with a P is about the prompt, and is not passed on.)
COLORING = r"""
_zsh_highlight() {
  emulate -L zsh
  local name=${BUFFER%%[[:space:]]*}
  local style='fg=red,bold'
  (( $+commands[$name] + $+builtins[$name] )) && style='fg=green'
  region_highlight=("0 $#name $style memo=stand-in" "P0 1 standout")
  (( $#BUFFER > $#name )) &&
    region_highlight+=("$(( $#name + 1 )) $#BUFFER none")
}
"""
STAND_INS = SUGGESTING + COLORING


def installed(name: str) -> str:
    """The file a plugin is loaded from, where it is installed on this
    machine: by Oh My Zsh or by a package.  Nothing where it is not."""
    places = [os.path.join(os.environ.get("ZSH")
                           or os.path.expanduser("~/.oh-my-zsh"),
                           "custom", "plugins"),
              "/usr/share/zsh/plugins", "/usr/share", "/usr/local/share",
              "/opt/homebrew/share"]
    files = [os.path.join(place, name, name + ".zsh") for place in places]
    return next(filter(os.path.isfile, files), "")


#: The stand-ins, and the real plugins as well where both are installed.
REAL = [installed("zsh-autosuggestions"), installed("zsh-syntax-highlighting")]
PLUGINS = [STAND_INS] + [
    "".join(f"source {shlex.quote(path)}\n" for path in REAL)] * all(REAL)
PLUGIN_IDS = ["stand-ins", "installed"][:len(PLUGINS)]


def test_a_shell_without_such_plugins_is_not_asked(shell):
    box = Asking(shell("PS1='%% '\n"))
    session = box.session
    assert session.hinting is False
    session.look("echo o", 6)
    box.live.idle(0.3)
    assert box.hints == [] and box.live.input_text() == "%"
    # Every prompt says it anew: a plugin may be loaded at any time, and
    # a text that was waiting is looked at then.
    box.run("_zsh_highlight() { region_highlight=('0 4 bold') }")
    assert session.hinting is True
    assert box.live.pump(lambda: box.hints, 10.0)
    assert box.hints == [("echo o", "", [(0, 4, "bold")])]
    assert box.live.pump(lambda: box.live.input_text() == "% echo o", 10.0)


@pytest.mark.parametrize("plugin, suggestion, colors", [
    (SUGGESTING, "echo one two", []),
    (COLORING, "", [(0, 4, "fg=green"), (5, 6, "none")])])
def test_either_plugin_alone_says_its_part(shell, plugin, suggestion, colors):
    box = Asking(shell("PS1='%% '\n" + plugin))
    box.live.run("echo one two")
    assert box.session.hinting is True
    assert box.look("echo o") == (suggestion, colors)


@pytest.mark.parametrize("plugins", PLUGINS, ids=PLUGIN_IDS)
def test_the_plugins_say_how_a_line_goes_on_and_color_it(shell, plugins):
    box = Asking(shell("PS1='%% '\n" + plugins, files=FILES))
    ran = ["echo one two", "ls beta.txt alpha.txt", "true"]
    for command in ran:
        box.live.run(command)
    assert box.session.hinting is True
    suggestion, colors = box.look("echo o")
    assert suggestion == "echo one two" and (0, 4, "fg=green") in colors
    assert box.look("tr")[0] == "true"
    assert box.look("nosuch") == ("", [(0, 6, "fg=red,bold")])
    # The line editor has the text it looked at, and draws it after it
    # has answered.
    assert box.live.pump(lambda: box.live.input_text() == "% nosuch", 10.0)
    # What a key makes of the text comes with what they say about that.
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    text, suggestion, colors = box.hints[-1]
    assert (text, suggestion) == ("ls beta.txt ", "ls beta.txt alpha.txt")
    assert (0, 2, "fg=green") in colors
    # zsh-autosuggestions shows what it suggests behind the line editor's
    # text.  Here the text box does that: nothing follows the text there,
    # where it would pass for something the key has listed.
    box.live.idle(0.3)
    assert box.live.input_text() == "% ls beta.txt"
    assert box.session.listed() == (1, 0)
    # A command is run as before, and nothing of all this was printed.
    assert box.run("echo done").lines() == ["done"]
    assert [b.command for b in commands(box.session)] == ran + ["echo done"]
    assert background(box.session) == []


def test_text_new_to_the_line_editor_starts_the_history_walk_over(shell):
    box = Asking(shell("PS1='%% '\n" + STAND_INS))
    for command in ("echo one", "echo two"):
        box.live.run(command)
    assert box.ask(UP, "") == ("echo two", 8)
    assert box.again(UP) == ("echo one", 8)
    # Typed on from there, and looked at: the text is the newest line now,
    # and Up from it finds the newest command again.
    assert box.look("echo onex") == ("", [(0, 4, "fg=green"), (5, 9, "none")])
    assert box.ask(UP, "echo onex") == ("echo two", 8)
    assert box.again(DOWN) == ("echo onex", 9)
    assert box.hints[-1] == ("echo onex", "",
                             [(0, 4, "fg=green"), (5, 9, "none")])
    assert box.again(DOWN) == ("echo onex", 9)       # there is none newer


def test_no_plugin_draws_behind_the_line_editors_text(shell):
    """zsh-autosuggestions wraps itself around the widgets of the line
    editor: it keeps each under another name and takes its place.  After
    each of them it shows its suggestion behind the text, unless it has
    been switched off.  The stand-in here does the same."""
    box = Asking(shell(
        "PS1='%% '\n" + STAND_INS +
        "typeset -ga seen\n"
        "wrapped() {\n"
        "  seen+=($WIDGET:${+_ZSH_AUTOSUGGEST_DISABLED})\n"
        "  zle kept-$WIDGET\n"
        "  (( ${+_ZSH_AUTOSUGGEST_DISABLED} )) || POSTDISPLAY=' and so on'\n"
        "}\n"
        "for name in expand-or-complete end-of-history; do\n"
        "  zle -A $name kept-$name\n"
        "  zle -N $name wrapped\n"
        "done\n"
        "stubborn() { POSTDISPLAY=' and so forth' }\n"
        "zle -N stubborn\n"
        "bindkey '^[[Z' stubborn\n", files=FILES))
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    assert box.live.input_text() == "% ls beta.txt"
    assert box.session.listed() == (1, 0)
    # What some other widget puts there all the same is taken away.
    assert box.ask(b"\x1b[Z", "ls be") == ("ls be", 5)
    assert box.live.input_text() == "% ls be"
    assert box.session.listed() == (1, 0)
    # The plugin was switched off for the widget the key runs, and for no
    # longer; going back to the newest line is zsh's own doing, wrapped
    # in nothing.
    block = box.run("print -r -- $seen ${+_ZSH_AUTOSUGGEST_DISABLED}")
    assert block.lines() == ["expand-or-complete:1 0"]


def test_the_text_box_asks_the_line_editor_of_a_distribution(shell):
    """Debian and Ubuntu give every user the completion system and keys
    that count on the terminal being in application mode.  The release
    workflows run on such a machine, and so do many users."""
    box = Asking(shell("PS1='%% '\n" + DISTRIBUTION_ZSHRC + STAND_INS,
                       files=FILES))
    for command in ("echo one", "echo two"):
        box.live.run(command)
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    assert box.ask(TAB, "ls al") == ("ls alpha", 8)
    assert box.session.listed() == (1, 0)
    assert box.again(TAB) == ("ls alpha", 8)         # the list
    assert box.session.listed() == (1, 1)
    assert box.again(TAB) == ("ls alpha.txt", 12)    # and one after the other
    assert box.again(TAB) == ("ls alphabet.txt", 15)
    # The arrow keys the way a terminal in application mode sends them,
    # which is the way the box hands them on while the line editor waits.
    assert box.live.pump(lambda: box.session.term.app_cursor)
    up, down = b"\x1bOA", b"\x1bOB"
    assert box.ask(up, "dra") == ("echo two", 8)
    assert box.again(up) == ("echo one", 8)
    assert box.again(down) == ("echo two", 8)
    assert box.again(down) == ("dra", 3)
    assert box.look("echo t") == (
        "echo two", [(0, 4, "fg=green"), (5, 6, "none")])
    assert box.run("echo done").lines() == ["done"]
    assert background(box.session) == []


@pytest.mark.parametrize("options", [
    "nounset", "errreturn", "ksharrays", "shwordsplit globsubst",
    "warncreateglobal warnnestedvar",
    "nounset errreturn ksharrays shwordsplit globsubst warncreateglobal "
    "warnnestedvar"])
def test_the_configuration_may_set_any_options(shell, options):
    box = Asking(shell(
        f"PS1='%% '\n{STAND_INS}"
        "stubborn() { POSTDISPLAY=' and so forth' }\n"
        "zle -N stubborn\n"
        "bindkey '^[[Z' stubborn\n"
        f"setopt {options}\n", files=FILES))
    block = box.live.run("echo works; false")
    assert block.lines() == ["works"] and block.exit_code == 1
    assert block.command == "echo works; false"
    assert box.session.hinting is True
    assert box.look("echo w") == (
        "echo works; false", [(0, 4, "fg=green"), (5, 6, "none")])
    assert box.ask(TAB, "ls be") == ("ls beta.txt ", 12)
    assert box.hints[-1] == (
        "ls beta.txt ", "", [(0, 2, "fg=green"), (3, 12, "none")])
    assert box.ask(b"\x1b[Z", "ls be") == ("ls be", 5)
    assert box.live.input_text() == "% ls be"
    assert box.ask(UP, "") == ("echo works; false", 17)
    assert box.run("echo again").lines() == ["again"]
    assert background(box.session) == []             # and no complaints
