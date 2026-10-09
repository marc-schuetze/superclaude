#!/usr/bin/env bash
# sc.tmux — tmux integration for the sc session picker.
#
# Self-locates and registers a `sc-popup` command-alias that opens the sc
# fzf picker inside a tmux popup with the absolute path baked in, so it
# works regardless of the tmux server's $PATH.
#
# Manual install — add to ~/.tmux.conf:
#     run-shell /path/to/superclaude/sc.tmux
#
# TPM install — add to ~/.tmux.conf:
#     set -g @plugin 'marc-schuetze/superclaude'
#
# Then use `sc-popup` anywhere a tmux command is expected:
#     bind-key C-s sc-popup
#     # or inside display-menu:
#     bind-key -n MouseDown1StatusLeft display-menu \
#       'Sessions' s sc-popup ...
#
# Configuration (set in ~/.tmux.conf BEFORE the run-shell line):
#     set -g @sc-key 'C-s'         # also bind this key to sc-popup
#     set -g @sc-popup-width  90%  # popup width  (default 90%)
#     set -g @sc-popup-height 90%  # popup height (default 90%)

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SC_BIN="$CURRENT_DIR/bin/sc"

if [[ ! -x "$SC_BIN" ]]; then
    tmux display-message "sc.tmux: $SC_BIN not found or not executable"
    exit 1
fi

opt() {
    local v
    v="$(tmux show-option -gqv "$1")"
    [[ -n "$v" ]] && printf '%s' "$v" || printf '%s' "$2"
}

W="$(opt '@sc-popup-width'  '90%')"
H="$(opt '@sc-popup-height' '90%')"
KEY="$(opt '@sc-key' '')"

# SC_POPUP: the picker is already in a popup, so fzf must not open its own
tmux set-option -sa command-alias "sc-popup=display-popup -E -w $W -h $H -e SC_POPUP=1 \"$SC_BIN\""

if [[ -n "$KEY" ]]; then
    tmux bind-key "$KEY" sc-popup
fi
