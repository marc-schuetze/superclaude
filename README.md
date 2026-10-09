# superclaude

Claude Code in tmux sessions, plus the helpers around it: a touch-friendly
picker, profiles that give a session its own context, MCP servers and memory,
and a remote picker over mosh.

The repo is the generic base. Everything personal lives outside it, under
`~/.config/superclaude/`.

## Layers

1. **Session.** A harness (claude, codex, opencode, vibe) runs in a tmux session
   on one server (`tmux -L superclaude`). The session is named `sc|<dir>[-N]`;
   its identity is stored in session options (`@sc_dir`, `@sc_key`,
   `@sc_harness`), so names only have to be unique.
2. **Profile.** A directory with optional parts, applied when a session starts:

        CLAUDE.md               who it is (roles only; projects bring their own)
        mcp.json                its MCP servers; a profile gets exactly these, none without the file
        system.md               appended to the system prompt (rules, not context)
        .agent                  key=value: harness= dirs= view= new= scratch=yes
        .claude/settings.json   hooks: recall before each prompt, sync on exit, status line

   A **role** is a profile that is also the working directory: it has a home,
   `memory/` (Claude Code's auto-memory, symlinked in) and `notes/`.
   A **class** is a profile without a home, applied to a project repo; the
   session runs inside the repo with the repo's own CLAUDE.md and memory.
   `classes/map` maps project roots to classes.
3. **Helpers.** Picker with views, local memory index (`recall`), git sync of
   role memory, scratch dirs named by haiku, `scd` (picker on another host),
   `sc.tmux` (picker as tmux popup).

## Usage

`superclaude`, `sc`, `agent` and `ag` are the same command.

    sc                    picker: roles, running sessions, "+ here"; tab cycles the views
    sc n                  new session in $PWD (profile from the map, or none)
    sc 3                  attach the 3rd running session (newest first)
    sc home               new session for the role home
    sc home "check the backups"   same, with a first message
    sc myproject          session in a project found under a map root, class from the map
    sc web myproject      explicit class (needed when a name exists under two roots)
    sc web                picker over that class's projects
    sc temp               class with new=: asks for the task, creates a haiku-named dir
    sc ~/some/dir         any dir; class from the map if it lies under a root
    sc -h codex home      run codex instead of claude for this session
    sc ls                 roles, then running sessions (with Claude's topic)
    sc kill home          kill every session of a role or project (or one by name)
    sc init home          scaffold a role from templates/role
    sc recall home "pihole vip"   query the role's local memory
    sc sync               commit/pull/push memory + notes of all roles

    scd [args]            the same picker on SCD_HOST, over mosh

Picker keys: enter opens (a role or project starts a new session, a running
session attaches), ctrl-n starts another session next to the selected one,
ctrl-x starts a codex session, ctrl-d deletes a dir of a `scratch=yes` class
together with its Claude transcripts, tab/shift-tab switch views.

## Local setup: ~/.config/superclaude/

    config                       KEY=VALUE lines (data, not sourced; the environment wins)
    profiles/                    default $SC_PROFILES, often a git repo shared between machines
      <role>/                    a role
      classes/<class>/           a class
      classes/map                /path/to/root=class, one per line
      templates/role/            overrides the repo's templates/role for `sc init`

`config` keys:

| key | default | meaning |
|---|---|---|
| `SC_PROFILES` | `~/.config/superclaude/profiles` | roles, classes, map, templates |
| `SC_TMUX_SOCKET` | `superclaude` | the tmux server; `codeman` shares [Codeman](https://github.com/marc-schuetze/codeman-superclaude)'s |
| `SC_HARNESS` | `claude` | default harness |
| `SC_LEGACY_SOCKETS` | empty | other tmux servers whose `sc\|`/`ag\|` sessions the picker still lists |
| `SCD_HOST` | empty | `user@host` for `scd` |

Class keys in `classes/<class>/.agent`:

- `view=NAME`: the picker tab the class's projects appear in (default `projects`).
  Tabs follow the order of `classes/map`.
- `new=QUESTION`: `sc <class>` asks this, names a new dir under the class root
  and starts there; the view gets a "+ new task" row.
- `scratch=yes`: its dirs may be deleted with ctrl-d.

Example:

    # ~/.config/superclaude/config
    SC_PROFILES=~/agents
    SCD_HOST=me@desktop.example.org

    # ~/agents/classes/map
    /home/me/code/work=work
    /home/me/code/own=own
    /tmp/tasks=temp

    # ~/agents/classes/temp/.agent
    view=temp
    new=what are we doing?
    scratch=yes

## Memory

Two layers per role. `memory/` is Claude Code's own auto-memory (loaded every
session), kept in the profiles repo through a symlink
`~/.claude/projects/<encoded>/memory -> <role>/memory`. An existing memory dir
there is moved in first; a name present on both sides keeps both copies.
`lib/recall.py` adds a local SQLite FTS5 index over `memory/`, `notes/` and the
role's transcripts; as a `UserPromptSubmit` hook it injects up to three matching
snippets. The index (`.recall.sqlite`) is derived data, per machine, gitignored.

`sc sync` (also the `SessionEnd` hook) commits only `memory/` and `notes/`,
fetches, replays its own commits when both machines moved, and pushes. It never
publishes other local changes. A failure is written to
`.git/superclaude-sync-failed` and shown in the status line.

The hooks in role settings call `agent _recall`, `agent _status` and
`agent sync`; these names stay stable.

## tmux integration

    run-shell '/path/to/superclaude/sc.tmux'
    bind-key C-s sc-popup

or with TPM: `set -g @plugin 'marc-schuetze/superclaude'`. Options, set before
the `run-shell` line: `@sc-key`, `@sc-popup-width`, `@sc-popup-height` (90%).

## Install

    git clone https://github.com/marc-schuetze/superclaude.git
    export PATH="$PWD/superclaude/bin:$PATH"
    ln -s "$PWD/superclaude/completions/_superclaude" ~/.zsh/completions/_superclaude

Needs tmux, fzf >= 0.45, python3 (sqlite3 with FTS5), git, and the harness CLIs;
`scd` needs mosh. Claude is started with `--dangerously-skip-permissions` and
`CLAUDE_CODE_SANDBOXED=1` (no folder-trust dialog). MCP secrets belong in your
environment, never in the profiles repo.

Tests: `python3 -m unittest discover tests`.
