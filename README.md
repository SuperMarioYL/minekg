**简体中文** | [English](./README.en.md)

<div align="right"><sub><b>中文</b>&nbsp;&nbsp;⇄&nbsp;&nbsp;<a href="./README.en.md">EN</a></sub></div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/hero-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/hero-light.svg">
  <img src="./assets/hero-light.svg" width="880" alt="MineKG">
</picture>

<p align="center"><sub>MineKG 是把你的微信 / 支付宝 / 银行流水 / 笔记导出融合成一个可自然语言查询知识图谱的本地优先工具。</sub></p>

<p align="center">
  <a href="./LICENSE"><img src="https://img.shields.io/github/license/SuperMarioYL/minekg?label=license&color=0071E3"></a>
  <a href="https://github.com/SuperMarioYL/minekg/releases"><img src="https://img.shields.io/github/v/release/SuperMarioYL/minekg?label=release"></a>
  <a href="https://github.com/SuperMarioYL/minekg/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SuperMarioYL/minekg/ci.yml?label=CI"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-0071E3">
  <img src="https://img.shields.io/badge/Skill-ready-5E5CE6">
  <img src="https://img.shields.io/badge/Claude%20Code-skill-8985FF">
</p>

> **把散落在微信、支付宝、银行流水与笔记里的「小张」识别成同一个人，然后用一句话问回你的数据。**

## 为什么是现在

[Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify)（103k stars）证明了一件事：把代码库用确定性 AST 解析、不依赖向量库、每条边可溯源，变成可查询的知识图谱——这套原语已经是被验证的热门方向。它以一个 `/graphify` **Skill** 的形态跑在 **Claude Code** 和 Cursor 里。MineKG 做的是同构迁移：把那个「结构化数据 → 可查询 KG」的原语，从「代码库」搬到「中国普通用户散落的个人数据导出」上。中国 NER（人名/地名/机构/时间）已是 commodity，PIPL 让微信/支付宝/银行都（勉为其难地）开放了导出——三件事同时就绪，这个消费侧接口才第一次可达。不是 graphify 的中文版，而是一个新的 load-bearing 原语：**跨源实体链接**。

## 目录

- [架构](#架构)
- [安装与快速开始](#安装与快速开始)
- [用法](#用法)
- [Demo](#demo)
- [配置](#配置)
- [路线图](#路线图)
- [付费：家庭/团队对账托管](#付费家庭团队对账托管)
- [许可证](#许可证)

<h2><img src="https://api.iconify.design/tabler:topology-star-3.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 架构</h2>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/atlas-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="./assets/atlas-light.svg">
  <img src="./assets/atlas-light.svg" width="880" alt="架构：导出 → NER + 跨源链接 → SQLite KG + 自然语言问答">
</picture>

单个 Python 进程、单个 SQLite 文件（`~/.minekg/minekg.db`）。没有微服务、没有向量库（确定性 KG，沿用 graphify「no vector store」立场）。只有短文本片段会发 qwen 做 NER，**原始导出文件永不离开本机**——对一个触碰个人账单/流水的工具，这是硬性要求。

核心原语是 **跨源 `canonical_id` 解析**：微信显示名「小张」、支付宝备注「张三」、银行流水「张\*」折叠为同一个节点。

<h2><img src="https://api.iconify.design/tabler:rocket.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 安装与快速开始</h2>

```bash
git clone https://github.com/SuperMarioYL/minekg && cd minekg && pip install -e .
minekg ingest examples/exports && minekg link
minekg ask "去年和小张相关的所有资金往来合计"
```

> 国内可走 Gitee 镜像免 GitHub 摩擦：`git clone https://gitee.com/SuperMarioYL/minekg`（推送后由你同步）。
> 没有配置 DashScope key 也照常工作——NER/链接/问答走确定性规则回退，10 分钟内离线可复现。

<details><summary>样例输出</summary>

```
$ minekg ask "去年和小张相关的所有资金往来合计"
Q: 去年和小张相关的所有资金往来合计
A: 500.00 元
entity: person:张三  (matched: 小张)
provenance:
  alipay  examples/exports/alipay.csv  line 5   2025-01-10  张三  200.0  person:张三
  alipay  examples/exports/alipay.csv  line 6   2025-03-15  张三  300.0  person:张三
```

</details>

<h2><img src="https://api.iconify.design/tabler:terminal-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 用法</h2>

五个子命令覆盖从导出到问答的全流程：

```bash
# 1. 可选：配置 DashScope（通义千问 qwen）key，提升中文 NER/链接/问答精度
minekg config --dashscope-key sk-***

# 2. 把导出文件丢进一个目录，自动按内容识别来源
minekg ingest ./exports        # alipay.csv + bank.pdf + wechat.txt + notes.md → 统一 Record JSONL

# 3. 中文 NER + 跨源 canonical_id 解析 + SQLite KG + 歧义报告
minekg link                    # 微信「小张」≈ 支付宝「张三」≈ 银行「张*」

# 4. 自然语言问答，返回数字 + 逐行 provenance 表
minekg ask "和小张相关的所有资金往来合计"
minekg ask "去年和小张吃了几次饭"        # 笔数意图
minekg ask "2025 年和小张相关的支出平均"

# 5. （后续里程碑）逐条确认/合并被标记的实体对
minekg resolve
```

适配器细节：

| 来源 | 文件 | 解析方式 |
|---|---|---|
| 微信 | `.txt` | 时间戳+发送者 行块解析，重连多行消息 |
| 支付宝 | `.csv` | 自动定位表头（兼容各版本列名），GBK/UTF-8-BOM 编码自适应 |
| 银行 | `.pdf` | `pdfplumber` 取文本行，重连多行「对手信息」，**保留全名不脱敏** |
| 笔记 | `.md` | 逐行成记录，抽取日期；实体交给 NER |

> 银行流水说明（schema 实测）：招商银行 PDF 用 `pdfplumber.extract_tables()` 返回 0 张表，故走文本行解析；对手信息列会跨行折叠，需重连；交易对方姓名是**完整**而非脱敏的。`minekg/sources/bank.py` 的 `parse_lines` 是核心逻辑，`parse_pdf` 是 pdfplumber 薄封装。

<h2><img src="https://api.iconify.design/tabler:photo.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> Demo</h2>

![demo](assets/demo.gif)

10 分钟从 `git clone` 到拿到带凭证的答案。上面的 GIF 由 [`docs/demo.tape`](./docs/demo.tape) 经 vhs 渲染（`assets/demo.gif` 即提交实物，`demo.yml` 仅按需刷新）。

### 对比 graphify

| 维度 | [graphify](https://github.com/Graphify-Labs/graphify) | MineKG |
|---|:---:|:---:|
| 输入数据 | 代码库（AST/SQL schema） | 中文消费导出（微信/支付宝/银行/笔记） |
| 中文 NER（人名/地名/机构/时间） | — | ✓ |
| 跨源实体链接（canonical_id） | — | ✓ |
| 代码库→KG 成熟度与规模 | ✓（103k stars，成熟） | 部分（新，m1） |
| 无向量库 / 每边可溯源 | ✓ | ✓ |

诚实地讲：在「代码库」这件事上 graphify 比我们成熟得多、快得多；MineKG 的价值在它不碰的那个面——中国普通用户的异构个人导出 + 中文 NER + 跨源链接。

<h2><img src="https://api.iconify.design/tabler:adjustments.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 配置</h2>

| 键 / 环境变量 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `minekg config --dashscope-key` | str | 无 | DashScope（通义千问 qwen）API key；不设则走规则回退 |
| `MINEKG_HOME` | path | `~/.minekg` | 工作目录（records.jsonl / minekg.db / config.json），测试与 demo 可改指临时目录 |
| DashScope endpoint | str | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI 兼容端点，只发短文本片段 |

<h2><img src="https://api.iconify.design/tabler:map-2.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 路线图</h2>

- [x] **m1**：四个来源适配器 → 统一 Record JSONL（带 provenance）+ NER + 跨源链接 + SQLite KG + 自然语言问答，端到端跑通
- [ ] **m2**：qwen 全量 NER/链接质量打磨 + 歧义召回，链接报告可导出/可再入
- [ ] **m3**：`minekg resolve` rich TUI 逐条确认/合并被标记实体对；多银行 PDF 解析器注册表（社区可贡献「一个银行一个解析器」）
- [ ] 未来：更多来源（券商/公积金）、年度复盘一键报告、本地优先的「家庭/团队对账」托管层

## 付费：家庭/团队对账托管

面向 **商业用户**（commercial audience）。本地 CLI 永久免费、本地拥有、单用户——这是它处理个人账单/流水所必须的信任前提。付费方向是已命名、当前不建的**托管层**：

- **谁先付钱**：小微团队/家庭，需要跨成员对账——一个创业者把「个人微信 + 公司支付宝 + 银行流水」合并算年度税务/复盘，或一个家庭把夫妻双方导出合并成一份可问答的家计账本。
- **付什么钱**：托管层——minekg 核心跑云端，成员各自上传导出，合并成一个家庭/团队 KG + 角色视图（夫妻各看各的，家庭共看合计）。本地 CLI 不变，托管按团队收。
- **价格点**：¥39/月起 / 团队（3-5 成员，含每月一定 qwen 调用额度），超出按量。CN 消费向 SaaS 心理价位带在 ¥19-49/月。
- **最小「给我信用卡」路径**：CLI 跑通 10 分钟 demo（免费）→ README 一个「同步到云端家庭账本」按钮 → 邀请 2 名成员上传导出 → 合并 KG + 角色视图 → 回答「我们家上个月谁付了多少」。信用卡在看到合并结果那一刻才收。

详见 GTM §8。本地优先是底线，托管是 opt-in 的，绝不会取代本地 CLI。

<h2><img src="https://api.iconify.design/tabler:license.svg?color=%230071E3&width=24" height="22" align="absmiddle" alt=""> 许可证</h2>

MIT，见 [LICENSE](./LICENSE)。欢迎在 [Issues](https://github.com/SuperMarioYL/minekg/issues) 报 bug 或附上匿名真实多源导出复现；PR 走 Fork → 分支 → PR。

## 分享

```
MineKG — 把微信/支付宝/银行流水/笔记融合成可自然语言查询的本地知识图谱。沿用 graphify 的 Claude Code Skill 原语，但接的是中国普通用户的个人数据导出。git clone 后 10 分钟从「小张」问到 500 元带凭证。 https://github.com/SuperMarioYL/minekg
```

<p align="center"><sub><a href="./LICENSE">MIT</a> © 2026 SuperMarioYL</sub></p>
