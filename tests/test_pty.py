"""A child process on a pseudo-terminal: small quick children, no shell
integration involved."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import select
import signal
import struct
import termios
import threading
import time
import warnings

import pytest

from jupyterminal.shell import pty as pty_module
from jupyterminal.shell.pty import PtyProcess

ENV = {"PATH": os.environ["PATH"]}


@pytest.fixture
def spawn():
    """Factory for children; whatever still runs afterwards is killed."""
    made: list[PtyProcess] = []

    def factory(*argv: str, env: dict | None = None, cwd: str | None = None,
                cols: int = 80, rows: int = 24) -> PtyProcess:
        proc = PtyProcess(list(argv), dict(ENV, **(env or {})), cwd,
                          cols, rows)
        made.append(proc)
        return proc

    yield factory
    for proc in made:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.close()


def read_until(proc: PtyProcess, token: bytes, timeout: float = 10.0) -> bytes:
    """Collect output until ``token`` has arrived."""
    got = b""
    deadline = time.monotonic() + timeout
    while token not in got:
        assert time.monotonic() < deadline, got
        select.select([proc.fd], [], [], 0.05)
        got += proc.read() or b""
    return got


def read_to_end(proc: PtyProcess, timeout: float = 10.0) -> bytes:
    """Collect output until the terminal reports that the child is gone."""
    got = b""
    deadline = time.monotonic() + timeout
    while True:
        assert time.monotonic() < deadline, got
        select.select([proc.fd], [], [], 0.05)
        data = proc.read()
        if data == b"":
            return got
        got += data or b""


def window_size(fd: int) -> tuple[int, int, int, int]:
    """``(rows, cols, width, height)`` as the terminal driver holds them."""
    packed = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
    return struct.unpack("HHHH", packed)


# -- waiting -----------------------------------------------------------------

class SteppingTime:
    """Stands in for the ``time`` module: every sleep takes half a second,
    and nothing else takes any time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.asked = 0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        self.asked += 1
        assert self.asked < 1000             # time must not stand still
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += 0.5


def test_waiting_looks_again_every_few_milliseconds(spawn, monkeypatch):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    clock = SteppingTime()
    monkeypatch.setattr(pty_module, "time", clock)
    proc._wait(1.0)                          # up to its deadline, not past
    assert clock.slept == [0.004, 0.004] and clock.now == 1.0
    proc._wait(0.75)
    assert clock.slept == [0.004] * 4


def test_waiting_ends_as_soon_as_the_child_has(spawn, monkeypatch):
    proc = spawn("true")
    proc._wait(10.0)
    assert proc.exit_status == 0
    clock = SteppingTime()
    monkeypatch.setattr(pty_module, "time", clock)
    proc._wait(10.0)
    assert clock.slept == []


# -- starting ----------------------------------------------------------------

def test_the_utf8_flag_has_its_number_on_every_python():
    """termios names the flag from Python 3.13 on.  Before that the number
    has to do, and where Python knows it, it is the same number."""
    class Old:
        pass

    class New:
        IUTF8 = 0o123

    assert pty_module._utf8_flag(Old) == 0o40000
    assert pty_module._utf8_flag(New) == 0o123
    assert pty_module.IUTF8 == 0o40000
    assert getattr(termios, "IUTF8", 0o40000) == 0o40000


def test_the_child_runs_on_a_terminal_of_the_given_size(spawn):
    proc = spawn("/bin/sh", "-c", "stty size && tty", cols=132, rows=43)
    lines = read_to_end(proc).split(b"\r\n")
    assert lines[0] == b"43 132"
    assert lines[1].startswith(b"/dev/pts/") and lines[2:] == [b""]
    proc._wait(10.0)
    assert proc.exit_status == 0


def test_the_terminal_controls_the_child_and_takes_utf8_input(spawn):
    proc = spawn("/bin/sh", "-c",
                 "echo to-the-terminal > /dev/tty; "
                 "echo $JUPYTERMINAL_SHELL_PID; ps -o sid= -p $$")
    assert termios.tcgetattr(proc.fd)[0] & 0o40000       # IUTF8
    lines = read_to_end(proc).split(b"\r\n")
    # /dev/tty is this terminal, the child knows its own process id, and
    # it leads a session of its own.
    assert lines[0] == b"to-the-terminal"
    assert lines[1] == str(proc.pid).encode()
    assert int(lines[2]) == proc.pid


def test_a_terminal_that_refuses_its_settings_still_gets_the_child(
        spawn, monkeypatch):
    def refuse(fd):
        raise termios.error(errno.EIO, "refused")

    monkeypatch.setattr(termios, "tcgetattr", refuse)
    proc = spawn("/bin/sh", "-c", "echo still-started")
    monkeypatch.undo()
    assert read_to_end(proc) == b"still-started\r\n"
    assert not termios.tcgetattr(proc.fd)[0] & 0o40000   # IUTF8


def test_the_child_starts_in_the_given_directory(spawn, tmp_path):
    here = os.path.realpath(os.getcwd()).encode() + b"\r\n"
    there = os.path.realpath(tmp_path)
    assert read_to_end(spawn("pwd", "-P", cwd=there)) \
        == there.encode() + b"\r\n"
    assert read_to_end(spawn("pwd", "-P")) == here
    # A directory that is gone is no reason not to start.
    missing = os.path.join(there, "missing")
    assert read_to_end(spawn("pwd", "-P", cwd=missing)) == here


def test_the_child_gets_exactly_the_environment_given(spawn):
    proc = spawn("/bin/sh", "-c", "echo $MARKER:${HOME-unset}",
                 env={"MARKER": "m1"})
    assert read_to_end(proc) == b"m1:unset\r\n"


def test_a_program_that_cannot_be_started_ends_with_127(spawn):
    proc = spawn("/nonexistent/program")
    assert read_to_end(proc) == b""
    proc._wait(10.0)
    assert proc.exit_status == 127


def test_the_child_starts_with_no_signal_ignored_or_blocked(spawn):
    """Python ignores SIGPIPE and SIGXFSZ, and whatever started the tests
    may have blocked more: none of that may reach the child."""
    names = ("SIGPIPE", "SIGXFSZ", "SIGINT", "SIGQUIT", "SIGTERM", "SIGHUP",
             "SIGCHLD", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGWINCH")
    numbers = [getattr(signal, name) for name in names]
    before = [signal.signal(number, signal.SIG_IGN) for number in numbers]
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR2})
    try:
        proc = spawn("grep", "-E", "SigBlk|SigIgn", "/proc/self/status")
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for number, handler in zip(numbers, before, strict=True):
            signal.signal(number, handler)
    report = dict(line.split(b":\t") for line in
                  read_to_end(proc).split(b"\r\n")[:2])
    assert int(report[b"SigBlk"], 16) == 0
    assert int(report[b"SigIgn"], 16) == 0


def test_no_descriptor_of_ours_reaches_the_child(spawn):
    """Not even one that asks to be inherited."""
    read_end, write_end = os.pipe()
    os.dup2(read_end, 200, inheritable=True)
    try:
        proc = spawn("/bin/sh", "-c", "echo /proc/$$/fd/*")
        listed = read_to_end(proc).split()
    finally:
        for fd in (read_end, write_end, 200):
            os.close(fd)
    assert listed[:3] == [f"/proc/{proc.pid}/fd/{n}".encode() for n in "012"]
    assert f"/proc/{proc.pid}/fd/200".encode() not in listed


def test_a_pipeline_in_the_child_ends_quietly(spawn):
    """With SIGPIPE ignored, as Python leaves it, ``yes`` would complain
    about a broken pipe instead of just ending."""
    proc = spawn("/bin/sh", "-c", "yes | head -n 1; echo done")
    assert read_to_end(proc) == b"y\r\ndone\r\n"


def test_forking_while_other_threads_run_raises_no_warning(spawn):
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait)
    thread.start()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            proc = spawn("true")
    finally:
        stop.set()
        thread.join()
    proc._wait(10.0)
    assert proc.exit_status == 0


# -- the child's side of the fork, with everything it calls replaced ---------

class FakeOs:
    """Notes what the child's side asks of the system instead of doing it."""

    def __init__(self, calls: list, failing: tuple = ()) -> None:
        self.calls = calls
        self.failing = failing

    def note(self, name: str, *args) -> None:
        self.calls.append((name, *args))
        if name in self.failing:
            raise OSError(errno.EPERM, name)

    def close(self, fd) -> None:
        self.note("close", fd)

    def login_tty(self, fd) -> None:
        self.note("login_tty", fd)

    def listdir(self, path) -> list[str]:
        self.note("listdir", path)
        return ["0", "1", "2", "6", "9", "3"]

    def closerange(self, low, high) -> None:
        self.note("closerange", low, high)

    def chdir(self, path) -> None:
        self.note("chdir", path)

    def getpid(self) -> int:
        return 4242

    def execvpe(self, file, argv, env) -> None:
        self.note("execvpe", file, argv, env)


class FakeSignal:
    """Every signal and constant is its own name."""

    def __init__(self, calls: list) -> None:
        self.calls = calls

    def __getattr__(self, name: str) -> str:
        return name

    def pthread_sigmask(self, how, mask) -> None:
        self.calls.append(("pthread_sigmask", how, mask))

    def signal(self, number, handler) -> None:
        self.calls.append(("signal", number, handler))


RESET = [("pthread_sigmask", "SIG_SETMASK", ())] + [
    ("signal", name, "SIG_DFL") for name in (
        "SIGPIPE", "SIGXFSZ", "SIGINT", "SIGQUIT", "SIGTERM", "SIGHUP",
        "SIGCHLD", "SIGTSTP", "SIGTTIN", "SIGTTOU", "SIGWINCH")]


def become(monkeypatch, cwd, failing: tuple = ()) -> tuple[list, dict]:
    """Run the child's side on fakes, as process 4242 forked by process 1;
    what it did, and the caller's environment afterwards."""
    calls: list = []
    monkeypatch.setattr(pty_module, "os", FakeOs(calls, failing))
    monkeypatch.setattr(pty_module, "signal", FakeSignal(calls))
    given = {"A": "1"}
    assert pty_module._become(1, 5, 6, ["zsh", "-l"], given, cwd) is None
    return calls, given


def test_the_childs_side_step_by_step(monkeypatch):
    calls, given = become(monkeypatch, "/home/me")
    assert calls == [
        ("close", 5),
        ("login_tty", 6),
        ("listdir", "/proc/self/fd"),
        ("closerange", 3, 10),               # up to the highest one open
        *RESET,
        ("chdir", "/home/me"),
        ("execvpe", "zsh", ["zsh", "-l"],
         {"A": "1", "JUPYTERMINAL_SHELL_PID": "4242"}),
    ]
    assert given == {"A": "1"}           # the caller's own is left alone


def test_the_childs_side_without_a_directory(monkeypatch):
    calls, _ = become(monkeypatch, None)
    assert [call[0] for call in calls[-2:]] == ["signal", "execvpe"]
    calls, _ = become(monkeypatch, "")
    assert [call[0] for call in calls[-2:]] == ["signal", "execvpe"]


def test_the_childs_side_when_the_directory_cannot_be_entered(monkeypatch):
    calls, _ = become(monkeypatch, "/gone", failing=("chdir",))
    assert calls[-2:] == [
        ("chdir", "/gone"),
        ("execvpe", "zsh", ["zsh", "-l"],
         {"A": "1", "JUPYTERMINAL_SHELL_PID": "4242"}),
    ]


def test_the_childs_side_without_a_list_of_its_descriptors(monkeypatch):
    calls, _ = become(monkeypatch, None, failing=("listdir",))
    assert calls[:4] == [("close", 5), ("login_tty", 6),
                         ("listdir", "/proc/self/fd"),
                         ("closerange", 3, 1 << 20)]     # then all of them
    assert calls[-1][0] == "execvpe"


def test_the_childs_side_gives_up_where_it_cannot_go_on(monkeypatch):
    with pytest.raises(OSError, match="execvpe"):
        become(monkeypatch, "/home/me", failing=("execvpe",))
    calls: list = []
    monkeypatch.setattr(pty_module, "os", FakeOs(calls, ("login_tty",)))
    with pytest.raises(OSError, match="login_tty"):
        pty_module._become(1, 5, 6, ["zsh"], {}, None)
    assert calls == [("close", 5), ("login_tty", 6)]     # and nothing more


def test_the_process_that_forked_never_becomes_the_program(monkeypatch):
    calls: list = []
    monkeypatch.setattr(pty_module, "os", FakeOs(calls))
    with pytest.raises(RuntimeError) as caught:
        pty_module._become(4242, 5, 6, ["zsh"], {}, None)
    assert str(caught.value) \
        == "only the forked child may become the program"
    assert calls == []                       # it touched nothing


class Exited(Exception):
    """Raised in place of ending the process."""


def forked(monkeypatch, pid: int, become, other: int = 1) -> list:
    """Start a child with the fork replaced: ``pid`` is what it returns,
    and afterwards the process finds itself to be another one than before
    (or, with ``other=0``, the same).  The pair of descriptors that was
    opened for the child is returned."""
    opened = []
    openpty = os.openpty
    numbers = iter([os.getpid(), os.getpid() + other])

    def exit_instead(status: int) -> None:
        raise Exited(status)

    monkeypatch.setattr(os, "openpty",
                        lambda: opened.append(openpty()) or opened[-1])
    monkeypatch.setattr(os, "fork", lambda: pid)
    monkeypatch.setattr(os, "getpid", lambda: next(numbers))
    monkeypatch.setattr(os, "_exit", exit_instead)
    monkeypatch.setattr(pty_module, "_become", become)
    try:
        with pytest.raises(Exited) as caught:
            PtyProcess(["prog", "arg"], {"A": "1"}, "/somewhere", 80, 24)
    finally:
        monkeypatch.undo()
    assert caught.value.args == (127,)
    return list(opened[0])


def test_forking_tells_who_forked(monkeypatch):
    monkeypatch.setattr(os, "fork", lambda: 4321)
    assert pty_module._fork() == (os.getpid(), 4321)


def test_the_forked_side_is_handed_the_terminal_and_the_program(monkeypatch):
    seen = []
    here = os.getpid()

    def become_instead(parent, master, slave, argv, env, cwd) -> None:
        seen.append((parent, master, slave, os.get_blocking(master),
                     argv, env, cwd))

    master, slave = forked(monkeypatch, 0, become_instead)
    assert os.ttyname(slave).startswith("/dev/pts/")
    assert seen == [(here, master, slave, True,
                     ["prog", "arg"], {"A": "1"}, "/somewhere")]
    os.close(master)
    os.close(slave)


def test_a_forked_side_that_fails_ends_with_127(monkeypatch):
    def fail(*args) -> None:
        raise KeyboardInterrupt("or anything else")

    master, slave = forked(monkeypatch, 0, fail)
    os.close(master)
    os.close(slave)


def test_the_forked_side_ends_there_whoever_it_seems_to_be(monkeypatch):
    """It does not need to find out that it is another process first."""
    master, slave = forked(monkeypatch, 0, lambda *args: None, other=0)
    os.close(master)
    os.close(slave)


def test_a_fork_gone_wrong_never_carries_on_as_a_second_terminal(monkeypatch):
    """Not the child by the number the fork returned, yet not the process
    that forked either: it must end, not act as a copy of the parent."""
    became = []
    master, slave = forked(monkeypatch, 4321, became.append)
    assert became == []
    os.close(master)                         # both were still open
    os.close(slave)


# -- output ------------------------------------------------------------------

def test_reading_never_waits(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    assert os.get_blocking(proc.fd) is False
    assert read_until(proc, b"ready\r\n") == b"ready\r\n"
    assert proc.read() is None               # nothing more for now


def test_output_ends_with_an_empty_read_once_the_child_is_gone(spawn):
    proc = spawn("/bin/sh", "-c", "printf 'one\\ntwo'")
    assert read_to_end(proc) == b"one\r\ntwo"
    assert proc.read() == b""                # and stays that way
    proc.close()
    assert proc.read() == b""                # nothing to read from at all


def test_reading_reports_any_other_failure(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    os.close(proc.fd)                        # behind its back
    with pytest.raises(OSError) as caught:
        proc.read()
    assert caught.value.errno == errno.EBADF
    proc.fd = -1


def test_reading_takes_at_most_the_size_asked_for(spawn):
    proc = spawn("/bin/sh", "-c", "printf abcdef; exec cat")
    assert read_until(proc, b"abcdef") == b"abcdef"
    proc.write(b"12345\n")
    echoed = b"12345\r\n12345\r\n"           # by the terminal, then by cat
    got = b""
    deadline = time.monotonic() + 10.0
    while len(got) < len(echoed):
        assert time.monotonic() < deadline, got
        select.select([proc.fd], [], [], 0.05)
        piece = proc.read(3) or b""
        assert len(piece) <= 3
        got += piece
    assert got == echoed


# -- input -------------------------------------------------------------------

def test_input_reaches_the_child(spawn):
    proc = spawn("/bin/sh", "-c", "read line; echo got:$line")
    proc.write(b"hello\n")
    assert read_to_end(proc) == b"hello\r\ngot:hello\r\n"
    assert proc._outbox == b""


def test_input_the_child_is_not_taking_yet_waits_its_turn(spawn):
    count = 300_000
    data = bytes(i * 7 % 251 for i in range(count))
    proc = spawn("/bin/sh", "-c", "stty raw -echo; printf R; sleep 0.2; "
                                  f"head -c {count} | md5sum")
    read_until(proc, b"R")
    proc.write(data)
    assert 0 < len(proc._outbox) < count     # the terminal took a part only
    assert proc.flush() is True
    deadline = time.monotonic() + 30.0
    while proc.flush():
        assert time.monotonic() < deadline
        select.select([], [proc.fd], [], 0.05)
    assert proc._outbox == b"" and proc.flush() is False
    digest = hashlib.md5(data).hexdigest().encode()
    assert read_until(proc, b"-\n", 30.0) == digest + b"  -\n"


def test_nothing_is_queued_for_a_closed_terminal_or_from_no_input(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    proc.write(b"")
    assert proc._outbox == b"" and proc.read() is None
    proc.close()
    proc.write(b"too late")
    assert proc._outbox == b"" and proc.flush() is False


def test_input_still_waiting_when_the_terminal_closes_is_dropped(spawn):
    proc = spawn("/bin/sh", "-c", "stty raw -echo; printf R; exec sleep 60")
    read_until(proc, b"R")
    proc.write(bytes(300_000))
    assert proc.flush() is True              # nobody takes it
    proc.close()
    assert proc.flush() is False and proc._outbox == b""


def test_input_for_a_terminal_that_fails_is_dropped(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    os.close(proc.fd)                        # behind its back
    proc.write(b"lost")
    assert proc._outbox == b"" and proc.flush() is False
    proc.fd = -1


# -- size --------------------------------------------------------------------

def test_resizing_tells_the_terminal_driver(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat", cols=132, rows=43)
    read_until(proc, b"ready\r\n")
    assert window_size(proc.fd) == (43, 132, 0, 0)
    proc.resize(100, 30)
    assert window_size(proc.fd) == (30, 100, 0, 0)
    proc.resize(90, 20, 810, 360)
    assert window_size(proc.fd) == (20, 90, 810, 360)
    proc.close()
    proc.resize(10, 5)                       # a closed terminal: no error


def test_a_size_the_descriptor_cannot_take_is_no_error():
    read_end, write_end = os.pipe()
    PtyProcess._set_size(read_end, 80, 24)   # not a terminal
    os.close(read_end)
    os.close(write_end)
    PtyProcess._set_size(read_end, 80, 24)   # not even open


# -- the end -----------------------------------------------------------------

def test_the_exit_status_is_reported_once_the_child_has_ended(spawn):
    proc = spawn("/bin/sh", "-c", "read line; exit 3")
    assert proc.poll() is None and proc.exit_status is None
    proc.write(b"go\n")
    proc._wait(10.0)
    assert proc.poll() == 3 and proc.exit_status == 3


def test_a_child_ended_by_a_signal_reports_it_negated(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    os.kill(proc.pid, signal.SIGKILL)
    proc._wait(10.0)
    assert proc.poll() == -signal.SIGKILL


def test_the_status_is_asked_for_only_until_it_is_known(spawn, monkeypatch):
    proc = spawn("/bin/sh", "-c", "exit 4")
    proc._wait(10.0)
    assert proc.exit_status == 4
    monkeypatch.setattr(os, "waitpid", None)     # must not be called again
    assert proc.poll() == 4


def test_a_child_reaped_by_someone_else_counts_as_ended(spawn):
    proc = spawn("/bin/sh", "-c", "exit 5")
    os.waitpid(proc.pid, 0)
    assert proc.poll() == 0


def test_waiting_gives_up_at_its_deadline(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    began = time.monotonic()
    proc._wait(0.1)
    assert 0.1 <= time.monotonic() - began < 5.0
    assert proc.exit_status is None


def test_closing_hangs_up_and_the_child_ends(spawn, monkeypatch):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    fd = proc.fd
    proc.close()
    assert proc.fd == -1
    assert proc.exit_status == -signal.SIGHUP
    with pytest.raises(OSError) as caught:
        os.fstat(fd)                             # really closed
    assert caught.value.errno == errno.EBADF
    monkeypatch.setattr(os, "close", None)       # closing again does nothing
    monkeypatch.setattr(os, "killpg", None)
    proc.close()
    assert proc.fd == -1 and proc.exit_status == -signal.SIGHUP


def test_a_child_that_has_ended_is_not_signalled(spawn, monkeypatch):
    proc = spawn("true")
    proc._wait(10.0)
    assert proc.exit_status == 0
    monkeypatch.setattr(os, "killpg", None)      # must not be called
    proc.close()
    assert proc.fd == -1 and proc.exit_status == 0


def test_closing_a_terminal_that_is_already_gone(spawn):
    proc = spawn("/bin/sh", "-c", "echo ready; exec cat")
    read_until(proc, b"ready\r\n")
    os.close(proc.fd)                            # behind its back
    proc.close()
    assert proc.fd == -1
    assert proc.exit_status == -signal.SIGHUP


def test_a_child_that_ignores_the_hangup_is_told_once_more(spawn,
                                                           monkeypatch):
    signalled = []
    monkeypatch.setattr(os, "killpg", lambda *args: signalled.append(args))
    proc = spawn("/bin/sh", "-c", "trap '' HUP; echo ready; exec sleep 60")
    read_until(proc, b"ready\r\n")
    began = time.monotonic()
    proc.close()
    waited = time.monotonic() - began
    assert signalled == [(proc.pid, signal.SIGHUP)]
    assert proc.exit_status is None              # it was given its time
    assert 0.29 <= waited < 5.0
    monkeypatch.undo()


def test_a_child_that_may_not_be_signalled_is_left_alone(spawn, monkeypatch):
    def refuse(pid, number):
        raise PermissionError(errno.EPERM, "not yours")

    monkeypatch.setattr(os, "killpg", refuse)
    proc = spawn("/bin/sh", "-c", "trap '' HUP; echo ready; exec sleep 60")
    read_until(proc, b"ready\r\n")
    proc.close()
    assert proc.fd == -1 and proc.exit_status is None
    monkeypatch.undo()
