# HuntAPI - plan

> **Status:** Phases 1, 2 and 3 are implemented and committed (v0.3.0). Phase 4 is the
> gated one and has not been started - it needs the terms-of-service decision in
> section 2 below. Phase 5 is unblocked and can follow whenever.

Goal: capture everything Hunt: Showdown 1896 knows about a player (stats, MMR, match
results, progression) by speaking the game's own client API, and expose it as a local
API/database that tools can build on.

Companion project: `../Hunt-ify` (static game data: items, ballistics, maps, models).
Hunt-ify answers "what exists in the game". HuntAPI answers "what happened to me".

---

## 1. What the reconnaissance found

### 1.1 The client API is protobuf over a custom TCP protocol

`bin/win_x64/GameHunt.dll` embeds the complete set of protobuf file descriptors for
Crytek's client API. They are recoverable in full - **100 descriptors parsed, 89 Hunt
`.proto` files, 32 services, 151 RPCs, 383 messages** - because the protobuf C++ runtime
stores each `FileDescriptorProto` as a serialised blob inside the binary.

The Go package annotation on every file is `huntshowdown.com/crystack/clientapi`, and
the C++ package is `FP` / `FP.Hunt` ("FP" = the CryStack front-plane). So the backend is
a Crytek in-house service mesh, with Heroic Labs **Nakama** bolted on for social and
**Vivox** for voice.

Transport, from `Header.proto`:

```
ReqHeader { invoking_id, grain_id, message_id, body_length, trace_parent }
ResHeader { message_id, body_length, error_code, error_message, unsubscribe_id, server_time }
```

`grain_id` (Orleans-style virtual actors) plus `unsubscribe_id` on the response side means
this is a **multiplexed, subscription-capable stream**, not request/response HTTP. Several
RPCs are server-push streams (`SubForNotifications`, `MissionUpdatesStream`,
`EventsStateStream`, `MaintenanceStream`, `ChatStream`, `DsReadyStream`).

Note there is **no gRPC** in the binary: the framing is Crytek's own.

### 1.2 Endpoint discovery is public

The client bootstraps from a hardcoded URL found in the DLL:

```
https://config.huntshowdown.com/v1/{ENV}/endpoints.json
```

`ENV` is `live`. That file is publicly fetchable and currently lists 22 front-ends:
`capi{1,2}-lv-<region>.huntshowdown.com:61088` for PC (regions: eu, us_east, us_west,
asia, oceania, russian, south_america) and `capi{1,2}-cnlv-*` for console, which
additionally carry a `certFetchingUrl`. This matches the log line
`ChangeConnectionStatus '0:Disconnected' -> '1:WaitingForEnvEndpoints'`.

Login sequence, straight from `Game.log`:

```
WaitingForEnvEndpoints -> WaitingForRegionSelect -> WaitingForGetAuthToken
-> WaitingForSteamAuthTicket -> ReadyForSigningIn -> SigningIn -> LoggedIn
```

So authentication is a **Steam session ticket** (`Auth.Login`), exchanged for an
`FP.Hunt.AuthToken` via `AuthSvc.GetToken`.

### 1.3 The old attributes.xml route is dead

Every existing Hunt tracker scrapes `USER/Profiles/default/attributes.xml` for
`MissionBagPlayer_*` keys. On the current build that is **gone**: the live file has 7,616
attributes and zero `MissionBag*` entries, and the string `MissionBagPlayer` no longer
exists anywhere in `GameHunt.dll`. What remains in attributes.xml is settings, `Unlocks/*`
(4,956 entries), `ActiveSkin/*`, favourites and news-read state - useful for an inventory
snapshot, useless for match results.

`Game.log` is likewise thin: lobby/session lifecycle, matchmaking tokens, session ids,
map/mission template names, region, news ids. No kills, no bounty, no MMR. Good for
"a match started/ended and here is its session id", nothing more.

**Conclusion: the protobuf API is the only route to real stats on this build.**

### 1.4 What the API actually exposes

The stat-bearing surface, by service:

| Service | RPC | What you get |
|---|---|---|
| `MetaService` | `ClientRequest(MetaRequest)` | the dispatcher - 90+ `MetaReqType` sub-commands, incl. `Req_GetFullBloodLine` |
| `MetaService` | `GetPublicInfo` | another player's `MetaPublicInfo`: rank, prestige, `mm_rating`, full `MetaBloodLineStats`, nakama_id |
| `MetaService` | `SubForNotifications` | server-push stream of meta changes |
| `MetaService` | `UpdateMissionBagFromClient` | the post-match write path |
| `ProfileService` | `GetProfile` / `QueryProfile` | own profile, regions, maintenance |
| `LeaderboardService` | `Get` | K/D, kills, deaths, kill streak, banishing streak, wellsprings, per game type, cross-platform toggle |
| `MatchMaking` | `GetPlayerSoloMMRating` | your MMR as an int |
| `EventLeaderboardService` | `GetLeaderboard`, `GetRewardingBracketsPoints` | live-event standings |
| `MissionService` | `GetAllActiveMissions`, `MissionUpdatesStream` | contract state |
| `MissionLogService` | `Log` | the per-match event bundle |

The single richest object is `MetaMissionBag` (the end-of-match bag). It carries, per
match: `mission_template_name`, `game_sub_mode`, `mission_duration`, `time_start_utc`,
`is_quick_play`, `bosses_in_mission`, health chunks, consumables used, accolades won
(with gems/bounty/exp/gold breakdown), loot boxes, reward events, live-event points and
boost percentages, `skill_based_pvp_rating`, and - the important part - a full
`teams[] -> players[]` roster where each `Player` has:

```
profile_id, blood_line_name, blood_line_anon_name, mm_rating, is_bot,
killed_by_me_ts[], killed_me_ts[], downed_by_me_ts[], downed_me_ts[],
killed_team_mate_ts[], killed_by_team_mate_ts[], pickedup_bounty_ts[],
extracted_bounty, extraction_ts, team_extraction, proximity_to_me
```

That is every opponent's MMR and a timestamped kill/down graph of the whole match. It is
strictly more than the old attributes.xml ever gave.

Career totals live in `MetaBloodLineStats` (33 fields: player kills, deaths, assists,
clues, banishes, teams wiped, monster kills, total bounty, boss kills, K/D, KDA,
`max_kill_distance`, per-weapon `MetaWeaponStats[]`, per-skin stats) plus
`MetaBloodLineSkillRating` (`skill_rating`, `skill_variance`, `clash_skill_rating`,
`clash_skill_variance`, `skill_based_pvp_rating`, `skill_based_bounty_rate`,
`skill_based_total_games`) and `MetaBloodLineCoreInfo` (gold, exp, rank, prestige,
`onlineTime`, `playedInGameTime`, gems, `mission_counter`, `away_days`).

---

## 2. Constraints to decide on before building

These are real and they shape the whole project, so they need a decision rather than an
assumption.

1. **Talking to the live backend with a non-game client is a ToS question.** Crytek's
   EULA prohibits unauthorised clients. Reading your *own* traffic is a different
   proposition from authenticating a homebrew client against `capi1-lv-eu`. There is also
   Easy Anti-Cheat in the process, though EAC does not police a separate process making
   its own TCP connections.
2. **The Steam auth ticket has to come from somewhere.** Legitimately it comes from
   `ISteamUser::GetAuthSessionTicket` for app 594650, which requires a running Steam
   client owning the game. That part is ordinary Steamworks usage.
3. **The framing is undocumented, but smaller than it looked.** The `.proto` messages are
   recovered; the bytes *around* them are not - the outer length delimiter, whether the
   stream is TLS, and how `max_part_size` splitting is encoded. The RPC id table, which
   looked like the big unknown, is not one: `ProtocolMetadata.GetProtocolInfo` is a
   pre-auth system call (`invoking_id 0x1000002`) that returns every RPC's name, id and
   channel. See [docs/WIRE_FORMAT.md](docs/WIRE_FORMAT.md).

**My recommendation:** build it in the order below, which front-loads all the value that
carries no risk, and treat the live-client work as an explicitly gated Phase 4 you decide
on separately once Phases 1-3 exist. Phases 1-3 alone already give a working match
tracker.

---

## 3. Plan

### Phase 1 - Land the schema (no risk) - DONE

Recovers 100/100 descriptors in 0.25s and the output compiles clean under protoc.

- `tools/dump_protos.py` - scan `GameHunt.dll` for `FileDescriptorProto` blobs
  (`0x0A`, varint len, name ending `.proto`), parse, re-render as `.proto` source.
- `proto/` - the 88 recovered files, committed, so the schema is diffable across patches.
- `tools/diff_protos.py` - compare two builds and report added/removed/renamed
  RPCs and fields. This is what makes the repo hold its value: every Hunt patch, re-run
  and see what Crytek changed.
- Generate Python bindings with `grpc_tools.protoc` (messages only, no gRPC stubs -
  there is no gRPC here).

Deliverable: a versioned, machine-readable description of the entire Hunt client API.

### Phase 2 - Local sources, no network - DONE

Everything obtainable without touching Crytek's servers, so there is a working tool even
if Phase 4 never happens.

- `huntapi/sources/attributes.py` - parse `attributes.xml` into a normalised inventory
  and unlock snapshot (`Unlocks/*`, `ActiveSkin/*`, loadouts, settings). Resolve item ids
  against Hunt-ify's `structured/items/` so unlocks come out as real weapon names.
- `huntapi/sources/gamelog.py` - tail `USER/Game.log` and emit lifecycle events:
  session start/end, matchmaking token, lobby sid, mission template, region, map. Handle
  the log rotating into `LogBackups/`.
- SQLite store: `profiles`, `snapshots`, `sessions`, `events`. Snapshot-diffing
  attributes.xml between sessions already yields "what you unlocked this session".

### Phase 3 - Static endpoint and schema intelligence - DONE

- `huntapi/config.py` - fetch and cache `config.huntshowdown.com/v1/live/endpoints.json`,
  expose the region list, ping the front-ends. Purely public data; useful on its own as a
  server-status feature.
- Document the wire format as far as it can be read statically: `docs/WIRE_FORMAT.md`,
  covering `ReqHeader`/`ResHeader`, the `RespErrorCode` enum, the grain/subscription
  model, and the login state machine transcribed from `Game.log`.

### Phase 4 - The client protocol (gated - needs your go-ahead)

Only start this once you have decided the ToS question above. Two sub-options, and they
differ a lot in risk:

- **4a, passive (recommended first):** observe your own client's traffic to
  `capi*:61088` and reconstruct the framing from real captures. No fake client, no
  authentication, nothing sent to Crytek that the game did not already send. This
  establishes the message-id table and confirms whether the stream is TLS-wrapped
  (`libssl-1_1.dll` ships with the game, so assume yes and expect to need key material
  from the process).
- **4b, active:** implement `huntapi/transport/` - connect, Steam-ticket login via
  `Auth.Login`, then call `ProfileService.GetProfile`,
  `MatchMaking.GetPlayerSoloMMRating`, `LeaderboardService.Get`,
  `MetaService.GetPublicInfo`, and subscribe to `MetaService.SubForNotifications` to
  capture each `MetaMissionBag` as it lands. This is the version that gives full match
  history with per-opponent MMR - and the version that needs an explicit decision.

### Phase 5 - Serve it

- `huntapi/api.py` - local FastAPI: `/profile`, `/stats`, `/matches`,
  `/matches/{id}`, `/leaderboard`, `/players/{profile_id}`, `/servers`.
- Join through to Hunt-ify: a match's weapon ids resolve to real ballistics, a
  mission template resolves to the POI map. That is the payoff of the two repos sitting
  side by side.
- Optional web UI reusing Hunt-ify's `usecases/` viewer pattern.

---

## 4. Suggested layout

```
HuntAPI/
├─ README.md
├─ PLAN.md                  # this file
├─ proto/                   # 88 recovered .proto files (per game build)
├─ docs/
│  ├─ API_SURFACE.md        # all 32 services / 151 RPCs, annotated
│  ├─ WIRE_FORMAT.md        # header framing, error codes, login state machine
│  └─ DATA_SOURCES.md       # protobuf vs attributes.xml vs Game.log, what each gives
├─ tools/
│  ├─ dump_protos.py        # DLL -> .proto (Phase 1)
│  └─ diff_protos.py        # build-over-build schema diff
└─ huntapi/
   ├─ sources/              # attributes.xml, Game.log  (Phase 2)
   ├─ config.py             # endpoint discovery        (Phase 3)
   ├─ transport/            # client protocol           (Phase 4, gated)
   ├─ store.py              # SQLite
   └─ api.py                # FastAPI                   (Phase 5)
```

## 5. Order of work

1. Phase 1 - schema landed and committed. Half a day, already prototyped.
2. `docs/API_SURFACE.md` + `docs/DATA_SOURCES.md` - the reference material, generated
   from the descriptors rather than written by hand.
3. Phase 2 - the local tracker that works today.
4. Phase 3 - endpoints and wire documentation.
5. Decide on Phase 4a/4b, then Phase 5.
