"""Declarative project configuration and reconciliation."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Board, ISSUE_TYPES, PRIORITIES, now


CONFIG_SCHEMA_VERSION = 1
MANAGED_BY = "config"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    path: Path
    data: dict[str, Any]
    digest: str

    @property
    def project(self) -> dict[str, Any]:
        return self.data["project"]

    @property
    def schema_version(self) -> int:
        return self.data["schema_version"]


def _canonical_digest(data: dict[str, Any]) -> str:
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as source:
            data = tomllib.load(source)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    validate_config(data)
    return ProjectConfig(config_path, data, _canonical_digest(data))


def validate_config(data: dict[str, Any]) -> None:
    version = data.get("schema_version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"unsupported config schema_version {version!r}; expected {CONFIG_SCHEMA_VERSION}")
    project = data.get("project")
    if not isinstance(project, dict):
        raise ConfigError("[project] is required")
    key = project.get("key", "")
    if not isinstance(key, str) or not key.isalnum() or not 2 <= len(key) <= 10:
        raise ConfigError("project.key must be 2-10 alphanumeric characters")
    if not isinstance(project.get("name"), str) or not project["name"].strip():
        raise ConfigError("project.name is required")
    defaults = data.get("defaults", {})
    if defaults.get("issue_type", "task") not in ISSUE_TYPES:
        raise ConfigError("defaults.issue_type is invalid")
    if defaults.get("priority", "medium") not in PRIORITIES:
        raise ConfigError("defaults.priority is invalid")
    policy = data.get("agent_policy", {})
    if not isinstance(policy, dict):
        raise ConfigError("[agent_policy] must be a table")
    if "require_assignee_before_start" in policy and not isinstance(policy["require_assignee_before_start"], bool):
        raise ConfigError("agent_policy.require_assignee_before_start must be boolean")
    reviewers = policy.get("require_reviewer_for", [])
    if not isinstance(reviewers, list) or any(item not in ISSUE_TYPES for item in reviewers):
        raise ConfigError("agent_policy.require_reviewer_for contains an invalid issue type")
    if "branch_pattern" in policy and not isinstance(policy["branch_pattern"], str):
        raise ConfigError("agent_policy.branch_pattern must be a string")

    label_keys: set[str] = set()
    label_names: set[str] = set()
    for label in data.get("labels", []):
        if not isinstance(label, dict) or not label.get("key") or not label.get("name"):
            raise ConfigError("each [[labels]] entry requires key and name")
        if label["key"] in label_keys or label["name"] in label_names:
            raise ConfigError("label keys and names must be unique")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", label.get("color", "#64748b")):
            raise ConfigError(f"invalid color for label {label['key']}")
        label_keys.add(label["key"]); label_names.add(label["name"])

    workflows = data.get("workflows")
    if not isinstance(workflows, dict) or not workflows:
        raise ConfigError("[workflows] must define at least one issue type")
    for issue_type, workflow in workflows.items():
        if issue_type not in ISSUE_TYPES or not isinstance(workflow, dict):
            raise ConfigError(f"invalid workflow issue type: {issue_type}")
        states = workflow.get("states", [])
        if not states or len(states) != len(set(states)) or not all(isinstance(s, str) and s for s in states):
            raise ConfigError(f"workflow {issue_type} states must be non-empty and unique")
        initial = workflow.get("initial")
        terminal = workflow.get("terminal", [])
        if initial not in states:
            raise ConfigError(f"workflow {issue_type} initial state is not in states")
        if not terminal or any(state not in states for state in terminal):
            raise ConfigError(f"workflow {issue_type} terminal states are invalid")
        transitions = workflow.get("transitions", [])
        if any(not isinstance(edge, list) or len(edge) != 2 or edge[0] not in states or edge[1] not in states for edge in transitions):
            raise ConfigError(f"workflow {issue_type} has an invalid transition")
        reachable = {initial}
        changed = True
        while changed:
            changed = False
            for source, target in transitions:
                if source in reachable and target not in reachable:
                    reachable.add(target); changed = True
        missing = set(states) - reachable
        if missing:
            raise ConfigError(f"workflow {issue_type} has unreachable states: {', '.join(sorted(missing))}")


def default_config(project_name: str, key: str) -> str:
    header = f'''schema_version = 1

[project]
key = "{key}"
name = "{project_name}"
description = ""

[defaults]
issue_type = "task"
priority = "medium"

[agent_policy]
require_assignee_before_start = true
require_reviewer_for = ["feature", "bug"]
branch_pattern = "{{issue_key}}-{{slug}}"

[[labels]]
key = "backend"
name = "Backend"
color = "#64748b"
'''
    workflow = '''
[workflows.{issue_type}]
initial = "backlog"
terminal = ["done", "cancelled"]
states = ["backlog", "todo", "in_progress", "in_review", "done", "cancelled"]
transitions = [
  ["backlog", "todo"],
  ["todo", "in_progress"],
  ["in_progress", "in_review"],
  ["in_review", "in_progress"],
  ["in_review", "done"],
  ["backlog", "cancelled"],
  ["todo", "cancelled"],
  ["in_progress", "cancelled"],
  ["in_review", "cancelled"],
]
'''
    return header + "".join(workflow.format(issue_type=issue_type) for issue_type in ISSUE_TYPES)


class ConfigService:
    def __init__(self, board: Board):
        self.board = board

    def plan(self, config: ProjectConfig) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        key = config.project["key"].upper()
        with self.board.connect() as db:
            project = db.execute("SELECT * FROM projects WHERE key=?", (key,)).fetchone()
            if not project:
                actions.append({"action": "create", "entity": "project", "key": key})
                current_workflows: dict[str, Any] = {}
                current_labels: dict[str, Any] = {}
            else:
                desired = (config.project["name"], config.project.get("description", ""))
                if (project["name"], project["description"]) != desired:
                    actions.append({"action": "update", "entity": "project", "key": key})
                current_workflows = {row["issue_type"]: row for row in db.execute("SELECT * FROM workflows WHERE project_id=?", (project["id"],))}
                current_labels = {row["key"]: row for row in db.execute("SELECT * FROM labels WHERE project_id=? AND key IS NOT NULL", (project["id"],))}
            for issue_type, workflow in config.data["workflows"].items():
                current = current_workflows.get(issue_type)
                states = json.dumps(workflow["states"])
                transitions = json.dumps(workflow["transitions"])
                if not current:
                    actions.append({"action": "create", "entity": "workflow", "key": issue_type})
                elif current["states_json"] != states or current["transitions_json"] != transitions or current["managed_by"] != MANAGED_BY:
                    actions.append({"action": "update", "entity": "workflow", "key": issue_type})
            for label in config.data.get("labels", []):
                current = current_labels.get(label["key"])
                if not current:
                    actions.append({"action": "create", "entity": "label", "key": label["key"]})
                elif current["name"] != label["name"] or current["color"] != label.get("color", "#64748b") or current["managed_by"] != MANAGED_BY:
                    actions.append({"action": "update", "entity": "label", "key": label["key"]})
            applied = db.execute("SELECT digest FROM project_config pc JOIN projects p ON p.id=pc.project_id WHERE p.key=?", (key,)).fetchone()
        return {"config": str(config.path), "digest": config.digest, "previous_digest": applied[0] if applied else None, "actions": actions, "changed": bool(actions) or not applied or applied[0] != config.digest}

    def apply(self, config: ProjectConfig) -> dict[str, Any]:
        plan = self.plan(config)
        key = config.project["key"].upper()
        if not plan["changed"]:
            with self.board.connect() as db:
                project_id = db.execute("SELECT id FROM projects WHERE key=?", (key,)).fetchone()[0]
            return {**plan, "project_id": project_id, "applied": False}
        stamp = now()
        with self.board.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute("SELECT * FROM projects WHERE key=?", (key,)).fetchone()
            if project:
                project_id = project["id"]
                db.execute("UPDATE projects SET name=?,description=?,managed_by=?,updated_at=? WHERE id=?", (config.project["name"], config.project.get("description", ""), MANAGED_BY, stamp, project_id))
            else:
                cur = db.execute("INSERT INTO projects(key,name,description,created_at,updated_at,managed_by) VALUES(?,?,?,?,?,?)", (key, config.project["name"], config.project.get("description", ""), stamp, stamp, MANAGED_BY))
                project_id = cur.lastrowid
            for issue_type, workflow in config.data["workflows"].items():
                used = {row[0] for row in db.execute("SELECT DISTINCT status FROM issues WHERE project_id=? AND type=?", (project_id, issue_type))}
                removed = used - set(workflow["states"])
                if removed:
                    raise ConfigError(f"cannot remove workflow states used by issues: {', '.join(sorted(removed))}")
                db.execute("INSERT INTO workflows(project_id,issue_type,states_json,transitions_json,managed_by) VALUES(?,?,?,?,?) ON CONFLICT(project_id,issue_type) DO UPDATE SET states_json=excluded.states_json,transitions_json=excluded.transitions_json,managed_by=excluded.managed_by", (project_id, issue_type, json.dumps(workflow["states"]), json.dumps(workflow["transitions"]), MANAGED_BY))
            for label in config.data.get("labels", []):
                current = db.execute("SELECT id FROM labels WHERE project_id=? AND (key=? OR (key IS NULL AND name=?))", (project_id, label["key"], label["name"])).fetchone()
                if current:
                    db.execute("UPDATE labels SET key=?,name=?,color=?,managed_by=? WHERE id=?", (label["key"], label["name"], label.get("color", "#64748b"), MANAGED_BY, current["id"]))
                else:
                    db.execute("INSERT INTO labels(project_id,key,name,color,managed_by) VALUES(?,?,?,?,?)", (project_id, label["key"], label["name"], label.get("color", "#64748b"), MANAGED_BY))
            diff = json.dumps(plan["actions"], ensure_ascii=False)
            defaults_json = json.dumps(config.data.get("defaults", {}))
            policy_json = json.dumps(config.data.get("agent_policy", {}))
            db.execute("INSERT INTO project_config(project_id,digest,schema_version,defaults_json,agent_policy_json,applied_at) VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET digest=excluded.digest,schema_version=excluded.schema_version,defaults_json=excluded.defaults_json,agent_policy_json=excluded.agent_policy_json,applied_at=excluded.applied_at", (project_id, config.digest, config.schema_version, defaults_json, policy_json, stamp))
            db.execute("INSERT INTO config_applies(project_id,digest,schema_version,diff_json,applied_at) VALUES(?,?,?,?,?)", (project_id, config.digest, config.schema_version, diff, stamp))
            self.board._activity(db, None, "project", project_id, "config_applied", {"digest": config.digest, "actions": plan["actions"]})
        return {**plan, "project_id": project_id, "applied": True}


def suggested_key(name: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:10]
    return key if len(key) >= 2 else "APP"
