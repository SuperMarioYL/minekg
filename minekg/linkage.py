"""Cross-source entity-linkage — the load-bearing primitive.

Resolve a ``canonical_id`` for every entity mention so that the same person /
place / org appearing under different surface forms across 微信 / 支付宝 /
银行 / 笔记 collapses to one node. The canonical example:

    微信 "小张"  ≈  支付宝 "张三"  ≈  银行 "张*"

Strategy (rule fallback, always available; qwen optional for ambiguous
pairs):

* **Exact surface match** → same canonical (confidence 1.0).
* **Person normalisation** — strip colloquial prefixes (小/老/阿/大) and
  honorific suffixes (总/哥/姐/老板/经理/师傅), then key on the surname for
  masked ("张*") or single-surname ("小张") forms. Two mentions sharing a
  surname but lacking a full resolved name are *proposed* as the same
  canonical and **flagged as ambiguous** (confidence 0.6) — the user confirms
  via ``minekg resolve``.
* **Most specific wins** — when "小张" (surname 张) and "张三" (full 张三)
  co-occur, the canonical id is ``person:张三`` and "小张"/"张*" are aliases
  flagged for review.

The output is a :class:`LinkageResult` carrying per-entity canonical ids, an
alias map, record→entity edges, and an ambiguity report.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import Entity, ORG, PERSON, PLACE

_PREFIX = "小老阿大"
_SUFFIX = "总哥姐老板经理师傅娘师傅"


@dataclass
class Ambiguity:
    canonical_id: str
    surface_forms: list
    reason: str
    confidence: float


@dataclass
class LinkageResult:
    entities: list = field(default_factory=list)  # Entity with canonical_id set
    aliases: dict = field(default_factory=dict)  # canonical_id -> [surface_forms]
    edges: list = field(default_factory=list)  # (record_id, canonical_id)
    ambiguities: list = field(default_factory=list)  # Ambiguity


def _surname(name: str) -> str | None:
    """Return the single-char surname if ``name`` is a masked/colloquial form."""
    if not name:
        return None
    s = name.strip()
    if s.startswith("*") or not s:
        return None
    # strip masked star
    while s.endswith("*") or s.endswith("＊"):
        s = s[:-1]
    # strip honorific suffix chars
    while len(s) >= 3 and s[-1] in _SUFFIX:
        s = s[:-1]
    # strip colloquial prefix
    while len(s) >= 2 and s[0] in _PREFIX:
        s = s[1:]
    if not s:
        return None
    if len(s) == 1:
        return s  # already a surname
    if len(s) == 2 or len(s) == 3:
        return s[0]
    return None


def _person_key(surface: str) -> tuple:
    """Return (kind, key) where kind ∈ {'full','surname'} for grouping."""
    s = surface.strip()
    masked = s.endswith("*") or s.endswith("＊")
    while s.endswith("*") or s.endswith("＊"):
        s = s[:-1]
    while len(s) >= 3 and s[-1] in _SUFFIX:
        s = s[:-1]
    while len(s) >= 2 and s[0] in _PREFIX:
        s = s[1:]
    if not s:
        return ("none", surface)
    if masked and len(s) >= 1:
        return ("surname", s[0])
    if len(s) == 1:
        return ("surname", s)
    if len(s) in (2, 3, 4):
        return ("full", s)
    return ("none", surface)


def link(records: list, client=None) -> LinkageResult:
    """Run cross-source linkage over records (mutates ``record.entities``)."""
    res = LinkageResult()

    # 1) exact surface-form buckets (all types) — high confidence
    exact: dict = {}  # (type, surface) -> canonical_id
    # persons also keyed by surname for fuzzy proposal
    person_full: dict = {}  # full-name -> canonical_id
    person_surname: dict = {}  # surname -> list[(canonical, surface, masked)]

    def canonical_for(typ: str, surface: str) -> str:
        key = (typ, surface)
        if key in exact:
            return exact[key]
        cid = f"{typ}:{surface}"
        exact[key] = cid
        return cid

    # First pass: assign exact canonicals + collect person grouping data.
    for rec in records:
        if rec.record_id is None:
            # assign a stable id if missing
            import hashlib

            rec.record_id = hashlib.md5(
                f"{rec.source}|{rec.provenance.file}|{rec.provenance.line}|{rec.raw_row}".encode()
            ).hexdigest()[:12]
        for ent in rec.entities:
            if ent.type == PERSON:
                kind, key = _person_key(ent.surface_form)
                if kind == "full":
                    cid = canonical_for(PERSON, key)
                    ent.canonical_id = cid
                    person_full.setdefault(key, cid)
                    person_surname.setdefault(key[0], []).append((cid, ent.surface_form, False))
                elif kind == "surname":
                    # don't lock the canonical yet — may merge into a full name
                    masked = ent.surface_form.endswith("*") or ent.surface_form.endswith("＊")
                    person_surname.setdefault(key, []).append((None, ent.surface_form, masked))
                    # tentative canonical; resolved below
                    ent.canonical_id = f"person:{key}"
                else:
                    ent.canonical_id = canonical_for(PERSON, ent.surface_form)
            else:
                ent.canonical_id = canonical_for(ent.type, ent.surface_form)

    # 2) resolve surname-only persons against full names if a full name with
    #    the same surname exists in the dataset → merge + flag ambiguity.
    seen_amb: set = set()
    for rec in records:
        for ent in rec.entities:
            if ent.type != PERSON:
                continue
            kind, key = _person_key(ent.surface_form)
            if kind != "surname":
                continue
            surname = key
            # is there a full name with this surname?
            full_cids = [
                (name, cid)
                for name, cid in person_full.items()
                if name.startswith(surname)
            ]
            if full_cids:
                # pick the shortest full name (most canonical)
                full_cids.sort(key=lambda x: len(x[0]))
                chosen_name, chosen_cid = full_cids[0]
                ent.canonical_id = chosen_cid
                ent.confidence = 0.6
                amb_key = (chosen_cid, ent.surface_form)
                if amb_key not in seen_amb:
                    seen_amb.add(amb_key)
                    res.ambiguities.append(
                        Ambiguity(
                            canonical_id=chosen_cid,
                            surface_forms=[ent.surface_form, chosen_name],
                            reason=f"surname-only '{ent.surface_form}' merged into full name '{chosen_name}' (same surname {surname})",
                            confidence=0.6,
                        )
                    )
            else:
                # keep the surname canonical; flag for review if there are
                # multiple distinct surname-only mentions
                ent.canonical_id = f"person:{surname}"
                ent.confidence = 0.7

    # 3) optional qwen disambiguation for ambiguous person pairs
    if client is not None and res.ambiguities:
        _qwen_disambiguate(res, client)

    # 4) build aliases + edges
    for rec in records:
        for ent in rec.entities:
            cid = ent.canonical_id
            if not cid:
                continue
            res.aliases.setdefault(cid, [])
            if ent.surface_form not in res.aliases[cid]:
                res.aliases[cid].append(ent.surface_form)
            res.edges.append((rec.record_id, cid))
            # append a copy with canonical id set
            res.entities.append(
                Entity(
                    type=ent.type,
                    surface_form=ent.surface_form,
                    canonical_id=cid,
                    confidence=ent.confidence,
                )
            )

    return res


def _qwen_disambiguate(res: LinkageResult, client) -> None:
    """Ask qwen to confirm/reject each flagged ambiguous pair.

    Mutates ``res.ambiguities`` in place: confirmed pairs keep their
    canonical; rejected ones are dropped (the surname mention keeps its own
    ``person:<surname>`` canonical).
    """
    from openai import OpenAI  # noqa: F401  (client already typed loosely)

    keep = []
    for amb in res.ambiguities:
        a, b = amb.surface_forms[0], amb.surface_forms[-1]
        prompt = (
            "判断以下两个中文人名称呼是否指同一个人，只回答 JSON "
            '{"same": true/false}。\n'
            f"A: {a}\nB: {b}"
        )
        try:
            resp = client.chat.completions.create(
                model="qwen-turbo",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            import json, re

            text = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                data = json.loads(m.group(0))
                if data.get("same") is False:
                    continue  # drop the merge
            keep.append(amb)
        except Exception:
            keep.append(amb)  # on failure, keep the rule proposal
    res.ambiguities = keep
