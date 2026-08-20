import unittest
from pathlib import Path

from local_board.mcp import schemas


ROOT = Path(__file__).resolve().parents[2]


class SkillTest(unittest.TestCase):
    def test_skill_matches_packaged_template_and_has_valid_frontmatter(self):
        skill = (ROOT / ".agents/skills/local-board/SKILL.md").read_text()
        packaged = (ROOT / "local_board/templates/local-board-skill.md").read_text()
        self.assertEqual(skill, packaged)
        self.assertTrue(skill.startswith("---\nname: local-board\ndescription:"))
        self.assertNotIn("TODO", skill)

    def test_tools_named_by_skill_exist_in_contract(self):
        skill = (ROOT / ".agents/skills/local-board/SKILL.md").read_text()
        available = {item["name"] for item in schemas()}
        required = {"whoami", "list_projects", "get_project_context", "list_issues", "get_issue_context", "create_issue", "claim_issue", "add_dependency", "add_attachment", "add_git_link", "transition_issue", "release_issue"}
        self.assertEqual(required - available, set())
        for name in required:
            self.assertIn(f"`{name}`", skill)

    def test_skill_documents_issue_quality_and_git_policy(self):
        skill = (ROOT / ".agents/skills/local-board/SKILL.md").read_text()
        for issue_type in ("task", "bug", "feature", "chore", "epic"):
            self.assertIn(f"`{issue_type}`", skill)
        for priority in ("none", "low", "medium", "high", "urgent"):
            self.assertIn(f"`{priority}`", skill)
        for required_guidance in (
            "## Describe new work",
            "Markdown",
            "checklist",
            "agent_policy.branch_pattern",
            "local-board sync-branch",
            "LOCAL_BOARD_TOKEN",
        ):
            self.assertIn(required_guidance, skill)

    def test_tracked_examples_do_not_contain_real_tokens(self):
        paths = [ROOT / "examples/mcp-http.example.json", ROOT / "docs/agent-guide.md", ROOT / ".agents/skills/local-board/SKILL.md"]
        content = "\n".join(path.read_text() for path in paths)
        self.assertIn("${LOCAL_BOARD_TOKEN}", content)
        self.assertNotRegex(content, r"Bearer [A-Za-z0-9_-]{32,}")
