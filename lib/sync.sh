#!/usr/bin/env bash
# sync.sh (sc sync) — reindex every role's local memory, commit memory/ and notes/,
# fetch, fast-forward or replay the sync commits onto the remote, push.
# Quiet, never fails the caller (SessionEnd hook); a failure leaves
# superclaude-sync-failed in the git dir with the reason, the status line shows it.
set -uo pipefail
ROLES="${SC_PROFILES:?SC_PROFILES not set}"
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
cd "$ROLES" || exit 0
# .git is a file in a linked worktree; the lock sits in the common dir so
# every worktree of the repo shares it
gitdir="$(git rev-parse --absolute-git-dir)" || exit 0
common="$(git rev-parse --path-format=absolute --git-common-dir)" || exit 0
flag="$gitdir/superclaude-sync-failed"
fail() { printf '%s\n' "$1" ${drift:+"$drift"} > "$flag"; echo "sc sync: $1" >&2; exit 0; }
# wait for a running sync instead of leaving this session's notes behind;
# bounded like the 20s fetch + 20s push of the holder
exec 9>"$common/agent-sync.lock"; flock -w 90 9 || fail "another sync held the lock > 90s, retry: sc sync"
# CLAUDE.md is a symlink to AGENTS.md; sed -i and rename-writes replace it
# with a copy. An unchanged copy is relinked; a diverged copy, a link to
# anything else and a missing CLAUDE.md are reported.
[[ -d .githooks ]] && ! git config --get core.hooksPath >/dev/null && git config core.hooksPath .githooks
drift=""
for a in */AGENTS.md; do
    [[ -f "$a" ]] || continue
    c="${a%AGENTS.md}CLAUDE.md"
    if [[ -L "$c" ]]; then [[ "$(readlink "$c")" == AGENTS.md ]] || drift+="$c "
    elif [[ ! -e "$c" ]]; then drift+="$c "
    elif cmp -s "$a" "$c"; then ln -sf AGENTS.md "$c"
    else drift+="$c "; fi
done
# written now so no later exit path drops it; fail() keeps it next to its own reason.
# Other failure lines stay, a stale drift line from an earlier run goes.
drift_note="merge edits into AGENTS.md and relink"
kept="$(grep -v "$drift_note\$" "$flag" 2>/dev/null)"
[[ -n "$drift" ]] && drift="${drift% } no symlink to AGENTS.md, $drift_note"
if [[ -n "$drift" ]]; then printf '%s\n' ${kept:+"$kept"} "$drift" > "$flag"
elif [[ -z "$kept" ]]; then rm -f "$flag"; fi
for r in */; do
    [[ -f "$r/CLAUDE.md" ]] && "$HERE/lib/recall.py" index "$r" >/dev/null 2>&1
done

# Only immediate role memory/notes directories belong to this automatic hook.
# A mixed index is left completely alone, including its already staged files.
only_sync_paths() {
    local path rest seen=0
    while IFS= read -r -d '' path; do
        rest="${path#*/}"
        [[ "$rest" != "$path" ]] || return 1
        case "$rest" in memory/*|notes/*) seen=1 ;; *) return 1 ;; esac
    done
    [[ "${1:-allow-empty}" == allow-empty || "$seen" == 1 ]]
}
git diff --cached --no-renames --name-only -z | only_sync_paths || exit 0

branch="$(git symbolic-ref --quiet --short HEAD)" || exit 0
remote="$(git config --get "branch.$branch.remote")" || exit 0
target="$(git config --get "branch.$branch.merge")" || exit 0
upstream_ref="$(git rev-parse --symbolic-full-name '@{upstream}')" || exit 0
upstream="$(git rev-parse --verify "$upstream_ref")" || exit 0
[[ "$target" == refs/heads/* ]] || exit 0
same_branch() {
    [[ "$(git symbolic-ref --quiet --short HEAD)" == "$branch" &&
       "$(git config --get "branch.$branch.remote")" == "$remote" &&
       "$(git config --get "branch.$branch.merge")" == "$target" &&
       "$(git rev-parse --symbolic-full-name '@{upstream}')" == "$upstream_ref" ]]
}

# Check each commit: a net diff could hide an unrelated change and its revert.
# Also reject merge/empty commits, whose intent this hook cannot establish.
only_sync_commits() {
    local commits commit parents
    local -a parent_fields
    commits="$(git rev-list "$1..$2")" || return 1
    for commit in $commits; do
        parents="$(git rev-list --parents -n 1 "$commit")" || return 1
        read -r -a parent_fields <<< "$parents"
        (( ${#parent_fields[@]} <= 2 )) || return 1
        git diff-tree --root --no-renames --no-commit-id --name-only -r -z "$commit" |
            only_sync_paths require-path || return 1
    done
}
head="$(git rev-parse "refs/heads/$branch")" || exit 0
only_sync_commits "$upstream" "$head" || fail "local commits outside memory/ and notes/: push them by hand"
same_branch || exit 0

scope=(':(glob)*/memory/**' ':(glob)*/notes/**')
# memory/ and notes/ hold text a role writes, not data: new binary or large
# files stay out and are reported, a bulk drop of new files stops the sync.
# Data belongs in /x/temp or a project dir. New = untracked or staged but not in HEAD.
mapfile -d '' -t new_paths < <(git ls-files -z --others --exclude-standard -- "${scope[@]}"
    git diff --cached --no-renames --diff-filter=A --name-only -z -- "${scope[@]}")
(( ${#new_paths[@]} <= 100 )) || fail "${#new_paths[@]} new files in memory/notes, move data out of the roles repo"
mapfile -d '' -t sync_paths < <(git ls-files -z --cached -- "${scope[@]}")
skipped=()
declare -A skip=()
for f in "${new_paths[@]}"; do
    if (( $(stat -c %s "$f") <= 524288 )) && { [[ ! -s "$f" ]] || grep -Iq . "$f"; }; then
        sync_paths+=("$f")
    else
        skipped+=("$f"); skip["$f"]=1
    fi
done
# enumerated names are literal: a deleted "notes/*" must not glob in a skipped file
if (( ${#sync_paths[@]} )); then
    git --literal-pathspecs add -A -- "${sync_paths[@]}" >/dev/null 2>&1 || exit 0
fi
commit_paths=()
while IFS= read -r -d '' f; do
    [[ -n "${skip["$f"]:-}" ]] || commit_paths+=("$f")
done < <(git diff --cached --no-renames --name-only -z -- "${scope[@]}")
if (( ${#commit_paths[@]} )); then
    # --only keeps unrelated files out even if another session stages them
    # after the initial check. Such changes remain in the shared index.
    same_branch || exit 0
    git --literal-pathspecs commit --only -qm "sync: $(hostname -s) $(date +%F_%H:%M)" -- \
        "${commit_paths[@]}" >/dev/null 2>&1 || fail "commit failed (pre-commit hook or index.lock?)"
fi
same_branch || exit 0
timeout 20 git fetch -q "$remote" "$target" >/dev/null 2>&1 || fail "fetch from $remote failed"
same_branch || exit 0
# the tracking ref, not FETCH_HEAD: a concurrent fetch may rewrite FETCH_HEAD
fetched="$(git rev-parse --verify "$upstream_ref")" || exit 0
head="$(git rev-parse "refs/heads/$branch")" || exit 0
if git merge-base --is-ancestor "$head" "$fetched"; then
    git merge -q --ff-only "$fetched" >/dev/null 2>&1 || fail "fast-forward failed (local changes in the way?)"
elif ! git merge-base --is-ancestor "$fetched" "$head"; then
    # Both sides moved: replay the local sync commits onto the remote in a
    # throwaway worktree, then move the branch with a compare-and-swap and
    # carry index/worktree along with read-tree -m -u, which refuses instead
    # of touching uncommitted files.
    base="$(git merge-base "$head" "$fetched")" || fail "no merge base with $remote"
    only_sync_commits "$base" "$head" || fail "local commits outside memory/ and notes/: push them by hand"
    tmp="$(mktemp -d)"; new=""
    git worktree add -q --detach "$tmp" "$fetched" >/dev/null 2>&1 || fail "worktree for replay failed"
    # ponytail: a commit the remote already has stops the replay as "empty" and is
    # reported as a conflict; --empty=drop once git >= 2.45 is everywhere
    if git -C "$tmp" cherry-pick "$base..$head" >/dev/null 2>&1; then
        new="$(git -C "$tmp" rev-parse HEAD)"
    else
        git -C "$tmp" cherry-pick --abort >/dev/null 2>&1
    fi
    git worktree remove --force "$tmp" >/dev/null 2>&1
    [[ -n "$new" ]] || fail "memory conflict with $remote: resolve by hand in $ROLES"
    same_branch || exit 0
    # update-ref with the old value fails if another session committed meanwhile
    git update-ref -m "sc sync: replay" "refs/heads/$branch" "$new" "$head" >/dev/null 2>&1 ||
        fail "branch moved during replay, retry: sc sync"
    if ! git read-tree -m -u "$head" "$new" >/dev/null 2>&1; then
        # only undo our own move; a commit made since then stays
        git update-ref -m "sc sync: replay undone" "refs/heads/$branch" "$head" "$new" >/dev/null 2>&1
        fail "replayed sync commits not applied (local changes in the way?)"
    fi
fi
same_branch || exit 0
upstream="$(git rev-parse --verify "$upstream_ref")" || exit 0
head="$(git rev-parse "refs/heads/$branch")" || exit 0
only_sync_commits "$upstream" "$head" || exit 0
same_branch || exit 0
# Explicit refspec and SHA avoid push.default, followTags, and any newer commit
# created by another session between the audit above and this push.
timeout 20 git -c push.followTags=false push -q --no-follow-tags \
    --recurse-submodules=no "$remote" "$head:$target" >/dev/null 2>&1 || fail "push to $remote failed"
(( ${#skipped[@]} )) && fail "not synced, binary or > 512K (${#skipped[@]}): ${skipped[0]}, move data out of the roles repo"
[[ -n "$drift" ]] && { echo "sc sync: $drift" >&2; exit 0; }
rm -f "$flag"
exit 0
