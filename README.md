# HuntAPI

Everything Hunt: Showdown 1896 knows about a player - stats, MMR, match results,
progression - and where to get it from.

Companion to [`Hunt-ify`](../Hunt-ify), which extracts the game's static data (items,
ballistics, maps, models). Hunt-ify answers *what exists in the game*; HuntAPI answers
*what happened to you*.

Read-only throughout: nothing here modifies the game install.

## The short version

Hunt's entire client API is recoverable from the game binary. The protobuf C++ runtime
keeps every generated file's `FileDescriptorProto` inside `GameHunt.dll`, so the complete
API definition can be read out without running the game:

```bash
python tools/dump_protos.py --game "E:/SteamLibrary/steamapps/common/Hunt Showdown 1896"
```

**100 descriptors, 89 `.proto` files, 32 services, 151 RPCs, 383 messages** at game
version 2.8.1.18, in a quarter of a second, and the whole set compiles clean under
`protoc`. See [docs/API_SURFACE.md](docs/API_SURFACE.md).

The backend is Crytek's in-house "CryStack" mesh - the Go package annotation on every
file is `huntshowdown.com/crystack/clientapi` - with Nakama for social and Vivox for
voice. It is not gRPC and not REST: a custom multiplexed protocol on TCP 61088, with
Orleans-style `grain_id` actor routing and subscription streams. Endpoint discovery is
public. See [docs/WIRE_FORMAT.md](docs/WIRE_FORMAT.md).

**The `attributes.xml` route every existing Hunt tracker uses is dead on this build.**
The `MissionBagPlayer_*` keys are gone from the file and the strings are gone from the
binary; match results now exist only on the wire. See
[docs/DATA_SOURCES.md](docs/DATA_SOURCES.md).

## Install

```bash
pip install -r requirements.txt
python tools/gen_bindings.py    # compile proto/ into huntapi/pb (needed for ingest/history)
```

Python 3.11+. The game install is auto-detected through the Steam registry entry and
`libraryfolders.vdf`; override with `--game <path>` or `HUNT_GAME_DIR`.

## Use

```bash
python -m huntapi scan
```

Parses every log in `USER/LogBackups` plus the live `Game.log`, and snapshots
`attributes.xml`, into `hunt.db`.

```bash
python -m huntapi summary
```

```
map           matches     avg      total
cemetery          106    958s        28h
creek              94   1086s        28h
civilwar           84   1000s        22h
colorado           79   1036s        22h
ALL               363               101h
```

Other commands:

| Command | What it does |
|---|---|
| `matches -n 20` | recent matches: time, map, duration, region, dedicated server, lobby |
| `unlocks` | diff the last two `attributes.xml` snapshots, resolved to item names |
| `servers --ping` | Hunt's live front-ends by region, with TCP handshake latency |
| `watch` | follow `Game.log` as you play |
| `ingest <blob>` | decode a raw `MetaMissionBag` into full match history |
| `history` | list decoded matches: map, duration, mode, death, PvP rating, players |
| `serve` | run the local HTTP API on `127.0.0.1:8848` |

## The local API

```bash
python -m huntapi serve
```

Read-only, bound to localhost, serving what you already collected. Interactive docs at
`/docs`.

| Route | Returns |
|---|---|
| `/stats` | headline aggregates: matches, playtime, K/D, distinct opponents |
| `/matches` | match lifecycle from `Game.log` - time, map, duration, region, server |
| `/history` | full decoded match records |
| `/history/{key}` | one match with every player and a timestamped kill graph |
| `/players` | opponents aggregated by profile, with average and peak MMR |
| `/players/{profile_id}` | one opponent's history against you |
| `/unlocks` | owned unlocks from the latest snapshot, resolved to item names |
| `/items/{simple_id}` | the full Hunt-ify record, including ballistics |
| `/servers?ping=true` | live front-ends by region, optionally timed |

`/items/{simple_id}` is the Hunt-ify join: an id out of a match or an unlock comes back
as a real weapon with its damage, muzzle velocity, rate of fire and ammo variants.

`unlocks` resolves numeric ids to real names through Hunt-ify's item catalog, so a diff
reads `Unlocks/0/1001  Crimson Varnish` rather than a bare integer.

## Layout

```
HuntAPI/
├─ PLAN.md                  # the five-phase plan and its open questions
├─ proto/<version>/         # recovered .proto files, one directory per game build
├─ docs/
│  ├─ API_SURFACE.md        # generated: every service, RPC and message
│  ├─ WIRE_FORMAT.md        # framing, routing, login, error codes
│  └─ DATA_SOURCES.md       # what each of the three sources actually gives you
├─ tools/
│  ├─ dump_protos.py        # GameHunt.dll -> .proto
│  ├─ diff_protos.py        # schema diff between two game builds
│  ├─ gen_bindings.py       # proto/ -> huntapi/pb python bindings
│  └─ gen_api_surface.py    # descriptors -> docs/API_SURFACE.md
├─ tests/                   # decoder round-trip + API route coverage
└─ huntapi/
   ├─ paths.py              # find the install and the files it writes
   ├─ catalog.py            # id -> item name and full record, via Hunt-ify
   ├─ config.py             # public endpoint discovery
   ├─ store.py              # SQLite
   ├─ api.py                # local read-only HTTP API
   ├─ cli.py
   ├─ pb/                   # generated protobuf bindings (not committed)
   ├─ wire/                 # MetaMissionBag decoder
   └─ sources/              # attributes.xml, Game.log
```

## Surviving game patches

Re-run the extractor after a patch and diff the schema:

```bash
python tools/dump_protos.py --game "<game>"
python tools/diff_protos.py proto/2.8.1.18 proto/2.8.2.0
```

Added and removed RPCs, messages, enum values and field-number changes all come out, so
a decoder breaking is a visible diff rather than a mystery.

## Match history

The `MetaMissionBag` decoder is built and tested: it turns one wire blob into a full
match record - every player's MMR and bot flag, a timestamped kill/down graph, bounty,
end reason - stored across `bags` / `bag_players` / `bag_kills`. Feed it a blob:

```bash
python -m huntapi ingest match.bin
python -m huntapi history
```

Two things are worth knowing before you count on this:

- **The server keeps no match history.** No RPC reads past matches back; the rich bag is
  a single transient slot the client drains after each match. Full history can only be
  built by capturing each bag going forward - see [PLAN.md](PLAN.md) Phase 4.
- **Getting the bag bytes is the gated step.** The backend is TLS 1.3 with a private CA
  and the game runs EasyAntiCheat, so there is no zero-risk capture. The methods and
  their ban implications are laid out in [docs/WIRE_FORMAT.md](docs/WIRE_FORMAT.md)
  section 9; the decoder is ready for real bytes the moment you choose one.

## Status

Phases 1, 2, 3 and 5 are done: schema recovery, local sources, endpoint intelligence and
the local API. Phase 4's decoder and storage are done and tested.

Not done, by choice: capturing real mission bags (no zero-risk method exists under
TLS 1.3 + EAC) and the Phase 4b active client (cannot be verified without sending your
credentials to Crytek from a non-game client). Both are explained in [PLAN.md](PLAN.md).
