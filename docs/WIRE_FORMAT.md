# Hunt client protocol - what is known

Everything here is derived statically, from the schema recovered out of `GameHunt.dll`
(game 2.8.1.18), from strings in the binary, and from `USER/Game.log`. Nothing in this
document required connecting to Crytek's servers with anything other than a browser.

Where something is inferred rather than observed, it says so.

## 1. Bootstrap

The client starts with one hardcoded HTTPS URL, found in `GameHunt.dll`:

```
https://config.huntshowdown.com/v1/{ENV}/endpoints.json
```

`ENV` comes from the launcher's `+online_account_client_settings live` argument, visible
in the `Command Line:` line of every `Game.log`. The response is a plain public JSON
array; `huntapi servers` fetches and caches it.

Each entry is `{address, port, pingPort, certFetchingUrl, region, measureInput, platforms}`.
Live currently returns 22 front-ends: `capi{1,2}-lv-<region>.huntshowdown.com:61088` for
PC across seven regions (eu, us_east, us_west, asia, oceania, russian, south_america),
and `capi{1,2}-cnlv-*` for console, which additionally carry a `certFetchingUrl` pointing
at `xboxcert-<region>.huntshowdown.com`.

All PC front-ends accept TCP on **61088** and complete a handshake, so reachability can
be measured without speaking the protocol at all.

## 2. Login state machine

Transcribed from `Game.log`, `[Hunt Online] [Account] ChangeConnectionStatus`:

```
0  Disconnected
1  WaitingForEnvEndpoints      <- fetch endpoints.json
3  WaitingForRegionSelect      <- "Found region in config: eu"
4  WaitingForGetAuthToken
5  WaitingForSteamAuthTicket   <- Steamworks GetAuthSessionTicket, app 594650
6  ReadyForSigningIn
8  SigningIn                   <- FP.Auth.Login
10 LoggedIn
```

State 2, 7 and 9 were not observed in any captured log; presumably platform paths other
than Steam.

## 3. Framing

Two messages wrap every call (`Header.proto`):

```protobuf
message ReqHeader {
  uint32 invoking_id  = 1;   // which service/RPC - see the id table below
  int64  grain_id     = 2;   // target actor instance
  uint32 message_id   = 3;   // correlation id, echoed in the response
  int32  body_length  = 4;   // length of the protobuf body that follows
  string trace_parent = 5;   // W3C traceparent; the client also ships OpenTelemetry
}

message ResHeader {
  uint32 message_id     = 1;
  int32  body_length    = 2;
  uint32 error_code     = 3;   // FP.RespErrorCode
  string error_message  = 4;
  uint32 unsubscribe_id = 5;   // non-zero => this is a subscription delivery
  int64  server_time    = 6;
}
```

`body_length` being *inside* the header means the header itself must be delimited by
something outside protobuf - a fixed-size or varint length prefix on the wire. **That
outer delimiter is the one piece not recoverable from the binary's descriptors** and is
the main thing a passive capture needs to establish.

Assume the stream is TLS: the game ships `libcrypto-1_1.dll` and `libssl-1_1.dll`, and
the console endpoints advertise a `certFetchingUrl`.

## 4. Routing: `invoking_id`

`ProtocolMetadata.proto` names the service classes. The top byte selects the class:

| Value | Hex | Meaning |
|---|---|---|
| 0 | `0x0` | `HeartBeatId` |
| 16777216 | `0x1000000` | `SystemCallService` / `AuthLogin` |
| 16777217 | `0x1000001` | `AuthLogout` |
| 16777218 | `0x1000002` | `GetProtocolInfo` |
| 16777219 | `0x1000003` | `SystemCallServiceEnd` |
| 33554432 | `0x2000000` | `GrainService` - the normal per-actor RPC path |
| 50331648 | `0x3000000` | `ObserverService` - subscriptions |
| 67108864 | `0x4000000` | `ObserverUnsubscribeService` |
| 83886080 | `0x5000000` | `GatewayExtService` |
| 127506841 | `0x7999999` | `EndInvokingId` |

### The RPC id table is not a reverse-engineering problem

`FP.ProtocolMetadata.GetProtocolInfo` is a **system call**, in the pre-auth
`0x1000002` slot, and it returns:

```protobuf
message RpcInfo     { string name = 1; uint32 id = 2; string channel = 3; }
message InvokeInfo  { FP.Version version = 1; repeated FP.RpcInfo rpc_infos = 2; }
message ChannelInfo { uint32 id = 1; string name = 2; uint32 max_pending_messages = 3; uint32 max_part_size = 4; }
message ProtocolInfo { FP.InvokeInfo invoking_infos = 1; repeated FP.ChannelInfo channel_infos = 2; string instance = 3; }
```

So the server hands out the complete name-to-id mapping for every RPC, plus the channel
list and per-channel message-size limits. Anything that can frame a single request can
ask for the whole routing table rather than deriving it. `ChannelInfo.max_part_size`
further implies large payloads are split across parts.

## 5. Subscriptions

No RPC in the schema uses protobuf streaming. Subscriptions are ordinary unary calls
that the server keeps answering, correlated by `ResHeader.unsubscribe_id` and torn down
through `ObserverUnsubscribeService`.

`ServiceExt.proto` defines a `MethodType { ReqRsp, PubSub }` enum, so each method is
tagged one way or the other in Crytek's source. That tag does **not** survive into the
binary: all 151 recovered methods carry empty `MethodOptions`, and the custom option
extension itself is not among the recovered descriptors. Until `GetProtocolInfo` is
called for the real table, the subscription set below is inferred from RPC naming and
from the behaviour visible in `Game.log`, not read from the schema:
`MissionService.MissionUpdatesStream`, `EventsService.EventsStateStream`,
`PlatformService.MaintenanceStream`, `PlatformService.ReleasePackStream`,
`ChatService.ChatStream`, `MissionReconnectService.DsReadyStream`,
`Vivox.VoipRemoteMute`, `ChatService.ChatRemoteMute`, `DediServer.CommandStream` and
`DediServer.ProfilingStream`.

`MetaService.SubForNotifications -> MetaServerNotification` is the one that matters for
stats: it is how the client learns a match result landed.

## 6. Authentication

`FP.Auth.Login` takes `AuthLoginReq`:

| Field | Notes |
|---|---|
| `protocol_version` | `EProtocolVersion_Current = 3` |
| `game` | game identifier string |
| `game_version` | `FP.Version {major_, minor_, build, revision}` - must match the build |
| `session_type` | `Client`, `DedicatedServer` or `GmTool` |
| `user_type` | `FP.EUserType` |
| `platform_variant` | platform string |
| `token` / `id_token` / `id_pwd` / `id_str_token` | four alternative credential shapes |
| `meta_signature` | signature echoed back in `AuthResult` |

`AuthResult` returns `authorized`, a session `token`, `session_id`, `profile_id`,
`FP.ClientConfig`, `nakama_id`, and on Xbox a set of `XboxTokens`. `AuthSvc.GetToken`
then yields an `FP.Hunt.AuthToken { exp_unix_time, key }` - a 128-bit key with an
expiry, so tokens are refreshed rather than long-lived.

The `nakama_id` on both `AuthResult` and `MetaPublicInfo` confirms Heroic Labs Nakama
sits behind the social side; `Nakama.proto` exposes only `Connect` and `Disconnect`,
so the client is handed a Nakama session and talks to it separately.

## 7. Error codes

`FP.RespErrorCode`, low-level range only (the enum reserves 10-1000 for transport-level
failures and stops there; application errors ride in per-service messages):

| Code | Name |
|---|---|
| 0 | `Success` |
| 1 | `SuccessNoChange` |
| 2 | `SuccessEventualConsistency` |
| 3 | `SuccessEquivalent` |
| 9 | `SuccessHeartBeat` |
| 10 | `GeneralError` / `ErrorsBegin` / `LowLevelErrorsBegin` |
| 12 | `RpcNotFound` |
| 13 | `ServiceUnavailable` |
| 15 | `FepOverload` |
| 16 | `RequestTimeout` |
| 17 | `UnauthenticatedRequest` |
| 18 | `BaseProtocolMismatch` |
| 1000 | `LowLevelErrorsEnd` |

`BaseProtocolMismatch` is what a wrong `protocol_version` or `game_version` produces.

## 8. What is still unknown

1. The outer framing around `ReqHeader` - length prefix format, and whether a magic or
   version byte precedes it.
2. Whether the stream is TLS and, if so, which cert validation the client performs.
3. Compression, and how `ChannelInfo.max_part_size` splitting is encoded.
4. The concrete `grain_id` values for each service.

All four are answerable from a passive capture of the game's own traffic. None of them
require guessing.
