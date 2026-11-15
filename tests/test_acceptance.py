"""The acceptance criteria of the functional spec, one test each.

Every test drives the real window over a real zsh session: commands are
typed as key presses, blocks are clicked with the mouse, and results are
read back from the clipboard.
"""

from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from conftest import SMI_COPIED, SMI_PROGRAMS, install_smi
from jupyterminal.ui.view import PAD_LEFT

CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
NONE = Qt.KeyboardModifier.NoModifier
LEFT = Qt.MouseButton.LeftButton


def clipboard() -> str:
    return QGuiApplication.clipboard().text()


def click(live, block, mods=NONE) -> None:
    QTest.mouseClick(live.view, LEFT, mods, live.block_point(block))
    live.settle(0.05)


def copy_shortcut(live, mods=CTRL | SHIFT) -> str:
    QGuiApplication.clipboard().setText("\0stale")
    QTest.keyClick(live.view, Qt.Key.Key_C, mods)
    live.settle(0.05)
    return clipboard()


def type_line(live, text: str) -> None:
    """Type text and press Enter, as key presses on the view."""
    QTest.keyClicks(live.view, text)
    QTest.keyClick(live.view, Qt.Key.Key_Return)


def output_point(live, block, row: int, col: int) -> QPoint:
    view = live.view
    frame = view.frame()
    top = frame.tops[frame.ids.index(block.id)] - view.scroll
    y = top + block.view.header_h + row * view.r.ch + view.r.ch // 2
    return QPoint(PAD_LEFT + col * view.r.cw + 2, int(y))


@pytest.mark.parametrize("program", SMI_PROGRAMS)
def test_01_nvidia_smi_block_copies_exactly(live_window, program):
    """nvidia-smi runs; one click on its block plus Ctrl+Shift+C puts its
    exact output on the clipboard, line for line, with no color codes.
    A stand-in that prints such a table runs everywhere, the program
    itself where it is installed."""
    live = live_window(cols=120)
    install_smi(live.home)
    block = live.run(program, timeout=90)
    click(live, block)
    assert live.view.selected == [block.id]
    copied = copy_shortcut(live)
    assert copied and "\x1b" not in copied
    assert copied == "\n".join(line.rstrip(" ") for line in block.lines())
    # The same bytes through a file prove the copy is exact.
    live.run(f"{program} > smi.txt 2>&1", timeout=90)
    again = live.run("cat smi.txt")
    click(live, again)
    with open(os.path.join(live.home, "smi.txt"), encoding="utf-8",
              errors="replace") as handle:
        reference = handle.read()
    # The copy rules: the final newline goes, and trailing spaces; every
    # other byte the program printed is there, line for line.
    body = reference[:-1] if reference.endswith("\n") else reference
    expected = "\n".join(line.rstrip(" ") for line in body.split("\n"))
    assert copy_shortcut(live) == expected


def test_01_the_copy_is_the_tables_text_without_its_colors(live_window):
    """The stand-in colors its table on a terminal; the clipboard gets the
    text alone, without the spaces at the ends of its lines."""
    live = live_window(cols=120)
    install_smi(live.home)
    block = live.run(SMI_PROGRAMS[0])
    assert block.store.styles                    # it was shown in color
    click(live, block)
    assert copy_shortcut(live) == SMI_COPIED


def test_02_a_300_character_line_copies_as_one_line(live_window):
    live = live_window(cols=100)
    block = live.run("print -r -- ${(l:300::=:)}")
    assert block.view.out_rows == 3              # displayed wrapped
    click(live, block)
    assert copy_shortcut(live) == "=" * 300


PIP_LIKE = r'''
import sys, time
total = 48.0
w = sys.stdout.write
w("Collecting bigpackage\n")
w("  Downloading bigpackage-1.0-py3-none-any.whl (48.0 MB)\n")
w("\x1b[?25l")
for i in range(1, 49):
    done = int(40 * i / 48)
    bar = "\x1b[38;5;197m" + "━" * done + "\x1b[38;5;237m" + "━" * (40 - done)
    w(f"\r\x1b[2K   {bar}\x1b[0m \x1b[32m{i:.1f}/{total:.1f} MB\x1b[0m "
      f"\x1b[31m9.9 MB/s\x1b[0m eta \x1b[36m0:00:0{(48 - i) // 10}\x1b[0m")
    sys.stdout.flush()
    time.sleep(0.01)
w("\n\x1b[?25h")
w("Installing collected packages: bigpackage\n")
w("Successfully installed bigpackage-1.0\n")
'''


def test_03_progress_bar_animates_in_place_and_copies_once(live_window):
    """A pip-style download bar: redrawn in place on one line, and the
    copied output holds its final state once, not every frame."""
    live = live_window(cols=100)
    with open(os.path.join(live.home, "fakepip.py"), "w",
              encoding="utf-8") as handle:
        handle.write(PIP_LIKE)
    block = live.start("python3 fakepip.py")
    frames = set()
    rows_while_animating = set()

    def watch() -> bool:
        """Note the bar at every look, until the final messages come."""
        screen = block.screen
        texts = [row.text() for row in screen.grid] if screen else []
        animating = not any("Installing" in text for text in texts)
        bars = [text for text in texts if "━" in text and animating]
        frames.update(bars[-1:])
        rows_while_animating.add(len(bars))
        return not block.running

    assert live.wait(watch, 60)
    assert live.wait(live.at_prompt)
    assert len(frames) > 3                       # it did animate ...
    assert rows_while_animating - {0} == {1}     # ... on one and the same row
    click(live, block)
    lines = copy_shortcut(live).split("\n")
    assert lines[0] == "Collecting bigpackage"
    bars = [line for line in lines if "━" in line]
    assert len(bars) == 1
    assert "48.0/48.0 MB" in bars[0] and "eta 0:00:00" in bars[0]
    assert lines[-1] == "Successfully installed bigpackage-1.0"
    assert len(lines) == 5


def test_04_false_is_marked_failed_and_true_is_not(live_window):
    live = live_window()
    bad = live.run("false")
    good = live.run("true")
    view = live.view
    view.frame()
    assert bad.failed and bad.exit_code == 1
    assert ("fail", "exit 1", 7) in bad.view.items
    background, header, bar = view._block_colors(bad)
    assert bar == view.theme.failed_bar          # the red sidebar
    assert background == view.theme.failed_background
    assert not good.failed and good.exit_code == 0
    assert view._block_colors(good) == (None, view.theme.header_background,
                                        None)
    assert not any(kind == "fail" for kind, _, _ in good.view.items)
    # And it shows: the failed block has red pixels at its left edge.
    image = view.grab().toImage()
    point = live.block_point(bad)
    assert image.pixelColor(1, point.y()) == view.theme.failed_bar
    assert image.pixelColor(1, live.block_point(good).y()) != \
        view.theme.failed_bar


def test_05_three_blocks_selected_with_shift_click(live_window):
    live = live_window()
    blocks = [live.run(f"echo output-{n}") for n in ("a", "b", "c")]
    click(live, blocks[0])
    click(live, blocks[2], SHIFT)
    assert live.view.selected == [b.id for b in blocks]
    assert copy_shortcut(live) == "output-a\n\noutput-b\n\noutput-c"


def test_06_copy_command_and_output(live_window):
    live = live_window()
    for name in ("one.txt", "two.txt"):
        open(os.path.join(live.home, name), "w").close()
    block = live.run("ls -la")
    click(live, block)
    copied = copy_shortcut(live, CTRL | ALT | SHIFT)
    lines = copied.split("\n")
    assert lines[0] == "$ ls -la"
    assert lines[1].startswith("total ")
    assert any(line.endswith(" one.txt") for line in lines)
    assert any(line.endswith(" two.txt") for line in lines)
    assert copied == "$ ls -la\n" + "\n".join(block.lines())


def test_07_seq_1000000_stays_responsive_and_copies_completely(live_window):
    """How long the interface is busy is measured twice.  The processor
    time of its thread says how much work one step is, whatever else the
    machine is doing, and is held to a quarter of a second.  The time on
    the clock also sees a step that waits for something, and gets ten
    times as long, since on a busy machine it says little about this
    program."""
    live = live_window()
    view = live.view
    start = time.perf_counter()
    block = live.start("seq 1000000")
    longest = waited = 0.0
    while block.running:
        began, began_clock = time.thread_time(), time.perf_counter()
        live.app.processEvents()
        longest = max(longest, time.thread_time() - began)   # one step
        waited = max(waited, time.perf_counter() - began_clock)
        assert time.perf_counter() - start < 120
    assert live.wait(live.at_prompt)
    assert longest < 0.25, f"the interface was busy for {longest:.2f}s"
    assert waited < 2.5, f"the interface stalled for {waited:.2f}s"
    # Scrolling anywhere in it paints promptly.
    for fraction in (0.0, 0.5, 1.0):
        view._set_scroll(view.frame().max_scroll * fraction)
        began = time.thread_time()
        view.grab()
        assert time.thread_time() - began < 0.25
    # One click anywhere on the block (its header is a million lines up).
    QTest.mouseClick(view, LEFT, NONE, QPoint(200, 120))
    live.settle(0.05)
    assert view.selected == [block.id]
    began = time.thread_time()
    copied = copy_shortcut(live)
    assert time.thread_time() - began < 3.0
    assert copied.count("\n") == 999_999
    assert copied.startswith("1\n2\n3\n") and copied.endswith("\n1000000")


def test_08_selection_survives_new_output_in_a_running_block(live_window):
    live = live_window()
    # ping, or where it is missing or not allowed, anything else that
    # prints a line every now and then.
    block = live.start("ping -i 0.2 127.0.0.1 || "
                       "while :; do echo reply; sleep 0.2; done")
    assert live.wait(lambda: len(block.lines()) >= 4, 20), block.lines()
    live.view.follow = False
    first = block.lines()[1]
    QTest.mousePress(live.view, LEFT, NONE, output_point(live, block, 1, 0))
    QTest.mouseMove(live.view, output_point(live, block, 1, 5))
    QTest.mouseMove(live.view, output_point(live, block, 2, 10))
    QTest.mouseRelease(live.view, LEFT, NONE,
                       output_point(live, block, 2, 10))
    live.settle(0.05)
    chosen = live.view._sel
    text = live.view.selection_text()
    assert chosen is not None and text.startswith(first[:5])
    count = len(block.lines())
    assert live.wait(lambda: len(block.lines()) >= count + 5, 20)
    assert block.running
    assert live.view._sel == chosen              # neither cleared nor moved
    assert live.view.selection_text() == text
    QTest.keyClick(live.view, Qt.Key.Key_C, CTRL)
    assert live.wait(live.at_prompt)


@pytest.mark.parametrize("program,expect,keys", [
    ("vim -u NONE -N sample.txt", "sample line 1", [":q!", "\r"]),
    ("less -+F -+X sample.txt", "sample line 1", ["q"]),
    ("htop", "PID", ["q"]),
])
def test_09_full_screen_programs(live_window, program, expect, keys):
    """vim, htop and less work as in a conventional terminal, and the
    block list returns intact after each exits."""
    live = live_window()
    with open(os.path.join(live.home, "sample.txt"), "w") as handle:
        handle.write("".join(f"sample line {i}\n" for i in range(1, 200)))
    before = [live.run("echo one"), live.run("echo two")]
    heights = [b.view.height for b in before]
    block = live.start(program)
    assert live.wait(lambda: live.session.fullscreen, 20)
    screen = live.session.term.screen
    assert live.wait(lambda: any(expect in row.text()
                                 for row in screen.grid), 20)
    assert not live.view.bar.isVisible()         # the block list is hidden
    assert (screen.cols, screen.rows) == (live.session.cols,
                                          live.session.rows)
    if program.startswith(("vim", "less")):
        # The program reacts to keys typed on the view: scroll a page.
        QTest.keyClick(live.view, Qt.Key.Key_F, CTRL)
        assert live.wait(lambda: any("sample line 4" in row.text()
                                     for row in screen.grid), 10)
        assert not any(row.text().startswith("sample line 1 ") or
                       row.text().rstrip() == "sample line 1"
                       for row in screen.grid)
    for key in keys:
        if key == "\r":
            QTest.keyClick(live.view, Qt.Key.Key_Return)
        elif key == ":q!":
            QTest.keyClick(live.view, Qt.Key.Key_Escape)
            QTest.keyClicks(live.view, key)
        else:
            QTest.keyClicks(live.view, key)
    assert live.wait(live.at_prompt, 20)
    assert not live.session.fullscreen
    assert block.fullscreen and block.exit_code == 0
    assert ("chip", "full-screen", 14) in live.view._header_items(block)
    assert [b for b in live.session.blocks if b.is_command] == \
        before + [block]
    live.view.frame()
    assert [b.view.height for b in before] == heights
    assert [b.lines() for b in before] == [["one"], ["two"]]
    after = live.run("echo three")
    assert after.lines() == ["three"]


def test_10_python_repl_in_one_block(live_window):
    live = live_window()
    count = len(live.session.blocks)
    type_line(live, "python3 -q")
    assert live.wait(lambda: live.session.running is not None)
    block = live.session.running

    def asked(times: int) -> bool:
        """Python has shown its prompt this many times, and waits at the
        last one.  A line typed before that is echoed by the terminal
        driver as well, in front of the prompt, as in any terminal."""
        lines = block.lines()
        return sum(line.startswith(">>>") for line in lines) == times \
            and lines[-1].rstrip() == ">>>"

    assert live.wait(lambda: asked(1))
    type_line(live, "greeting = 'hello'")
    assert live.wait(lambda: asked(2))
    type_line(live, "print(greeting * 2)")
    assert live.wait(lambda: "hellohello" in block.lines() and asked(3))
    assert block.running and len(live.session.blocks) == count + 1
    type_line(live, "exit()")
    assert live.wait(live.at_prompt)
    assert len(live.session.blocks) == count + 1     # one block throughout
    click(live, block)
    transcript = copy_shortcut(live)
    assert transcript.split("\n") == [
        ">>> greeting = 'hello'",
        ">>> print(greeting * 2)",
        "hellohello",
        ">>> exit()",
    ]


def test_11_filter_for_error_and_clear(live_window):
    live = live_window()
    block = live.run("print -l 'step 1 ok' 'error: no space' 'step 2 ok' "
                     "'fatal error in step 3' 'done'")
    view = live.view

    def rows() -> int:
        """The rows of output the block shows once it is laid out."""
        view.frame()
        return block.view.out_rows

    assert rows() == 5
    click(live, block)
    QTest.keyClick(view, Qt.Key.Key_L, CTRL | SHIFT)     # Filter output
    assert view.search.isVisible()
    QTest.keyClicks(view.search.edit, "error")
    assert live.wait(lambda: rows() == 2)
    assert block.visible == [1, 3]
    assert view.copy_blocks([block], "output") == \
        "error: no space\nfatal error in step 3"
    view.search.edit.clear()                             # clearing restores
    assert live.wait(lambda: block.filter is None and rows() == 5)
    assert view.copy_blocks([block], "output").count("\n") == 4


def test_12_keyboard_navigation(live_window):
    live = live_window()
    first, second, third = (live.run(f"echo {n}") for n in "abc")
    view = live.view
    QTest.keyClick(view, Qt.Key.Key_Up, CTRL)
    assert view.selected == [third.id]                   # the newest block
    QTest.keyClick(view, Qt.Key.Key_Up)
    assert view.selected == [second.id]
    QTest.keyClick(view, Qt.Key.Key_Up)
    assert view.selected == [first.id]
    QTest.keyClick(view, Qt.Key.Key_Down)
    assert view.selected == [second.id]
    assert copy_shortcut(live) == "b"
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert view.selected == [] and view.hasFocus()
    # Back in the input area: the keys reach the shell again.
    type_line(live, "echo back-in-the-shell")
    assert live.wait(lambda: live.session.blocks[-1].lines()
                     == ["back-in-the-shell"] and live.at_prompt())
