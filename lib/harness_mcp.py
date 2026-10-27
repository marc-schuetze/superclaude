#!/usr/bin/env python3
"""harness_mcp opencode|vibe CONF_DIR — print the env value that gives opencode / vibe the
profile's mcp.json as their MCP servers, like claude's --strict-mcp-config: role servers on,
global servers of that harness that the role lacks switched off.

  opencode: OPENCODE_CONFIG_CONTENT (JSON, merged over opencode.json; also carries
            system.md as instructions)
  vibe:     VIBE_MCP_SERVERS (JSON list, merged by name over config.toml)
Stdlib only."""
import json, os, sys, tomllib
from pathlib import Path


def role_servers(conf):
    f = Path(conf) / "mcp.json"
    return json.loads(f.read_text()).get("mcpServers", {}) if f.is_file() else {}


def opencode(conf):
    cfg = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "opencode" / "opencode.json"
    glob = json.loads(cfg.read_text()).get("mcp", {}) if cfg.is_file() else {}
    glob = glob.get("servers", glob)  # v2 nests servers; v1 is flat
    servers = {}
    for name, s in role_servers(conf).items():
        if s.get("type") in ("http", "sse") or "url" in s:
            servers[name] = {"type": "remote", "url": s["url"], **({"headers": s["headers"]} if s.get("headers") else {})}
        else:
            servers[name] = {"type": "local", "command": [s["command"], *s.get("args", [])],
                             **({"environment": s["env"]} if s.get("env") else {})}
    for name, s in glob.items():
        if name not in servers and isinstance(s, dict):
            servers[name] = {**s, "disabled": True}
    out = {"mcp": {"servers": servers}}
    if (Path(conf) / "system.md").is_file():
        out["instructions"] = [str(Path(conf) / "system.md")]
    return out


def vibe(conf):
    cfg = Path(os.environ.get("VIBE_HOME", Path.home() / ".vibe")) / "config.toml"
    glob = tomllib.loads(cfg.read_text()).get("mcp_servers", []) if cfg.is_file() else []
    servers = []
    for name, s in role_servers(conf).items():
        if s.get("type") in ("http", "sse") or "url" in s:
            servers.append({"name": name, "transport": "http" if s.get("type") == "sse" else "streamable-http",
                            "url": s["url"], **({"headers": s["headers"]} if s.get("headers") else {})})
        else:
            servers.append({"name": name, "transport": "stdio", "command": s["command"],
                            "args": s.get("args", []), **({"env": s["env"]} if s.get("env") else {})})
    names = {s["name"] for s in servers}
    servers += [{**s, "disabled": True} for s in glob if s.get("name") not in names]
    return servers


if __name__ == "__main__":
    harness, conf = sys.argv[1], sys.argv[2]
    print(json.dumps({"opencode": opencode, "vibe": vibe}[harness](conf), ensure_ascii=False))
