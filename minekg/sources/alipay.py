"""Alipay (支付宝) bill CSV adapter.

Alipay's personal transaction export is a CSV with a metadata header (title +
blank lines), a real header row, data rows and a footer summary. Column
names differ slightly between Alipay versions, so the parser locates the
header row by looking for a 交易对方 / 对方 column and a 金额 column, then maps
the remaining columns by name.

Encoding is auto-detected (支付宝 exports are commonly GBK / GB18030 /
UTF-8-BOM).
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from .. import ALIPAY, Provenance, Record, read_text

_AMOUNT_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _parse_amount(raw: str) -> float | None:
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    s = s.replace(",", "").replace("¥", "").replace("￥", "").strip()
    m = _AMOUNT_RE.search(s)
    if not m:
        return None
    try:
        return abs(float(m.group(0)))
    except ValueError:
        return None


def _norm_header(h: str) -> str:
    h = h or ""
    h = h.replace("（", "(").replace("）", ")")  # full-width → half-width parens
    h = h.replace("/", "")  # 收/支 → 收支 so column lookup is robust
    return re.sub(r"\s+", "", h)


def parse_lines(lines: list, file_path: str) -> list:
    """Parse pre-split CSV lines into Alipay records."""
    # Use csv to honour quoted fields.
    reader = csv.reader(io.StringIO("\n".join(lines)))
    rows = [r for r in reader]

    # find the header row
    header_idx = None
    col = {}
    for i, row in enumerate(rows):
        norm = [_norm_header(c) for c in row]
        joined = "|".join(norm)
        if ("交易对方" in joined or "对方" in joined) and "金额" in joined:
            header_idx = i
            for j, name in enumerate(norm):
                col[name] = j
            break

    records: list = []
    if header_idx is None:
        return records

    def find_col(*names) -> int | None:
        for n in names:
            if n in col:
                return col[n]
        return None

    i_party = find_col("交易对方", "对方", "对方账户", "交易对方账户")
    i_amt = find_col("金额(元)", "金额", "金额（元）")
    i_time = find_col("交易创建时间", "付款时间", "交易时间")
    i_goods = find_col("商品名称", "商品", "商品说明")
    i_type = find_col("类型", "交易分类")
    i_dir = find_col("收/支", "收支", "资金状态")
    i_status = find_col("交易状态", "状态")

    for r in range(header_idx + 1, len(rows)):
        row = rows[r]
        if not row or all(not c.strip() for c in row):
            continue
        first = row[0].strip() if row else ""
        # footer guards
        if first.startswith("---") or "交易笔数" in first or "本期共" in first:
            break

        party = row[i_party].strip() if i_party is not None and i_party < len(row) else ""
        amt_raw = row[i_amt].strip() if i_amt is not None and i_amt < len(row) else ""
        amount = _parse_amount(amt_raw)
        ts = row[i_time].strip() if i_time is not None and i_time < len(row) else ""
        goods = row[i_goods].strip() if i_goods is not None and i_goods < len(row) else ""
        ctype = row[i_type].strip() if i_type is not None and i_type < len(row) else ""
        direction = row[i_dir].strip() if i_dir is not None and i_dir < len(row) else ""
        status = row[i_status].strip() if i_status is not None and i_status < len(row) else ""

        # Skip rows that are clearly not transactions (no party and no amount)
        if not party and amount is None:
            continue
        # Ignore refused / failed rows from the total flow
        if status and ("失败" in status or "关闭" in status):
            continue

        content_parts = [p for p in (goods, ctype) if p]
        records.append(
            Record(
                source=ALIPAY,
                raw_row=",".join(row),
                provenance=Provenance(file=file_path, line=header_idx + 1 + (r - header_idx)),
                ts=ts or None,
                amount=amount,
                direction=direction or None,
                counterparty=party or None,
                content=" | ".join(content_parts) if content_parts else "",
            )
        )
    return records


def parse(path) -> list:
    path = Path(path)
    text = read_text(path)
    lines = text.splitlines()
    return parse_lines(lines, str(path))
