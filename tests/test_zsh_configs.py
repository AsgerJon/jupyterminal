"""The integration under shell configurations that stress it: prompts
that redraw themselves, plugins that draw from event handlers, hooks the
user already owns."""

from __future__ import annotations

import os

from jupyterminal.session import BACKGROUND, output_text


def background(session):
    return [b for b in session.blocks if b.kind == BACKGROUND]


def test_two_line_prompt_with_right_prompt(shell):
    live = shell(rc="PS1=$'%F{blue}top line%f\\n%# '\nRPROMPT='right'\n")
    rows = live.input_text().split("\n")
    assert rows[0].startswith("top line") and rows[1].startswith("%")
    assert "right" in rows[1]
    block = live.run("echo ok")
    assert output_text(block) == "ok"
    live.run("sleep 0.3 &")
    live.type("abc")
    assert live.pump(lambda: any("done" in output_text(b)
                                 for b in background(live.session))
                     and live.input_text().count("\n") == 1
                     and live.input_text().split("\n")[1].startswith("% abc"))
    rows = live.input_text().split("\n")
    assert len(rows) == 2 and rows[0].startswith("top line")
    assert rows[1].startswith("% abc")
    assert "done" in output_text(background(live.session)[0])


def test_a_prompt_that_redraws_itself_every_second(shell):
    rc = ("PS1='%D{%S} %# '\n"
          "TMOUT=1\n"
          "TRAPALRM() { zle reset-prompt }\n")
    live = shell(rc=rc)
    live.type("half typed")
    seen = set()

    def redrawn() -> bool:
        """The clock in the prompt has shown three different seconds."""
        text = live.input_text()
        seen.update({text} if text.endswith("% half typed") else ())
        return len(seen) >= 3

    assert live.pump(redrawn, 30)        # several asynchronous redraws
    after = live.input_text()
    assert live.session.blocks == []     # none of it became "output"
    assert after.count("\n") == 0 and after.endswith("% half typed")
    live.type("\x15")
    block = live.run("echo still works")
    assert output_text(block) == "still works"


def test_a_plugin_drawing_suggestions_from_an_event_handler(shell):
    rc = r'''
zmodload zsh/zle
_sug_show() { POSTDISPLAY=" <suggested>" }
zle -N _sug_show
_sug_done() {
  local fd=$1
  zle -F $fd
  exec {fd}<&-
  zle _sug_show
  zle -R
}
_sug_insert() {
  zle .self-insert
  local fd
  exec {fd}< <(sleep 0.15; print x)
  zle -F $fd _sug_done
}
zle -N self-insert _sug_insert
'''
    live = shell(rc=rc)

    def suggested(typed: str):
        return lambda: f"{typed} <suggested>" in live.input_text()

    for typed in ("g", "gi", "git"):
        live.type(typed[-1])
        assert live.pump(suggested(typed))
    assert live.session.blocks == []     # the suggestion is not output
    live.type("\x15")
    assert live.pump(lambda: "git" not in live.input_text())
    block = live.run("echo fine")
    assert output_text(block) == "fine"


def test_vi_mode(shell):
    live = shell(rc="bindkey -v\n")
    live.type("echo wrong")
    live.type("\x1b")                    # to command mode
    live.idle(0.3)
    live.type("bcwright")                # back a word, change it
    live.idle(0.2)
    block = live.run("")
    assert block.command == "echo right"
    live.type("\x1b")
    live.idle(0.3)
    assert live.session.set_command_buffer("echo from-vicmd")
    assert live.pump(lambda: "from-vicmd" in live.input_text())
    block = live.run("")
    assert output_text(block) == "from-vicmd"


def test_the_users_own_line_editor_hooks_keep_running(shell):
    rc = ("zle-line-init() { typeset -g MY_INIT=ran }\n"
          "zle -N zle-line-init\n"
          "zle-line-finish() { typeset -g MY_FINISH=ran }\n"
          "zle -N zle-line-finish\n"
          "precmd() { typeset -g MY_PRECMD=ran }\n"
          "preexec() { typeset -g MY_PREEXEC=$1 }\n")
    live = shell(rc=rc)
    live.run("true")
    block = live.run("print -r -- $MY_INIT $MY_FINISH $MY_PRECMD $MY_PREEXEC")
    assert output_text(block).startswith("ran ran ran print -r --")


#: What the system-wide zshrc of a Debian or Ubuntu machine sets up before
#: the user's own file is read: keys bound by what terminfo says they send,
#: the terminal switched to application mode for as long as the line editor
#: is active (only then do the keys send that), and the completion system.
DISTRIBUTION_ZSHRC = r"""
typeset -A key
key=(Up "${terminfo[kcuu1]}" Down "${terminfo[kcud1]}"
     Home "${terminfo[khome]}" End "${terminfo[kend]}")
[[ -n ${key[Up]} ]] && bindkey "${key[Up]}" up-line-or-history
[[ -n ${key[Down]} ]] && bindkey "${key[Down]}" down-line-or-history
[[ -n ${key[Home]} ]] && bindkey "${key[Home]}" beginning-of-line
[[ -n ${key[End]} ]] && bindkey "${key[End]}" end-of-line
if (( ${+terminfo[smkx]} )) && (( ${+terminfo[rmkx]} )); then
  function zle-line-init () { emulate -L zsh; printf '%s' ${terminfo[smkx]} }
  function zle-line-finish () { emulate -L zsh; printf '%s' ${terminfo[rmkx]} }
  zle -N zle-line-init
  zle -N zle-line-finish
fi
autoload -U compinit
compinit
"""


def test_a_distribution_that_keeps_the_keypad_in_application_mode(shell):
    """The release workflows run on such a system, and so do many users."""
    live = shell(rc=DISTRIBUTION_ZSHRC)
    term = live.session.term
    assert live.session.blocks == []     # the setup itself printed nothing
    assert live.pump(lambda: term.app_cursor and term.app_keypad)
    first = live.run("echo first")
    assert output_text(first) == "first"
    # The arrow key, as a terminal in application mode sends it, is the
    # one the distribution bound: it brings the last command back.
    assert live.pump(lambda: term.app_cursor)
    live.type(b"\x1bOA")
    assert live.pump(lambda: live.input_text().endswith("echo first"))
    again = live.run("")
    assert again.command == "echo first" and output_text(again) == "first"
    # A program gets the terminal the way it expects to find it, and none
    # of the switching back and forth is anybody's output.
    block = live.start("sleep 30")
    assert live.pump(live.job_in_foreground)
    assert live.pump(lambda: not term.app_cursor and not term.app_keypad)
    live.type(b"\x03")
    live.wait_prompt()
    # (What the block holds is the terminal's own echo of the interrupt.)
    assert block.exit_code == 130 and block.lines() == ["^C"]
    assert live.pump(lambda: term.app_cursor)
    assert [b.kind for b in live.session.blocks] == [first.kind] * 3


def test_prompt_with_command_substitution(shell):
    live = shell(rc="setopt promptsubst\nPS1='$(print dyn-$((1+1))) %# '\n")
    assert live.input_text().startswith("dyn-2 %")
    block = live.run("echo ok")
    assert output_text(block) == "ok"
    assert live.input_text().startswith("dyn-2 %")


def test_partial_line_marker_never_reaches_the_output(shell):
    live = shell(rc="unsetopt promptsp\nPROMPT_EOL_MARK='<<EOL>>'\n")
    block = live.run("printf 'no newline'")
    assert output_text(block) == "no newline"
    block = live.run("printf 'x\\n'")
    assert output_text(block) == "x"


def test_job_notices_with_notify_off(shell):
    live = shell(rc="unsetopt notify\n")
    live.run("(sleep 0.2; : > finished) &")
    assert live.pump(lambda: os.path.exists(
        os.path.join(live.home, "finished")))
    live.idle(0.3)                            # time to say so, if it would
    assert background(live.session) == []     # zsh has not said it yet
    block = live.run("echo next")
    assert output_text(block) == "next"       # and not into this block
    notice, = background(live.session)
    assert "done" in output_text(notice)


def test_login_shell_and_zdotdir_from_zshenv(shell):
    live = shell(rc="",
                 files={".zshenv": "export ZDOTDIR=$HOME/conf\n",
                        "conf/.zshrc": "alias hi='echo from-conf'\n"
                                       "PS1='conf> '\n"})
    assert live.input_text().startswith("conf>")
    block = live.run("hi; print -r -- $ZDOTDIR; env | grep -c ^ZDOTDIR=")
    lines = output_text(block).split("\n")
    assert lines[0] == "from-conf"
    assert lines[1].endswith("/conf") and lines[2] == "1"


def test_manual_line_when_the_startup_shims_are_bypassed(shell, monkeypatch):
    """A system whose /etc/zsh/zshenv sets ZDOTDIR never reads the shims.
    One line in the user's own .zshrc loads the integration instead."""
    from jupyterminal.shell import ShellRuntime
    original = ShellRuntime.environment

    def bypassed(self, base=None):
        env = original(self, base)
        env.pop("JUPYTERMINAL_USER_ZDOTDIR", None)
        env["ZDOTDIR"] = env["HOME"]      # as if a global file had set it
        return env

    monkeypatch.setattr(ShellRuntime, "environment", bypassed)
    rc = ("PS1='manual> '\n"
          "[[ -n $JUPYTERMINAL_DIR ]] && "
          "source $JUPYTERMINAL_DIR/integration.zsh\n")
    live = shell(rc=rc)
    assert live.input_text().startswith("manual>")
    bad = live.run("false")
    good = live.run("print -r -- $ZDOTDIR; env | grep -c JUPYTERMINAL")
    assert bad.exit_code == 1 and good.exit_code == 1    # grep counted 0
    assert output_text(good) == f"{live.home}\n0"


# -- replacing the shell ------------------------------------------------------

def test_exec_of_another_program_gets_nothing_of_ours(shell):
    live = shell()
    block = live.start("exec sh -c 'echo ZD=${ZDOTDIR-unset} "
                       "NONCE=${JUPYTERMINAL_NONCE:+leaked}'")
    live.pump(lambda: live.session.state == "closed")
    assert block.lines() == ["ZD=unset NONCE="]


def test_exec_zsh_through_an_alias_or_a_compound_line(shell):
    live = shell(rc="alias reload='exec zsh'\n")
    live.run("reload")
    assert output_text(live.run("echo one")) == "one"
    live.run("cd . && exec zsh -i")
    assert output_text(live.run("echo two")) == "two"
    assert [b.command for b in live.session.blocks if b.is_command] == \
        ["reload", "echo one", "cd . && exec zsh -i", "echo two"]
    block = live.run("env | grep -c -e JUPYTERMINAL -e ZDOTDIR")
    assert output_text(block) == "0"


def test_exec_zsh_starts_the_way_a_plain_one_would(shell):
    """An unexported ZDOTDIR set in ~/.zshenv: the new shell must read
    ~/.zshenv again, and ZDOTDIR must stay unexported."""
    live = shell(rc="",
                 files={".zshenv": "ZDOTDIR=$HOME/zd\nREAD_ZSHENV=$$\n",
                        "zd/.zshrc": "PS1='zd> '\n"})
    assert live.input_text().startswith("zd>")
    live.run("exec zsh")
    assert live.input_text().startswith("zd>")
    block = live.run("print -r -- ${READ_ZSHENV:+read} ${(t)ZDOTDIR} "
                     "${ZDOTDIR:t}; env | grep -c ZDOTDIR")
    assert output_text(block) == "read scalar zd\n0"


def test_paths_derived_from_the_shim_directory_are_repaired(tmp_path):
    """A system-wide startup file that computes, say, the history file
    from ZDOTDIR sees the shim directory; that must not stick."""
    import subprocess
    from conftest import ZSH
    from jupyterminal.shell import ShellRuntime
    home = tmp_path / "home"
    home.mkdir()
    runtime = ShellRuntime()
    try:
        env = runtime.environment({
            "HOME": str(home), "PATH": "/usr/bin:/bin",
            "HISTFILE": runtime.path + "/.zsh_history",
            "ELSEWHERE": "/var/tmp/keep"})
        out = subprocess.run(
            [ZSH, "-c", "print -r -- $HISTFILE $ELSEWHERE ${ZDOTDIR-unset}"],
            env=env, capture_output=True, text=True, timeout=20)
    finally:
        runtime.cleanup()
    assert out.stdout.strip() == f"{home}/.zsh_history /var/tmp/keep unset"
    assert out.stderr == ""


# -- found in review ----------------------------------------------------------

def test_an_idle_prompt_stays_idle_when_another_plugin_watches_events(shell):
    """A second zle -F handler means output cannot be diverted - which
    must not turn into asking about it in a loop."""
    rc = ("mkfifo $HOME/ff\n"
          "exec {keep}<>$HOME/ff\n"
          "watcher() { local l; read -r -u $1 l }\n"
          "zle -F $keep watcher\n")
    live = shell(rc=rc)
    live.idle(1.0)
    asked = live.session._sync_seq
    versions = live.session.version
    live.idle(3.0)
    assert live.session._sync_seq - asked <= 6       # backing off
    assert live.session.version == versions          # and nothing to repaint
    assert live.session._hold is None
    block = live.run("echo fine")
    assert output_text(block) == "fine"


def test_vi_mode_escape_shows_at_once(shell):
    rc = ("bindkey -v\n"
          "zle-keymap-select() { [[ $KEYMAP == vicmd ]] && print -n '\\e[2 q'"
          " || print -n '\\e[6 q' }\n"
          "zle -N zle-keymap-select\n")
    live = shell(rc=rc)
    live.type("hello")
    assert live.pump(live.idle_prompt)
    column = live.session.input.cx
    live.type("\x1b")
    # KEYTIMEOUT (0.4 s) passes, then zsh steps the cursor back and the
    # hook reshapes it; neither may wait for the next key.
    assert live.pump(lambda: live.session.input.cx == column - 1
                     and live.session.input.cursor_shape == 2, 10.0)
