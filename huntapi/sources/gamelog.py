"""Read ``USER/Game.log`` and the rotated copies in ``USER/LogBackups``.

The log carries no gameplay statistics - no kills, no bounty, no MMR; those only exist
on the wire. What it does carry is an accurate match *lifecycle*: when a match started
and ended, on which map, against which dedicated server, under which lobby session and
mission id. That is enough to build a match timeline locally, and enough to key rows
that a future wire capture can fill in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

LOG_START = re.compile(r"^Log Started at \w+ (\w+ +\d+ [\d:]+ \d{4})")
FILE_VERSION = re.compile(r"^FileVersion:\s*(\S+)")
BUILD_TAG = re.compile(r'BackupNameAttachment="\s*(Build\(\d+\)[^"]*)"')
LINE = re.compile(r"^<(\d{2}):(\d{2}):(\d{2})>\s*(.*)$")

REGION = re.compile(r"\[Account\] Found region in config:\s*(\S+)")
CONNECTION = re.compile(r"\[Account\] ChangeConnectionStatus '\d+:(\w+)' -> '\d+:(\w+)'")
MM_TOKEN = re.compile(r"CMatchMaking::Request response m_pendingRequest token = ,?(\d+)")
MISSION = re.compile(r"mission=([0-9a-f-]{8,}), Lobby: (\d+)")
GAME_RULES = re.compile(r"GameRulesChanged\(\) newRules=(\w+), Lobby: (\d+)")
# Current builds log: PrepareLevel 'levels/cemetery' (client)
# Builds up to ~2.3 logged:  PrepareLevel levels/cemetery
PREPARE_LEVEL = re.compile(r"PrepareLevel\s+'?([\w/]+)'?(?:\s*\((\w+)\))?")
LOADING_LEVEL = re.compile(r"=+ Loading level ([\w/]+) =+")
# Levels that are never a match: the main menu and the offline shooting range.
NON_MATCH_LEVELS = {"menu", "levels/shooting"}
JOIN = re.compile(r"ConnectToCoreGame\(\): join command: 'connect <session>(\w+),([\d.]+):(\d+)'")
ENTER_STATE = re.compile(r"EnterState\(\) entering state (\w+)\(\d+\).*Lobby: (\d+)")
NEW_SESSION = re.compile(r"CGameLobbyManager::NewSessionRequest Success hNext=(\d+)")


@dataclass
class LogHeader:
    path: Path
    started_at: datetime | None
    game_version: str | None
    build_tag: str | None


@dataclass
class Match:
    """One trip from the lobby into a level and back."""

    lobby_sid: str | None = None
    mission_id: str | None = None
    game_rules: str | None = None
    level: str | None = None
    server_ip: str | None = None
    server_port: int | None = None
    session_token: str | None = None
    matchmaking_token: str | None = None
    region: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    source_log: str | None = None

    @property
    def map_name(self) -> str | None:
        """``levels/civilwar`` -> ``civilwar``."""
        return self.level.rsplit("/", 1)[-1] if self.level else None

    @property
    def duration_s(self) -> int | None:
        if self.started_at and self.ended_at:
            return max(0, int((self.ended_at - self.started_at).total_seconds()))
        return None

    @property
    def key(self) -> str:
        """Stable identity for deduplication across overlapping logs."""
        stamp = self.started_at.isoformat() if self.started_at else "?"
        return f"{self.lobby_sid or '?'}:{stamp}"


def read_header(path: Path) -> LogHeader:
    started_at = version = build = None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for _, line in zip(range(40), handle):
            if started_at is None and (m := LOG_START.match(line)):
                for fmt in ("%b %d %H:%M:%S %Y", "%b  %d %H:%M:%S %Y"):
                    try:
                        started_at = datetime.strptime(" ".join(m[1].split()), "%b %d %H:%M:%S %Y")
                        break
                    except ValueError:
                        continue
            elif version is None and (m := FILE_VERSION.match(line)):
                version = m[1]
            elif build is None and (m := BUILD_TAG.search(line)):
                build = m[1]
    return LogHeader(path=path, started_at=started_at, game_version=version, build_tag=build)


class _Clock:
    """Log lines carry only HH:MM:SS, so track date rollover across midnight."""

    def __init__(self, start: datetime | None):
        self.current = start
        self.last_time: tuple[int, int, int] | None = None

    def stamp(self, h: int, m: int, s: int) -> datetime | None:
        if self.current is None:
            return None
        if self.last_time and (h, m, s) < self.last_time:
            self.current += timedelta(days=1)
        self.last_time = (h, m, s)
        return self.current.replace(hour=h, minute=m, second=s, microsecond=0)


def parse_log(path: str | Path) -> list[Match]:
    """Extract every match found in a single log file."""
    path = Path(path)
    header = read_header(path)
    clock = _Clock(header.started_at)

    matches: list[Match] = []
    pending = Match(source_log=path.name)
    region: str | None = None
    in_match = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            parsed = LINE.match(raw.rstrip("\n"))
            if not parsed:
                continue
            now = clock.stamp(int(parsed[1]), int(parsed[2]), int(parsed[3]))
            body = parsed[4]

            if m := REGION.search(body):
                region = m[1]
            elif m := MM_TOKEN.search(body):
                pending.matchmaking_token = m[1]
            elif m := NEW_SESSION.search(body):
                pending.lobby_sid = m[1]
            elif m := MISSION.search(body):
                pending.mission_id, pending.lobby_sid = m[1], m[2]
            elif m := GAME_RULES.search(body):
                pending.game_rules, pending.lobby_sid = m[1], m[2]
            elif m := PREPARE_LEVEL.search(body):
                level, side = m[1], m[2]
                if level in NON_MATCH_LEVELS:
                    pending.level = None  # back to the menu, forget the last level
                elif side in (None, "client"):
                    pending.level = level
            elif m := LOADING_LEVEL.search(body):
                level = m[1]
                if level in NON_MATCH_LEVELS:
                    pending.level = None
                elif pending.level is None:
                    pending.level = level
            elif m := JOIN.search(body):
                pending.session_token = m[1]
                pending.server_ip, pending.server_port = m[2], int(m[3])
            elif m := ENTER_STATE.search(body):
                state, sid = m[1], m[2]
                if state == "eLS_Game" and not in_match:
                    pending.lobby_sid = pending.lobby_sid or sid
                    pending.region = region
                    pending.started_at = now
                    in_match = True
                elif state in ("eLS_Leaving", "eLS_None") and in_match:
                    pending.ended_at = now
                    matches.append(pending)
                    pending = Match(source_log=path.name)
                    in_match = False

    if in_match:  # log ended mid-match, most likely a crash or a still-running session
        matches.append(pending)
    return matches


def parse_all(paths: list[Path]) -> Iterator[Match]:
    """Parse many logs, dropping duplicates that appear in overlapping rotations."""
    seen: set[str] = set()
    for path in paths:
        for match in parse_log(path):
            if match.key in seen:
                continue
            seen.add(match.key)
            yield match


def follow(path: str | Path, poll_s: float = 1.0) -> Iterator[str]:
    """Tail a log, surviving the game rotating it out from under us."""
    import time

    path = Path(path)
    handle = None
    inode = None
    try:
        while True:
            if handle is None:
                if not path.is_file():
                    time.sleep(poll_s)
                    continue
                handle = path.open("r", encoding="utf-8", errors="replace")
                handle.seek(0, 2)
                inode = path.stat().st_size
            line = handle.readline()
            if line:
                yield line.rstrip("\n")
                continue
            time.sleep(poll_s)
            try:  # rotated or truncated -> reopen
                if path.stat().st_size < inode:
                    handle.close()
                    handle = None
                    continue
                inode = path.stat().st_size
            except OSError:
                handle.close()
                handle = None
    finally:
        if handle:
            handle.close()
