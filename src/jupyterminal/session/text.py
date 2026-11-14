"""What lands on the clipboard, and searching within a block.

The copy rules, in one place:

* plain text only - styles never reach this module;
* logical lines: a line the window showed wrapped is one line;
* trailing spaces at line ends are removed, tabs and blank lines stay;
* the final trailing newline is removed;
* while a filter is active, only the lines it shows are copied;
* several blocks are joined in session order with one blank line between.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .block import Block

OUTPUT = "output"
COMMAND = "command"
BOTH = "both"

_TRAILING = re.compile(" +$", re.MULTILINE)


def clean_lines(lines: list[str]) -> str:
    """Join logical lines the way copied text must look."""
    text = "\n".join(lines)
    if " \n" in text or text.endswith(" "):
        text = _TRAILING.sub("", text)
    return text


def shown_lines(block: Block) -> list[str]:
    """The block's output lines, limited to those its filter shows."""
    lines = block.lines()
    spec = block.filter
    if spec is None:
        return lines
    return [lines[i] for i in filter_indices(lines, spec)]


def output_text(block: Block) -> str:
    return clean_lines(shown_lines(block))


def block_text(block: Block, mode: str = OUTPUT) -> str:
    """The text of one block for a copy action."""
    if mode == COMMAND:
        return block.command
    output = output_text(block)
    if mode == BOTH and block.is_command:
        return f"$ {block.command}\n{output}" if output \
            else f"$ {block.command}"
    return output


def copy_text(blocks: list[Block], mode: str = OUTPUT) -> str:
    """The text of several blocks, in the order given."""
    if mode == COMMAND:
        blocks = [b for b in blocks if b.is_command]
    return "\n\n".join(block_text(block, mode) for block in blocks)


# -- filtering ----------------------------------------------------------------

@dataclass(frozen=True)
class FilterSpec:
    """Which lines of a block to show."""

    pattern: str
    regex: bool = False
    case_sensitive: bool = False
    invert: bool = False
    context: int = 0

    def compile(self) -> re.Pattern | None:
        """The compiled pattern, or ``None`` if it is not valid."""
        return compile_pattern(self.pattern, self.regex, self.case_sensitive)


def compile_pattern(pattern: str, regex: bool,
                    case_sensitive: bool) -> re.Pattern | None:
    if not pattern:
        return None
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        return re.compile(pattern if regex else re.escape(pattern), flags)
    except re.error:
        return None


def filter_indices(lines: list[str], spec: FilterSpec) -> list[int]:
    """Indices of the lines a filter shows, in order."""
    compiled = spec.compile()
    count = len(lines)
    if compiled is None:
        return list(range(count))
    search = compiled.search
    if spec.invert:
        hits = [i for i, line in enumerate(lines) if not search(line)]
    else:
        hits = [i for i, line in enumerate(lines) if search(line)]
    context = spec.context
    if context <= 0 or not hits:
        return hits
    mask = bytearray(count)
    for i in hits:
        low = max(0, i - context)
        high = min(count, i + context + 1)
        mask[low:high] = b"\x01" * (high - low)
    return [i for i, shown in enumerate(mask) if shown]


# -- finding ------------------------------------------------------------------

def find_matches(lines: list[str], pattern: str, regex: bool = False,
                 case_sensitive: bool = False,
                 indices: list[int] | None = None, limit: int = 100_000
                 ) -> list[tuple[int, int, int]]:
    """All matches as ``(line, start, end)``, in reading order.

    ``indices`` restricts the search to those lines (the shown ones while
    a filter is active).
    """
    compiled = compile_pattern(pattern, regex, case_sensitive)
    if compiled is None:
        return []
    out: list[tuple[int, int, int]] = []
    candidates = range(len(lines)) if indices is None else indices
    for i in candidates:
        for match in compiled.finditer(lines[i]):
            if match.end() > match.start():
                out.append((i, match.start(), match.end()))
        if len(out) >= limit:
            break
    return out
