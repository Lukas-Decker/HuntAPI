"""Command line entry point.

    python -m huntapi scan          # parse logs + attributes.xml into the database
    python -m huntapi matches       # recent matches
    python -m huntapi summary       # per-map totals
    python -m huntapi unlocks       # what changed between the last two snapshots
    python -m huntapi servers       # live front-ends, with latency
    python -m huntapi watch         # follow the log as you play
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import catalog, config, paths
from .sources import attributes, gamelog
from .store import Store

DEFAULT_DB = "hunt.db"
DEFAULT_CACHE = ".cache/endpoints.json"


def cmd_scan(args) -> int:
    game = paths.resolve(args.game)
    with Store(args.db) as store:
        logs = game.all_logs()
        added = store.add_matches(gamelog.parse_all(logs))
        print(f"logs scanned : {len(logs)}")
        print(f"matches added: {added} (total {store.count_matches()})")

        if game.attributes.is_file():
            snapshot = attributes.parse(game.attributes)
            snapshot_id = store.add_snapshot(snapshot)
            if snapshot_id is None:
                print("attributes   : unchanged since last snapshot")
            else:
                info = snapshot.summary()
                print(f"attributes   : snapshot #{snapshot_id}, {info['attributes']} attrs, "
                      f"{info['unlocked_nonzero']} unlocks, {info['favorites']} favourites")
        else:
            print(f"attributes   : not found at {game.attributes}")
    return 0


def cmd_matches(args) -> int:
    with Store(args.db) as store:
        rows = store.recent_matches(args.limit)
        if not rows:
            print("No matches recorded. Run 'scan' first.")
            return 0
        print(f"{'started':<20} {'map':<10} {'dur':>6}  {'region':<8} {'server':<16} lobby")
        for row in rows:
            started = (row["started_at"] or "")[:19].replace("T", " ")
            duration = f"{row['duration_s']}s" if row["duration_s"] is not None else "-"
            print(f"{started:<20} {row['map'] or '-':<10} {duration:>6}  "
                  f"{row['region'] or '-':<8} {row['server_ip'] or '-':<16} {row['lobby_sid'] or '-'}")
    return 0


def cmd_summary(args) -> int:
    with Store(args.db) as store:
        rows = store.match_summary()
        if not rows:
            print("No matches recorded. Run 'scan' first.")
            return 0
        total = sum(r["matches"] for r in rows)
        seconds = sum(r["total_s"] or 0 for r in rows)
        print(f"{'map':<12} {'matches':>8} {'avg':>7} {'total':>10}")
        for row in rows:
            print(f"{row['map']:<12} {row['matches']:>8} {row['avg_duration_s']:>6}s "
                  f"{(row['total_s'] or 0) // 3600:>9}h")
        print(f"{'ALL':<12} {total:>8} {'':>7} {seconds // 3600:>9}h")
    return 0


def cmd_unlocks(args) -> int:
    items = catalog.Catalog(args.items)
    with Store(args.db) as store:
        snapshots = store.snapshots()
        if len(snapshots) < 2:
            print(f"Need two snapshots to diff, have {len(snapshots)}. "
                  "Run 'scan' again after playing.")
            return 0
        old, new = snapshots[-2], snapshots[-1]
        delta = store.diff_snapshots(old["id"], new["id"])
        print(f"snapshot #{old['id']} ({old['taken_at']}) -> #{new['id']} ({new['taken_at']})")
        for kind in ("added", "changed", "removed"):
            entries = delta[kind]
            if not entries:
                continue
            print(f"\n{kind} ({len(entries)}):")
            for name, (before, after) in sorted(entries.items())[: args.limit]:
                label = _describe(name, items)
                arrow = f"{before!r} -> {after!r}" if kind == "changed" else repr(after or before)
                print(f"  {label:<52} {arrow}")
            if len(entries) > args.limit:
                print(f"  ... and {len(entries) - args.limit} more")
    return 0


def _describe(name: str, items: catalog.Catalog) -> str:
    """Turn a raw attribute key into something readable where we can."""
    if match := attributes.UNLOCK_KEY.match(name):
        item = items.by_simple_id(int(match[2]))
        return f"{name}  {item.name}" if item else name
    if match := attributes.FAVORITE_KEY.match(name):
        item = items.by_hex_id(int(match[1]))
        return f"Favorited  {item.name}" if item else name
    return name


def cmd_servers(args) -> int:
    endpoints = config.load_cached(args.cache, env=args.env)
    grouped = config.regions(endpoints, pc_only=not args.all_platforms)
    print(f"{len(endpoints)} endpoints from {config.CONFIG_URL.format(env=args.env)}\n")
    for region, group in grouped.items():
        print(f"{region}:")
        for endpoint in group:
            latency = ""
            if args.ping:
                ms = config.tcp_latency(endpoint)
                latency = f"  {ms:6.1f} ms" if ms is not None else "  unreachable"
            print(f"  {endpoint.address}:{endpoint.port}{latency}")
    return 0


def cmd_watch(args) -> int:
    game = paths.resolve(args.game)
    print(f"following {game.game_log} (ctrl-c to stop)")
    interesting = (
        gamelog.PREPARE_LEVEL, gamelog.ENTER_STATE, gamelog.JOIN,
        gamelog.MISSION, gamelog.CONNECTION, gamelog.GAME_RULES,
    )
    try:
        for line in gamelog.follow(game.game_log):
            if any(pattern.search(line) for pattern in interesting):
                print(line.strip())
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="huntapi", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=DEFAULT_DB, help=f"database path (default: {DEFAULT_DB})")
    sub = ap.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="parse logs and attributes.xml into the database")
    scan.add_argument("--game", help="Hunt install directory (auto-detected if omitted)")
    scan.set_defaults(func=cmd_scan)

    matches = sub.add_parser("matches", help="list recent matches")
    matches.add_argument("-n", "--limit", type=int, default=20)
    matches.set_defaults(func=cmd_matches)

    summary = sub.add_parser("summary", help="per-map totals")
    summary.set_defaults(func=cmd_summary)

    unlocks = sub.add_parser("unlocks", help="diff the last two attribute snapshots")
    unlocks.add_argument("--items", type=Path, help="Hunt-ify structured/items directory")
    unlocks.add_argument("-n", "--limit", type=int, default=40)
    unlocks.set_defaults(func=cmd_unlocks)

    servers = sub.add_parser("servers", help="list Hunt's live front-end endpoints")
    servers.add_argument("--env", default=config.DEFAULT_ENV)
    servers.add_argument("--cache", default=DEFAULT_CACHE)
    servers.add_argument("--ping", action="store_true", help="measure TCP handshake latency")
    servers.add_argument("--all-platforms", action="store_true", help="include console endpoints")
    servers.set_defaults(func=cmd_servers)

    watch = sub.add_parser("watch", help="follow Game.log live")
    watch.add_argument("--game")
    watch.set_defaults(func=cmd_watch)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
