"""NetWatch Desktop entry point.

Starts the loopback API, restores a saved Apple session, and opens a native
window (WebView2 on Windows, WebKit on macOS/Linux). Falls back to the default
browser if no webview runtime is available.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import threading
import time

from . import store
from .appleclient import HELPER
from .config import APP_NAME, APP_VERSION, DEMO_MODE, LOG_PATH, ensure_dirs


def setup_logging(verbose: bool = False) -> None:
    ensure_dirs()
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    fh = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # Never let a dependency log a credential.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("srp").setLevel(logging.WARNING)


def auto_sync_loop(stop: threading.Event) -> None:
    """Background refresh for tags with auto-sync enabled."""
    log = logging.getLogger("netwatch.autosync")
    while not stop.wait(60):
        try:
            interval_h = float(store.get_setting("auto_sync_interval", 8) or 8)
            last = store.get_setting("last_auto_sync", 0) or 0
            if time.time() - float(last) < interval_h * 3600:
                continue
            rows = [t for t in store.list_tags(with_private=True) if t["auto_sync"]]
            if not rows:
                continue
            log.info("auto-sync: refreshing %d tag(s)", len(rows))
            fetched = HELPER.fetch_reports(rows)
            total = sum(
                store.add_reports(r["id"], fetched.get(r["hashedAdvKey"], [])) for r in rows
            )
            store.set_setting("last_auto_sync", time.time())
            log.info("auto-sync: %d new report(s)", total)
        except Exception as exc:
            log.debug("auto-sync skipped: %s", exc)


def main() -> int:
    ap = argparse.ArgumentParser(prog="netwatch", description=f"{APP_NAME} Desktop")
    ap.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    ap.add_argument("--no-window", action="store_true",
                    help="serve the API only and print the URL (no native window)")
    ap.add_argument("--browser", action="store_true", help="open in the default browser")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)
    log = logging.getLogger("netwatch")
    log.info("%s %s starting%s", APP_NAME, APP_VERSION, " (DEMO MODE)" if DEMO_MODE else "")

    from .server import serve, SESSION_TOKEN

    httpd, url = serve()
    full_url = f"{url}?token={SESSION_TOKEN}"

    # Restoring a session touches the network; don't block the window on it.
    threading.Thread(target=HELPER.restore, daemon=True).start()

    # If the operator switched the ATAK bridge on, bring it back. It stays shut
    # otherwise — a tracker app has no business opening a LAN port uninvited.
    from . import atakbridge
    atakbridge.start_if_enabled()

    stop = threading.Event()
    threading.Thread(target=auto_sync_loop, args=(stop,), daemon=True).start()

    def park() -> int:
        """Keep the process alive for the headless paths, then clean up."""
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            atakbridge.stop(remember=False)
            httpd.shutdown()
        return 0

    if args.no_window:
        print(f"{APP_NAME} API: {full_url}")
        if atakbridge.is_running():
            b = atakbridge.status()
            where = ", ".join(b["urls"]) or f"port {b['port']}"
            print(f"{APP_NAME} ATAK bridge: {where}  pair code: {b['code']}")
        return park()

    if args.browser:
        import webbrowser
        webbrowser.open(full_url)
        return park()

    try:
        import webview

        webview.create_window(
            APP_NAME, full_url,
            width=1280, height=820, min_size=(960, 640),
            background_color="#1b1b1d",
        )
        webview.start()
    except Exception as exc:
        log.warning("native window unavailable (%s); falling back to the browser", exc)
        import webbrowser
        webbrowser.open(full_url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    finally:
        stop.set()
        # remember=False: closing the app must not un-tick the operator's choice.
        atakbridge.stop(remember=False)
        httpd.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
