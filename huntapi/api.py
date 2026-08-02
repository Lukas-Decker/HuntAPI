"""Local read-only HTTP API over everything HuntAPI has collected.

    python -m huntapi serve

Serves the SQLite database as JSON and joins item ids through to Hunt-ify's catalog, so
a weapon id comes back with its real ballistics. Read-only and bound to localhost: it
exposes your own local data, nothing is sent anywhere.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import catalog, config
from .sources import attributes
from .store import Store


def rows(items: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in items]


def create_app(db_path: str = "hunt.db", items_dir: str | None = None,
               endpoint_cache: str = ".cache/endpoints.json") -> FastAPI:
    app = FastAPI(
        title="HuntAPI",
        version="0.5.0",
        summary="Local match history and stats for Hunt: Showdown 1896.",
    )
    items = catalog.Catalog(items_dir)

    def store() -> Store:
        if not Path(db_path).exists():
            raise HTTPException(503, f"database {db_path} not found - run 'huntapi scan' first")
        return Store(db_path)

    @app.get("/health")
    def health() -> dict:
        exists = Path(db_path).exists()
        return {
            "ok": True,
            "database": db_path,
            "database_present": exists,
            "catalog_available": items.available,
            "catalog_items": len(items),
        }

    @app.get("/stats")
    def stats() -> dict:
        with store() as s:
            return s.stats()

    @app.get("/matches")
    def matches(limit: int = Query(50, ge=1, le=1000)) -> list[dict]:
        """Match lifecycle from Game.log: time, map, duration, region, server."""
        with store() as s:
            return rows(s.recent_matches(limit))

    @app.get("/history")
    def history(limit: int = Query(50, ge=1, le=1000)) -> list[dict]:
        """Full match records decoded from MetaMissionBag."""
        with store() as s:
            return rows(s.recent_bags(limit))

    @app.get("/history/{match_key}")
    def history_detail(match_key: str) -> dict:
        with store() as s:
            bag = s.bag(match_key)
            if bag is None:
                raise HTTPException(404, "match not found")
            return {
                "match": dict(bag),
                "players": rows(s.bag_players(match_key)),
                "kills": rows(s.bag_kills(match_key)),
            }

    @app.get("/players")
    def players(min_appearances: int = Query(1, ge=1), limit: int = Query(100, ge=1, le=1000)) -> list[dict]:
        """Opponents seen across decoded matches, aggregated by profile."""
        with store() as s:
            return rows(s.opponents(min_appearances, limit))

    @app.get("/players/{profile_id}")
    def player(profile_id: int) -> dict:
        with store() as s:
            row = s.player(profile_id)
            if row is None:
                raise HTTPException(404, "player not seen in any decoded match")
            return dict(row)

    @app.get("/unlocks")
    def unlocks(resolved: bool = Query(True), limit: int = Query(500, ge=1, le=10000)) -> list[dict]:
        """Owned unlocks from the latest attributes snapshot, joined to item names."""
        with store() as s:
            raw = s.latest_snapshot_unlocks()
        out = []
        for name, value in raw.items():
            if not value or value == "0":
                continue
            entry: dict = {"key": name, "value": value}
            if resolved and (m := attributes.UNLOCK_KEY.match(name)):
                item = items.by_simple_id(int(m[2]))
                if item:
                    entry["name"] = item.name
                    entry["kind"] = item.kind
            out.append(entry)
            if len(out) >= limit:
                break
        return out

    @app.get("/items/{simple_id}")
    def item(simple_id: int) -> dict:
        """The full Hunt-ify record for an item id - the ballistics join."""
        record = items.record_by_simple_id(simple_id)
        if record is None:
            raise HTTPException(404, "item not in catalog (or catalog unavailable)")
        return record

    @app.get("/servers")
    def servers(ping: bool = Query(False), pc_only: bool = Query(True)) -> list[dict]:
        try:
            endpoints = config.load_cached(endpoint_cache)
        except OSError as exc:
            raise HTTPException(502, f"could not reach endpoint config: {exc}")
        result = []
        for e in endpoints:
            if pc_only and not e.is_pc:
                continue
            row = {"address": e.address, "port": e.port, "region": e.region,
                   "platforms": list(e.platforms)}
            if ping:
                row["latency_ms"] = config.tcp_latency(e)
            result.append(row)
        return result

    @app.get("/")
    def index() -> JSONResponse:
        return JSONResponse({
            "service": "HuntAPI",
            "endpoints": ["/health", "/stats", "/matches", "/history", "/history/{key}",
                          "/players", "/players/{profile_id}", "/unlocks", "/items/{simple_id}",
                          "/servers", "/docs"],
        })

    return app
