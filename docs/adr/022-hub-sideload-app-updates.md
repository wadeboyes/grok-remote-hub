# ADR 022: Serve sideload app updates from the hub

- **Status:** Accepted
- **Date:** 2026-10-14
- **Tags:** android, updates, hub_token

## Context

The Galaxy S24 Ultra and Galaxy Tab S10 install Grok Remote as a sideload APK. Wade wants a newer build to install itself, the way Haven TV reads an `update.json` every few hours, checks the file, and installs with no prompt. The phone already reaches the hub over Tailscale.

`wadeboyes/grok-remote-hub` is a public GitHub repo. A public GitHub Release would publish the APK to the internet. The hub token already guards every route except `/health`.

## Decision

1. The hub serves `GET /app/update.json` and `GET /app/GrokRemote-<version>.apk` from `data/app-updates/<channel>/`. The app uses the `stable` channel. Those routes are not exempt from `hub_token`. The download uses `Authorization: Bearer`, not the token in the query string.
2. `python tools/publish_update.py <apk> <channel>` copies the APK, writes `update.json` (versionCode, versionName, file name, SHA-256, time), and deletes older APKs in that channel. The hub reads the files on each request. Restart the hub only the first time the routes are new.
3. The app checks on launch and every 3 hours while it is open, plus a Settings button that shows the current and available versions. It refuses a download whose SHA-256 or signing certificate does not match. Install uses `PackageInstaller` with `USER_ACTION_NOT_REQUIRED` and `setRequestUpdateOwnership` so later updates can be silent once this app is the installer of record.
4. The first sideload (Drive, Files, or USB) can still require one confirm tap. Android can also show a prompt when silent install is not allowed. Docs say that plainly.

## Consequences

- APKs stay on the PC. They are not committed and they are not on a public release page.
- 1.0.0 has no updater. 1.0.1 (versionCode 2) is installed over it once. Self-update starts with the next published versionCode.
- A device that has not allowed this app to install unknown apps is sent to that Android setting before a session starts.
