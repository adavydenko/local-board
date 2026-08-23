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


if __name__ == "__main__":
    unittest.main()
