"""Drive bin/superclaude against a private tmux server, a fake claude and a temp config."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SC = Path(__file__).resolve().parents[1] / "bin" / "superclaude"


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sc-engine-test-")
        self.addCleanup(self.temp.cleanup)
        root = self.root = Path(self.temp.name)
        self.sock = f"sc-test-{os.getpid()}"
        self.addCleanup(subprocess.run, ["tmux", "-L", self.sock, "kill-server"], capture_output=True)
        fake = root / "bin"
        fake.mkdir()
        (fake / "claude").write_text("#!/bin/sh\nexec sleep 300\n")
        (fake / "claude").chmod(0o755)
        prof = self.prof = root / "profiles"
        (prof / "home/memory").mkdir(parents=True)
        (prof / "home/CLAUDE.md").write_text("role\n")
        (prof / "home/mcp.json").write_text('{"mcpServers": {}}\n')
        self.code, self.tmp = root / "code", root / "scratch"
        for d in (self.code / "foo", self.code / "foo-2", self.code / "a.b", self.tmp / "t1"):
            d.mkdir(parents=True)
        for c, agent in (("work", "view=work\n"), ("temp", "view=temp\nnew=what?\nscratch=yes\n")):
            (prof / "classes" / c).mkdir(parents=True)
            (prof / "classes" / c / ".agent").write_text(agent)
        (prof / "classes/map").write_text(f"# comment\n{self.code}=work\n{self.tmp}=temp\n")
        conf = root / "config"
        conf.write_text(f'SC_PROFILES="{prof}"\nSC_TMUX_SOCKET=from-config\nSC_HARNESS=claude\nIGNORED=1\n')
        self.env = dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}", SC_CONFIG=str(conf),
                        SC_TMUX_SOCKET=self.sock, CLAUDE_CONFIG_DIR=str(root / "claude"))
        self.env.pop("TMUX", None)

    def sc(self, *args):
        return subprocess.run([str(SC), *args], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=30)

    def sessions(self):
        out = subprocess.run(["tmux", "-L", self.sock, "list-sessions", "-F",
                              "#{session_name}\t#{@sc_dir}\t#{@sc_key}"],
                             capture_output=True, text=True).stdout
        return sorted(tuple(l.split("\t")) for l in out.splitlines())

    def test_names_are_unique_and_identity_lives_in_options(self):
        self.sc(str(self.code / "foo-2"))
        self.sc("foo")
        self.sc("foo")   # base taken, -2 taken by the foo-2 project: gets -3
        self.sc(str(self.code / "a.b"))
        c = str(self.code)
        cs = c.replace(".", "_")
        self.assertEqual(self.sessions(), sorted([
            (f"sc|{cs}/foo-2", f"{c}/foo-2", "foo-2"),
            (f"sc|{cs}/foo", f"{c}/foo", "foo"),
            (f"sc|{cs}/foo-3", f"{c}/foo", "foo"),
            (f"sc|{cs}/a_b", f"{c}/a.b", "a.b"),
        ]))
        self.sc("kill", "foo")
        self.assertEqual([s[2] for s in self.sessions()], ["a.b", "foo-2"])

    def test_role_session_links_memory_and_refuses_a_foreign_link(self):
        self.sc("home")
        proj = self.root / "claude/projects" / str(self.prof / "home").replace("/", "-").replace(".", "-")
        self.assertEqual((proj / "memory").resolve(), (self.prof / "home/memory").resolve())
        (proj / "memory").unlink()
        (proj / "memory").symlink_to(self.root)
        r = self.sc("home")
        self.assertIn("points to", r.stderr)

    def test_existing_memory_is_moved_without_losing_a_clash(self):
        proj = self.root / "claude/projects" / str(self.prof / "home").replace("/", "-").replace(".", "-")
        (proj / "memory").mkdir(parents=True)
        (proj / "memory/a.md").write_text("from claude\n")
        (self.prof / "home/memory/a.md").write_text("from repo\n")
        self.sc("home")
        texts = sorted(p.read_text() for p in (self.prof / "home/memory").iterdir())
        self.assertEqual(texts, ["from claude\n", "from repo\n"])

    def test_views_from_classes(self):
        self.assertEqual(self.sc("_view", "next").stdout.count("reload("), 1)
        env = dict(self.env, FZF_PROMPT="roles > ")
        r = subprocess.run([str(SC), "_view", "next"], env=env, capture_output=True, text=True)
        self.assertIn("change-prompt(work > )", r.stdout)
        env["FZF_PROMPT"] = "roles > "
        r = subprocess.run([str(SC), "_view", "prev"], env=env, capture_output=True, text=True)
        self.assertIn("change-prompt(temp > )", r.stdout)
        temp = self.sc("_picklist", "temp").stdout.splitlines()
        self.assertIn("new task", temp[1])
        self.assertTrue(temp[1].endswith("\ttemp"))
        self.assertEqual([l.split("\t")[1] for l in temp[2:]], [str(self.tmp / "t1")])
        work = self.sc("_picklist", "work").stdout
        self.assertNotIn("new task", work)
        self.assertIn(str(self.code / "foo"), work)

    def test_environment_beats_config(self):
        self.sc(str(self.code / "foo"))
        self.assertEqual(len(self.sessions()), 1)
        r = subprocess.run(["tmux", "-L", "from-config", "has-session"], capture_output=True)
        self.assertNotEqual(r.returncode, 0)

    def test_unmapped_dir_and_empty_map_start_without_profile(self):
        (self.prof / "classes/map").write_text("# nothing mapped\n")
        free = self.root / "free"
        free.mkdir()
        r = subprocess.run([str(SC), "n"], cwd=free, env=self.env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=30)
        self.assertIn("started", r.stdout, r.stderr)
        self.assertEqual([s[1] for s in self.sessions()], [str(free)])


if __name__ == "__main__":
    unittest.main()
