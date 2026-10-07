# The ATAK bridge API

The HTTP interface between NetWatch Desktop and the ATAK plugin. Documented so
you can write your own client — a dashboard, a script, a different TAK client.

Implemented in [`../desktop/app/netwatch/atakbridge.py`](../desktop/app/netwatch/atakbridge.py);
the reference client is [`../NetWatch-TAK/app/src/main/java/com/atakmap/android/netwatch/net/BridgeClient.java`](../NetWatch-TAK/app/src/main/java/com/atakmap/android/netwatch/net/BridgeClient.java).

## Why it exists separately

NetWatch's own API binds `127.0.0.1` on an OS-assigned port and authenticates
with a token regenerated every launch. Correct for a desktop UI talking to
itself, and useless to a tablet: nothing off the machine can reach it, and the
token changes before anyone could write it down.

So the bridge is a second listener with different trade-offs — a fixed port, a
long-lived pair code, read-only, and **off until the operator switches it on**.

## Connecting

- **Base URL** `http://<host>:8787` (port configurable)
- **Auth** `X-NetWatch-Pair: ABCD-EFGH-JKMN-PQRT` on every call except
  `/api/hello`. `?code=` also works, for curl.
- **Transport** plain HTTP. See [Security](#security).

Codes are case-insensitive and separators are ignored, so `abcdefghjkmnpqrt`
works. They're built from an alphabet with every confusable pair removed —
no `O`/`0`, `I`/`1`, `S`/`5`, `B`/`8` — so there's nothing to misread off a
screen and nothing to guess at on input.

## Discovery

Broadcast `NETWATCH-DISCOVER?` as UDP to port **8788**. Every NetWatch with its
bridge on replies with JSON:

```json
{"product":"NetWatch","app":"NetWatch","version":"2.4.1","api":1,
 "host":"WORKSHOP-PC","port":8787}
```

The reply deliberately **does not contain the pair code** — handing a credential
to anything that asks would defeat having one. Trust the datagram's source
address over any address inside the payload.

**This cannot work over a VPN.** Tailscale, WireGuard and IPsec are
point-to-point and carry no broadcast domain. Use the pairing link instead.

## Pairing link

One string carrying everything a client needs:

```
netwatch://pair?h=<host>&p=<port>&c=<pair code>&n=<friendly name>
```

The desktop app emits one **per address**, because a PC on both a LAN and a VPN
has several and only one is reachable from any given client.

## Endpoints

Everything is `GET` and read-only except `POST /api/poll`.

### `GET /api/hello` — no auth

Identity only, so a client can say "found NetWatch, now enter the code" instead
of showing a bare 401. No counts, no names, no positions.

### `GET /api/status`

```json
{"ok":true,"app":"NetWatch","product":"NetWatch","version":"2.4.1",
 "edition":"retail","api":1,"host":"WORKSHOP-PC","tags":3,"fixes":2,
 "reports":1420,"last_sync":"2026-10-06T18:22:41+00:00",
 "time":"2026-10-06T18:40:02+00:00","unix":1791398402.1}
```

`tags` is how many exist; `fixes` is how many have ever been located.

### `GET /api/fixes`

Current position of every tag.

```json
{"ok":true,"unix":1791398402.1,"fixes":[{
  "id":1,"uid":"netwatch-a1b2c3d4e5f60718","name":"Bike","color":"#2dd4bf",
  "show_track":true,"visible":true,"notes":"","reports":412,
  "first":"2026-09-30T08:11:02+00:00","last":"2026-10-06T18:22:41+00:00",
  "lat":51.5207,"lon":-0.1407,"ts":"2026-10-06T18:22:41+00:00",
  "unix":1791397361.0,"age_s":1041.1,"accuracy":55.0,"confidence":2,"status":0
}]}
```

A tag with no fix yet is **still listed**, with `lat`/`lon`/`ts`/`age_s` as
`null`. Clients should show it as waiting rather than omitting it — the operator
knows they own it and will wonder where it went. Watch for `null` rather than
coercing to `0`, or the tag lands in the Gulf of Guinea.

`uid` is stable across calls (so a marker moves instead of multiplying) and
opaque (see [Security](#security)).

### `GET /api/history?id=<n>&from=&to=&limit=`

One tag's breadcrumbs, **oldest first** so a polyline can be drawn without
reversing it.

`from`/`to` are `YYYY-MM-DD` (widened to cover the whole day) or full ISO
timestamps. `limit` defaults to 2000, capped at 20000.

```json
{"ok":true,"id":1,"uid":"netwatch-...","name":"Bike","color":"#2dd4bf",
 "show_track":true,"visible":true,
 "points":[{"lat":51.5201,"lon":-0.1399,"ts":"...","unix":1791310000.0,
            "accuracy":62.0,"confidence":1,"status":0}]}
```

### `GET /api/tracks?from=&to=&limit=`

Every tag's breadcrumbs in one request — one round trip instead of N, which
matters on a tablet with a flaky link.

### `GET /api/cot?stale=<seconds>&type=<cot type>`

Ready-made Cursor on Target events, one per **visible** tag that has a position.

```json
{"ok":true,"stale":900,"type":"a-f-G-E-S","events":["<event version=\"2.0\" ...>"]}
```

`type` sets the icon a TAK client draws; it's validated against
`^[a-z](-[A-Za-z0-9]+){1,8}$` and anything else falls back to the default, because
the value lands in an XML attribute. Each event carries a stable `uid`, the tag's
colour as a signed ARGB int, and its age in the remarks.

Hidden tags are dropped here, so hiding a tag in the desktop app also stops it
being shared with a TAK team.

### `POST /api/poll`

Ask NetWatch to fetch new reports from Apple, then return the refreshed fixes.

```json
{"ok":true,"synced":3,"new_reports":7,"fixes":[ ... ]}
```

**Rate-limited to once a minute**, and only one at a time. Too soon gets `409`
with a message saying how long to wait — that's a normal, informative answer, not
a fault. Apple rate-limits this too.

## Errors

| Code | Meaning |
|---|---|
| `200` | Fine |
| `401` | Pair code missing or wrong. Responses are delayed ~1s and logged with the peer address |
| `404` | No such route |
| `409` | Valid request that can't be served now — unknown tag id, sync too soon |
| `500` | Bug. Check `%LOCALAPPDATA%\NetWatch\netwatch.log` |

Errors are `{"ok": false, "error": "..."}`, written for a person in a field
rather than a log file.

## Security

**What never crosses the wire, in any response:** `advertisementKey`,
`hashedAdvKey`, `privateKey` — **no key material of any kind**.

That isn't squeamishness. A tag's hashed advertisement key is the handle Apple's
servers accept: anyone holding it can query that tag's location themselves,
forever, with no way to revoke it short of reflashing the board. So tags travel
as `uid` — `sha256(per-install salt + hashedAdvKey)` truncated — which is stable
enough for markers and useless anywhere else. The salt is generated per install,
so the same tag has different uids on two different NetWatch installs.

`desktop/app/tests/test_bridge.py` walks every byte of every response looking for
the real keys, and also fails if the *field names* reappear — which catches a
refactor that adds them back empty.

A pair code is a much weaker thing: read access to positions, revocable instantly
by rotating it. Rotating invalidates every paired client immediately.

**Plain HTTP, deliberately.** The bridge is a desktop app on someone's own
network with no hostname and no CA willing to issue for `192.168.x.x`. A
self-signed certificate would mean shipping a trust anchor or teaching every
customer to install one — worse security theatre than being plain about what this
is. So:

- **Fine** on a LAN you control, or over a VPN.
- **Not fine** port-forwarded through a router. Anyone who could read the traffic
  would have both the code and your tag locations. Use a VPN if you need it from
  away.

## Trying it

```bash
curl -H "X-NetWatch-Pair: ABCD-EFGH-JKMN-PQRT" http://192.168.1.42:8787/api/status
curl "http://192.168.1.42:8787/api/fixes?code=ABCD-EFGH-JKMN-PQRT"
```

## Versioning

Every response carries `api` (currently `1`). Fields will be added; existing ones
won't change meaning without that number changing.
