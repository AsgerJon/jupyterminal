"""Whatever a program prints, the terminal must take it without failing
and keep its own structures sound."""

from __future__ import annotations

import random

import pytest

from jupyterminal.session import Session
from jupyterminal.term import LineStore, Screen, Terminal
from conftest import COLS, NONCE, mark, prompt, submit

FINALS = "@ABCDEFGHIJKLMPSTXZ`abcdefghlmnrstu"
TEXT = ["hello", "wörld", "日本語", "e\u0301", "😀", "\t", " ", "x" * 50,
        "a b c", "~", "┌─┐"]


def random_stream(rng: random.Random, pieces: int) -> bytes:
    out: list[str] = []
    for _ in range(pieces):
        kind = rng.random()
        if kind < 0.35:
            out.append(rng.choice(TEXT))
        elif kind < 0.50:
            out.append(rng.choice(["\r", "\n", "\r\n", "\b", "\x07", "\x0e",
                                   "\x0f", "\x00", "\x7f", "\x0b"]))
        elif kind < 0.80:
            count = rng.randint(0, 3)
            params = ";".join(str(rng.choice([0, 1, 2, 5, 9, 40, 999]))
                              if rng.random() < 0.85 else ""
                              for _ in range(count))
            prefix = rng.choice(["", "", "", "?", ">"])
            middle = rng.choice(["", "", "", " ", "$", "!"])
            out.append(f"\x1b[{prefix}{params}{middle}{rng.choice(FINALS)}")
        elif kind < 0.86:
            mode = rng.choice([1, 6, 7, 25, 47, 1047, 1049, 1000, 1006,
                               2004, 4, 20])
            out.append(f"\x1b[?{mode}{rng.choice('hl')}")
        elif kind < 0.90:
            out.append(f"\x1b[{rng.randint(0, 107)};{rng.randint(0, 255)}m")
        elif kind < 0.94:
            out.append(rng.choice(["\x1b]0;title\x07", "\x1b]11;?\x1b\\",
                                   "\x1b]52;c;aGk=\x07", "\x1bP$qm\x1b\\",
                                   "\x1b]7717;bad;C;x\x07",
                                   "\x1b]unfinished"]))
        else:
            out.append("\x1b" + rng.choice("78DEHMc=>()#Z\\ %") +
                       rng.choice(["", "0", "B", "8"]))
    data = "".join(out).encode("utf-8")
    if rng.random() < 0.3:
        # Raw garbage, including invalid UTF-8.
        junk = bytes(rng.randrange(256) for _ in range(rng.randint(1, 40)))
        cut = rng.randrange(len(data) + 1)
        data = data[:cut] + junk + data[cut:]
    return data


def check(screen: Screen) -> None:
    assert 0 <= screen.cy < len(screen.grid) <= screen.rows
    assert 0 <= screen.cx < screen.cols
    assert 0 <= screen.top <= screen.bot < screen.rows
    for row in screen.grid:
        assert len(row.cells) <= screen.cols
        if row.styles is not None:
            assert len(row.styles) == len(row.cells)
        if row.wrapped:
            assert len(row.cells) == screen.cols     # wrapped rows are full
    lines = screen.all_lines()
    assert screen.line_count() == len(lines)   # counted without building
    for text, runs in lines:
        assert isinstance(text, str)
        if runs:
            ends = [end for end, _ in runs]
            assert ends == sorted(ends) and ends[-1] <= len(text)


@pytest.mark.parametrize("seed", range(40))
def test_random_streams_leave_the_screen_sound(seed):
    rng = random.Random(seed)
    cols, rows = rng.choice([(1, 1), (2, 2), (7, 3), (20, 5), (80, 24)])
    store = LineStore()
    screen = Screen(cols, rows, store)
    term = Terminal(screen, lambda data: None)
    for _ in range(30):
        data = random_stream(rng, rng.randint(1, 60))
        cut = rng.randrange(len(data) + 1)
        term.feed(data[:cut])
        term.feed(data[cut:])
        check(term.screen)
        check(term.main_screen)
        if rng.random() < 0.2:
            cols, rows = rng.randint(1, 40), rng.randint(1, 12)
            term.resize(cols, rows)
            check(term.screen)
            check(term.main_screen)
    term.set_screen(screen)
    screen.finish()
    assert all(isinstance(text, str) for text in store.texts)


@pytest.mark.parametrize("seed", range(15))
def test_random_streams_leave_the_session_sound(seed):
    """The same, through the block router, with marks at random points."""
    rng = random.Random(1000 + seed)
    session = Session(COLS, 8, nonce=NONCE)
    events = [prompt, lambda: submit("cmd"), lambda: mark("T"),
              lambda: mark("A"), lambda: mark("I"), lambda: mark("F", "x"),
              lambda: prompt(1), lambda: mark("P", "0", "off", "/w"),
              lambda: mark("S", "1", "1")]
    for _ in range(120):
        if rng.random() < 0.45:
            session.feed(rng.choice(events)())
        else:
            session.feed(random_stream(rng, rng.randint(1, 25)))
        if rng.random() < 0.1:
            session.resize(rng.randint(2, 50), rng.randint(2, 12))
        for block in session.blocks:
            assert isinstance(block.lines(), list)
            if block.screen is not None:
                check(block.screen)
        check(session.input)
        ids = [b.id for b in session.blocks]
        assert ids == sorted(set(ids))
    session.close()
    assert all(not b.running for b in session.blocks)
