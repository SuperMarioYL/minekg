"""Personal notes (笔记) Markdown adapter.

Each non-empty, non-pure-decor line of a Markdown note becomes one record.
Dates written in the note (``2023-01-15``, ``2023年1月``, ``去年``…) are
extracted into ``ts``; the raw line is preserved in ``raw_row`` and the line
number in provenance. Entity extraction (人名/地名/机构/时间) is done later
by :mod:`minekg.ner`.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import NOTES, Provenance, Record, read_text

# A date that may appear anywhere in a note line.
_DATE_RE = re.compile(
    r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{4}年\d{1,2}月)"
)


def _strip_md_syntax(line: str) -> str:
    """Drop leading markdown list/heading markers for the content field."""
    s = re.sub(r"^[\s>#\-\*\+]+", "", line).strip()
    return s


def parse_lines(lines: list, file_path: str) -> list:
    records: list = []
    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        # skip pure markdown fences / horizontal rules
        if re.match(r"^\s*(```|---|\*\*\*|___)\s*$", line):
            continue
        m = _DATE_RE.search(line)
        ts = m.group(1) if m else None
        if ts:
            ts = ts.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
        content = _strip_md_syntax(line)
        records.append(
            Record(
                source=NOTES,
                raw_row=line.strip(),
                provenance=Provenance(file=file_path, line=idx),
                ts=ts,
                content=content,
            )
        )
    return records


def parse(path) -> list:
    path = Path(path)
    text = read_text(path)
    return parse_lines(text.splitlines(), str(path))
