"""Unit tests for the terminal presentation layer (ui.py)."""

import io
import unittest

from local_board import ui


class _Tty(io.StringIO):
    def isatty(self):
        return True


class ThemeTest(unittest.TestCase):
    def test_paint_is_identity_without_colour(self):
        theme = ui.Theme(colour=False)
        self.assertEqual(theme.paint("text", "bold", "red"), "text")

    def test_paint_wraps_with_ansi_codes(self):
        theme = ui.Theme(colour=True)
        self.assertEqual(theme.paint("x", "bold"), "\033[1mx\033[0m")
        self.assertEqual(theme.paint("x", "red"), "\033[31mx\033[0m")
        self.assertEqual(theme.bold("x"), "\033[1mx\033[0m")
        self.assertEqual(theme.dim("x"), "\033[2mx\033[0m")
        self.assertEqual(theme.cyan("x"), "\033[36mx\033[0m")

    def test_glyph_falls_back_to_ascii(self):
        fancy = ui.Theme(unicode=True)
        plain = ui.Theme(unicode=False)
        self.assertEqual(fancy.glyph("pass"), "✔")
        self.assertEqual(plain.glyph("pass"), "ok  ")
        self.assertEqual(plain.glyph("fail"), "fail")

    def test_glyph_is_coloured_when_colour_is_on(self):
        theme = ui.Theme(colour=True, unicode=True)
        self.assertEqual(theme.glyph("fail"), "\033[31m✘\033[0m")


class ColourDetectionTest(unittest.TestCase):
    def test_always_and_never_override_everything(self):
        self.assertTrue(ui._supports_colour("always", io.StringIO(), {"NO_COLOR": "1"}))
        self.assertFalse(ui._supports_colour("never", _Tty(), {"FORCE_COLOR": "1"}))

    def test_no_color_env_wins_over_tty(self):
        self.assertFalse(ui._supports_colour("auto", _Tty(), {"NO_COLOR": "1"}))

    def test_force_color_env_wins_over_pipe(self):
        self.assertTrue(ui._supports_colour("auto", io.StringIO(), {"FORCE_COLOR": "1"}))

    def test_dumb_terminal_is_plain(self):
        self.assertFalse(ui._supports_colour("auto", _Tty(), {"TERM": "dumb"}))

    def test_auto_follows_the_tty(self):
        self.assertTrue(ui._supports_colour("auto", _Tty(), {}))
        self.assertFalse(ui._supports_colour("auto", io.StringIO(), {}))

    def test_unicode_detection_follows_stream_encoding(self):
        utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        ascii_stream = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        self.assertTrue(ui._supports_unicode(utf8))
        self.assertFalse(ui._supports_unicode(ascii_stream))
        self.assertFalse(ui._supports_unicode(io.StringIO()))  # no encoding attr → ascii

    def test_configure_sets_the_process_theme(self):
        saved = (ui.theme.colour, ui.theme.unicode)
        try:
            result = ui.configure("always", stream=io.StringIO(), environ={})
            self.assertIs(result, ui.theme)
            self.assertTrue(ui.theme.colour)
            ui.configure("never", stream=io.StringIO(), environ={})
            self.assertFalse(ui.theme.colour)
        finally:
            ui.theme.colour, ui.theme.unicode = saved


class MessageVocabularyTest(unittest.TestCase):
    def setUp(self):
        self.saved = (ui.theme.colour, ui.theme.unicode)
        ui.theme.colour = False
        ui.theme.unicode = False

    def tearDown(self):
        ui.theme.colour, ui.theme.unicode = self.saved

    def test_error_prints_severity_then_indented_hints(self):
        out = io.StringIO()
        ui.error("board not found", "run `local-board init`", stream=out)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "error: board not found")
        self.assertEqual(lines[1], "  hint: run `local-board init`")

    def test_warn_uses_warning_prefix(self):
        out = io.StringIO()
        ui.warn("config drift", stream=out)
        self.assertEqual(out.getvalue(), "warning: config drift\n")

    def test_note_and_heading(self):
        out = io.StringIO()
        ui.note("plain", stream=out)
        ui.heading("Section", stream=out)
        self.assertEqual(out.getvalue(), "plain\nSection\n")

    def test_fields_aligns_labels(self):
        out = io.StringIO()
        ui.fields([("url", "http://x"), ("database", "/tmp/db")], stream=out)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "  url       http://x")
        self.assertEqual(lines[1], "  database  /tmp/db")

    def test_fields_with_no_rows_prints_nothing(self):
        out = io.StringIO()
        ui.fields([], stream=out)
        self.assertEqual(out.getvalue(), "")

    def test_listing_pads_names_to_width(self):
        out = io.StringIO()
        ui.listing([("init", "set up")], width=6, stream=out)
        self.assertEqual(out.getvalue(), "  init    set up\n")

    def test_check_renders_status_glyph_and_message(self):
        out = io.StringIO()
        ui.check("pass", "schema", "v4", width=8, stream=out)
        self.assertEqual(out.getvalue(), "  ok    schema    v4\n")


if __name__ == "__main__":
    unittest.main()
