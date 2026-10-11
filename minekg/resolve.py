"""Interactive resolution of flagged entity pairs — the human review gate.

``minekg link`` applies cross-source merges tentatively and reports the
risky ones (surname-only / masked forms merged into a full name) in the
``ambiguities`` table. This module is the minimal rich-driven TUI that
actiones that report:

* **[y] 确认合并** — the merge stands; the ambiguity row is deleted.
* **[n] 拆分独立** — the first surface form moves back out to its own
  ``person:<surface>`` canonical: its entities/aliases rows are re-pointed
  and the edges of records whose text mentions the split form (but not the
  canonical full name) are re-linked. Records mentioning both forms stay
  merged — a deterministic text-match heuristic over the existing schema,
  not a schema change.
* **[s] 跳过** — keep the pair flagged for a later run.
* **[q] 退出** — stop reviewing; remaining pairs stay flagged.

The prompt/input functions are injectable so CI and scripted runs are
deterministic (EOF ends the review gracefully with nothing changed).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

from . import kg

_CHOICES = ("y", "n", "s", "q")


def _stdin_input(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        # scripted / piped runs end the review instead of spinning
        return "q"


def _load_ambiguities(db) -> list:
    conn = kg.connect(db)
    try:
        rows = conn.execute(
            "SELECT id, canonical_id, surface_forms, reason, confidence "
            "FROM ambiguities ORDER BY id"
        ).fetchall()
        out = []
        for r in rows:
            try:
                forms = json.loads(r["surface_forms"])
            except (json.JSONDecodeError, TypeError):
                forms = [r["surface_forms"]]
            out.append(
                {
                    "id": r["id"],
                    "canonical_id": r["canonical_id"],
                    "surface_forms": forms,
                    "reason": r["reason"],
                    "confidence": r["confidence"],
                }
            )
        return out
    finally:
        conn.close()


def _accept(db, amb_id: int) -> None:
    """Keep the proposed merge; the pair is reviewed and final."""
    conn = kg.connect(db)
    try:
        conn.execute("DELETE FROM ambiguities WHERE id = ?", (amb_id,))
        conn.commit()
    finally:
        conn.close()


def _split(db, amb: dict) -> int:
    """Move the first surface form out of the merged canonical.

    Returns the number of record edges re-pointed to the new canonical.
    """
    old = amb["canonical_id"]
    form = amb["surface_forms"][0]
    keep_name = amb["surface_forms"][-1]
    new = f"person:{form}"
    conn = kg.connect(db)
    try:
        conn.execute(
            "UPDATE entities SET canonical_id = ? WHERE canonical_id = ? AND surface_form = ?",
            (new, old, form),
        )
        conn.execute(
            "DELETE FROM aliases WHERE canonical_id = ? AND surface_form = ?",
            (old, form),
        )
        conn.execute(
            "INSERT OR IGNORE INTO aliases (canonical_id, surface_form) VALUES (?,?)",
            (new, form),
        )
        rows = conn.execute(
            """SELECT DISTINCT r.record_id, r.raw_row, r.content FROM records r
               JOIN edges e ON e.record_id = r.record_id
               WHERE e.canonical_id = ?""",
            (old,),
        ).fetchall()
        moved = 0
        for row in rows:
            text = f"{row['raw_row'] or ''} {row['content'] or ''}"
            # records that only mention the split form follow it out; records
            # that also mention the canonical full name stay merged
            if form in text and keep_name not in text:
                cur = conn.execute(
                    "UPDATE edges SET canonical_id = ? WHERE record_id = ? AND canonical_id = ?",
                    (new, row["record_id"], old),
                )
                moved += max(cur.rowcount, 0)
        conn.execute("DELETE FROM ambiguities WHERE id = ?", (amb["id"],))
        conn.commit()
        return moved
    finally:
        conn.close()


def run(
    db,
    input_fn: Callable[[str], str] | None = None,
    echo: Callable[[str], None] | None = None,
) -> list:
    """Review every flagged pair in the KG at ``db``.

    ``input_fn(prompt) -> str`` and ``echo(text)`` are injectable; defaults
    read stdin and print nothing. Returns a list of
    ``(ambiguity, decision)`` with decision ∈ {"accepted", "split", "skipped"}.
    """
    input_fn = input_fn or _stdin_input
    echo = echo or (lambda _text: None)

    ambs = _load_ambiguities(db)
    if not ambs:
        echo("[dim]no ambiguous links — nothing to review[/dim]")
        return []

    decisions: list = []
    for amb in ambs:
        echo(
            f"[bold magenta]{amb['canonical_id']}[/bold magenta]  "
            f"{' ≈ '.join(amb['surface_forms'])}"
        )
        echo(f"  {amb['reason']}  (confidence {amb['confidence']})")
        choice = ""
        while choice not in _CHOICES:
            raw = input_fn(
                "确认同一人? [y]确认合并 / [n]拆分独立 / [s]跳过 / [q]退出: "
            ).strip().lower()
            if raw in _CHOICES:
                choice = raw
            else:
                echo("[yellow]请输入 y / n / s / q[/yellow]")

        if choice == "y":
            _accept(db, amb["id"])
            decisions.append((amb, "accepted"))
            echo("  [green]确认合并[/green]")
        elif choice == "n":
            moved = _split(db, amb)
            decisions.append((amb, "split"))
            echo(
                f"  [green]拆分独立 → person:{amb['surface_forms'][0]}"
                f"（{moved} 条记录移出）[/green]"
            )
        elif choice == "s":
            decisions.append((amb, "skipped"))
            echo("  [yellow]已跳过，保持待复核[/yellow]")
        else:  # q
            echo("[dim]已退出，剩余标记保持待复核[/dim]")
            break
    return decisions
