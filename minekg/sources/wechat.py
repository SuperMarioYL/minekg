"""WeChat export adapter.

WeChat (微信) chat exports are plain-text conversation logs. There is no
single canonical format — every export tool emits something slightly
different — so this parser is deliberately tolerant. The two shapes it
recognises:

1. ``<timestamp> <speaker>`` followed by one or more message-body lines until
   the next timestamp line. This is what the common WeChatMsg / wechat-dump
   tools produce.
2. A fallback where each non-empty line is treated as a single message with
   no speaker (still useful for free-form notes-like exports).

Each emitted record carries the speaker as ``counterparty`` (when present),
the message body as ``content`` and the full block as ``raw_row``.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import Provenance, Record, WECHAT, read_text

# 2023-01-15 12:30:45 小张   /   2023/01/15 12:30:45 小张
_TS_RE = re.compile(
    r"^(\d{4}[-/]\d{1,2}[-/]\d{1,2}[\sT]+\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$"
)


def parse_lines(lines: list, file_path: str) -> list:
    """Parse pre-split text lines into WeChat records."""
    records: list = []
    current_ts: str | None = None
    current_speaker: str | None = None
    body: list = []
    block_start_line = 0

    def flush(end_line: int) -> None:
        nonlocal current_ts, current_speaker, body, block_start_line
        if current_ts is None and not body:
            return
        content = "\n".join(body).strip()
        if not content and current_speaker is None:
            current_ts = None
            current_speaker = None
            body = []
            return
        records.append(
            Record(
                source=WECHAT,
                raw_row=f"{current_ts or ''} {current_speaker or ''}\n{content}".strip(),
                provenance=Provenance(file=file_path, line=block_start_line),
                ts=current_ts,
                counterparty=current_speaker,
                content=content,
            )
        )
        current_ts = None
        current_speaker = None
        body = []
        block_start_line = end_line

    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        m = _TS_RE.match(stripped)
        if m:
            flush(idx)
            current_ts = m.group(1).replace("/", "-")
            # normalise the T separator to a space
            current_ts = current_ts.replace("T", " ")
            current_speaker = m.group(2).strip()
            block_start_line = idx
        elif stripped:
            if current_ts is None and current_speaker is None:
                # fallback shape: standalone line, treat as a body-only record
                current_ts = None
                current_speaker = None
                block_start_line = idx
                body.append(stripped)
                flush(idx + 1)
            else:
                body.append(stripped)
        # blank lines just separate blocks; do nothing

    flush(len(lines))
    return records


def parse(path) -> list:
    path = Path(path)
    text = read_text(path)
    lines = text.splitlines()
    return parse_lines(lines, str(path))
