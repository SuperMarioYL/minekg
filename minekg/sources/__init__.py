"""Source adapters — each turns one export format into unified ``Record`` list.

Every adapter exposes ``parse(path) -> list[Record]`` (and where useful a
``parse_lines`` helper for unit testing the parsing logic without the file
layer). Detection of which adapter to use lives in :mod:`minekg.ingest`.
"""

from __future__ import annotations

from . import alipay, bank, notes, wechat

__all__ = ["alipay", "bank", "notes", "wechat"]
