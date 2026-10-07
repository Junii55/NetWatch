"""Interactive Apple sign-in check — RUN THIS YOURSELF.

This is the one part of the pipeline that cannot be automated or tested on your
behalf, because it needs your real Apple ID password. Use a spare Apple ID.

    cd app
    python tests\test_apple_login.py

What it does:
  1. provisions anisette in-process (proves no Docker is needed)
  2. signs in, handling two-factor
  3. persists the session, then reopens it from disk
  4. optionally fetches reports for the tags already in your database

What it never does:
  * print your password
  * write your password anywhere
  * keep your password after the login call returns

The password is read with getpass (no echo) and held only for the duration of
the login call.
"""

from __future__ import annotations

import getpass
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from netwatch import store                      # noqa: E402
from netwatch.appleclient import HELPER, AppleError, STATE_LOGGED_IN, STATE_NEEDS_2FA  # noqa: E402

ok = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok
    ok = ok and bool(cond)
    print(("PASS" if cond else "FAIL"), label, extra)


def main() -> int:
    if os.environ.get("NETWATCH_DEMO") == "1":
        print("NETWATCH_DEMO=1 is set — unset it, or this tests nothing real.")
        return 1

    store.init_db()

    print("=" * 62)
    print(" NetWatch — Apple sign-in check")
    print(" Use a SPARE Apple ID. Your password is never stored.")
    print("=" * 62)

    # 1. anisette ------------------------------------------------------
    print("\n[1/4] Starting the Apple helper (anisette, in-process)...")
    t0 = time.time()
    health = HELPER.health()
    check("anisette healthy", health["ok"], f"{time.time() - t0:.1f}s · {health.get('anisette')}")
    if not health["ok"]:
        print("     ", health["detail"])
        return 1

    # 2. sign in -------------------------------------------------------
    print("\n[2/4] Sign in")
    email = input("      Apple ID: ").strip()
    if not email:
        print("      aborted")
        return 1
    password = getpass.getpass("      Password (not echoed): ")

    try:
        status = HELPER.login(email, password)
    except AppleError as exc:
        check("login accepted", False, str(exc))
        return 1
    finally:
        del password          # drop it immediately

    if status["state"] == STATE_NEEDS_2FA:
        methods = status["methods"]
        print("\n      Two-factor required:")
        for i, m in enumerate(methods):
            print(f"        [{i}] {m['label']}")
        idx = 0
        if len(methods) > 1:
            try:
                idx = int(input(f"      Choose 0-{len(methods) - 1}: ").strip() or "0")
            except ValueError:
                idx = 0
        try:
            HELPER.request_2fa(idx)
            print("      code sent")
        except AppleError as exc:
            check("2FA code sent", False, str(exc))
            return 1

        for attempt in range(3):
            code = input("      Code: ").strip()
            try:
                status = HELPER.submit_2fa(idx, code)
                break
            except AppleError as exc:
                print(f"      rejected: {exc}")
                if attempt == 2:
                    check("2FA accepted", False)
                    return 1

    check("signed in", status["state"] == STATE_LOGGED_IN, status.get("email", ""))
    if status["state"] != STATE_LOGGED_IN:
        return 1

    # 3. session persistence -------------------------------------------
    print("\n[3/4] Session persistence")
    saved = store.load_apple_session()
    check("session saved and sealed", saved is not None)
    if saved:
        check("saved email matches", saved[0] == email)
        check("stored blob is not the password", "password" not in str(saved[1]).lower())

    HELPER._account = None          # force a cold restore
    HELPER._state = "logged_out"
    check("session restores from disk", HELPER.restore())

    # 4. fetch ----------------------------------------------------------
    print("\n[4/4] Location fetch")
    rows = store.list_tags(with_private=True)
    if not rows:
        print("      No tags in the database yet — add one and flash a board,")
        print("      then re-run to exercise the fetch path.")
    else:
        print(f"      Fetching for {len(rows)} tag(s)...")
        try:
            fetched = HELPER.fetch_reports(rows)
            total = sum(len(v) for v in fetched.values())
            check("fetch succeeded", True, f"{total} report(s)")
            for row in rows:
                n = len(fetched.get(row["hashedAdvKey"], []))
                print(f"        {row['name']:<20} {n} report(s)")
            if total == 0:
                print("      0 reports is normal for a freshly flashed tag:")
                print("      an iPhone has to pass it first (5-40 minutes).")
        except AppleError as exc:
            check("fetch succeeded", False, str(exc))

    print("\nRESULT:", "ALL PASS" if ok else "FAILURES PRESENT")
    print("\nYour session is stored encrypted; the app will reuse it.")
    print("Sign out any time from the app's Login panel.")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\naborted")
        raise SystemExit(130)
