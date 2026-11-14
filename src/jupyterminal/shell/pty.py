"""A child process on a pseudo-terminal."""

from __future__ import annotations

import errno
import fcntl
import os
import signal
import struct
import termios
import time
import warnings

#: Signals the program must find the way a login would have left them.
_DEFAULT_SIGNALS = ("SIGPIPE", "SIGXFSZ", "SIGINT", "SIGQUIT", "SIGTERM",
                    "SIGHUP", "SIGCHLD", "SIGTSTP", "SIGTTIN", "SIGTTOU",
                    "SIGWINCH")


def _utf8_flag(module=termios) -> int:
    """The input flag that tells the terminal driver its input is UTF-8.

    Without it, erasing a character in a program that reads whole lines
    takes away one byte of it.  Python names the flag from 3.13 on; its
    number on Linux is the same on every machine.
    """
    return getattr(module, "IUTF8", 0o40000)


IUTF8 = _utf8_flag()


def _descriptor_limit() -> int:
    """One more than the highest descriptor this process holds.

    Read from /proc: trying every number up to the limit on open files
    instead takes a noticeable part of a second where that limit is high.
    """
    try:
        return max(map(int, os.listdir("/proc/self/fd"))) + 1
    except OSError:
        return 1 << 20


def _fork() -> tuple[int, int]:
    """Fork; the process that did it, and what the fork returned there.

    In one piece, so that there is no fork without the means to tell the
    two processes apart afterwards.
    """
    parent = os.getpid()
    with warnings.catch_warnings():
        # Forking with other threads alive is fine here: the child only
        # makes system calls before it execs.
        warnings.simplefilter("ignore", DeprecationWarning)
        return parent, os.fork()


def _become(parent: int, master: int, slave: int, argv: list[str],
            env: dict[str, str], cwd: str | None) -> None:
    """The child's side of the fork: turn into ``argv`` on the terminal.

    Comes back only when the program could not be started.  ``parent`` is
    the process that forked, which must never get here itself.
    """
    if os.getpid() == parent:
        raise RuntimeError("only the forked child may become the program")
    os.close(master)
    os.login_tty(slave)
    os.closerange(3, _descriptor_limit())
    # Whatever started this process may have blocked or ignored signals;
    # the program must not inherit that.
    signal.pthread_sigmask(signal.SIG_SETMASK, ())
    for name in _DEFAULT_SIGNALS:
        signal.signal(getattr(signal, name), signal.SIG_DFL)
    if cwd:
        try:
            os.chdir(cwd)
        except OSError:
            pass
    env = dict(env)
    env["JUPYTERMINAL_SHELL_PID"] = str(os.getpid())
    os.execvpe(argv[0], argv, env)


class PtyProcess:
    """Runs ``argv`` on a new pseudo-terminal.

    The master side is non-blocking; :meth:`read` and :meth:`write` never
    wait.
    """

    def __init__(self, argv: list[str], env: dict[str, str], cwd: str | None,
                 cols: int, rows: int) -> None:
        master, slave = os.openpty()
        self._set_size(master, cols, rows)
        try:
            attrs = termios.tcgetattr(slave)
            attrs[0] |= IUTF8
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
        except termios.error:
            pass
        parent, pid = _fork()
        if pid == 0:
            try:
                _become(parent, master, slave, argv, env, cwd)
            except BaseException:
                pass
            os._exit(127)
        if os.getpid() != parent:
            # Whatever went wrong: a forked process must on no account
            # carry on as a second copy of this one.
            os._exit(127)
        os.close(slave)
        os.set_blocking(master, False)
        self.pid = pid
        self.fd = master
        self.exit_status: int | None = None
        self._outbox = bytearray()

    @staticmethod
    def _set_size(fd: int, cols: int, rows: int,
                  width: int = 0, height: int = 0) -> None:
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, width, height))
        except OSError:
            pass

    def resize(self, cols: int, rows: int, width: int = 0,
               height: int = 0) -> None:
        if self.fd != -1:
            self._set_size(self.fd, cols, rows, width, height)

    def read(self, size: int = 65536) -> bytes | None:
        """Read available output.

        Returns ``None`` when nothing is available right now and ``b""``
        when the terminal has been closed by the other side.
        """
        if self.fd == -1:
            return b""
        try:
            return os.read(self.fd, size)
        except BlockingIOError:
            return None
        except OSError as error:
            if error.errno == errno.EIO:
                return b""
            raise

    def write(self, data: bytes) -> None:
        """Queue input for the program and send as much as possible."""
        self._outbox += data
        self.flush()

    def flush(self) -> bool:
        """Send queued input; True when some is still waiting."""
        outbox = self._outbox
        while outbox:
            try:
                sent = os.write(self.fd, outbox)
            except BlockingIOError:
                break
            except OSError:
                sent = len(outbox)       # nobody will ever take it
            del outbox[:sent]
        return bool(outbox)

    def poll(self) -> int | None:
        """The child's exit status, or ``None`` while it runs."""
        if self.exit_status is None:
            try:
                pid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                self.exit_status = 0
            else:
                if pid == self.pid:
                    self.exit_status = os.waitstatus_to_exitcode(status)
        return self.exit_status

    def close(self) -> None:
        """Hang up: close the terminal and let the child go."""
        if self.fd != -1:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = -1
        # The terminal reports "closed" a moment before a child that is
        # exiting on its own can be reaped; give it that moment.
        self._wait(0.05)
        if self.exit_status is None:
            try:
                os.killpg(self.pid, signal.SIGHUP)
            except OSError:
                pass
            self._wait(0.25)

    def _wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(0.004)
