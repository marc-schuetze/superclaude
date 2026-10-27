#!/usr/bin/env python3
"""verify.py [--mcp] [--host USER@HOST] [NAME ...] — read-only check of the agent kit
(sc verify). Prints FAIL/WARN lines and a summary; exit 1 on any FAIL.

  skills   every global/role/class skill link resolves to a SKILL.md whose name matches,
           ~/.agents/skills -> ~/.claude/skills, <role>/.agents/skills -> .claude/skills,
           no skill both global and per role, skill repos clean and pushed
  mcp      every mcp.json (roles, classes) and global harness config parses, stdio
           commands exist, no literal tokens; --mcp also sends `initialize` to each server
  memory   MEMORY.md within Claude's load limit, no orphan files, no dead index links
  parity   claude and codex get the same role kit: MCP names, memory, recall/sync hooks
  --host   runs `agent verify` on another machine over ssh
NAME limits the role/class checks to these roles or classes. Stdlib only.
"""
import json, os, re, select, shlex, shutil, subprocess, sys, time, tomllib, urllib.request
from pathlib import Path

HOME = Path.home()
PROFILES = Path(os.environ.get("SC_PROFILES", HOME / ".config/superclaude/profiles"))
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
CODEX = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
MEMORY_LIMIT = 25_000          # bytes of MEMORY.md Claude loads at session start (and 200 lines)
SECRET_KEY = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD)", re.I)
LITERAL_ASSIGN = re.compile(r"\b\w*(TOKEN|KEY|SECRET|PASSWORD)\w*=(?![\"']?\$)\S", re.I)

results = []


def report(level, area, msg):
    results.append((level, area, msg))


def profiles(names):
    roles = sorted(p for p in PROFILES.iterdir() if (p / "CLAUDE.md").exists() and p.name not in ("classes", "templates")) if PROFILES.is_dir() else []
    classes = sorted(p for p in (PROFILES / "classes").iterdir() if p.is_dir()) if (PROFILES / "classes").is_dir() else []
    if names:
        roles = [r for r in roles if r.name in names]
        classes = [c for c in classes if c.name in names]
    return roles, classes


def frontmatter_name(md):
    for line in md.read_text(errors="replace").splitlines()[1:20]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None


def check_skill_dir(d, label):
    names = set()
    if not d.is_dir():
        return names
    for e in sorted(d.iterdir()):
        if e.name.startswith("."):
            continue
        md = e / "SKILL.md"
        if not md.is_file():
            report("FAIL", "skills", f"{label}/{e.name}: no SKILL.md (link -> {os.readlink(e) if e.is_symlink() else 'dir'})")
            continue
        n = frontmatter_name(md)
        if n != e.name:
            report("WARN", "skills", f"{label}/{e.name}: frontmatter name is {n!r}")
        names.add(e.name)
    return names


def git_state(path):
    top = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip()
    if not top:
        return None, ""
    dirty = subprocess.run(["git", "-C", top, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    ahead = subprocess.run(["git", "-C", top, "rev-list", "--count", "@{u}..HEAD"], capture_output=True, text=True)
    state = []
    if dirty:
        state.append(f"{len(dirty.splitlines())} uncommitted")
    if ahead.returncode:
        state.append("no upstream")
    elif ahead.stdout.strip() != "0":
        state.append(f"{ahead.stdout.strip()} unpushed")
    return top, ", ".join(state)


def check_skills(roles, classes):
    glob = CLAUDE / "skills"
    gnames = check_skill_dir(glob, "~/.claude/skills")
    agents = HOME / ".agents/skills"
    if agents.exists() and agents.resolve() != glob.resolve():
        report("WARN", "skills", "~/.agents/skills does not point to ~/.claude/skills (codex/vibe see other skills)")
    extra = [e.name for e in (CODEX / "skills").iterdir() if not e.name.startswith(".")] if (CODEX / "skills").is_dir() else []
    for n in extra:
        report("WARN", "skills", f"~/.codex/skills/{n}: codex also reads ~/.agents/skills, possible duplicate")
    dirs = [glob]
    for p in roles + classes:
        sk = p / ".claude/skills"
        label = str(p.relative_to(PROFILES))
        names = check_skill_dir(sk, f"{label}/.claude/skills")
        for n in sorted(names & gnames):
            report("WARN", "skills", f"{label}: {n} is also global")
        if names and p in roles:
            a = p / ".agents/skills"
            if not a.exists() or a.resolve() != sk.resolve():
                report("FAIL", "parity", f"{label}: .agents/skills missing or not -> .claude/skills (codex lacks role skills)")
        dirs.append(sk)
    seen = set()
    for d in dirs:
        for e in (d.iterdir() if d.is_dir() else []):
            if not (e / "SKILL.md").is_file():
                continue
            top, state = git_state(e.resolve())
            if top and top not in seen:
                seen.add(top)
                if state:
                    report("WARN", "skills", f"repo {top}: {state}")


def stdio_target(server):
    cmd, args = server.get("command"), server.get("args") or []
    if cmd in ("bash", "sh") and len(args) >= 2 and args[0] == "-c":
        m = re.findall(r"\bexec\s+(\S+)", args[1])
        return m[-1] if m else None
    return cmd


def check_servers(servers, where):
    for name, s in servers.items():
        if not isinstance(s, dict):
            continue
        env = s.get("env") or s.get("environment") or {}
        for k, v in env.items():
            if SECRET_KEY.search(k) and isinstance(v, str) and v and not v.startswith("$"):
                report("FAIL", "mcp", f"{where}: {name}: literal secret in env {k}")
        joined = " ".join(map(str, s.get("args") or []))
        if LITERAL_ASSIGN.search(joined):
            report("FAIL", "mcp", f"{where}: {name}: literal secret in args")
        if s.get("url") or s.get("type") in ("http", "sse", "remote"):
            continue
        cmd = s.get("command")
        target = stdio_target(s) if not isinstance(cmd, list) else cmd[0]
        if target and not (shutil.which(target) or Path(os.path.expanduser(target)).exists()):
            report("FAIL", "mcp", f"{where}: {name}: command not found: {target}")


def load_mcp_json(f):
    try:
        return json.loads(f.read_text()).get("mcpServers", {})
    except ValueError as e:
        report("FAIL", "mcp", f"{f}: invalid JSON ({e})")
        return {}


def check_mcp(roles, classes):
    configs = {}
    for p in roles + classes:
        f = p / "mcp.json"
        if f.is_file():
            configs[str(p.relative_to(PROFILES))] = load_mcp_json(f)
            check_servers(configs[str(p.relative_to(PROFILES))], str(f))
    glob = {}
    cj = HOME / ".claude.json"
    if cj.is_file():
        d = json.loads(cj.read_text())
        check_servers(d.get("mcpServers", {}), "~/.claude.json")
        glob["claude"] = set(d.get("mcpServers", {}))
        for proj, v in (d.get("projects") or {}).items():
            check_servers((v or {}).get("mcpServers") or {}, f"~/.claude.json project {proj}")
    ct = CODEX / "config.toml"
    if ct.is_file():
        cs = tomllib.loads(ct.read_text()).get("mcp_servers", {})
        check_servers(cs, "~/.codex/config.toml")
        glob["codex"] = set(cs)
    vt = Path(os.environ.get("VIBE_HOME", HOME / ".vibe")) / "config.toml"
    if vt.is_file():
        vs = {s.get("name"): s for s in tomllib.loads(vt.read_text()).get("mcp_servers", [])}
        check_servers(vs, "~/.vibe/config.toml")
        glob["vibe"] = set(vs)
    oc = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "opencode/opencode.json"
    if oc.is_file():
        m = json.loads(oc.read_text()).get("mcp", {})
        check_servers(m.get("servers", m), "opencode.json")
        glob["opencode"] = set(m.get("servers", m))
    # outside `agent` every harness should see the same global servers (marc, 27.10.: claude = codex)
    if "claude" in glob and "codex" in glob and glob["claude"] != glob["codex"]:
        report("FAIL", "parity", f"global MCP: claude {sorted(glob['claude'])} != codex {sorted(glob['codex'])}")
    for h in ("vibe", "opencode"):
        if h in glob and "claude" in glob and glob[h] != glob["claude"]:
            report("WARN", "parity", f"global MCP: {h} {sorted(glob[h])} != claude {sorted(glob['claude'])}")
    return configs


def probe(server, timeout_stdio=45, timeout_http=20):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "sc-verify", "version": "1"}}}
    if server.get("url"):
        req = urllib.request.Request(server["url"], data=json.dumps(init).encode(), headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **(server.get("headers") or {})})
        body = urllib.request.urlopen(req, timeout=timeout_http).read(2000).decode("utf-8", "replace")
        return "result" in body
    p = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env={**os.environ, **(server.get("env") or {})})
    try:
        p.stdin.write(json.dumps(init) + "\n")
        p.stdin.flush()
        r, _, _ = select.select([p.stdout], [], [], timeout_stdio)
        return bool(r) and '"result"' in p.stdout.readline()
    finally:
        p.kill()


def check_mcp_live(configs):
    unique = {}
    for where, servers in configs.items():
        for name, s in servers.items():
            unique.setdefault(json.dumps(s, sort_keys=True), (name, s, []))[2].append(where)
    for name, s, where in unique.values():
        t = time.time()
        try:
            ok = probe(s)
        except Exception as e:  # report and go on: one dead server must not hide the rest
            ok, err = False, type(e).__name__
        else:
            err = "timeout or no result"
        dt = time.time() - t
        if ok:
            report("OK", "mcp-live", f"{name} {dt:.1f}s [{', '.join(where)}]")
        else:
            report("FAIL", "mcp-live", f"{name} {dt:.1f}s {err} [{', '.join(where)}]")


def check_memory(roles):
    for r in roles:
        mem = r / "memory"
        idx = mem / "MEMORY.md"
        if not idx.is_file():
            continue
        text = idx.read_text(errors="replace")
        size, lines = len(text.encode()), text.count("\n") + 1
        if size > MEMORY_LIMIT or lines > 200:
            report("FAIL", "memory", f"{r.name}: MEMORY.md {size} bytes / {lines} lines, Claude loads only ~{MEMORY_LIMIT} bytes / 200 lines")
        elif size > MEMORY_LIMIT * 0.9:
            report("WARN", "memory", f"{r.name}: MEMORY.md {size} bytes, close to the ~{MEMORY_LIMIT} byte load limit")
        linked = set(re.findall(r"\]\(([^)]+\.md)\)", text))
        files = {p.name for p in mem.glob("*.md") if p.name != "MEMORY.md"}
        dead = sorted(l for l in linked if not (mem / l).is_file())
        orphans = sorted(files - {Path(l).name for l in linked})
        if dead:
            report("FAIL", "memory", f"{r.name}: {len(dead)} index links without file: {', '.join(dead[:5])}")
        if orphans:
            report("WARN", "memory", f"{r.name}: {len(orphans)} memory files not in MEMORY.md: {', '.join(orphans[:5])}{' …' if len(orphans) > 5 else ''}")
        nofm = sorted(f for f in files if not (mem / f).read_text(errors="replace").startswith("---"))
        if nofm:
            report("WARN", "memory", f"{r.name}: {len(nofm)} files without frontmatter: {', '.join(nofm[:5])}")


def check_parity(roles, configs, here):
    sys.path.insert(0, str(here))
    import codex_args
    for r in roles:
        label = r.name
        a = codex_args.main(str(r), str(r))
        ov = {a[i + 1].split("=", 1)[0]: a[i + 1].split("=", 1)[1] for i, x in enumerate(a) if x == "-c"}
        want = set(configs.get(label, {}))
        got = {k.split(".")[1] for k in ov if k.startswith("mcp_servers.") and not k.endswith(".enabled")}
        if want != got:
            report("FAIL", "parity", f"{label}: codex MCP {sorted(got)} != mcp.json {sorted(want)}")
        settings = r / ".claude/settings.json"
        hooks = json.loads(settings.read_text()).get("hooks", {}) if settings.is_file() else {}
        cmds = json.dumps(hooks)
        if (r / "memory").is_dir():
            if "agent _recall" not in cmds or "agent sync" not in cmds:
                report("FAIL", "parity", f"{label}: claude settings.json lacks agent _recall/agent sync hooks")
            if "hooks.UserPromptSubmit" not in ov or "hooks.SessionEnd" not in ov:
                report("FAIL", "parity", f"{label}: codex gets no recall/sync hooks")
        if (r / "memory/MEMORY.md").is_file() and "# Role memory" not in ov.get("developer_instructions", ""):
            report("FAIL", "parity", f"{label}: codex gets no role memory")


def main(argv):
    live = "--mcp" in argv
    host = argv[argv.index("--host") + 1] if "--host" in argv else None
    if host:
        rest = [a for i, a in enumerate(argv) if a != "--host" and (i == 0 or argv[i - 1] != "--host")]
        # ssh joins its arguments into one remote command line: quote once, as a whole
        remote = "bash -lc " + shlex.quote("agent verify " + " ".join(map(shlex.quote, rest)))
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, remote], text=True)
        return r.returncode
    names = [a for a in argv if not a.startswith("--")]
    roles, classes = profiles(names)
    check_skills(roles, classes)
    configs = check_mcp(roles, classes)
    if live:
        check_mcp_live(configs)
    check_memory(roles)
    check_parity(roles, configs, Path(__file__).resolve().parent)
    for level, area, msg in results:
        if level != "OK" or live:
            print(f"{level:4} {area:8} {msg}")
    fails = sum(l == "FAIL" for l, _, _ in results)
    warns = sum(l == "WARN" for l, _, _ in results)
    print(f"verify: {len(roles)} roles, {len(classes)} classes: {fails} FAIL, {warns} WARN")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
