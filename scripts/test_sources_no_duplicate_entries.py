"""Guard against entry duplication in sources.md.

Merging a stale branch into a reorganised `sources.md` does not conflict — git
appends the relocated block instead of aligning it, and the result is a run of
byte-identical entries that reads as normal prose. It has happened twice. A
reader counting sources double-counts, which is exactly the kind of quiet
inflation this archive exists to avoid.

Note that duplicate *URLs* are legitimate and common: an entry cites the
primary it comments on, and cross-references repeat links deliberately. The
invariant is on whole entries, not on links.
"""

from __future__ import annotations

import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "sources.md"
ENTRY_PREFIX = "- **"


def entry_blocks(text: str) -> list[tuple[int, str]]:
    """Split into (1-indexed start line, block text) for each top-level entry."""
    lines = text.split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(ENTRY_PREFIX)]
    blocks = []
    for start, end in zip(starts, starts[1:] + [len(lines)]):
        blocks.append((start + 1, "\n".join(lines[start:end]).rstrip()))
    return blocks


class SourcesEntryUniquenessTests(unittest.TestCase):
    def test_no_two_entries_are_byte_identical(self):
        blocks = entry_blocks(SOURCES.read_text(encoding="utf-8"))
        self.assertGreater(len(blocks), 50, "entry parsing looks broken")
        seen: dict[str, list[int]] = defaultdict(list)
        for line_no, block in blocks:
            seen[block].append(line_no)
        duplicates = {block: at for block, at in seen.items() if len(at) > 1}
        if duplicates:
            report = "\n".join(
                f"  lines {at}: {block.splitlines()[0][:70]}"
                for block, at in sorted(duplicates.items(), key=lambda kv: kv[1][0])
            )
            self.fail(
                f"{len(duplicates)} duplicated entry block(s) in sources.md — "
                f"likely a stale-branch merge:\n{report}"
            )


if __name__ == "__main__":
    unittest.main()
