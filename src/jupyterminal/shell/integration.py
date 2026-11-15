"""Shell integration: the private directory a session's shell starts from.

zsh is pointed at this directory through ``ZDOTDIR``.  The startup shims in
it run the user's own configuration unchanged and then load the script that
reports command boundaries (see ``zsh/integration.zsh``).
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import tempfile
from importlib import resources

_ESCAPED = re.compile("%([0-9A-Fa-f]{2})")

_SHIMS = {
    ".zshenv": "zshenv.zsh",
    ".zprofile": "zprofile.zsh",
    ".zshrc": "zshrc.zsh",
    ".zlogin": "zlogin.zsh",
    "integration.zsh": "integration.zsh",
}

#: Key sequence bound, in the shell, to "take the buffer file as input".
SET_BUFFER_KEY = b"\x1b[7717;1~"
#: ... to "do what the edit file asks for, and answer with the result".
EDIT_KEY = b"\x1b[7717;2~"
#: ... to "say what the plugins make of the text in the look file".
LOOK_KEY = b"\x1b[7717;3~"


def decode_field(text: str) -> str:
    """Undo the ``%XX`` escaping the shell applies to mark fields."""
    return _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), text)


def is_zsh(shell: str) -> bool:
    return os.path.basename(shell).lstrip("-") == "zsh"


def find_shell(preferred: str | None = None) -> str:
    """The shell to run: the configured one, else zsh if it can be found."""
    if preferred:
        return preferred
    login = os.environ.get("SHELL", "")
    if login and is_zsh(login) and os.access(login, os.X_OK):
        return login
    found = shutil.which("zsh")
    if found:
        return found
    return login or "/bin/sh"


class ShellRuntime:
    """The per-session directory holding shims, the sync fifo and the
    buffer hand-over file."""

    def __init__(self) -> None:
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base or not os.access(base, os.W_OK):
            base = None
        self.path = tempfile.mkdtemp(prefix="jupyterminal-", dir=base)
        self.nonce = secrets.token_hex(12)
        source = resources.files("jupyterminal.shell") / "zsh"
        for target, name in _SHIMS.items():
            text = (source / name).read_text(encoding="utf-8")
            with open(os.path.join(self.path, target), "w",
                      encoding="utf-8") as handle:
                handle.write(text)
        self.sync_path = os.path.join(self.path, "sync")
        try:
            os.mkfifo(self.sync_path, 0o600)
        except OSError:
            self.sync_path = ""
        self.buffer_path = os.path.join(self.path, "buffer")
        self.edit_path = os.path.join(self.path, "edit")
        self.look_path = os.path.join(self.path, "look")
        self._sync_fd = -1

    def environment(self, base: dict[str, str] | None = None
                    ) -> dict[str, str]:
        """The environment for the shell process."""
        env = dict(os.environ if base is None else base)
        for name in ("COLUMNS", "LINES", "PROMPT_EOL_MARK", "POSTEDIT"):
            env.pop(name, None)
        if "ZDOTDIR" in env:
            env["JUPYTERMINAL_USER_ZDOTDIR"] = env["ZDOTDIR"]
        else:
            env.pop("JUPYTERMINAL_USER_ZDOTDIR", None)
        env["ZDOTDIR"] = self.path
        env["JUPYTERMINAL_DIR"] = self.path
        env["JUPYTERMINAL_NONCE"] = self.nonce
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        env["TERM_PROGRAM"] = "jupyterminal"
        return env

    def request_sync(self, seq: int) -> bool:
        """Ask the shell's line editor to report when it is idle."""
        if not self.sync_path:
            return False
        if self._sync_fd == -1:
            try:
                self._sync_fd = os.open(
                    self.sync_path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                return False  # the shell has not opened its end yet
        try:
            os.write(self._sync_fd, b"%d\n" % seq)
        except OSError:
            return False
        return True

    def write_buffer(self, text: str) -> None:
        """Leave ``text`` for the shell's set-buffer widget."""
        fd = os.open(self.buffer_path,
                     os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)

    def write_edit(self, text: str, cursor: int, key: bytes,
                   fallback: str, edited: bool) -> None:
        """Leave a request for the shell's edit widget: do on ``text``,
        with the cursor in front of its character number ``cursor``, what
        ``key`` does in the shell, or what the widget ``fallback`` does
        where that key does nothing of use.  ``edited`` says that the
        text was edited since the widget last answered: this key does not
        follow the one before it, then, even if the text is the same."""
        self._request(self.edit_path, text, cursor, key, fallback.encode(),
                      edited, b"")

    def write_look(self, text: str, cursor: int, number: int) -> None:
        """Leave request ``number`` for the shell's look widget: say what
        the plugins make of ``text``."""
        self._request(self.look_path, text, cursor, b"", b"", True,
                      b"%d" % number)

    @staticmethod
    def _request(path: str, text: str, cursor: int, key: bytes,
                 fallback: bytes, edited: bool, number: bytes) -> None:
        # A full stop ends the text: reading a file, the shell would drop
        # the newlines at its end.
        data = b"\n".join((b"%d" % cursor, key, fallback,
                           b"1" if edited else b"", number,
                           text.encode("utf-8", "replace") + b"."))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)

    def cleanup(self) -> None:
        if self._sync_fd != -1:
            os.close(self._sync_fd)
            self._sync_fd = -1
        shutil.rmtree(self.path, ignore_errors=True)
