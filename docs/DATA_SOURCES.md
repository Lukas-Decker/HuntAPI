# Where Hunt's trackable data actually lives

Three places, with very different value. Measured against game build 2.8.1.18.

| Source | Needs network | Match results | Career stats | Status |
|---|---|---|---|---|
| `USER/Game.log` | no | lifecycle only | no | implemented |
| `USER/Profiles/default/attributes.xml` | no | **no longer** | no | implemented |
| Client API (protobuf, port 61088) | yes | everything | everything | schema recovered, transport not built |

## 1. attributes.xml - the route that used to work

Every Hunt tracker written before 1896 read `MissionBagPlayer_<team>_<player>_*` keys out
of this file after each match. That is how the community got per-opponent MMR.

**Those keys no longer exist.** On the current build:

* the live file holds 7,616 attributes and **zero** `MissionBag*` entries
* the strings `MissionBagPlayer`, `MissionBagEntry`, `MissionBagTeam` and
  `MissionBagNumPlayers` do not appear anywhere in `GameHunt.dll`

The mission bag still exists, but only as the `FP.Hunt.MetaMissionBag` protobuf message
travelling over the wire. `CUIMissionComponent::ScanLastMissionBagAnd*` reads it straight
from memory to draw the summary screen; nothing writes it to disk.

What attributes.xml still gives, and what `huntapi` parses:

| Key pattern | Count (this profile) | Meaning |
|---|---|---|
| `Unlocks/<category>/<simpleId>` | 4,956 | unlock state bitfield, 16 categories |
| `ItemsUserData/Favorited/<hexId>` | 1,506 | favourited items |
| `ActiveSkin/<n>` | 506 | `base item\|normal\|0=skin\|normal\|0` |
| `Loadout/*`, `FilterStates/*`, `NewsFeed/Item*` | ~250 | UI state |
| everything else | 648 | graphics, audio, input, HUD settings |

Both id forms resolve against Hunt-ify's `structured/items/*.json`: `simpleId` directly,
and the favourite ids as the decimal form of the catalog's hex `id`. Current hit rate is
3,479 of 4,312 non-zero unlocks and 1,376 of 1,506 favourites - the misses are items
added after Hunt-ify's 2.8.0.54 extraction, plus non-item unlocks such as profile badges,
frames and titles.

The unlock value is a small bitfield. Observed values are 0, 2, 8, 10, 16, 18 and 26, so
bits 2, 8 and 16 are in use. The game does not name these bits anywhere in the binary,
so `huntapi` reports the raw value and its decomposition rather than inventing labels.

Snapshot-diffing is the useful operation here: take one before a session and one after,
and the delta is exactly what you unlocked, favourited or re-skinned.

## 2. Game.log - match lifecycle

No kills, no bounty, no MMR. What it does carry, reliably, back through every rotated log
in `USER/LogBackups`:

| Field | Log line |
|---|---|
| match start | `EnterState() entering state eLS_Game(7)` |
| match end | `entering state eLS_Leaving(10)` / `eLS_None(0)` |
| map | `PrepareLevel 'levels/cemetery' (client)` |
| dedicated server | `ConnectToCoreGame(): join command: 'connect <session>X,IP:PORT'` |
| lobby session | `Lobby: <sid>` / `NewSessionRequest Success hNext=<sid>` |
| mission id | `mission=<uuid>, Lobby: <sid>` |
| game rules | `GameRulesChanged() newRules=InstantAction` |
| region | `[Account] Found region in config: eu` |
| matchmaking token | `CMatchMaking::Request response m_pendingRequest token = ,<n>` |

Two format quirks worth knowing, both handled:

* builds up to roughly 2.3 logged `PrepareLevel levels/cemetery` unquoted and with no
  `(client)`/`(server)` marker; current builds quote it and add the side
* log lines carry only `<HH:MM:SS>`, so the date comes from the `Log Started at` header
  and has to be rolled forward across midnight

Against this profile's 84 logs that yields **363 matches and 101 hours** of history,
every one with a map, and all but four with a server IP.

The shooting range (`levels/shooting`) and the menu are explicitly excluded, otherwise a
range visit leaks its level name into the next real match.

## 3. The client API - everything else

See [WIRE_FORMAT.md](WIRE_FORMAT.md) for the transport and
[API_SURFACE.md](API_SURFACE.md) for all 32 services and 151 RPCs.

The stat-bearing calls:

* `MetaService.ClientRequest` with `Req_GetFullBloodLine` - the whole profile
* `MetaService.GetPublicInfo` - any player's rank, prestige, MMR and career stats
* `MetaService.SubForNotifications` - push notification that a match result landed
* `MatchMaking.GetPlayerSoloMMRating` - your MMR as a plain int
* `LeaderboardService.Get` - K/D, kills, deaths, kill streak, banishing streak,
  wellsprings, per game mode, optionally cross-platform
* `ProfileService.GetProfile` / `QueryProfile`
* `EventLeaderboardService.GetLeaderboard`

`FP.Hunt.MetaMissionBag` is the payload that replaces the old attributes.xml scrape, and
it is strictly richer: per-match map, sub-mode, duration, start time, bosses, accolades
with full reward breakdown, live-event points and boosts, plus `teams[] -> players[]`
where each player carries `profile_id`, bloodline name, `mm_rating`, `is_bot`, and
timestamped `killed_by_me_ts[]`, `killed_me_ts[]`, `downed_by_me_ts[]`,
`downed_me_ts[]`, `pickedup_bounty_ts[]`, `extracted_bounty` and `extraction_ts`.

Career totals sit in `MetaBloodLineStats` (33 fields, including per-weapon kill counts
and `max_kill_distance`), `MetaBloodLineSkillRating` (`skill_rating`, `skill_variance`,
separate clash values) and `MetaBloodLineCoreInfo` (gold, exp, rank, prestige,
`onlineTime`, `playedInGameTime`, `mission_counter`).
