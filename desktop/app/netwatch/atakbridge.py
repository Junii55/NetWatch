"""Read-only LAN bridge so ATAK / WinTAK can show NetWatch tags.

Why a second listener
---------------------
The main API (`server.py`) binds 127.0.0.1 on an OS-assigned port and
authenticates with a token regenerated every launch. That is correct for a
desktop UI talking to itself and useless to a tablet: nothing off this machine
can reach it, and the token changes before anyone could write it down.

So ATAK gets its own door:

* a **fixed, user-visible port** (8787 by default) on a chosen interface;
* a **long-lived pair code** the operator reads off the screen once;
* **read-only** endpoints — locations out, nothing in except "sync now";
* **off until switched on**, because a tracker app should not open a port
  on a stranger's network just in case.

What it deliberately never serves
---------------------------------
No `advertisementKey`, `hashedAdvKey` or `privateKey`, ever. Those are not
cosmetic: the hashed advertisement key is the lookup handle Apple's servers
accept, so anyone who copies it can query that tag's location for themselves,
forever, without this app. Tags are identified on the wire by `uid` — a salted
digest that is stable (so a marker moves instead of multiplying) and useless
anywhere else. `app/tests/test_bridge.py` asserts the real keys never appear in
a response.

Threat model, stated plainly
---------------------------
Plain HTTP on a local network, gated by a shared code. That is appropriate for
a LAN or a VPN like Tailscale or WireGuard, and it is **not** appropriate on an
untrusted network or forwarded through a router: someone who can watch the
traffic sees the code and then the locations. The UI says so next to the switch.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import store
from .config import APP_NAME, APP_VERSION, EDITION

log = logging.getLogger("netwatch.atakbridge")

DEFAULT_PORT = 8787
DISCOVERY_PORT = 8788

# Settings keys (plain settings; the pair code is a credential and is sealed).
K_ENABLED = "atak_bridge_enabled"
K_PORT = "atak_bridge_port"
K_BIND = "atak_bridge_bind"
K_DISCOVERY = "atak_bridge_discovery"
SECRET_PAIR = "atak_pair_code"

# A sync hits Apple, so the bridge cannot be used to hammer it.
POLL_MIN_INTERVAL = 60.0

# Characters a pair code is built from. Every confusable pair has one member
# removed rather than remapped on input — 0/O, 1/I/L, 5/S, 8/B — so a code read
# off a screen and typed on a tablet has no ambiguous character in it and the
# reader never has to guess which one was meant.
_CODE_ALPHABET = "234679ACDEFGHJKMNPQRTUVWXYZ"
_CODE_GROUPS = 4
_CODE_GROUP_LEN = 4

# Reentrant on purpose: start()/stop() report their outcome through status(),
# which needs the same lock to read the live socket address.
_state_lock = threading.RLock()
_httpd: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_discovery: "_DiscoveryResponder | None" = None
_last_poll = 0.0
_poll_lock = threading.Lock()


class BridgeError(Exception):
    """A user-presentable bridge failure."""


# --------------------------------------------------------------------------- #
# Pair code
# --------------------------------------------------------------------------- #
def _new_code() -> str:
    groups = [
        "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_GROUP_LEN))
        for _ in range(_CODE_GROUPS)
    ]
    return "-".join(groups)


def normalise_code(raw: str) -> str:
    """Accept what a human typed: any case, any separators, extra spaces.

    No confusable-character remapping, because the alphabet has no confusable
    characters to remap. Guessing that a typed "O" meant "Q" would silently turn
    one wrong code into a different wrong code.
    """
    s = "".join(ch for ch in (raw or "").upper() if ch.isalnum())
    body = s[: _CODE_GROUPS * _CODE_GROUP_LEN]
    return "-".join(
        body[i:i + _CODE_GROUP_LEN] for i in range(0, len(body), _CODE_GROUP_LEN)
    )


def pair_code(*, create: bool = True) -> str:
    code = store.get_secret(SECRET_PAIR, "")
    if not code and create:
        code = _new_code()
        store.set_secret(SECRET_PAIR, code)
        log.info("generated a new ATAK pair code")
    return code


def rotate_code() -> str:
    code = _new_code()
    store.set_secret(SECRET_PAIR, code)
    log.info("ATAK pair code rotated; paired clients must be re-paired")
    return code


def _code_matches(supplied: str) -> bool:
    want = pair_code(create=False)
    if not want:
        return False
    return secrets.compare_digest(normalise_code(supplied), want)


# --------------------------------------------------------------------------- #
# Identity and time helpers
# --------------------------------------------------------------------------- #
def _uid_salt() -> str:
    """Per-install salt so a tag's bridge uid cannot be correlated across installs."""
    salt = store.get_setting("atak_uid_salt", "")
    if not salt:
        salt = secrets.token_hex(16)
        store.set_setting("atak_uid_salt", salt)
    return salt


def tag_uid(tag: dict) -> str:
    """A stable, opaque CoT uid for a tag.

    Stable so ATAK moves one marker instead of accumulating hundreds; opaque so
    the real Apple lookup key never leaves this machine.
    """
    digest = sha256(f"{_uid_salt()}|{tag['hashedAdvKey']}".encode()).hexdigest()
    return f"netwatch-{digest[:16]}"


def _unix(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Payload builders — the only place tag fields are chosen
# --------------------------------------------------------------------------- #
def _point(r: dict) -> dict:
    return {
        "lat": r["latitude"],
        "lon": r["longitude"],
        "ts": r.get("timestamp"),
        "unix": _unix(r.get("timestamp")),
        "accuracy": r.get("horizontal_accuracy"),
        "confidence": r.get("confidence"),
        "status": r.get("status"),
    }


def _fix(tag: dict) -> dict:
    """One tag's current position.

    Tags with no report yet are still listed, with a null position, so the
    plugin can show "waiting for a fix" instead of silently omitting a tag the
    operator knows they own.
    """
    latest = store.latest_report(tag["id"])
    rng = store.report_range(tag["id"])
    out = {
        "id": tag["id"],
        "uid": tag_uid(tag),
        "name": tag["name"],
        "color": tag.get("color") or "#2dd4bf",
        "show_track": bool(tag.get("show_track", True)),
        # Hidden in the desktop app means hidden on the TAK map too, so the two
        # agree rather than quietly disagreeing.
        "visible": bool(tag.get("visible", True)),
        "notes": tag.get("notes", ""),
        "reports": rng["count"],
        "first": rng["first"],
        "last": rng["last"],
        "lat": None,
        "lon": None,
        "ts": None,
        "unix": None,
        "age_s": None,
        "accuracy": None,
        "confidence": None,
        "status": None,
    }
    if latest:
        p = _point(latest)
        out.update(p)
        if p["unix"] is not None:
            out["age_s"] = max(0.0, time.time() - p["unix"])
    return out


def _fixes() -> list[dict]:
    return [_fix(t) for t in store.list_tags()]


def _window(q: dict) -> tuple[str | None, str | None]:
    """Same date-window rules as the desktop UI, so both agree."""
    since = (q.get("from") or "").strip() or None
    until = (q.get("to") or "").strip() or None
    if since and len(since) == 10:
        since += "T00:00:00+00:00"
    if until and len(until) == 10:
        until += "T23:59:59.999999+00:00"
    return since, until


def _track(tag: dict, q: dict) -> dict:
    since, until = _window(q)
    try:
        limit = max(1, min(20000, int(q.get("limit", 2000))))
    except (TypeError, ValueError):
        limit = 2000
    reports = store.get_reports(tag["id"], limit=limit, since=since, until=until)
    # Oldest first: a polyline is drawn in order, and the plugin should not have
    # to reverse it on a tablet.
    reports.reverse()
    return {
        "id": tag["id"],
        "uid": tag_uid(tag),
        "name": tag["name"],
        "color": tag.get("color") or "#2dd4bf",
        "show_track": bool(tag.get("show_track", True)),
        "visible": bool(tag.get("visible", True)),
        "points": [_point(r) for r in reports],
    }


DEFAULT_COT_TYPE = "a-f-G-E-S"
# A CoT type is a short hyphenated code such as a-f-G-E-S. Anything else is
# refused rather than escaped: this value lands in an XML attribute that TAK
# clients parse, and the only legitimate inputs match this.
_COT_TYPE_OK = re.compile(r"^[a-z](-[A-Za-z0-9]+){1,8}$")


def _cot_events(stale_s: int = 900, cot_type: str = DEFAULT_COT_TYPE) -> list[str]:
    """Live CoT for every tag that has a position.

    One event per tag with a stable uid, so a TAK client updates the marker it
    already has. Colour travels so the tag looks the same as in the desktop app.

    The type decides the icon a TAK client draws, so the caller chooses it —
    otherwise a tag shared to a team would always show as a sensor no matter what
    the operator picked in the plugin.
    """
    from xml.sax.saxutils import escape, quoteattr

    if not _COT_TYPE_OK.match(cot_type or ""):
        cot_type = DEFAULT_COT_TYPE

    out = []
    for fix in _fixes():
        if fix["lat"] is None or not fix.get("visible", True):
            continue
        ts = fix["ts"] or _iso_now()
        try:
            start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            start = datetime.now(timezone.utc)
        ce = fix["accuracy"] or 9999999.0
        argb = _argb(fix["color"])
        out.append(
            f'<event version="2.0" uid={quoteattr(fix["uid"])} type="{cot_type}" '
            f'time="{escape(ts)}" start="{escape(ts)}" '
            f'stale="{(start + timedelta(seconds=stale_s)).isoformat()}" how="m-g">'
            f'<point lat="{fix["lat"]:.7f}" lon="{fix["lon"]:.7f}" hae="0.0" '
            f'ce="{ce}" le="9999999.0"/>'
            f"<detail>"
            f"<contact callsign={quoteattr(fix['name'])}/>"
            f'<color argb="{argb}"/>'
            f"<remarks>NetWatch tag &#183; "
            f"{int(fix['age_s'] or 0) // 60} min old &#183; "
            f"±{int(ce) if ce < 9999999 else '?'} m</remarks>"
            f"<precisionlocation geopointsrc=\"CALC\" altsrc=\"???\"/>"
            f"</detail></event>"
        )
    return out


def _argb(hex_colour: str) -> int:
    """'#2dd4bf' -> signed ARGB int, which is what CoT and ATAK expect."""
    s = (hex_colour or "").lstrip("#")
    try:
        rgb = int(s[:6], 16)
    except ValueError:
        rgb = 0x2DD4BF
    v = 0xFF000000 | rgb
    return v - 0x100000000 if v > 0x7FFFFFFF else v


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
def _r_status(_q) -> dict:
    tags = store.list_tags()
    fixes = _fixes()
    return {
        "ok": True,
        "app": APP_NAME,
        "product": "NetWatch",
        "version": APP_VERSION,
        "edition": EDITION,
        "api": 1,
        "host": socket.gethostname(),
        "tags": len(tags),
        "fixes": sum(1 for f in fixes if f["lat"] is not None),
        "reports": sum(f["reports"] for f in fixes),
        "last_sync": store.get_setting("last_sync"),
        "time": _iso_now(),
        "unix": time.time(),
    }


def _r_fixes(_q) -> dict:
    return {"ok": True, "unix": time.time(), "fixes": _fixes()}


def _r_history(q) -> dict:
    raw = q.get("id")
    if raw in (None, ""):
        raise BridgeError("history needs ?id=<tag id>")
    try:
        tag_id = int(raw)
    except (TypeError, ValueError):
        raise BridgeError(f"invalid tag id {raw!r}") from None
    tag = store.get_tag(tag_id)
    if not tag:
        raise BridgeError(f"no tag with id {tag_id}")
    return {"ok": True, **_track(tag, q)}


def _r_tracks(q) -> dict:
    # Every track in one request: a tablet on a flaky link should not need one
    # round trip per tag.
    return {"ok": True, "tracks": [_track(t, q) for t in store.list_tags()]}


def _r_cot(q) -> dict:
    try:
        stale = max(60, min(86400, int(q.get("stale", 900))))
    except (TypeError, ValueError):
        stale = 900
    cot_type = (q.get("type") or DEFAULT_COT_TYPE).strip()
    return {
        "ok": True,
        "stale": stale,
        "type": cot_type if _COT_TYPE_OK.match(cot_type) else DEFAULT_COT_TYPE,
        "events": _cot_events(stale, cot_type),
    }


def _r_poll(_q) -> dict:
    """Ask Apple for new reports. Rate-limited, and never more than one at once."""
    global _last_poll
    if not _poll_lock.acquire(blocking=False):
        raise BridgeError("a sync is already running")
    try:
        waited = time.time() - _last_poll
        if waited < POLL_MIN_INTERVAL:
            raise BridgeError(
                f"sync was {int(waited)}s ago; wait "
                f"{int(POLL_MIN_INTERVAL - waited)}s (Apple rate-limits this)"
            )
        from .server import api_sync          # imported late: server imports us

        result = api_sync({}, {})
        _last_poll = time.time()
        return {
            "ok": True,
            "synced": result.get("synced", 0),
            "new_reports": result.get("new_reports", 0),
            "fixes": _fixes(),
        }
    finally:
        _poll_lock.release()


ROUTES = {
    ("GET", "/api/status"): _r_status,
    ("GET", "/api/fixes"): _r_fixes,
    ("GET", "/api/history"): _r_history,
    ("GET", "/api/tracks"): _r_tracks,
    ("GET", "/api/cot"): _r_cot,
    ("POST", "/api/poll"): _r_poll,
}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    server_version = f"NetWatchBridge/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _handle(self, method: str) -> None:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        # An unauthenticated probe gets just enough to identify the app, so a
        # client can say "found NetWatch, now enter the pair code" instead of a
        # bare 401. No counts, no names, no positions.
        if method == "GET" and parsed.path in ("/", "/api/hello"):
            self._json(200, {
                "ok": True, "product": "NetWatch", "app": APP_NAME,
                "version": APP_VERSION, "api": 1, "host": socket.gethostname(),
                "paired": False, "hint": "send the pair code in X-NetWatch-Pair",
            })
            return

        supplied = self.headers.get("X-NetWatch-Pair", "") or q.get("code", "")
        if not _code_matches(supplied):
            log.warning("bridge: rejected %s %s from %s (bad pair code)",
                        method, parsed.path, self.client_address[0])
            # Slow down a code guesser without blocking the whole server: the
            # handler runs on its own thread.
            time.sleep(1.0)
            self._json(401, {"ok": False, "error": "pair code missing or wrong"})
            return

        handler = ROUTES.get((method, parsed.path))
        if handler is None:
            self._json(404, {"ok": False, "error": f"no route for {method} {parsed.path}"})
            return

        if method == "POST":
            # Drain the body so the connection can be reused; nothing reads it.
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(length)

        try:
            self._json(200, handler(q))
        except BridgeError as exc:
            self._json(409, {"ok": False, "error": str(exc)})
        except Exception as exc:
            log.exception("bridge: %s %s failed", method, parsed.path)
            self._json(500, {"ok": False, "error": f"Unexpected error: {exc}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


# --------------------------------------------------------------------------- #
# Discovery: answer "is there a NetWatch here?" on the local network
# --------------------------------------------------------------------------- #
PROBE = b"NETWATCH-DISCOVER?"


class _DiscoveryResponder(threading.Thread):
    """Replies to a UDP broadcast probe so the plugin can find this PC.

    Broadcast reaches a plain LAN and does not cross a VPN or a routed subnet —
    Tailscale and WireGuard have no broadcast domain. That is why the pairing
    link exists: discovery is the convenience, the link is the fallback that
    always works.

    The reply carries the host, port and name only. Handing out the pair code to
    anything that asks would defeat the point of having one.
    """

    daemon = True

    def __init__(self, bridge_port: int):
        super().__init__(name="netwatch-discovery")
        self.bridge_port = bridge_port
        self._sock: socket.socket | None = None
        self._stop = threading.Event()

    def run(self) -> None:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.settimeout(0.5)
            s.bind(("", DISCOVERY_PORT))
            self._sock = s
        except OSError as exc:
            log.warning("bridge: discovery disabled (%s)", exc)
            return
        log.info("bridge: discovery listening on udp/%d", DISCOVERY_PORT)
        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            if not data.startswith(PROBE):
                continue
            reply = json.dumps({
                "product": "NetWatch",
                "app": APP_NAME,
                "version": APP_VERSION,
                "api": 1,
                "host": socket.gethostname(),
                "port": self.bridge_port,
            }).encode()
            try:
                self._sock.sendto(reply, addr)
                log.info("bridge: answered a discovery probe from %s", addr[0])
            except OSError:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
# Addresses to show the operator
# --------------------------------------------------------------------------- #
# Probe destinations used to ask the OS "what source address would you use to
# reach this?". connect() on a UDP socket sends nothing; it only selects a route,
# so this is instant and works with no privileges.
#
# 100.100.100.100 sits in 100.64.0.0/10, the carrier-grade NAT range Tailscale
# assigns its nodes from. If Tailscale is up there is a route to it and the
# source address that comes back is this machine's own Tailscale address — which
# is the address a tablet on the tailnet must use, and which is NOT the one the
# default route would give.
_ROUTE_PROBES = [
    ("203.0.113.1", "default route"),    # TEST-NET-3: guaranteed unroutable
    ("100.100.100.100", "Tailscale"),
]

# Where a VPN typically lands. Tailscale uses 100.64/10; WireGuard and IPsec are
# configured by hand and usually sit in ordinary private space, so they cannot be
# told apart from a LAN by address alone — they are listed, just not labelled.
_CGNAT = "100."


def _classify(ip: str) -> tuple[str, int]:
    """(label, sort key). Lower sorts first, so VPN addresses lead."""
    if ip.startswith(_CGNAT):
        try:
            second = int(ip.split(".")[1])
            if 64 <= second <= 127:
                return "VPN (Tailscale)", 0
        except (IndexError, ValueError):
            pass
    if ip.startswith(("10.", "192.168.")):
        return "local network", 2
    try:
        a, b = (int(x) for x in ip.split(".")[:2])
        if a == 172 and 16 <= b <= 31:
            return "local network", 2
    except (IndexError, ValueError):
        pass
    return "this machine", 3


def local_addresses() -> list[dict]:
    """Every IPv4 address a tablet might reach this machine on, best first.

    Returns dicts so the UI can say *which* address is which. That matters more
    than it sounds: a tablet on a VPN cannot use the LAN address, and a tablet on
    the LAN cannot use the VPN one, and nothing on screen distinguishes
    192.168.1.42 from 100.64.0.5 unless we say so.
    """
    found: dict[str, str] = {}      # ip -> hint about where it came from

    for target, why in _ROUTE_PROBES:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 9))
            found.setdefault(s.getsockname()[0], why)
        except OSError:
            pass                    # no route that way; nothing to report
        finally:
            s.close()

    try:
        for addr in socket.gethostbyname_ex(socket.gethostname())[2]:
            found.setdefault(addr, "")
    except OSError:
        pass

    out = []
    for ip in found:
        # 127.x is this machine only. 169.254.x is APIPA: an adapter that failed
        # to get an address, so it is never reachable and only clutters the list.
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        label, order = _classify(ip)
        out.append({"ip": ip, "label": label, "order": order})

    out.sort(key=lambda a: (a["order"], a["ip"]))
    return out


def pairing_link(host: str, port: int | None = None) -> str:
    """One string that carries everything a client needs to pair.

    Typing an IP, a port and a code into a tablet is three chances to get it
    wrong, and over a VPN the operator also has to know which of this machine's
    addresses is the right one. A link per address removes both problems.
    """
    from urllib.parse import quote

    p = port or configured_port()
    return (
        f"netwatch://pair?h={quote(host)}&p={p}"
        f"&c={pair_code()}&n={quote(socket.gethostname())}"
    )


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def configured_port() -> int:
    try:
        port = int(store.get_setting(K_PORT, DEFAULT_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return port if 1 <= port <= 65535 else DEFAULT_PORT


def configured_bind() -> str:
    """Which interface to listen on. Empty means every interface."""
    return str(store.get_setting(K_BIND, "") or "")


def is_running() -> bool:
    with _state_lock:
        return _httpd is not None


def start(port: int | None = None, bind: str | None = None) -> dict:
    """Open the bridge. Idempotent — restarts if the address changed."""
    global _httpd, _thread, _discovery

    want_port = port if port is not None else configured_port()
    want_bind = bind if bind is not None else configured_bind()

    with _state_lock:
        if _httpd is not None:
            current = _httpd.server_address
            if current[1] == want_port and current[0] == (want_bind or "0.0.0.0"):
                return status()
        _stop_locked()

        pair_code()                      # make sure one exists before anyone connects
        try:
            httpd = ThreadingHTTPServer((want_bind, want_port), _Handler)
        except OSError as exc:
            raise BridgeError(
                f"could not listen on port {want_port}: {exc}. Another program may "
                f"already be using it — pick a different port."
            ) from exc
        httpd.daemon_threads = True
        _httpd = httpd
        _thread = threading.Thread(target=httpd.serve_forever, daemon=True,
                                   name="netwatch-bridge")
        _thread.start()

        # The port actually bound, which is not the requested one when that was 0
        # and the OS chose. Advertising the request would tell every client on the
        # network to connect to port 0.
        bound_port = httpd.server_address[1]

        store.set_setting(K_ENABLED, True)
        store.set_setting(K_PORT, bound_port)
        store.set_setting(K_BIND, want_bind)

        if store.get_setting(K_DISCOVERY, True):
            _discovery = _DiscoveryResponder(bound_port)
            _discovery.start()

        log.info("ATAK bridge listening on %s:%d",
                 want_bind or "0.0.0.0", bound_port)
        return status()


def _stop_locked() -> None:
    global _httpd, _thread, _discovery
    if _discovery is not None:
        _discovery.stop()
        _discovery = None
    if _httpd is not None:
        _httpd.shutdown()
        _httpd.server_close()
        _httpd = None
    _thread = None


def stop(*, remember: bool = True) -> dict:
    with _state_lock:
        was = _httpd is not None
        _stop_locked()
    if remember:
        store.set_setting(K_ENABLED, False)
    if was:
        log.info("ATAK bridge stopped")
    return status()


def status() -> dict:
    with _state_lock:
        running = _httpd is not None
        addr = _httpd.server_address if running else None
    port = addr[1] if addr else configured_port()
    addrs = local_addresses()
    # One entry per address, each with its own pairing link, because which one a
    # tablet can reach depends on how it is connected.
    endpoints = [
        {
            "ip": a["ip"],
            "label": a["label"],
            "url": f"http://{a['ip']}:{port}",
            "link": pairing_link(a["ip"], port),
        }
        for a in addrs
    ]
    return {
        "running": running,
        "enabled": bool(store.get_setting(K_ENABLED, False)),
        "port": port,
        "bind": configured_bind(),
        "discovery": bool(store.get_setting(K_DISCOVERY, True)),
        "code": pair_code(),
        "endpoints": endpoints,
        "addresses": [a["ip"] for a in addrs],
        "urls": [e["url"] for e in endpoints],
        # The best single guess, for callers that want just one (the CLI banner).
        "link": endpoints[0]["link"] if endpoints else "",
        "hostname": socket.gethostname(),
        "discovery_port": DISCOVERY_PORT,
    }


def start_if_enabled() -> None:
    """Called at launch. A bridge the operator switched on should come back."""
    if not store.get_setting(K_ENABLED, False):
        return
    try:
        start()
    except BridgeError as exc:
        log.warning("ATAK bridge did not start: %s", exc)
