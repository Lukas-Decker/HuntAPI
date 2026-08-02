"""Round-trip test for the mission-bag decoder.

Builds a realistic MetaMissionBag from the recovered schema, serialises it to the same
bytes the game would put on the wire, then runs it through decode + store + read-back.
Proves the decode pipeline works before any real capture exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huntapi.pb import MetaMissionBag_pb2
from huntapi.store import Store
from huntapi.wire import mission_bag


def build_bag() -> MetaMissionBag_pb2.MetaMissionBag:
    """A two-team bounty match: I kill one enemy, get downed once, extract with bounty."""
    bag = MetaMissionBag_pb2.MetaMissionBag()
    bag.state = 2  # MissionFinished
    bag.mission_template_name = "levels/civilwar"
    bag.game_sub_mode = ""
    bag.mission_counter = 1337
    bag.mission_duration = 1058
    bag.time_start_utc = 1_770_000_000
    bag.dead = False
    bag.hunter_status = 1  # Alive
    bag.mission_end_reason = 3  # MissionCompleted
    bag.skill_based_pvp_rating = 2450
    bag.times_killed = 1
    bag.bosses_in_mission = 2
    bag.player_char.name = "Cassandra"
    bag.player_char.name_ui = "Cassandra"
    bag.player_char.level = 42

    me_team = bag.teams.add()
    me_team.mm_rating = 4
    me = me_team.players.add()
    me.profile_id = 111
    me.blood_line_name = "me"
    me.mm_rating = 4
    me.proximity_to_me = True
    me.extracted_bounty = 2
    me.team_extraction = True
    me.extraction_ts = 1000
    me.downed_me_ts.append(600)  # I was downed once at t=600

    enemy_team = bag.teams.add()
    enemy_team.mm_rating = 5
    foe = enemy_team.players.add()
    foe.profile_id = 222
    foe.blood_line_name = "victim"
    foe.mm_rating = 5
    foe.killed_by_me_ts.append(540)   # I killed them at t=540
    foe.downed_by_me_ts.append(500)   # after downing them at t=500
    bot = enemy_team.players.add()
    bot.profile_id = 0
    bot.blood_line_name = "AI Hunter"
    bot.is_bot = True
    bot.mm_rating = 2
    return bag


def test_roundtrip(tmp_path):
    original = build_bag()
    blob = original.SerializeToString()
    assert blob, "serialisation produced no bytes"

    match = mission_bag.decode(blob)

    assert match.mission_template == "levels/civilwar"
    assert match.mission_end_reason == "MissionCompleted"
    assert match.hunter_status == "Alive"
    assert match.num_teams == 2
    assert match.num_players == 3
    assert match.my_profile_id == 111
    assert match.character_name == "Cassandra"

    # one kill by me, one down by me, one down of me -> three kill events
    directions = sorted(k.direction for k in match.kills)
    assert directions == ["downed_by_me", "downed_me", "killed_by_me"]

    me = next(p for p in match.players if p.proximity_to_me)
    assert me.extracted_bounty == 2 and me.team_extraction
    assert me.downed_me == 1

    foe = next(p for p in match.players if p.profile_id == 222)
    assert foe.killed_by_me == 1 and foe.downed_by_me == 1 and foe.mm_rating == 5

    bot = next(p for p in match.players if p.is_bot)
    assert bot.blood_line_name == "AI Hunter"

    # store + read back
    store = Store(tmp_path / "t.db")
    assert store.add_bag(match, raw=blob) is True
    assert store.add_bag(match, raw=blob) is False  # idempotent on match key
    assert store.count_bags() == 1
    row = store.recent_bags()[0]
    assert row["mission_template"] == "levels/civilwar"
    assert row["skill_based_pvp_rating"] == 2450
    players = store.bag_players(match.key)
    assert len(players) == 3
    assert players[0]["mm_rating"] >= players[-1]["mm_rating"]  # ordered by rating desc
    store.close()

    # summary shape
    summary = match.summary()
    assert summary["my_kills"] == 1
    assert summary["players"] == 3
    print("summary:", summary)


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_roundtrip(Path(d))
    print("OK")
