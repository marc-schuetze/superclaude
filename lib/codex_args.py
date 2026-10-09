#!/usr/bin/env python3
"""codex_args CONF_DIR [WORK_DIR] — print codex CLI args (shell-quoted) that give codex the
same role/class kit claude gets: CLAUDE.md as project doc, system.md as
developer instructions, the role's mcp.json as MCP servers (global codex
servers outside the role switched off), dirs= as --add-dir. Stdlib only."""
import json, os, shlex, subprocess, sys, tomllib
from pathlib import Path

def toml_val(v):
    # JSON scalars/arrays of strings are valid TOML; objects become inline tables
    if isinstance(v, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {toml_val(x)}" for k, x in v.items()) + "}"
    if isinstance(v, list):
        return "[" + ", ".join(toml_val(x) for x in v) + "]"
    return json.dumps(v)

def trust(cfg, work):
    # codex asks "trust this folder?" per git root; pre-trust it for this run.
    # Whole `projects` table as one inline value: -c keys split on dots, and
    # paths like schlosserei-wahlich.de have dots. Existing entries are kept.
    if not work:
        return []
    r = subprocess.run(["git", "-C", work, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    root = r.stdout.strip() if r.returncode == 0 else work
    projects = tomllib.loads(cfg.read_text()).get("projects", {}) if cfg.is_file() else {}
    projects[root] = {**projects.get(root, {}), "trust_level": "trusted"}
    return ["-c", "projects=" + toml_val(projects)]

def main(conf, work=""):
    cfg = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "config.toml"
    args = ["-c", 'project_doc_fallback_filenames=["CLAUDE.md"]'] + trust(cfg, work)
    if not conf:
        return args
    conf = Path(conf)
    if (conf / "system.md").is_file():
        args += ["-c", "developer_instructions=" + json.dumps((conf / "system.md").read_text())]
    servers = {}
    if (conf / "mcp.json").is_file():
        servers = json.loads((conf / "mcp.json").read_text()).get("mcpServers", {})
    for name, s in servers.items():
        t = {"url": s["url"]} if s.get("type") in ("http", "sse") else \
            {k: s[k] for k in ("command", "args", "env") if k in s}
        args += ["-c", f"mcp_servers.{name}={toml_val(t)}"]
    if cfg.is_file():
        for name in tomllib.loads(cfg.read_text()).get("mcp_servers", {}):
            if name not in servers:
                # codex splits -c keys on dots and takes no quoting: bare names
                args += ["-c", f"mcp_servers.{name}.enabled=false"]
    if (conf / ".agent").is_file():
        for line in (conf / ".agent").read_text().splitlines():
            if line.startswith("dirs="):
                args += [a for d in line[5:].split(",") if os.path.isdir(d) for a in ("--add-dir", d)]
    return args

if __name__ == "__main__":
    pos = [x for x in sys.argv[1:] if x != "--check"]
    a = main(*(pos + ["", ""])[:2])
    if "--check" in sys.argv:  # self-check: every arg round-trips through the shell
        assert shlex.split(" ".join(map(shlex.quote, a))) == a
    print(" ".join(map(shlex.quote, a)))
