"""Loopback HTTP API + static UI host.

Security notes:
* Binds 127.0.0.1 only — never reachable from the network.
* Every /api/ call must carry the per-launch session token, so other local
  processes cannot drive the app or read tag keys.
* The Apple password is accepted on one endpoint, forwarded to Apple, and never
  stored or logged.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import secrets
import threading
import traceback
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import appleclient, atakbridge, exports, flasher, store, tags as tagmod, tileproviders
from .appleclient import HELPER, AppleError
from .config import (
    APP_NAME, APP_VERSION, DEMO_MODE, EDITION, HOST,
    IS_RETAIL, LOG_PATH, PORT, REQUIRE_ATTESTATION, WEB_DIR,
)
from .flasher import FlashError

log = logging.getLogger("netwatch.server")

SESSION_TOKEN = secrets.token_urlsafe(32)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Jobs (flashing runs in the background; the UI polls)
# --------------------------------------------------------------------------- #
def _new_job() -> str:
    jid = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[jid] = {"state": "running", "lines": [], "error": None, "result": None}
    return jid


def _job_say(jid: str, msg: str) -> None:
    with _jobs_lock:
        job = _jobs.get(jid)
        if job is not None:
            job["lines"].append(msg.rstrip())


def _job_done(jid: str, result=None, error: str | None = None) -> None:
    with _jobs_lock:
        job = _jobs.get(jid)
        if job is not None:
            job["state"] = "error" if error else "done"
            job["error"] = error
            job["result"] = result


# --------------------------------------------------------------------------- #
# Tag helpers
# --------------------------------------------------------------------------- #
def _tag_view(row: dict) -> dict:
    latest = store.latest_report(row["id"])
    return {
        **{k: row[k] for k in ("id", "name", "icon", "color", "show_track", "visible",
                               "printedMac", "hashedAdvKey", "advertisementKey",
                               "auto_sync", "notes", "created_at")},
        "last_seen": latest,
        "report_count": store.report_count(row["id"]),
        "range": store.report_range(row["id"]),
    }


def _require_tag(tag_id: int, *, with_private: bool = False) -> dict:
    row = store.get_tag(tag_id, with_private=with_private)
    if not row:
        raise ApiError("No such tag.", 404)
    return row


def _tag_id(body: dict, q: dict) -> int:
    """Accept the tag id from either the JSON body or the query string."""
    raw = body.get("id", q.get("id"))
    if raw in (None, ""):
        raise ApiError("No tag id supplied.")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ApiError(f"Invalid tag id: {raw!r}") from None


# --------------------------------------------------------------------------- #
# API handlers
# --------------------------------------------------------------------------- #
def _tile_state() -> dict:
    """Basemaps this edition offers, plus whichever one is selected.

    No API keys anywhere: every layer either works out of the box or points at
    the operator's own tile server.
    """
    custom = store.get_setting("tile_custom_url", "") or ""
    layers = tileproviders.available(custom_url=custom)

    selected = store.get_setting("map_tiles", "") or tileproviders.default_id()
    if selected not in layers:
        selected = tileproviders.default_id()

    out = {}
    for tid, spec in layers.items():
        pub = dict(spec)
        pub["enabled"] = True
        out[tid] = pub

    return {"layers": out, "selected": selected, "custom_url": custom}


def api_bootstrap(_body, _q) -> dict:
    return {
        "app": APP_NAME,
        "edition": EDITION,
        "retail": IS_RETAIL,
        "version": APP_VERSION,
        "demo": DEMO_MODE,
        "apple": HELPER.status(),
        "firmware": flasher.available_firmware(),
        "export_formats": [
            {"id": k, "label": v[0], "ext": v[1], "description": v[2]}
            for k, v in exports.FORMATS.items()
        ],
        "require_attestation": REQUIRE_ATTESTATION,
        "attested": (not REQUIRE_ATTESTATION) or bool(store.get_setting("ownership_attested", False)),
        "tiles": _tile_state(),
        "bridge": atakbridge.status(),
    }


def api_tiles(body, _q) -> dict:
    """Save the selected map source or a self-hosted tile URL."""
    if "custom_url" in body:
        url = (body.get("custom_url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            raise ApiError("Tile URL must start with http:// or https://")
        store.set_setting("tile_custom_url", url)
    if body.get("selected"):
        store.set_setting("map_tiles", body["selected"])
    return _tile_state()


def api_health(_body, _q) -> dict:
    return HELPER.health()


def api_attest(body, _q) -> dict:
    store.set_setting("ownership_attested", bool(body.get("accepted")))
    return {"attested": bool(body.get("accepted"))}


def api_login(body, _q) -> dict:
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    try:
        return HELPER.login(email, password)
    finally:
        body["password"] = ""        # don't let it linger in the parsed body
        password = ""


def api_2fa_request(body, _q) -> dict:
    return HELPER.request_2fa(int(body.get("index", 0)))


def api_2fa_submit(body, _q) -> dict:
    return HELPER.submit_2fa(int(body.get("index", 0)), body.get("code", ""))


def api_logout(_body, _q) -> dict:
    HELPER.logout()
    return {"ok": True}


def api_tags_list(_body, _q) -> dict:
    return {"tags": [_tag_view(t) for t in store.list_tags()]}


def api_tags_create(body, _q) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError("Give the tag a name.")
    icon = body.get("icon") or "tag"

    if body.get("keyfile"):
        try:
            parsed = tagmod.parse_keyfile(body["keyfile"])
        except ValueError as exc:
            raise ApiError(str(exc)) from exc
        created = []
        for t in parsed:
            if store.tag_exists(t["hashedAdvKey"]):
                continue
            tid = store.add_tag(
                name=t["name"] or name, icon=t.get("icon", icon),
                private_key_b64=t["privateKey"], adv_key_b64=t["advertisementKey"],
                hashed_adv_key_b64=t["hashedAdvKey"], printed_mac=t["printedMac"],
            )
            created.append(tid)
        if not created:
            raise ApiError("Those tags are already in your list.")
        return {"created": created, "tags": [_tag_view(t) for t in store.list_tags()]}

    t = tagmod.generate(name)
    if store.tag_exists(t["hashedAdvKey"]):
        raise ApiError("That key already exists — try again.")
    tid = store.add_tag(
        name=name, icon=icon, private_key_b64=t["privateKey"],
        adv_key_b64=t["advertisementKey"], hashed_adv_key_b64=t["hashedAdvKey"],
        printed_mac=t["printedMac"],
    )
    return {"created": [tid], "tag": _tag_view(store.get_tag(tid))}


def api_tags_update(body, q) -> dict:
    tag_id = _tag_id(body, q)
    _require_tag(tag_id)
    store.update_tag(tag_id, **{k: v for k, v in body.items()
                                if k in ("name", "icon", "color", "auto_sync",
                                         "show_track", "visible", "notes")})
    return {"tag": _tag_view(store.get_tag(tag_id))}


def api_tags_delete(body, q) -> dict:
    tag_id = _tag_id(body, q)
    _require_tag(tag_id)
    store.delete_tag(tag_id)
    return {"ok": True}


def _window(q: dict) -> tuple[str | None, str | None]:
    """Optional breadcrumb date window. Dates widen to cover the whole day."""
    since = (q.get("from") or "").strip() or None
    until = (q.get("to") or "").strip() or None
    if since and len(since) == 10:          # YYYY-MM-DD -> start of that day
        since += "T00:00:00+00:00"
    if until and len(until) == 10:          # YYYY-MM-DD -> end of that day
        until += "T23:59:59.999999+00:00"
    return since, until


def api_history(body, q) -> dict:
    tag_id = _tag_id(body, q)
    _require_tag(tag_id)
    limit = int(q.get("limit", 5000))
    since, until = _window(q)
    return {
        "reports": store.get_reports(tag_id, limit=limit, since=since, until=until),
        "range": store.report_range(tag_id),
    }


def api_sync(body, _q) -> dict:
    only = body.get("tag_id")
    rows = store.list_tags(with_private=True)
    if only:
        rows = [r for r in rows if r["id"] == int(only)]
    if not rows:
        return {"synced": 0, "new_reports": 0, "tags": []}
    try:
        fetched = HELPER.fetch_reports(rows)
    except AppleError as exc:
        raise ApiError(str(exc), 409) from exc

    new_total = 0
    for row in rows:
        reports = fetched.get(row["hashedAdvKey"], [])
        new_total += store.add_reports(row["id"], reports)
    store.set_setting("last_sync", datetime.now(timezone.utc).isoformat())
    return {
        "synced": len(rows),
        "new_reports": new_total,
        "tags": [_tag_view(t) for t in store.list_tags()],
    }


def api_ports(_body, _q) -> dict:
    return {"ports": flasher.list_ports()}


def api_detect(body, _q) -> dict:
    port = body.get("port") or ""
    if not port:
        raise ApiError("Pick a serial port first.")
    try:
        return flasher.detect(port)
    except FlashError as exc:
        raise ApiError(str(exc)) from exc


def api_flash(body, _q) -> dict:
    port, chip = body.get("port"), body.get("chip")
    tag_id, name = body.get("tag_id"), (body.get("name") or "").strip()
    if not port or not chip:
        raise ApiError("Connect to a board first.")

    # Flash an existing tag, or create a new one in the same step.
    if tag_id:
        row = _require_tag(int(tag_id), with_private=True)
    else:
        if not name:
            raise ApiError("Give the new tag a name.")
        t = tagmod.generate(name)
        new_id = store.add_tag(
            name=name, icon=body.get("icon") or "tag", private_key_b64=t["privateKey"],
            adv_key_b64=t["advertisementKey"], hashed_adv_key_b64=t["hashedAdvKey"],
            printed_mac=t["printedMac"],
        )
        row = store.get_tag(new_id, with_private=True)

    import base64
    adv_key = base64.b64decode(row["advertisementKey"])
    jid = _new_job()

    def worker() -> None:
        try:
            result = flasher.flash_tag(port, chip, adv_key, lambda m: _job_say(jid, m))
            result["tag"] = _tag_view(store.get_tag(row["id"]))
            _job_done(jid, result)
        except Exception as exc:
            _job_say(jid, f"ERROR {exc}")
            _job_done(jid, error=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return {"job": jid, "tag_id": row["id"], "printedMac": row["printedMac"]}


def api_job(_body, q) -> dict:
    with _jobs_lock:
        job = _jobs.get(q.get("id", ""))
        if job is None:
            raise ApiError("No such job.", 404)
        return dict(job)


def api_export(body, q) -> dict:
    tag_id = _tag_id(body, q)
    fmt = (q.get("fmt") or "json").lower()
    include_private = q.get("private", "1") != "0"
    row = _require_tag(tag_id, with_private=include_private)
    since, until = _window(q)
    reports = store.get_reports(tag_id, limit=100000, since=since, until=until)
    try:
        content = exports.export(fmt, row, reports, include_private=include_private)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    ext = exports.FORMATS[fmt][1]
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", row["name"]) or "tag"
    # Name the file after the window so successive exports do not collide.
    span = ""
    if since or until:
        span = f"_{(since or 'start')[:10]}_to_{(until or 'now')[:10]}"
    return {"filename": f"{safe}{span}.{ext}", "content": content, "points": len(reports)}


def api_bridge_get(_body, _q) -> dict:
    """State of the ATAK/TAK bridge, including the pair code and how to reach it."""
    return atakbridge.status()


def api_bridge_set(body, _q) -> dict:
    """Turn the bridge on or off, move its port, or rotate the pair code."""
    if body.get("rotate"):
        atakbridge.rotate_code()

    if "discovery" in body:
        store.set_setting(atakbridge.K_DISCOVERY, bool(body["discovery"]))

    if "enabled" in body and not body["enabled"]:
        return atakbridge.stop()

    port = body.get("port")
    if port is not None:
        try:
            port = int(port)
        except (TypeError, ValueError):
            raise ApiError(f"Invalid port: {port!r}") from None
        if not 1024 <= port <= 65535:
            raise ApiError("Pick a port between 1024 and 65535.")
    bind = body.get("bind")

    want_on = body.get("enabled", atakbridge.is_running())
    if not want_on:
        # Only settings changed while it is off; remember them for next time.
        if port is not None:
            store.set_setting(atakbridge.K_PORT, port)
        if bind is not None:
            store.set_setting(atakbridge.K_BIND, str(bind))
        return atakbridge.status()

    try:
        return atakbridge.start(port=port, bind=bind)
    except atakbridge.BridgeError as exc:
        raise ApiError(str(exc)) from exc


def api_logs(_body, _q) -> dict:
    try:
        text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = "(no log yet)"
    return {"log": text[-200_000:]}


def api_settings(body, _q) -> dict:
    for k, v in body.items():
        if k in ("map_tiles", "auto_sync_interval"):
            store.set_setting(k, v)
    return {"ok": True}


ROUTES: dict[tuple[str, str], callable] = {
    ("GET", "/api/bootstrap"): api_bootstrap,
    ("GET", "/api/health"): api_health,
    ("POST", "/api/attest"): api_attest,
    ("POST", "/api/login"): api_login,
    ("POST", "/api/2fa/request"): api_2fa_request,
    ("POST", "/api/2fa/submit"): api_2fa_submit,
    ("POST", "/api/logout"): api_logout,
    ("GET", "/api/tags"): api_tags_list,
    ("POST", "/api/tags"): api_tags_create,
    ("POST", "/api/tags/update"): api_tags_update,
    ("POST", "/api/tags/delete"): api_tags_delete,
    ("GET", "/api/history"): api_history,
    ("POST", "/api/sync"): api_sync,
    ("GET", "/api/ports"): api_ports,
    ("POST", "/api/detect"): api_detect,
    ("POST", "/api/flash"): api_flash,
    ("GET", "/api/job"): api_job,
    ("GET", "/api/export"): api_export,
    ("GET", "/api/logs"): api_logs,
    ("POST", "/api/settings"): api_settings,
    ("POST", "/api/tiles"): api_tiles,
    ("GET", "/api/bridge"): api_bridge_get,
    ("POST", "/api/bridge"): api_bridge_set,
}


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"

    def log_message(self, fmt, *args):  # quieter default logging
        log.debug("%s - %s", self.address_string(), fmt % args)

    # -- helpers ---------------------------------------------------------
    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-NetWatch-Token", ""), SESSION_TOKEN)

    # -- static ----------------------------------------------------------
    def _serve_static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB_DIR / rel).resolve()
        try:
            target.relative_to(WEB_DIR.resolve())
        except ValueError:
            self._json(403, {"error": "forbidden"})
            return
        if not target.is_file():
            self._json(404, {"error": "not found"})
            return
        data = target.read_bytes()
        if target.name == "index.html":
            data = data.replace(b"__NETWATCH_TOKEN__", SESSION_TOKEN.encode())
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, data, ctype)

    # -- dispatch --------------------------------------------------------
    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if not path.startswith("/api/"):
            if method == "GET":
                self._serve_static(path)
            else:
                self._json(405, {"error": "method not allowed"})
            return

        if not self._authorised():
            self._json(401, {"error": "unauthorised"})
            return

        handler = ROUTES.get((method, path))
        if handler is None:
            self._json(404, {"error": f"no route for {method} {path}"})
            return

        body: dict = {}
        if method == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    self._json(400, {"error": "request body was not valid JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"error": "request body must be a JSON object"})
                    return

        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            self._json(200, handler(body, query) or {})
        except ApiError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (AppleError, FlashError) as exc:
            self._json(409, {"error": str(exc)})
        except Exception as exc:
            log.error("unhandled error in %s: %s", path, traceback.format_exc())
            self._json(500, {"error": f"Unexpected error: {exc}"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


def serve() -> tuple[ThreadingHTTPServer, str]:
    """Start the API. Returns (server, url_with_token)."""
    store.init_db()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    host, port = httpd.server_address[0], httpd.server_address[1]
    url = f"http://{host}:{port}/"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    log.info("API listening on %s", url)
    return httpd, url
