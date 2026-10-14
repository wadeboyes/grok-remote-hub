# ADR 023: Archive list state on the hub, remove only linked worktrees

- **Status:** Accepted
- **Date:** 2026-10-14
- **Tags:** android, sessions, worktrees, archive

## Context

Wade wants to hide sessions and worktrees from the phone and tablet lists, and to delete a worktree folder from the PC. The two devices must agree. Session files under `~/.grok/sessions` are the conversation record and must stay.

## Decision

1. Archive state is `data/archive.json` on the hub. A session is keyed by session id. A worktree is keyed by its folder path. `GET /api/build/index` marks `archived` and still returns those items so the Archived tab can list them. Restore deletes the key. Archive and restore do not read or write session files.
2. `POST /api/build/worktree/remove` deletes one worktree with `git worktree remove` run in the parent repository. The default command has no `--force`. Uncommitted changes or unpushed commits return 409 until the client sends `force: true`, which adds a single `--force`. The git branch is never deleted.
3. The target must resolve to a directory at least two levels under `~/.grok/worktrees`. Symlinks that leave that tree, `..`, the worktrees root, a repo group folder, and the main checkout are refused before git runs. A removed path is recorded so a leftover `worktrees.db` row stays off the list. If that folder exists again later, the removed mark is dropped. The linked session id is archived. The session directory is not deleted.
4. The same routes stay behind `hub_token`. `/health` is still the only skip. `grok agent serve --always-approve` is unchanged.

## Consequences

- Archive syncs as soon as both devices refresh the index. Undo on one device calls restore.
- `git worktree remove --force` deletes uncommitted files in that worktree only. It does not delete the branch or the main checkout.
- A worktree removed outside this hub can still appear if `worktrees.db` lists a missing path. This hub hides a path only after its own remove.
