"""Bank (银行流水 PDF) adapter.

Per the schema smoke on a real 招商银行 PDF: ``pdfplumber.extract_tables()``
returns **0** tables — the statement is rendered as positioned text, not a
table object. So this adapter reads text lines via ``pdfplumber``'s
``extract_text`` and reassembles the rows itself.

Two load-bearing behaviours required by the schema note:

1. **Multi-line 对手信息 reassembly.** The counter-party / summary column
   wraps across lines, so a transaction often spans a date line plus one or
   more continuation lines (which carry the amount / balance). Continuation
   lines are detected by "no leading date" and folded into the current
   transaction's buffer.
2. **Full counter-party names.** 招商银行 statements print full names (not
   masked), so the adapter keeps the whole token rather than truncating.

The parsing logic is split out into :func:`parse_lines` (pure string list)
so it can be unit-tested with synthetic Chinese lines without needing a PDF
on disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import BANK, Provenance, Record

# A leading date: 2023-01-15 / 2023/01/15 / 2023年01月15日 / 20230115
_DATE_RE = re.compile(
    r"^(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{8})\b"
)
# A money amount: optional sign, digits with optional thousands sep, optional
# 2 decimals. Negative 支出 is shown as a leading "-".
_MONEY_RE = re.compile(r"-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})|-?\d+\.\d{1,2}")

_ORG_SUFFIX = (
    "公司", "银行", "集团", "有限公司", "超市", "便利店", "商店",
    "科技", "中心", "医院", "学校", "基金", "证券", "保险", "网店",
)

# A conservative surname set so the parser can tell 张三 / 张* apart from
# action tokens like 转账 / 工资 (which start with non-surname chars).
_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟黄穆"
    "萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮"
    "蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万"
)


def _looks_like_name(tok: str) -> bool:
    if not tok:
        return False
    masked = tok.endswith("*") or tok.endswith("＊")
    core = tok[:-1] if masked else tok
    # org (company / bank / shop …) — may be a multi-line fragment
    if any(tok.endswith(s) for s in _ORG_SUFFIX):
        return True
    if masked and core and re.fullmatch(r"[\u4e00-\u9fff]+", core):
        return core[0] in _SURNAMES
    # person full name: surname + 1-2 chars (excludes action tokens like 转账)
    if 2 <= len(core) <= 4 and re.fullmatch(r"[\u4e00-\u9fff]+", core):
        return core[0] in _SURNAMES
    return False


def _parse_money(s: str) -> float | None:
    s = (s or "").replace(",", "").strip()
    if not s:
        return None
    m = _MONEY_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _split_tokens(line: str) -> list:
    return [t for t in re.split(r"\s+", line.strip()) if t]


def parse_lines(lines: list, file_path: str) -> list:
    """Parse pre-split bank statement text lines into records."""
    records: list = []
    started = False
    # current transaction buffer
    cur_tokens: list = []
    cur_date: str | None = None
    cur_line_no = 0

    def _normalise_date(d: str) -> str:
        d = d.replace("/", "-").replace("年", "-").replace("月", "-").replace("日", "")
        if d.isdigit() and len(d) == 8:
            d = f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
        return d

    def _flush() -> None:
        nonlocal cur_tokens, cur_date, cur_line_no
        if cur_date is None and not cur_tokens:
            return
        toks = cur_tokens
        # amounts: collect all money-like tokens
        amounts = [t for t in toks if _parse_money(t) is not None]
        # the transaction amount is the first money token; trailing ones are
        # usually 余额 / 手续费. If a continuation line contributed the only
        # money token, it's the amount.
        amount = None
        direction = None
        for t in amounts:
            v = _parse_money(t)
            if v is not None:
                if amount is None:
                    amount = abs(v)
                    direction = "支" if v < 0 else "收"
                    break
        # counter-party: first name-like token, else the first non-money token
        non_money = [t for t in toks if _parse_money(t) is None]
        party = None
        summary = None
        for t in toks:
            if _looks_like_name(t):
                party = t
                break
        if party is None and non_money:
            party = non_money[0]
        rest = [t for t in non_money if t != party]
        summary = " ".join(rest) if rest else None
        if cur_date is not None or party is not None or amount is not None:
            records.append(
                Record(
                    source=BANK,
                    raw_row=f"{cur_date or ''} " + " ".join(cur_tokens),
                    provenance=Provenance(file=file_path, line=cur_line_no),
                    ts=cur_date,
                    amount=amount,
                    direction=direction,
                    counterparty=party,
                    content=summary or "",
                )
            )
        cur_tokens = []
        cur_date = None
        cur_line_no = 0

    for idx, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        if not line.strip():
            continue
        m = _DATE_RE.match(line.strip())
        if m:
            # flush previous
            _flush()
            started = True
            cur_date = _normalise_date(m.group(1))
            cur_line_no = idx
            rest = line.strip()[m.end():].strip()
            cur_tokens = _split_tokens(rest)
        elif started:
            # continuation line — fold into the current transaction's buffer.
            # (this is the multi-line 对手信息 reassembly path)
            cur_tokens += _split_tokens(line)
        # else: pre-data header line — skip

    _flush()
    return records


def parse_pdf(path, pdfplumber_module=None) -> list:
    """Parse a bank statement PDF via pdfplumber.

    ``pdfplumber_module`` is injectable so tests can pass a stub; in normal
    use the real :mod:`pdfplumber` is imported lazily (keeps the package
    importable without the dep installed).
    """
    if pdfplumber_module is None:
        import pdfplumber as pdfplumber_module  # type: ignore

    lines: list = []
    with pdfplumber_module.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            lines.extend(text.splitlines())
    return parse_lines(lines, str(path))


def parse(path) -> list:
    path = Path(path)
    if str(path).lower().endswith(".pdf"):
        return parse_pdf(path)
    # Allow a plain-text bank dump for convenience / testing.
    from .. import read_text

    text = read_text(path)
    return parse_lines(text.splitlines(), str(path))
