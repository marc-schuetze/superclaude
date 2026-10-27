"""lib/verify.py against a temp HOME with deliberate defects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

VERIFY = Path(__file__).resolve().parents[1] / "lib" / "verify.py"
# minimal stdio MCP server: answers the first line with a JSON-RPC result
FAKE_MCP = 'import sys,json; sys.stdin.readline(); print(json.dumps({"jsonrpc":"2.0","id":1,"result":{}})); sys.stdout.flush()'


def skill(root, name, fm_name=None):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {fm_name or name}\ndescription: x\n---\n")
    return d


class VerifyTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="sc-verify-test-")
        self.addCleanup(t.cleanup)
        h = self.home = Path(t.name)
        self.prof = h / "agents"
        self.role = self.prof / "infra"
        (self.role / "memory").mkdir(parents=True)
        (self.role / "CLAUDE.md").write_text("role\n")
        (self.role / ".claude").mkdir()
        (self.role / ".claude/settings.json").write_text(json.dumps({"hooks": {
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "agent _recall"}]}],
            "SessionEnd": [{"hooks": [{"type": "command", "command": "agent sync"}]}]}}))
        (self.role / "memory/MEMORY.md").write_text("- [A](a.md) — a\n- [Gone](gone.md) — dead link\n")
        (self.role / "memory/a.md").write_text("---\nname: a\n---\nfact\n")
        (self.role / "memory/orphan.md").write_text("no frontmatter\n")
        (self.role / "mcp.json").write_text(json.dumps({"mcpServers": {
            "ok": {"command": sys.executable, "args": ["-c", FAKE_MCP]},
            "leaky": {"command": "bash", "args": ["-c", "export API_TOKEN=abc123; exec true"]},
            "missing": {"command": "no-such-binary-xyz"}}}))
        repos = h / "repos"
        skill(repos, "role-skill")
        skill(repos, "dup")
        (self.role / ".claude/skills").mkdir()
        (self.role / ".claude/skills/role-skill").symlink_to(repos / "role-skill")
        (self.role / ".claude/skills/dup").symlink_to(repos / "dup")
        (self.role / ".claude/skills/broken").symlink_to(h / "nowhere")
        (h / ".claude/skills").mkdir(parents=True)
        (h / ".claude/skills/dup").symlink_to(repos / "dup")
        skill(h / ".claude/skills", "misnamed", fm_name="other")
        self.env = {**os.environ, "HOME": str(h), "SC_PROFILES": str(self.prof), "CLAUDE_CONFIG_DIR": str(h / ".claude"),
                    "CODEX_HOME": str(h / ".codex"), "VIBE_HOME": str(h / ".vibe"), "XDG_CONFIG_HOME": str(h / ".config")}

    def run_verify(self, *args):
        r = subprocess.run([sys.executable, str(VERIFY), *args], env=self.env, capture_output=True, text=True, timeout=120)
        return r.returncode, r.stdout

    def test_defects_are_reported(self):
        code, out = self.run_verify()
        self.assertEqual(code, 1)
        for needle in ("infra/.claude/skills/broken: no SKILL.md", "misnamed: frontmatter name is 'other'",
                       "infra: dup is also global", ".agents/skills missing", "leaky: literal secret in args",
                       "missing: command not found: no-such-binary-xyz", "1 index links without file: gone.md",
                       "1 memory files not in MEMORY.md: orphan.md", "1 files without frontmatter: orphan.md"):
            self.assertIn(needle, out)
        self.assertNotIn("FAIL parity  infra: codex", out)   # codex_args gives memory + hooks + same MCP names

    def test_oversized_memory_index_fails(self):
        (self.role / "memory/MEMORY.md").write_text("- [A](a.md) — " + "x" * 26000 + "\n")
        self.assertIn("Claude loads only", self.run_verify()[1])

    def test_live_probe(self):
        out = self.run_verify("--mcp")[1]
        self.assertRegex(out, r"OK   mcp-live ok \d")
        self.assertIn("FAIL mcp-live missing", out)


if __name__ == "__main__":
    unittest.main()
