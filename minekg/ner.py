"""Chinese NER — extract 人名 / 地名 / 机构 / 时间 per record.

Two backends:

1. **qwen (DashScope, OpenAI-compatible)** — when a DashScope API key is
   configured, short record snippets are sent to qwen for high-accuracy
   Chinese entity extraction. Raw export files never leave the machine; only
   the short ``content`` / ``counterparty`` snippet is sent.
2. **Rule fallback** — a deterministic gazetteer + regex extractor used when
   no key is present (and in tests / the offline demo). It is deliberately
   conservative: it prefers the structured ``counterparty`` field for
   persons and uses suffix patterns for places / orgs and date patterns for
   times.

The rule fallback is *not* a research-grade NER — its job is to keep the
end-to-end loop honest and runnable offline so the demo and tests are
deterministic. With a key configured the same code path delegates to qwen.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from . import Entity, ORG, PERSON, PLACE, TIME

# --- gazetteers ----------------------------------------------------------

# Common Chinese surnames (top ~120). Used to recognise person names that
# appear in free text (notes / wechat) without a structured counterparty.
_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟黄穆"
    "萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮"
    "蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万"
)

_PLACE_SUFFIX = ("市", "省", "区", "县", "镇", "村", "路", "街", "号", "大道",
                 "广场", "大厦", "站", "机场", "大学", "公园", "桥")
_ORG_SUFFIX = ("公司", "银行", "集团", "有限公司", "超市", "便利店", "商店",
               "科技", "中心", "医院", "学校", "基金", "证券", "保险", "网店",
               "工作室", "事务所")
_TIME_WORDS = ("去年", "今年", "前年", "去年", "上个月", "本月", "去年年底",
               "年初", "年底", "上半年", "下半年")
_DATE_RE = re.compile(
    r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{4}年\d{1,2}月|\d{1,2}月\d{1,2}日"
)

# A candidate person name: surname + 1-2 Chinese chars, optionally a masked
# trailing "*". Allows 小/老/阿/大 + name colloquial forms too.
_PERSON_RE = re.compile(
    r"(?:[小老阿大])?[\u4e00-\u9fff]{2,4}\*?|[\u4e00-\u9fff]{2,4}\*"
)


def _is_person(tok: str) -> bool:
    if not tok or len(tok) > 5:
        return False
    core = tok[:-1] if tok.endswith("*") else tok
    # strip colloquial prefix
    if core and core[0] in "小老阿大":
        core = core[1:]
    if not (2 <= len(core) <= 4):
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff]+", core):
        return False
    return core[0] in _SURNAMES


def _tokenize_chinese(text: str) -> list:
    # Greedy runs of CJK chars / alphanumeric; good enough for suffix matching.
    return re.findall(r"[\u4e00-\u9fffA-Za-z0-9*\u4e00-\u9fff]+", text)


def extract_rule(record) -> list:
    """Deterministic entity extraction for one record."""
    found: list = []
    seen = set()

    def add(type_: str, surface: str, conf: float = 1.0) -> None:
        key = (type_, surface)
        if surface and key not in seen:
            seen.add(key)
            found.append(Entity(type=type_, surface_form=surface, confidence=conf))

    # 1) Structured counterparty → person (highest-confidence signal).
    party = (record.counterparty or "").strip()
    if party and party not in ("我", "自己", "咱", "俺", "本人", "对方"):
        add(PERSON, party)

    # 2) Free-text extraction over the content + raw_row.
    text = f"{record.content} {record.raw_row}"
    # time
    for m in _DATE_RE.finditer(text):
        add(TIME, m.group(0))
    for w in _TIME_WORDS:
        if w in text:
            add(TIME, w)

    # orgs / places / persons by scanning token runs
    for run in re.findall(r"[\u4e00-\u9fff*]+", text):
        # try the whole run as org/place first
        if any(run.endswith(s) for s in _ORG_SUFFIX):
            add(ORG, run)
            continue
        if any(run.endswith(s) for s in _PLACE_SUFFIX):
            add(PLACE, run)
            continue

    # persons — two reliable shapes (free text has no word boundaries):
    #   (a) colloquial  小/老/阿/大 + one surname char  →  小张, 老王
    #   (b) standalone full name: a CJK run of length 2-3 starting with a
    #       surname (and not itself a colloquial prefix), incl. masked "张*"
    for m in re.finditer(r"[小老阿大][\u4e00-\u9fff]", text):
        if m.group()[1] in _SURNAMES:
            add(PERSON, m.group(), 0.85)
    for run in re.findall(r"[\u4e00-\u9fff*]+", text):
        masked = run.endswith("*")
        core = run[:-1] if masked else run
        if masked and core and core[0] in _SURNAMES:
            add(PERSON, run, 0.9)
            continue
        if 2 <= len(core) <= 3 and core[0] in _SURNAMES and core[0] not in "小老阿大":
            add(PERSON, run, 0.9)

    return found


def _qwen_prompt(record) -> str:
    return (
        "从下面这条个人数据记录中抽取中文实体，类型限定为 person(人名)、"
        "place(地名)、org(机构)、time(时间)。只返回 JSON 数组，"
        "每个元素形如 {\"type\":...,\"surface_form\":...}，不要解释。\n"
        f"记录: {record.content or record.raw_row}"
    )


def _parse_qwen_json(text: str) -> list:
    text = text.strip()
    # tolerate code fences
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*", "", text).strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # last-ditch: pull the first JSON array out of the text
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    out = []
    if not isinstance(data, list):
        data = [data]
    for item in data:
        if not isinstance(item, dict):
            continue
        t = (item.get("type") or "").lower()
        if t and t not in (PERSON, PLACE, ORG, TIME):
            # map common chinese labels
            mapping = {"人名": PERSON, "地名": PLACE, "机构": ORG, "时间": TIME,
                       "person": PERSON, "place": PLACE, "org": ORG, "time": TIME,
                       "organization": ORG, "date": TIME}
            t = mapping.get(t, t)
        surface = (item.get("surface_form") or item.get("text") or item.get("name") or "").strip()
        if surface:
            out.append(Entity(type=t or PERSON, surface_form=surface,
                             confidence=float(item.get("confidence", 0.95))))
    return out


def make_client(api_key: str, base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
                model: str = "qwen-turbo"):
    """Build an OpenAI-compatible client pointed at DashScope (qwen)."""
    from openai import OpenAI

    return OpenAI(api_key=api_key, base_url=base_url), model


def extract(record, client=None, model: str = "qwen-turbo") -> list:
    """Extract entities from one record.

    Uses qwen when ``client`` is provided, otherwise the rule fallback.
    """
    if client is not None:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": _qwen_prompt(record)}],
                temperature=0.0,
            )
            text = resp.choices[0].message.content or ""
            ents = _parse_qwen_json(text)
            if ents:
                # merge with structured counterparty (highest priority)
                rule = extract_rule(record)
                seen = {(e.type, e.surface_form) for e in ents}
                for e in rule:
                    if (e.type, e.surface_form) not in seen:
                        ents.append(e)
                return ents
        except Exception:
            # network / API failure → fall back to rules (never crash the loop)
            pass
    return extract_rule(record)


def extract_many(records: list, client=None, model: str = "qwen-turbo") -> list:
    """Mutate each record's ``entities`` in place; return the records."""
    for r in records:
        r.entities = extract(r, client=client, model=model)
    return records
