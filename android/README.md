# Grok Remote (Android)

Sideload app for Wade's Galaxy S24 Ultra and Galaxy Tab S10. It talks to Grok Remote Hub on the PC over Tailscale.

- Phone (narrow): one column. Session cards with a summary and a Resume button.
- Tablet, or any window at least 600dp wide: session list on the left, summary or chat on the right.
- All orientations. `minSdk` 34, `targetSdk` 35 (Android 14 and 15). Both devices shipped on Android 14.
- Touch targets are at least 48dp, which is at least 44px.

Default hub URL: `http://100.113.227.9:8787`. Put the real `hub_token` in Settings on the device. The token is stored in app private preferences, not in the APK.

## Install on the phone or tablet

This build is **1.0.5** (versionCode 6). It installs over 1.0.4 because the versionCode is higher and the signing key is the same. 1.0.4 can also install this build from the hub after you publish it. The first time the app installs its own update, Android usually asks for one confirm.

1. Copy `GrokRemote-v1.0.5.apk` onto the device (USB, Drive, or a nearby share). Use the same file on the phone and the tablet. Or publish it on the hub and tap **Check for updates** in 1.0.4.
2. Open the APK. If Android blocks it, allow that app (Files, Drive, or Chrome) to install unknown apps.
3. Install. Later builds signed with the same keystore replace this install. You do not uninstall first.
4. Connect Tailscale on the device, same tailnet as the PC.
5. Open **Grok Remote**. Confirm the hub URL. Paste `hub_token` from the PC `config.toml`. Save.
6. The list loads stored summaries. Tap **Resume** to open the chat for that session. A worktree with an existing session resumes the latest one in that folder. A worktree with none starts a fresh session.
7. On the tablet, rotate to landscape. The list stays on the left and the summary or chat stays on the right.
8. In Settings, allow Grok Remote to install unknown apps if Android asks. **Check for updates** shows the current version and the version published on the hub.

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

It does not launch a second interactive terminal. The hub agent (already started with `grok.exe`, not `grok.bat`) loads that folder and session, and the app opens the hub chat. If the folder already has a session, that latest one is resumed, including when the worktree database has no linked id. `session/new` runs only when the folder has no session.

The chat page is `/?open=<session-id>&cwd=<folder>&embed=chat&token=<hub_token>`. The hub also sets the same token as a cookie. The WebView stores that cookie before load, fills the pane (it does not zoom the page out), and logs script errors to logcat under `GrokRemoteWeb`. The embedded view hides the sidebar and the "No session selected" text. An empty session shows "No messages yet" and the composer stays on screen. The page opens one websocket, not two.

Each card, and the detail pane, has **Archive**. That hides the session or worktree from All, Sessions, and Worktrees. **Archived** lists it with **Restore**. A snackbar offers **Undo**. The hub keeps this in `data/archive.json`, so the phone and the tablet show the same list. Archive does not change session files.

Worktree cards also have **Remove**. The dialog names the folder and branch and says it can't be undone. The hub deletes that folder with `git worktree remove` from the parent repo and keeps the branch. If there are uncommitted changes or unpushed commits, it refuses and the app asks for **Remove anyway**. That second step uses `--force`. The main checkout and anything outside `~/.grok/worktrees` are refused. Remove also archives the session.

Summaries are written on the PC to `data/build_summaries.json`. The hub reads `chat_history.jsonl` and `prompt_history.jsonl` (and updates or events when those hold the conversation), so a session with history gets a real title and the four summary lines. A headless `grok.exe -p` refresh can still refine that text. It does not pass `--always-approve`. The app only displays what the hub stored. Restart the hub after pulling this branch so archive, remove, and session alerts are available. Install 1.0.5 for the alerts.

## Session alerts

Settings has **Session alerts**, on by default. Android asks for notification permission (required on Android 13 and newer). While it is on, the app keeps a quiet "Watching sessions" notice and long-polls the hub over Tailscale. There is no FCM. A backup check runs about every 15 minutes if that watch is stopped.

A notification appears when any session finishes, stops with an error, or needs an answer (a question, or a plan still waiting for approval). The title is the session name. The line is "Finished", "Stopped with an error", or "Needs your answer". Each session has its own group. The chat you are looking at on that device does not notify. Tap the notification to open that session's chat.

Auto approve on the agent is unchanged. A permission the hub already answers does not notify.

## Ship an update

The phone and the tablet ask the hub for updates. They do not use GitHub Releases. This repo is public, so the APK stays on the PC under `data/app-updates/`, which is not committed. `GET /app/update.json` and `GET /app/GrokRemote-<version>.apk` use the same `hub_token` as the rest of the hub. `/health` is still the only route that skips the token.

The app reads the `stable` channel on launch and every 3 hours while it is open. Settings has **Check for updates**, with the current version and the available version.

1. Bump `versionCode` and `versionName` in `android/app/build.gradle.kts`. This build is `1.0.5` (`versionCode` 6). The next build must use a higher `versionCode` than the one installed on the device.
2. Build the release APK with the stable keystore:

```powershell
cd C:\Users\WADEBOYES\grok-remote-hub\android
.\gradlew.bat assembleRelease
```

3. Publish it. The channel the app reads is `stable`:

```powershell
cd C:\Users\WADEBOYES\grok-remote-hub
python tools\publish_update.py android\app\build\outputs\apk\release\app-release.apk stable
```

That writes `data\app-updates\stable\update.json` and copies the APK to `data\app-updates\stable\GrokRemote-<version>.apk`. The script reads `versionCode` and `versionName` from the APK and fills in the SHA-256.

4. Restart the hub the first time you pull this update code, so the `/app` routes exist:

```powershell
.\restart-hub.ps1
```

After that, publishing again does not need a restart. The hub reads `update.json` from disk on each check.

On the device the app downloads an APK only when its `versionCode` is higher. It checks the SHA-256 against the manifest, checks that the signing certificate matches the installed app, then installs with the package installer session API (`USER_ACTION_NOT_REQUIRED` and update ownership on Android 14+).

The first copy on the phone or tablet is still a sideload from Drive, Files, or USB. That sideload may need one confirm tap. The first time this app installs a newer build itself, Android usually shows one more confirm, because Drive or Files is still the installer. After that confirm, Grok Remote is the installer of record and the update owner, and later updates can install with no prompt. Android can still show a prompt if it does not allow a silent install. The device also needs "install unknown apps" allowed for Grok Remote.
