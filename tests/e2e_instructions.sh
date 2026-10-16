#!/usr/bin/env bash
# e2e_instructions.sh [HARNESS...] — does every harness, started the way superclaude starts it,
# send the same instructions to the model? Runs each harness non-interactively against
# tests/capture.py (no real model, no tokens) and checks the captured request for:
#   global  first line of ~/.claude/CLAUDE.md (-> the global AGENTS.md)
#   role    first line of $SC_PROFILES/$ROLE/AGENTS.md         (role session)
#   class   first line of $SC_PROFILES/classes/$CLASS/system.md (class session, in this repo)
#   skills  every skill name under ~/.claude/skills, each description listed once
# ROLE/CLASS default to home/coding (SC_E2E_ROLE, SC_E2E_CLASS). SC_E2E_KEEP=1 keeps the
# captured requests. Exit 1 on any miss.
set -uo pipefail
HERE="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
P="${SC_PROFILES:-/x/agents}"; ROLE="${SC_E2E_ROLE:-home}"; CLASS="${SC_E2E_CLASS:-coding}"
TMP="$(mktemp -d)"; trap 'kill $(jobs -p) 2>/dev/null; [[ -n "${SC_E2E_KEEP:-}" ]] && echo "captures: $TMP" || rm -rf "$TMP"' EXIT
fn="$TMP/fn.sh"; sed -n '/^conf_get() {/,/^}/p;/^harness_cmd() {/,/^}/p' "$HERE/bin/superclaude" > "$fn"
first() { grep -m1 . "$1"; }

run() {  # $1 harness, $2 role|class, $3 port
    local h="$1" kind="$2" port="$3" dir conf cmd url="http://127.0.0.1:$3" out="$TMP/$1-$2"
    if [[ "$kind" == role ]]; then dir="$P/$ROLE"; conf="$P/$ROLE"; else dir="$HERE"; conf="$P/classes/$CLASS"; fi
    python3 "$HERE/tests/capture.py" "$port" "$out" >/dev/null 2>&1 & local cp=$!; sleep 1
    cmd="$(bash -c "die(){ echo \"\$*\" >&2; exit 1; }; HERE=$(printf %q "$HERE"); source $fn; harness_cmd $(printf %q "$dir") $(printf %q "$conf") $h" 2>/dev/null)"
    case "$h" in  # interactive -> one-shot, model -> capture.py
        claude)   cmd="ANTHROPIC_BASE_URL=$url ANTHROPIC_AUTH_TOKEN=x $cmd -p hi" ;;
        codex)    cmd="${cmd/codex /codex exec } -c model_provider=cap -c 'model_providers.cap={name=\"cap\",base_url=\"$url/v1\",wire_api=\"responses\"}' -c model=x hi" ;;
        opencode) printf '{"provider":{"cap":{"npm":"@ai-sdk/openai-compatible","options":{"baseURL":"%s/v1","apiKey":"x"},"models":{"x":{}}}}}' "$url" > "$TMP/oc-$kind.json"
                  cmd="OPENCODE_CONFIG=$TMP/oc-$kind.json ${cmd/opencode /opencode run } --model cap/x hi" ;;
        vibe)     cmd="VIBE_PROVIDERS='[{\"name\":\"cap\",\"api_base\":\"$url/v1\",\"api_key_env_var\":\"\",\"backend\":\"generic\"}]' VIBE_MODELS='[{\"name\":\"x\",\"provider\":\"cap\",\"alias\":\"x\"}]' VIBE_ACTIVE_MODEL=x $cmd -p hi" ;;
    esac
    (cd "$dir" && timeout 180 bash -c "$cmd" </dev/null >"$out.log" 2>&1)
    kill "$cp" 2>/dev/null
    python3 - "$out" "$h" "$kind" "$(first ~/.claude/CLAUDE.md)" "$(first "$P/$ROLE/AGENTS.md")" "$(first "$P/classes/$CLASS/system.md")" <<'EOF'
import glob, html, json, os, sys
out, h, kind, g, r, c = sys.argv[1:]
files = sorted(glob.glob(out + "/*.json"))
if not files:
    print(f"FAIL {h:8} {kind:5} no request reached the model, see {out}.log"); sys.exit(1)
def strings(x):
    if isinstance(x, str): return [x]
    vals = x.values() if isinstance(x, dict) else x if isinstance(x, list) else []
    return [s for v in vals for s in strings(v)]
text = html.unescape("\n".join(strings(json.load(open(files[0])))))  # vibe escapes skill XML
want = {"global": g, "role" if kind == "role" else "class": r if kind == "role" else c}
want |= {f"skill:{s}": s for s in os.listdir(os.path.expanduser("~/.claude/skills"))}
miss = [k for k, v in want.items() if v not in text]
def desc(s):  # first 60 chars of the skill description, the part a harness lists
    fm = open(os.path.expanduser(f"~/.claude/skills/{s}/SKILL.md")).read().split("---")[1]
    d = fm.split("description:", 1)[1].lstrip(" >-|\n").split("\n")[0].strip().strip('"')
    return d[:60]
dup = [s for s in os.listdir(os.path.expanduser("~/.claude/skills")) if text.count(desc(s)) > 1]
bad = miss + [f"duplicate:{s}" for s in dup]
print(f"{'FAIL' if bad else 'ok  '} {h:8} {kind:5} {len(want) - len(miss)}/{len(want)}" + (f" {bad}" if bad else ""))
sys.exit(1 if bad else 0)
EOF
}

(($#)) || set -- claude codex opencode vibe
rc=0; port=18800
for h in "$@"; do for kind in role class; do
    port=$((port + 1)); run $h $kind $port || rc=1
done; done
exit $rc
