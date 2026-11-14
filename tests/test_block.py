"""The block: its state, its output, and the git branch of a directory."""

from __future__ import annotations

import os

import pytest

from jupyterminal.session import block as block_module
from jupyterminal.session.block import (BACKGROUND, COMMAND, FINISHED,
                                        RUNNING, Block, git_branch)
from jupyterminal.term import LineStore, Screen, Terminal


class Clock:
    """Stands in for the ``time`` module inside ``block.py``."""

    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def time(self) -> float:
        return 1_700_000_000.0


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(block_module, "time", clock)
    return clock


def feed(block: Block, text: str) -> None:
    Terminal(block.screen).feed(text.encode())


# -- state --------------------------------------------------------------------

def test_the_names_of_kinds_and_states():
    assert (COMMAND, BACKGROUND) == ("command", "background")
    assert (RUNNING, FINISHED) == ("running", "finished")


def test_a_new_command_block(clock):
    block = Block(COMMAND, 20, 5, "ls -l", "/work", "main")
    assert block.id == 0
    assert (block.kind, block.command, block.cwd, block.branch) == \
        ("command", "ls -l", "/work", "main")
    assert block.label == ""
    assert block.started == 1_700_000_000.0
    assert block.duration is None and block.exit_code is None
    assert block.state == "running"
    assert block.running and block.is_command and not block.failed
    assert (block.not_run, block.fullscreen, block.cleared,
            block.collapsed) == (False, False, False, False)
    assert (block.filter, block.visible, block.find, block.view) == \
        (None, None, None, None)
    assert type(block.store) is LineStore
    assert type(block.screen) is Screen
    assert (block.screen.cols, block.screen.rows) == (20, 5)
    assert block.screen.store is block.store


def test_a_new_background_block():
    block = Block(BACKGROUND, 20, 5)
    assert block.label == "background output"
    assert (block.command, block.cwd, block.branch) == ("", "", "")
    assert block.running and not block.is_command and not block.failed


def test_elapsed_time_runs_until_the_block_finishes(clock):
    block = Block(COMMAND, 20, 5)
    assert block.elapsed() == 0.0
    clock.now = 103.5
    assert block.elapsed() == 3.5
    clock.now = 104.25
    block.finish(0)
    assert block.duration == 4.25
    clock.now = 500.0
    assert block.elapsed() == 4.25


def test_finishing_freezes_the_output(clock):
    block = Block(COMMAND, 20, 5, "printf")
    feed(block, "one\r\ntwo")
    assert block.lines() == ["one", "two"]
    assert block.store.texts == []
    clock.now = 101.0
    block.finish(7)
    assert (block.state, block.exit_code, block.duration) == \
        ("finished", 7, 1.0)
    assert not block.running
    assert block.screen is None
    assert block.store.texts == ["one", "two"]
    assert block.lines() == ["one", "two"]


def test_finishing_twice_changes_nothing(clock):
    block = Block(COMMAND, 20, 5)
    feed(block, "kept\r\n")
    clock.now = 102.0
    block.finish(3)
    clock.now = 110.0
    block.finish(0)
    assert (block.exit_code, block.duration) == (3, 2.0)
    assert block.lines() == ["kept"]


def test_only_a_finished_command_with_a_nonzero_status_has_failed():
    for status, failed in [(0, False), (None, False), (1, True),
                           (130, True), (-1, True)]:
        block = Block(COMMAND, 20, 5)
        block.exit_code = status
        assert not block.failed              # it is still running
        block.exit_code = None
        block.finish(status)
        assert block.failed is failed, status


# -- output -------------------------------------------------------------------

def test_a_fresh_block_has_no_output():
    block = Block(COMMAND, 20, 5)
    assert block.has_output() is False
    assert block.lines() == [] and block.line_count() == 0
    feed(block, "   \r\n  ")
    assert block.has_output() is False       # blanks are not output


def test_a_block_that_finished_without_output_has_none():
    block = Block(COMMAND, 20, 5)
    block.finish(0)
    assert block.has_output() is False
    assert block.lines() == [] and block.line_count() == 0


def test_output_on_the_screen_counts():
    block = Block(COMMAND, 20, 5)
    feed(block, "\r\n\r\n x")
    assert block.has_output() is True
    assert block.lines() == ["", "", " x"]
    assert block.line_count() == 3


def test_output_that_scrolled_off_counts():
    block = Block(COMMAND, 20, 2)
    feed(block, "a\r\nb\r\nc\r\n\x1b[2J")
    assert block.store.texts == ["a", "b"]
    assert all(row.is_blank() for row in block.screen.grid)
    assert block.has_output() is True
    # The erased row above the cursor is an empty line of its own.
    assert block.lines() == ["a", "b", ""]
    assert block.line_count() == 3


def test_the_first_part_of_a_line_that_scrolled_off_counts():
    block = Block(COMMAND, 5, 2)
    feed(block, "abcdefghijkl\x1b[2J")
    assert block.store.texts == []
    assert block.store.open_parts == ["abcde"]
    assert all(row.is_blank() for row in block.screen.grid)
    assert block.has_output() is True


def test_lines_of_a_finished_block_come_from_its_store():
    block = Block(COMMAND, 10, 3)
    feed(block, "0123456789abcde\r\nx\r\ny\r\nz\r\n")
    running = block.lines()
    assert running == ["0123456789abcde", "x", "y", "z"]
    assert block.line_count() == 4
    block.finish(0)
    assert block.lines() == running
    assert block.lines() is block.store.texts
    assert block.line_count() == 4


# -- the git branch -----------------------------------------------------------

def make_repo(root, head: str = "ref: refs/heads/main\n") -> None:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text(head, encoding="utf-8")


def test_no_path_is_in_no_repository(tmp_path, monkeypatch):
    make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)              # not even the one we are in
    assert git_branch("") == ""
    assert git_branch(".") == "main"


def test_the_branch_of_a_repository(tmp_path):
    make_repo(tmp_path)
    assert git_branch(str(tmp_path)) == "main"


def test_the_branch_is_found_from_a_directory_deep_inside(tmp_path):
    make_repo(tmp_path, "ref: refs/heads/feature/login\n")
    deep = tmp_path / "src" / "pkg" / "sub"
    deep.mkdir(parents=True)
    assert git_branch(str(deep)) == "feature/login"
    assert git_branch(str(tmp_path / "src")) == "feature/login"


def test_a_relative_path_counts_from_the_working_directory(tmp_path,
                                                           monkeypatch):
    make_repo(tmp_path, "ref: refs/heads/rel\n")
    (tmp_path / "a" / "b").mkdir(parents=True)
    monkeypatch.chdir(tmp_path / "a")
    assert git_branch("b") == "rel"
    assert git_branch(".") == "rel"


def test_the_nearest_repository_wins(tmp_path):
    make_repo(tmp_path, "ref: refs/heads/outer\n")
    inner = tmp_path / "vendor" / "lib"
    make_repo(inner, "ref: refs/heads/inner\n")
    (inner / "src").mkdir()
    assert git_branch(str(inner / "src")) == "inner"
    assert git_branch(str(tmp_path / "vendor")) == "outer"


def test_a_detached_head_shows_the_start_of_its_hash(tmp_path):
    make_repo(tmp_path, "0123456789abcdef0123456789abcdef01234567\n")
    assert git_branch(str(tmp_path)) == "0123456"


def test_a_reference_outside_the_branches_is_shown_whole(tmp_path):
    make_repo(tmp_path, "ref: refs/remotes/origin/main\n")
    assert git_branch(str(tmp_path)) == "refs/remotes/origin/main"


def test_the_reference_may_follow_its_colon_directly(tmp_path):
    make_repo(tmp_path, "ref:refs/heads/tight")
    assert git_branch(str(tmp_path)) == "tight"


def test_an_empty_head_names_nothing(tmp_path):
    make_repo(tmp_path, "")
    assert git_branch(str(tmp_path)) == ""


def test_bytes_that_are_not_text_do_not_break_the_reading(tmp_path):
    make_repo(tmp_path)
    (tmp_path / ".git" / "HEAD").write_bytes(b"ref: refs/heads/caf\xe9\n")
    assert git_branch(str(tmp_path)) == "caf\ufffd"


def test_a_worktree_points_at_its_git_directory(tmp_path):
    real = tmp_path / "real" / ".git" / "worktrees" / "wt"
    real.mkdir(parents=True)
    (real / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    tree = tmp_path / "wt"
    (tree / "docs").mkdir(parents=True)
    (tree / ".git").write_text("gitdir: ../real/.git/worktrees/wt\n",
                               encoding="utf-8")
    assert git_branch(str(tree)) == "topic"
    assert git_branch(str(tree / "docs")) == "topic"
    # The pointer may be absolute, and may follow its colon directly.
    (tree / ".git").write_text(f"gitdir:{real}", encoding="utf-8")
    assert git_branch(str(tree)) == "topic"


def test_a_git_file_that_points_nowhere_names_nothing(tmp_path):
    make_repo(tmp_path, "ref: refs/heads/outer\n")
    tree = tmp_path / "odd"
    tree.mkdir()
    (tree / ".git").write_text("not a pointer\n", encoding="utf-8")
    assert git_branch(str(tree)) == ""
    (tree / ".git").write_text("gitdir: missing\n", encoding="utf-8")
    assert git_branch(str(tree)) == ""
    (tree / ".git").write_bytes(b"gitdir: caf\xe9\n")
    assert git_branch(str(tree)) == ""
    # Seven characters of something else do not make it a pointer.
    (tree / "real").mkdir()
    (tree / "real" / "HEAD").write_text("ref: refs/heads/trap\n",
                                        encoding="utf-8")
    (tree / ".git").write_text("gitdir:real\n", encoding="utf-8")
    assert git_branch(str(tree)) == "trap"
    (tree / ".git").write_text("xxxxxxxreal\n", encoding="utf-8")
    assert git_branch(str(tree)) == ""


def test_a_repository_without_a_readable_head_names_nothing(tmp_path):
    (tmp_path / ".git").mkdir()
    assert git_branch(str(tmp_path)) == ""
    (tmp_path / ".git" / "HEAD").mkdir()
    assert git_branch(str(tmp_path)) == ""


def test_outside_every_repository_there_is_no_branch():
    assert not os.path.exists("/.git")
    assert git_branch("/") == ""
    assert git_branch("/no-such-directory-jt/one/two") == ""
