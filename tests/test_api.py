"""Exercise every API route in-process against a seeded database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

from huntapi.api import create_app
from huntapi.sources.gamelog import Match
from huntapi.store import Store
from huntapi.wire import mission_bag
from test_mission_bag import build_bag


def seed(db_path: Path) -> str:
    store = Store(db_path)
    match = mission_bag.decode(build_bag().SerializeToString())
    store.add_bag(match, raw=b"\x00")
    from datetime import datetime
    store.add_matches([Match(
        lobby_sid="123", mission_id="abc", game_rules="InstantAction",
        level="levels/civilwar", server_ip="1.2.3.4", server_port=22274,
        region="eu", started_at=datetime(2026, 7, 27, 19, 24),
        ended_at=datetime(2026, 7, 27, 19, 41), source_log="test.log",
    )])
    store.close()
    return match.key


def test_api(tmp_path: Path):
    db = tmp_path / "api.db"
    match_key = seed(db)
    client = TestClient(create_app(db_path=str(db)))

    health = client.get("/health").json()
    assert health["ok"] and health["database_present"]
    print("health:", health)

    stats = client.get("/stats").json()
    assert stats["decoded_matches"] == 1
    assert stats["log_matches"] == 1
    assert stats["player_kills"] == 1
    assert stats["distinct_opponents"] == 1  # the bot has profile_id 0 and is excluded
    print("stats:", stats)

    matches = client.get("/matches").json()
    assert len(matches) == 1 and matches[0]["map"] == "civilwar"

    history = client.get("/history").json()
    assert len(history) == 1 and history[0]["mission_template"] == "levels/civilwar"

    detail = client.get(f"/history/{match_key}").json()
    assert len(detail["players"]) == 3
    assert len(detail["kills"]) == 3
    assert detail["match"]["skill_based_pvp_rating"] == 2450
    print("detail players:", [(p["blood_line_name"], p["mm_rating"]) for p in detail["players"]])

    assert client.get("/history/nope").status_code == 404

    players = client.get("/players").json()
    assert any(p["profile_id"] == 222 and p["times_i_killed_them"] == 1 for p in players)
    one = client.get("/players/222").json()
    assert one["avg_mm_rating"] == 5
    assert client.get("/players/999999").status_code == 404

    # ballistics join through Hunt-ify, when the catalog is present
    if health["catalog_available"]:
        item = client.get("/items/1387").json()   # 1865 Carbine
        assert item["uiName"] == "1865 Carbine"
        assert "ballistics" in item
        print("join ok:", item["uiName"], "->", item["kind"], "| has ballistics")
        assert client.get("/items/99999999").status_code == 404

    index = client.get("/").json()
    assert "/stats" in index["endpoints"]
    print("routes:", len(index["endpoints"]))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        test_api(Path(d))
    print("OK")
