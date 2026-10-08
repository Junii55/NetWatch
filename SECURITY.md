# Security

What NetWatch stores, where it is stored, and what is exposed over the network.

## Reporting a vulnerability

Please open an issue at <https://github.com/junii55/netwatch/issues>, or contact
the maintainer privately if disclosure should be delayed. This is a small project
and there is no formal response-time commitment.

---

## What's stored, and where

Everything lives in `%LOCALAPPDATA%\NetWatch\`:

| | |
|---|---|
| `netwatch.db` | SQLite: tags, sealed private keys, location history, settings, sealed Apple session |
| `netwatch.log` | Activity log. No credentials, no key material |
| `anisette-*.bin` | Cached device provisioning, so sign-in is fast after the first time |

Tag private keys and the Apple session blob are sealed before they touch the
disk: **Windows DPAPI** bound to your user account, or AES-GCM with a `0600` key
file on other platforms. Another user on the same machine cannot read them.

That is not a substitute for disk encryption. It stops casual reads and other
local accounts; it does not stop someone with your logged-in session.

## The saved session includes the Apple password

Earlier revisions of this documentation stated that the password was never
written to disk. That was inaccurate, and the claim has been corrected
everywhere it appeared.

NetWatch does not write the password itself; the `findmy` library does.
`AppleAccount.to_json()` includes it, because Apple expires the iCloud token
every few days and the library re-runs the login to recover. Persisting the
session therefore persists the password.

Removing it before saving would not eliminate the secret — it would only break
that renewal, requiring the password and a verification code roughly twice a
week.

What is accurate:

- sealed with DPAPI before it reaches the disk, bound to your Windows account;
- never logged;
- sent nowhere except Apple;
- deleted when you sign out.

**Use a spare Apple ID.** If that trade isn't one you want, this is the point to
decide.

## Tag keys are unrecoverable on purpose

Each tag is an elliptic-curve keypair. The public half goes into the firmware and
is broadcast continuously. The private half stays in your database and is the
only thing that can decrypt that tag's locations.

There is **no escrow and no recovery**. Lose the database and every board already
deployed becomes permanently unlocatable — it will keep advertising and you will
never read another position from it.

This is why uninstalling leaves `%LOCALAPPDATA%\NetWatch\` in place. Use
**Export > JSON** for backups, and treat that file as key material, because it is.

## The ATAK bridge

Off until switched on. A tracking app should not open a port on someone's network
just in case.

**Never sent, in any response:** `advertisementKey`, `hashedAdvKey`,
`privateKey` — no key material at all. A tag's hashed advertisement key is the
handle Apple's servers accept, so anyone who copies it can query that tag's
location themselves, forever, unrevocably. Tags travel as a per-install salted
digest instead: stable enough to move a map marker, useless anywhere else.

`desktop/app/tests/test_bridge.py` scans every response for the real keys, and
fails if even the field names come back.

**Transport is plain HTTP, gated by the pair code.** There's no hostname and no
CA that will issue a certificate for `192.168.1.42`; a self-signed cert would
mean shipping a trust anchor or walking every customer through installing one,
which is worse than being clear about what this is.

- **Appropriate:** a LAN you control, or a VPN (Tailscale, WireGuard, IPsec).
- **Not appropriate:** forwarded through a router to the internet. Anyone able to
  read the traffic gets the code and your tag locations.

The pair code is revocable — rotate it in the app and every paired client stops
working immediately. On Android it's stored in the plugin's private
`SharedPreferences`, the same place ATAK keeps its own server credentials.

## The loopback API

Binds `127.0.0.1` only, on an OS-assigned port, and every `/api/` call needs a
token regenerated each launch. Other local processes can't drive the app or read
tag keys.

## Unsigned binaries

Releases are **not code-signed**. SmartScreen warns, Smart App Control may block
them outright, and some AV engines look harder because the build clears the
Control Flow Guard PE bit — which it has to, because the Apple helper runs code
in a CPU emulator and CFG rejects JIT'd indirect calls.

If you distribute this, sign it. Until then, build from source if you'd rather
not trust a binary.

## No auto-update

There is no update channel. An unsigned one would be remote code execution on
every install, so there isn't one until it can be done with a signed manifest
verified before applying.

## Things worth knowing

- **Apple's terms.** Authenticating to Find My with a non-Apple client is very
  likely a breach of the iCloud terms. Realistic outcomes: the Apple ID gets
  locked, or Apple changes the endpoints and every install breaks at once.
- **Anti-stalking.** These tags sit outside Apple's unwanted-tracking alerts, so
  someone carrying one is unlikely to be warned. That's a deliberate consequence
  of how the protocol works, not a feature to market. Don't.
- **Map tiles.** The shipped sources are run by volunteers whose usage policies
  are written for modest traffic. At volume, self-host or pay a provider.
