"""SQLite knowledge-graph store.

One process, one SQLite file at ``~/.minekg/minekg.db``. No vector store,
no embeddings — a deterministic, every-edge-explained KG (mirroring the
"no vector store" stance of the codebase→KG tools this project transfers
from).

Schema::

    records   (record_id PK, source, ts, amount, direction, counterparty,
               content, raw_row, file, line)
    entities  (id PK, record_id, type, surface_form, canonical_id, confidence)
    aliases   (canonical_id PK, surface_form)   -- one row per alias
    edges     (record_id, canonical_id)         -- record ↔ canonical entity
    ambiguities (id PK, canonical_id, surface_forms, reason, confidence)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from . import config_path, db_path
from .linkage import LinkageResult

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id    TEXT PRIMARY KEY,
    source       TEXT,
    ts           TEXT,
    amount       REAL,
    direction    TEXT,
    counterparty TEXT,
    content      TEXT,
    raw_row      TEXT,
    file         TEXT,
    line         INTEGER
);
CREATE TABLE IF NOT EXISTS entities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id     TEXT,
    type          TEXT,
    surface_form  TEXT,
    canonical_id  TEXT,
    confidence    REAL
);
CREATE TABLE IF NOT EXISTS aliases (
    canonical_id TEXT,
    surface_form TEXT,
    PRIMARY KEY (canonical_id, surface_form)
);
CREATE TABLE IF NOT EXISTS edges (
    record_id    TEXT,
    canonical_id TEXT
);
CREATE TABLE IF NOT EXISTS ambiguities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id  TEXT,
    surface_forms TEXT,
    reason        TEXT,
    confidence    REAL
);
CREATE INDEX IF NOT EXISTS idx_entities_canon ON entities(canonical_id);
CREATE INDEX IF NOT EXISTS idx_edges_canon ON edges(canonical_id);
CREATE INDEX IF NOT EXISTS idx_records_source ON records(source);
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    path = str(path or db_path())
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Optional[str] = None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def reset_db(path: Optional[str] = None) -> None:
    """Drop + recreate every table (used by ``minekg link`` for a clean KG)."""
    conn = connect(path)
    try:
        for t in ("entities", "aliases", "edges", "ambiguities", "records"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def write_records(records: list, path: Optional[str] = None) -> None:
    init_db(path)
    conn = connect(path)
    try:
        for r in records:
            if r.record_id is None:
                import hashlib

                r.record_id = hashlib.md5(
                    f"{r.source}|{r.provenance.file}|{r.provenance.line}|{r.raw_row}".encode()
                ).hexdigest()[:12]
            conn.execute(
                """INSERT OR REPLACE INTO records
                   (record_id, source, ts, amount, direction, counterparty,
                    content, raw_row, file, line)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    r.record_id, r.source, r.ts, r.amount, r.direction,
                    r.counterparty, r.content, r.raw_row,
                    r.provenance.file, r.provenance.line,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def write_linkage(result: LinkageResult, path: Optional[str] = None) -> None:
    """Persist entities / aliases / edges / ambiguities from a linkage run."""
    init_db(path)
    conn = connect(path)
    try:
        for ent in result.entities:
            conn.execute(
                """INSERT INTO entities
                   (record_id, type, surface_form, canonical_id, confidence)
                   VALUES (?,?,?,?,?)""",
                (None, ent.type, ent.surface_form, ent.canonical_id, ent.confidence),
            )
        for cid, forms in result.aliases.items():
            for f in forms:
                conn.execute(
                    "INSERT OR IGNORE INTO aliases (canonical_id, surface_form) VALUES (?,?)",
                    (cid, f),
                )
        for record_id, cid in result.edges:
            conn.execute(
                "INSERT INTO edges (record_id, canonical_id) VALUES (?,?)",
                (record_id, cid),
            )
        for amb in result.ambiguities:
            conn.execute(
                """INSERT INTO ambiguities
                   (canonical_id, surface_forms, reason, confidence)
                   VALUES (?,?,?,?)""",
                (
                    amb.canonical_id,
                    json.dumps(amb.surface_forms, ensure_ascii=False),
                    amb.reason,
                    amb.confidence,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def canonical_for_surface(surface: str, path: Optional[str] = None) -> str | None:
    """Look up the canonical_id for a given surface form (any type)."""
    conn = connect(path)
    try:
        row = conn.execute(
            "SELECT canonical_id FROM aliases WHERE surface_form = ? LIMIT 1",
            (surface,),
        ).fetchone()
        if row:
            return row["canonical_id"]
        # fall back to entities table (covers forms not stored as aliases)
        row = conn.execute(
            "SELECT canonical_id FROM entities WHERE surface_form = ? LIMIT 1",
            (surface,),
        ).fetchone()
        return row["canonical_id"] if row else None
    finally:
        conn.close()


def records_for_canonical(canonical_id: str, path: Optional[str] = None) -> list:
    """All records linked to a canonical entity, with provenance."""
    conn = connect(path)
    try:
        rows = conn.execute(
            """SELECT r.* FROM records r
               JOIN edges e ON e.record_id = r.record_id
               WHERE e.canonical_id = ?
               ORDER BY r.ts""",
            (canonical_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def all_canonical_persons(path: Optional[str] = None) -> list:
    conn = connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT canonical_id FROM entities WHERE type = 'person'"
        ).fetchall()
        return [r["canonical_id"] for r in rows]
    finally:
        conn.close()


def link_records(records_path=None, db_path_arg=None, client=None,
                 model: str = "qwen-turbo") -> LinkageResult:
    """Convenience: read records JSONL, NER, link, write KG. Returns result."""
    from . import from_jsonl, ner
    from .linkage import link as do_link

    records = from_jsonl(records_path or __import__("minekg", fromlist=["records_path"]).records_path())
    ner.extract_many(records, client=client, model=model)
    result = do_link(records, client=client)
    write_records(records, db_path_arg)
    write_linkage(result, db_path_arg)
    return result


def load_dashscope_client():
    """Return (client, model) if a DashScope key is configured, else (None, _)."""
    path = config_path()
    if not path.exists():
        return None, "qwen-turbo"
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "qwen-turbo"
    key = cfg.get("dashscope_api_key")
    if not key:
        return None, "qwen-turbo"
    from .ner import make_client

    client, model = make_client(key)
    return client, model
