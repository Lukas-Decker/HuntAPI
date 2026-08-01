"""Resolve Hunt's numeric item ids to real names using the Hunt-ify catalog.

Hunt identifies an item two ways, and attributes.xml uses both:

* ``simpleId`` - a small integer, used by ``Unlocks/<category>/<id>``
* ``id`` - a hex string, used in decimal by ``ItemsUserData/Favorited/<id>``

Hunt-ify's ``structured/items/*.json`` carries both, so it can bridge the two.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

HEX_ID = re.compile(r"[0-9A-F]{6,}")
DEFAULT_LOCATIONS = (
    Path(__file__).resolve().parents[2] / "Hunt-ify" / "structured" / "items",
    Path("../Hunt-ify/structured/items"),
)


@dataclass(frozen=True)
class Item:
    name: str
    kind: str
    simple_id: int | None
    hex_id: int | None
    rarity: str | None = None
    legendary: bool = False


class Catalog:
    """Item lookup backed by Hunt-ify. Degrades to empty rather than failing hard."""

    def __init__(self, items_dir: Path | None = None):
        self.items_dir = Path(items_dir) if items_dir else self._discover()

    @staticmethod
    def _discover() -> Path | None:
        for candidate in DEFAULT_LOCATIONS:
            if candidate.is_dir():
                return candidate
        return None

    @property
    def available(self) -> bool:
        return self.items_dir is not None and self.items_dir.is_dir()

    @cached_property
    def _loaded(self) -> tuple[dict[int, Item], dict[int, Item]]:
        by_simple: dict[int, Item] = {}
        by_hex: dict[int, Item] = {}
        if not self.available:
            return by_simple, by_hex
        for path in sorted(self.items_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(data, list):
                continue
            kind = path.stem
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("uiName") or entry.get("name")
                if not name:
                    continue
                simple = entry.get("simpleId")
                raw = entry.get("id")
                hex_id = int(raw, 16) if isinstance(raw, str) and HEX_ID.fullmatch(raw) else None
                item = Item(
                    name=name,
                    kind=kind,
                    simple_id=int(simple) if simple is not None else None,
                    hex_id=hex_id,
                    rarity=entry.get("rarity"),
                    legendary=bool(entry.get("legendary", False)),
                )
                if item.simple_id is not None:
                    by_simple.setdefault(item.simple_id, item)
                if item.hex_id is not None:
                    by_hex.setdefault(item.hex_id, item)
        return by_simple, by_hex

    def by_simple_id(self, simple_id: int) -> Item | None:
        return self._loaded[0].get(simple_id)

    def by_hex_id(self, hex_id: int) -> Item | None:
        return self._loaded[1].get(hex_id)

    def __len__(self) -> int:
        simple, hexed = self._loaded
        return len(simple) + len(hexed)
