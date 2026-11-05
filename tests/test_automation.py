import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
sys.path.insert(0, str(ROOT / "scripts"))
import _common  # noqa: E402
import _project  # noqa: E402
import jobs  # noqa: E402
import post_edit  # noqa: E402
import verify_gate  # noqa: E402

PY = f'"{sys.executable}"'


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class StateSandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old_state = _common.STATE_DIR
        _common.STATE_DIR = self.tmp / "state"
        self.env = mock.patch.dict(os.environ, {"KIT_GATE": "1", "KIT_NOTIFY": "0"})
        self.env.start()

    def tearDown(self):
        _common.STATE_DIR = self._old_state
        self.env.stop()

    def project(self, name="proj", kit=None, files=None):
        root = self.tmp / name
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "pyproject.toml").write_text("[project]\nname='p'\n", encoding="utf-8")
        if kit is not None:
            (root / ".kit.json").write_text(json.dumps(kit), encoding="utf-8")
        for rel, text in (files or {}).items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text, encoding="utf-8")
        return root

    def edit(self, root, rel, text="x = 1\n", sid="s1"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return post_edit.handle({"tool_name": "Edit", "session_id": sid, "cwd": str(root),
                                 "tool_input": {"file_path": str(path)}})

    def stop(self, sid="s1"):
        return verify_gate.handle({"hook_event_name": "Stop", "session_id": sid})

    def counting_test(self, root, exit_code=0):
        """A test command that counts its runs in runs.txt and exits with exit_code."""
        script = root / "fake_test.py"
        script.write_text(
            "import sys, pathlib\np = pathlib.Path(__file__).with_name('runs.txt')\n"
            "p.write_text(str(int(p.read_text() or 0) + 1) if p.exists() else '1')\n"
            f"print('FAILED test_login - AssertionError' if {exit_code} else '3 passed')\nsys.exit({exit_code})\n",
            encoding="utf-8")
        return f'{PY} "{script}"'

    def runs(self, root):
        p = root / "runs.txt"
        return int(p.read_text()) if p.exists() else 0


class ProjectDetectionTests(StateSandbox):
    def test_project_root_is_nearest_marker(self):
        outer = self.project("outer")
        inner = outer / "packages" / "web"
        inner.mkdir(parents=True)
        (inner / "package.json").write_text("{}", encoding="utf-8")
        self.assertEqual(_project.project_root(str(inner / "src" / "a.ts")), inner)
        self.assertEqual(_project.project_root(str(outer / "src" / "a.py")), outer)

    def test_no_marker_means_no_project(self):
        loose = Path(tempfile.mkdtemp()) / "notes.txt"
        loose.write_text("x", encoding="utf-8")
        with mock.patch.object(_project, "ROOT_MARKERS", ("definitely-not-a-marker",)):
            self.assertIsNone(_project.project_root(str(loose)))

    def test_custom_npm_scripts_are_never_auto_run(self):
        for script in ("node test-webhook.js", 'echo "No test specified" && exit 1', "curl http://prod"):
            with self.subTest(script=script):
                root = self.tmp / f"js{abs(hash(script))}"
                (root / "node_modules").mkdir(parents=True)
                (root / "package.json").write_text(json.dumps({"scripts": {"test": script}}), encoding="utf-8")
                self.assertIsNone(_project.test_command(root, dict(_project.DEFAULTS)))

    def test_react_scripts_runs_once_not_watch(self):
        root = self.tmp / "cra"
        (root / "node_modules").mkdir(parents=True)
        (root / "package.json").write_text(json.dumps({"scripts": {"test": "react-scripts test"}}), encoding="utf-8")
        tc = _project.test_command(root, dict(_project.DEFAULTS))
        self.assertIn("--watchAll=false", tc["cmd"])
        self.assertEqual(tc["env"]["CI"], "true")

    def test_js_runner_needs_installed_deps_and_picks_manager(self):
        root = self.tmp / "vite"
        root.mkdir()
        (root / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
        self.assertIsNone(_project.test_command(root, dict(_project.DEFAULTS)), "no node_modules yet")
        (root / "node_modules").mkdir()
        (root / "pnpm-lock.yaml").write_text("", encoding="utf-8")
        self.assertEqual(_project.test_command(root, dict(_project.DEFAULTS))["cmd"][0], "pnpm")

    def test_python_tests_detected_with_fallback_to_unittest(self):
        root = self.project(files={"tests/test_a.py": "import unittest\n"})
        with mock.patch.object(_project, "pytest_available", return_value=True):
            self.assertIn("pytest", _project.test_command(root, dict(_project.DEFAULTS))["cmd"])
        with mock.patch.object(_project, "pytest_available", return_value=False):
            self.assertIn("unittest", _project.test_command(root, dict(_project.DEFAULTS))["cmd"])

    def test_python_without_tests_or_tests_only_in_venv(self):
        root = self.project(files={"venv/Lib/site-packages/x/test_x.py": "", "node_modules/y/test_y.py": ""})
        self.assertIsNone(_project.test_command(root, dict(_project.DEFAULTS)))

    def test_venv_python_preferred(self):
        root = self.project()
        exe = root / "venv" / ("Scripts/python.exe" if _project.IS_WIN else "bin/python")
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
        self.assertEqual(_project.python_for(root), [str(exe)])

    def test_kit_json_overrides_and_bad_json_ignored(self):
        root = self.project(kit={"test": "make check", "docs_min_files": 9})
        cfg = _project.settings(root)
        self.assertEqual(cfg["docs_min_files"], 9)
        self.assertEqual(_project.test_command(root, cfg)["cmd"], "make check")
        (root / ".kit.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(_project.settings(root)["docs_min_files"], _project.DEFAULTS["docs_min_files"])

    def test_formatter_only_when_project_uses_it(self):
        root = self.project()
        f = root / "src" / "a.py"
        f.write_text("x=1\n", encoding="utf-8")
        self.assertIsNone(_project.formatter_for(f, root), "no ruff/black config -> never reformat")
        (root / "ruff.toml").write_text("", encoding="utf-8")
        with mock.patch.object(_project, "_tool", return_value=["ruff"]):
            self.assertEqual(_project.formatter_for(f, root)[:2], ["ruff", "format"])
        with mock.patch.object(_project, "_tool", return_value=None):
            self.assertIsNone(_project.formatter_for(f, root), "configured but not installed")

    def test_prettier_requires_config_and_local_binary(self):
        root = self.tmp / "web"
        root.mkdir()
        (root / "package.json").write_text("{}", encoding="utf-8")
        f = root / "a.ts"
        f.write_text("", encoding="utf-8")
        (root / ".prettierrc").write_text("{}", encoding="utf-8")
        self.assertIsNone(_project.formatter_for(f, root))
        b = root / "node_modules" / ".bin" / ("prettier.cmd" if _project.IS_WIN else "prettier")
        b.parent.mkdir(parents=True)
        b.write_text("", encoding="utf-8")
        self.assertIn("--write", _project.formatter_for(f, root))


class PostEditTests(StateSandbox):
    def test_records_relative_paths_once(self):
        root = self.project()
        self.edit(root, "src/a.py")
        self.edit(root, "src/a.py")
        state = _common.load_state("s1")
        self.assertEqual(state["dirty"][str(root)], ["src/a.py"])

    def test_relative_input_path_resolved_against_cwd(self):
        root = self.project()
        (root / "src" / "b.py").write_text("", encoding="utf-8")
        post_edit.handle({"tool_name": "Write", "session_id": "s1", "cwd": str(root),
                          "tool_input": {"file_path": "src/b.py"}})
        self.assertIn("src/b.py", _common.load_state("s1")["dirty"][str(root)])

    def test_ignores_claude_dirs_other_tools_and_missing_path(self):
        root = self.project()
        self.edit(root, ".claude/settings.json", "{}")
        post_edit.handle({"tool_name": "Read", "session_id": "s1", "tool_input": {"file_path": str(root / "x.py")}})
        post_edit.handle({"tool_name": "Edit", "session_id": "s1", "tool_input": {}})
        self.assertEqual(_common.load_state("s1").get("dirty", {}), {})

    def test_formatter_failure_reported_not_raised(self):
        root = self.project()
        with mock.patch.object(post_edit, "formatter_for", return_value=[sys.executable, "-c", "import sys; sys.exit('bad syntax')"]):
            out = self.edit(root, "src/a.py")
        self.assertIn("could not format", out["hookSpecificOutput"]["additionalContext"])


class VerifyGateTests(StateSandbox):
    def test_nothing_edited_means_no_gate(self):
        self.assertIsNone(self.stop())

    def test_docs_only_edits_skip_tests(self):
        root = self.project()
        root_kit = {"test": self.counting_test(root, 1)}
        (root / ".kit.json").write_text(json.dumps(root_kit), encoding="utf-8")
        self.edit(root, "README.md", "# hi")
        self.assertIsNone(self.stop())
        self.assertEqual(self.runs(root), 0)
        self.assertEqual(_common.load_state("s1")["dirty"], {})

    def test_passing_tests_let_claude_stop_and_report(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"test": self.counting_test(root, 0)}), encoding="utf-8")
        self.edit(root, "src/a.py")
        out = self.stop()
        self.assertIn("tests passed", out["systemMessage"])
        self.assertEqual(_common.load_state("s1")["dirty"], {})
        self.assertIsNone(self.stop(), "clean state after a pass")

    def test_failing_tests_block_then_give_up_after_cap(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"test": self.counting_test(root, 1)}), encoding="utf-8")
        self.edit(root, "src/a.py")
        first = self.stop()
        self.assertEqual(first["decision"], "block")
        self.assertIn("FAILED test_login", first["reason"])
        self.edit(root, "src/a.py", "x = 2\n")  # Claude tries a fix
        self.assertEqual(self.stop()["decision"], "block")
        self.edit(root, "src/a.py", "x = 3\n")
        final = self.stop()
        self.assertNotIn("decision", final)
        self.assertIn("gave up", final["systemMessage"])
        self.assertEqual(self.runs(root), 3)
        self.assertIsNone(self.stop(), "state reset after giving up")

    def test_fix_after_block_passes_and_resets_counter(self):
        root = self.project()
        kit = root / ".kit.json"
        kit.write_text(json.dumps({"test": self.counting_test(root, 1)}), encoding="utf-8")
        self.edit(root, "src/a.py")
        self.assertEqual(self.stop()["decision"], "block")
        kit.write_text(json.dumps({"test": self.counting_test(root, 0)}), encoding="utf-8")
        self.edit(root, "src/a.py", "fixed = True\n")
        self.assertIn("passed", self.stop()["systemMessage"])
        self.assertEqual(_common.load_state("s1")["gate_blocks"], 0)

    def test_change_doc_required_for_bigger_changes_without_rerunning_tests(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"test": self.counting_test(root, 0)}), encoding="utf-8")
        for name in ("a", "b", "c"):
            self.edit(root, f"src/{name}.py")
        blocked = self.stop()
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("docs/changes", blocked["reason"])
        self.edit(root, "docs/changes/2026-11-05-login.md", "# Login\n")
        self.assertNotIn("decision", self.stop() or {})
        self.assertEqual(self.runs(root), 1, "tests are not rerun when code did not change")

    def test_readme_or_changelog_counts_as_doc(self):
        self.assertTrue(verify_gate.has_change_doc(["src/a.py", "CHANGELOG.md"]))
        self.assertTrue(verify_gate.has_change_doc(["docs\\changes\\x.md"]))
        self.assertFalse(verify_gate.has_change_doc(["src/notes.md"]))

    def test_small_change_needs_no_doc(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"test": self.counting_test(root, 0)}), encoding="utf-8")
        self.edit(root, "src/a.py")
        self.assertNotIn("decision", self.stop())

    def test_gate_disabled_per_project_and_globally(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"gate": False, "test": self.counting_test(root, 1)}), encoding="utf-8")
        self.edit(root, "src/a.py")
        self.assertIsNone(self.stop())
        self.assertEqual(self.runs(root), 0)
        other = self.project("other")
        (other / ".kit.json").write_text(json.dumps({"test": self.counting_test(other, 1)}), encoding="utf-8")
        self.edit(other, "src/a.py", sid="s2")
        with mock.patch.dict(os.environ, {"KIT_GATE": "0"}):
            self.assertIsNone(self.stop("s2"))

    def test_timeout_does_not_block(self):
        root = self.project(kit={"test": f'{PY} -c "import time; time.sleep(30)"', "test_timeout": 1})
        self.edit(root, "src/a.py")
        out = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("timed out", out["systemMessage"])

    def bash(self, command, sid="s1"):
        post_edit.handle({"tool_name": "Bash", "session_id": sid, "tool_input": {"command": command}})

    def test_no_test_command_requires_claude_to_run_tests(self):
        root = self.project()
        self.edit(root, "src/a.py")
        blocked = self.stop()
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("no tests were run", blocked["reason"])
        self.bash("ls -la")  # not a test command
        self.assertEqual(self.stop()["decision"], "block")
        self.bash("cd api && python -m pytest tests/test_login.py -q")
        out = self.stop()
        self.assertNotIn("decision", out)
        self.assertIn("Claude ran tests", out["systemMessage"])

    def test_tests_run_before_the_last_edit_do_not_count(self):
        root = self.project()
        self.bash("npm test")
        import time as _t
        _t.sleep(0.01)
        self.edit(root, "src/a.py")
        self.assertEqual(self.stop()["decision"], "block")

    def test_auto_tests_off_never_runs_the_project_command(self):
        root = self.project()
        (root / ".kit.json").write_text(json.dumps({"auto_tests": False, "test": self.counting_test(root, 0)}),
                                        encoding="utf-8")
        self.edit(root, "src/a.py")
        self.assertIn("auto_tests: false", self.stop()["reason"])
        self.assertEqual(self.runs(root), 0)
        self.bash("npx vitest run src/a.test.ts")
        self.assertNotIn("decision", self.stop())

    def test_test_command_patterns(self):
        for cmd in ("pytest -q", "python -m unittest discover", "npm run test", "yarn test", "npx jest a",
                    "go test ./...", "uv run pytest", "dotnet test"):
            with self.subTest(cmd=cmd):
                self.assertTrue(post_edit.TEST_COMMAND.search(cmd))
        for cmd in ("D:/work/api/venv/Scripts/python.exe test_location_integration.py",
                    "& $py -W ignore examples/test_delete_candidate.py", "node createUser.test.js",
                    'python "C:/scratch/sm tests/test_app_per_position.py"', "python api_test.py"):
            with self.subTest(cmd=cmd):
                self.assertTrue(post_edit.TEST_COMMAND.search(cmd))
        for cmd in ("git status", "npm install", "cat test_notes.md", "echo testing", "python build_reports.py",
                    "node server.js", "notepad test_x.py"):
            with self.subTest(cmd=cmd):
                self.assertFalse(post_edit.TEST_COMMAND.search(cmd))

    def test_private_prefix_override_disables_auto_tests(self):
        fake_home = self.tmp / "home"
        (fake_home / ".my-agent-kit").mkdir(parents=True)
        (fake_home / ".my-agent-kit" / "automation.json").write_text(
            json.dumps({"projects": {"D:/Work": {"auto_tests": False}}}), encoding="utf-8")
        with mock.patch.object(Path, "home", return_value=fake_home):
            self.assertFalse(_project.settings(Path("D:/work/api"))["auto_tests"], "prefix match ignores case")
            self.assertTrue(_project.settings(Path("D:/other/app"))["auto_tests"])

    def test_deleted_project_is_dropped(self):
        root = self.project()
        self.edit(root, "src/a.py")
        import shutil
        shutil.rmtree(root)
        self.assertIsNone(self.stop())
        self.assertEqual(_common.load_state("s1")["dirty"], {})

    def test_two_projects_one_failing(self):
        good = self.project("good")
        bad = self.project("bad")
        (good / ".kit.json").write_text(json.dumps({"test": self.counting_test(good, 0)}), encoding="utf-8")
        (bad / ".kit.json").write_text(json.dumps({"test": self.counting_test(bad, 1)}), encoding="utf-8")
        self.edit(good, "src/a.py")
        self.edit(bad, "src/a.py")
        out = self.stop()
        self.assertEqual(out["decision"], "block")
        self.assertIn("bad", out["reason"])
        self.assertNotIn("### good", out["reason"])

    def test_entrypoint_outputs_valid_json(self):
        env = dict(os.environ, MY_AGENT_KIT_STATE=str(self.tmp / "s"))
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / "verify_gate.py")],
                              input=json.dumps({"session_id": "zz"}), capture_output=True, text=True, env=env)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))


class SessionToolsTests(unittest.TestCase):
    def test_lists_parent_project_skills_and_graph(self):
        import session_start
        top = Path(tempfile.mkdtemp())
        (top / ".claude" / "skills" / "team-ship").mkdir(parents=True)
        (top / ".claude" / "skills" / "team-ship" / "SKILL.md").write_text("x", encoding="utf-8")
        (top / ".claude" / "skills" / "no-skill-md").mkdir()
        (top / "graphify-out").mkdir()
        (top / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
        repo = top / "api" / "app"
        repo.mkdir(parents=True)
        lines = session_start.tools_lines(str(repo))
        self.assertTrue(any("team-ship" in l and "no-skill-md" not in l for l in lines))
        self.assertTrue(any("graphify-out/graph.json" in l and "0 day(s)" in l for l in lines))

    def test_nothing_found_means_no_lines(self):
        import session_start
        self.assertEqual(session_start.tools_lines(tempfile.mkdtemp()), [])


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(jobs, "HOME", self.tmp / "home"),
                        mock.patch.object(jobs, "REPORTS", self.tmp / "home" / "reports"),
                        mock.patch.dict(os.environ, {"KIT_NOTIFY": "0"})]
        for p in self.patches:
            p.start()
        # remote with one commit, a clone that is behind, ahead, and dirty
        self.remote = self.tmp / "remote.git"
        git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.roots = self.tmp / "roots"
        self.seed = self.roots / "seed"
        git(self.tmp, "clone", "-q", str(self.remote), str(self.seed))
        for repo in (self.seed,):
            git(repo, "config", "user.email", "me@example.com")
            git(repo, "config", "user.name", "me")
        (self.seed / "a.txt").write_text("a", encoding="utf-8")
        git(self.seed, "add", "a.txt")
        git(self.seed, "commit", "-q", "-m", "first")
        git(self.seed, "push", "-q", "-u", "origin", "main")
        (self.roots / "not-a-repo").mkdir()
        (self.roots / "node_modules").mkdir()
        git(self.roots / "node_modules", "init", "-q")

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def cfg(self, **extra):
        base = {"roots": [str(self.roots), str(self.tmp / "missing-root")], "exclude": ["node_modules"]}
        base.update(extra)
        return base

    def test_discover_skips_non_repos_excluded_and_missing_roots(self):
        names = [p.name for p in jobs.discover(self.cfg())]
        self.assertEqual(names, ["seed"])

    def test_repo_status_reports_ahead_dirty_and_new_remote_commits(self):
        other = self.roots / "other"
        git(self.tmp, "clone", "-q", str(self.remote), str(other))
        git(other, "config", "user.email", "teammate@example.com")
        git(other, "config", "user.name", "teammate")
        (other / "b.txt").write_text("b", encoding="utf-8")
        git(other, "add", "b.txt")
        git(other, "commit", "-q", "-m", "teammate change")
        git(other, "push", "-q")
        (self.seed / "local.txt").write_text("x", encoding="utf-8")
        info = jobs.repo_status(self.seed, fetch=True, since_hours=24)
        self.assertEqual(info["behind"], 1)
        self.assertEqual(info["uncommitted"], 1)
        self.assertTrue(any("to pull" in a for a in info["attention"]))
        self.assertTrue(any(r[2] == "teammate change" for r in info["recent"]))

    def test_branch_without_upstream_flagged(self):
        git(self.seed, "checkout", "-q", "-b", "feature")
        info = jobs.repo_status(self.seed, fetch=False, since_hours=24)
        self.assertIn("branch has no upstream", info["attention"])

    def test_morning_brief_writes_report_and_exit_code(self):
        with mock.patch.object(jobs, "config", return_value=self.cfg()), redirect_stdout(StringIO()):
            rc = jobs.morning_brief(mock.Mock(no_fetch=True, open=False))
        self.assertEqual(rc, 0)
        report = next((self.tmp / "home" / "reports").glob("morning-brief-*.md")).read_text(encoding="utf-8")
        self.assertIn("1 repos checked, 0 need attention", report)
        self.assertIn("| Stashes |", report)

    def test_morning_brief_with_no_repos(self):
        with mock.patch.object(jobs, "config", return_value={"roots": [str(self.tmp / "nothing")]}), \
                redirect_stdout(StringIO()):
            self.assertEqual(jobs.morning_brief(mock.Mock(no_fetch=True, open=False)), 2)

    def test_npm_audit_parsing(self):
        payload = json.dumps({"vulnerabilities": {"lodash": {"severity": "high"}, "x": {"severity": "low"}},
                              "metadata": {"vulnerabilities": {"high": 1, "low": 1, "critical": 0}}})
        with mock.patch.object(jobs, "sh", return_value=(1, payload)):
            r = jobs.npm_audit(self.seed)
        self.assertEqual(r["counts"]["high"], 1)
        self.assertEqual(r["top"][0], "lodash (high)")
        self.assertEqual(jobs.serious(r), 1)
        with mock.patch.object(jobs, "sh", return_value=(1, "npm ERR! network")):
            self.assertIn("error", jobs.npm_audit(self.seed))
        with mock.patch.object(jobs, "sh", return_value=(None, "npm not installed")):
            self.assertEqual(jobs.npm_audit(self.seed)["error"], "npm not installed")

    def test_pip_audit_parsing_and_missing_requirements(self):
        self.assertEqual(jobs.pip_audit(self.seed)["error"], "no requirements file")
        (self.seed / "requirements.txt").write_text("django==3.0\n", encoding="utf-8")
        payload = json.dumps({"dependencies": [{"name": "django", "version": "3.0",
                                                "vulns": [{"id": "PYSEC-1", "fix_versions": ["3.0.1"]}]}]})
        with mock.patch.object(jobs, "sh", return_value=(1, payload + "Found 1 known vulnerability in 1 package")):
            r = jobs.pip_audit(self.seed)
        self.assertEqual(r["counts"]["known"], 1)
        self.assertIn("PYSEC-1", r["top"][0])
        with mock.patch.object(jobs, "sh", return_value=(1, "ERROR: could not resolve\n")):
            self.assertIn("could not resolve", jobs.pip_audit(self.seed)["error"])

    def test_weekly_kit_parses_pending_updates(self):
        out = ("ok                     a   pinned 1\nUPDATE                 security-audit  pinned 1\n"
               "MERGE                  session-preflight pinned 1\n\nNot adopted from x (add ...):\n  motion   desc\n")
        with mock.patch.object(jobs, "sh", return_value=(1, out)), redirect_stdout(StringIO()) as buf:
            self.assertEqual(jobs.weekly_kit(mock.Mock()), 1)
        self.assertIn("security-audit, session-preflight", buf.getvalue())
        self.assertIn("not adopted: 1", buf.getvalue())

    def test_schedule_xml_is_valid_and_times_validated(self):
        trig = jobs.triggers({"morning_brief": {"time": "08:30"}, "weekly_kit": {"day": "fri", "time": "17:00"}})
        for job, t in trig.items():
            xml = jobs.task_xml(job, t, "C:/Py thon/pythonw.exe", '"D:/kit & co/jobs.py" ' + job)
            root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
            ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
            self.assertEqual(root.find(".//t:StartWhenAvailable", ns).text, "true")
            self.assertIn("&", root.find(".//t:Arguments", ns).text)
        self.assertIn("T08:30:00", trig["morning-brief"])
        self.assertIn("<Friday />", trig["weekly-kit"])
        for bad in ({"morning_brief": {"time": "25:00"}}, {"weekly_kit": {"day": "XYZ"}}, {"nightly_audit": {"time": "9am"}}):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                jobs.triggers(bad)

    def test_schedule_dry_run_changes_nothing(self):
        with mock.patch.object(jobs, "sh") as sh, redirect_stdout(StringIO()) as buf:
            jobs.schedule(mock.Mock(op="install", dry_run=True))
        sh.assert_not_called()
        if jobs.IS_WIN:
            self.assertEqual(buf.getvalue().count("would schedule"), 3)

    def test_latest_without_reports(self):
        with redirect_stdout(StringIO()):
            self.assertEqual(jobs.latest(mock.Mock(job=None)), 1)

    def test_main_survives_pythonw_without_console(self):
        with mock.patch.object(sys, "stdout", None), mock.patch.object(jobs, "latest", return_value=0):
            self.assertEqual(jobs.main(["latest"]), 0)


if __name__ == "__main__":
    unittest.main()
