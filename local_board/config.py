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

    milestone_keys: set[str] = set()
    for milestone in data.get("milestones", []):
        if not isinstance(milestone, dict) or not milestone.get("key") or not milestone.get("name"):
            raise ConfigError("each [[milestones]] entry requires key and name")
        if milestone["key"] in milestone_keys:
            raise ConfigError("milestone keys must be unique")
        milestone_keys.add(milestone["key"])

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
    migrations = data.get("state_migrations", {})
    if not isinstance(migrations, dict):
        raise ConfigError("[state_migrations] must be a table")
    for issue_type, mapping in migrations.items():
        if issue_type not in workflows or not isinstance(mapping, dict):
            raise ConfigError(f"invalid state_migrations issue type: {issue_type}")
        desired_states = workflows[issue_type]["states"]
        if any(not isinstance(old, str) or target not in desired_states for old, target in mapping.items()):
            raise ConfigError(f"state_migrations.{issue_type} must map old states to desired states")


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

    def plan(self, config: ProjectConfig, *, prune: bool = False) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        key = config.project["key"].upper()
        with self.board.connect() as db:
            project = db.execute("SELECT * FROM projects WHERE key=?", (key,)).fetchone()
            current_workflows = {} if not project else {r["issue_type"]: r for r in db.execute("SELECT * FROM workflows WHERE project_id=?", (project["id"],))}
            current_labels = {} if not project else {r["key"]: r for r in db.execute("SELECT * FROM labels WHERE project_id=? AND key IS NOT NULL", (project["id"],))}
            current_milestones = {} if not project else {r["key"]: r for r in db.execute("SELECT * FROM milestones WHERE project_id=? AND key IS NOT NULL", (project["id"],))}
            if not project:
                actions.append({"action": "create", "entity": "project", "key": key})
            elif (project["name"], project["description"], project["managed_by"]) != (config.project["name"], config.project.get("description", ""), MANAGED_BY):
                actions.append({"action": "update", "entity": "project", "key": key})
            desired = config.data["workflows"]
            for issue_type, workflow in desired.items():
                current = current_workflows.get(issue_type)
                values = (json.dumps(workflow["states"]), json.dumps(workflow["transitions"]), MANAGED_BY)
                if not current: actions.append({"action": "create", "entity": "workflow", "key": issue_type})
                elif (current["states_json"], current["transitions_json"], current["managed_by"]) != values: actions.append({"action": "update", "entity": "workflow", "key": issue_type})
            self._plan_named(actions, "label", config.data.get("labels", []), current_labels, ("name", "color"), ("", "#64748b"))
            self._plan_named(actions, "milestone", config.data.get("milestones", []), current_milestones, ("name", "description", "due_at"), ("", "", None))
            if prune:
                for entity, current, wanted in (("workflow", current_workflows, desired), ("label", current_labels, {x["key"]: x for x in config.data.get("labels", [])}), ("milestone", current_milestones, {x["key"]: x for x in config.data.get("milestones", [])})):
                    for stale_key, row in current.items():
                        if row["managed_by"] == MANAGED_BY and stale_key not in wanted: actions.append({"action": "delete", "entity": entity, "key": stale_key})
            applied = None if not project else db.execute("SELECT digest FROM project_config WHERE project_id=?", (project["id"],)).fetchone()
        return {"config": str(config.path), "digest": config.digest, "previous_digest": applied[0] if applied else None, "actions": actions, "changed": bool(actions) or not applied or applied[0] != config.digest}

    @staticmethod
    def _plan_named(actions, entity, desired, current, fields, defaults):
        for item in desired:
            row = current.get(item["key"])
            if not row: actions.append({"action": "create", "entity": entity, "key": item["key"]})
            elif row["managed_by"] != MANAGED_BY or any(row[f] != item.get(f, d) for f, d in zip(fields, defaults)):
                actions.append({"action": "update", "entity": entity, "key": item["key"]})

    def apply(self, config: ProjectConfig, *, prune: bool = False, actor_id: int | None = None) -> dict[str, Any]:
        plan = self.plan(config, prune=prune); key = config.project["key"].upper()
        if not plan["changed"]:
            with self.board.connect() as db: project_id = db.execute("SELECT id FROM projects WHERE key=?", (key,)).fetchone()[0]
            return {**plan, "project_id": project_id, "applied": False}
        stamp = now()
        with self.board.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            project = db.execute("SELECT * FROM projects WHERE key=?", (key,)).fetchone()
            if project:
                project_id = project["id"]; db.execute("UPDATE projects SET name=?,description=?,managed_by=?,updated_at=? WHERE id=?", (config.project["name"], config.project.get("description", ""), MANAGED_BY, stamp, project_id))
            else:
                project_id = db.execute("INSERT INTO projects(key,name,description,created_at,updated_at,managed_by) VALUES(?,?,?,?,?,?)", (key, config.project["name"], config.project.get("description", ""), stamp, stamp, MANAGED_BY)).lastrowid
            for issue_type, mapping in config.data.get("state_migrations", {}).items():
                for old, target in mapping.items(): db.execute("UPDATE issues SET status=?,updated_at=?,revision=revision+1 WHERE project_id=? AND type=? AND status=?", (target, stamp, project_id, issue_type, old))
            for issue_type, workflow in config.data["workflows"].items():
                used = {r[0] for r in db.execute("SELECT DISTINCT status FROM issues WHERE project_id=? AND type=?", (project_id, issue_type))}
                removed = used - set(workflow["states"])
                if removed: raise ConfigError(f"cannot remove workflow states used by issues without state_migrations: {', '.join(sorted(removed))}")
                db.execute("INSERT INTO workflows(project_id,issue_type,states_json,transitions_json,managed_by) VALUES(?,?,?,?,?) ON CONFLICT(project_id,issue_type) DO UPDATE SET states_json=excluded.states_json,transitions_json=excluded.transitions_json,managed_by=excluded.managed_by", (project_id, issue_type, json.dumps(workflow["states"]), json.dumps(workflow["transitions"]), MANAGED_BY))
            self._apply_named(db, "labels", project_id, config.data.get("labels", []), ("name", "color"), ("", "#64748b"))
            self._apply_named(db, "milestones", project_id, config.data.get("milestones", []), ("name", "description", "due_at"), ("", "", None), stamp)
            if prune:
                for table, desired in (("workflows", set(config.data["workflows"])), ("labels", {x["key"] for x in config.data.get("labels", [])}), ("milestones", {x["key"] for x in config.data.get("milestones", [])})):
                    column = "issue_type" if table == "workflows" else "key"
                    rows = db.execute(f"SELECT {column} FROM {table} WHERE project_id=? AND managed_by=?", (project_id, MANAGED_BY)).fetchall()
                    for row in rows:
                        if row[0] not in desired: db.execute(f"DELETE FROM {table} WHERE project_id=? AND {column}=?", (project_id, row[0]))
            diff=json.dumps(plan["actions"], ensure_ascii=False); defaults=json.dumps(config.data.get("defaults", {})); policy=json.dumps(config.data.get("agent_policy", {}))
            db.execute("INSERT INTO project_config(project_id,digest,schema_version,defaults_json,agent_policy_json,applied_at) VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET digest=excluded.digest,schema_version=excluded.schema_version,defaults_json=excluded.defaults_json,agent_policy_json=excluded.agent_policy_json,applied_at=excluded.applied_at", (project_id, config.digest, config.schema_version, defaults, policy, stamp))
            db.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"project.{key}.config_digest", config.digest))
            db.execute("INSERT INTO config_applies(project_id,digest,schema_version,diff_json,applied_at,actor_id) VALUES(?,?,?,?,?,?)", (project_id, config.digest, config.schema_version, diff, stamp, actor_id))
            self.board._activity(db, actor_id, "project", project_id, "config_applied", {"digest": config.digest, "actions": plan["actions"]})
        return {**plan, "project_id": project_id, "applied": True}

    @staticmethod
    def _apply_named(db, table, project_id, desired, fields, defaults, stamp=None):
        for item in desired:
            row = db.execute(f"SELECT id FROM {table} WHERE project_id=? AND key=?", (project_id, item["key"])).fetchone()
            values=[item.get(f, d) for f,d in zip(fields, defaults)]
            if row:
                assignments=",".join(f"{f}=?" for f in fields); db.execute(f"UPDATE {table} SET {assignments},managed_by=? WHERE id=?", (*values, MANAGED_BY, row["id"]))
            else:
                columns="project_id,key," + ",".join(fields) + ",managed_by" + (",created_at" if stamp else "")
                marks=",".join("?" for _ in columns.split(",")); args=[project_id,item["key"],*values,MANAGED_BY] + ([stamp] if stamp else [])
                db.execute(f"INSERT INTO {table}({columns}) VALUES({marks})", args)

def suggested_key(name: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:10]
    return key if len(key) >= 2 else "APP"


def migrate_config(path: str | Path) -> ProjectConfig:
    """Upgrade an older file in place; schema 1 is currently the only released format."""
    target = Path(path)
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot migrate {target}: {exc}") from exc
    version = data.get("schema_version")
    if version == CONFIG_SCHEMA_VERSION:
        return load_config(target)
    raise ConfigError(f"no migration path from schema_version {version!r} to {CONFIG_SCHEMA_VERSION}")


def export_config(board: Board, project: int | str, path: str | Path) -> ProjectConfig:
    """Export declarative entities only; runtime records and secrets never leave SQLite."""
    context = board.project_context(project)
    q = lambda value: json.dumps(value, ensure_ascii=False)
    lines = [f"schema_version = {CONFIG_SCHEMA_VERSION}", "", "[project]", f"key = {q(context['key'])}", f"name = {q(context['name'])}", f"description = {q(context['description'])}", "", "[defaults]", f"issue_type = {q(context['defaults'].get('issue_type', 'task'))}", f"priority = {q(context['defaults'].get('priority', 'medium'))}"]
    for label in context["labels"]:
        lines += ["", "[[labels]]", f"key = {q(label.get('key') or suggested_key(label['name']).lower())}", f"name = {q(label['name'])}", f"color = {q(label['color'])}"]
    for milestone in context["milestones"]:
        lines += ["", "[[milestones]]", f"key = {q(milestone.get('key') or suggested_key(milestone['name']).lower())}", f"name = {q(milestone['name'])}", f"description = {q(milestone['description'])}"]
        if milestone["due_at"] is not None: lines.append(f"due_at = {q(milestone['due_at'])}")
    for workflow in context["workflows"]:
        terminal = [s for s in workflow["states"] if not any(edge[0] == s for edge in workflow["transitions"])] or [workflow["states"][-1]]
        lines += ["", f"[workflows.{workflow['issue_type']}]", f"initial = {q(workflow['states'][0])}", f"terminal = {q(terminal)}", f"states = {q(workflow['states'])}", f"transitions = {q(workflow['transitions'])}"]
    policy=context["agent_policy"]
    lines += ["", "[agent_policy]", f"require_assignee_before_start = {str(policy.get('require_assignee_before_start', False)).lower()}", f"require_reviewer_for = {q(policy.get('require_reviewer_for', []))}", f"branch_pattern = {q(policy.get('branch_pattern', '{issue_key}-{slug}'))}", ""]
    target=Path(path); target.parent.mkdir(parents=True, exist_ok=True); target.write_text("\n".join(lines), encoding="utf-8")
    return load_config(target)
