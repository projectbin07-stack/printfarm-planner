"""Reading text and JSON files the way Windows tools write them."""

from __future__ import annotations

import codecs
import json
from pathlib import Path
from typing import Any

__all__ = ["read_json", "read_text"]


def read_text(path: Path) -> str:
    """Read a text file as UTF-8, accepting a byte-order mark and UTF-16 with a BOM.

    Windows PowerShell 5.1 writes UTF-16 for ``command > file`` and UTF-8 with a BOM when
    ``Out-File`` is set to ``utf8``; both are decoded here instead of failing.
    """
    raw = path.read_bytes()
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def read_json(path: Path) -> Any:
    """``json.loads(read_text(path))``; decoding errors surface as ``ValueError``."""
    return json.loads(read_text(path))
