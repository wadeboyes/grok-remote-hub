#!/usr/bin/env python3
"""Publish a Grok Remote APK for in-app updates.

Usage:
    python tools/publish_update.py <apk> <channel>

Writes data/app-updates/<channel>/update.json and copies the APK beside it.
The hub serves those files. Restart the hub only the first time the /app
routes are new. Later publishes are read from disk.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.app_update import UpdateError, publish_apk


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: python tools/publish_update.py <apk> <channel>", file=sys.stderr)
        return 2
    apk = Path(argv[1])
    channel = argv[2]
    try:
        manifest = publish_apk(apk, channel)
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"published {manifest['apk']} versionCode={manifest['versionCode']} "
        f"channel={manifest['channel']}"
    )
    print(f"sha256 {manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
