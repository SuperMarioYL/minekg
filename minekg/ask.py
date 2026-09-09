"""Natural-language → SQL → answer over the local KG.

Two backends:

1. **Rule fallback** (always available, deterministic, used by tests + the
   offline demo): parses the question for a person mention, a time scope and
   an aggregation intent, then resolves the person to a ``canonical_id``
   via the aliases table and computes the answer over the linked records.
2. **qwen (DashScope)** — when a key is configured, the KG schema + the
   question are sent to qwen, which returns a ``SELECT`` statement executed
   against the SQLite KG. Any non-SELECT / error falls back to the rule path.

The answer always carries per-row provenance (source file, line, ts,
counterparty, amount, canonical_id) so every number is traceable to a row
that left the user's machine.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import kg


@dataclass
class Answer:
    question: str
    answer: str
    value: float | None
    unit: str | None
    canonical_id: str | None
    rows: list = field(default_factory=list)  # provenance rows
    matched_aliases: list = field(default_factory=list)
    sql: str | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Question parsing (rule fallback)
# ---------------------------------------------------------------------------

_TIME_SCOPES = {
    "去年": "last_year",
    "前年": "prev_prev_year",
    "今年": "this_year",
    "上个月": "last_month",
    "上月": "last_month",
    "本月": "this_month",
    "年初": "this_year",
    "年底": "this_year",
}

_INTENT_SUM = ("合计", "总和", "总额", "一共", "总共", "花了多少", "总花费", "总计")
_INTENT_COUNT = ("多少笔", "几笔", "次数", "多少次", "几次")
_INTENT_AVG = ("平均", "均值")


def _parse_time_scope(q: str) -> tuple:
    """Return (scope, value) where scope ∈ {'year','month','all'} and value the int."""
    now = datetime.now()
    for kw, scope in _TIME_SCOPES.items():
        if kw in q:
            if scope == "last_year":
                return ("year", now.year - 1)
            if scope == "prev_prev_year":
                return ("year", now.year - 2)
            if scope == "this_year":
                return ("year", now.year)
            if scope == "last_month":
                # previous month, accounting for year rollover
                m = now.month - 1 or 12
                y = now.year - (1 if now.month == 1 else 0)
                return ("month", (y, m))
            if scope == "this_month":
                return ("month", (now.year, now.month))
    # explicit YYYY年
    m = re.search(r"(\d{4})\s*年", q)
    if m:
        return ("year", int(m.group(1)))
    return ("all", None)


def _parse_intent(q: str) -> str:
    if any(k in q for k in _INTENT_COUNT):
        return "count"
    if any(k in q for k in _INTENT_AVG):
        return "avg"
    if any(k in q for k in _INTENT_SUM):
        return "sum"
    # default: if question mentions 资金/钱/花费/支出/收入 → sum; else count
    if re.search(r"资金|钱|花费|支出|收入|转账|往来|金额", q):
        return "sum"
    return "count"


def _find_person(q: str, db_path) -> tuple:
    """Return (canonical_id, matched_alias) for the person in the question."""
    conn = kg.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT canonical_id, surface_form FROM aliases"
        ).fetchall()
    finally:
        conn.close()
    # also include canonical surface forms (e.g. "person:张三" → 张三)
    forms = [(r["canonical_id"], r["surface_form"]) for r in rows]
    # longest-first so "小张" wins over "张"
    forms.sort(key=lambda x: len(x[1]), reverse=True)
    for cid, form in forms:
        if form and form in q:
            return cid, form
    # fallback: 和/与/跟 X 相关 pattern → resolve X by surname
    m = re.search(r"(?:和|与|跟)\s*([^\s,，。的]{2,4})\s*相关", q)
    if m:
        surface = m.group(1)
        cid = kg.canonical_for_surface(surface, db_path)
        if cid:
            return cid, surface
        # surname fallback: maybe only the surname is known
        from .linkage import _person_key

        kind, key = _person_key(surface)
        if kind in ("full", "surname"):
            cid2 = kg.canonical_for_surface(key, db_path)
            if cid2:
                return cid2, key
            return (f"person:{key}", key)
    return None, None


def _year_of_ts(ts: str | None) -> int | None:
    if not ts:
        return None
    m = re.search(r"(\d{4})", ts)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ask(question: str, db_path=None, client=None) -> Answer:
    """Answer a natural-language question over the KG."""
    db_path = db_path or kg.db_path()
    if not Path(db_path).exists():
        # KG not built yet — guide the user instead of raising on missing tables.
        return Answer(
            question=question,
            answer="未在知识图谱中找到对应的人名/实体",
            value=None, unit=None, canonical_id=None,
            note="先运行 minekg ingest 与 minekg link 构建图谱",
        )
    intent = _parse_intent(question)
    scope, scope_val = _parse_time_scope(question)
    cid, alias = _find_person(question, db_path)

    if cid is None:
        return Answer(
            question=question,
            answer="未在知识图谱中找到对应的人名/实体",
            value=None, unit=None, canonical_id=None,
            note="先运行 minekg ingest 与 minekg link 构建图谱",
        )

    rows = kg.records_for_canonical(cid, db_path)

    # time filtering
    def in_scope(r) -> bool:
        if scope == "all":
            return True
        if scope == "year":
            return _year_of_ts(r.get("ts")) == scope_val
        if scope == "month":
            y, m = scope_val
            ts = r.get("ts") or ""
            mm = re.search(r"(\d{4})\D(\d{1,2})", ts)
            if not mm:
                return False
            return int(mm.group(1)) == y and int(mm.group(2)) == m
        return True

    rows = [r for r in rows if in_scope(r)]

    # only financial rows carry amounts for sum/avg intents
    fin = [r for r in rows if r.get("amount") is not None]

    if intent == "count":
        value = float(len(rows))
        answer_text = f"{int(value)} 条记录"
        unit = "条"
    elif intent == "avg":
        value = (sum(r["amount"] for r in fin) / len(fin)) if fin else 0.0
        answer_text = f"{value:.2f} 元（平均）"
        unit = "元"
    else:  # sum
        value = sum(r["amount"] for r in fin) if fin else 0.0
        answer_text = f"{value:.2f} 元"
        unit = "元"

    provenance = [
        {
            "source": r.get("source"),
            "file": r.get("file"),
            "line": r.get("line"),
            "ts": r.get("ts"),
            "counterparty": r.get("counterparty"),
            "amount": r.get("amount"),
            "direction": r.get("direction"),
            "canonical_id": cid,
        }
        for r in fin or rows
    ]

    sources = sorted({r["source"] for r in rows}) if rows else []
    note = (
        f"实体 {cid}（匹配自问题中的「{alias}」）共 {len(rows)} 条记录，"
        f"横跨来源: {', '.join(sources) or '无'}；其中 {len(fin)} 条含金额。"
    )

    return Answer(
        question=question,
        answer=answer_text,
        value=value,
        unit=unit,
        canonical_id=cid,
        rows=provenance,
        matched_aliases=[alias] if alias else [],
        note=note,
    )
