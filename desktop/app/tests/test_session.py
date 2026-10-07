"""Apple session persistence — the "it logs me out every time" regression.

The bug this pins down:

  NetWatch restored a saved session with ``AppleAccount(provider, state_info=...)``.
  That restores the login tokens but pairs them with the *fresh* anisette
  provider it is handed, discarding the provisioning that was saved alongside
  them. Apple binds a session to the anisette device that created it, so the old
  tokens presented by a new device get a 401; findmy answers the 401 by re-running
  the login, and Apple answers that with REQUIRE_2FA. The customer sees "signed
  out, log in again" on every launch even though nothing was wrong with the
  session.

  ``AppleAccount.from_json`` rebuilds the provider from the saved mapping, so the
  device survives the round trip.

The decisive assertion is `anisette provisioning survives`. The others guard the
second half of the fix: a failed restore must not delete the saved session, which
previously turned one bad launch into a permanent sign-out.

Needs network for the provisioning step, and makes no Apple *account* calls — no
credentials are involved anywhere in this file.

    python app\\tests\\test_session.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="netwatch-session-test-"))
os.environ["NETWATCH_DATA_DIR"] = str(_TMP)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netwatch import store                                   # noqa: E402
from netwatch.appleclient import HELPER, STATE_LOGGED_OUT    # noqa: E402

PASS, FAIL = 0, 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    store.init_db()

    from findmy import AppleAccount, LocalAnisetteProvider

    print("provisioning an anisette device (no account involved)...")
    account = AppleAccount(LocalAnisetteProvider())
    try:
        headers = account.get_anisette_headers()
    except Exception as exc:
        print(f"\nSKIPPED: anisette could not provision ({exc}).")
        print("This test needs network. Nothing is proven either way.")
        return 0
    print(f"  {len(headers)} headers\n")

    saved = dict(account.to_json())
    ani_before = json.dumps(saved["anisette"], sort_keys=True)
    ids_before = saved["ids"]
    check("a provisioned account carries real anisette state",
          len(ani_before) > 500, f"{len(ani_before)} bytes")

    print("\n-- the regression ----------------------------------------------")
    # The old way, kept here so the difference stays visible to anyone who is
    # tempted to "simplify" the restore back to the constructor.
    old = AppleAccount(LocalAnisetteProvider(), state_info=saved)
    old_ani = json.dumps(dict(old.to_json())["anisette"], sort_keys=True)
    check("the old constructor restore LOSES the anisette device "
          "(this is the bug)", old_ani != ani_before)

    new = AppleAccount.from_json(saved)
    new_state = dict(new.to_json())
    check("from_json keeps the anisette device",
          json.dumps(new_state["anisette"], sort_keys=True) == ani_before)
    check("from_json keeps the device ids", new_state["ids"] == ids_before)

    print("\n-- through NetWatch's own store ---------------------------------")
    store.save_apple_session("tester@example.com", saved)
    loaded = store.load_apple_session()
    check("the session seals and unseals", loaded is not None)
    email, state_info = loaded
    check("the email round-trips", email == "tester@example.com")
    check("the sealed blob keeps the anisette device",
          json.dumps(state_info["anisette"], sort_keys=True) == ani_before)

    restored = AppleAccount.from_json(state_info)
    check("a sealed-then-restored account still has the same device",
          json.dumps(dict(restored.to_json())["anisette"], sort_keys=True) == ani_before)

    print("\n-- a failed restore must not destroy the session ----------------")
    # Point the helper at a blob that cannot be rebuilt, which is what a
    # transient failure looks like from the outside.
    store.save_apple_session("tester@example.com", {"type": "account", "junk": True})
    HELPER._account = None
    HELPER._state = STATE_LOGGED_OUT
    ok = HELPER.restore()
    check("restore() reports failure on a broken blob", ok is False)
    check("the saved session is STILL THERE after a failed restore",
          store.load_apple_session() is not None,
          "it was deleted - a single bad launch would sign the user out forever")

    print("\n-- logout is the only thing that deletes it ---------------------")
    HELPER.logout()
    check("logout() clears the saved session", store.load_apple_session() is None)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
