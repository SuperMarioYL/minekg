# Changelog

All notable changes to MineKG are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [0.2.0] - 2026-09-09

### Fixed

- **银行流水：整数金额不再丢失。** `minekg/sources/bank.py` 的金额正则此前要求必须带小数位，
  导致打印为 `500` 或 `1,200` 的整数金额被当成普通文本（`amount=None`，数字漏进摘要），
  进而让 `ask` 的合计/均值静默丢掉这些交易。现在逗号分组的整数与 1–7 位的整数金额都能解析，
  而长交易号/账号（10+ 位）仍不会被误判为金额。
- **`link --records` 与 `ask --records` 指向同一个 KG 文件。** 此前 `link --records X`
  把知识图谱写到默认的 `~/.minekg/minekg.db`，而 `ask --records X` 读取 `X` 同目录下的
  `minekg.db`——两者不一致会让 `ask` 以 `no such table: aliases` 崩溃退出。现在传入
  `--records` 时两个命令都在 records 文件所在目录读写 KG（`link` 报告也会打印真实路径）。
- **KG 未构建时 `minekg ask` 不再崩溃。** 数据库文件不存在时返回既定的引导话术
  （"先运行 minekg ingest 与 minekg link 构建图谱"）并以退出码 0 结束，而不是抛出
  sqlite3 异常。
- **`minekg config --show` 真正打印完整 API key。** 此前 `--show` 是无效开关——带不带它
  输出完全相同（均为掩码形式）。现在 `--show` 输出完整 key，默认输出仍为掩码/未配置。

### Added

- `minekg --version` 命令行开关（此前 CLI 没有任何版本输出口）。
- 本 `CHANGELOG.md`（回溯包含 0.1.0 的完整历史）。

### Changed

- 版本号在所有表面同步升至 0.2.0：`VERSION`、`pyproject.toml`、
  `minekg.__version__`、`web/site.json`（`meta.implementation_version` +
  新增 `meta.content_version`，footer 追加 `· v0.2.0`）。

## [0.1.0] - 2026-08-07

首个发布：本地优先的中文个人数据知识图谱 CLI。

- 四个源适配器（支付宝账单 CSV / 银行流水 PDF / 微信导出 txt / 笔记 md）统一输出带
  provenance 的 Record JSONL（`minekg ingest`）。
- 中文 NER + 跨源实体链接（`minekg link`）：微信「小张」≈ 支付宝「张三」≈ 银行「张*」
  折叠为同一 `canonical_id`，歧义链接进入报告供人工复核；可选 DashScope（qwen）加成，
  无 key 时全程规则回退、离线可用。
- 自然语言问答（`minekg ask`）：NL→SQL 跑在本地 SQLite KG 上，答案附带逐行 provenance。
- 原始导出文件永不离开本机；仅短文本片段可选发给 qwen。
