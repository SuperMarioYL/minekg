"""Focused regression tests for the v0.3.0 fixes.

Each test pins one defect found at tag v0.2.0 (see CHANGELOG [0.3.0]):
the fabricated-unknown-person ask answer, the ingest missing-dir crash,
the release workflow shipping zero assets, the resolve stub (plan m3
contract), and the version/demo-doc lockstep surfaces.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from minekg import ALIPAY, WECHAT, Provenance, Record, __version__, to_jsonl
from minekg import ask as ask_mod
from minekg import kg, ner
from minekg.ask import ask
from minekg.linkage import link

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Isolate MINEKG_HOME so tests never touch the user's real ~/.minekg."""
    h = tmp_path / "minekg_home"
    h.mkdir()
    monkeypatch.setenv("MINEKG_HOME", str(h))
    return h


def _zhang_records():
    """Two alipay rows for 张三 plus one wechat row for 小张 (same person)."""
    last_year = datetime.now().year - 1
    return [
        Record(source=ALIPAY, raw_row="a1", provenance=Provenance("a", 1),
               counterparty="张三", amount=200.0, ts=f"{last_year}-01-10",
               content="转账"),
        Record(source=ALIPAY, raw_row="a2", provenance=Provenance("a", 2),
               counterparty="张三", amount=300.0, ts=f"{last_year}-03-15",
               content="转账"),
        Record(source=WECHAT, raw_row="m1", provenance=Provenance("w", 1),
               counterparty="小张", content="和小张吃饭"),
    ]


def _build_kg(tmp_path):
    """Build a linked KG next to a records.jsonl; returns (records, db)."""
    records_path = tmp_path / "records.jsonl"
    db = tmp_path / "minekg.db"
    records = _zhang_records()
    to_jsonl(records, records_path)
    ner.extract_many(records)
    result = link(records)
    kg.reset_db(db)
    kg.write_records(records, db)
    kg.write_linkage(result, db)
    return records_path, db


# ---------------------------------------------------------------------------
# m8 — ask returns the guidance answer for a person the KG has never seen
# ---------------------------------------------------------------------------


class TestAskUnknownPerson:
    def test_guidance_for_unknown_person(self, tmp_path):
        _, db = _build_kg(tmp_path)
        ans = ask("和王五相关的所有资金往来合计", db_path=db)
        assert "未在知识图谱" in ans.answer
        assert ans.canonical_id is None
        assert "minekg link" in ans.note
        # no fabricated entity line
        assert "王五" not in (ans.canonical_id or "")

    def test_known_person_still_answered(self, tmp_path):
        _, db = _build_kg(tmp_path)
        ans = ask("和张三相关的所有资金往来合计", db_path=db)
        assert ans.canonical_id == "person:张三"
        assert ans.value == pytest.approx(500.0)

    def test_ask_docstring_no_longer_claims_model_sql(self):
        doc = ask_mod.__doc__ or ""
        assert "SELECT" not in doc


# ---------------------------------------------------------------------------
# m9 — ingest on a missing directory fails with a friendly error
# ---------------------------------------------------------------------------


class TestIngestMissingDir:
    def test_missing_dir_friendly_error(self, tmp_path, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        result = CliRunner().invoke(app, ["ingest", str(tmp_path / "missing")])
        assert result.exit_code == 1
        # typer.Exit(1) surfaces as SystemExit — anything else is a raw crash
        assert isinstance(result.exception, SystemExit)
        assert "未找到目录" in result.output
        assert "Traceback" not in result.output

    def test_existing_dir_still_works(self, tmp_path, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        d = tmp_path / "exports"
        d.mkdir()
        (d / "wechat.txt").write_text(
            "2023-01-10 12:30:00 小张\n上次借的钱我转回给你了\n", encoding="utf-8"
        )
        result = CliRunner().invoke(app, ["ingest", str(d)])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# m10 — release workflow attaches dist artifacts to the GitHub Release
# ---------------------------------------------------------------------------


class TestReleaseWorkflow:
    def test_release_workflow_uploads_assets(self):
        text = (REPO_ROOT / ".github/workflows/release.yml").read_text("utf-8")
        assert "gh release upload" in text
        assert "--clobber" in text
        # create-or-view guard so the upload never races a missing release
        assert "gh release view" in text and "gh release create" in text


# ---------------------------------------------------------------------------
# m11 — minimal minekg resolve TUI (plan §4 / m3 contract)
# ---------------------------------------------------------------------------


class TestResolveTui:
    def _ambiguity_count(self, db) -> int:
        conn = kg.connect(db)
        try:
            return conn.execute("SELECT COUNT(*) FROM ambiguities").fetchone()[0]
        finally:
            conn.close()

    def test_accept_merge(self, tmp_path):
        from typer.testing import CliRunner

        from minekg.cli import app

        records_path, db = _build_kg(tmp_path)
        assert self._ambiguity_count(db) == 1

        result = CliRunner().invoke(
            app, ["resolve", "--records", str(records_path)], input="y\n"
        )
        assert result.exit_code == 0, result.output
        assert self._ambiguity_count(db) == 0
        aliases = kg.connect(db).execute(
            "SELECT surface_form FROM aliases WHERE canonical_id = 'person:张三'"
        ).fetchall()
        assert {"小张", "张三"} == {r["surface_form"] for r in aliases}

    def test_reject_splits_surface_out(self, tmp_path):
        from typer.testing import CliRunner

        from minekg.cli import app

        records_path, db = _build_kg(tmp_path)

        result = CliRunner().invoke(
            app, ["resolve", "--records", str(records_path)], input="n\n"
        )
        assert result.exit_code == 0, result.output
        assert self._ambiguity_count(db) == 0

        conn = kg.connect(db)
        try:
            xiao = {r["surface_form"] for r in conn.execute(
                "SELECT surface_form FROM aliases WHERE canonical_id = 'person:小张'"
            ).fetchall()}
            zhang = {r["surface_form"] for r in conn.execute(
                "SELECT surface_form FROM aliases WHERE canonical_id = 'person:张三'"
            ).fetchall()}
            assert "小张" in xiao
            assert "张三" in zhang and "小张" not in zhang
            # the wechat-only record follows 小张 out; the alipay rows stay
            xiao_records = conn.execute(
                """SELECT COUNT(*) FROM edges WHERE canonical_id = 'person:小张'"""
            ).fetchone()[0]
            zhang_records = conn.execute(
                """SELECT COUNT(*) FROM edges WHERE canonical_id = 'person:张三'"""
            ).fetchone()[0]
        finally:
            conn.close()
        assert xiao_records == 1
        assert zhang_records == 2

    def test_empty_input_preserves_ambiguities(self, tmp_path):
        from typer.testing import CliRunner

        from minekg.cli import app

        records_path, db = _build_kg(tmp_path)
        result = CliRunner().invoke(
            app, ["resolve", "--records", str(records_path)], input=""
        )
        assert result.exit_code == 0, result.output
        assert self._ambiguity_count(db) == 1

    def test_nothing_to_review(self, tmp_path):
        from typer.testing import CliRunner

        from minekg.cli import app

        records_path = tmp_path / "records.jsonl"
        db = tmp_path / "minekg.db"
        records = [
            Record(source=ALIPAY, raw_row="a1", provenance=Provenance("a", 1),
                   counterparty="张三", amount=200.0, ts="2025-01-10"),
        ]
        to_jsonl(records, records_path)
        ner.extract_many(records)
        result = link(records)
        kg.reset_db(db)
        kg.write_records(records, db)
        kg.write_linkage(result, db)

        out = CliRunner().invoke(
            app, ["resolve", "--records", str(records_path)]
        )
        assert out.exit_code == 0, out.output
        assert "nothing to review" in out.output


# ---------------------------------------------------------------------------
# m12 — v0.3.0 version surfaces in lockstep + recorded-demo truth
# ---------------------------------------------------------------------------


class TestVersionBump:
    def test_cli_version_flag(self, home):
        from typer.testing import CliRunner

        from minekg.cli import app

        out = CliRunner().invoke(app, ["--version"]).output
        assert "0.3.0" in out
        assert __version__ in out

    def test_version_surfaces_lockstep(self):
        from minekg import __version__ as v

        assert v == "0.3.0"
        assert (REPO_ROOT / "VERSION").read_text("utf-8").strip() == v
        pyproject = (REPO_ROOT / "pyproject.toml").read_text("utf-8")
        m = re.search(r'^version = "([^"]+)"', pyproject, re.M)
        assert m and m.group(1) == v

    def test_site_json_version(self):
        site = json.loads((REPO_ROOT / "web/site.json").read_text("utf-8"))
        assert site["meta"]["implementation_version"] == "0.3.0"
        assert "v0.3.0" in site["footer"]["tag"]["zh"]
        assert "v0.3.0" in site["footer"]["tag"]["en"]

    def test_demo_results_matches_live_run(self):
        demo = json.loads(
            (REPO_ROOT / "docs/demo-results.json").read_text("utf-8")
        )
        assert demo["version"] == __version__
        proc = subprocess.run(
            [sys.executable, "examples/presentation_demo.py"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        recorded = json.loads(demo["steps"][0]["output"])
        assert json.loads(proc.stdout) == recorded

    def test_readme_demo_mentions_current_version(self):
        zh = (REPO_ROOT / "README.md").read_text("utf-8")
        en = (REPO_ROOT / "README.en.md").read_text("utf-8")
        assert "v0.3.0 的真实规则导入" in zh
        assert "v0.1.0 的真实规则导入" not in zh
        assert "on v0.3.0" in en
        assert "on v0.1.0" not in en
