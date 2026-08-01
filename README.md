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
│  └─ gen_api_surface.py    # descriptors -> docs/API_SURFACE.md
└─ huntapi/
   ├─ paths.py              # find the install and the files it writes
   ├─ catalog.py            # id -> item name, via Hunt-ify
   ├─ config.py             # public endpoint discovery
   ├─ store.py              # SQLite
   ├─ cli.py
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

## Status

Phases 1-3 of [PLAN.md](PLAN.md) are done: schema recovery, local sources, endpoint
intelligence. Phase 4, speaking the protocol to Crytek's servers, is deliberately not
started - it is a terms-of-service decision, not a technical one, and the reasoning is
written up in the plan.
