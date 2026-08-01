"""Locate the Hunt install and the files it writes."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

APP_ID = "594650"
FOLDER_NAME = "Hunt Showdown 1896"


def _steam_root() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as handle:
                value = winreg.QueryValueEx(handle, "SteamPath")[0]
                if value:
                    return Path(value)
        except OSError:
            continue
    return None


def _library_paths(steam: Path) -> list[Path]:
    """Parse libraryfolders.vdf without a full VDF parser: we only want the paths."""
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    libraries = [steam]
    if vdf.is_file():
        text = vdf.read_text(encoding="utf-8", errors="replace")
        libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s*"([^"]+)"', text)]
    return libraries


def find_game() -> Path | None:
    """Best-effort discovery of the Hunt install directory."""
    override = os.environ.get("HUNT_GAME_DIR")
    if override and (Path(override) / "bin" / "win_x64").is_dir():
        return Path(override)
    steam = _steam_root()
    if steam:
        for library in _library_paths(steam):
            candidate = library / "steamapps" / "common" / FOLDER_NAME
            if (candidate / "bin" / "win_x64").is_dir():
                return candidate
    return None


@dataclass(frozen=True)
class GamePaths:
    root: Path

    @property
    def user(self) -> Path:
        return self.root / "USER"

    @property
    def attributes(self) -> Path:
        return self.user / "Profiles" / "default" / "attributes.xml"

    @property
    def game_log(self) -> Path:
        return self.user / "Game.log"

    @property
    def log_backups(self) -> Path:
        return self.user / "LogBackups"

    @property
    def game_hunt_dll(self) -> Path:
        return self.root / "bin" / "win_x64" / "GameHunt.dll"

    def all_logs(self) -> list[Path]:
        """Every log oldest-first, so replaying them rebuilds history in order."""
        # schematyc_legacy.log is a directory, not a log; glob picks it up regardless.
        candidates = [p for p in self.log_backups.glob("*.log") if p.is_file()]
        logs = sorted(candidates, key=lambda p: p.stat().st_mtime)
        if self.game_log.is_file():
            logs.append(self.game_log)
        return logs


def resolve(explicit: str | Path | None = None) -> GamePaths:
    root = Path(explicit) if explicit else find_game()
    if root is None:
        raise SystemExit(
            "Could not find Hunt: Showdown 1896. Pass --game <path> or set HUNT_GAME_DIR."
        )
    if not (Path(root) / "bin" / "win_x64").is_dir():
        raise SystemExit(f"{root} does not look like a Hunt install (no bin/win_x64).")
    return GamePaths(Path(root))
