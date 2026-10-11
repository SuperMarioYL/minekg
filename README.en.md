[简体中文](README.md) | **English**

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="960" alt="MineKG — Get an aggregate with the rows behind it.">
</picture>

**MineKG imports WeChat text, Alipay CSV, supported bank PDFs and notes into SQLite, links entity aliases and returns source rows with simple aggregate queries.**

`Python 3.10+` · [MIT](LICENSE) · [GitHub](https://github.com/SuperMarioYL/minekg) · [Website](https://minekg.lei6393.com)

## Why it helps

Nicknames, full names and masked names can split related records across exports. MineKG stores source records and aliases together and links query results back to files and lines. Name grouping is a candidate inference; ambiguous identities still need human review.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="960" alt="Query the supplied synthetic exports">
</picture>

## Architecture

Source adapters produce Record objects with provenance, NER extracts entities, and linkage builds canonical IDs, aliases and ambiguity records. kg.py stores records, entities, aliases and edges. ask.py uses rules for person, time and sum/count/avg intent before reading linked rows; its client parameter does not currently drive model-generated SQL.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="960" alt="Exports to linked records and answers">
</picture>

## Install

Requires Python 3.10+. Installation may need network access; the self-cleaning example explicitly uses rules without reading the user’s MineKG data directory or model configuration.

```bash
git clone https://github.com/SuperMarioYL/minekg.git
cd minekg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Quickstart

```bash
python examples/presentation_demo.py
```

Three bundled synthetic export formats produce eleven records. Rules link 小张 to person:张三; the explicit 2025 query returns 500.00 yuan from two amounts, 200 and 300, while retaining one ambiguity. This sums amount fields; it is not net income minus expenditure.

## Usage

```bash
# Keep this example’s state in a separate directory
export MINEKG_HOME="$PWD/.minekg-example"
minekg ingest examples/exports
minekg link
minekg ask "2025 年和小张相关的资金往来合计"
minekg ask "2025 年和小张相关的记录有多少条"
```

link rebuilds the target SQLite graph; it is not an incremental merge into an unrelated existing database. resolve currently prints a future-feature notice and cannot confirm identities interactively.

## Capabilities and integrations

| Source | Implemented path |
|---|---|
| WeChat TXT | Timestamp/sender message blocks |
| Alipay CSV | Header and encoding handling |
| Bank PDF | pdfplumber text-line parser; format must match |
| Markdown notes | Per-line records and dates |
| SQLite | Queries, aliases and row provenance |

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="960" alt="Selected personal export formats">
</picture>

## Configuration and limits

MINEKG_HOME defaults to ~/.minekg and contains records.jsonl, minekg.db and config.json. Without a configured DashScope key, extraction and linkage use rules. With a key, NER and ambiguity processing may send record snippets to the service; those snippets can contain personal information, so this is not guaranteed zero egress.

Queries support the implemented person/time/aggregation rules rather than arbitrary natural language or complete income/expense filtering semantics. Alias rules may join people sharing a surname. Provenance does not establish identity or accounting correctness. Validate other bank exports and encrypted/scanned PDFs separately.

## Recorded demo

Real rule-based import, linkage and SQLite querying on v0.3.0. No real personal data, model request or bank-PDF acceptance is involved; the temporary database is removed afterward.

[Inputs, commands and complete output](docs/demo-results.json)

[Retained terminal recording](assets/demo.gif) · [Recording script](docs/demo.tape). The replayable record above describes this example.

## Roadmap

- [x] Export adapters and rule/optional-model entity processing.
- [x] SQLite linkage, simple queries and row provenance.
- [x] Ambiguity reporting.
- [ ] Interactive resolve and more bank parsers.
- [ ] Improved linkage quality and query semantics.

No hosted household/team ledger or paid synchronization service is provided.

## Development and license

```bash
python -m pip install pytest
python -m pytest
```

See minekg/__init__.py for the shared format. New source adapters should preserve files and line numbers.

[MIT](LICENSE) · [Issues](https://github.com/SuperMarioYL/minekg/issues)
