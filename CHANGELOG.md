# Changelog

All notable changes to **Grok Remote Hub** are documented here.  
Format inspired by [Keep a Changelog](https://keepachangelog.com/). Versions follow the hub `__version__` when bumped; otherwise entries are grouped by **release themes** with commit SHAs for GitHub.

This file is the **public narrative**. Session chat context is not required to understand history.

---

## [Unreleased]

### Fixed
- **Phone composer is a full-width row.** In the embedded chat the message box spans the screen, with attach on the left and Send on the right. The status line sits above it. The session title is not repeated under the app bar.
- **A dropped turn can be continued.** If the transcript still has a pending tool call, or a turn that never finished, the hub sends session/cancel before the next prompt. If Grok still produces no output, the chat offers **Restart this session**. That reloads the same session, or reattaches the Grok process when no other session is in a turn. The note explains what happened.
- **Busy Grok no longer dies on a keepalive ping.** The hub ACP socket does not use the client ping timeout (the hub probe covers an idle socket). A ping timeout while a prompt is in flight does not mark the agent zombie or end the turn. After reconnect the hub loads the same session so later output still streams. The chat says "Connection to Grok blipped, reconnected. Grok may still be working."
- **Embedded chat was a black pane.** Resume now keeps one websocket, and the embedded page always shows the transcript (or "No messages yet") plus a composer. The chat column is not clipped when the sidebar is hidden. App build 1.0.3 (versionCode 4). WebView console lines go to logcat as `GrokRemoteWeb`.
- **Resume opens the chat.** A worktree card resumes the latest session in that folder and starts a fresh session only when none exists. The embedded page loads its scripts with the same `hub_token` (page URL, cookie, or Bearer) and opens that session instead of staying on Connecting with no session selected.
- **Session titles and summaries** now read `chat_history.jsonl`, `prompt_history.jsonl`, and updates or events when those are the conversation. Untitled sessions with history get a short title from the first user request. Stored filler summaries are rebuilt, and a history change refreshes the list text. Headless summary sessions under `data/.grok-hub-summary` stay off the list.

### Added
- **Session alerts on the phone and tablet.** The hub exposes `GET /api/notify/poll` behind `hub_token` (long-poll of turn end and pending question). The app listens over Tailscale, shows a notification per session, and skips the chat that is open on that device. Tap opens that session. Settings has **Session alerts**, on by default. Text is the session title plus "Finished", "Stopped with an error", or "Needs your answer". App build 1.0.5 (versionCode 6).
- **Archive and remove.** Archive on a session or worktree card (and in the detail pane) hides it from All, Sessions, and Worktrees. Archived lists it with Restore. An undo snackbar brings it back. The hub stores that in `data/archive.json`, keyed by session id or worktree path, so the phone and the tablet stay in sync. Archive never changes files under `~/.grok/sessions`. Remove is for worktrees only. A confirm dialog names the folder and branch and says it can't be undone. The hub runs `git worktree remove` from the parent repo, with no `--force` unless the worktree has uncommitted changes or unpushed commits. That case needs a second **Remove anyway**. The branch is kept. Paths outside `~/.grok/worktrees`, and the main repo checkout, are refused. Remove also archives the session record. App build 1.0.4 (versionCode 5).
- **Android self-update.** The app checks the hub on launch and every 3 hours, verifies SHA-256 and the signing certificate, then installs with the package installer session API. `python tools/publish_update.py <apk> <channel>` writes `data/app-updates/<channel>/update.json`. This build is 1.0.1 (versionCode 2) so it can install over 1.0.0. See `android/README.md`.
- **Android sideload app** for the Galaxy S24 Ultra and the Galaxy Tab S10. Home lists every Grok Build session and worktree, newest first, with a stored plain English summary. One tap resumes with `grok.exe --cwd <dir> --resume <session-id>` (never grok.bat) and opens the hub chat. Wide screens use two panes. See `android/README.md`.
- **Build index API** (`GET /api/build/index`, `POST /api/build/summaries/refresh`, `POST /api/build/resume`). Summaries refresh on the PC when session files change, including a headless `grok -p` pass, and are stored so the list loads without waiting.

### Changed
- **Mobile pretty Hub UI** — self-hosted Inter + system-ui (no Google Fonts / no serif fallback), card home, Working resume cards, sticky primary Send, bottom-sheet modals with a grab handle, idle image/video chrome hidden. Critical inline CSS so Tailscale/Brave never flashes raw desktop HTML. Touch ≥44px kept. Desktop (≥900px) stays compact. Static cache-bust `?v=20260905q`.
- **Mobile-first Hub UI** — phone/tablet portrait uses ≥44px touch targets, full-width primary actions (New / Send / Stop / Approve / Submit / Start), stacked composer above the keyboard, and sheet-style plan / agent-question / new-session / site-preview modals. Desktop (≥900px) stays compact. Working (running/recent) sessions are listed on the empty home for one-tap resume. Static cache-bust `?v=20260905n`.

---

## [0.4.0] — 2026-07-19

ACP reliability, honest compact, and hub control plane. Remote hub is now a full control plane: chat-ready ACP health, CLI-aligned turn ownership, honest context compact, and session UX that survives refresh, restart, and heavy history.

### Changed
- **New session folder picker** — Projects | Browse segmented tabs; browse mode uses breadcrumbs, folder rows with open chevron, sticky **Use this folder** CTA, loading/empty states; Projects list prioritizes search, shows Recent (localStorage `grh.recentProjects.v1`), and clearer empty copy; entry choice remembers list vs browse for Back.
- **Skip redundant session/load when already warm** — hub tracks session ids successfully `session/new` or `session/load`'d in this process; repeat loads skip the agent RPC (cleared on ACP disconnect). Does not remove model prefill cost of large history on first real prompt.

### Added
- **Cold-session latency hints** — slim banner/composer hint “Loading session into agent…” while attach is in flight; one-time soft note on large context sessions that first reply may take longer (same as CLI), suggesting `/compact` or New. Send is never blocked.
- **ANSI SGR colors in terminal/tool detail** — live `terminal_out` and final tool detail render CSI SGR as colored spans (safe DOM); one-liners / activity strip use plain `strip_ansi`.
- **Session restore on refresh** — last selected chat id stored in `localStorage` (`grh.selectedSession.v1`); bootstrap re-opens that session when it still appears in `/api/sessions` (desktop + mobile).
- **Goal banner with persistent elapsed** — slim `#goal-banner` above the transcript for CLI `/goal` mode (`active` / `paused`); wall-clock elapsed survives turns and refresh via `grh.sessionGoals.v1` + live `update_goal` / slash lifecycle.
- **ACP structured trace** — ring buffer + daily `logs/acp-trace-YYYYMMDD.jsonl`; `GET /api/admin/acp-trace?n=100`; last 5 on `/health` as `acpTraceRecent` (connect/send/recv/probe/heal/compact/quality, no secrets or full prompts).
- **ACP WebSocket ping probe** — after 45s idle silence (no pending RPC), hub pings the agent WS; probe fail forces unhealthy so heal reconnects (no auto-KillAgent).
- **Files media Share/Save** — video preview (mp4/mov/webm/m4v), Web Share API from Files, higher raw serve limit (150 MB), optional `?download=1` Content-Disposition (file-first; ADR 006/010).
- **Binary upload + attach** — `POST /api/fs/upload` into session `uploads/`; composer paperclip and Files Upload; path prefill; image 40 MB / video 150 MB caps + MIME allowlist.
- **Hub plan viewer** — `GET /api/sessions/{id}/plan` reads `plan.md` + `plan_mode.json`; View plan modal.
- **Hub plan-mode handshake** — `POST /api/sessions/{id}/plan/action` writes `plan_mode.json` (approve / request_changes / quit) so Approve clears `awaiting_plan_approval` without stock TUI `a`-key / `exit_plan_mode` (ADR 012); Approve also auto-sends continue inject text. Inline plan strip + View plan only when plan is awaiting/Active (not leftover `plan.md`).
- **ACP quality** — `acpQuality` (`ok`/`stale`/`zombie`/`down`); chat-ready requires quality ok; zombie send-fail disconnect (ADR 013).
- **Restart agent from pill** — when hung/down, click status pill → confirm → `POST /api/admin/restart-agent` (KillAgent-style serve recycle; hub stays up) (ADR 014).
- **Turn telemetry / capacity** — `liveTurns` age/silence/ttfb; capacity banner while work runs.
- **Open-tool wait cues** — strip prefers running over quiet while tools open; local `waiting · Ns` heartbeat.
- **Live terminal_out** — hub terminal/* pump streams deltas to UI tool rows.
- **Tool-row site Preview** — when a tool summary/path ends in `.html`/`.htm`, a compact **Preview** control opens the existing in-hub site preview (file-first; ADR 010).
- **Sticky active user prompt** — scroll-linked You: pin; one-line collapsed by default; click expands; higher contrast sticky bar.

### Removed
- **Heavy-session soft context banner** — dropped `#context-budget-banner` / `contextBudget` status field (journal-size false positives after compact). Context UX matches the CLI usage bar only.

### Fixed
- **Hub `/compact` = ACP `_x.ai/compact_conversation`** — same agent serve path as CLI remote (not session/prompt); no-shrink while session still full is `failed` with signals-grounded “did not free context” message (not cheerful success).
- **Compact feedback grounded in session signals** — /compact and auto-compact toast/system text use signals.json used/window (never "already minimal" while the CTX bar is still full); hub resolves reduced vs still-full vs low and sends an authoritative `message`.
- **No-output heal vs load-replay suppress** — heal no longer force-releases load suppress in `finally` (that cancelled the quiet period); `wait_load_suppress_settled` lets history flush finish; loading always suppresses residual fanout even if an active turn is registered; `session_prompt` settles then releases before `_register_active_turn`.
- **Compact toast vs CTX bar honesty** — compact feedback reports compact-op X→Y only; CTX bar always comes from session signals (no applyUsagePatch from compact after, no flicker when signals stay high).
- **No-output recovery forces session reload** — do not skip `session/load` when already warm/loaded (dead worker produced zero updates); forget warm, real load, reconnect+load on fail. UI restores pending user prompt after history reload/refresh and keeps Resend pending on no-output failures.
- **session/load quiet-period suppress** — hold historical fanout until 1.5s silence (max 20s), rearm on each suppressed frame; fixed 0.3s release no longer lets multi-second history floods thrash the UI.
- **Honest compact feedback** — claim "Context compacted" only when after < before; no-op / missing tokens say no change; hub marks `reduced`/`feedback`; UI rate-limits compact system lines (~4s) and does not patch usage upward from no-op compact.
- **Sticky scroll rate limit** — cap non-forced `scrollIfSticky` to ~10/s (90ms min) so residual update bursts cannot thrash the transcript.
- **Hard reload once after hub process restart** — when `bootId` changes with the page still open, clear live state then `location.reload()` once per bootId (`sessionStorage` `grh.bootReload.*`) so clients never mix cached JS with new Python.
- **Stream merge overlap / double-print** — `mergeStreamText` absorbs redundant suffix and longest prefix/suffix overlap (mixed cumulative+delta); 50ms identical-chunk dedupe on append; pure matrix in `hub.ui_format.merge_stream_text`.
- **Wake mid-tool not cleared on silence alone** — `should_clear_turn_on_wake` / JS mirror keep healthy open tools/plan when `acpQuality` is ok; stale/zombie/down still clear (ADR-015).
- **Session restore-by-id** — bootstrap opens saved session via history when id is missing from top-N list; pin stores cwd+title; 404 clears pin.
- **Visibility/reconnect thrash** — debounced wake (visibility + pageshow + online); reconnect only if WS not OPEN; force history skips apply when fingerprint unchanged; fingerprint includes message count + last full length; scroll freeze without forcing stickToBottom.
- **Turn end always cancel-before-clear** — `AcpClient.end_turn` awaits agent cancel (bounded, prefers last-known method) then `force_clear_turn`; stall watchdog, reset-turn, no-output recovery, and stuck-before-prompt paths use it (no fire-and-forget cancel after unlock).
- **Disable auto-restart-agent after no-output prefill timeouts** — failed no-output retry no longer calls `_restart_agent_process` solely because process is up; fat-session slow first token must not KillAgent other projects (operator restart still available).
- **Redundant session/load on already-warm sessions** — attach/ensure no longer re-hits the agent for a session this process already loaded (avoids extra cold-path delay after the first load).
- **Reconnect/visibility force-refresh history** — after wake, reconnect, or turn idle, force-refresh selected history so the last assistant message appears even if the client still thought a turn was running.
- **Hub prompt-path timing + agent TTFB** — live process-reuse has no warmup waits; logs split hub ensure/send vs agent first update; TTFB excludes `user_message_chunk` / `available_commands_update`.
- **First-byte no-output stays 60s (CLI-like)** — do not scale zero-activity wait by `updates.jsonl` size (was 180/300s on heavy sessions); auto-retry budget 90s; soft surface after failed retry (no auto KillAgent).
- **Wake/reconnect turn re-sync (CLI-aligned, ADR 015)** — after visibility visible, `online`, or WS reconnect, re-fetch `/health` and clear dead turns (cancel + reset when `acpQuality` is stale/zombie/down or silence ≥120s); server is sole authority for idle; healthy turns re-seed timers.
- **Browse projects root missing / wrong default** — default `projects_root` prefers first existing among `~/Projects`, `D:/Projects`, `~/Documents/Projects`; missing root is created when possible; Browse error path shows status (not a blank panel) with config.toml hint on 404.
- **Multi-table messages** — every GFM table in a message body renders as HTML, not only the first.
- **Short GFM table separators** — agent-written tables with 1–2 dashes per separator cell (e.g. `|--|---|`) now render as tables instead of plain text.
- **Orphan agent turn after hub force-clear** — stall watchdog, admin reset-turn, and no-output recovery now call `session/cancel` (via `notify_agent_cancel`) so the agent releases the old prompt; UI unlock no longer leaves the next message blocked forever.
- **Heavy-session no-output false kill** — never suppress ACP activity mid-turn; release load-suppress before re-prompt. (First-byte wait no longer scales by journal size; no-output heal now forces reload — see above.)
- **Turn elapsed/silence timers seed from server age** — strip `running · Ns` and tool `waiting · Ns` survive hard-refresh/reconnect (no more client-only `Date.now()` reset to 0s).
- **session/load historical replay no longer streamed live** — drops agent history flood during load (stops UI tool strobe); history still via REST/WS.
- **Stale ACP heal no longer kills mid-prompt** — heal skips `stale` while a turn is active (stall watchdog owns silent prompts); `ACP_STALE_SECONDS` raised to 90s so quality does not flip before the 60s no-output policy.
- **Compact token sanity** — reject absurd/non-finite compact token counts (>5M or negative) hub-side and in UI so bogus “375k context” / scroll thrash does not paint.
- **`/compact` context bar + feedback** — hub intercepts `/compact` via `_x.ai/compact_conversation`, broadcasts `compact`/`usage` from `_x.ai/session_notification` `auto_compact_*`, and the UI updates the context bar with before→after (or no-op) feedback instead of waiting on the 6s poll alone.
- **Live stream text doubling** — `mergeStreamText` matches history cumulative-vs-delta merge so mid-turn assistant/thought text no longer looks duplicated.
- Tools stay **collapsed** by default; empty expand no longer shows “No detail.”
- Thinking summary no longer doubles the word “Thinking.”
- Composer placeholder adapts to width (short vs slash-hint) with CSS ellipsis.
- Status pill distinguishes agent process up vs ACP disconnected; auto-reconnect ACP when process is up (capped retries).
- Mobile transcript GFM tables re-parse from raw stream text and scroll horizontally.
- Mobile: hide session-banner; sticky You flush to top of chat scroll.

---

## [0.3.2] — 2026-07-12

Stream feel, session source labels, and restart UX polish on top of the 0.3.x hub.

### Added
- **CLI / Hub source pills** — session rail labels hub-owned vs stock CLI/TUI sessions (`isCli` on the session API; distinct pill colors).
- **Optimistic user bubble** — composer submit paints your message immediately; server echo is deduped.
- **One-tap Resend after hub process restart** — last prompt kept in sessionStorage; Resend on the error strip when a live turn was interrupted (client-first; no server store of prompt text).
- **Stream parity** — clearer Thinking panels, richer tool detail, tools open while running/pending; subagent spawn/finish as system lines in history and live stream.

### Fixed
- **No-output auto-retry** — on first silent turn, hub reloads the same session (reconnect ACP if needed) and resends once; only then surfaces a soft failure. No `session/new`, no map rewrite.
- Nested ACP content shapes for thought/tool text extraction (history + live UI).

### Docs
- README gallery screenshots refreshed from a live hub (sanitized demo titles/paths).

---

## [0.3.1] — 2026-07-12

### Added
- **In-hub site preview** (Files tree): double-click or **Preview** on `.html` / `.htm` opens a same-origin modal; **Close** stops the preview instance (`hub/site_preview.py`, `/api/preview/*`, `/preview-site/*`).
- **Public CHANGELOG** — release-oriented history mapped to commits and ADRs.

### Fixed
- Device presets (mobile / tablet / desktop) set a **real iframe viewport** so CSS media queries reflow; no longer only max-width crop. Same fix in CLI Preview Hub chrome.

---

## [0.3.0] — 2026-07-12

Hub version bump to **0.3.0**. Concurrent multi-project turns, restart continuity, Preview Hub CLI, public-ready packaging polish.

### Added
- **Multi-turn across projects** — default up to 3 concurrent live turns on different project folders; same-cwd prompts still queue (`max_concurrent_turns`, multi-turn policy).
- **Session continuity after hub restart** — view-first ensure; KeepAgent default on `restart-hub.ps1`; no-output recovery **keeps the same session** (no surprise `session/new` fork). ADR 009.
- **Browser Preview Hub (CLI)** — Node stdlib companion under `tools/preview-hub/` for static/SPA preview when the editor has no Simple Browser (`npm run preview`).
- **Ask-user ACP shapes** aligned with agent `outcome` discriminant; stop/cancel force-clears hub turn state.

### Commits (newest first)
| SHA | Summary |
|-----|---------|
| `a39c428` | Browser Preview Hub companion (Node stdlib) |
| `cd6c738` | Multi-turn, session continuity, ask-user, KeepAgent restart |

---

## [0.2.x] — Public readiness & session UX — 2026-07-11

First public-facing packaging: MIT, SECURITY, CONTRIBUTING, scrubbed paths, session filters, ask-user UX, tests.

### Added
- Working / Subagent / All session filters; pin; residual idle status; meta popover; mobile delete.
- Session UX helpers; soft-attach live prompt session; ask-user UI path.
- Unit tests + Python Playwright smoke; release readiness notes.
- Public README gallery (sanitized screenshots), logo, architecture SVG.

### Fixed / chore
- Portable defaults (`~/Projects`); remove personal Tailscale examples from scripts.
- Untrack session lab notes / superpowers from git; ignore probe dumps.

### Commits
| SHA | Summary |
|-----|---------|
| `a79282d` | README gallery with safe product screenshots |
| `45feadb` | Public README, SECURITY, CONTRIBUTING, community files |
| `be7fa9b` | Portable quick start and release readiness notes |
| `8afa7ff` | UX helper tests + Python Playwright smoke |
| `7964be1` | SPA session filters, residual strip, mobile delete, meta popover |
| `daecd46` | Session UX helpers, ask-user ACP, residual idle status |
| `48fef5b` | Portable defaults; scrub personal paths; restart-hub.ps1 |
| `9504b23` | MIT license |
| `efad04a` | Keep personal notes and superpowers out of git |
| `898b4c4` | Docs handoff — ADRs 007–008 |

---

## Usage bars, subagents, plan UI — 2026-07-11

### Added
- Weekly **plan usage** bar (billing credits via CLI auth).
- Compact dual usage bars (session context + plan) with token counts / popovers.
- Subagent session kind pills, filter, pin-to-top.
- Collapsible tool rows; auto-expand active plan tasks.
- Collapsible session rail; smart Browse sessions control.

### Fixed
- JWT from `auth.json` when refresh is revoked.
- Context bar hover used/total tokens; session vs monthly clarity.

### Commits
| SHA | Summary |
|-----|---------|
| `5b0a04a` | Subagent pills, kind filter, pin-to-top, larger weekly bar |
| `e0c3501` | auth.json JWT for weekly plan when refresh revoked |
| `1744b7d` | Weekly plan usage bar from billing credits |
| `30bb06e` | Compact dual usage bar with counts and popovers |
| `bc34ada` | Context bar hover used/total; session vs monthly |
| `e2d7a18` | Docs handoff — ADRs 005–006 |
| `39b64f5` | Collapse tool rows; auto-expand active plan tasks |
| `4f3ae83` | Collapsible session rail; smart Browse sessions |

---

## Core hub reliability (ACP, sessions, ops) — 2026-07-10–11

Foundation for production remote use: full ACP client surface, sole-writer sessions, WMI start, desktop follow, prompt queue.

### Added
- Full ACP client: permissions, fs, terminal (tool turns complete).
- Sole-writer / hub-owned sessions; attach-on-open; dual-hub topology (not TUI multi-client). ADRs 001–004.
- Detached hub start via WMI; stop-hub process tree; follow.ps1 disk mirror.
- Prompt queue while a turn is running; composer stays usable.
- Slash command cache/rebroadcast; context usage bar.

### Fixed
- Slash palette mobile positioning / scroll jump / name-first matching.
- Composer unlock during queued turns.

### Commits
| SHA | Summary |
|-----|---------|
| `8aa18d8` | Slash palette mobile scroll jump / flash |
| `65f7ce1` | Slash name-first matching; skill palette listing |
| `b6b153e` | ACP full client, sole-writer sessions, WMI start, follow |
| `72832c9` | Docs handoff — ADRs 001–004, session log |
| `fd5aa0e` | Keep composer unlocked during turns for prompt queue |
| `44a180c` | Queue prompts while agent turn is running |
| `3a916af` | Cache and rebroadcast agent slash commands |
| `991ce20` | Slash palette mobile fixed positioning |
| `b91430d` | Slash palette + context usage bar |

---

## Files, markdown, mobile shell — 2026-07-10

### Added
- File tree rail; `/api/fs` list / read / write; image lightbox.
- Markdown edit/preview; Mermaid in markdown preview.
- Project chip; mobile composer / viewport fixes.

### Fixed
- Transcript scroll bounce; user echo dedupe; autoGrow height; iPhone focus zoom.

### Commits
| SHA | Summary |
|-----|---------|
| `154da5d` | Image preview and lightbox for files tree |
| `6a8cd84` | Always show project name chip |
| `7682583` | Mobile composer / iPhone focus zoom |
| `764bac5` | Top-align composer; stop tall empty input |
| `b792a75` | Transcript scroll bounce during stream |
| `dc18b3c` | Dedupe user message echo |
| `101f863` | Mermaid in markdown file preview |
| `fc7460c` | Markdown Edit/Preview for .md files |
| `515a3ae` | Composer scrollHeight before autoGrow constrain |
| `04eed74` | File tree rail and mobile composer fixes |
| `0019b2a` | Expose `/api/fs` list read write endpoints |

---

## Earlier foundation

Earlier commits (pre-file-tree) establish the aiohttp hub, agent supervisor, session index/tailer, SPA shell, Tailscale dual-bind, and first smoke paths. Use `git log --oneline` for the full linear history before `0019b2a`.

---

## Architecture decision records

Product “why” lives in `docs/adr/` (not only in chat):

| ADR | Topic |
|-----|--------|
| 001 | Session lifecycle / sole-writer |
| 002 | Full ACP client surface |
| 003 | Dual-hub topology (not TUI multi-client) |
| 004 | Detached hub start (WMI) |
| 005–006 | Prompt queue; session cwd file browser |
| 007–008 | Subagent kind; title/rename |
| 009 | KeepAgent + continuity (view-first; no no-output fork) |
| 010 | File-first site preview (not cloud artifacts) |
| 011–014 | Agent vs ACP health; plan handshake; quality; in-hub restart |
| 015–016 | CLI-aligned turn ownership; load quiet-period suppress |

---

## Reading this on GitHub

1. **Releases** — tag `vX.Y.Z` when shipping; paste the matching section above into the release notes (latest: [v0.4.0](https://github.com/ChrisP-Builds/grok-remote-hub/releases/tag/v0.4.0)).
2. **Commits tab** — still the source of truth for diffs; this changelog is the human index.
3. **Do not rewrite published history** on `master` without a coordinated force-push plan; prefer forward commits + changelog.

### Not in git (by design)

Runtime and lab artifacts stay local: `config.toml`, `data/`, `logs/`, probe dumps, personal screenshots, `.playwright-mcp/`.
