"""The CLI's user-facing contract: help, version, errors, and output modes.

These assert the promises an operator or a harness can rely on — a bare
invocation teaches, unknown commands suggest, `--json` is global, and colour
never leaks into a pipe — rather than the exact wording of any one line.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from local_board import __version__, usage
from local_board.cli import build_parser

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ANSI = "\033["


def _env(**overrides):
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    env.pop("FORCE_COLOR", None)
    env.pop("NO_COLOR", None)
    env.update(overrides)
    return env


def run_cli(*args, cwd=None, **env):
    return subprocess.run(
        ["python3", "-m", "local_board.cli", *args],
        cwd=cwd or PROJECT_ROOT,
        env=_env(**env),
        capture_output=True,
        text=True,
    )


class HelpTest(unittest.TestCase):
    def test_bare_invocation_prints_the_overview_instead_of_a_usage_error(self):
        result = run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        for command in ("init", "serve", "status", "doctor", "config", "backup", "restore"):
            self.assertIn(command, result.stdout)
        self.assertNotIn("error:", result.stdout)

    def test_overview_groups_commands_by_intent(self):
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for group in usage.GROUP_ORDER:
            self.assertIn(group, result.stdout)

    def test_command_help_documents_usage_and_examples(self):
        result = run_cli("serve", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("local-board serve", result.stdout)
        self.assertIn("Examples", result.stdout)
        self.assertIn("Global options", result.stdout)

    def test_help_subcommand_matches_the_command_help_flag(self):
        self.assertEqual(run_cli("help", "serve").stdout, run_cli("serve", "--help").stdout)

    def test_help_without_a_topic_prints_the_overview(self):
        self.assertEqual(run_cli("help").stdout, run_cli("--help").stdout)

    def test_help_for_an_unknown_topic_suggests_a_command(self):
        result = run_cli("help", "serv")
        self.assertEqual(result.returncode, 2)
        self.assertIn("serve", result.stderr)


class VersionTest(unittest.TestCase):
    def test_every_version_spelling_reports_the_same_line(self):
        expected = f"local-board {__version__}\n"
        for flag in ("-v", "-V", "--version"):
            with self.subTest(flag=flag):
                result = run_cli(flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)


class UnknownCommandTest(unittest.TestCase):
    def test_typo_exits_two_and_suggests_the_closest_command(self):
        result = run_cli("serv")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown command", result.stderr)
        self.assertIn("did you mean `serve`?", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_unknown_command_with_no_near_match_still_points_at_help(self):
        result = run_cli("zzzzzz")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--help", result.stderr)


class ColourTest(unittest.TestCase):
    def test_output_to_a_pipe_carries_no_escape_codes(self):
        self.assertNotIn(ANSI, run_cli("--help").stdout)

    def test_force_color_paints_and_no_color_wins_back(self):
        self.assertIn(ANSI, run_cli("--help", FORCE_COLOR="1").stdout)
        self.assertNotIn(ANSI, run_cli("--help", FORCE_COLOR="1", NO_COLOR="1").stdout)

    def test_color_never_beats_force_color(self):
        self.assertNotIn(ANSI, run_cli("--color", "never", "--help", FORCE_COLOR="1").stdout)


class MissingBoardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_command_needing_a_board_names_init_as_the_fix(self):
        result = run_cli("actor", "someone", cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no board", result.stderr)
        self.assertIn("local-board init", result.stderr)

    def test_status_reports_an_uninitialized_repository_without_failing(self):
        result = run_cli("status", "--json", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["database"], "not initialized")


class GlobalOptionPlacementTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.assertEqual(run_cli("init", cwd=self.root).returncode, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def test_json_is_accepted_before_and_after_the_command(self):
        before = run_cli("--json", "status", cwd=self.root)
        after = run_cli("status", "--json", cwd=self.root)
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(json.loads(before.stdout), json.loads(after.stdout))

    def test_json_before_the_command_is_not_reset_by_the_subparser(self):
        result = run_cli("--json", "actor", "scripted", cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("token", json.loads(result.stdout))

    def test_init_and_backup_honour_the_global_json_flag(self):
        for command in (("init", "--force"), ("backup",)):
            with self.subTest(command=command):
                result = run_cli(*command, "--json", cwd=self.root)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsInstance(json.loads(result.stdout), dict)

    def test_json_errors_stay_machine_readable_on_stdout(self):
        run_cli("actor", "dup", cwd=self.root)
        result = run_cli("actor", "dup", "--json", cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "conflict")
        self.assertNotIn(ANSI, result.stdout)

    def test_doctor_exits_nonzero_when_a_check_fails(self):
        result = run_cli("doctor", "--url", "http://127.0.0.1:1/mcp", cwd=self.root)
        self.assertEqual(result.returncode, 1)
        self.assertIn("hint", result.stderr)


class CommandTableTest(unittest.TestCase):
    def test_parser_and_help_table_describe_the_same_commands(self):
        parser = build_parser()
        actions = [action for action in parser._subparsers._group_actions]
        self.assertEqual(set(actions[0].choices), set(usage.BY_NAME))

    def test_every_command_carries_a_summary_and_a_usage_line(self):
        for command in usage.COMMANDS:
            with self.subTest(command=command.name):
                self.assertTrue(command.summary)
                self.assertTrue(command.usage)

    def test_every_documented_command_renders_its_help_page(self):
        for name in usage.BY_NAME:
            with self.subTest(command=name):
                result = run_cli(name, "--help")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Usage", result.stdout)


if __name__ == "__main__":
    unittest.main()
