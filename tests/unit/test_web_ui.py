import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class WebUiTest(unittest.TestCase):
    def test_ui_uses_dynamic_workflows_and_human_review_controls(self):
        html = (ROOT / "local_board/static/index.html").read_text()
        self.assertNotIn("const states=['backlog'", html)
        for marker in ["/api/issues/", "available_transitions", "Claim issue", "Reviewer", "Checklist", "Comments", "projectSelect", "activityView"]:
            self.assertIn(marker, html)

    def test_ui_escapes_content_before_markdown_rendering(self):
        html = (ROOT / "local_board/static/index.html").read_text()
        self.assertIn("let out=esc(value", html)
        self.assertIn("textContent=v", html)
