import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import kit  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class Sandbox(unittest.TestCase):
    """Points every kit path at a temporary directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.kit_root = self.tmp / "kit"
        (self.kit_root / "skills").mkdir(parents=True)
        (self.kit_root / "config").mkdir()
        (self.kit_root / "hooks").mkdir()
        (self.kit_root / "global").mkdir()
        (self.kit_root / "global" / "CLAUDE.md").write_text("# rules\n- be good\n", encoding="utf-8")
        (self.kit_root / "hooks" / "h.py").write_text("print()\n", encoding="utf-8")
        (self.kit_root / "config" / "hooks.json").write_text(json.dumps(
            {"events": {"PreToolUse": [{"matcher": "Bash", "script": "h.py", "timeout": 5}]}}), encoding="utf-8")
        self.saved = {k: getattr(kit, k) for k in ("KIT", "SKILLS", "CONFIG", "LOCK_PATH", "CACHE", "CLAUDE_HOME",
                                                   "KIT_HOME", "HOOK_MARKER")}
        kit.KIT = self.kit_root
        kit.SKILLS = self.kit_root / "skills"
        kit.CONFIG = self.kit_root / "config"
        kit.LOCK_PATH = kit.CONFIG / "upstream.lock.json"
        kit.CACHE = self.kit_root / ".upstream"
        kit.CLAUDE_HOME = self.tmp / "claude"
        kit.KIT_HOME = self.tmp / "kithome"
        kit.HOOK_MARKER = "kit/hooks/"

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(kit, k, v)

    def quiet(self, argv):
        with redirect_stdout(StringIO()) as out:
            rc = kit.main(argv)
        return rc, out.getvalue()

    def make_skill(self, name, body="Use when testing.\n"):
        d = kit.SKILLS / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Does a thing. Use when testing.\n---\n\n{body}",
                                    encoding="utf-8")
        return d


class InstallTests(Sandbox):
    def test_install_is_idempotent_and_preserves_user_settings(self):
        self.make_skill("alpha")
        kit.CLAUDE_HOME.mkdir()
        user_hook = {"matcher": "Write", "hooks": [{"type": "command", "command": "mine.sh"}]}
        (kit.CLAUDE_HOME / "settings.json").write_text(json.dumps(
            {"theme": "dark", "hooks": {"PreToolUse": [user_hook]}}), encoding="utf-8")
        (kit.CLAUDE_HOME / "CLAUDE.md").write_text("my own notes\n", encoding="utf-8")

        rc, _ = self.quiet(["install"])
        self.assertEqual(rc, 0)
        settings = json.loads((kit.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["theme"], "dark")
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
        self.assertIn(user_hook, settings["hooks"]["PreToolUse"])
        md = (kit.CLAUDE_HOME / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("my own notes"))
        self.assertIn("- be good", md)
        self.assertTrue((kit.CLAUDE_HOME / "skills" / "alpha" / "SKILL.md").is_file())

        self.assertEqual(self.quiet(["audit"])[0], 0)
        self.quiet(["install"])  # second run must not duplicate anything
        settings = json.loads((kit.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
        self.assertEqual((kit.CLAUDE_HOME / "CLAUDE.md").read_text(encoding="utf-8").count(kit.BLOCK_BEGIN), 1)

    def test_private_rules_appended_to_managed_block(self):
        kit.KIT_HOME.mkdir(parents=True)
        (kit.KIT_HOME / "CLAUDE.private.md").write_text("- work rule\n", encoding="utf-8")
        text = kit.claude_md_text("")
        self.assertLess(text.index("- be good"), text.index("- work rule"))
        self.assertLess(text.index("- work rule"), text.index(kit.BLOCK_END))

    def test_conflicting_skill_folder_is_backed_up(self):
        self.make_skill("alpha")
        existing = kit.CLAUDE_HOME / "skills" / "alpha"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("old", encoding="utf-8")
        self.quiet(["install", "--only", "skills"])
        self.assertTrue(kit.is_link(existing))
        backups = list((kit.KIT_HOME / "backups").rglob("SKILL.md"))
        self.assertEqual([b.read_text(encoding="utf-8") for b in backups], ["old"])

    def test_uninstall_removes_only_kit_items(self):
        self.make_skill("alpha")
        kit.CLAUDE_HOME.mkdir()
        (kit.CLAUDE_HOME / "CLAUDE.md").write_text("mine\n", encoding="utf-8")
        self.quiet(["install"])
        self.quiet(["uninstall"])
        self.assertFalse((kit.CLAUDE_HOME / "skills" / "alpha").exists())
        self.assertTrue((kit.SKILLS / "alpha" / "SKILL.md").is_file(), "uninstall must not delete kit sources")
        self.assertEqual((kit.CLAUDE_HOME / "CLAUDE.md").read_text(encoding="utf-8"), "mine\n")
        self.assertNotIn("hooks", json.loads((kit.CLAUDE_HOME / "settings.json").read_text(encoding="utf-8")))

    def test_dry_run_changes_nothing(self):
        self.make_skill("alpha")
        self.quiet(["install", "--dry-run"])
        self.assertFalse(kit.CLAUDE_HOME.exists())


class ValidateTests(Sandbox):
    def test_valid_skill_passes(self):
        self.make_skill("good")
        self.assertEqual(self.quiet(["validate"])[0], 0)

    def test_name_mismatch_and_missing_reference_fail(self):
        d = self.make_skill("bad", "Run scripts/missing.py\n")
        text = (d / "SKILL.md").read_text(encoding="utf-8").replace("name: bad", "name: other")
        (d / "SKILL.md").write_text(text, encoding="utf-8")
        rc, out = self.quiet(["validate"])
        self.assertEqual(rc, 1)
        self.assertIn("must match the folder name", out)
        self.assertIn("scripts/missing.py", out)

    def test_folded_description(self):
        meta, _ = kit.parse_frontmatter("---\nname: x\ndescription: >\n  line one\n  line two\n---\nbody")
        self.assertEqual(meta["description"], "line one line two")

    def test_new_skill_scaffold_validates(self):
        self.assertEqual(self.quiet(["new-skill", "my-flow", "-d", "Does X. Use when Y."])[0], 0)
        self.assertEqual(self.quiet(["validate", "my-flow"])[0], 0)
        self.assertEqual(self.quiet(["new-skill", "Bad_Name", "-d", "x"])[0], 2)


class UpstreamTests(Sandbox):
    def setUp(self):
        super().setUp()
        self.up = self.tmp / "upstream"
        (self.up / "skills" / "tool").mkdir(parents=True)
        git(self.up, "init", "-q", "-b", "main")
        git(self.up, "config", "user.email", "t@example.com")
        git(self.up, "config", "user.name", "t")
        self.write_up("one\ntwo\nthree\nfour\nfive\n")
        (kit.CONFIG / "upstreams.json").write_text(json.dumps({"sources": {"src": {
            "url": self.up.as_uri(), "branch": "main", "skills_dir": "skills"}}}), encoding="utf-8")

    def write_up(self, body, msg="c"):
        (self.up / "skills" / "tool" / "SKILL.md").write_text(
            "---\nname: tool\ndescription: T. Use when testing.\n---\n" + body, encoding="utf-8")
        git(self.up, "add", "-A")
        git(self.up, "commit", "-q", "-m", msg)

    def local(self):
        return (kit.SKILLS / "tool" / "SKILL.md").read_text(encoding="utf-8")

    def state(self):
        return kit.all_states(fetch_first=True)[0]["state"]

    def test_full_lifecycle(self):
        self.quiet(["upstream", "fetch"])
        self.assertEqual(self.quiet(["upstream", "add", "src", "tool"])[0], 0)
        self.assertEqual(self.state(), "current")

        # upstream-only change -> fast update
        self.write_up("one\ntwo\nthree\nfour\nFIVE\n")
        self.assertEqual(self.state(), "update-available")
        self.assertEqual(self.quiet(["upstream", "update", "tool"])[0], 0)
        self.assertIn("FIVE", self.local())
        self.assertEqual(self.state(), "current")

        # local-only change survives
        (kit.SKILLS / "tool" / "SKILL.md").write_text(self.local().replace("one", "ONE"), encoding="utf-8")
        self.assertEqual(self.state(), "customized")

        # both changed, different lines -> clean three-way merge
        self.write_up("one\ntwo\nthree\nFOUR\nFIVE\n")
        self.assertEqual(self.state(), "conflict")
        self.assertEqual(self.quiet(["upstream", "update", "tool"])[0], 0)
        self.assertIn("ONE", self.local())
        self.assertIn("FOUR", self.local())
        self.assertEqual(self.state(), "customized")

        # both changed the same line -> review copy, local untouched
        (kit.SKILLS / "tool" / "SKILL.md").write_text(self.local().replace("two", "mine"), encoding="utf-8")
        self.write_up("one\ntheirs\nthree\nFOUR\nFIVE\n")
        before = self.local()
        rc, out = self.quiet(["upstream", "update", "tool"])
        self.assertEqual(rc, 1)
        self.assertIn("CONFLICTS", out)
        self.assertEqual(self.local(), before)
        review = (kit.CACHE / "review" / "tool" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("<<<<<<<", review)

        # resolve and accept
        self.quiet(["upstream", "accept", "tool"])
        self.assertEqual(self.state(), "customized")

    def test_crlf_does_not_count_as_local_change(self):
        self.quiet(["upstream", "fetch"])
        self.quiet(["upstream", "add", "src", "tool"])
        path = kit.SKILLS / "tool" / "SKILL.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(self.state(), "current")

    def test_new_skills_listed(self):
        self.quiet(["upstream", "fetch"])
        rc, out = self.quiet(["upstream", "new"])
        self.assertIn("tool", out)


if __name__ == "__main__":
    unittest.main()
