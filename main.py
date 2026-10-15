"""JARVIS launcher.

    python main.py              start the JARVIS desktop assistant (v4)
    python main.py --selftest   diagnostics report
    python main.py --cli        text-only console
    python main.py --legacy     the previous v0.1 web/voice app (legacy_main.py)

The legacy app starts an unauthenticated web server on 0.0.0.0 and a public
tunnel that exposes screen streaming and remote control. It is kept only for
reference; prefer the v4 desktop app.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    if "--legacy" in sys.argv:
        sys.argv.remove("--legacy")
        print("WARNING: the legacy app exposes an unauthenticated remote-control server. Use with care.")
        import legacy_main

        legacy_main.main()
    else:
        from jarvis.__main__ import main

        sys.exit(main())
