import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = ROOT / "local_board" / "templates"


class SkillTest(unittest.TestCase):
    """Structural checks only: another agent owns the wording of these templates."""

    def test_template_files_exist_and_are_non_empty(self):
        templates = list(TEMPLATES_DIR.glob("*"))
        self.assertTrue(templates, "expected packaged templates in local_board/templates/")
        for template in templates:
            self.assertGreater(template.stat().st_size, 0, f"{template} is empty")

    def test_skill_has_valid_frontmatter(self):
        skill = (TEMPLATES_DIR / "local-board-skill.md").read_text()
        self.assertTrue(skill.startswith("---\nname: local-board\ndescription:"))

    def test_tools_reference_is_generated_and_current(self):
        """The tracked cheat-sheet must byte-match the catalog-derived render: no second brain."""
        from local_board.onboarding import render_tools_reference

        tracked = (ROOT / ".agents/skills/local-board/references/tools.md").read_text(encoding="utf-8")
        self.assertEqual(tracked, render_tools_reference())

    def test_tool_annotations_reference_real_tools(self):
        from local_board import mcp

        catalog = {item["name"] for item in mcp.TOOLS_READ + mcp.TOOLS_WRITE + mcp.TOOLS_CORRECTION + mcp.TOOLS_ADMIN}
        for name in (*mcp.TOOL_REV, *mcp.TOOL_NOTES):
            self.assertIn(name, catalog, f"annotation for unknown tool {name}")


if __name__ == "__main__":
    unittest.main()
