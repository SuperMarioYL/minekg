# Changelog

All notable changes to MineKG are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[SemVer](https://semver.org/).

## [0.3.0] - 2026-10-12

### Fixed

- **`minekg ask` 不再为图谱里不存在的人编造 "0.00 元" 答案。** 此前 `ask("和王五相关的…")`
  在 KG 从未见过王五时会落到兜底分支、凭空返回 `person:王五` 与 "0.00 元" 的看似确定的答案；
  现在返回既定的引导话术（"未在知识图谱中找到对应的人名/实体，先运行 minekg ingest 与
  minekg link 构建图谱"）。图谱里真实存在的实体（含同形 canonical）行为不变。
  同时修正 `ask.py` 模块文档：qwen 的 `client` 参数目前不驱动模型生成 SQL，文档现如实说明。
- **`minekg ingest` 指向不存在的目录时给出友好错误。** 此前直接抛出原始
  `FileNotFoundError` traceback；现在打印缺失路径与期望的导出布局（alipay.csv / bank.pdf /
  wechat.txt / notes.md）并以退出码 1 结束。
- **Release workflow 真正把构建产物挂到 GitHub Release。** 此前只上传会过期的 CI artifact，
  GitHub Release 页面 0 个附件（v0.2.0 实测）；现在 tag 构建完成后把 wheel / sdist /
  checksums.txt 上传到对应 Release。
- **README 记录的演示版本与 demo-results.json 对齐。** README/README.en 声称演示录制于
  v0.1.0，而其链接的 docs/demo-results.json 记录的是 0.2.0；现已在 0.3.0 重新离线运行演示
  脚本并同步三处版本说明（README.md / README.en.md / web/site.json）。

### Added

- **`minekg resolve` 交互式复核（m3 合约）。** 逐条展示 `link` 报告的歧义实体对，支持
  `[y] 确认合并`（合并生效）、`[n] 拆分独立`（首个称呼移回独立 canonical，仅迁移只提及该
  称呼的记录边）、`[s] 跳过`、`[q] 退出`；与 link/ask 一致支持 `--records` 定位 KG。
  拒绝拆分走确定性的文本匹配启发式，不改 KG schema。

### Changed

- 版本号在所有表面同步升至 0.3.0：`VERSION`、`pyproject.toml`、
  `minekg.__version__`、`web/site.json`（`meta.implementation_version` + footer `· v0.3.0`）、
  README/README.en 演示版本说明、`docs/demo-results.json`（0.3.0 下重录，输出不变）。

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
