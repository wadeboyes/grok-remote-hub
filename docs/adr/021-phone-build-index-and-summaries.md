# ADR 021: Phone build index, stored summaries, hub resume

- **Status:** Accepted
- **Date:** 2026-10-14
- **Tags:** android, sessions, worktrees, summaries, resume

## Context

Wade wants Grok Build on a Galaxy S24 Ultra and a Galaxy Tab S10, talking to the hub at `http://100.113.227.9:8787`. The existing session rail is a Working list capped for the web UI. It does not list git worktrees under `~/.grok/worktrees`, and it does not keep a plain English "where did this get to" note that the phone can paint immediately.

## Decision

1. `GET /api/build/index` lists sessions from `~/.grok/sessions` and worktrees from `~/.grok/worktrees.db` plus untracked checkouts under `~/.grok/worktrees`. Newest first. Not limited to a grok-builds projects folder. The web Working list is unchanged.
2. Summaries live in `data/build_summaries.json`. The index reads that file. A background pass refreshes a summary when `summary.json` or `updates.jsonl` changes. The pass tries headless `grok.exe -p` (no `--always-approve`, never `grok.bat`) and falls back to a local note from the session files.
3. `POST /api/build/resume` resumes with the same arguments as `grok.exe --cwd <dir> --resume <session-id>`, through the existing hub agent, then returns a chat path. It does not start a second interactive TUI. Restart (no session on a worktree, or a failed resume that mints a new id) uses hub `session/new` in that folder.
4. The same routes stay behind `hub_token`. There is no guest mode.
5. The Android client is a sideload APK. Narrow width is one column. Width of 600dp or more is two panes: list on the left, summary or chat on the right. All orientations are allowed.

## Consequences

- The phone list can show CLI sessions and worktrees the Working filter hides.
- Headless summary runs can spend model time. They are one at a time, and a failed attempt waits before retrying the same fingerprint.
- Resume still follows the hub sole writer rule. A foreign CLI session may come back as a live hub session for that folder, with the original transcript opened in chat.
- Later updates must be signed with `android/keystore/grok-remote-release.jks`. The password is not stored in git.
