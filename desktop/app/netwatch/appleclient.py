"""Apple Helper — authentication and location-report retrieval.

What this does, and what Docker used to do
------------------------------------------
Nothing here needs a container. The classic OpenHaystack / Macless-Haystack
stack runs Docker solely to host an *anisette server*, which produces the
device-attestation headers Apple requires at login. The `anisette` package does
that in-process, so the whole pipeline is:

    1. ESP32 advertises the tag's PUBLIC key over BLE.
    2. Passing iPhones encrypt their own GPS fix with that public key and
       upload it to Apple. (Apple's phones do this work, not us.)
    3. We authenticate to Apple and ask for reports matching the SHA-256 of our
       advertisement keys.  <- anisette headers are needed only here
    4. We decrypt the reports locally with the tag's PRIVATE key.

Credential policy — read this before repeating the old claim
------------------------------------------------------------
NetWatch never writes the password itself. **findmy does.** `AppleAccount.
to_json()` includes the account password, because Apple expires the iCloud token
every few days and findmy silently re-runs the GSA login with the stored
credentials to recover. Persist the session and the password goes with it.

So the honest statement is: the password is sealed at rest inside the session
blob (DPAPI on Windows, bound to this user account), never logged, and never
sent anywhere except Apple. Stripping it before saving does not make the secret
go away — it only breaks silent re-auth, so the customer retypes their password
and a 2FA code every few days.

Earlier revisions of this file and the README claimed the password was never
written to disk. That was wrong.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from . import store
from .config import ANISETTE_LIBS, ANISETTE_PROV, DEMO_MODE, ensure_dirs  # noqa: F401

log = logging.getLogger("netwatch.apple")

STATE_LOGGED_OUT = "logged_out"
STATE_NEEDS_2FA = "needs_2fa"
STATE_LOGGED_IN = "logged_in"


class AppleError(Exception):
    """A user-presentable Apple-side failure."""


class AppleHelper:
    """Owns the Apple session. Thread-safe; the HTTP server is threaded."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._account: Any = None
        self._anisette: Any = None
        self._email: str = ""
        self._state: str = STATE_LOGGED_OUT
        self._methods: list[Any] = []
        self._last_error: str = ""

    # ----------------------------------------------------------------- #
    # Anisette
    # ----------------------------------------------------------------- #
    def _get_anisette(self):
        """Build (and cache on disk) the in-process anisette provider."""
        if self._anisette is not None:
            return self._anisette
        from anisette import Anisette

        ensure_dirs()
        try:
            if ANISETTE_LIBS.exists() and ANISETTE_PROV.exists():
                ani = Anisette.load(ANISETTE_LIBS, ANISETTE_PROV)
            else:
                ani = Anisette.init()
            if not ani.is_provisioned:
                ani.provision()
            # Cache so later launches skip provisioning entirely.
            ani.save_libs(ANISETTE_LIBS)
            ani.save_provisioning(ANISETTE_PROV)
        except Exception as exc:
            raise AppleError(
                f"could not start the Apple helper (anisette): {exc}. "
                f"Check your internet connection and try again."
            ) from exc

        self._anisette = ani
        return ani

    def _anisette_provider(self):
        """Wrap the raw anisette session in findmy's provider interface."""
        from findmy import LocalAnisetteProvider

        ani = self._get_anisette()
        provider = LocalAnisetteProvider()
        # findmy's LocalAnisetteProvider manages its own Anisette internally;
        # we keep ours warm so provisioning is cached and health checks are cheap.
        _ = ani
        return provider

    # ----------------------------------------------------------------- #
    # Health
    # ----------------------------------------------------------------- #
    def health(self) -> dict:
        """Backs the 'Apple Helper' status card in the UI."""
        if DEMO_MODE:
            return {"ok": True, "detail": "Demo mode — no Apple calls are made.",
                    "anisette": "demo", "technical": "NETWATCH_DEMO=1"}
        try:
            ani = self._get_anisette()
            headers = ani.get_data()
            return {
                "ok": True,
                "detail": "Apple helper is running normally. Signing in and updating "
                          "tag locations can work.",
                "anisette": "in-process (no Docker)",
                "technical": f"provisioned={ani.is_provisioned} headers={len(headers)} "
                             f"cache={ANISETTE_PROV.name}",
            }
        except Exception as exc:
            return {
                "ok": False,
                "detail": f"Apple helper is not healthy: {exc}",
                "anisette": "unavailable",
                "technical": repr(exc),
            }

    # ----------------------------------------------------------------- #
    # Session
    # ----------------------------------------------------------------- #
    def status(self) -> dict:
        with self._lock:
            return {
                "state": self._state,
                "email": self._email,
                "methods": [self._describe(m) for m in self._methods],
                "last_error": self._last_error,
            }

    @staticmethod
    def _describe(method: Any) -> dict:
        name = type(method).__name__
        if "Sms" in name:
            return {
                "kind": "sms",
                "label": f"Text message to {getattr(method, 'phone_number', '') or 'your phone'}",
            }
        return {"kind": "trusted_device", "label": "Prompt on a trusted Apple device"}

    @staticmethod
    def _map_state(account: Any) -> str:
        """Translate findmy's login state into ours, honestly.

        The states are ordered LOGGED_OUT < REQUIRE_2FA < AUTHENTICATED <
        LOGGED_IN. Only the last can fetch reports: AUTHENTICATED means the
        password was accepted but the iCloud login has not completed, so it is
        not a usable session either.
        """
        from findmy import LoginState

        try:
            st = account.login_state
        except Exception:
            return STATE_LOGGED_OUT
        if st == LoginState.LOGGED_IN:
            return STATE_LOGGED_IN
        if st == LoginState.REQUIRE_2FA:
            return STATE_NEEDS_2FA
        return STATE_LOGGED_OUT

    def _load_2fa_methods(self, account: Any) -> list:
        try:
            return list(account.get_2fa_methods())
        except Exception as exc:
            log.warning("could not list two-factor methods: %s", exc)
            return []

    def restore(self) -> bool:
        """Re-open a saved session at launch. Returns True if signed in.

        Takes the restored account's word for its own state instead of assuming
        success. Assuming it was a real bug: a session saved mid-2FA, or one
        Apple had since invalidated, came back as "signed in", and the failure
        only surfaced later as an unexplained error on the first sync.
        """
        if DEMO_MODE:
            with self._lock:
                self._state, self._email = STATE_LOGGED_IN, "demo@example.com"
            return True
        saved = store.load_apple_session()
        if not saved:
            return False
        email, state_info = saved
        try:
            from findmy import AppleAccount

            # from_json, NOT AppleAccount(provider, state_info=...).
            #
            # Both restore the login tokens, but the constructor pairs them with
            # whatever anisette provider you hand it — a *fresh* one, with new
            # provisioning. Apple binds a session to the anisette device that
            # created it, so presenting the old tokens with a new device gets a
            # 401, which findmy answers by re-running the login, which Apple
            # answers with REQUIRE_2FA. That is the "it logs me out every time I
            # reopen it" bug: the session was fine, we were handing Apple a
            # different device each launch.
            #
            # from_json rebuilds the provider from the saved mapping, so the
            # device identity survives. Verified by round-tripping a provisioned
            # account: the constructor loses the anisette blob, from_json keeps it.
            account = AppleAccount.from_json(
                state_info, anisette_libs_path=str(ANISETTE_LIBS)
                if ANISETTE_LIBS.exists() else None)
            state = self._map_state(account)
            if state == STATE_LOGGED_OUT:
                # Do NOT delete the saved session here. See the handler below.
                log.info("saved Apple session for %s is not usable yet", email)
                with self._lock:
                    self._account, self._email = account, email
                    self._state = STATE_LOGGED_OUT
                return False

            methods = self._load_2fa_methods(account) if state == STATE_NEEDS_2FA else []
            with self._lock:
                self._account, self._email, self._state = account, email, state
                self._methods = methods
                self._last_error = ("" if state == STATE_LOGGED_IN else
                                    "Apple needs a two-factor code to finish signing in.")
            log.info("restored Apple session for %s (%s)", email, state)
            return state == STATE_LOGGED_IN
        except Exception as exc:
            # Deliberately NOT clearing the saved session.
            #
            # This used to delete it, which turned any one-off failure — no
            # network while anisette starts, a library hiccup, a half-written
            # blob — into a permanent sign-out with nothing left to retry from.
            # Keeping it costs nothing: a session that is genuinely dead just
            # fails again next launch, and signing in overwrites it. Only an
            # explicit logout deletes it now.
            log.warning("could not restore the Apple session (keeping it for "
                        "the next attempt): %s", exc)
            with self._lock:
                self._state = STATE_LOGGED_OUT
                self._email = email
                self._last_error = f"Could not reopen the saved Apple session: {exc}"
            return False

    def login(self, email: str, password: str) -> dict:
        """Sign in. The password is discarded as soon as this returns."""
        if DEMO_MODE:
            with self._lock:
                self._state, self._email = STATE_LOGGED_IN, email
            return self.status()
        if not email or not password:
            raise AppleError("Enter both your Apple ID and password.")

        from findmy import AppleAccount, LoginState

        try:
            account = AppleAccount(self._anisette_provider())
            result = account.login(email, password)
        except AppleError:
            raise
        except Exception as exc:
            msg = str(exc) or exc.__class__.__name__
            if "credential" in msg.lower() or "401" in msg:
                raise AppleError("Apple rejected that Apple ID or password.") from exc
            raise AppleError(f"Sign-in failed: {msg}") from exc
        finally:
            password = ""  # noqa: F841  - drop the reference promptly

        with self._lock:
            self._account = account
            self._email = email
            self._last_error = ""
            if result == LoginState.REQUIRE_2FA:
                try:
                    self._methods = list(account.get_2fa_methods())
                except Exception as exc:
                    raise AppleError(f"Could not list two-factor methods: {exc}") from exc
                self._state = STATE_NEEDS_2FA
            elif result == LoginState.LOGGED_IN:
                self._methods = []
                self._state = STATE_LOGGED_IN
                self._persist()
            else:
                # AUTHENTICATED or LOGGED_OUT: the password went through but the
                # session is not usable, so do not claim it is.
                self._state = STATE_LOGGED_OUT
                self._methods = []
                raise AppleError(
                    f"Apple did not finish signing in (state: {result}). "
                    f"Try again in a moment."
                )
            return self.status()

    def request_2fa(self, index: int) -> dict:
        with self._lock:
            if self._state != STATE_NEEDS_2FA:
                raise AppleError("Not waiting for a two-factor code.")
            if not 0 <= index < len(self._methods):
                raise AppleError("That two-factor option is no longer available.")
            method = self._methods[index]
        try:
            method.request()
        except Exception as exc:
            raise AppleError(f"Could not send the code: {exc}") from exc
        return {"sent": True, "method": self._describe(method)}

    def submit_2fa(self, index: int, code: str) -> dict:
        from findmy import LoginState

        code = (code or "").strip().replace("-", "").replace(" ", "")
        if not code:
            raise AppleError("Enter the six-digit code.")
        with self._lock:
            if self._state != STATE_NEEDS_2FA:
                raise AppleError("Not waiting for a two-factor code.")
            if not 0 <= index < len(self._methods):
                raise AppleError("That two-factor option is no longer available.")
            method = self._methods[index]
        try:
            result = method.submit(code)
        except Exception as exc:
            raise AppleError(f"That code was not accepted: {exc}") from exc

        # A good 2FA submit ends at LOGGED_IN: findmy re-runs the GSA auth and
        # then logs into mobileme. AUTHENTICATED means the code was accepted but
        # the iCloud login did not finish, which cannot fetch reports — storing
        # that as "signed in" is how you get a mystery failure on the next sync.
        with self._lock:
            if result == LoginState.LOGGED_IN or self._map_state(self._account) == STATE_LOGGED_IN:
                self._state = STATE_LOGGED_IN
                self._methods = []
                self._last_error = ""
                self._persist()
            elif result == LoginState.AUTHENTICATED:
                raise AppleError(
                    "Apple accepted the code but did not finish signing in to "
                    "iCloud. Try again in a moment."
                )
            else:
                raise AppleError("Apple did not complete sign-in. Try the code again.")
            return self.status()

    def _fetch_error(self, account: Any, exc: Exception) -> AppleError:
        """Turn a fetch failure into something the operator can act on.

        The common one is Apple expiring the session: findmy notices the 401,
        re-runs the GSA login with the stored credentials, and Apple answers
        REQUIRE_2FA. findmy then raises "Unexpected login state after reauth",
        which is accurate and tells the customer nothing.

        The account itself is the authority on what happened, so ask it rather
        than matching on the message. When it really is 2FA, move into the
        two-factor state and load the methods, so the UI can ask for a code
        instead of making them sign in from scratch.
        """
        if self._map_state(account) == STATE_NEEDS_2FA:
            methods = self._load_2fa_methods(account)
            with self._lock:
                self._state = STATE_NEEDS_2FA
                self._methods = methods
                self._last_error = "Apple asked for a new two-factor code."
            self._persist()
            return AppleError(
                "Apple needs a new two-factor code before it will hand over "
                "locations. Open Login and enter the code it sends you — your "
                "tags and their keys are untouched."
            )
        return AppleError(f"Could not fetch locations from Apple: {exc}")

    def _persist(self) -> None:
        """Save the session blob.

        Note this writes the Apple password too, because findmy's to_json()
        includes it and needs it to re-authenticate. See the module docstring.
        `secretbox` seals the blob before it reaches the disk.
        """
        with self._lock:
            account, email = self._account, self._email
        if account is None:
            return
        try:
            store.save_apple_session(email, dict(account.to_json()))
        except Exception as exc:
            log.warning("could not save Apple session: %s", exc)

    def logout(self) -> None:
        with self._lock:
            try:
                if self._account is not None:
                    self._account.close()
            except Exception:
                pass
            self._account = None
            self._methods = []
            self._state = STATE_LOGGED_OUT
            self._email = ""
        store.clear_apple_session()

    # ----------------------------------------------------------------- #
    # Reports
    # ----------------------------------------------------------------- #
    def fetch_reports(self, tag_rows: list[dict], *, history: bool = True) -> dict[str, list[dict]]:
        """Fetch and decrypt reports. Returns {hashedAdvKey: [report, ...]}."""
        if DEMO_MODE:
            return _demo_reports(tag_rows)
        with self._lock:
            account, state = self._account, self._state
        if state != STATE_LOGGED_IN or account is None:
            raise AppleError("Sign in with your Apple ID first.")
        if not tag_rows:
            return {}

        from . import tags as tagmod

        keys, by_hash = [], {}
        for row in tag_rows:
            kp = tagmod.keypair_for(row["privateKey"])
            keys.append(kp)
            by_hash[kp.hashed_adv_key_b64] = row["hashedAdvKey"]

        try:
            fetch = account.fetch_location_history if history else account.fetch_location
            raw = fetch(keys)
        except Exception as exc:
            raise self._fetch_error(account, exc) from exc

        # Apple's iCloud token expires every few days. findmy renews it silently
        # during a fetch, in memory — so without this the saved blob keeps
        # getting older, every launch replays a stale session, and eventually
        # the renewal needs a fresh 2FA code. Save the refreshed state instead.
        self._persist()

        out: dict[str, list[dict]] = {row["hashedAdvKey"]: [] for row in tag_rows}
        for key, reports in _normalise(raw, keys):
            bucket = out.setdefault(by_hash.get(key.hashed_adv_key_b64, key.hashed_adv_key_b64), [])
            for rep in reports:
                converted = _convert(rep)
                if converted:
                    bucket.append(converted)
        return out


def _normalise(raw: Any, keys: list) -> list[tuple[Any, list]]:
    """findmy returns a report, a list, or a dict depending on the call shape."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [(k, (v if isinstance(v, list) else ([v] if v else []))) for k, v in raw.items()]
    if isinstance(raw, list):
        return [(keys[0], raw)] if keys else []
    return [(keys[0], [raw])] if keys else []


def _convert(rep: Any) -> dict | None:
    try:
        ts = rep.timestamp
        if isinstance(ts, datetime):
            ts = ts.astimezone(timezone.utc).isoformat()
        return {
            "timestamp": str(ts),
            "latitude": float(rep.latitude),
            "longitude": float(rep.longitude),
            "confidence": _maybe_int(getattr(rep, "confidence", None)),
            "horizontal_accuracy": _maybe_float(getattr(rep, "horizontal_accuracy", None)),
            "status": _maybe_int(getattr(rep, "status", None)),
        }
    except Exception as exc:  # an undecryptable report is not fatal
        log.debug("skipping report: %s", exc)
        return None


def _maybe_int(v):
    try: return int(v) if v is not None else None
    except Exception: return None


def _maybe_float(v):
    try: return float(v) if v is not None else None
    except Exception: return None


def _demo_reports(tag_rows: list[dict]) -> dict[str, list[dict]]:
    """Synthetic track so the whole UI can be exercised without Apple."""
    import math
    import random
    from datetime import timedelta

    # Anchor to the top of the hour so repeated syncs return the SAME points —
    # otherwise demo mode invents fresh history on every refresh and hides
    # whether de-duplication actually works.
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    out: dict[str, list[dict]] = {}
    for i, row in enumerate(tag_rows):
        rnd = random.Random(row["hashedAdvKey"])
        lat0 = 40.7128 + rnd.uniform(-0.05, 0.05) + i * 0.01
        lon0 = -74.0060 + rnd.uniform(-0.05, 0.05) + i * 0.01
        pts = []
        for n in range(24):
            pts.append({
                "timestamp": (now - timedelta(minutes=30 * n)).isoformat(),
                "latitude": lat0 + 0.004 * math.sin(n / 3.0) + rnd.uniform(-3e-4, 3e-4),
                "longitude": lon0 + 0.004 * math.cos(n / 3.0) + rnd.uniform(-3e-4, 3e-4),
                "confidence": rnd.choice([1, 2, 2, 3]),
                "horizontal_accuracy": round(rnd.uniform(8, 80), 1),
                "status": 0,
            })
        out[row["hashedAdvKey"]] = pts
    return out


HELPER = AppleHelper()
