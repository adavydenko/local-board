"""Unit tests for the command table and rendered help pages (usage.py)."""

import io
import unittest

from local_board import __version__, ui, usage


class _PlainTheme(unittest.TestCase):
    def setUp(self):
        self.saved = (ui.theme.colour, ui.theme.unicode)
        ui.theme.colour = False
        ui.theme.unicode = False

    def tearDown(self):
        ui.theme.colour, ui.theme.unicode = self.saved


class CommandTableTest(_PlainTheme):
    def test_command_names_are_unique(self):
        names = [command.name for command in usage.COMMANDS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(usage.BY_NAME))

    def test_every_group_is_rendered_or_deliberately_bare(self):
        # `help` carries an empty group on purpose (it appears in the epilogue);
        # every other command must belong to a group the overview knows about.
        for command in usage.COMMANDS:
            if command.group:
                self.assertIn(command.group, usage.GROUP_ORDER, command.name)


class OverviewTest(_PlainTheme):
    def test_overview_names_every_grouped_command_and_the_version(self):
        out = io.StringIO()
        usage.overview(out)
        text = out.getvalue()
        self.assertIn(__version__, text)
        self.assertIn(usage.TAGLINE, text)
        self.assertIn(usage.DOCS_URL, text)
        for command in usage.COMMANDS:
            if command.group:
                self.assertIn(command.name, text)
        for group in usage.GROUP_ORDER:
            self.assertIn(group, text)

    def test_overview_shows_global_options_with_env_names(self):
        out = io.StringIO()
        usage.overview(out)
        text = out.getvalue()
        self.assertIn("--json", text)
        self.assertIn("[env: LOCAL_BOARD_DB]", text)


class CommandHelpTest(_PlainTheme):
    def test_help_page_renders_for_every_command(self):
        for name in usage.BY_NAME:
            out = io.StringIO()
            usage.command_help(name, out)
            text = out.getvalue()
            self.assertIn(usage.BY_NAME[name].summary, text, name)
            self.assertIn("Usage", text, name)
            self.assertIn("-h, --help", text, name)

    def test_help_page_includes_examples_and_next_steps_when_declared(self):
        for name, command in usage.BY_NAME.items():
            out = io.StringIO()
            usage.command_help(name, out)
            text = out.getvalue()
            for example in command.examples:
                self.assertIn(example, text, name)
            for step in command.next_steps:
                self.assertIn(step, text, name)
            for sub, summary in command.subcommands:
                self.assertIn(sub, text, name)
                self.assertIn(summary, text, name)


class DidYouMeanTest(_PlainTheme):
    def test_close_typo_suggests_the_command(self):
        self.assertIn("serve", usage.did_you_mean("serv"))
        self.assertIn("doctor", usage.did_you_mean("docter"))

    def test_gibberish_suggests_nothing(self):
        self.assertEqual(usage.did_you_mean("xyzzy-quux"), [])


if __name__ == "__main__":
    unittest.main()
