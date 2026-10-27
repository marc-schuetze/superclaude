"""lib/harness_mcp.py: profile mcp.json -> opencode / vibe env config."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HM = Path(__file__).resolve().parents[1] / "lib" / "harness_mcp.py"


class HarnessMcpTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="sc-hm-test-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.conf = self.root / "role"
        self.conf.mkdir()
        (self.conf / "mcp.json").write_text(json.dumps({"mcpServers": {
            "local-a": {"command": "bash", "args": ["-c", "exec a"], "env": {"X": "1"}},
            "remote-b": {"type": "http", "url": "https://b.example/mcp"},
            "sse-c": {"type": "sse", "url": "http://c.example/sse"}}}))
        (self.conf / "system.md").write_text("sys\n")
        (self.root / "opencode").mkdir()
        (self.root / "opencode/opencode.json").write_text(json.dumps({"mcp": {
            "local-a": {"type": "local", "command": ["old"]},
            "global-only": {"type": "remote", "url": "https://g.example"}}}))
        (self.root / "vibe").mkdir()
        (self.root / "vibe/config.toml").write_text(
            '[[mcp_servers]]\nname = "global-only"\ntransport = "stdio"\ncommand = "g"\nargs = []\n')
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(self.root), "VIBE_HOME": str(self.root / "vibe")}

    def run_hm(self, harness, conf=None):
        out = subprocess.run([str(HM), harness, str(conf or self.conf)], env=self.env,
                             capture_output=True, text=True, check=True).stdout
        return json.loads(out)

    def test_opencode(self):
        out = self.run_hm("opencode")
        s = out["mcp"]["servers"]
        self.assertEqual(s["local-a"], {"type": "local", "command": ["bash", "-c", "exec a"], "environment": {"X": "1"}})
        self.assertEqual(s["remote-b"], {"type": "remote", "url": "https://b.example/mcp"})
        self.assertEqual(s["global-only"]["disabled"], True)
        self.assertEqual(out["instructions"], [str(self.conf / "system.md")])

    def test_vibe(self):
        s = {x["name"]: x for x in self.run_hm("vibe")}
        self.assertEqual(s["local-a"]["transport"], "stdio")
        self.assertEqual(s["local-a"]["args"], ["-c", "exec a"])
        self.assertEqual(s["remote-b"]["transport"], "streamable-http")
        self.assertEqual(s["sse-c"]["transport"], "http")
        self.assertIs(s["global-only"]["disabled"], True)
        self.assertNotIn("disabled", s["local-a"])

    def test_role_without_mcp_json_disables_all_globals(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertEqual([x["disabled"] for x in self.run_hm("vibe", empty)], [True])
        servers = self.run_hm("opencode", empty)["mcp"]["servers"]
        self.assertEqual(sorted(servers), ["global-only", "local-a"])
        self.assertTrue(all(s["disabled"] for s in servers.values()))


if __name__ == "__main__":
    unittest.main()
