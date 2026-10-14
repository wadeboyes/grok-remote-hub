# Grok Remote (Android)

Sideload app for Wade's Galaxy S24 Ultra and Galaxy Tab S10. It talks to Grok Remote Hub on the PC over Tailscale.

- Phone (narrow): one column. Session cards with a summary and a Resume button.
- Tablet, or any window at least 600dp wide: session list on the left, summary or chat on the right.
- All orientations. `minSdk` 34, `targetSdk` 35 (Android 14 and 15). Both devices shipped on Android 14.
- Touch targets are at least 48dp, which is at least 44px.

Default hub URL: `http://100.113.227.9:8787`. Put the real `hub_token` in Settings on the device. The token is stored in app private preferences, not in the APK.

## Install on the phone or tablet

1. Copy `GrokRemote-v1.apk` onto the device (USB, Drive, or a nearby share). Use the same file on the phone and the tablet.
2. Open the APK. If Android blocks it, allow that app (Files, Drive, or Chrome) to install unknown apps.
3. Install. Later builds signed with the same keystore replace this install. You do not uninstall first.
4. Connect Tailscale on the device, same tailnet as the PC.
5. Open **Grok Remote**. Confirm the hub URL. Paste `hub_token` from the PC `config.toml`. Save.
6. The list loads stored summaries. Tap **Resume** (or **Restart** on a worktree with no session) to open the chat.
7. On the tablet, rotate to landscape. The list stays on the left and the summary or chat stays on the right.

The PC hub must be running this branch. See "Pull this build on the PC" in the repo README. Short form:

```powershell
cd C:\Users\WADEBOYES\grok-remote-hub
git remote add wadeboyes https://github.com/wadeboyes/grok-remote-hub.git
git fetch wadeboyes
git checkout -B cursor/android-grok-build-v1 wadeboyes/cursor/android-grok-build-v1
.\restart-hub.ps1
```

Skip `git remote add` if that remote already exists. The PC `origin` often still points at `wadeobyes/grok-remote-hub`, which is not this repo.

## Keystore

Release and debug builds are signed with a stable sideload key so updates install over the top.

| Item | Location |
|---|---|
| Keystore | `android/keystore/grok-remote-release.jks` (tracked) |
| Alias | `grokremote` |
| Passwords | Not in git. `android/keystore.properties` (gitignored) or env vars |

Create `android/keystore.properties` from `keystore.properties.example`:

```
storePassword=YOUR_PASSWORD
keyPassword=YOUR_PASSWORD
keyAlias=grokremote
```

Or set `GROK_REMOTE_STORE_PASSWORD`, `GROK_REMOTE_KEY_PASSWORD`, and optionally `GROK_REMOTE_KEY_ALIAS` in the environment. Those override the properties file.

Do not commit `keystore.properties`. Back up the `.jks` and the password together. A new keystore will not update an already installed APK.

## Rebuild

Needs JDK 17+, Android SDK 35, and build tools 35. On Windows, Android Studio is enough. Point the SDK at the project with `android/local.properties` (gitignored):

```
sdk.dir=C:\\Users\\WADEBOYES\\AppData\\Local\\Android\\Sdk
```

Or set `ANDROID_HOME`.

```powershell
cd C:\Users\WADEBOYES\grok-remote-hub\android
.\gradlew.bat assembleRelease
```

The APK is:

```
android\app\build\outputs\apk\release\app-release.apk
```

Linux or macOS: `./gradlew assembleRelease`.

The Gradle wrapper is `android/gradlew` and `android/gradlew.bat`. You do not need a system Gradle install.

## What resume does

The button calls `POST /api/build/resume` with the hub token. The hub builds:

```
C:\Users\WADEBOYES\.grok\bin\grok.exe --cwd <dir> --resume <session-id>
```

It does not launch a second interactive terminal. The hub agent (already started with `grok.exe`, not `grok.bat`) loads that folder and session, and the app opens the hub chat. Restart, when there is no session to resume, starts a new hub session in that folder with `session/new`.

Summaries are written on the PC to `data/build_summaries.json`. The app only reads them. A headless `grok.exe -p` refresh runs when a session changes. That path does not pass `--always-approve`.
