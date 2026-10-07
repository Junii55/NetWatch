"""Basemap sources, and the licensing reality behind each one.

Read this before shipping. There is no free tile source that is unambiguously
licensed for an unlimited commercial product. The options are:

  * low volume on a community server, within their policy and with attribution;
  * a paid provider with your API key (what a product normally does);
  * self-hosting your own tiles (no per-tile cost, no vendor cut-off).

Each entry carries a `licence` field so the UI can tell the truth about what the
user is looking at, instead of quietly putting the operator at risk.

`unlicensed: True` marks sources that breach the provider's terms. Those exist
ONLY in the `personal` edition and must never ship in something you sell.
"""

from __future__ import annotations

from .config import IS_RETAIL

# Free / community sources. Fine for personal use and low-volume; every one of
# them has a usage policy that rules out a high-traffic commercial product.
_COMMUNITY = {
    "osm": {
        "label": "Street (OpenStreetMap)",
        "url": "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attr": '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        "maxZoom": 19,
        "licence": "community",
        "note": "OSMF tile policy: no heavy or commercial use. Fine while you are small.",
    },
    # CARTO's basemaps were here. Removed 2026-10: they now serve tiles
    # watermarked "API KEY REQUIRED" to unkeyed callers. A good reminder that a
    # free community tile source can withdraw at any time, which is why the
    # Custom / self-hosted option exists.
    "opentopo": {
        "label": "Topographic (OpenTopoMap)",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "subdomains": "abc",
        "attr": '&copy; OpenStreetMap contributors, SRTM | &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
        "maxZoom": 17,
        "licence": "community",
        "note": "Volunteer-run and rate-limited. Not for production traffic.",
    },
}

# No API-key providers. NetWatch deliberately ships only sources that work out
# of the box, plus your own self-hosted tiles. If you ever want a paid provider
# (MapTiler, Stadia, Thunderforest), add it as another entry with `{key}` in the
# URL and substitute the key in resolve() — nothing else needs to change.

# Google's undocumented endpoints. PERSONAL EDITION ONLY.
_GOOGLE = {
    "google_roadmap": {
        "label": "Google Roads", "url": "https://{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
        "subdomains": ["mt0", "mt1", "mt2", "mt3"], "attr": "Map data &copy; Google",
        "maxZoom": 20, "licence": "unlicensed", "unlicensed": True,
    },
    "google_satellite": {
        "label": "Google Satellite", "url": "https://{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        "subdomains": ["mt0", "mt1", "mt2", "mt3"], "attr": "Imagery &copy; Google",
        "maxZoom": 22, "licence": "unlicensed", "unlicensed": True,
    },
    "google_hybrid": {
        "label": "Google Hybrid", "url": "https://{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        "subdomains": ["mt0", "mt1", "mt2", "mt3"], "attr": "Imagery &copy; Google",
        "maxZoom": 22, "licence": "unlicensed", "unlicensed": True,
    },
    "google_terrain": {
        "label": "Google Terrain", "url": "https://{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
        "subdomains": ["mt0", "mt1", "mt2", "mt3"], "attr": "Map data &copy; Google",
        "maxZoom": 20, "licence": "unlicensed", "unlicensed": True,
    },
}

CUSTOM_ID = "custom"


def available(*, custom_url: str = "") -> dict[str, dict]:
    """Basemaps this build offers, in menu order.

    Open sources plus, if configured, your own tile server. Nothing here needs
    an account or an API key.
    """
    out: dict[str, dict] = dict(_COMMUNITY)
    if custom_url.strip():
        out[CUSTOM_ID] = {
            "label": "Custom / self-hosted",
            "url": custom_url.strip(),
            "attr": "Custom tile source",
            "maxZoom": 22,
            "licence": "custom",
            "note": "Your own tile server. No third-party limits.",
        }
    if not IS_RETAIL:
        out.update(_GOOGLE)
    return out


def resolve(tile_id: str, *, custom_url: str = "") -> dict | None:
    """A ready-to-use layer spec, or None if that layer is not available."""
    spec = available(custom_url=custom_url).get(tile_id)
    return dict(spec) if spec else None


def default_id() -> str:
    """What to show on first run."""
    return "osm"
