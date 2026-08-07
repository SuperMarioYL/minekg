"""End-to-end tests for the MineKG m1 loop (ingest → NER → link → KG → ask).

All tests run on the deterministic rule fallback (no DashScope key needed) so
they pass in a clean CI without network access.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from minekg import ALIPAY, BANK, NOTES, WECHAT, from_jsonl, to_jsonl
from minekg import ingest as ingest_mod
from minekg import kg, ner
from minekg.ask import ask
from minekg.linkage import link, _person_key
from minekg.sources import alipay, bank, notes, wechat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WECHAT_TXT = """2023-01-10 12:30:00 小张
上次借的钱我转回给你了
2023-01-10 12:31:00 我
好的，收到
2023-03-15 09:00:00 小张
周六一起吃饭
"""

NOTES_MD = """# 年度复盘

2023-01-15 和小张在星巴克吃饭，支付宝转了200

去年12月借给小张300，至今未还
"""

BANK_LINES = [
    "招商银行 交易明细",
    "账户 6225 **** 1234",
    "交易日期 摘要 交易对方 金额",
    # multi-line 对手信息 reassembly: amount on the continuation line
    "2023-02-20 工资 某科技",
    "有限公司 5000.00",
    "2023-01-15 转账 张三 200.00",
    "2023-04-01 转账 张* 300.00",
]


def _alipay_csv(last_year: int) -> str:
    return (
        "支付宝(中国)网络技术有限公司 电子客户回单\n"
        "查询时间 2023-01-01 ~ 2023-12-31\n"
        "------------ 交易记录明细 ------------\n"
        "交易号,商家订单号,交易创建时间,付款时间,类型,交易对方,商品名称,"
        "金额（元）,收/支,交易状态\n"
        f"202301001,001,{last_year}-01-10 12:00:00,{last_year}-01-10 12:00:00,"
        "转账,张三,即时到账,200.00,收入,交易成功\n"
        f"202301002,002,{last_year}-03-15 09:00:00,{last_year}-03-15 09:00:00,"
        "转账,张三,转账,300.00,支出,交易成功\n"
        "------------ 交易记录结束 ------------\n"
        "交易笔数 2 笔\n"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate MINEKG_HOME so tests never touch the user's real ~/.minekg."""
    h = tmp_path / "minekg_home"
    h.mkdir()
    monkeypatch.setenv("MINEKG_HOME", str(h))
    return h


@pytest.fixture
def exports(tmp_path):
    last_year = datetime.now().year - 1
    d = tmp_path / "exports"
    d.mkdir()
    (d / "wechat.txt").write_text(WECHAT_TXT, encoding="utf-8")
    (d / "notes.md").write_text(NOTES_MD.replace("2023", str(last_year)).replace(
        "去年12月", f"{last_year}年12月"), encoding="utf-8")
    (d / "alipay.csv").write_text(_alipay_csv(last_year), encoding="utf-8-sig")
    return d


# ---------------------------------------------------------------------------
# Per-adapter unit tests
# ---------------------------------------------------------------------------

class TestAdapters:
    def test_wechat_parse(self, tmp_path):
        p = tmp_path / "wechat.txt"
        p.write_text(WECHAT_TXT, encoding="utf-8")
        records = wechat.parse(p)
        assert len(records) == 3
        speakers = {r.counterparty for r in records}
        assert "小张" in speakers
        assert all(r.source == WECHAT for r in records)
        assert records[0].ts.startswith("2023-01-10")
        assert records[0].provenance.file == str(p)

    def test_alipay_parse(self, tmp_path):
        last_year = datetime.now().year - 1
        p = tmp_path / "alipay.csv"
        p.write_text(_alipay_csv(last_year), encoding="utf-8-sig")
        records = alipay.parse(p)
        assert len(records) == 2
        assert all(r.source == ALIPAY for r in records)
        assert all(r.counterparty == "张三" for r in records)
        amounts = sorted(r.amount for r in records)
        assert amounts == [200.0, 300.0]

    def test_notes_parse(self, tmp_path):
        last_year = datetime.now().year - 1
        p = tmp_path / "notes.md"
        p.write_text(
            NOTES_MD.replace("2023", str(last_year)).replace(
                "去年12月", f"{last_year}年12月"),
            encoding="utf-8",
        )
        records = notes.parse(p)
        assert len(records) >= 2
        assert all(r.source == NOTES for r in records)
        assert any("小张" in r.content for r in records)

    def test_bank_parse_lines_chinese(self):
        records = bank.parse_lines(BANK_LINES, "bank.pdf")
        # 3 transactions (the multi-line 工资/某科技 row must collapse to ONE)
        assert len(records) == 3
        parties = [r.counterparty for r in records]
        assert "张三" in parties
        assert "张*" in parties
        # multi-line reassembly: the 5000.00 amount lands on the 02-20 row,
        # not as a phantom 4th transaction
        txns = {r.ts: r for r in records}
        assert txns["2023-02-20"].amount == 5000.0
        assert txns["2023-01-15"].amount == 200.0
        assert txns["2023-04-01"].amount == 300.0
        # full counter-party name kept (schema note: not masked)
        assert "某科技" in (txns["2023-02-20"].counterparty or "")

    def test_bank_parse_pdf_latin(self, tmp_path):
        # proves the pdfplumber plumbing: a hand-built PDF is read, its text
        # lines are extracted and parse_lines produces records. (Chinese in a
        # base-14-font PDF isn't extractable without an embedded CJK font, so
        # the Chinese parsing logic is covered by test_bank_parse_lines_chinese
        # and the PDF layer is proven here with Latin text.)
        pdfplumber = pytest.importorskip("pdfplumber")
        pdf_bytes = _build_pdf([
            "2023-01-15 Zhangsan Lunch 50.00",
            "2023-02-20 Acmeinc Salary 5000.00",
        ])
        p = tmp_path / "bank.pdf"
        p.write_bytes(pdf_bytes)
        records = bank.parse_pdf(p, pdfplumber_module=pdfplumber)
        assert len(records) == 2
        amounts = sorted(r.amount for r in records)
        assert amounts == [50.0, 5000.0]
        assert records[0].ts == "2023-01-15"


# ---------------------------------------------------------------------------
# NER + linkage
# ---------------------------------------------------------------------------

class TestNerLinkage:
    def test_ner_extracts_person_place_time(self):
        from minekg import Provenance, Record

        r = Record(
            source=NOTES, raw_row="2023-01-15 和小张在星巴克吃饭",
            provenance=Provenance(file="n.md", line=1),
            content="和小张在星巴克吃饭",
        )
        ents = ner.extract_rule(r)
        types = {e.type for e in ents}
        assert "person" in types
        forms = [e.surface_form for e in ents if e.type == "person"]
        assert "小张" in forms

    def test_person_key_normalisation(self):
        assert _person_key("小张")[0] == "surname"
        assert _person_key("张三")[0] == "full"
        assert _person_key("张*")[0] == "surname"

    def test_linkage_cross_source_canonical(self):
        from minekg import Entity, Provenance, Record

        records = [
            Record(source=WECHAT, raw_row="m1", provenance=Provenance("w", 1),
                   counterparty="小张", content="和小张吃饭"),
            Record(source=ALIPAY, raw_row="a1", provenance=Provenance("a", 1),
                   counterparty="张三", content="转账", amount=200.0),
            Record(source=BANK, raw_row="b1", provenance=Provenance("b", 1),
                   counterparty="张*", content="转账", amount=300.0),
        ]
        ner.extract_many(records)
        result = link(records)
        canonicals = {e.canonical_id for e in result.entities if e.type == "person"}
        # 张三 (full) is the resolved canonical; 小张 and 张* merge into it
        assert "person:张三" in canonicals
        aliases = result.aliases.get("person:张三", [])
        assert "小张" in aliases and "张三" in aliases and "张*" in aliases
        # the surname-only merges are flagged for review
        assert any(a.canonical_id == "person:张三" for a in result.ambiguities)


# ---------------------------------------------------------------------------
# KG + ask
# ---------------------------------------------------------------------------

class TestKgAsk:
    def test_kg_roundtrip(self, home):
        from minekg import Provenance, Record

        records = [
            Record(source=ALIPAY, raw_row="a1", provenance=Provenance("a", 1),
                   counterparty="张三", amount=200.0, ts="2023-01-10"),
        ]
        ner.extract_many(records)
        result = link(records)
        kg.reset_db()
        kg.write_records(records)
        kg.write_linkage(result)
        rows = kg.records_for_canonical("person:张三")
        assert len(rows) == 1
        assert rows[0]["amount"] == 200.0

    def test_ask_sum_cross_source(self, home, exports):
        # full m1 loop on wechat + alipay + notes (all Chinese)
        records = ingest_mod.ingest(exports, home / "records.jsonl")
        ner.extract_many(records)
        result = link(records)
        kg.reset_db()
        kg.write_records(records)
        kg.write_linkage(result)

        ans = ask("和小张相关的所有资金往来合计", home / "minekg.db")
        # alipay 张三 (=小张 via surname) sums to 200 + 300
        assert ans.canonical_id == "person:张三"
        assert ans.value == pytest.approx(500.0)
        assert ans.unit == "元"
        # provenance rows come from the alipay source
        srcs = {r["source"] for r in ans.rows}
        assert "alipay" in srcs
        assert all(r["canonical_id"] == "person:张三" for r in ans.rows)

    def test_ask_last_year_scope(self, home, exports):
        records = ingest_mod.ingest(exports, home / "records.jsonl")
        ner.extract_many(records)
        result = link(records)
        kg.reset_db()
        kg.write_records(records)
        kg.write_linkage(result)

        ans = ask("去年和小张相关的所有资金往来合计", home / "minekg.db")
        last_year = datetime.now().year - 1
        assert ans.value == pytest.approx(500.0)
        assert all(str(last_year) in (r["ts"] or "") for r in ans.rows if r["ts"])


# ---------------------------------------------------------------------------
# Full pipeline incl. bank records (parse_lines Chinese path)
# ---------------------------------------------------------------------------

class TestPipelineWithBank:
    def test_pipeline_includes_bank_records(self, home, exports):
        from minekg import Provenance, Record

        records = ingest_mod.ingest(exports, home / "records.jsonl")
        # add bank records via parse_lines (Chinese — the schema-verified path)
        bank_records = bank.parse_lines(BANK_LINES, "bank.pdf")
        # normalise bank dates to "last year" so 去年 scope matches
        last_year = datetime.now().year - 1
        for r in bank_records:
            if r.ts:
                r.ts = r.ts.replace("2023", str(last_year))
        records += bank_records

        ner.extract_many(records)
        result = link(records)
        kg.reset_db()
        kg.write_records(records)
        kg.write_linkage(result)

        ans = ask("去年和小张相关的所有资金往来合计", home / "minekg.db")
        # alipay 张三 (200+300) + bank 张三 (200) = 700; bank 张* (300) also
        # merges into person:张三, so total = 200+300+200+300 = 1000
        assert ans.canonical_id == "person:张三"
        assert ans.value == pytest.approx(1000.0)
        srcs = {r["source"] for r in ans.rows}
        assert {"alipay", "bank"} <= srcs


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------

class TestCli:
    def test_ingest_link_ask(self, home, exports):
        from typer.testing import CliRunner

        from minekg.cli import app

        runner = CliRunner()
        r = runner.invoke(app, ["ingest", str(exports)])
        assert r.exit_code == 0, r.output
        assert "records" in r.output

        r = runner.invoke(app, ["link"])
        assert r.exit_code == 0, r.output
        assert "person:张三" in r.output

        r = runner.invoke(app, ["ask", "和小张相关的所有资金往来合计"])
        assert r.exit_code == 0, r.output
        assert "500" in r.output

    def test_config_show(self, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        runner = CliRunner()
        r = runner.invoke(app, ["config"])
        assert r.exit_code == 0, r.output
        assert "qwen key" in r.output


# ---------------------------------------------------------------------------
# Minimal PDF generator (Latin text only — for the pdfplumber plumbing test)
# ---------------------------------------------------------------------------

def _build_pdf(lines: list) -> bytes:
    ops = ["BT", "/F1 12 Tf", "72 700 Td", "16 TL"]
    for i, ln in enumerate(lines):
        safe = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i == 0:
            ops.append(f"({safe}) Tj")
        else:
            ops.append("T*")
            ops.append(f"({safe}) Tj")
    ops.append("ET")
    content = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n
        out += body
        out += b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1, xref_pos)
    return bytes(out)
