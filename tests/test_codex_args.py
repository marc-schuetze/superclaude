"""lib/codex_args.py: role memory, class skills and hooks for codex; lib/recall.py codex sessions."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

LIB = Path(__file__).resolve().parents[1] / "lib"


def args(conf, work, env):
    out = subprocess.run([str(LIB / "codex_args.py"), str(conf), str(work)], env=env,
                         capture_output=True, text=True, check=True).stdout
    return shlex.split(out)


def overrides(a):
    return {a[i + 1].split("=", 1)[0]: a[i + 1].split("=", 1)[1] for i, x in enumerate(a) if x == "-c"}


class CodexArgsTests(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(prefix="sc-cx-test-")
        self.addCleanup(t.cleanup)
        self.root = Path(t.name)
        self.env = {**os.environ, "CODEX_HOME": str(self.root / "codex")}
        self.role = self.root / "role"
        (self.role / "memory").mkdir(parents=True)
        (self.role / "memory/MEMORY.md").write_text("- [Fact](fact.md) — a fact\n")
        (self.role / "system.md").write_text("role rules\n")
        self.cls = self.root / "cls"
        skill = self.root / "skill-repo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("---\nname: demo\ndescription: >-\n  Folded demo\n  description.\n---\nbody\n")
        (self.cls / ".claude/skills").mkdir(parents=True)
        (self.cls / ".claude/skills/demo").symlink_to(skill)
        self.project = self.root / "project"
        self.project.mkdir()

    def test_role_gets_memory_and_hooks(self):
        a = args(self.role, self.role, self.env)
        o = overrides(a)
        dev = json.loads(o["developer_instructions"])
        self.assertIn("role rules", dev)
        self.assertIn("# Role memory", dev)
        self.assertIn("- [Fact](fact.md)", dev)
        self.assertIn("agent _recall", o["hooks.UserPromptSubmit"])
        self.assertIn("setsid -f agent sync", o["hooks.SessionEnd"])
        self.assertIn("--dangerously-bypass-hook-trust", a)

    def test_class_session_lists_class_skills_without_hooks(self):
        o = overrides(args(self.cls, self.project, self.env))
        dev = json.loads(o["developer_instructions"])
        self.assertIn("- demo: ", dev)
        self.assertIn("Folded demo description.", dev)
        self.assertNotIn("hooks.UserPromptSubmit", o)


class RecallCodexTests(unittest.TestCase):
    def test_codex_rollout_of_the_role_is_indexed(self):
        with tempfile.TemporaryDirectory(prefix="sc-rc-test-") as t:
            root = Path(t)
            role, other = root / "role", root / "other"
            (role / "memory").mkdir(parents=True)
            other.mkdir()
            day = root / "codex/sessions/2026/10/27"
            day.mkdir(parents=True)
            def rollout(name, cwd, text):
                lines = [{"type": "session_meta", "payload": {"cwd": str(cwd)}},
                         {"type": "response_item", "payload": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": text}]}}]
                (day / name).write_text("\n".join(json.dumps(x) for x in lines) + "\n")
            rollout("rollout-a.jsonl", role, "the zebrafinch deployment needs a restart today")
            rollout("rollout-b.jsonl", other, "the zebrafinch belongs to another directory entirely")
            env = {**os.environ, "CODEX_HOME": str(root / "codex"), "CLAUDE_CONFIG_DIR": str(root / "claude")}
            out = subprocess.run([str(LIB / "recall.py"), "query", str(role)], input='{"prompt": "zebrafinch"}',
                                 env=env, capture_output=True, text=True, check=True).stdout
            ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("rollout-a.jsonl", ctx)
            self.assertNotIn("rollout-b.jsonl", ctx)


if __name__ == "__main__":
    unittest.main()
