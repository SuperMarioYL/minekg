"""MineKG — local-first personal knowledge graph for Chinese data exports.

MineKG fuses a Chinese ordinary user's exported 微信聊天记录 / 支付宝账单 /
银行流水 PDF / 笔记 into one queryable SQLite-backed knowledge graph, then
answers natural-language questions with per-row provenance.

This package exposes a small, stable data model (``Record`` / ``Entity`` /
``Provenance``) shared by every source adapter, the NER/linkage stages and the
``ask`` (NL -> SQL) stage. The CLI lives in :mod:`minekg.cli`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

__version__ = "0.2.0"

__all__ = [
    "Provenance",
    "Entity",
    "Record",
    "home_dir",
    "records_path",
    "db_path",
    "config_path",
    "read_text",
    "to_jsonl",
    "from_jsonl",
]

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ENV_HOME = "MINEKG_HOME"


def home_dir() -> Path:
    """Return the MineKG working directory (``~/.minekg`` by default).

    Overridable via the ``MINEKG_HOME`` env var so tests and the demo can run
    in a throwaway temp dir without touching the user's real data.
    """
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".minekg"


def records_path() -> Path:
    return home_dir() / "records.jsonl"


def db_path() -> Path:
    return home_dir() / "minekg.db"


def config_path() -> Path:
    return home_dir() / "config.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Entity types
PERSON = "person"
PLACE = "place"
ORG = "org"
TIME = "time"

# Source tags
WECHAT = "wechat"
ALIPAY = "alipay"
BANK = "bank"
NOTES = "notes"


@dataclass
class Provenance:
    """Where a record came from inside its source file."""

    file: str
    line: Optional[int] = None


@dataclass
class Entity:
    """An entity mention extracted from a record.

    ``surface_form`` is the exact string as it appeared; ``canonical_id`` is
    assigned by the linkage stage (``None`` until then).
    """

    type: str  # person | place | org | time
    surface_form: str
    canonical_id: Optional[str] = None
    confidence: float = 1.0


@dataclass
class Record:
    """A unified record emitted by any source adapter."""

    source: str  # wechat | alipay | bank | notes
    raw_row: str
    provenance: Provenance
    ts: Optional[str] = None
    amount: Optional[float] = None
    direction: Optional[str] = None  # 收 / 支 / None
    counterparty: Optional[str] = None
    content: str = ""
    entities: list = field(default_factory=list)
    record_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # provenance stays a nested dict; entities stay as list of dicts
        return d


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def to_jsonl(records: list, path: os.PathLike | str) -> str:
    """Write records as JSONL. Returns the path written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    return str(path)


def from_jsonl(path: os.PathLike | str) -> list:
    """Read records from JSONL back into :class:`Record` objects."""
    path = Path(path)
    records: list = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            prov = d.get("provenance") or {}
            records.append(
                Record(
                    source=d.get("source", ""),
                    raw_row=d.get("raw_row", ""),
                    provenance=Provenance(
                        file=prov.get("file", ""),
                        line=prov.get("line"),
                    ),
                    ts=d.get("ts"),
                    amount=d.get("amount"),
                    direction=d.get("direction"),
                    counterparty=d.get("counterparty"),
                    content=d.get("content", ""),
                    entities=[
                        Entity(
                            type=e.get("type", ""),
                            surface_form=e.get("surface_form", ""),
                            canonical_id=e.get("canonical_id"),
                            confidence=e.get("confidence", 1.0),
                        )
                        for e in d.get("entities", [])
                    ],
                    record_id=d.get("record_id"),
                )
            )
    return records


# ---------------------------------------------------------------------------
# Text reading (encoding tolerant — Chinese exports are GBK / UTF-8-BOM mixed)
# ---------------------------------------------------------------------------


def read_text(path: os.PathLike | str) -> str:
    """Read a text file trying common Chinese encodings."""
    path = Path(path)
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # last resort: ignore undecodable bytes
    return raw.decode("utf-8", errors="replace")
