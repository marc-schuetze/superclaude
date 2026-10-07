# superclaude

Claude Code with tmux session management. One sticky session per project,
plus a touch-friendly picker that drops into a tmux popup so you can
switch sessions from a phone without chording `prefix` keys.

Three scripts:

- **`superclaude`** — full CLI: create / list / attach / kill named tmux sessions running `claude`. Session names are derived from `$PWD` so each project gets its own sticky session. `superclaude list all` opens an `fzf` picker over every session.
- **`sc`** — short, mobile-friendly wrapper around the same session pool. `sc` opens an `fzf` picker filling the screen, with "+ new session" pinned at the top. `sc n` creates new, `sc <N>` attaches by row index. Pairs with [`sc.tmux`](#tmux-integration) for one-tap session switching from inside tmux.
- **`scd`** — `sc`, but on the desktop node over `mosh`. Runs the remote `sc` on `desktop.marc.zkm.de` so you pick from *its* session pool, with mosh's roaming/reconnect for flaky links. Same arg surface as `sc` (`scd`, `scd n`, `scd <N>`). Client-side only — install it wherever you *initiate* from (laptop/phone), not on the desktop itself.

## Roles and agents

A **role** is a directory under `$SC_AGENTS_DIR` (default `/x/agents`, a git repo
synced between machines) with its own `CLAUDE.md`, `mcp.json` (loaded strictly, nothing else), `.claude/` and
`memory/`. `sc home` drops you into the home specialist with that role's
instructions, MCP servers and auto-memory, no further choice at start. The
role's auto-memory is symlinked from `~/.claude/projects/<encoded>/memory` into
`<role>/memory`, so it travels with the repo.

A role may carry a `.sc` file with `agent=codex|opencode|vibe` to pick its
harness; `sc -a codex …` overrides for one session. Claude is the default.

```
sc            picker: open sessions, then roles (a), then recent projects (p)
sc a          roles only
sc p          recent projects only (from ~/.claude/history.jsonl)
sc home       attach or create the role session
sc ~/foo      attach or create the session of a directory
sc -a codex n new codex session in $PWD
```

zsh completion (`sc <Tab>` lists roles, `-a <Tab>` lists harnesses):

```sh
ln -s "$PWD/superclaude/completions/_sc" ~/.zsh/completions/_sc   # dir must be in $FPATH
```

## Install

```sh
git clone git@github.com:marc-schuetze/superclaude.git
ln -s "$PWD/superclaude/bin/superclaude" ~/.local/bin/superclaude
ln -s "$PWD/superclaude/bin/sc"          ~/.local/bin/sc
ln -s "$PWD/superclaude/bin/scd"         ~/.local/bin/scd   # optional: remote-to-desktop
```

Requires `tmux`, `fzf`, and `claude` (Claude Code CLI) on `$PATH`.

## Usage

```
superclaude              attach existing session for $PWD, or create one
superclaude new          new session in $PWD (auto-numbered if one exists)
superclaude list         list sessions for $PWD
superclaude list all     fzf picker over ALL sc sessions
superclaude attach NAME  attach to a specific session
superclaude kill NAME    kill a specific session
superclaude role ROLE    attach or create the role's session (memory symlink ensured)
superclaude roles        list roles
superclaude open DIR     attach or create the session of a directory

sc                       fzf picker: sessions, roles, recent projects; "+ new" at top
sc n                     new session in $PWD
sc <N>                   attach to row N (most-recent first)
sc a | sc p              roles only | recent projects only
sc ROLE | sc DIR         attach or create the session of a role / directory
sc -a AGENT ...          harness: claude (default), codex, opencode, vibe

scd                      sc picker on the desktop node (over mosh)
scd n                    new session on the desktop
scd <N>                  attach the desktop's row N
```

The picker shows `idx ●/· age  title  ·  dir` per row — `●` = attached, `·` = idle.

## tmux integration

Open the `sc` picker as a tmux popup, with the absolute path resolved at
load-time so it works even if `sc` isn't on the tmux server's `$PATH`.

Add one line to `~/.tmux.conf`:

```tmux
run-shell '/path/to/superclaude/sc.tmux'
```

Or via [TPM](https://github.com/tmux-plugins/tpm):

```tmux
set -g @plugin 'scharc/superclaude'
```

This registers a `sc-popup` command-alias. Use it anywhere a tmux command
is expected:

```tmux
bind-key C-s sc-popup

# or from a display-menu:
bind-key -n F1 display-menu 'Sessions' s sc-popup ...
```

Optional config (set before the `run-shell` line):

```tmux
set -g @sc-key 'C-s'        # also auto-bind a key
set -g @sc-popup-width  90% # popup size (default 90%)
set -g @sc-popup-height 90%
```

## Notes

- Sessions are launched with `claude --dangerously-skip-permissions`. Edit `CLAUDE_CMD` in `bin/superclaude` if you want different defaults.
- Session-name slug: `sc|<absolute-path>`. Multiple sessions for the same path get suffixed `-2`, `-3`, ...
