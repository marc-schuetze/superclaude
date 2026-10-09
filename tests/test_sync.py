"""Exercise automatic sync against disposable repositories and a local remote."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest


SYNC = Path(__file__).resolve().parents[1] / "lib" / "sync.sh"


class SyncScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "roles"
        self.remote = self.root / "remote.git"
        self.real_git = shutil.which("git")
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_ALLOW_PROTOCOL="file",
                        CLAUDE_CONFIG_DIR=str(self.root / "claude"),
                        SC_PROFILES=str(self.repo))
        self.git("init", "--bare", "--initial-branch=main", str(self.remote), cwd=self.root)
        self.git("clone", str(self.remote), str(self.repo), cwd=self.root)
        self.git("config", "user.name", "Sync Test")
        self.git("config", "user.email", "sync@example.invalid")
        self.write("home/CLAUDE.md", "role\n")
        self.write("home/memory/known.md", "old\n")
        self.write("home/notes/old.md", "remove me\n")
        self.write(".gitignore", ".recall.sqlite\n")
        self.git("add", ".")
        self.git("commit", "-m", "initial")
        self.git("push", "-u", "origin", "main")
        self.initial = self.git("rev-parse", "HEAD")

    def git(self, *args, cwd=None):
        return subprocess.run([self.real_git, *args], cwd=cwd or self.repo, env=self.env,
                              check=True, capture_output=True, text=True).stdout.strip()

    def write(self, path, contents):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)

    def sync(self):
        subprocess.run(["bash", str(SYNC)], env=self.env, check=True,
                       capture_output=True, text=True, timeout=30)

    def remote_head(self):
        return self.git("rev-parse", "refs/heads/main", cwd=self.remote)

    def test_normal_sync_add_modify_delete_and_leave_unstaged_role(self):
        self.write("home/memory/known.md", "new\n")
        self.write("home/notes/with space.md", "new note\n")
        (self.repo / "home/notes/old.md").unlink()
        self.write("home/CLAUDE.md", "local rule edit\n")
        self.sync()
        self.assertNotEqual(self.remote_head(), self.initial)
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))
        self.assertEqual(set(self.git("diff", "--name-only", self.initial, "HEAD").splitlines()),
                         {"home/memory/known.md", "home/notes/old.md", "home/notes/with space.md"})
        self.assertEqual(self.git("diff", "--name-only"), "home/CLAUDE.md")

    def test_mixed_staged_index_is_preserved(self):
        self.write("home/memory/known.md", "new\n")
        self.write("home/CLAUDE.md", "local rule edit\n")
        self.git("add", "home/CLAUDE.md", "home/memory/known.md")
        before = self.git("diff", "--cached", "--binary")
        self.sync()
        self.assertEqual(self.git("diff", "--cached", "--binary"), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.remote_head(), self.initial)

    def test_already_staged_memory_deletions_are_synced(self):
        self.git("rm", "home/memory/known.md", "home/notes/old.md")
        self.sync()
        self.assertNotEqual(self.remote_head(), self.initial)
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")

    def test_preexisting_unrelated_commit_is_not_published(self):
        self.write("home/CLAUDE.md", "unpublished rule edit\n")
        self.git("add", "home/CLAUDE.md")
        self.git("commit", "-m", "unrelated local commit")
        local_head = self.git("rev-parse", "HEAD")
        self.write("home/memory/known.md", "new\n")
        self.sync()
        self.assertEqual(self.git("rev-parse", "HEAD"), local_head)
        self.assertEqual(self.remote_head(), self.initial)
        self.assertEqual(self.git("diff", "--name-only"), "home/memory/known.md")

    def test_reverted_unrelated_commit_is_not_published(self):
        self.write("home/CLAUDE.md", "unpublished rule edit\n")
        self.git("add", "home/CLAUDE.md")
        self.git("commit", "-m", "unrelated local commit")
        self.git("revert", "--no-edit", "HEAD")
        self.assertEqual(self.git("diff", "--name-only", self.initial, "HEAD"), "")
        self.sync()
        self.assertEqual(self.remote_head(), self.initial)

    def test_rename_from_rules_into_memory_is_not_published(self):
        self.git("config", "diff.renames", "true")
        self.git("mv", "home/CLAUDE.md", "home/memory/rule.md")
        self.git("commit", "-m", "move unrelated rule")
        self.sync()
        self.assertEqual(self.remote_head(), self.initial)

    def test_no_upstream_skips_without_staging(self):
        self.git("branch", "--unset-upstream")
        self.write("home/memory/known.md", "new\n")
        self.sync()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.remote_head(), self.initial)

    def test_detached_head_skips_without_staging(self):
        self.git("checkout", "--detach")
        self.write("home/memory/known.md", "new\n")
        self.sync()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.remote_head(), self.initial)

    def test_branch_switch_during_fetch_does_not_publish_other_branch(self):
        self.git("checkout", "-b", "other")
        self.write("home/CLAUDE.md", "unrelated rule on other branch\n")
        self.git("add", "home/CLAUDE.md")
        self.git("commit", "-m", "other branch rule")
        self.git("push", "-u", "origin", "other")
        self.git("checkout", "main")
        wrapper_dir = self.root / "bin"
        wrapper_dir.mkdir()
        wrapper = wrapper_dir / "git"
        wrapper.write_text("#!/bin/sh\n"
                           f"if [ \"$1\" = fetch ]; then {shlex.quote(self.real_git)} "
                           "checkout -q other || exit 1; fi\n"
                           f"exec {shlex.quote(self.real_git)} \"$@\"\n")
        wrapper.chmod(0o755)
        self.env["PATH"] = str(wrapper_dir) + os.pathsep + self.env["PATH"]
        self.sync()
        self.assertEqual(self.git("branch", "--show-current"), "other")
        self.assertEqual(self.remote_head(), self.initial)

    def test_failed_push_retries_existing_memory_commit(self):
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.write("home/memory/known.md", "new\n")
        self.sync()
        pending = self.git("rev-parse", "HEAD")
        self.assertNotEqual(pending, self.initial)
        self.assertEqual(self.remote_head(), self.initial)
        hook.unlink()
        self.sync()
        self.assertEqual(self.remote_head(), pending)
        self.assertEqual(self.git("rev-parse", "HEAD"), pending)

    def test_push_does_not_publish_other_branches_or_tags(self):
        self.git("branch", "other")
        self.git("config", "push.default", "matching")
        self.git("config", "push.followTags", "true")
        self.write("home/memory/known.md", "new\n")
        self.git("add", "home/memory/known.md")
        self.git("commit", "-m", "memory update")
        self.git("tag", "-a", "local-only", "-m", "do not publish")
        self.sync()
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", cwd=self.remote),
                         "refs/heads/main")

    def other_clone_pushes(self, path, contents):
        other = self.root / "other"
        if not other.exists():
            self.git("clone", str(self.remote), str(other), cwd=self.root)
            self.git("config", "user.name", "Other", cwd=other)
            self.git("config", "user.email", "other@example.invalid", cwd=other)
        (other / path).parent.mkdir(parents=True, exist_ok=True)
        (other / path).write_text(contents)
        self.git("add", path, cwd=other)
        self.git("commit", "-m", "other machine", cwd=other)
        self.git("push", "origin", "main", cwd=other)

    def test_diverged_memory_commits_are_replayed_and_pushed(self):
        self.write("home/memory/known.md", "here\n")
        self.git("add", "home/memory/known.md")
        self.git("commit", "-m", "sync: here")
        self.other_clone_pushes("home/memory/there.md", "there\n")
        self.write("home/CLAUDE.md", "uncommitted rule edit\n")
        self.sync()
        self.assertEqual(self.remote_head(), self.git("rev-parse", "HEAD"))
        self.assertEqual((self.repo / "home/memory/there.md").read_text(), "there\n")
        self.assertEqual((self.repo / "home/memory/known.md").read_text(), "here\n")
        self.assertEqual(self.git("diff", "--name-only"), "home/CLAUDE.md")
        self.assertFalse((self.repo / ".git/superclaude-sync-failed").exists())

    def test_diverged_conflict_is_reported_and_keeps_local_commit(self):
        self.write("home/memory/known.md", "here\n")
        self.git("add", "home/memory/known.md")
        self.git("commit", "-m", "sync: here")
        local = self.git("rev-parse", "HEAD")
        self.other_clone_pushes("home/memory/known.md", "there\n")
        remote = self.remote_head()
        self.sync()
        self.assertEqual(self.git("rev-parse", "HEAD"), local)
        self.assertEqual(self.remote_head(), remote)
        self.assertIn("conflict", (self.repo / ".git/superclaude-sync-failed").read_text())
        self.assertEqual(self.git("worktree", "list").count("\n"), 0)
        self.assertEqual(self.git("status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
