"""Layer-1 guard for the behavioral eval pack (evals/): structure, not behavior.

The eval runner itself needs a live agent and never runs in CI; this test keeps
the pack from rotting silently — every assertion kind must be one the runner
implements, every {In} placeholder must reference a seeded issue, and statuses
and labels used in seeds must exist in the scaffolded default configuration.
"""

import json
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPO_ROOT / "evals" / "scenarios.json"
RUNNER = REPO_ROOT / "evals" / "run_eval.py"


def _kinds(assertion):
    yield assertion["kind"]
    for sub in assertion.get("of", []):
        yield from _kinds(sub)


class EvalsPackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        runner_source = RUNNER.read_text(encoding="utf-8")
        match = re.search(r"ASSERTION_KINDS = \{(.*?)\}", runner_source, re.S)
        cls.runner_kinds = set(re.findall(r'"(\w+)"', match.group(1)))

    def test_scenarios_use_only_runner_assertion_kinds(self):
        for scenario in self.data["scenarios"]:
            for assertion in scenario["assertions"]:
                for kind in _kinds(assertion):
                    self.assertIn(kind, self.runner_kinds, f"{scenario['key']}: unknown kind {kind}")

    def test_placeholders_reference_seeded_issues(self):
        for scenario in self.data["scenarios"]:
            seeded = len(scenario.get("seed", []))
            for index in re.findall(r"\{I(\d+)\}", scenario["prompt"]):
                self.assertLessEqual(int(index), seeded, f"{scenario['key']}: placeholder I{index}")
            for assertion in scenario["assertions"]:
                for sub in [assertion, *assertion.get("of", [])]:
                    if "issue" in sub:
                        self.assertLessEqual(sub["issue"], seeded, f"{scenario['key']}: issue index")

    def test_seed_statuses_and_labels_exist_in_default_config(self):
        from local_board.config import default_config

        config = default_config("Eval", "EV")
        for scenario in self.data["scenarios"]:
            for spec in scenario.get("seed", []):
                if "status" in spec:
                    self.assertIn(f'name = "{spec["status"]}"', config, f"{scenario['key']}: status")
                for label in spec.get("labels", []):
                    self.assertIn(f'key = "{label}"', config, f"{scenario['key']}: label {label}")

    def test_scenario_keys_unique(self):
        keys = [scenario["key"] for scenario in self.data["scenarios"]]
        self.assertEqual(len(keys), len(set(keys)))


if __name__ == "__main__":
    unittest.main()
