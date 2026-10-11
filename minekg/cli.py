"""``minekg`` CLI — typer-based entry point.

Commands::

    minekg config [--dashscope-key KEY]   show / set the DashScope (qwen) key
    minekg ingest ./exports               auto-detect + emit unified Record JSONL
    minekg link                           中文 NER + 跨源 canonical_id → SQLite KG
    minekg ask "<自然语言问题>"            NL → answer over the KG + 逐行 provenance
    minekg resolve                        逐条确认/拆分 link 报告的歧义实体对

Everything lives under ``~/.minekg`` (or ``$MINEKG_HOME``), so a raw export
file never leaves the machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from . import config_path, db_path, home_dir, records_path

app = typer.Typer(
    name="minekg",
    help="把你散落的微信/支付宝/银行流水与笔记融合成可自然语言查询的个人知识图谱。",
    no_args_is_help=True,
)
console = Console()


def _load_config() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_config(cfg: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@app.command()
def config(
    dashscope_key: str = typer.Option(
        None, "--dashscope-key", help="DashScope (通义千问 qwen) API key"
    ),
    show: bool = typer.Option(False, "--show", help="print the configured key"),
) -> None:
    """Show or set the DashScope (qwen) API key.

    Without a key, NER / linkage / NL→SQL run on the deterministic rule
    fallback — the loop still works end-to-end offline.
    """
    cfg = _load_config()
    if dashscope_key:
        cfg["dashscope_api_key"] = dashscope_key
        _save_config(cfg)
        console.print("[green]DashScope API key saved.[/green]")
        return
    masked = None
    key = cfg.get("dashscope_api_key")
    if key:
        masked = key[:6] + "***" if len(key) > 6 else "***"
    console.print(f"home     : {home_dir()}")
    console.print(f"records  : {records_path()}")
    console.print(f"kg (db)  : {db_path()}")
    if show and key:
        console.print(f"qwen key : {key}")
    else:
        console.print(f"qwen key : {masked or '(未配置, 使用规则模式)'}")


@app.command()
def ingest(
    exports_dir: Path = typer.Argument(..., help="dir with alipay.csv / bank.pdf / wechat.txt / notes.md"),
) -> None:
    """Auto-detect source files and emit unified Record JSONL."""
    if not exports_dir.exists():
        console.print(
            f"[yellow]未找到目录: {exports_dir} — 请传入包含 alipay.csv / "
            "bank.pdf / wechat.txt / notes.md 的导出目录。[/yellow]"
        )
        raise typer.Exit(1)

    from . import ingest as do_ingest

    home_dir().mkdir(parents=True, exist_ok=True)
    records = do_ingest.ingest(exports_dir)
    table = Table(title=f"ingest: {len(records)} records → {records_path()}")
    table.add_column("source", style="cyan")
    table.add_column("count", justify="right")
    counts: dict = {}
    for r in records:
        counts[r.source] = counts.get(r.source, 0) + 1
    for src in sorted(counts):
        table.add_row(src, str(counts[src]))
    console.print(table)


@app.command()
def link(
    records_arg: Path = typer.Option(
        None, "--records", help="records JSONL (default ~/.minekg/records.jsonl)"
    ),
) -> None:
    """Run 中文 NER + cross-source entity-linkage → SQLite KG + report."""
    from . import from_jsonl, ner
    from .kg import load_dashscope_client, reset_db, write_linkage, write_records
    from .linkage import link as do_link

    home_dir().mkdir(parents=True, exist_ok=True)
    records_path_arg = str(records_arg) if records_arg else records_path()
    # The KG lives next to the records file when --records points at one, so
    # `link --records X` and `ask --records X` read/write the same db.
    db = str(records_arg.parent / "minekg.db") if records_arg else db_path()
    records = from_jsonl(records_path_arg)
    if not records:
        console.print("[yellow]no records found — run `minekg ingest` first.[/yellow]")
        raise typer.Exit(1)

    client, model = load_dashscope_client()
    ner.extract_many(records, client=client, model=model)
    result = do_link(records, client=client)

    reset_db(db)
    write_records(records, db)
    write_linkage(result, db)

    # report
    console.print(f"[green]linked {len(result.entities)} entities → {db}[/green]")
    t = Table(title="canonical persons")
    t.add_column("canonical_id", style="magenta")
    t.add_column("aliases")
    t.add_column("records", justify="right")
    canon_counts: dict = {}
    for _rid, cid in result.edges:
        canon_counts[cid] = canon_counts.get(cid, 0) + 1
    for cid in sorted({e.canonical_id for e in result.entities if e.type == "person"}):
        aliases = result.aliases.get(cid, [])
        t.add_row(cid, ", ".join(aliases), str(canon_counts.get(cid, 0)))
    console.print(t)

    if result.ambiguities:
        at = Table(title=f"ambiguous links ({len(result.ambiguities)}) — review with `minekg resolve`")
        at.add_column("canonical_id", style="magenta")
        at.add_column("forms")
        at.add_column("reason")
        for amb in result.ambiguities:
            at.add_row(amb.canonical_id, " ≈ ".join(amb.surface_forms), amb.reason)
        console.print(at)
    else:
        console.print("[dim]no ambiguous links[/dim]")


@app.command()
def ask(
    question: str = typer.Argument(..., help="自然语言问题，例如「去年和小张相关的所有资金往来合计」"),
    records_arg: Path = typer.Option(None, "--records", hidden=True),
) -> None:
    """Answer a natural-language question over the KG with per-row provenance."""
    from .ask import ask as do_ask
    from .kg import load_dashscope_client

    client, model = load_dashscope_client()
    db = str(records_arg.parent / "minekg.db") if records_arg else db_path()
    ans = do_ask(question, db_path=db, client=client)

    console.print()
    console.print(f"[bold cyan]Q:[/bold cyan] {question}")
    console.print(f"[bold green]A:[/bold green] {ans.answer}")
    if ans.canonical_id:
        console.print(f"[dim]entity: {ans.canonical_id}  (matched: {', '.join(ans.matched_aliases)})[/dim]")
    console.print(f"[dim]{ans.note}[/dim]")

    if ans.rows:
        t = Table(title="provenance")
        for col in ("source", "file", "line", "ts", "counterparty", "amount", "canonical_id"):
            t.add_column(col)
        for r in ans.rows:
            t.add_row(
                str(r.get("source") or ""),
                str(r.get("file") or ""),
                str(r.get("line") if r.get("line") is not None else ""),
                str(r.get("ts") or ""),
                str(r.get("counterparty") or ""),
                str(r.get("amount") if r.get("amount") is not None else ""),
                str(r.get("canonical_id") or ""),
            )
        console.print(t)


@app.command()
def resolve(
    records_arg: Path = typer.Option(
        None, "--records", help="records JSONL (default ~/.minekg/records.jsonl)"
    ),
) -> None:
    """Confirm / split the flagged entity pairs reported by `minekg link`."""
    from .resolve import run as do_resolve

    db = str(records_arg.parent / "minekg.db") if records_arg else db_path()
    if not Path(db).exists():
        console.print(
            "[yellow]no KG found — run `minekg ingest` and `minekg link` first.[/yellow]"
        )
        return

    decisions = do_resolve(db, echo=console.print)
    accepted = sum(1 for _, d in decisions if d == "accepted")
    split = sum(1 for _, d in decisions if d == "split")
    skipped = sum(1 for _, d in decisions if d == "skipped")
    console.print(
        f"[green]resolve done:[/green] {accepted} merged, {split} split, "
        f"{skipped} kept for later"
    )


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"minekg {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        help="Show the minekg version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    """MineKG — local-first personal knowledge graph for Chinese data exports."""


def cli_entry() -> None:  # entry point target
    app()


if __name__ == "__main__":
    cli_entry()
