"""Focused regression tests for the v0.2.0 fixes.

Each test pins one defect found at tag v0.1.0 (see CHANGELOG [0.2.0]):
bank integer amounts, the link/ask --records db contract, the config
--show flag, and the CLI --version surface.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from minekg import Record, Provenance, to_jsonl
from minekg import kg
from minekg.ask import ask
from minekg.sources import bank


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate MINEKG_HOME so tests never touch the user's real ~/.minekg."""
    h = tmp_path / "minekg_home"
    h.mkdir()
    monkeypatch.setenv("MINEKG_HOME", str(h))
    return h


# ---------------------------------------------------------------------------
# m4 — bank whole-number amounts
# ---------------------------------------------------------------------------


class TestBankIntegerAmounts:
    def test_integer_amount_parsed(self):
        records = bank.parse_lines(
            ["2023-01-15 转账 张三 500", "2023-06-01 转账 李四 1200"], "b.txt"
        )
        by_ts = {r.ts: r for r in records}
        assert by_ts["2023-01-15"].amount == 500.0
        assert by_ts["2023-06-01"].amount == 1200.0
        # the number token must no longer leak into the summary content
        assert "500" not in (by_ts["2023-01-15"].content or "")

    def test_comma_grouped_integer_amount_parsed(self):
        records = bank.parse_lines(["2023-02-20 工资 某科技 1,200"], "b.txt")
        assert records[0].amount == 1200.0

    def test_decimal_amounts_unchanged(self):
        records = bank.parse_lines(
            ["2023-01-15 转账 张三 200.00", "2023-04-01 转账 张* 300.00"], "b.txt"
        )
        amounts = sorted(r.amount for r in records)
        assert amounts == [200.0, 300.0]

    def test_long_digit_run_stays_non_money(self):
        # a 12-digit transaction id must not be read as the amount
        assert bank._parse_money("202301150012") is None
        records = bank.parse_lines(
            ["2023-01-15 转账 张三 202301150012"], "b.txt"
        )
        assert records[0].amount is None

    def test_negative_integer_is_expenditure(self):
        records = bank.parse_lines(["2023-01-15 转账 张三 -500"], "b.txt")
        assert records[0].amount == 500.0
        assert records[0].direction == "支"


# ---------------------------------------------------------------------------
# m5 — link/ask --records db contract + fail-soft ask
# ---------------------------------------------------------------------------


def _records_file(tmp_path):
    last_year = datetime.now().year - 1
    recs = [
        Record(source="alipay", raw_row="a1", provenance=Provenance("a", 1),
               counterparty="张三", amount=200.0, ts=f"{last_year}-01-10"),
        Record(source="alipay", raw_row="a2", provenance=Provenance("a", 2),
               counterparty="张三", amount=300.0, ts=f"{last_year}-03-15"),
    ]
    path = tmp_path / "records.jsonl"
    to_jsonl(recs, path)
    return path


class TestRecordsDbContract:
    def test_link_then_ask_with_records(self, tmp_path, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        recs = _records_file(tmp_path)
        runner = CliRunner()

        r = runner.invoke(app, ["link", "--records", str(recs)])
        assert r.exit_code == 0, r.output
        # the KG must land next to the records file (where ask reads it)
        assert (tmp_path / "minekg.db").exists()
        # and the report prints the actual db path (rich may hard-wrap the
        # long temp path across lines, so compare against the unwrapped text)
        assert str(tmp_path / "minekg.db") in r.output.replace("\n", "")

        r = runner.invoke(
            app, ["ask", "和张三相关的所有资金往来合计", "--records", str(recs)]
        )
        assert r.exit_code == 0, r.output
        assert "500" in r.output

    def test_ask_without_kg_returns_guidance(self, tmp_path, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        missing = tmp_path / "nope" / "minekg.db"
        ans = ask("和小张相关的资金往来合计", db_path=missing)
        assert "未在知识图谱" in ans.answer
        assert "minekg link" in ans.note

        runner = CliRunner()
        r = runner.invoke(app, ["ask", "和小张相关的资金往来合计"])
        assert r.exit_code == 0, r.output
        assert "未在知识图谱" in r.output


# ---------------------------------------------------------------------------
# m6 — config --show prints the full key
# ---------------------------------------------------------------------------


class TestConfigShow:
    def _save_key(self, home):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.json").write_text(
            json.dumps({"dashscope_api_key": "sk-abcdef1234567890"}),
            encoding="utf-8",
        )

    def test_show_prints_full_key(self, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        self._save_key(home)
        out = CliRunner().invoke(app, ["config", "--show"]).output
        assert "sk-abcdef1234567890" in out

    def test_default_stays_masked(self, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        self._save_key(home)
        out = CliRunner().invoke(app, ["config"]).output
        assert "sk-abcdef1234567890" not in out
        assert "sk-abc***" in out


# ---------------------------------------------------------------------------
# m7 — CLI --version surface
# ---------------------------------------------------------------------------


class TestVersionSurface:
    def test_cli_version_flag(self, home):
        from typer.testing import CliRunner

        from minekg import __version__
        from minekg.cli import app

        out = CliRunner().invoke(app, ["--version"]).output
        assert __version__ in out
        assert "0.3.0" in out

    def test_version_file_matches_package(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        version_file = (repo_root / "VERSION").read_text(encoding="utf-8").strip()
        from minekg import __version__

        assert version_file == __version__
