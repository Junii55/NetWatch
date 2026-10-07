"""ATAK bridge: authentication, payload shape, and — above all — key secrecy.

The bridge is the only part of NetWatch that listens on a network interface, so
it is the only part where a mistake exposes something. The test that matters
most here is `test_no_key_material_anywhere`: it walks every byte of every
response looking for the tag's advertisement key, its hashed advertisement key
and its private key. A tag's hashed advertisement key is the handle Apple's
servers accept, so leaking it hands over that tag's location history to anyone
on the network, permanently, with no way to revoke it short of reflashing the
board.

Run it directly:

    python app\\tests\\test_bridge.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# A fresh database, always. Pointing these tests at the real store would create
# and delete tags next to the user's live ones, and a deleted tag's private key
# is unrecoverable — the board it was flashed onto becomes permanently
# unlocatable. config.py reads this at import, so it must be set first.
_TMP = Path(tempfile.mkdtemp(prefix="netwatch-bridge-test-"))
os.environ["NETWATCH_DATA_DIR"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import atakbridge, store, tags as tagmod   # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def get(path: str, code: str | None, port: int, method: str = "GET") -> tuple[int, dict]:
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    if code is not None:
        req.add_header("X-NetWatch-Pair", code)
    if method == "POST":
        req.data = b"{}"
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def main() -> int:
    store.init_db()

    # Two tags: one with history, one that has never reported.
    t1 = tagmod.generate("Bridge Test Bike")
    id1 = store.add_tag(
        name="Bridge Test Bike", icon="tag", private_key_b64=t1["privateKey"],
        adv_key_b64=t1["advertisementKey"], hashed_adv_key_b64=t1["hashedAdvKey"],
        printed_mac=t1["printedMac"], color="#60a5fa",
    )
    t2 = tagmod.generate("Bridge Test Nofix")
    store.add_tag(
        name="Bridge Test Nofix", icon="tag", private_key_b64=t2["privateKey"],
        adv_key_b64=t2["advertisementKey"], hashed_adv_key_b64=t2["hashedAdvKey"],
        printed_mac=t2["printedMac"],
    )
    store.add_reports(id1, [
        {"timestamp": "2026-10-01T10:00:00+00:00", "latitude": 51.5, "longitude": -0.12,
         "confidence": 2, "horizontal_accuracy": 40.0, "status": 0},
        {"timestamp": "2026-10-02T11:30:00+00:00", "latitude": 51.51, "longitude": -0.13,
         "confidence": 3, "horizontal_accuracy": 25.0, "status": 0},
        {"timestamp": "2026-10-03T12:00:00+00:00", "latitude": 51.52, "longitude": -0.14,
         "confidence": 1, "horizontal_accuracy": 70.0, "status": 0},
    ])

    # Loopback only, and no discovery responder: a test suite must not open a LAN
    # port or start answering broadcast probes on whoever's network this runs on.
    store.set_setting(atakbridge.K_DISCOVERY, False)
    st = atakbridge.start(port=0, bind="127.0.0.1")
    port = st["port"]
    code = st["code"]
    print(f"\nbridge on 127.0.0.1:{port}  code {code}\n")

    print("-- lifecycle: port reporting -----------------------------------")
    # Asked for 0, so the OS chose. Everything downstream - the pairing link, the
    # discovery reply - must carry the port actually bound, never the request.
    check("an OS-assigned port is reported, not the 0 that was asked for",
          port > 0, f"reported {port}")
    check("the pairing link carries the bound port",
          f"p={port}" in st["link"], st["link"])
    check("the stored port is the bound one",
          int(store.get_setting(atakbridge.K_PORT) or 0) == port)

    print("-- pair code ---------------------------------------------------")
    check("code uses no confusable characters",
          not (set(code) & set("01ILOSB85")),
          f"code was {code}")
    check("code normalises from lower case and spaces",
          atakbridge.normalise_code(code.lower().replace("-", " ")) == code)
    s, _ = get("/api/fixes", None, port)
    check("no code -> 401", s == 401, f"got {s}")
    s, _ = get("/api/fixes", "WRONG-CODE-HERE-XXXX", port)
    check("wrong code -> 401", s == 401, f"got {s}")
    s, body = get("/api/hello", None, port)
    check("unauthenticated /api/hello identifies the app", s == 200 and body.get("product") == "NetWatch")
    check("/api/hello does not count tags", "tags" not in body and "fixes" not in body)

    print("-- addresses offered for pairing -------------------------------")
    # A tablet on a VPN cannot use the LAN address and vice versa, so each
    # address is classified and gets its own pairing link.
    for ip, want in (
        ("100.83.4.21", "VPN (Tailscale)"),     # 100.64/10 CGNAT: a tailnet node
        ("100.127.255.1", "VPN (Tailscale)"),   # top of the range
        ("192.168.1.42", "local network"),
        ("192.168.1.5", "local network"),
        ("172.16.0.9", "local network"),
        ("172.32.0.9", "this machine"),         # just outside 172.16/12
        ("100.200.0.1", "this machine"),        # 100.x but outside the CGNAT range
    ):
        label, _ = atakbridge._classify(ip)
        check(f"{ip} is labelled {want!r}", label == want, f"got {label!r}")

    addrs = atakbridge.local_addresses()
    check("link-local APIPA addresses are never offered",
          not any(a["ip"].startswith("169.254.") for a in addrs),
          str([a["ip"] for a in addrs]))
    check("loopback is never offered",
          not any(a["ip"].startswith("127.") for a in addrs),
          str([a["ip"] for a in addrs]))
    check("a VPN address would sort ahead of a LAN one",
          atakbridge._classify("100.83.4.21")[1] < atakbridge._classify("10.0.0.1")[1])

    eps = atakbridge.status()["endpoints"]
    check("every offered address carries its own pairing link",
          all(e["link"].startswith("netwatch://pair?h=") and e["ip"] in e["link"]
              for e in eps),
          str(eps))
    check("each pairing link carries the pair code",
          all(atakbridge.pair_code() in e["link"] for e in eps))

    print("\n-- payloads ----------------------------------------------------")
    s, status = get("/api/status", code, port)
    check("status 200", s == 200, f"got {s}")
    check("status counts 2 tags, 1 with a fix",
          status.get("tags") == 2 and status.get("fixes") == 1,
          json.dumps(status))
    check("status reports 3 stored reports", status.get("reports") == 3)

    s, fixes = get("/api/fixes", code, port)
    rows = fixes["fixes"]
    check("fixes 200 and lists both tags", s == 200 and len(rows) == 2, f"{s} {len(rows)}")
    bike = next(r for r in rows if r["name"] == "Bridge Test Bike")
    nofix = next(r for r in rows if r["name"] == "Bridge Test Nofix")
    check("latest fix wins", bike["lat"] == 51.52 and bike["lon"] == -0.14,
          f"{bike['lat']},{bike['lon']}")
    check("tag colour travels", bike["color"] == "#60a5fa", bike["color"])
    check("a tag with no report is listed with a null position",
          nofix["lat"] is None and nofix["reports"] == 0)
    check("uid is stable across calls",
          get("/api/fixes", code, port)[1]["fixes"][0]["uid"] == rows[0]["uid"])
    check("uid is opaque", bike["uid"].startswith("netwatch-") and len(bike["uid"]) == 25,
          bike["uid"])

    s, hist = get(f"/api/history?id={id1}", code, port)
    pts = hist["points"]
    check("history 200 with 3 points", s == 200 and len(pts) == 3, f"{s} {len(pts)}")
    check("history is oldest-first for drawing",
          pts[0]["lat"] == 51.5 and pts[-1]["lat"] == 51.52,
          f"{pts[0]['lat']}..{pts[-1]['lat']}")

    s, win = get(f"/api/history?id={id1}&from=2026-10-02&to=2026-10-02", code, port)
    check("history date window filters to one day",
          s == 200 and len(win["points"]) == 1 and win["points"][0]["lat"] == 51.51,
          json.dumps(win.get("points")))

    s, tracks = get("/api/tracks", code, port)
    check("tracks returns one entry per tag", s == 200 and len(tracks["tracks"]) == 2)

    print("\n-- hiding a tag ------------------------------------------------")
    check("tags are visible by default", bike["visible"] is True)
    store.update_tag(id1, visible=False)
    s, hidden = get("/api/fixes", code, port)
    row = next(r for r in hidden["fixes"] if r["name"] == "Bridge Test Bike")
    check("a hidden tag is still listed, marked hidden",
          s == 200 and row["visible"] is False and len(hidden["fixes"]) == 2)
    s, hidden_cot = get("/api/cot", code, port)
    check("a hidden tag is dropped from CoT, so it is not shared with a team",
          s == 200 and not hidden_cot["events"], str(hidden_cot.get("events")))
    store.update_tag(id1, visible=True)
    s, back = get("/api/cot", code, port)
    check("showing it again brings the CoT event back",
          s == 200 and len(back["events"]) == 1)

    s, cot = get("/api/cot", code, port)
    check("cot 200 with one event for the tag that has a fix",
          s == 200 and len(cot["events"]) == 1, f"{s} {len(cot.get('events', []))}")
    ev = cot["events"][0]
    check("cot event carries the callsign", 'callsign="Bridge Test Bike"' in ev, ev[:200])
    check("cot event uses the stable uid", f'uid="{bike["uid"]}"' in ev, ev[:200])
    check("cot event is well-formed XML", _parses(ev))
    check("cot event carries the tag colour",
          f'argb="{atakbridge._argb("#60a5fa")}"' in ev, ev[:300])

    s, typed = get("/api/cot?type=a-f-G-U-C", code, port)
    check("cot type is honoured so a shared tag keeps its icon",
          s == 200 and 'type="a-f-G-U-C"' in typed["events"][0],
          typed["events"][0][:160])
    # The type lands in an XML attribute, so a bad one must be refused outright
    # rather than escaped into the document.
    for bad_type in ('a-f" foo="bar', "<script>", "../../etc", "", "A" * 99):
        s, out = get("/api/cot?type=" + urllib.parse.quote(bad_type), code, port)
        ok = s == 200 and f'type="{atakbridge.DEFAULT_COT_TYPE}"' in out["events"][0]
        check(f"bad cot type {bad_type[:18]!r} falls back to the default", ok,
              out.get("events", [""])[0][:160])

    s, bad = get("/api/history", code, port)
    check("history without an id -> 409", s == 409, f"got {s}")
    s, bad = get("/api/history?id=999999", code, port)
    check("history for an unknown tag -> 409", s == 409, f"got {s}")
    s, _ = get("/api/nope", code, port)
    check("unknown route -> 404", s == 404, f"got {s}")

    print("\n-- no key material anywhere ------------------------------------")
    secrets = {
        "advertisementKey": t1["advertisementKey"],
        "hashedAdvKey": t1["hashedAdvKey"],
        "privateKey": t1["privateKey"],
        "advertisementKey#2": t2["advertisementKey"],
        "hashedAdvKey#2": t2["hashedAdvKey"],
        "privateKey#2": t2["privateKey"],
    }
    blobs = []
    for path in ("/api/status", "/api/fixes", f"/api/history?id={id1}",
                 "/api/tracks", "/api/cot"):
        blobs.append((path, json.dumps(get(path, code, port)[1])))

    for path, blob in blobs:
        for label, value in secrets.items():
            # Compare on the raw base64 and on the url/JSON-escaped forms, since
            # a leak could arrive re-encoded rather than verbatim.
            needles = {value, value.rstrip("="), value.replace("/", "\\/")}
            hit = next((n for n in needles if n and n in blob), None)
            check(f"{path} does not leak {label}", hit is None,
                  f"found {hit!r}")

    # Belt and braces: the field names themselves must not appear either, which
    # catches a future refactor that adds them back with an empty value.
    for path, blob in blobs:
        banned = [k for k in ("advertisementKey", "hashedAdvKey", "privateKey",
                              "adv_key", "hashed_adv_key", "sealed_priv")
                  if k in blob]
        check(f"{path} has no key field names", not banned, str(banned))

    print("\n-- lifecycle ---------------------------------------------------")
    old = code
    new = atakbridge.rotate_code()
    check("rotate changes the code", new != old)
    s, _ = get("/api/fixes", old, port)
    check("the old code stops working after a rotate", s == 401, f"got {s}")
    s, _ = get("/api/fixes", new, port)
    check("the new code works", s == 200, f"got {s}")

    atakbridge.stop()
    check("stop() clears running", not atakbridge.is_running())
    check("stop() remembers the operator turned it off",
          store.get_setting(atakbridge.K_ENABLED) is False)
    try:
        get("/api/fixes", new, port)
        check("port is closed after stop", False, "the request still succeeded")
    except Exception:
        check("port is closed after stop", True)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


def _parses(xml: str) -> bool:
    from xml.etree import ElementTree
    try:
        ElementTree.fromstring(xml)
        return True
    except ElementTree.ParseError:
        return False


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        try:
            atakbridge.stop(remember=False)
        except Exception:
            pass
        shutil.rmtree(_TMP, ignore_errors=True)
