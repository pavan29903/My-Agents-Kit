import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import context_watch  # noqa: E402
import guard_bash  # noqa: E402
import secret_guard  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class GuardBashTests(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "owner")
        git(self.repo, "config", "user.email", "owner@example.com")
        self.config = {"rules": [{"path_prefix": self.repo, "name": "owner", "email": "owner@example.com"}]}

    def decide(self, command, config=None):
        return guard_bash.decide(command, self.repo, self.config if config is None else config)

    def test_blocks_attribution_in_commit_message(self):
        cmd = 'git commit -m "fix: x\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>"'
        self.assertEqual(self.decide(cmd)[0], "deny")

    def test_blocks_attribution_in_heredoc_and_pr(self):
        self.assertEqual(self.decide("git commit -F - <<EOF\nfix\n\nGenerated with Claude Code\nEOF")[0], "deny")
        self.assertEqual(self.decide('gh pr create --body "x\n\U0001F916 Generated with [Claude Code](https://claude.com/claude-code)"')[0], "deny")

    def test_blocks_attribution_in_message_file(self):
        msg = Path(self.repo) / "msg.txt"
        msg.write_text("feat: y\n\nCo-authored-by: Claude <noreply@anthropic.com>\n", encoding="utf-8")
        self.assertEqual(self.decide("git commit -F msg.txt")[0], "deny")

    def test_clean_commit_allowed(self):
        self.assertIsNone(self.decide('git commit -m "feat: add parser"'))

    def test_wrong_identity_denied_with_fix(self):
        git(self.repo, "config", "user.email", "someone@else.com")
        decision, reason = self.decide('git commit -m "x"')
        self.assertEqual(decision, "deny")
        self.assertIn('git config user.email "owner@example.com"', reason)

    def test_identity_rule_by_remote(self):
        git(self.repo, "remote", "add", "origin", "https://github.com/owner/repo.git")
        git(self.repo, "config", "user.email", "someone@else.com")
        config = {"rules": [{"remote_pattern": r"github\.com[:/]owner/", "name": "owner", "email": "owner@example.com"}]}
        self.assertEqual(self.decide('git commit -m "x"', config)[0], "deny")

    def test_push_with_attributed_commit_denied(self):
        (Path(self.repo) / "a.txt").write_text("a", encoding="utf-8")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "feat: a\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        decision, reason = self.decide("git push origin main")
        self.assertEqual(decision, "deny")
        self.assertIn("attribution", reason)

    def test_push_with_wrong_author_denied(self):
        (Path(self.repo) / "a.txt").write_text("a", encoding="utf-8")
        git(self.repo, "add", "a.txt")
        git(self.repo, "-c", "user.email=other@x.com", "commit", "-q", "-m", "feat: a")
        self.assertEqual(self.decide("git push")[0], "deny")

    def test_clean_push_allowed(self):
        (Path(self.repo) / "a.txt").write_text("a", encoding="utf-8")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-q", "-m", "feat: a")
        self.assertIsNone(self.decide("git push -u origin main"))

    def test_dangerous_commands_ask(self):
        for cmd in ("git push --force origin main", "git push -f", "git reset --hard HEAD~3", "git clean -fdx",
                    "rm -rf /", "rm -rf ~", "rm -rf .", "Remove-Item -Recurse -Force C:\\",
                    "Format-Volume -DriveLetter D", "git checkout -- ."):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard_bash.decide(cmd, self.repo, {})[0], "ask")

    def test_safe_commands_pass(self):
        for cmd in ("git push --force-with-lease", "rm -rf node_modules", "rm -rf ./build",
                    "Remove-Item -Recurse -Force .\\dist", "git status", "git reset HEAD file.txt",
                    "npm run format"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(guard_bash.decide(cmd, self.repo, {}))

    def test_hook_entrypoint_fails_open_on_bad_input(self):
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard_bash.py")], input="not json",
                              capture_output=True, text=True, env=dict(os.environ, MY_AGENT_KIT_STATE=tempfile.mkdtemp()))
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_hook_entrypoint_emits_decision(self):
        payload = {"tool_name": "Bash", "cwd": self.repo, "tool_input": {"command": "git reset --hard"}}
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / "guard_bash.py")], input=json.dumps(payload),
                              capture_output=True, text=True)
        out = json.loads(proc.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")


class PrivateConfigTests(unittest.TestCase):
    def test_private_rules_are_added_not_replacing(self):
        import _common
        from unittest import mock
        kit_root, home = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        (kit_root / "config").mkdir()
        (kit_root / "config" / "identity.json").write_text(
            json.dumps({"block_ai_attribution": True, "rules": [{"name": "personal"}]}), encoding="utf-8")
        (home / ".my-agent-kit").mkdir()
        (home / ".my-agent-kit" / "identity.json").write_text(
            json.dumps({"rules": [{"name": "work"}], "block_ai_attribution": False}), encoding="utf-8")
        with mock.patch.object(_common, "KIT_ROOT", kit_root), mock.patch.object(Path, "home", return_value=home):
            cfg = _common.load_config("identity")
        self.assertEqual([r["name"] for r in cfg["rules"]], ["personal", "work"])
        self.assertFalse(cfg["block_ai_attribution"])


class SecretGuardTests(unittest.TestCase):
    def test_detects_real_looking_secrets(self):
        samples = ["AKIAABCDEFGHIJKLMNOP", "ghp_" + "a1B2" * 9, "sk-ant-api03-" + "x7" * 20,
                   "-----BEGIN RSA PRIVATE KEY-----", "postgres://app:s3cretPass@db.internal:5432/app"]
        for s in samples:
            with self.subTest(s=s[:12]):
                self.assertIsNotNone(secret_guard.find_secret(f"key = '{s}'"))

    def test_ignores_placeholders(self):
        for s in ("OPENAI_API_KEY=${OPENAI_API_KEY}", "postgres://user:<password>@host/db",
                  "token = 'ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'", "plain text"):
            with self.subTest(s=s):
                self.assertIsNone(secret_guard.find_secret(s))

    def test_handle_asks_for_write(self):
        out = secret_guard.handle({"tool_name": "Write", "tool_input": {
            "file_path": "app/config.py", "content": "KEY='AKIAABCDEFGHIJKLMNOP'"}})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIsNone(secret_guard.handle({"tool_name": "Write", "tool_input": {
            "file_path": ".env.example", "content": "KEY='AKIAABCDEFGHIJKLMNOP'"}}))


class ContextWatchTests(unittest.TestCase):
    def test_reads_latest_usage(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as fh:
            fh.write(json.dumps({"message": {"usage": {"input_tokens": 1}}}) + "\n")
            fh.write(json.dumps({"message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 250000,
                                                       "output_tokens": 5}}}) + "\n")
        self.assertEqual(context_watch.context_tokens(fh.name), 250015)
        self.assertIsNone(context_watch.context_tokens(fh.name + ".missing"))

    def test_warns_once_and_rearms(self):
        state_dir = Path(tempfile.mkdtemp())
        import _common
        old = _common.STATE_DIR
        _common.STATE_DIR = state_dir
        try:
            path = state_dir / "t.jsonl"

            def turn(tokens):
                path.write_text(json.dumps({"message": {"usage": {"input_tokens": tokens}}}) + "\n", encoding="utf-8")
                return context_watch.on_prompt({"session_id": "s1", "transcript_path": str(path)})

            self.assertIsNone(turn(1000))
            self.assertIn("Context is", turn(context_watch.WARN + 1)["systemMessage"])
            self.assertIsNone(turn(context_watch.WARN + 2))
            self.assertIn("Strongly", turn(context_watch.CRITICAL + 1)["systemMessage"])
            self.assertIsNone(turn(1000))  # compacted: re-arm
            self.assertIsNotNone(turn(context_watch.WARN + 1))
        finally:
            _common.STATE_DIR = old


if __name__ == "__main__":
    unittest.main()
