"""What a copy takes from blocks, and filtering and finding in lines."""

from __future__ import annotations

import dataclasses
import re

import pytest

from jupyterminal.session import text as text_module
from jupyterminal.session.block import BACKGROUND, COMMAND, Block
from jupyterminal.session.text import (FilterSpec, block_text, clean_lines,
                                       compile_pattern, copy_text,
                                       filter_indices, find_matches,
                                       output_text, shown_lines)
from jupyterminal.term import Terminal

GREEK = ["alpha", "beta", "Gamma", "delta", "epsilon", "zeta", "eta",
         "theta", "iota", "kappa"]


def block_of(lines: list[str], command: str = "cmd",
             kind: str = COMMAND) -> Block:
    """A finished block that printed ``lines``."""
    block = Block(kind, 40, 5, command=command)
    Terminal(block.screen).feed(
        "".join(line + "\r\n" for line in lines).encode())
    block.finish(0)
    return block


# -- the copy rules -----------------------------------------------------------

def test_lines_are_joined_without_their_trailing_spaces():
    assert clean_lines([]) == ""
    assert clean_lines(["a", "b"]) == "a\nb"
    assert clean_lines(["a  ", "b"]) == "a\nb"           # inside the text
    assert clean_lines(["a", "b  "]) == "a\nb"           # at its very end
    assert clean_lines(["a ", " b ", "c "]) == "a\n b\nc"
    assert clean_lines(["a", "   ", "b"]) == "a\n\nb"


def test_tabs_blank_lines_and_leading_spaces_stay():
    assert clean_lines(["a\t", "\tb", "", "  c"]) == "a\t\n\tb\n\n  c"
    assert clean_lines(["x \t "]) == "x \t"
    assert clean_lines(["", ""]) == "\n"
    assert clean_lines([" "]) == ""


def test_output_text_is_every_line_unless_a_filter_is_set():
    block = block_of(["error: one", "fine  ", "ERROR: two", "", "fine too"])
    assert shown_lines(block) == ["error: one", "fine", "ERROR: two", "",
                                  "fine too"]
    assert output_text(block) == "error: one\nfine\nERROR: two\n\nfine too"
    block.filter = FilterSpec("error")
    assert shown_lines(block) == ["error: one", "ERROR: two"]
    assert output_text(block) == "error: one\nERROR: two"
    block.filter = FilterSpec("nothing like it")
    assert shown_lines(block) == []
    assert output_text(block) == ""


def test_the_names_of_the_copy_modes():
    assert (text_module.OUTPUT, text_module.COMMAND, text_module.BOTH) == \
        ("output", "command", "both")


def test_block_text_in_each_mode():
    block = block_of(["out 1", "out 2"], command="make all")
    assert block_text(block) == "out 1\nout 2"
    assert block_text(block, "output") == "out 1\nout 2"
    assert block_text(block, "command") == "make all"
    assert block_text(block, "both") == "$ make all\nout 1\nout 2"


def test_block_text_of_a_command_without_output():
    quiet = block_of([], command="true")
    assert block_text(quiet, "output") == ""
    assert block_text(quiet, "command") == "true"
    assert block_text(quiet, "both") == "$ true"


def test_block_text_of_background_output_has_no_command_line():
    background = block_of(["job done"], command="", kind=BACKGROUND)
    assert block_text(background, "output") == "job done"
    assert block_text(background, "both") == "job done"
    assert block_text(background, "command") == ""


def test_block_text_with_a_filter_copies_only_what_is_shown():
    block = block_of(["keep this", "drop", "keep that"], command="run")
    block.filter = FilterSpec("keep")
    assert block_text(block, "both") == "$ run\nkeep this\nkeep that"
    block.filter = FilterSpec("absent")
    assert block_text(block, "both") == "$ run"


def test_several_blocks_are_joined_by_one_blank_line():
    first = block_of(["a1", "a2"], command="first")
    second = block_of([], command="second")
    third = block_of(["c1"], command="", kind=BACKGROUND)
    assert copy_text([]) == ""
    assert copy_text([first]) == "a1\na2"
    assert copy_text([first, third]) == "a1\na2\n\nc1"
    assert copy_text([third, first]) == "c1\n\na1\na2"
    assert copy_text([first, second, third]) == "a1\na2\n\n\n\nc1"
    assert copy_text([first, second, third], "both") == \
        "$ first\na1\na2\n\n$ second\n\nc1"
    # Only commands have a command to copy.
    assert copy_text([first, second, third], "command") == "first\n\nsecond"
    assert copy_text([third], "command") == ""


# -- filters ------------------------------------------------------------------

def test_a_filter_is_a_value():
    spec = FilterSpec("x")
    assert (spec.pattern, spec.regex, spec.case_sensitive, spec.invert,
            spec.context) == ("x", False, False, False, 0)
    assert spec == FilterSpec("x") and hash(spec) == hash(FilterSpec("x"))
    assert spec != FilterSpec("x", context=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.pattern = "y"


def test_patterns_are_plain_text_unless_marked_as_expressions():
    plain = compile_pattern("a.c", False, False)
    assert plain.pattern == re.escape("a.c")
    assert plain.search("xA.Cx") is not None
    assert plain.search("abc") is None
    exact = compile_pattern("a.c", False, True)
    assert exact.search("a.c") is not None
    assert exact.search("A.C") is None
    assert exact.flags == re.compile("a").flags      # no flag of its own
    assert plain.flags == re.compile("a", re.IGNORECASE).flags
    expression = compile_pattern("a.c", True, True)
    assert expression.pattern == "a.c"
    assert expression.search("abc") is not None
    assert expression.search("ABC") is None
    assert compile_pattern("a.c", True, False).search("ABC") is not None


def test_no_pattern_and_broken_patterns_compile_to_nothing():
    assert compile_pattern("", False, False) is None
    assert compile_pattern("", True, True) is None
    assert compile_pattern("a(", True, False) is None
    # The same text is fine when it is not an expression.
    assert compile_pattern("a(", False, False).search("ya(") is not None
    assert FilterSpec("").compile() is None
    assert FilterSpec("a(", regex=True).compile() is None


def test_a_filter_compiles_with_its_own_settings():
    assert FilterSpec("a.c").compile().pattern == re.escape("a.c")
    assert FilterSpec("a.c", regex=True).compile().pattern == "a.c"
    assert FilterSpec("A").compile().search("a") is not None
    assert FilterSpec("A", case_sensitive=True).compile().search("a") is None


def test_a_filter_shows_the_lines_that_match():
    assert filter_indices(GREEK, FilterSpec("eta")) == [1, 5, 6, 7]
    assert filter_indices(GREEK, FilterSpec("gamma")) == [2]
    assert filter_indices(GREEK, FilterSpec("gamma", case_sensitive=True)) \
        == []
    assert filter_indices(GREEK, FilterSpec("Gamma", case_sensitive=True)) \
        == [2]
    assert filter_indices(GREEK, FilterSpec("^.eta$", regex=True)) == [1, 5]
    assert filter_indices(GREEK, FilterSpec("omega")) == []
    assert filter_indices([], FilterSpec("eta")) == []


def test_an_inverted_filter_shows_the_other_lines():
    assert filter_indices(GREEK, FilterSpec("eta", invert=True)) == \
        [0, 2, 3, 4, 8, 9]
    assert filter_indices(GREEK, FilterSpec("omega", invert=True)) == \
        list(range(10))
    assert filter_indices(GREEK, FilterSpec("a", invert=True)) == [4]
    assert filter_indices(GREEK, FilterSpec(".", regex=True, invert=True)) \
        == []


def test_a_filter_without_a_usable_pattern_shows_everything():
    assert filter_indices(GREEK, FilterSpec("")) == list(range(10))
    assert filter_indices(GREEK, FilterSpec("", invert=True, context=3)) == \
        list(range(10))
    assert filter_indices(GREEK, FilterSpec("(", regex=True)) == \
        list(range(10))


def test_context_lines_around_each_match():
    def shown(pattern: str, context: int) -> list[int]:
        return filter_indices(GREEK, FilterSpec(pattern, context=context))

    assert shown("epsilon", 2) == [2, 3, 4, 5, 6]
    assert shown("epsilon", 1) == [3, 4, 5]
    assert shown("epsilon", 0) == [4]
    assert shown("epsilon", -1) == [4]
    # At the edges there is less context to give.
    assert shown("alpha", 2) == [0, 1, 2]
    assert shown("beta", 2) == [0, 1, 2, 3]
    assert shown("Gamma", 2) == [0, 1, 2, 3, 4]
    assert shown("delta", 2) == [1, 2, 3, 4, 5]
    assert shown("kappa", 3) == [6, 7, 8, 9]
    assert shown("iota", 3) == [5, 6, 7, 8, 9]
    assert shown("epsilon", 50) == list(range(10))
    # Neighbouring matches share their context.
    assert shown("eta", 1) == [0, 1, 2, 4, 5, 6, 7, 8]
    assert shown("omega", 2) == []
    assert filter_indices(GREEK, FilterSpec("eta", invert=True, context=1)) \
        == [0, 1, 2, 3, 4, 5, 7, 8, 9]


# -- finding ------------------------------------------------------------------

FISH = ["one fish", "two fish", "red FISH blue fish", "none"]


def test_find_gives_every_match_in_reading_order():
    assert find_matches(FISH, "fish") == [
        (0, 4, 8), (1, 4, 8), (2, 4, 8), (2, 14, 18)]
    assert find_matches(FISH, "fish", case_sensitive=True) == [
        (0, 4, 8), (1, 4, 8), (2, 14, 18)]
    assert find_matches(FISH, "none") == [(3, 0, 4)]
    assert find_matches(FISH, "shark") == []
    assert find_matches([], "fish") == []


def test_find_with_expressions():
    assert find_matches(FISH, "f.sh$", regex=True) == [
        (0, 4, 8), (1, 4, 8), (2, 14, 18)]
    assert find_matches(FISH, "F.SH", regex=True, case_sensitive=True) == [
        (2, 4, 8)]
    assert find_matches(FISH, "f.sh") == []          # a dot is a dot
    assert find_matches(FISH, "f(", regex=True) == []
    assert find_matches(FISH, "") == []
    assert find_matches(FISH, "", regex=True) == []


def test_find_skips_matches_of_nothing():
    assert find_matches(["abc", "aXXb", "XaX"], "X*", regex=True) == [
        (1, 1, 3), (2, 0, 1), (2, 2, 3)]
    assert find_matches(["abc"], "^", regex=True) == []


def test_find_can_be_held_to_some_lines():
    assert find_matches(FISH, "fish", indices=[0, 2]) == [
        (0, 4, 8), (2, 4, 8), (2, 14, 18)]
    assert find_matches(FISH, "fish", indices=[3]) == []
    assert find_matches(FISH, "fish", indices=[]) == []
    assert find_matches(FISH, "n", indices=[3, 0]) == [
        (3, 0, 1), (3, 2, 3), (0, 1, 2)]


def test_find_stops_after_the_line_that_reaches_the_limit():
    lines = ["aa"] * 5
    first_two = [(0, 0, 1), (0, 1, 2), (1, 0, 1), (1, 1, 2)]
    assert find_matches(lines, "a", limit=1) == first_two[:2]
    assert find_matches(lines, "a", limit=2) == first_two[:2]
    assert find_matches(lines, "a", limit=3) == first_two
    assert find_matches(lines, "a", limit=4) == first_two
    assert len(find_matches(lines, "a", limit=5)) == 6
    assert len(find_matches(lines, "a")) == 10
    # Lines without a match do not end the search.
    assert find_matches(["none", "aa", "none", "a"], "a", limit=3) == [
        (1, 0, 1), (1, 1, 2), (3, 0, 1)]


def test_find_stops_at_a_hundred_thousand_matches():
    assert len(find_matches(["a"] * 100_001, "a")) == 100_000
