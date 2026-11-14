"""The private directory a session's shell starts from, and the helpers
around it.  No shell runs here."""

from __future__ import annotations

import errno
import os
import stat
import tempfile
from importlib import resources

import pytest

from jupyterminal.shell import (ShellRuntime, decode_field, find_shell,
                                is_zsh, integration)

SHIMS = {
    ".zshenv": "zshenv.zsh",
    ".zprofile": "zprofile.zsh",
    ".zshrc": "zshrc.zsh",
    ".zlogin": "zlogin.zsh",
    "integration.zsh": "integration.zsh",
}


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    """A runtime under a directory of the test's own, removed afterwards."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    made = ShellRuntime()
    yield made
    made.cleanup()


def executable(path, text: str = "#!/bin/sh\n") -> str:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


# -- mark fields -------------------------------------------------------------

def test_fields_are_decoded():
    assert decode_field("nothing to undo") == "nothing to undo"
    assert decode_field("") == ""
    assert decode_field("a%3Bb") == "a;b"
    assert decode_field("a%3bb") == "a;b"            # either case
    assert decode_field("100%25") == "100%"
    assert decode_field("%0Atwo%0a") == "\ntwo\n"
    assert decode_field("%41%42%43") == "ABC"
    assert decode_field("%E9") == "\xe9"


def test_what_is_no_escape_is_left_alone():
    assert decode_field("50% off") == "50% off"
    assert decode_field("%zz %4") == "%zz %4"
    assert decode_field("%414") == "A4"              # two digits, no more


# -- which shell -------------------------------------------------------------

def test_what_counts_as_zsh():
    assert is_zsh("zsh") and is_zsh("/usr/bin/zsh")
    assert is_zsh("-zsh")                            # a login shell's name
    assert not is_zsh("/bin/bash") and not is_zsh("/bin/zsh5")
    assert not is_zsh("/opt/zsh/bin/fish") and not is_zsh("")


def test_the_configured_shell_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELL", executable(tmp_path / "zsh"))
    assert find_shell("/bin/other") == "/bin/other"


def test_the_users_own_zsh_is_preferred_to_any_other(monkeypatch, tmp_path):
    own = executable(tmp_path / "zsh")
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    executable(elsewhere / "zsh")
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setenv("SHELL", own)
    assert find_shell() == own
    assert find_shell("") == own                     # "" is "not configured"


def test_another_login_shell_gives_way_to_a_zsh_that_can_be_found(
        monkeypatch, tmp_path):
    elsewhere = tmp_path / "bin"
    elsewhere.mkdir()
    found = executable(elsewhere / "zsh")
    monkeypatch.setenv("PATH", str(elsewhere))
    monkeypatch.setenv("SHELL", executable(tmp_path / "bash"))
    assert find_shell() == found
    # So does a zsh that cannot be run ...
    broken = tmp_path / "zsh"
    broken.write_text("")
    monkeypatch.setenv("SHELL", str(broken))
    assert find_shell() == found
    # ... and no login shell at all.
    monkeypatch.delenv("SHELL")
    assert find_shell() == found


def test_without_any_zsh_the_login_shell_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))        # nothing in there
    bash = executable(tmp_path / "bash")
    monkeypatch.setenv("SHELL", bash)
    assert find_shell() == bash
    broken = tmp_path / "zsh"
    broken.write_text("")
    monkeypatch.setenv("SHELL", str(broken))
    assert find_shell() == str(broken)               # it is all there is
    monkeypatch.delenv("SHELL")
    assert find_shell() == "/bin/sh"


# -- the directory -----------------------------------------------------------

def test_the_directory_holds_the_shims_and_is_private(runtime, tmp_path):
    assert os.path.dirname(runtime.path) == str(tmp_path)
    assert os.path.basename(runtime.path).startswith("jupyterminal-")
    assert stat.S_IMODE(os.stat(runtime.path).st_mode) == 0o700
    assert sorted(os.listdir(runtime.path)) == sorted([*SHIMS, "sync"])
    packaged = resources.files("jupyterminal.shell") / "zsh"
    for target, name in SHIMS.items():
        with open(os.path.join(runtime.path, target),
                  encoding="utf-8") as handle:
            assert handle.read() == (packaged / name).read_text("utf-8")
    assert runtime.sync_path == os.path.join(runtime.path, "sync")
    mode = os.stat(runtime.sync_path).st_mode
    assert stat.S_ISFIFO(mode) and stat.S_IMODE(mode) == 0o600
    assert runtime.buffer_path == os.path.join(runtime.path, "buffer")


def test_every_runtime_has_a_directory_and_a_nonce_of_its_own(runtime):
    other = ShellRuntime()
    try:
        assert other.path != runtime.path and other.nonce != runtime.nonce
        for nonce in (runtime.nonce, other.nonce):
            assert len(nonce) == 24 and int(nonce, 16) >= 0
    finally:
        other.cleanup()


def temporary_home_of_a_runtime() -> str:
    """Where a new runtime puts its directory (which is removed again)."""
    made = ShellRuntime()
    try:
        assert os.path.isdir(made.path)
        return os.path.dirname(made.path)
    finally:
        made.cleanup()


def test_without_a_usable_runtime_directory_the_temporary_one_serves(
        monkeypatch):
    for unusable in ("", "/nonexistent/runtime/dir"):
        monkeypatch.setenv("XDG_RUNTIME_DIR", unusable)
        assert temporary_home_of_a_runtime() == tempfile.gettempdir()
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert temporary_home_of_a_runtime() == tempfile.gettempdir()


def test_cleaning_up_removes_everything_and_may_be_repeated(runtime):
    reader = os.open(runtime.sync_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert runtime.request_sync(1)
        held = runtime._sync_fd
        assert held >= 0
        runtime.cleanup()
        assert not os.path.exists(runtime.path)
        assert runtime._sync_fd == -1
        with pytest.raises(OSError) as caught:
            os.fstat(held)                           # it was closed
        assert caught.value.errno == errno.EBADF
        runtime.cleanup()
    finally:
        os.close(reader)


# -- the environment ---------------------------------------------------------

def test_the_environment_points_the_shell_at_the_shims(runtime):
    base = {"HOME": "/home/me", "TERM": "dumb", "COLUMNS": "80",
            "LINES": "24", "PROMPT_EOL_MARK": "%", "POSTEDIT": "x",
            "JUPYTERMINAL_USER_ZDOTDIR": "/stale"}
    given = dict(base)
    assert runtime.environment(given) == {
        "HOME": "/home/me",
        "ZDOTDIR": runtime.path,
        "JUPYTERMINAL_DIR": runtime.path,
        "JUPYTERMINAL_NONCE": runtime.nonce,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "TERM_PROGRAM": "jupyterminal",
    }
    assert given == base                             # a copy was changed


def test_the_users_own_zdotdir_is_passed_on_to_the_shims(runtime):
    env = runtime.environment({"ZDOTDIR": "/home/me/.zsh"})
    assert env["JUPYTERMINAL_USER_ZDOTDIR"] == "/home/me/.zsh"
    assert env["ZDOTDIR"] == runtime.path


def test_without_a_base_the_process_environment_is_used(runtime,
                                                        monkeypatch):
    monkeypatch.setenv("JT_TEST_MARKER", "from the process")
    monkeypatch.setenv("COLUMNS", "80")
    env = runtime.environment()
    assert env["JT_TEST_MARKER"] == "from the process"
    assert "COLUMNS" not in env and env["ZDOTDIR"] == runtime.path
    assert os.environ["COLUMNS"] == "80"             # only the copy changed
    assert runtime.environment({}) == {
        "ZDOTDIR": runtime.path, "JUPYTERMINAL_DIR": runtime.path,
        "JUPYTERMINAL_NONCE": runtime.nonce, "TERM": "xterm-256color",
        "COLORTERM": "truecolor", "TERM_PROGRAM": "jupyterminal"}


# -- asking the line editor --------------------------------------------------

def test_a_question_reaches_whoever_reads_the_fifo(runtime):
    reader = os.open(runtime.sync_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert runtime.request_sync(7) is True
        assert os.read(reader, 100) == b"7\n"
        first = runtime._sync_fd
        assert runtime.request_sync(12) is True
        assert os.read(reader, 100) == b"12\n"
        assert runtime._sync_fd == first             # opened only once
    finally:
        os.close(reader)


def test_no_question_is_asked_before_the_shell_listens(runtime):
    assert runtime.request_sync(1) is False
    assert runtime._sync_fd == -1
    reader = os.open(runtime.sync_path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert runtime.request_sync(2) is True       # now it does
        assert os.read(reader, 100) == b"2\n"
    finally:
        os.close(reader)


def test_a_question_nobody_can_take_any_more_is_not_asked(runtime):
    reader = os.open(runtime.sync_path, os.O_RDONLY | os.O_NONBLOCK)
    assert runtime.request_sync(1) is True
    os.close(reader)                                 # the shell has gone
    assert runtime.request_sync(2) is False


def test_without_a_fifo_no_question_is_asked(tmp_path, monkeypatch):
    def refuse(path, mode):
        raise OSError(errno.EPERM, "no fifos here")

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(os, "mkfifo", refuse)
    made = ShellRuntime()
    monkeypatch.undo()
    try:
        assert made.sync_path == ""
        assert sorted(os.listdir(made.path)) == sorted(SHIMS)
        monkeypatch.setattr(os, "open", None)        # nothing to open, even
        assert made.request_sync(1) is False
        monkeypatch.undo()
    finally:
        made.cleanup()


# -- handing a command line over ---------------------------------------------

def test_the_buffer_file_holds_the_text_and_is_private(runtime):
    runtime.write_buffer("echo 'a long first version'\n# caf\xe9")
    with open(runtime.buffer_path, encoding="utf-8") as handle:
        assert handle.read() == "echo 'a long first version'\n# caf\xe9"
    assert stat.S_IMODE(os.stat(runtime.buffer_path).st_mode) == 0o600
    runtime.write_buffer("short")                    # replaces, whole
    with open(runtime.buffer_path, encoding="utf-8") as handle:
        assert handle.read() == "short"


def test_the_key_that_takes_the_buffer():
    assert integration.SET_BUFFER_KEY == b"\x1b[7717;1~"
