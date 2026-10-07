"""Frozen-app entry point.

PyInstaller runs its entry script as top-level `__main__`, which breaks the
relative imports inside `netwatch/__main__.py` ("attempted relative import with
no known parent package"). Importing the package properly here keeps those
imports valid in both the frozen app and a normal `python -m netwatch` run.
"""

from __future__ import annotations

import os
import sys

if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _selftest() -> int:
    """`NetWatch.exe --selftest` - verify the Apple helper works inside the bundle.

    anisette emulates Apple's provisioning libraries with Unicorn, which is the
    most fragile thing in the package. A native crash here produces no Python
    traceback, so each step is printed and flushed before it runs: whatever is
    printed last is the step that died.
    """
    # The shipped exe is windowed, so stdout may go nowhere. Write each step to
    # a file and flush immediately: after a native crash the file still holds
    # everything up to the failing step.
    out_path = os.path.join(os.path.expanduser("~"), "netwatch-selftest.txt")
    out = open(out_path, "w", encoding="utf-8", buffering=1)

    def say(msg: str) -> None:
        print(msg, flush=True)
        out.write(msg + "\n")
        out.flush()
        os.fsync(out.fileno())

    say(f"writing to {out_path}")

    say(f"frozen       : {getattr(sys, 'frozen', False)}")
    say(f"_MEIPASS     : {getattr(sys, '_MEIPASS', '(none)')}")

    say("\n[1] import unicorn")
    import unicorn
    say(f"    unicorn {getattr(unicorn, '__version__', '?')} from {unicorn.__file__}")

    say("[2] construct a Unicorn engine")
    from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM
    uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    say("    engine OK")

    say("[3] map memory + run one instruction")
    uc.mem_map(0x1000, 0x1000)
    uc.mem_write(0x1000, b"\x1f\x20\x03\xd5")        # NOP
    uc.emu_start(0x1000, 0x1004)
    say("    emulation OK")

    say("[4] import anisette")
    from anisette import Anisette
    say("    imported")

    say("[5] Anisette.init()")
    ani = Anisette.init()
    say("    init OK")

    say("[6] provision()")
    ani.provision()
    say(f"    provisioned = {ani.is_provisioned}")

    say("[7] get_data()")
    headers = ani.get_data()
    say(f"    {len(headers)} headers")

    say("\nSELFTEST PASSED - Apple sign-in can work in this build.")
    return 0


from netwatch.__main__ import main  # noqa: E402

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        try:
            raise SystemExit(_selftest())
        except SystemExit:
            raise
        except BaseException:
            import traceback
            traceback.print_exc()
            raise SystemExit(1)
    raise SystemExit(main())
