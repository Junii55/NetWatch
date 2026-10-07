"""Location-history export formats.

JSON keeps private keys so an export can be re-imported or moved to another
machine; every other format is location data only.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

FORMATS = {
    "json":    ("JSON",    "json", "Full device data: config, history and private keys"),
    "csv":     ("CSV",     "csv",  "Spreadsheet-friendly timestamp/lat/lon/accuracy"),
    "gpx":     ("GPX",     "gpx",  "GPS Exchange — Google Earth, Garmin, fitness apps"),
    "kml":     ("KML",     "kml",  "Google Earth native format"),
    "geojson": ("GeoJSON", "geojson", "Web maps and GIS — QGIS, Leaflet, Mapbox"),
    "cot":     ("CoT",     "xml",  "Cursor on Target for ATAK / WinTAK / TAK Server"),
}


def _iso(ts: str) -> str:
    return ts


def _sorted_oldest_first(reports: list[dict]) -> list[dict]:
    return sorted(reports, key=lambda r: r.get("timestamp", ""))


def export(fmt: str, tag: dict, reports: list[dict], *, include_private: bool = True) -> str:
    fmt = fmt.lower()
    if fmt not in FORMATS:
        raise ValueError(f"unknown export format '{fmt}'")
    return _DISPATCH[fmt](tag, _sorted_oldest_first(reports), include_private)


# --------------------------------------------------------------------------- #
def _json(tag: dict, reports: list[dict], include_private: bool) -> str:
    payload = {
        "name": tag["name"],
        "icon": tag.get("icon", "tag"),
        "id": tag["hashedAdvKey"],
        "advertisementKey": tag["advertisementKey"],
        "hashedAdvKey": tag["hashedAdvKey"],
        "hashedPublicKey": tag["hashedAdvKey"],
        "printedMac": tag["printedMac"],
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "history": reports,
    }
    if include_private and tag.get("privateKey"):
        payload["privateKey"] = tag["privateKey"]
    return json.dumps(payload, indent=2) + "\n"


def _csv(tag: dict, reports: list[dict], _p: bool) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["timestamp", "latitude", "longitude", "confidence", "horizontal_accuracy", "status"])
    for r in reports:
        w.writerow([r.get("timestamp", ""), r.get("latitude", ""), r.get("longitude", ""),
                    r.get("confidence", ""), r.get("horizontal_accuracy", ""), r.get("status", "")])
    return buf.getvalue()


def _gpx(tag: dict, reports: list[dict], _p: bool) -> str:
    name = escape(tag["name"])
    pts = "\n".join(
        f'        <trkpt lat="{r["latitude"]:.7f}" lon="{r["longitude"]:.7f}">\n'
        f'          <time>{escape(_iso(r.get("timestamp", "")))}</time>\n'
        f'        </trkpt>'
        for r in reports
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="NetWatch" xmlns="http://www.topografix.com/GPX/1/1">\n'
        f'  <metadata><name>{name}</name></metadata>\n'
        f'  <trk>\n    <name>{name}</name>\n    <trkseg>\n{pts}\n    </trkseg>\n  </trk>\n'
        '</gpx>\n'
    )


def _kml(tag: dict, reports: list[dict], _p: bool) -> str:
    name = escape(tag["name"])
    marks = "\n".join(
        f'    <Placemark>\n      <name>{name}</name>\n'
        f'      <TimeStamp><when>{escape(_iso(r.get("timestamp", "")))}</when></TimeStamp>\n'
        f'      <description>accuracy: {r.get("horizontal_accuracy", "?")} m, '
        f'confidence: {r.get("confidence", "?")}</description>\n'
        f'      <Point><coordinates>{r["longitude"]:.7f},{r["latitude"]:.7f},0</coordinates></Point>\n'
        f'    </Placemark>'
        for r in reports
    )
    line = " ".join(f'{r["longitude"]:.7f},{r["latitude"]:.7f},0' for r in reports)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<kml xmlns="http://www.opengis.net/kml/2.2">\n  <Document>\n'
        f'    <name>{name}</name>\n{marks}\n'
        f'    <Placemark><name>{name} track</name>\n'
        f'      <LineString><tessellate>1</tessellate><coordinates>{line}</coordinates></LineString>\n'
        f'    </Placemark>\n'
        '  </Document>\n</kml>\n'
    )


def _geojson(tag: dict, reports: list[dict], _p: bool) -> str:
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["longitude"], r["latitude"]]},
            "properties": {
                "name": tag["name"],
                "timestamp": r.get("timestamp"),
                "confidence": r.get("confidence"),
                "horizontal_accuracy": r.get("horizontal_accuracy"),
            },
        }
        for r in reports
    ]
    if len(reports) > 1:
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[r["longitude"], r["latitude"]] for r in reports]},
            "properties": {"name": f"{tag['name']} track"},
        })
    return json.dumps({"type": "FeatureCollection", "features": features}, indent=2) + "\n"


def _cot(tag: dict, reports: list[dict], _p: bool) -> str:
    name = escape(tag["name"])
    events = []
    for r in reports:
        ts = r.get("timestamp", "")
        try:
            start = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            start = datetime.now(timezone.utc)
        stale = (start + timedelta(hours=1)).isoformat()
        ce = r.get("horizontal_accuracy") or 9999999.0
        events.append(
            f'  <event version="2.0" uid="netwatch-{escape(tag["hashedAdvKey"][:16])}-{uuid.uuid4().hex[:8]}" '
            f'type="a-f-G-U-C" time="{escape(ts)}" start="{escape(ts)}" stale="{escape(stale)}" how="m-g">\n'
            f'    <point lat="{r["latitude"]:.7f}" lon="{r["longitude"]:.7f}" hae="0.0" ce="{ce}" le="9999999.0"/>\n'
            f'    <detail><contact callsign="{name}"/><remarks>NetWatch tag</remarks></detail>\n'
            f'  </event>'
        )
    return '<?xml version="1.0" encoding="UTF-8"?>\n<events>\n' + "\n".join(events) + "\n</events>\n"


_DISPATCH = {
    "json": _json, "csv": _csv, "gpx": _gpx,
    "kml": _kml, "geojson": _geojson, "cot": _cot,
}
