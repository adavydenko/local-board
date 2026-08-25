# Skill evaluations

Testing the local-board skill happens in two layers:

1. **Static checks** — `tests/unit/test_skill.py`, run in CI. They prove the
   skill is *consistent* (frontmatter valid, referenced files exist, every tool
   the skill names exists on the real MCP surface and vice versa). They cannot
   prove the skill is *sufficient*.
2. **Behavioral evaluations** — this directory. A real agent works real
   scenarios against a live board, and the resulting **board state** is graded
   with objective assertions (status category, assignee, lease, comments,
   labels, activity journal). No LLM judge. These need a live agent runtime,
   so they are **not part of GitHub CI** — run them locally.

## Running

```bash
# with the skill (default)
python evals/run_eval.py --agent \
  'claude -p "$(cat "$EVAL_PROMPT_FILE")" --dangerously-skip-permissions'

# baseline: same scenarios, instructions stripped — the control group
python evals/run_eval.py --baseline --agent '…'

# variance: repeat each scenario, e.g. 3 times
python evals/run_eval.py --runs 3 --agent '…'

# one scenario, keep the temp repo for inspection
python evals/run_eval.py --scenario review-convention --keep --agent '…'
```

The `--agent` command is any shell command that performs one autonomous agent
turn. It runs with cwd set to a freshly scaffolded temporary repository
(`git init` + `local-board init`, board served live on a loopback port) and
receives:

- `EVAL_PROMPT_FILE` — path to the scenario prompt;
- `LOCAL_BOARD_URL` / `LOCAL_BOARD_TOKEN` — the live MCP endpoint and the
  member token of the `eval-agent` actor;
- a ready `.mcp.json` in the repo pointing MCP clients at that endpoint (the
  Authorization header expands `LOCAL_BOARD_TOKEN` from the environment).

Any agent runtime works if it can read a prompt and reach an MCP server —
substitute your own command for the `claude` example.

## Method

The design follows the evaluation-driven pattern from Anthropic's skill
authoring guidance: scenarios encode the behaviors the skill exists to induce,
each run is compared against a **baseline without the skill**, and repeated
runs (`--runs`) expose variance. Grading is by board-state assertions declared
in `scenarios.json` — deterministic and agent-agnostic; if a scenario needs a
subjective judgment, prefer adding an objective proxy assertion over a judge.

Scenarios cover the skill's load-bearing behaviors: claim → work → complete;
taking over an expired lease; abandoning infeasible work with a justification
(release clears assignee); the `review_required` convention (In Review, label
kept for the reviewer); reacting to a regression traced to a completed issue
(reopen or file a follow-up).

`tests/unit/test_evals_pack.py` lint-checks this pack in CI (valid JSON, known
assertion kinds, placeholders consistent with seeds) so the scenarios cannot
rot silently between local runs.
