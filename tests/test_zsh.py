"""The session against a real zsh: the non-visual acceptance checks."""

from __future__ import annotations

import os
import subprocess
import time

import pytest

from conftest import SMI_COPIED, SMI_PROGRAMS, SMI_TEXT, install_smi
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
