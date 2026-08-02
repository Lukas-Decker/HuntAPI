"""Decode a ``FP.Hunt.MetaMissionBag`` protobuf into flat match records.

This is the payload that replaces the old attributes.xml scrape. It arrives once per
match, over the wire, and this module turns one ``MetaMissionBag`` blob into a normalised
(match, players, kills) triple ready for the database - independent of how the bytes were
obtained. Feeding it a captured blob and feeding it a synthetic one go through exactly the
same path, which is what makes it testable without a live capture.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from huntapi.pb import MetaMissionBag_pb2

# Enum id -> label, transcribed from the recovered schema so the DB carries words.
MISSION_END_REASON = {
    0: "Unknown", 1: "EAC_Kicked", 2: "ClientDisconnected", 3: "MissionCompleted",
    4: "UserQuitGame", 5: "MissionExpired", 6: "ServerSystemError", 7: "Killed",
    8: "KilledByMonster", 9: "KilledByPlayer", 10: "KilledBySuicide", 11: "MissionNeverEnded",
}
MISSION_END_ACTION = {0: "Normal", 1: "KillHunter", 2: "RollBackProgress", 3: "PartialRewards"}
HUNTER_STATUS = {
    0: "Invalid", 1: "Alive", 2: "Dead", 3: "Downed", 4: "Burning", 5: "LeftTheMission",
}
MM_MODE = {0: "Fair", 1: "Full"}
MISSION_STATE = {0: "Empty", 1: "MissionStarted", 2: "MissionFinished", 3: "ContentsDumped"}


@dataclass
class PlayerRecord:
    match_key: str
    team_index: int
    profile_id: int
    blood_line_name: str
    is_bot: bool
    mm_rating: int
    matchmaking_mode: str
    extracted_bounty: int
    team_extraction: bool
    extraction_ts: int
    killed_by_me: int          # counts, derived from the timestamp arrays
    killed_me: int
    downed_by_me: int
    downed_me: int
    proximity_to_me: bool


@dataclass
class KillEvent:
    match_key: str
    profile_id: int             # the other player in the event
    team_index: int
    direction: str              # killed_by_me | killed_me | downed_by_me | downed_me | ...
    mission_time_s: int         # timestamp as carried in the bag


@dataclass
class MatchRecord:
    key: str
    state: str
    is_quick_play: bool
    is_tutorial: bool
    game_sub_mode: str
    mission_template: str
    mission_counter: int
    mission_duration_s: int
    time_start_utc: int
    dead: bool
    hunter_status: str
    mission_end_reason: str
    mission_end_action: str
    skill_based_pvp_rating: int
    times_killed: int
    character_name: str
    character_level: int
    bosses_in_mission: int
    num_teams: int
    num_players: int
    my_profile_id: int
    players: list[PlayerRecord] = field(default_factory=list)
    kills: list[KillEvent] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "key": self.key,
            "map": self.mission_template,
            "mode": "QuickPlay" if self.is_quick_play else (self.game_sub_mode or "BountyHunt"),
            "duration_s": self.mission_duration_s,
            "died": self.dead,
            "end_reason": self.mission_end_reason,
            "teams": self.num_teams,
            "players": self.num_players,
            "my_kills": sum(1 for k in self.kills if k.direction == "killed_by_me"),
            "kills_recorded": len(self.kills),
        }


_DIRECTIONS = (
    ("killed_by_me_ts", "killed_by_me"),
    ("killed_me_ts", "killed_me"),
    ("downed_by_me_ts", "downed_by_me"),
    ("downed_me_ts", "downed_me"),
    ("killed_by_team_mate_ts", "killed_by_team_mate"),
    ("killed_team_mate_ts", "killed_team_mate"),
    ("downed_by_team_mate_ts", "downed_by_team_mate"),
    ("downed_team_mate_ts", "downed_team_mate"),
)


def make_key(bag) -> str:
    """A stable identity for a match.

    The bag has no match id of its own, so we key on the fields that together are unique
    for a given player's view of a match: their profile, when it started, and the mission
    counter. Falls back to a content hash if those are absent.
    """
    parts = [str(bag.mission_counter), str(bag.time_start_utc)]
    me = _find_me(bag)
    if me is not None:
        parts.append(str(me.profile_id))
    key_source = "|".join(parts)
    if key_source.strip("|0"):
        return hashlib.sha1(key_source.encode()).hexdigest()[:16]
    return hashlib.sha1(bag.SerializeToString()).hexdigest()[:16]


def _find_me(bag):
    """The player entry flagged proximity_to_me is the local player's own row."""
    for team in bag.teams:
        for player in team.players:
            if player.proximity_to_me:
                return player
    return None


def decode(blob: bytes) -> MatchRecord:
    """Parse raw MetaMissionBag bytes into a MatchRecord."""
    bag = MetaMissionBag_pb2.MetaMissionBag()
    bag.ParseFromString(blob)
    return from_message(bag)


def from_message(bag) -> MatchRecord:
    key = make_key(bag)
    me = _find_me(bag)

    players: list[PlayerRecord] = []
    kills: list[KillEvent] = []
    for team_index, team in enumerate(bag.teams):
        for player in team.players:
            players.append(PlayerRecord(
                match_key=key,
                team_index=team_index,
                profile_id=player.profile_id,
                blood_line_name=player.blood_line_name or player.blood_line_anon_name,
                is_bot=player.is_bot,
                mm_rating=player.mm_rating,
                matchmaking_mode=MM_MODE.get(player.matchmaking_mode, str(player.matchmaking_mode)),
                extracted_bounty=player.extracted_bounty,
                team_extraction=player.team_extraction,
                extraction_ts=player.extraction_ts,
                killed_by_me=len(player.killed_by_me_ts),
                killed_me=len(player.killed_me_ts),
                downed_by_me=len(player.downed_by_me_ts),
                downed_me=len(player.downed_me_ts),
                proximity_to_me=player.proximity_to_me,
            ))
            for attr, direction in _DIRECTIONS:
                for ts in getattr(player, attr):
                    kills.append(KillEvent(
                        match_key=key,
                        profile_id=player.profile_id,
                        team_index=team_index,
                        direction=direction,
                        mission_time_s=ts,
                    ))

    return MatchRecord(
        key=key,
        state=MISSION_STATE.get(bag.state, str(bag.state)),
        is_quick_play=bag.is_quick_play,
        is_tutorial=bag.is_tutorial,
        game_sub_mode=bag.game_sub_mode,
        mission_template=bag.mission_template_name,
        mission_counter=bag.mission_counter,
        mission_duration_s=bag.mission_duration,
        time_start_utc=bag.time_start_utc,
        dead=bag.dead,
        hunter_status=HUNTER_STATUS.get(bag.hunter_status, str(bag.hunter_status)),
        mission_end_reason=MISSION_END_REASON.get(bag.mission_end_reason, str(bag.mission_end_reason)),
        mission_end_action=MISSION_END_ACTION.get(bag.mission_end_action, str(bag.mission_end_action)),
        skill_based_pvp_rating=bag.skill_based_pvp_rating,
        times_killed=bag.times_killed,
        character_name=bag.player_char.name_ui or bag.player_char.name,
        character_level=bag.player_char.level,
        bosses_in_mission=bag.bosses_in_mission,
        num_teams=len(bag.teams),
        num_players=sum(len(t.players) for t in bag.teams),
        my_profile_id=me.profile_id if me else 0,
        players=players,
        kills=kills,
    )
