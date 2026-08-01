"""Read ``USER/Profiles/default/attributes.xml``.

This file is what every pre-1896 Hunt tracker scraped for match results. On current
builds the ``MissionBag*`` keys are gone (see docs/DATA_SOURCES.md) and what is left is
a settings, unlock and inventory snapshot. Still worth capturing: diffing two snapshots
tells you exactly what a play session unlocked.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

UNLOCK_KEY = re.compile(r"^Unlocks/(\d+)/(\d+)$")
FAVORITE_KEY = re.compile(r"^ItemsUserData/Favorited/(\d+)$")
ACTIVE_SKIN_KEY = re.compile(r"^ActiveSkin/(\d+)$")

# Unlock values are a small bitfield. The game does not name the bits anywhere in the
# binary, so we expose the raw value and the decomposed bits rather than inventing
# meanings for them. Observed values: 0, 2, 8, 10, 16, 18, 26.
UNLOCK_BITS = (1, 2, 4, 8, 16, 32)


@dataclass
class ActiveSkin:
    slot: int
    base_item: str
    skin: str

    @classmethod
    def parse(cls, slot: int, value: str) -> "ActiveSkin | None":
        """Values look like ``dolch 96|normal|0=legendary dolch 96 - a - ambush|normal|0``."""
        if "=" not in value:
            return None
        left, _, right = value.partition("=")
        base = left.split("|")[0].strip()
        skin = right.split("|")[0].strip()
        if not base:
            return None
        return cls(slot=slot, base_item=base, skin=skin)


@dataclass
class Unlock:
    category: int
    simple_id: int
    value: int

    @property
    def bits(self) -> list[int]:
        return [b for b in UNLOCK_BITS if self.value & b]


@dataclass
class AttributeSnapshot:
    path: Path
    version: str
    digest: str
    raw: dict[str, str] = field(default_factory=dict)
    unlocks: list[Unlock] = field(default_factory=list)
    favorites: list[int] = field(default_factory=list)
    active_skins: list[ActiveSkin] = field(default_factory=list)

    @property
    def settings(self) -> dict[str, str]:
        """Everything that is not a bulk collection key: graphics, audio, input, UI."""
        return {
            k: v for k, v in self.raw.items()
            if not (UNLOCK_KEY.match(k) or FAVORITE_KEY.match(k) or ACTIVE_SKIN_KEY.match(k))
        }

    def summary(self) -> dict[str, int | str]:
        return {
            "version": self.version,
            "attributes": len(self.raw),
            "unlocks": len(self.unlocks),
            "unlocked_nonzero": sum(1 for u in self.unlocks if u.value),
            "favorites": len(self.favorites),
            "active_skins": len(self.active_skins),
            "settings": len(self.settings),
        }


def parse(path: str | Path) -> AttributeSnapshot:
    path = Path(path)
    data = path.read_bytes()
    root = ElementTree.fromstring(data.decode("utf-8", errors="replace"))

    snapshot = AttributeSnapshot(
        path=path,
        version=root.attrib.get("Version", "unknown"),
        digest=hashlib.sha256(data).hexdigest(),
    )
    for node in root.iter("Attr"):
        name = node.attrib.get("name")
        if name is None:
            continue
        value = node.attrib.get("value", "")
        snapshot.raw[name] = value

        if match := UNLOCK_KEY.match(name):
            try:
                snapshot.unlocks.append(
                    Unlock(int(match[1]), int(match[2]), int(value or 0))
                )
            except ValueError:
                pass
        elif match := FAVORITE_KEY.match(name):
            snapshot.favorites.append(int(match[1]))
        elif match := ACTIVE_SKIN_KEY.match(name):
            if skin := ActiveSkin.parse(int(match[1]), value):
                snapshot.active_skins.append(skin)

    return snapshot


def diff(old: AttributeSnapshot, new: AttributeSnapshot) -> dict[str, dict[str, tuple[str, str]]]:
    """What changed between two snapshots, split into added / removed / changed."""
    added = {k: ("", v) for k, v in new.raw.items() if k not in old.raw}
    removed = {k: (v, "") for k, v in old.raw.items() if k not in new.raw}
    changed = {
        k: (old.raw[k], new.raw[k])
        for k in old.raw.keys() & new.raw.keys()
        if old.raw[k] != new.raw[k]
    }
    return {"added": added, "removed": removed, "changed": changed}
