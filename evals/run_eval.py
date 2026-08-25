#!/usr/bin/env python3
"""Local behavioral evaluation of the local-board skill (layer 2).

Runs a real agent against a freshly scaffolded repository + board per scenario
and grades the resulting BOARD STATE against objective assertions — no LLM
judge. This is deliberately NOT part of GitHub CI: it needs a live agent
runtime. Run it locally:

    python evals/run_eval.py --agent \\
      'claude -p "$(cat "$EVAL_PROMPT_FILE")" --dangerously-skip-permissions'

Contract with the agent command (executed through the shell, cwd = the
scenario's temp repository):
  - EVAL_PROMPT_FILE  path to the scenario prompt text
  - LOCAL_BOARD_URL   MCP endpoint of the scenario's live server
  - LOCAL_BOARD_TOKEN bearer token of the "eval-agent" member actor
  - .mcp.json in the repo already points MCP clients at the server with the
    Authorization header expanded from LOCAL_BOARD_TOKEN.

--baseline strips the scaffolded instructions (AGENTS.md, .agents/,
.local-board/AGENT.md) so the same scenarios measure the agent WITHOUT the
skill; compare pass rates between the two modes. --runs N repeats every
scenario to expose run-to-run variance.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from local_board.db import Board  # noqa: E402
from local_board.web import make_handler  # noqa: E402

SCENARIOS_PATH = Path(__file__).resolve().parent / "scenarios.json"

ASSERTION_KINDS = {
    "issue_category",
    "issue_status",
    "assignee_is_agent",
    "unassigned",
    "lease_cleared",
    "comment_count_min",
    "label_present",
    "label_absent",
    "activity_action",
    "new_issue_created",
    "any_of",
}


def load_scenarios() -> dict:
    with SCENARIOS_PATH.open(encoding="utf-8") as stream:
        return json.load(stream)


def scaffold_repo(workdir: Path, *, baseline: bool) -> None:
    subprocess.run(["git", "init", "-q"], cwd=workdir, check=True)
    import os

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['local-board','init']; from local_board.cli import main; main()"],
        cwd=workdir, check=True, capture_output=True, text=True, env=env,
    )
    if baseline:
        for path in ("AGENTS.md", ".agents", ".local-board/AGENT.md"):
            target = workdir / path
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()


def seed(board: Board, scenario: dict, seeder_id: int, agent_id: int, other_id: int) -> list[int]:
    issue_ids = []
    for spec in scenario.get("seed", []):
        issue = board.create_issue(
            seeder_id,
            spec["title"],
            spec.get("description", ""),
            status=spec.get("status"),
            labels=spec.get("labels"),
        )
        issue_ids.append(issue["id"])
        claim = spec.get("claim")
        if claim in ("expired-other", "agent"):
            holder = other_id if claim == "expired-other" else agent_id
            expiry = "2000-01-01T00:00:00+00:00" if claim == "expired-other" else "2999-01-01T00:00:00+00:00"
            with board.transaction() as db:
                db.execute(
                    "UPDATE issues SET assignee_id=?, claimed_at=?, claim_expires_at=? WHERE id=?",
                    (holder, "2000-01-01T00:00:00+00:00", expiry, issue["id"]),
                )
    return issue_ids


def grade_one(board: Board, assertion: dict, ctx: dict) -> tuple[bool, str]:
    kind = assertion["kind"]
    if kind == "any_of":
        results = [grade_one(board, sub, ctx) for sub in assertion["of"]]
        ok = any(passed for passed, _ in results)
        return ok, " OR ".join(detail for _, detail in results)

    with board.connect() as db:
        if kind == "new_issue_created":
            total = db.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
            return total > ctx["seed_count"], f"issues total {total} vs seeded {ctx['seed_count']}"

        issue_id = ctx["issue_ids"][assertion["issue"] - 1]
        row = db.execute(
            "SELECT i.status, i.assignee_id, i.claimed_at, i.claim_expires_at, s.category "
            "FROM issues i JOIN statuses s ON s.name=i.status WHERE i.id=?",
            (issue_id,),
        ).fetchone()
        if kind == "issue_category":
            if "equals" in assertion:
                return row["category"] == assertion["equals"], f"category={row['category']}"
            return row["category"] != assertion["not_equals"], f"category={row['category']}"
        if kind == "issue_status":
            return row["status"] == assertion["equals"], f"status={row['status']}"
        if kind == "assignee_is_agent":
            return row["assignee_id"] == ctx["agent_id"], f"assignee_id={row['assignee_id']}"
        if kind == "unassigned":
            return row["assignee_id"] is None, f"assignee_id={row['assignee_id']}"
        if kind == "lease_cleared":
            return row["claim_expires_at"] is None, f"claim_expires_at={row['claim_expires_at']}"
        if kind == "comment_count_min":
            count = db.execute(
                "SELECT COUNT(*) FROM comments WHERE issue_id=?", (issue_id,)
            ).fetchone()[0]
            return count >= assertion["min"], f"comments={count}"
        if kind in ("label_present", "label_absent"):
            hit = db.execute(
                "SELECT 1 FROM issue_labels il JOIN labels l ON l.id=il.label_id "
                "WHERE il.issue_id=? AND (l.key=? OR l.name=?)",
                (issue_id, assertion["key"], assertion["key"]),
            ).fetchone()
            present = hit is not None
            return (present if kind == "label_present" else not present), f"label present={present}"
        if kind == "activity_action":
            hit = db.execute(
                "SELECT 1 FROM activity WHERE entity_type='issue' AND entity_id=? AND action=?",
                (issue_id, assertion["action"]),
            ).fetchone()
            return hit is not None, f"action {assertion['action']} recorded={hit is not None}"
    raise ValueError(f"unknown assertion kind: {kind}")


def run_scenario(scenario: dict, args: argparse.Namespace) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix=f"lb-eval-{scenario['key']}-"))
    server = None
    try:
        scaffold_repo(workdir, baseline=args.baseline)
        board = Board(workdir / ".local-board" / "state" / "board.db")
        seeder = board.create_actor("eval-seeder", kind="human", role="admin")
        agent = board.create_actor("eval-agent")
        other = board.create_actor("eval-other-agent")
        issue_ids = seed(board, scenario, seeder["id"], agent["id"], other["id"])

        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(board))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_port}/mcp"

        prefix = board.get_board()["prefix"]
        with board.connect() as db:
            numbers = {issue_id: db.execute("SELECT number FROM issues WHERE id=?", (issue_id,)).fetchone()[0]
                       for issue_id in issue_ids}
        prompt = scenario["prompt"]
        for index, issue_id in enumerate(issue_ids, start=1):
            prompt = prompt.replace(f"{{I{index}}}", f"{prefix}-{numbers[issue_id]}")

        (workdir / ".mcp.json").write_text(json.dumps({
            "mcpServers": {"local-board": {
                "type": "http", "url": url,
                "headers": {"Authorization": "Bearer ${LOCAL_BOARD_TOKEN}"},
            }},
        }, indent=2) + "\n", encoding="utf-8")
        prompt_file = workdir / ".eval-prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        env = {**dict(__import__("os").environ),
               "LOCAL_BOARD_URL": url,
               "LOCAL_BOARD_TOKEN": agent["token"],
               "EVAL_PROMPT_FILE": str(prompt_file)}
        try:
            completed = subprocess.run(args.agent, shell=True, cwd=workdir, env=env,
                                       timeout=args.timeout, capture_output=True, text=True)
            agent_note = f"agent exit {completed.returncode}"
        except subprocess.TimeoutExpired:
            agent_note = f"agent timed out after {args.timeout}s"

        ctx = {"issue_ids": issue_ids, "agent_id": agent["id"], "seed_count": len(issue_ids)}
        checks = []
        for assertion in scenario["assertions"]:
            passed, detail = grade_one(board, assertion, ctx)
            checks.append({"kind": assertion["kind"], "passed": passed, "detail": detail})
        return {"key": scenario["key"], "agent": agent_note, "checks": checks,
                "passed": all(check["passed"] for check in checks), "workdir": str(workdir)}
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if not args.keep:
            shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", required=True, help="shell command that runs one agent turn (see module docstring)")
    parser.add_argument("--scenario", help="run only the scenario with this key")
    parser.add_argument("--runs", type=int, default=1, help="repetitions per scenario (variance)")
    parser.add_argument("--baseline", action="store_true", help="strip skill/instructions before running")
    parser.add_argument("--timeout", type=int, default=600, help="seconds per agent run")
    parser.add_argument("--keep", action="store_true", help="keep scenario temp repos for inspection")
    args = parser.parse_args()

    scenarios = load_scenarios()["scenarios"]
    if args.scenario:
        scenarios = [scenario for scenario in scenarios if scenario["key"] == args.scenario]
        if not scenarios:
            parser.error(f"no scenario named {args.scenario!r}")

    mode = "baseline (no skill)" if args.baseline else "with skill"
    print(f"local-board skill eval — {mode}, {args.runs} run(s) per scenario\n")
    failures = 0
    for scenario in scenarios:
        passes = 0
        for run in range(args.runs):
            result = run_scenario(scenario, args)
            passes += result["passed"]
            marker = "PASS" if result["passed"] else "FAIL"
            print(f"[{marker}] {scenario['key']} (run {run + 1}/{args.runs}, {result['agent']})")
            for check in result["checks"]:
                if not check["passed"]:
                    print(f"       ✗ {check['kind']}: {check['detail']}")
            if args.keep:
                print(f"       repo kept at {result['workdir']}")
        if passes < args.runs:
            failures += 1
        print(f"  → {scenario['key']}: {passes}/{args.runs} passed\n")
    print("done:", "all scenarios green" if failures == 0 else f"{failures} scenario(s) with failures")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
