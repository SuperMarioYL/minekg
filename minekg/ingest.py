"""Ingest — auto-detect source files and emit unified Record JSONL.

``minekg ingest ./exports`` scans a directory, routes each file to the right
adapter by extension + content sniffing, collects every ``Record`` and writes
them as one JSONL stream (default ``~/.minekg/records.jsonl``) with full
provenance (source file + line number per record).
"""

from __future__ import annotations

import os
from pathlib import Path

from . import ALIPAY, BANK, NOTES, WECHAT, to_jsonl
from .sources import alipay, bank, notes, wechat


def _looks_like_wechat_txt(text: str) -> bool:
    # Heuristic: presence of a timestamp-prefixed message line.
    import re

    return bool(re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}[\sT]+\d{1,2}:\d{2}", text))


def _looks_like_alipay_csv(text: str) -> bool:
    head = text[:600]
    return ("交易对方" in head or "支付宝" in head) and ("金额" in head or "," in head)


def detect_source(path: os.PathLike | str) -> str | None:
    """Return the source tag for a file, or ``None`` if unrecognised."""
    path = Path(path)
    name = path.name.lower()
    if name.endswith(".pdf"):
        return BANK
    if name.endswith(".csv"):
        return ALIPAY
    if name.endswith(".md") or name.endswith(".markdown"):
        return NOTES
    if name.endswith(".txt"):
        from . import read_text

        text = read_text(path)
        if _looks_like_wechat_txt(text):
            return WECHAT
        if _looks_like_alipay_csv(text):
            return ALIPAY
        # ambiguous txt → treat as notes-ish free text
        return NOTES
    return None


def ingest_file(path: os.PathLike | str) -> list:
    """Parse a single file into records (auto-detected)."""
    path = Path(path)
    source = detect_source(path)
    if source == WECHAT:
        return wechat.parse(path)
    if source == ALIPAY:
        return alipay.parse(path)
    if source == BANK:
        return bank.parse(path)
    if source == NOTES:
        return notes.parse(path)
    return []


def ingest(exports_dir: os.PathLike | str, out_path: os.PathLike | str | None = None) -> list:
    """Ingest every recognised file in ``exports_dir`` → unified JSONL.

    Returns the list of records (also written to ``out_path``, default
    ``~/.minekg/records.jsonl``).
    """
    exports_dir = Path(exports_dir)
    records: list = []
    if exports_dir.is_file():
        records.extend(ingest_file(exports_dir))
    else:
        for p in sorted(exports_dir.iterdir()):
            if p.is_file() and detect_source(p) is not None:
                records.extend(ingest_file(p))

    if out_path is None:
        from . import records_path

        out_path = records_path()
    to_jsonl(records, out_path)
    return records
